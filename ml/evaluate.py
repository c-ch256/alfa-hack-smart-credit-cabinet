"""Metrics and standalone evaluation on the fixed out-of-time test."""
import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (roc_auc_score, average_precision_score, brier_score_loss,
 log_loss, roc_curve, confusion_matrix, precision_recall_fscore_support, precision_recall_curve)

def metrics(y, probability, threshold=0.12):
    y = np.asarray(y)
    probability = np.asarray(probability)
    fpr, tpr, _ = roc_curve(y, probability)
    auc = roc_auc_score(y, probability)
    precision, recall, f1, _ = precision_recall_fscore_support(y, probability >= threshold,
        average="binary", zero_division=0)
    observed, predicted = calibration_curve(y, probability, n_bins=10, strategy="quantile")
    bands = np.searchsorted([0.05, 0.10, 0.20], probability, side="right")
    return {"n": len(y), "positives": int(y.sum()), "roc_auc": float(auc),
        "gini": float(2 * auc - 1), "average_precision": float(average_precision_score(y, probability)),
        "brier": float(brier_score_loss(y, probability)), "log_loss": float(log_loss(y, probability)),
        "ks": float(np.max(tpr - fpr)), "mean_pd": float(probability.mean()),
        "default_rate": float(y.mean()), "threshold": threshold,
        "precision": float(precision), "recall": float(recall), "f1": float(f1),
        "confusion_matrix": confusion_matrix(y, probability >= threshold, labels=[0,1]).tolist(),
        "calibration_curve": {"mean_predicted": predicted.tolist(), "observed": observed.tolist()},
        "risk_bands": {band: {"n": int((bands == i).sum()),
            "default_rate": float(y[bands == i].mean()) if (bands == i).any() else None,
            "mean_pd": float(probability[bands == i].mean()) if (bands == i).any() else None}
            for i, band in enumerate("ABCD")}}

def plots(y, probability, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    output.mkdir(parents=True, exist_ok=True)
    actual, predicted = calibration_curve(y, probability, n_bins=10, strategy="quantile")
    curves = [("calibration", predicted, actual, "Mean predicted PD", "Observed default rate"),
              ("roc", *roc_curve(y, probability)[:2], "False positive rate", "True positive rate")]
    precision, recall, _ = precision_recall_curve(y, probability)
    curves.append(("precision_recall", recall, precision, "Recall", "Precision"))
    for name, x, values, xlabel, ylabel in curves:
        fig, ax = plt.subplots()
        ax.plot(x, values, "o-" if name == "calibration" else "-")
        if name != "precision_recall":
            ax.plot([0,1], [0,1], "--", color="gray")
        ax.set(xlabel=xlabel, ylabel=ylabel, title="Synthetic data only")
        fig.savefig(output / (name + ".png"), bbox_inches="tight")
        plt.close(fig)

def main():
    from ml.train import read_data, split_data
    from ml.features import INPUT_FIELDS, row_to_request
    from ml.inference import ModelScorer
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/synthetic_data.csv"))
    args = parser.parse_args()
    df = read_data(args.data)
    scorer = ModelScorer()
    indices = split_data(df, scorer.metadata["seed"])[-1]
    results = scorer.score_many([row_to_request(row) for row in df.loc[indices, INPUT_FIELDS].to_dict("records")])
    report = metrics(df.loc[indices, "default_12m"], [r.pd for r in results])
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
