"""Contract, artifact and public serialization tests (real saved model)."""
import copy
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from ml.credit_policy import risk_band
from ml.dto import PublicResult, ScoringRequest, ScoringResult
from ml.errors import (
    ArtifactError,
    InputValidationError,
    OutOfDomainError,
    SchemaVersionError,
)
from ml.features import (
    CONTRACT_DIR,
    INPUT_FIELDS,
    build_features,
    request_to_row,
    row_to_request,
    schema_validator,
)
from ml.inference import ModelScorer
from ml.train import preprocessing_digest, read_data, split_data


def test_preprocessing_digest_is_portable(tmp_path, monkeypatch):
    source_root = Path(__file__).parents[1]
    expected = preprocessing_digest()
    for name in ('features.py', 'model.py'):
        source = (source_root / name).read_bytes()
        (tmp_path / name).write_bytes(source.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    monkeypatch.setattr('ml.train.ROOT', tmp_path)
    assert preprocessing_digest() == expected
    metadata = json.loads((source_root / 'artifacts' / 'metadata.json').read_text())
    assert metadata['preprocessing_sha256'] == expected
    with (tmp_path / 'features.py').open('ab') as source:
        source.write(b'\n# changed preprocessing')
    assert preprocessing_digest() != expected

@pytest.fixture(scope="module")
def scorer():
    return ModelScorer()

@pytest.fixture
def req():
    return copy.deepcopy(json.loads((CONTRACT_DIR / "scoring_requests.json").read_text(encoding="utf-8"))[0]["request"])

def test_backend_examples(scorer):
    examples = json.loads((CONTRACT_DIR / "scoring_requests.json").read_text(encoding="utf-8"))
    for example in examples:
        req = example["request"]
        result = scorer.score(req)
        assert isinstance(result, ScoringResult)
        schema_validator("scoring_result.v2.schema.json").validate(result.to_dict())
        schema_validator("scoring_result.schema.json").validate(result.to_v1())
        assert result.risk_band == risk_band(result.pd)
        assert result == scorer.score(req)
        assert row_to_request(request_to_row(req)) == req

def test_all_generated_rows_match_contract():
    df = read_data(Path("data/synthetic_data.csv"))
    assert set(df.columns) == set(INPUT_FIELDS) | {"default_12m","application_date"}
    for row in df.to_dict("records"):
        request_to_row(row_to_request(row))

def test_no_target_or_date_leakage():
    df = read_data(Path("data/synthetic_data.csv"))
    expected = build_features(df)
    df["default_12m"] = 1 - df.default_12m
    df["application_date"] = pd.Timestamp("2099-01-01")
    pd.testing.assert_frame_equal(expected, build_features(df))

def test_disjoint_splits():
    df = read_data(Path("data/synthetic_data.csv"))
    groups = split_data(df,42)
    assert sum(map(len,groups)) == len(df)
    for i,a in enumerate(groups):
        for b in groups[i+1:]:
            assert not set(a) & set(b)
    assert df.loc[np.concatenate(groups[:3]),"application_date"].max() < df.loc[groups[3],"application_date"].min()

def test_invalid_and_unknown_inputs(scorer, req):
    req["company"]["okved"] = "99.99"
    req["company"]["region"] = "Новый регион"
    req["company"]["avg_monthly_revenue"] = 0
    assert 0 <= scorer.score(req).pd <= 1

@pytest.mark.parametrize("mutation", ["missing","extra","bool_amount","float_amount","negative","nan","infinity","bad_category","age","debt"])
def test_invalid_requests(scorer, req, mutation):
    if mutation == "missing": del req["amount"]
    elif mutation == "extra": req["pd"] = .1
    elif mutation == "bool_amount": req["amount"] = True
    elif mutation == "float_amount": req["amount"] = 3000000.0
    elif mutation == "negative": req["amount"] = -1
    elif mutation == "nan": req["company"]["revenue_trend_6m"] = float("nan")
    elif mutation == "infinity": req["company"]["revenue_trend_6m"] = float("inf")
    elif mutation == "bad_category": req["product_code"] = "other"
    elif mutation == "age": req["company"]["bank_client_months"] = 999
    elif mutation == "debt": req["bki"]["active_loans"] = 0
    with pytest.raises(InputValidationError): scorer.score(req)

def test_monotonic_overdue_and_batch(scorer, req):
    requests = []
    for overdue in (0,5,30,45,90):
        other = copy.deepcopy(req)
        other["bki"]["max_overdue_days_12m"] = overdue
        requests.append(other)
    results = scorer.score_many(requests)
    # Learned direction is checked rather than hardcoded into inference.
    assert np.all(np.diff([r.pd for r in results]) >= -1e-12)
    for request,result in zip(requests,results):
        assert result.pd == pytest.approx(scorer.score(request).pd, abs=1e-12)
    assert scorer.score_many([]) == []

@pytest.mark.parametrize("pd_value,band", [(0,"A"),(.049999,"A"),(.05,"B"),(.099999,"B"),(.10,"C"),(.199999,"C"),(.20,"D"),(1,"D")])
def test_band_boundaries(pd_value, band):
    assert risk_band(pd_value) == band

@pytest.mark.parametrize("pd_value", [-1,1.1,float("nan"),float("inf"),True])
def test_invalid_pd(pd_value):
    with pytest.raises(InputValidationError): risk_band(pd_value)

def test_versions(scorer, req):
    envelope = ScoringRequest("test-123", req)
    schema_validator("scoring_request.v2.schema.json").validate(envelope.to_dict())
    assert scorer.score(envelope).application_id == "test-123"
    with pytest.raises(SchemaVersionError): scorer.score({"application_id":"x","schema_version":"9","request":req})

def test_extreme_numeric_is_typed_error(scorer, req):
    req["amount"] = 10 ** 100
    with pytest.raises(InputValidationError): scorer.score(req)

def test_public_nested_offer_rejects_internal_fields():
    public = PublicResult("public-test").to_dict()
    public["offers"] = [{"product_code":"working_capital", "amount":300000,
        "term_months":12, "annual_rate":.22, "monthly_payment":30000, "pd":.1}]
    with pytest.raises(InputValidationError): PublicResult.from_dict(public)

@pytest.mark.parametrize("field,value", [("status","liquidating"),("defaults",1),("max_overdue_days_12m",91)])
def test_out_of_domain(scorer, req, field, value):
    req["fns" if field == "status" else "bki"][field] = value
    with pytest.raises(OutOfDomainError): scorer.score(req)

def test_missing_model(tmp_path):
    with pytest.raises(ArtifactError): ModelScorer(tmp_path)

@pytest.mark.parametrize("damage", ["bytes","version","features","libraries","identity","schema","preprocessing"])
def test_corrupt_or_incompatible(tmp_path, damage):
    for path in Path("ml/artifacts").iterdir():
        if path.is_file(): shutil.copy2(path,tmp_path/path.name)
    if damage == "bytes":
        (tmp_path/"pipeline.joblib").write_bytes(b"corrupted")
    else:
        path = tmp_path/"metadata.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        if damage == "version": meta["contract_version"] = "99"
        elif damage == "features": meta["transformed_features"] = ["wrong"]
        elif damage == "libraries": meta["libraries"]["scikit-learn"] = "0"
        elif damage == "identity": meta["model_version"] = "wrong"
        elif damage == "schema": meta["request_schema_sha256"] = "0"
        elif damage == "preprocessing": meta["preprocessing_sha256"] = "0"
        path.write_text(json.dumps(meta),encoding="utf-8")
    with pytest.raises(ArtifactError): ModelScorer(tmp_path)

def test_explanation_reconstructs_probability(scorer, req):
    result = scorer.score(req)
    details = result.details
    assert details["intercept"] + details["contribution_sum"] == pytest.approx(details["log_odds"])
    assert 1 / (1 + np.exp(-details["log_odds"])) == pytest.approx(result.pd)
    frame = pd.DataFrame([request_to_row(req)])[INPUT_FIELDS]
    transformed = scorer.pipeline[:-1].transform(frame)[0]
    actual = transformed * scorer.pipeline.named_steps["classifier"].coef_[0]
    names = scorer.metadata["transformed_features"]
    for reason in result.reasons:
        assert reason["contribution"] == pytest.approx(actual[names.index(reason["feature"])])
    assert "approve" not in result.to_dict() and "decision" not in result.to_dict()

def test_public_dto(scorer, req):
    public = PublicResult("public-test").to_dict()
    assert PublicResult.from_dict(public).to_dict() == public
    for field in ("pd","risk_band","reasons","details","model_version","coefficients","thresholds","shap"):
        leaked = copy.deepcopy(public)
        leaked[field] = "sensitive"
        with pytest.raises(InputValidationError): PublicResult.from_dict(leaked)
    with pytest.raises(InputValidationError): PublicResult.from_dict(scorer.score(req).to_dict())


def test_cyrillic_explanations_are_preserved(scorer, req):
    result = scorer.score(req)
    assert result.reasons[0]["text"].startswith("\u0412\u043d\u0443\u0442\u0440\u0435\u043d\u043d\u0438\u0439")
    assert "?" not in result.reasons[0]["text"]
