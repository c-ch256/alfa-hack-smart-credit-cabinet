"""Educational policy prototype; no persistence or bank approval status."""
import json
import math
from pathlib import Path

from ml.dto import ScoringRequest, CreditDecision, fingerprint
from ml.errors import InputValidationError
from ml.features import request_to_row

CONFIG_PATH = Path(__file__).parent / "policy_config.json"

def load_config(path=CONFIG_PATH):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    if cfg["schema_version"] != "1.0":
        raise InputValidationError("Unknown policy configuration version")
    bounds = cfg["risk_boundaries"]
    if len(bounds) != 3 or not 0 < bounds[0] < bounds[1] < bounds[2] < 1:
        raise InputValidationError("Invalid risk boundaries")
    if cfg["min_dscr"] <= 0 or not 0 < cfg["pd_eligible_below"] < cfg["pd_decline_at_or_above"] <= 1:
        raise InputValidationError("Invalid policy thresholds")
    if any(v < 0 for v in cfg["ranking_weights"].values()) or not math.isclose(sum(cfg["ranking_weights"].values()), 1):
        raise InputValidationError("Ranking weights must sum to 1")
    return cfg

def risk_band(pd_value, boundaries=None):
    bounds = boundaries if boundaries is not None else load_config()["risk_boundaries"]
    if isinstance(pd_value, bool) or not isinstance(pd_value, (float, int)) or not math.isfinite(pd_value) or not 0 <= pd_value <= 1:
        raise InputValidationError("PD must be finite and between 0 and 1")
    if len(bounds) != 3 or not 0 < bounds[0] < bounds[1] < bounds[2] < 1:
        raise InputValidationError("Invalid risk boundaries")
    return "ABCD"[sum(pd_value >= t for t in bounds)]

def monthly_payment(amount, months, annual_rate):
    if amount <= 0 or months <= 0 or annual_rate < 0 or not all(math.isfinite(v) for v in (amount, months, annual_rate)):
        raise InputValidationError("Invalid annuity arguments")
    rate = annual_rate / 12
    return amount / months if rate == 0 else amount * rate / (-math.expm1(-months * math.log1p(rate)))

def precheck(req, purpose=None, investment_object_confirmed=False, config=None):
    cfg = config or load_config()
    req = ScoringRequest.from_payload(req)
    row = request_to_row(req.request)
    def decision(status, code):
        return CreditDecision(req.application_id, status, (code,))
    if row["status"] != "active" or row["defaults"] > 0 or row["max_overdue_days_12m"] > cfg["stop_overdue_above"]:
        return decision("DECLINED", "STOP_FACTOR")
    if purpose not in cfg["purposes"]:
        return decision("INSUFFICIENT_DATA", "UNKNOWN_PURPOSE")
    if purpose is not None and row["product_code"] not in cfg["purposes"][purpose]:
        return decision("DECLINED", "PRODUCT_PURPOSE_MISMATCH")
    product = cfg["products"][row["product_code"]]
    if not product["min_amount"] <= row["amount"] <= product["max_amount"] or not product["min_term"] <= row["term_months"] <= product["max_term"]:
        return decision("DECLINED", "PRODUCT_LIMIT")
    if row["product_code"] == "investment":
        if not investment_object_confirmed:
            return decision("INSUFFICIENT_DATA", "INVESTMENT_OBJECT_REQUIRED")
        if row["amount"] >= product["collateral_from"] and row["collateral_value"] <= 0:
            return decision("INSUFFICIENT_DATA", "COLLATERAL_REQUIRED")
    return None

def evaluate(req, scoring, purpose=None, investment_object_confirmed=False, config=None):
    cfg = config or load_config()
    req = ScoringRequest.from_payload(req)
    preliminary = precheck(req, purpose, investment_object_confirmed, cfg)
    if preliminary:
        return preliminary
    row = request_to_row(req.request)
    if scoring.application_id != req.application_id or scoring.request_hash != fingerprint(req.request):
        raise InputValidationError("Score belongs to a different request")
    if scoring.risk_band != risk_band(scoring.pd, cfg["risk_boundaries"]):
        raise InputValidationError("PD and risk band disagree")
    if row["max_overdue_days_12m"] > cfg["manual_overdue_above"] or row["amount"] > cfg["max_auto_amount"]:
        return CreditDecision(req.application_id, "MANUAL_REVIEW", ("MANUAL_FLAG",))
    if scoring.pd >= cfg["pd_decline_at_or_above"]:
        return CreditDecision(req.application_id, "DECLINED", ("HIGH_RISK",))
    if scoring.pd >= cfg["pd_eligible_below"]:
        return CreditDecision(req.application_id, "MANUAL_REVIEW", ("RISK_REVIEW",))
    rate = cfg["key_rate"] + cfg["products"][row["product_code"]]["margin"] + cfg["risk_premiums"][scoring.risk_band]
    payment = monthly_payment(row["amount"], row["term_months"], rate)
    cash = row["avg_monthly_revenue"] - row["avg_monthly_expenses"]
    dscr = cash / (row["existing_monthly_payments"] + payment)
    status = "ELIGIBLE" if dscr >= cfg["min_dscr"] else "DECLINED"
    code = "WITHIN_POLICY" if status == "ELIGIBLE" else "INSUFFICIENT_CASH_FLOW"
    return CreditDecision(req.application_id, status, (code,), dscr, rate, payment)
