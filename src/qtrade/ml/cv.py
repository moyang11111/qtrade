"""Time-series cross-validation with anti-lookahead guarantees."""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger("qtrade.ml.cv")


class TimeSeriesCV:
    """Expanding window CV — train data always ends before val data starts."""

    def __init__(self, config: dict):
        self.n_folds = config.get("n_folds", 5)
        self.method = config.get("method", "expanding")
        self.val_window = config.get("val_window", 63)
        self.gap = config.get("gap", 5)

    def split(self, X: pd.DataFrame, y: pd.Series) -> list[tuple[list, list]]:
        n = len(X)
        dates = X.index
        splits = []

        total_val = self.n_folds * self.val_window
        train_end_start = max(self.val_window, n - total_val - self.gap)

        for i in range(self.n_folds):
            train_end = train_end_start + i * self.val_window
            val_start = train_end + self.gap
            val_end = min(val_start + self.val_window, n)

            if val_end <= val_start:
                break

            train_idx = list(range(0, train_end))
            val_idx = list(range(val_start, val_end))
            splits.append((train_idx, val_idx))

        # Anti-lookahead assertions
        for train_idx, val_idx in splits:
            assert max(train_idx) < min(val_idx), \
                f"Train/val index overlap! train_max={max(train_idx)}, val_min={min(val_idx)}"
            assert dates[max(train_idx)] < dates[min(val_idx)], \
                f"Date overlap! train_end={dates[max(train_idx)]}, val_start={dates[min(val_idx)]}"
            logger.debug("[ANTI-LOOKAHEAD] CV fold: train ends %s, val starts %s",
                         dates[max(train_idx)].date(), dates[min(val_idx)].date())

        return splits

    def evaluate(self, X: pd.DataFrame, y: pd.Series, model) -> dict:
        """Run CV and return aggregated metrics."""
        splits = self.split(X, y)
        fold_metrics = []

        for fold_i, (train_idx, val_idx) in enumerate(splits):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]

            fold_model = model.clone()
            fold_model.fit(X_train, y_train)
            metrics = fold_model.evaluate(X_val, y_val)
            metrics["fold"] = fold_i + 1
            metrics["train_end"] = str(X.index[train_idx[-1]].date())
            metrics["val_start"] = str(X.index[val_idx[0]].date())
            fold_metrics.append(metrics)
            logger.info("  Fold %d: accuracy=%.3f (train→%s, val→%s)",
                        fold_i + 1, metrics["accuracy"],
                        metrics["train_end"], metrics["val_start"])

        accuracies = [m.get("accuracy", 0) for m in fold_metrics]
        return {
            "folds": fold_metrics,
            "mean_accuracy": round(float(np.mean(accuracies)), 4),
            "std_accuracy": round(float(np.std(accuracies)), 4),
        }
