"""Train a validated LR Pipeline; choose regularization using validation only."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import log_loss, brier_score_loss

from ml.dto import CONTRACT_VERSION
from ml.evaluate import metrics, plots
from ml.features import INPUT_FIELDS, MODEL_FEATURES, validate_frame
from ml.model import create_pipeline

ROOT = Path(__file__).parent
PACKAGE_NAMES = ("numpy", "pandas", "scikit-learn", "joblib", "jsonschema")

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def preprocessing_digest():
    return hashlib.sha256(b"".join((ROOT / name).read_bytes()
        for name in ("features.py", "model.py"))).hexdigest()

def versions():
    return {name: importlib.metadata.version(name) for name in PACKAGE_NAMES}

def read_data(path):
    df = pd.read_csv(path, dtype={"okved": str}, parse_dates=["application_date"])
    if set(df.columns) != set(INPUT_FIELDS) | {"default_12m", "application_date"}:
        raise ValueError("Dataset must contain 20 inputs, application_date, default_12m")
    if df.isna().any().any() or df.duplicated().any() or not df.default_12m.isin([0,1]).all():
        raise ValueError("Dataset contains missing values, duplicates or invalid labels")
    if df.application_date.max() + pd.DateOffset(months=12) > pd.Timestamp("2026-10-23"):
        raise ValueError("Unmatured labels")
    validate_frame(df[INPUT_FIELDS])
    return df

def split_data(df, seed=42):
    cutoff = df.application_date.max() - pd.DateOffset(months=6)
    historical = df.index[df.application_date <= cutoff]
    test = df.index[df.application_date > cutoff]
    train, holdout = train_test_split(historical, test_size=0.30, random_state=seed,
                                    stratify=df.loc[historical, "default_12m"])
    validation, calibration = train_test_split(holdout, test_size=0.5, random_state=seed,
                                              stratify=df.loc[holdout, "default_12m"])
    groups = (train, validation, calibration, test)
    if len(set().union(*(set(g) for g in groups))) != len(df) or sum(map(len, groups)) != len(df):
        raise ValueError("Split overlap or missing rows")
    for group in groups:
        if df.loc[group, "default_12m"].nunique() != 2:
            raise ValueError("Each split needs both classes")
    return groups

def fit_model(df, seed=42):
    train, validation, calibration, test = split_data(df, seed)
    x, y = df[INPUT_FIELDS], df.default_12m
    candidates = []
    for C in (0.1, 1.0, 10.0):
        model = create_pipeline(seed, C)
        model.fit(x.loc[train], y.loc[train])
        probability = model.predict_proba(x.loc[validation])[:, 1]
        candidates.append((float(log_loss(y.loc[validation], probability)),
                           float(brier_score_loss(y.loc[validation], probability)), C, model))
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = candidates[0][-1]
    if list(selected.classes_) != [0,1]:
        raise ValueError("Class 1 must represent default")
    calibration_pd = selected.predict_proba(x.loc[calibration])[:, 1]
    diagnostic = metrics(y.loc[calibration], calibration_pd)
    # Do not fit a second estimator just because a calibration split exists.
    # LogLoss + Brier + curve are retained as the evidence for human review.
    diagnostic["method"] = "Raw logistic predict_proba; no separate calibrator"
    diagnostic["reason"] = "Proper-loss LR; calibration diagnostics reported, no automatic claim of perfect calibration"
    diagnostic["mean_gap"] = diagnostic["mean_pd"] - diagnostic["default_rate"]
    diagnostic["prevalence_standard_error"] = float(np.sqrt(diagnostic["default_rate"] * (1-diagnostic["default_rate"]) / len(calibration)))
    probability = selected.predict_proba(x.loc[test])[:,1]
    return selected, {
        "seed": seed, "selected_C": candidates[0][2],
        "selection": "minimum validation LogLoss, then Brier, then smaller C; test excluded",
        "validation_candidates": [{"C": c[2], "log_loss": c[0], "brier": c[1]} for c in candidates],
        "calibration": diagnostic, "test": metrics(y.loc[test], probability),
        "split_sizes": dict(zip(("train","validation","calibration","test"), map(len, (train, validation, calibration, test)))),
    }

def save_artifact(model, df, data_path, directory, seed, selected_C):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pipeline.joblib"
    joblib.dump(model, path)
    artifact_hash = digest(path)
    metadata = {
        "artifact_format": "logistic-pipeline-v2", "contract_version": CONTRACT_VERSION,
        "model_version": "logistic-v2-" + artifact_hash[:16],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed, "parameters": {"C": selected_C, "solver": "lbfgs", "max_iter": 2000, "class_weight": None},
        "libraries": versions(), "input_fields": INPUT_FIELDS, "model_features": MODEL_FEATURES,
        "transformed_features": model.named_steps["preprocessing"].get_feature_names_out().tolist(),
        "data_sha256": digest(data_path), "pipeline_sha256": artifact_hash,
        "preprocessing_sha256": preprocessing_digest(),
        "request_schema_sha256": digest(ROOT / "contracts/scoring_request.schema.json"),
        "calibrator": {"id": "none", "method": "raw logistic probabilities"},
        "positive_class": 1, "schema_source": "metadata.json", "rows": len(df),
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata

def psi(reference, comparison):
    bins = np.unique(np.quantile(reference, np.linspace(0,1,11)))
    if len(bins) < 2:
        return 0.0
    bins[0], bins[-1] = -np.inf, np.inf
    a = np.clip(np.histogram(reference, bins)[0] / len(reference), 1e-6, 1)
    b = np.clip(np.histogram(comparison, bins)[0] / len(comparison), 1e-6, 1)
    return float(np.sum((b-a)*np.log(b/a)))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic_data.csv"))
    parser.add_argument("--artifacts", type=Path, default=Path("ml/artifacts"))
    parser.add_argument("--reports", type=Path, default=Path("ml/reports"))
    args = parser.parse_args()
    start = time.perf_counter()
    df = read_data(args.data)
    fitted = [fit_model(df, seed) for seed in (42,43,44)]
    model, primary = fitted[0]
    metadata = save_artifact(model, df, args.data, args.artifacts, 42, primary["selected_C"])
    args.reports.mkdir(parents=True, exist_ok=True)
    train, _, _, test = split_data(df, 42)
    probability = model.predict_proba(df.loc[test, INPUT_FIELDS])[:,1]
    plots(df.loc[test, "default_12m"], probability, args.reports)
    report = {"warning": "Synthetic demonstration only; not evidence for real lending.",
              "model_version": metadata["model_version"], "primary": primary,
              "stability": [run for _, run in fitted],
              "stability_summary": {name: {"mean": float(np.mean([r["test"][name] for _,r in fitted])),
                   "std": float(np.std([r["test"][name] for _,r in fitted])),
                   "min": float(min(r["test"][name] for _,r in fitted)),
                   "max": float(max(r["test"][name] for _,r in fitted))}
                   for name in ("roc_auc","brier","log_loss")},
              "pd_psi_train_test": psi(model.predict_proba(df.loc[train, INPUT_FIELDS])[:,1], probability),
              "elapsed_seconds": time.perf_counter()-start}
    names = metadata["transformed_features"]
    coefficients = model.named_steps["classifier"].coef_[0]
    pd.DataFrame({"feature": names, "coefficient": coefficients,
                  "abs_coefficient": np.abs(coefficients)}).sort_values("abs_coefficient", ascending=False).to_csv(
                      args.reports / "coefficients.csv", index=False)
    transformed = model[:-1].transform(df.loc[test, INPUT_FIELDS])
    contributions = transformed * coefficients
    pd.DataFrame({"feature": names, "mean_absolute_log_odds_contribution": np.abs(contributions).mean(axis=0)}).sort_values(
        "mean_absolute_log_odds_contribution", ascending=False).to_csv(args.reports / "importance.csv", index=False)
    errors = df.loc[test, ["application_date", "default_12m"]].copy()
    errors["pd"] = probability
    errors["classification"] = np.where(probability >= .12, "positive", "negative")
    errors["is_error_at_0_12"] = (probability >= .12) != errors.default_12m.to_numpy()
    errors.to_csv(args.reports / "test_predictions.csv", index_label="row_id")
    (args.reports / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"model_version": metadata["model_version"], "test": primary["test"],
                     "calibration": primary["calibration"], "elapsed_seconds": report["elapsed_seconds"]}, indent=2))

if __name__ == "__main__":
    main()
