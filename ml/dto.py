"""Internal DTOs and a closed public serialization boundary."""
from dataclasses import dataclass, asdict
import hashlib
import json
from typing import Literal

from ml.errors import InputValidationError, SchemaVersionError

CONTRACT_VERSION = "2.0"

def fingerprint(payload):
    try:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise InputValidationError("Input is not finite JSON") from error
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

@dataclass(frozen=True)
class ScoringRequest:
    application_id: str
    request: dict
    schema_version: str = CONTRACT_VERSION

    def __post_init__(self):
        if self.schema_version != CONTRACT_VERSION:
            raise SchemaVersionError("Unsupported request contract version")
        if not isinstance(self.application_id, str) or not self.application_id.strip():
            raise InputValidationError("application_id must be a nonempty string")
        if not isinstance(self.request, dict):
            raise InputValidationError("request must be an object")

    @classmethod
    def from_payload(cls, payload):
        if isinstance(payload, cls):
            return payload
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json")
        if not isinstance(payload, dict):
            raise InputValidationError("Expected request object")
        if "request" in payload or "schema_version" in payload:
            if set(payload) != {"application_id", "schema_version", "request"}:
                raise InputValidationError("Envelope must contain exactly application_id, schema_version, request")
            return cls(**payload)
        return cls("legacy-" + fingerprint(payload)[:16], payload)

    def to_dict(self):
        return asdict(self)

@dataclass(frozen=True)
class ScoringResult:
    application_id: str
    pd: float
    risk_band: str
    model_version: str
    reasons: list
    request_hash: str
    details: dict
    technical_status: Literal["OK"] = "OK"
    schema_version: str = CONTRACT_VERSION

    def to_dict(self):
        result = asdict(self)
        json.dumps(result, allow_nan=False)
        return result

    def to_v1(self):
        return {key: getattr(self, key) for key in ("pd", "risk_band", "model_version", "reasons")}

@dataclass(frozen=True)
class CreditDecision:
    application_id: str
    status: Literal["ELIGIBLE", "DECLINED", "MANUAL_REVIEW", "INSUFFICIENT_DATA"]
    codes: tuple[str, ...]
    dscr: float | None = None
    annual_rate: float | None = None
    monthly_payment: float | None = None
    def to_dict(self):
        result = asdict(self)
        result["codes"] = list(self.codes)
        return result

@dataclass(frozen=True)
class LoanOffer:
    application_id: str
    product_code: str
    amount: int
    term_months: int
    annual_rate: float
    monthly_payment: float
    dscr: float
    rank_score: float
    def to_dict(self):
        return asdict(self)

@dataclass(frozen=True)
class PublicOffer:
    product_code: str
    amount: int
    term_months: int
    annual_rate: float
    monthly_payment: float

@dataclass(frozen=True)
class PublicResult:
    """No internal result can be passed implicitly or serialized wholesale."""
    application_id: str
    message_code: Literal["PROCESSING_COMPLETED"] = "PROCESSING_COMPLETED"
    offers: tuple[PublicOffer, ...] = ()

    def __post_init__(self):
        if not isinstance(self.application_id, str) or not self.application_id:
            raise InputValidationError("Public application_id is required")
        if self.message_code != "PROCESSING_COMPLETED":
            raise InputValidationError("Unapproved public message code")
        if not isinstance(self.offers, tuple) or any(type(o) is not PublicOffer for o in self.offers):
            raise InputValidationError("Public offers must be explicitly constructed PublicOffer DTOs")

    def to_dict(self):
        from ml.features import schema_validator
        result = asdict(self)
        result["offers"] = list(result["offers"])
        schema_validator("public_result.schema.json").validate(result)
        json.dumps(result, allow_nan=False)
        return result

    @classmethod
    def from_dict(cls, data):
        from ml.features import schema_validator
        try:
            schema_validator("public_result.schema.json").validate(data)
            json.dumps(data, allow_nan=False)
        except Exception as error:
            raise InputValidationError("Invalid public result") from error
        return cls(data["application_id"], data["message_code"],
                   tuple(PublicOffer(**offer) for offer in data["offers"]))
