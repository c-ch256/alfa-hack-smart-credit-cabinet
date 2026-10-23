"""Метрики на независимом тесте и графики калибровки."""
import numpy as np
from sklearn.calibration import calibration_curve
from sklearn.metrics import (roc_auc_score, average_precision_score, brier_score_loss,
                             log_loss, roc_curve, confusion_matrix)


def metrics(y, probability):
    fpr, tpr, _ = roc_curve(y, probability)
    auc = roc_auc_score(y, probability)
    return {
        'roc_auc': float(auc), 'gini': float(2 * auc - 1),
        'pr_auc_average_precision': float(average_precision_score(y, probability)),
        'brier': float(brier_score_loss(y, probability)),
        'log_loss': float(log_loss(y, probability)),
        'ks': float(np.max(tpr - fpr)),
        'mean_pd': float(np.mean(probability)), 'default_rate': float(np.mean(y)),
        'thresholds': {str(t): {
            'confusion_matrix': confusion_matrix(y, probability >= t, labels=[0, 1]).tolist(),
            'share_at_or_above': float(np.mean(probability >= t)),
        } for t in (0.12, 0.25)},
    }


def plots(y, raw, calibrated, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1], '--', color='gray')
    for label, probability in [('Raw', raw), ('Calibrated', calibrated)]:
        observed, predicted = calibration_curve(y, probability, n_bins=10, strategy='quantile')
        ax.plot(predicted, observed, 'o-', label=label)
    ax.set(xlabel='Mean predicted probability', ylabel='Observed default rate')
    ax.legend()
    fig.savefig(output / 'calibration.png', bbox_inches='tight')
    plt.close(fig)
    fig, ax = plt.subplots()
    fpr, tpr, _ = roc_curve(y, calibrated)
    ax.plot(fpr, tpr, label=f'AUC={roc_auc_score(y, calibrated):.3f}')
    ax.plot([0, 1], [0, 1], '--', color='gray')
    ax.set(xlabel='False positive rate', ylabel='True positive rate')
    ax.legend()
    fig.savefig(output / 'roc.png', bbox_inches='tight')
    plt.close(fig)
