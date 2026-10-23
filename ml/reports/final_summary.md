# Final ML implementation report

## Status

Completed and executed on Windows / Python 3.12.10. CatBoost is not imported or required by the active implementation. No Backend, Frontend, database or authorization developed. No remote push.

## Preservation and changes

Previous implementation is preserved in commit 795ba63. After completion, the user requested cleanup: three redundant backup directories, Python/pytest caches and obsolete installation/duplicate CLI logs were removed. Current source, installed environment, dataset, trained artifact and authoritative reports were retained. Cleanup did not change model or dataset hashes.

Changed/added paths (new implementation is not committed):

- README.md
- ml/MODEL_CARD.md
- ml/README.md
- ml/artifacts/metadata.json
- ml/artifacts/pipeline.joblib
- ml/benchmark.py
- ml/cli.py
- ml/contracts/README_ML.md
- ml/contracts/credit_decision.schema.json
- ml/contracts/loan_offer.schema.json
- ml/contracts/public_result.schema.json
- ml/contracts/scoring_request.v2.schema.json
- ml/contracts/scoring_result.v2.schema.json
- ml/credit_policy.py
- ml/dto.py
- ml/errors.py
- ml/evaluate.py
- ml/features.py
- ml/inference.py
- ml/model.py
- ml/offer_optimizer.py
- ml/policy_config.json
- ml/reports/backend_examples.json
- ml/reports/benchmark.json
- ml/reports/calibration.png
- ml/reports/coefficients.csv
- ml/reports/error_analysis.md
- ml/reports/final_summary.md
- ml/reports/importance.csv
- ml/reports/metrics.json
- ml/reports/offer_examples.json
- ml/reports/precision_recall.png
- ml/reports/pytest.xml
- ml/reports/roc.png
- ml/reports/test_predictions.csv
- ml/requirements-lock.txt
- ml/requirements.txt
- ml/tests/test_contract.py
- ml/tests/test_policy_optimizer.py
- ml/tests/test_training.py
- ml/train.py
- pytest.ini

## Architecture

Validated request -> stop factors -> sklearn LR Pipeline -> PD -> independent credit policy -> deterministic optimizer -> internal offers / explicit public DTO. Each candidate is rescored and policy checked. Declined, manual-review and insufficient-data requests do not generate offers.

## Model and data

Model logistic-v2-b758b66495a04782; LogisticRegression C=0.1, random_state=42, class 1=default. Single saved Pipeline with imputers, scaler, encoder, validation and feature builder. Chosen by validation LogLoss, then Brier and smaller C; test not used for selection. Separate calibrator not added.

Existing dataset unchanged: 15000 rows, 978 positive labels (6.52%). Split sizes: {'train': 7919, 'validation': 1697, 'calibration': 1697, 'test': 3687}. Out-of-time test; earlier splits are stratified random splits. No future fields or target in model features.

## Independent test metrics

| Metric | Value |
|---|---:|
| roc_auc | 0.951186 |
| gini | 0.902373 |
| average_precision | 0.775327 |
| brier | 0.028543 |
| log_loss | 0.107446 |
| ks | 0.762712 |
| precision | 0.493827 |
| recall | 0.793651 |
| f1 | 0.608828 |

n=3687, positives=252, diagnostic threshold=0.12, confusion matrix [[TN,FP],[FN,TP]]=[[3230, 205], [52, 200]].

## Calibration and stability

Calibration holdout: mean PD 0.062352, observed rate 0.064231, Brier 0.021688, LogLoss 0.080032. Test mean PD 0.065649 vs rate 0.068348. Local deviations remain; no perfect-calibration claim. See calibration curve and risk bands. No evidence from this diagnostic justifies blindly adding a second estimator.

Seeds 42/43/44 (fixed test; training/validation memberships vary):

| Metric | Mean | Std | Min | Max |
|---|---:|---:|---:|---:|
| roc_auc | 0.952146 | 0.000718 | 0.951186 | 0.952914 |
| brier | 0.028700 | 0.000122 | 0.028543 | 0.028841 |
| log_loss | 0.107453 | 0.000262 | 0.107135 | 0.107777 |

PD PSI train/test=0.001136; no temporal drift simulated.

## Real scoring and offer examples

