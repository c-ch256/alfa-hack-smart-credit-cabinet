"""Actual fitting and serialization, not mocks of estimator behavior."""
import copy
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.impute import SimpleImputer

from ml.features import INPUT_FIELDS, request_to_row, row_to_request
from ml.model import create_pipeline
from ml.train import read_data, split_data

@pytest.fixture(scope="module")
def trained():
    df = read_data(Path("data/synthetic_data.csv"))
    # A fixed subset reduces test runtime while retaining genuine training.
    df = df.sample(2500, random_state=42).reset_index(drop=True)
    train,val,cal,test = split_data(df,42)
    model = create_pipeline(42)
    model.fit(df.loc[train,INPUT_FIELDS],df.loc[train,"default_12m"])
    return df,(train,val,cal,test),model

def test_training_reproducibility(trained):
    df,groups,model = trained
    train,_,_,test = groups
    second = create_pipeline(42)
    second.fit(df.loc[train,INPUT_FIELDS],df.loc[train,"default_12m"])
    np.testing.assert_allclose(model.predict_proba(df.loc[test,INPUT_FIELDS]),
                               second.predict_proba(df.loc[test,INPUT_FIELDS]),rtol=0,atol=1e-12)

def test_saved_pipeline(trained,tmp_path):
    df,groups,model = trained
    path=tmp_path/"pipeline.joblib"
    joblib.dump(model,path)
    loaded=joblib.load(path)
    np.testing.assert_array_equal(model.predict_proba(df.loc[groups[-1],INPUT_FIELDS]),
                                  loaded.predict_proba(df.loc[groups[-1],INPUT_FIELDS]))

def test_preprocessing_fit_on_train_only(trained):
    df,groups,model = trained
    train,_,_,test = groups
    features=model.named_steps["features"].transform(df.loc[train,INPUT_FIELDS])
    numeric=model.named_steps["preprocessing"].named_transformers_["num"]
    expected=features.select_dtypes(include="number").columns
    from ml.features import NUM_FEATURES
    np.testing.assert_allclose(numeric.named_steps["scaler"].mean_,features[NUM_FEATURES].mean().to_numpy())
    before=numeric.named_steps["scaler"].mean_.copy()
    changed=df.loc[test,INPUT_FIELDS].copy()
    changed["avg_monthly_revenue"]=10**12
    model.predict_proba(changed)
    np.testing.assert_array_equal(before,numeric.named_steps["scaler"].mean_)

def test_dataframe_and_request_preprocessing_match(trained):
    df,groups,model=trained
    one=df.loc[[groups[-1][0]],INPUT_FIELDS]
    converted=pd.DataFrame([request_to_row(row_to_request(one.iloc[0].to_dict()))])[INPUT_FIELDS]
    np.testing.assert_array_equal(model.predict_proba(one),model.predict_proba(converted))

def test_imputer_is_fitted_and_handles_internal_missing(trained):
    _,_,model=trained
    numeric=model.named_steps["preprocessing"].named_transformers_["num"]
    imputer=numeric.named_steps["imputer"]
    assert isinstance(imputer,SimpleImputer)
    values=pd.DataFrame([dict(zip(imputer.feature_names_in_,[np.nan]*len(imputer.feature_names_in_)))])
    assert np.isfinite(imputer.transform(values)).all()
    # Required external values remain strict: imputation is defensive inside preprocessing.

def test_probabilities_and_positive_class(trained):
    df,groups,model=trained
    probability=model.predict_proba(df.loc[groups[-1],INPUT_FIELDS])
    assert list(model.classes_) == [0,1]
    assert np.isfinite(probability).all() and ((probability>=0)&(probability<=1)).all()
    np.testing.assert_allclose(probability.sum(axis=1),1)

def test_final_metrics_and_seed_stability():
    import json
    report=json.loads(Path("ml/reports/metrics.json").read_text())
    assert [r["seed"] for r in report["stability"]] == [42,43,44]
    for run in report["stability"]:
        assert run["calibration"]["n"] > 0
        assert run["test"]["n"] > 0
        assert run["test"]["positives"] > 0
        assert 0 <= run["test"]["roc_auc"] <= 1
        assert np.isfinite(run["test"]["log_loss"])
        assert np.isfinite(run["test"]["brier"])
        assert "validation" in run["selection"]
    assert report["primary"]["seed"] == 42
