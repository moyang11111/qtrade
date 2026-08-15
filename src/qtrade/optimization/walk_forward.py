"""Walk-forward validation for SignalGenerator strategies."""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd
from loguru import logger
from tqdm import tqdm

from qtrade.optimization.grid_search import GridSearchOptimizer


class WalkForwardValidator:
    """Walk-forward validation for SignalGenerator strategies.

    Each split: grid-search the best params on the training window, then score
    those params out-of-sample on the test window. No ``fit()`` method is used —
    strategies are scored through ``objective_func(strategy, df)``.
    """

    def __init__(self, strategy_class, param_grid: Dict[str, List],
                 objective_func: Callable,
                 n_splits: int = 5,
                 train_ratio: float = 0.7,
                 gap: int = 0,
                 retrain_every: int = 1,
                 constraints: List[str] | None = None,
                 strategy_name: str | None = None):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.objective_func = objective_func
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.gap = gap
        self.retrain_every = retrain_every
        self.constraints = constraints or []
        self.strategy_name = strategy_name
        self.results: List[Dict] = []

    def _generate_splits(self, data: pd.DataFrame) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        n = len(data)
        split_size = max(1, n // self.n_splits)
        splits = []
        for i in range(self.n_splits):
            test_end = min(split_size * (i + 1), n)
            train_end = int(test_end * self.train_ratio)
            test_start = min(train_end + self.gap, test_end)
            if test_start >= test_end or train_end <= 0:
                continue
            splits.append((data.iloc[:train_end], data.iloc[test_start:test_end]))
        return splits

    def validate(self, df: pd.DataFrame) -> Dict:
        """Run walk-forward validation. Returns summary dict with per-split results."""
        splits = self._generate_splits(df)
        logger.info("Walk-forward validation: {} splits", len(splits))
        self.results = []

        for i, (train_df, test_df) in enumerate(tqdm(splits, desc="Walk-Forward")):
            logger.debug("Split {}/{}: train={}, test={}", i + 1, len(splits), len(train_df), len(test_df))

            optimizer = GridSearchOptimizer(
                self.strategy_class,
                self.param_grid,
                self.objective_func,
                constraints=self.constraints,
                strategy_name=self.strategy_name,
            )
            opt_results = optimizer.optimize(train_df)
            best_params = opt_results["best_params"]
            train_score = opt_results["best_score"]

            result = {
                "split": i + 1,
                "train_size": len(train_df),
                "test_size": len(test_df),
                "best_params": best_params,
                "train_score": train_score,
                "test_score": None,
                "score_degradation": None,
            }

            if best_params is None:
                logger.warning("No valid params found for split {}", i + 1)
                self.results.append(result)
                continue

            try:
                strategy = self.strategy_class({"name": self.strategy_name, **best_params})
                test_score = self.objective_func(strategy, test_df)
                result["test_score"] = test_score
                result["score_degradation"] = train_score - test_score
            except Exception as e:
                logger.warning("Test failed for split {}: {}", i + 1, e)
                result["error"] = str(e)

            self.results.append(result)

        test_scores = [r["test_score"] for r in self.results if r.get("test_score") is not None]

        summary = {
            "n_splits": len(splits),
            "avg_test_score": float(np.mean(test_scores)) if test_scores else None,
            "std_test_score": float(np.std(test_scores)) if test_scores else None,
            "min_test_score": float(np.min(test_scores)) if test_scores else None,
            "max_test_score": float(np.max(test_scores)) if test_scores else None,
            "avg_score_degradation": float(np.mean([r["score_degradation"] for r in self.results if r.get("score_degradation") is not None])) if any(r.get("score_degradation") is not None for r in self.results) else None,
            "results": self.results,
        }

        if summary["avg_test_score"] is not None:
            logger.info("Avg test score: {:.4f} ± {:.4f}", summary["avg_test_score"], summary["std_test_score"])

        return summary

    def get_results_df(self) -> pd.DataFrame:
        if not self.results:
            return pd.DataFrame()
        rows = []
        for result in self.results:
            row = {
                "split": result["split"],
                "train_size": result["train_size"],
                "test_size": result["test_size"],
                "train_score": result.get("train_score"),
                "test_score": result.get("test_score"),
                "score_degradation": result.get("score_degradation"),
            }
            if result.get("best_params"):
                for k, v in result["best_params"].items():
                    row[f"param_{k}"] = v
            rows.append(row)
        return pd.DataFrame(rows)

    def analyze_stability(self) -> Dict:
        """Analyze parameter stability across splits."""
        if not self.results:
            return {}
        all_params: Dict[str, List] = {}
        for result in self.results:
            if result.get("best_params"):
                for key, value in result["best_params"].items():
                    all_params.setdefault(key, []).append(value)
        stability = {}
        for param, values in all_params.items():
            stability[param] = "stable" if len(set(values)) == 1 else ("moderate" if len(set(values)) <= 3 else "unstable")
        return {"parameter_stability": stability, "parameter_values": all_params}
