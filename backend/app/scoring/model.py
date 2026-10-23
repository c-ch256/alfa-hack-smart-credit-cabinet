"""Адаптер доверенной синтетической ML-модели к контракту backend Scorer."""

import hashlib
import math
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from app.scoring.base import ScoreReason, ScoringRequest, ScoringResult

_ARTIFACT_DIR = Path(__file__).resolve().parents[3] / 'ml' / 'artifacts'
_TRUSTED_PIPELINE_SHA256 = 'b758b66495a04782909b3e1b5189b52a755544b60e4aadfde657d738f74e2c9d'


class ModelScorerError(RuntimeError):
    """Контролируемый технический сбой; кредитное решение не формируется."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__('Оценка модели временно недоступна')


def _trusted_artifact(directory: Path) -> Path:
    pipeline = directory / 'pipeline.joblib'
    metadata = directory / 'metadata.json'
    if directory.is_symlink() or pipeline.is_symlink() or metadata.is_symlink():
        raise ModelScorerError('UNTRUSTED_ARTIFACT')
    try:
        if not directory.is_dir() or not metadata.is_file():
            raise ModelScorerError('MODEL_ARTIFACT_UNAVAILABLE')
        digest = hashlib.sha256(pipeline.read_bytes()).hexdigest()
    except OSError:
        raise ModelScorerError('MODEL_ARTIFACT_UNAVAILABLE') from None
    if digest != _TRUSTED_PIPELINE_SHA256:
        raise ModelScorerError('MODEL_ARTIFACT_MISMATCH')
    return directory


def _ml_payload(req: ScoringRequest) -> dict:
    if not isinstance(req, ScoringRequest):
        raise ModelScorerError('INVALID_SCORING_REQUEST')
    try:
        validated = ScoringRequest.model_validate(req.model_dump(mode='python'))
        payload = validated.model_dump(mode='python')
        trend = validated.company.revenue_trend_6m
        if not trend.is_finite():
            raise ValueError('non-finite trend')
        numeric_trend = float(trend)
        if not math.isfinite(numeric_trend) or Decimal(str(numeric_trend)) != trend:
            raise ValueError('trend cannot be represented by ML contract')
        payload['company']['revenue_trend_6m'] = numeric_trend
        from ml.features import request_to_row

        request_to_row(payload)
        return payload
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise ModelScorerError('INVALID_SCORING_REQUEST') from None


class ModelScorer:
    """Только PD; решения и условия остаются в backend policy."""

    source = 'model'
    is_test_result = True

    def __init__(self, model_dir: Path | None = None) -> None:
        directory = _trusted_artifact(model_dir or _ARTIFACT_DIR)
        try:
            from ml.inference import ModelScorer as MlModelScorer

            self._delegate = MlModelScorer(directory)
            self.model_version = self._delegate.metadata['model_version']
            if not isinstance(self.model_version, str) or not self.model_version:
                raise ValueError('invalid model version')
        except Exception:
            raise ModelScorerError('MODEL_LOAD_FAILED') from None

    def score(self, req: ScoringRequest) -> ScoringResult:
        try:
            from ml.dto import ScoringRequest as MlScoringRequest
            from ml.dto import ScoringResult as MlScoringResult
            from ml.dto import fingerprint
            from ml.features import schema_validator

            payload = _ml_payload(req)
            request_hash = fingerprint(payload)
            request_id = f'backend-{request_hash}'
            result = self._delegate.score(MlScoringRequest(request_id, payload))
            if not isinstance(result, MlScoringResult):
                raise ValueError('invalid ML result type')
            schema_validator('scoring_result.v2.schema.json').validate(result.to_dict())
            if (
                result.application_id != request_id
                or result.request_hash != request_hash
                or result.schema_version != '2.0'
                or result.technical_status != 'OK'
                or result.model_version != self.model_version
            ):
                raise ValueError('ML result does not match request')
            if type(result.pd) is not float or not math.isfinite(result.pd):
                raise ValueError('invalid PD')
            reasons = []
            for reason in result.reasons:
                if not isinstance(reason, dict) or set(reason) != {
                    'feature',
                    'text',
                    'effect',
                    'contribution',
                }:
                    raise ValueError('invalid reason')
                contribution = reason['contribution']
                if type(contribution) not in (int, float) or not math.isfinite(contribution):
                    raise ValueError('invalid contribution')
                reasons.append(
                    ScoreReason.model_validate(
                        {
                            **reason,
                            'contribution': Decimal(str(contribution)),
                        }
                    )
                )
            return ScoringResult.model_validate(
                {
                    'pd': Decimal(str(result.pd)),
                    'risk_band': result.risk_band,
                    'model_version': result.model_version,
                    'reasons': reasons,
                }
            )
        except ModelScorerError:
            raise
        except Exception:
            raise ModelScorerError('MODEL_INFERENCE_FAILED') from None
