"""python -m ml.train --data data/synthetic_data.csv"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import joblib
import numpy as np
import pandas as pd
from catboost import CatBoostClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split

from ml.evaluate import metrics, plots
from ml.features import build_features, CAT_FEATURES, INPUT_FIELDS


def read_data(path):
    return pd.read_csv(path, dtype={'okved': str}, parse_dates=['application_date'])


def split_data(df, seed):
    cutoff = df.application_date.max() - pd.DateOffset(months=6)
    historical = df.index[df.application_date <= cutoff]
    test = df.index[df.application_date > cutoff]
    train, holdout = train_test_split(historical, test_size=0.30, random_state=seed,
                                    stratify=df.loc[historical, 'default_12m'])
    validation, calibration = train_test_split(holdout, test_size=0.5, random_state=seed,
                                             stratify=df.loc[holdout, 'default_12m'])
    return train, validation, calibration, test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data/synthetic_data.csv'))
    parser.add_argument('--artifacts', type=Path, default=Path('ml/artifacts'))
    parser.add_argument('--reports', type=Path, default=Path('ml/reports'))
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    started = time.perf_counter()
    df = read_data(args.data)
    x = build_features(df)
    y = df.default_12m
    train, validation, calibration, test = split_data(df, args.seed)
    for name, indices in [('train', train), ('validation', validation), ('calibration', calibration), ('test', test)]:
        if y.loc[indices].nunique() != 2:
            raise ValueError(f'{name} needs both target classes')
    model = CatBoostClassifier(iterations=500, depth=5, learning_rate=0.05,
        loss_function='Logloss', eval_metric='Logloss', random_seed=args.seed,
        thread_count=4, allow_writing_files=False, verbose=False,
        monotone_constraints={'max_overdue_days_12m': 1, 'tax_debt': 1,
                              'business_age_months': -1, 'collateral_coverage': -1,
                              'principal_coverage': -1})
    model.fit(x.loc[train], y.loc[train], cat_features=CAT_FEATURES,
              eval_set=(x.loc[validation], y.loc[validation]), early_stopping_rounds=50)
    # Platt scaling на отдельном наборе; test не участвует в выборе модели.
    raw_cal = model.predict(x.loc[calibration], prediction_type='RawFormulaVal')
    calibrator = LogisticRegression(C=1000, solver='lbfgs', random_state=args.seed)
    calibrator.fit(raw_cal.reshape(-1, 1), y.loc[calibration])
    if calibrator.coef_[0, 0] <= 0:
        raise ValueError('Calibration reverses risk ordering; inspect dataset')
    raw_test = model.predict_proba(x.loc[test])[:, 1]
    scores = model.predict(x.loc[test], prediction_type='RawFormulaVal')
    calibrated = calibrator.predict_proba(scores.reshape(-1, 1))[:, 1]
    args.artifacts.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)
    model.save_model(str(args.artifacts / 'model.cbm'))
    joblib.dump(calibrator, args.artifacts / 'calibration.joblib')
    data_hash = hashlib.sha256(args.data.read_bytes()).hexdigest()
    metadata = {
        'model_version': f'catboost-v1-seed{args.seed}-{data_hash[:12]}',
        'trained_at_utc': datetime.now(timezone.utc).isoformat(),
        'seed': args.seed, 'data_sha256': data_hash, 'features': list(x.columns),
        'categorical_features': CAT_FEATURES, 'tree_count': model.tree_count_,
        'calibration': 'Platt scaling on independent calibration split',
        'shap_units': 'raw model log-odds before Platt calibration',
        'split_sizes': {name: len(idx) for name, idx in zip(
            ['train', 'validation', 'calibration', 'test'], [train, validation, calibration, test])},
    }
    (args.artifacts / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    (args.artifacts / 'feature_schema.json').write_text(json.dumps({
        'contract': 'Craftors ScoringRequest v1', 'input_fields': INPUT_FIELDS,
        'features': list(x.columns), 'categorical_features': CAT_FEATURES,
        'missing': 'reject', 'unknown_categories': 'CatBoost native category hashing',
        'ratios': 'denominators >= 1; ratios clipped to [-100, 100]',
        'rate_dependent_features': False,
    }, indent=2) + '\n', encoding='utf-8')
    report = {'warning': 'Synthetic metrics do not establish quality on real data.',
        'split_sizes': metadata['split_sizes'], 'seed': args.seed,
        'raw': metrics(y.loc[test], raw_test),
        'calibrated': metrics(y.loc[test], calibrated),
        'constant_train_prevalence': metrics(y.loc[test], np.full(len(test), y.loc[train].mean())),
        'elapsed_seconds': time.perf_counter() - started}
    plots(y.loc[test], raw_test, calibrated, args.reports)
    (args.reports / 'metrics.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
