"""Validation and interpretable features used by the entire sklearn Pipeline."""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from sklearn.base import BaseEstimator, TransformerMixin

from ml.errors import InputValidationError

CONTRACT_DIR = Path(__file__).parent / "contracts"

@lru_cache(maxsize=None)
def schema_validator(name="scoring_request.schema.json"):
    registry = Registry().with_resource("urn:craftors:scoring-request-v1", Resource.from_contents(
        json.loads((CONTRACT_DIR / "scoring_request.schema.json").read_text(encoding="utf-8"))))
    return Draft202012Validator(json.loads((CONTRACT_DIR / name).read_text(encoding="utf-8")), registry=registry)

REQUEST_SCHEMA = json.loads((CONTRACT_DIR / "scoring_request.schema.json").read_text(encoding="utf-8"))
GROUPS = {key: list(REQUEST_SCHEMA["properties"][key]["properties"]) for key in ("company", "bki", "fns")}
ROOT_FIELDS = ["product_code", "amount", "term_months", "collateral_value"]
INPUT_FIELDS = ROOT_FIELDS + [field for fields in GROUPS.values() for field in fields]
CAT_FEATURES = ["product_code", "okved"]
NUM_FEATURES = ["business_age_months", "bank_client_months", "revenue_trend_6m",
                "max_overdue_days_12m", "tax_debt", "mass_address", "net_margin",
                "principal_coverage", "log_loan_ratio", "log_debt_ratio",
                "collateral_coverage", "log_revenue"]
MODEL_FEATURES = NUM_FEATURES + CAT_FEATURES

def request_to_row(request):
    if hasattr(request, "model_dump"):
        request = request.model_dump(mode="json")
    try:
        # Reject Python bool-as-int and floats-as-int even if JSON Schema accepts 1.0.
        if not isinstance(request, dict):
            raise InputValidationError("Expected request object")
        schema_validator().validate(request)
        row = {key: request[key] for key in ROOT_FIELDS}
        for group in GROUPS:
            row.update(request[group])
        properties = {key: REQUEST_SCHEMA["properties"][key] for key in ROOT_FIELDS}
        for group in GROUPS:
            properties.update(REQUEST_SCHEMA["properties"][group]["properties"])
        for name, spec in properties.items():
            value = row[name]
            if spec["type"] in ("integer", "number") and (isinstance(value, (int, float)) and abs(value) > 1e15):
                raise InputValidationError(name + " exceeds the technical numeric limit 1e15")
            if spec["type"] == "integer" and type(value) is not int:
                raise InputValidationError(name + " must be an integer")
            if spec["type"] == "number" and (type(value) not in (int, float) or not np.isfinite(value)):
                raise InputValidationError(name + " must be finite")
        if row["bank_client_months"] > row["business_age_months"]:
            raise InputValidationError("Bank relationship exceeds business age")
        if row["active_loans"] == 0 and (row["total_debt"] != 0 or row["existing_monthly_payments"] != 0):
            raise InputValidationError("No active loans but current debt/payments provided")
        return row
    except (ValidationError, KeyError, TypeError, OverflowError) as error:
        raise InputValidationError(str(error)) from error

def row_to_request(row):
    def native(value):
        return value.item() if isinstance(value, np.generic) else value
    result = {key: native(row[key]) for key in ROOT_FIELDS}
    for group, fields in GROUPS.items():
        result[group] = {key: native(row[key]) for key in fields}
    return result

def validate_frame(rows):
    if not isinstance(rows, pd.DataFrame) or set(rows.columns) != set(INPUT_FIELDS):
        raise InputValidationError("Pipeline requires exactly the 20 input columns")
    for row in rows.to_dict(orient="records"):
        request_to_row(row_to_request(row))
    return rows.copy()

def build_features(rows):
    x = rows[INPUT_FIELDS].copy()
    revenue = x.avg_monthly_revenue.clip(lower=1)
    amount = x.amount.clip(lower=1)
    cash = x.avg_monthly_revenue - x.avg_monthly_expenses
    x["net_margin"] = (cash / revenue).clip(-2, 1)
    x["principal_coverage"] = (cash / (x.existing_monthly_payments + amount / x.term_months).clip(lower=1)).clip(-2, 5)
    x["log_loan_ratio"] = np.log1p((amount / (12 * revenue)).clip(0, 100))
    x["log_debt_ratio"] = np.log1p((x.total_debt / (12 * revenue)).clip(0, 100))
    x["collateral_coverage"] = (x.collateral_value / amount).clip(0, 3)
    x["log_revenue"] = np.log1p(x.avg_monthly_revenue)
    for name in ("tax_debt", "mass_address"):
        x[name] = x[name].astype(float)
    # Preserve the full supplied OKVED as a categorical string.
    for name in CAT_FEATURES:
        x[name] = x[name].astype(str)
    return x[MODEL_FEATURES]

class InputValidator(TransformerMixin, BaseEstimator):
    def fit(self, X, y=None):
        validate_frame(X)
        return self
    def transform(self, X):
        return validate_frame(X)
    def get_feature_names_out(self, input_features=None):
        return np.array(INPUT_FIELDS, dtype=object)

class FeatureBuilder(TransformerMixin, BaseEstimator):
    def fit(self, X, y=None):
        return self
    def transform(self, X):
        return build_features(X)
    def get_feature_names_out(self, input_features=None):
        return np.array(MODEL_FEATURES, dtype=object)