First integration request: PD=0.0006857069, band=A, technical_status=OK. Internal reasons are true coefficient contributions in log-odds; their total plus intercept reconstructs the probability. See backend_examples.json.

| Scenario | Decision | Best amount | Months | Annual rate | Monthly payment | DSCR |
|---|---|---:|---:|---:|---:|---:|
| working_capital | ELIGIBLE | 3000000 | 36 | 22.00% | 114571.36 | 3.8368 |
| investment | ELIGIBLE | 3000000 | 36 | 20.00% | 111490.75 | 3.8878 |

Declined example: ['STOP_FACTOR'], offers=[], candidate calls=0. No scoring call for preliminary stop factors. Details: offer_examples.json.

Public serialization is allowlisted and tested against direct and nested internal-field injection. Public DTO contains only application ID, a fixed message code and public offer terms. No PD, coefficients, risk band, internal DSCR, scores or thresholds.

## Tests and commands

```powershell
.\.venv\Scripts\python.exe -m pip install -r ml/requirements-lock.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m ml.train
.\.venv\Scripts\python.exe -m ml.evaluate
.\.venv\Scripts\python.exe -m pytest -q --junitxml=ml/reports/pytest.xml
.\.venv\Scripts\python.exe -m ml.cli score
.\.venv\Scripts\python.exe -m ml.cli offers --purpose working_capital
.\.venv\Scripts\python.exe -m ml.cli offers --purpose working_capital --public
.\.venv\Scripts\python.exe -m ml.benchmark
```

Final implementation suite before cleanup: 79 passed, 0 failures, 0 errors, 0 skipped. The actual final-run pytest.xml is retained. Independent evaluation and both CLI modes also executed successfully; redundant CLI JSON copies and console text were removed during cleanup. The canonical metrics and internal/public examples remain in metrics.json, backend_examples.json and offer_examples.json. pip check reported no broken requirements. Tests were not rerun for cleanup: executable source was unchanged.

## Performance (latest run)

Load 23.16 ms; p50 81.78 ms; p95 158.10 ms; batch of 30 in 195.16 ms. RSS after 145.17 MiB; peak 145.17 MiB. Includes validation, preprocessing and explanations. No approved SLA. In particular, these measurements do not meet a 20 ms single-call target if that earlier aspirational target is later made mandatory.

Platform: Windows-11-10.0.26200-SP0; CPU: Intel64 Family 6 Model 142 Stepping 12, GenuineIntel; logical CPUs: 8; Python: 3.12.10.

## Artifacts and reports

pipeline.joblib: 5562 bytes, SHA-256 b758b66495a04782909b3e1b5189b52a755544b60e4aadfde657d738f74e2c9d. metadata.json is the only artifact-schema manifest; it records libraries, schema version, features and hashes. No standalone calibrator.

Reports retained: metrics.json; calibration.png; roc.png; precision_recall.png; coefficients.csv; importance.csv; test_predictions.csv; error_analysis.md; benchmark.json; backend_examples.json; offer_examples.json; data-check.json; pytest.xml; this final report. Obsolete installation reports and duplicate command outputs were removed at the user's request.

## Backend integration

Create ModelScorer once. Pass ScoringRequest(application_id, v1_request). score() returns typed internal ScoringResult; to_v1() adapts the old JSON contract. optimize(req, purpose, scorer, investment_object_confirmed=...) returns a separate policy decision and offers. Never expose internal.to_dict() to the client: use OptimizationResult.public(). Catch typed validation, domain and artifact errors. MODEL_DIR is supported. No hidden fallback.

## Limitations

All data and results are synthetic. High AUC reflects a simple latent logistic formula, not real banking quality. No real reject inference, drift or segment validation. Calibration can differ by group. Some candidate combinations have limited training support.

Policy, rates, purpose mapping and ranking weights are explicit educational assumptions in policy_config.json, not bank standards. Original ineligible requests are not rescued by counteroffers in this workflow. There is no loan issuance or legally effective approval. Decision persistence, audit of refusals, authorization, real FNS/BKI, notification wording, integration flags and HTTP API remain Backend responsibilities.

A Matplotlib font-cache permission warning occurred during training; graphs were successfully generated. It did not interrupt training or tests. Earlier pytest fixture/annuity-reference issues and UTF-8 text corruption were corrected; final suite includes Unicode regression coverage.
