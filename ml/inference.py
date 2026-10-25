"""Trusted internal scoring. No loan decisions, offers or external calls."""
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from ml.dto import CONTRACT_VERSION, ScoringRequest, ScoringResult, fingerprint
from ml.errors import ArtifactError, OutOfDomainError
from ml.features import INPUT_FIELDS, request_to_row, schema_validator
from ml.train import ROOT, digest, preprocessing_digest, versions

RISK_BOUNDARIES = (0.05, 0.10, 0.20)


def _risk_band(pd_value):
    return 'ABCD'[sum(pd_value >= boundary for boundary in RISK_BOUNDARIES)]

class ModelScorer:
    def __init__(self, model_dir=None):
        directory = Path(model_dir or os.getenv("MODEL_DIR", str(ROOT / "artifacts")))
        try:
            self.metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            meta = self.metadata
            if meta["artifact_format"] != "logistic-pipeline-v2" or meta["contract_version"] != CONTRACT_VERSION:
                raise ArtifactError("Incompatible artifact/contract version")
            if meta["pipeline_sha256"] != digest(directory / "pipeline.joblib"):
                raise ArtifactError("Artifact hash mismatch")
            if meta["libraries"] != versions() or meta["preprocessing_sha256"] != preprocessing_digest():
                raise ArtifactError("Runtime libraries or preprocessing mismatch")
            if meta["request_schema_sha256"] != digest(ROOT / "contracts/scoring_request.schema.json"):
                raise ArtifactError("Input schema mismatch")
            self.pipeline = joblib.load(directory / "pipeline.joblib")
            if list(self.pipeline.classes_) != [0,1] or meta["positive_class"] != 1:
                raise ArtifactError("Invalid positive class")
            actual = self.pipeline.named_steps["preprocessing"].get_feature_names_out().tolist()
            if actual != meta["transformed_features"] or meta["input_fields"] != INPUT_FIELDS:
                raise ArtifactError("Feature order mismatch")
            if meta["model_version"] != "logistic-v2-" + meta["pipeline_sha256"][:16]:
                raise ArtifactError("Model identity mismatch")
        except ArtifactError:
            raise
        except Exception as error:
            raise ArtifactError("Unable to load compatible local model: " + str(error)) from error
        self.boundaries = RISK_BOUNDARIES

    def score(self, req: ScoringRequest) -> ScoringResult:
        return self.score_many([req])[0]

    def score_many(self, requests):
        if not requests:
            return []
        envelopes = [ScoringRequest.from_payload(req) for req in requests]
        rows = [request_to_row(req.request) for req in envelopes]
        for row in rows:
            if row["status"] != "active" or row["defaults"] > 0 or row["max_overdue_days_12m"] > 90:
                raise OutOfDomainError("Request has a pre-scoring stop factor")
        x = pd.DataFrame(rows)[INPUT_FIELDS]
        probabilities = self.pipeline.predict_proba(x)[:,1]
        transformed = self.pipeline[:-1].transform(x)
        classifier = self.pipeline.named_steps["classifier"]
        contributions = transformed * classifier.coef_[0]
        logits = classifier.intercept_[0] + contributions.sum(axis=1)
        results = []
        for req, p, terms, logit in zip(envelopes, probabilities, contributions, logits):
            if not np.isfinite(p) or not 0 <= p <= 1:
                raise ArtifactError("Non-finite probability")
            reasons = []
            for i in np.argsort(-np.abs(terms))[:4]:
                value = float(terms[i])
                reasons.append({"feature": self.metadata["transformed_features"][i],
                    "text": "Внутренний вклад преобразованного признака в log-odds",
                    "effect": "increases_risk" if value >= 0 else "decreases_risk",
                    "contribution": value})
            result = ScoringResult(req.application_id, float(p), _risk_band(float(p)),
                self.metadata["model_version"], reasons, fingerprint(req.request),
                {"units": "log-odds relative to fitted intercept",
                 "intercept": float(classifier.intercept_[0]), "log_odds": float(logit),
                 "contribution_sum": float(terms.sum())})
            schema_validator("scoring_result.v2.schema.json").validate(result.to_dict())
            results.append(result)
        return results
