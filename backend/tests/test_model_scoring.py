# ruff: noqa: F811 -- тестовый helper импортируется из соседнего модуля

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from test_scoring_policy import case  # noqa: F401

from app.scoring import model as model_module
from app.scoring.base import ScoringResult
from app.services.policy import build_scoring_request

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def scorer(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    pytest.importorskip('sklearn', reason='ML runtime is not installed in backend environment')
    return model_module.ModelScorer()


def request_for(profile: str):
    application, company, checks = case(profile=profile)
    return build_scoring_request(application, company, checks)


@pytest.mark.parametrize('profile', ['client1', 'client2', 'client3'])
def test_model_contract_for_scored_demo_profiles(scorer, profile) -> None:
    result = scorer.score(request_for(profile))
    assert isinstance(result, ScoringResult)
    assert isinstance(result.pd, Decimal) and 0 <= result.pd <= 1
    assert result.model_version == scorer.model_version
    assert result.reasons and all(
        isinstance(reason.contribution, Decimal) for reason in result.reasons
    )
    assert scorer.source == 'model' and scorer.is_test_result is True
    assert set(result.model_dump()) == {'pd', 'risk_band', 'model_version', 'reasons'}
    assert 'details' not in result.model_dump_json()


def test_stop_factor_profile_is_technical_model_error(scorer) -> None:
    with pytest.raises(model_module.ModelScorerError) as error:
        scorer.score(request_for('client4'))
    assert error.value.code == 'MODEL_INFERENCE_FAILED'


def test_decimal_is_numeric_without_silent_precision_loss(scorer) -> None:
    request = request_for('client1')
    request.company.revenue_trend_6m = Decimal('0.040000')
    payload = model_module._ml_payload(request)
    assert type(payload['company']['revenue_trend_6m']) is float
    assert payload['company']['revenue_trend_6m'] == 0.04
    assert scorer.score(request).pd >= 0
    request.company.revenue_trend_6m = Decimal('0.123456789012345678901')
    with pytest.raises(model_module.ModelScorerError) as error:
        scorer.score(request)
    assert error.value.code == 'INVALID_SCORING_REQUEST'


def test_ml_cross_field_validation_is_controlled(scorer) -> None:
    request = request_for('client1')
    request.bki.active_loans = 0
    with pytest.raises(model_module.ModelScorerError) as error:
        scorer.score(request)
    assert error.value.code == 'INVALID_SCORING_REQUEST'


@pytest.mark.parametrize(
    'mutation',
    [
        {'request_hash': '0' * 64},
        {'application_id': 'other'},
        {'pd': float('nan')},
        {'pd': 1},
        {'risk_band': 'D'},
        {'model_version': 'wrong'},
        {'technical_status': 'ERROR'},
        {'schema_version': '9.0'},
        {'details': {'unexpected': 'internal'}},
        {
            'reasons': [
                {
                    'feature': 'bad',
                    'text': 'bad',
                    'effect': 'increases_risk',
                    'contribution': float('inf'),
                }
            ]
        },
    ],
)
def test_invalid_ml_results_fail_closed(scorer, monkeypatch, mutation) -> None:
    from ml.dto import ScoringRequest as MlScoringRequest
    from ml.dto import fingerprint

    request = request_for('client1')
    payload = model_module._ml_payload(request)
    request_hash = fingerprint(payload)
    raw = scorer._delegate.score(MlScoringRequest(f'backend-{request_hash}', payload))
    monkeypatch.setattr(scorer._delegate, 'score', lambda _req: replace(raw, **mutation))
    with pytest.raises(model_module.ModelScorerError) as error:
        scorer.score(request)
    assert error.value.code == 'MODEL_INFERENCE_FAILED'
    assert 'details' not in str(error.value)


def test_inference_exception_is_sanitized(scorer, monkeypatch) -> None:
    def fail(_request):
        raise RuntimeError('synthetic secret value')

    monkeypatch.setattr(scorer._delegate, 'score', fail)
    with pytest.raises(model_module.ModelScorerError) as error:
        scorer.score(request_for('client1'))
    assert error.value.code == 'MODEL_INFERENCE_FAILED'
    assert 'synthetic secret value' not in str(error.value)


def test_only_pinned_artifact_can_be_loaded(monkeypatch) -> None:
    monkeypatch.setattr(model_module, '_TRUSTED_PIPELINE_SHA256', '0' * 64)
    with pytest.raises(model_module.ModelScorerError) as error:
        model_module.ModelScorer()
    assert error.value.code == 'MODEL_ARTIFACT_MISMATCH'


def test_ml_loader_error_is_controlled(scorer, monkeypatch) -> None:
    import ml.inference

    def fail(_directory):
        raise RuntimeError('synthetic loader secret')

    monkeypatch.setattr(ml.inference, 'ModelScorer', fail)
    with pytest.raises(model_module.ModelScorerError) as error:
        model_module.ModelScorer()
    assert error.value.code == 'MODEL_LOAD_FAILED'
    assert 'synthetic loader secret' not in str(error.value)
