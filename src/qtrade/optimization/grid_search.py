"""Grid search optimization for SignalGenerator strategies."""
from __future__ import annotations

import itertools
from typing import Callable, Dict, List, Any

import pandas as pd
from loguru import logger
from tqdm import tqdm


def expand_param_space(param_space: Dict[str, Any]) -> Dict[str, List]:
    """Expand an Optuna-style param_space spec into a concrete grid.

    Supported specs per parameter:
      - a list of values (categorical)
      - {"type": "int", "low": ..., "high": ..., "step": ...}
      - {"type": "float", "low": ..., "high": ..., "step": ...}
      - {"type": "categorical", "choices": [...]}
    """
    grid: Dict[str, List] = {}
    for name, spec in param_space.items():
        if isinstance(spec, list):
            grid[name] = list(spec)
        elif isinstance(spec, dict):
            ptype = spec.get("type", "int")
            if ptype == "categorical":
                grid[name] = list(spec["choices"])
            elif ptype == "int":
                low, high, step = spec["low"], spec["high"], spec.get("step", 1)
                grid[name] = list(range(int(low), int(high) + 1, int(step)))
            elif ptype == "float":
                low, high, step = spec["low"], spec["high"], spec.get("step", 1.0)
                values = []
                v = float(low)
                while v <= float(high) + 1e-12:
                    values.append(round(v, 8))
                    v += float(step)
                grid[name] = values
            elif ptype == "discrete_uniform":
                low, high, step = spec["low"], spec["high"], spec.get("step", 1.0)
                values = []
                v = float(low)
                while v <= float(high) + 1e-12:
                    values.append(round(v, 8))
                    v += float(step)
                grid[name] = values
            else:
                raise ValueError(f"Unknown parameter type for {name}: {ptype}")
        else:
            raise ValueError(f"Unknown parameter spec for {name}: {spec!r}")
    return grid


class GridSearchOptimizer:
    """Grid search optimizer for SignalGenerator strategies.

    Strategies are created with ``strategy_class(config_dict)`` and scored by
    ``objective_func(strategy, df)`` — the objective is responsible for calling
    ``generate_signals()`` and running the backtest.
    """

    def __init__(self, strategy_class, param_grid: Dict[str, List],
                 objective_func: Callable,
                 constraints: List[str] | None = None,
                 strategy_name: str | None = None):
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.objective_func = objective_func
        self.constraints = constraints or []
        self.strategy_name = strategy_name or getattr(strategy_class, "__name__", "strategy")
        self.results: List[Dict] = []

    def _params_ok(self, params: Dict) -> bool:
        for expr in self.constraints:
            try:
                if not eval(expr, {"__builtins__": {}}, dict(params)):
                    return False
            except Exception as exc:  # unparsable constraint -> fail closed
                logger.warning("Skipping constraint '{}': {}", expr, exc)
                return False
        return True

    def optimize(self, df: pd.DataFrame) -> Dict:
        """Run grid search. Returns best_params / best_score / all_results."""
        keys = list(self.param_grid.keys())
        values = [self.param_grid[k] for k in keys]
        combinations = list(itertools.product(*values))

        logger.info("Grid search: {} parameter combinations", len(combinations))

        best_score = float("-inf")
        best_params = None
        self.results = []

        for combo in tqdm(combinations, desc="Grid Search"):
            params = dict(zip(keys, combo))

            if not self._params_ok(params):
                self.results.append({"params": params, "score": None, "error": "constraint"})
                continue

            try:
                strategy = self.strategy_class({"name": self.strategy_name, **params})
                score = self.objective_func(strategy, df)
                self.results.append({"params": params, "score": score})
                if score is not None and score > best_score:
                    best_score = score
                    best_params = params
            except Exception as e:
                logger.warning("Failed with params {}: {}", params, e)
                self.results.append({"params": params, "score": None, "error": str(e)})

        logger.info("Best score: {}", best_score)
        logger.info("Best params: {}", best_params)

        return {
            "best_params": best_params,
            "best_score": best_score,
            "all_results": self.results,
        }

    def get_results_df(self) -> pd.DataFrame:
        if not self.results:
            return pd.DataFrame()
        rows = []
        for result in self.results:
            row = {"score": result.get("score")}
            row.update(result["params"])
            if "error" in result:
                row["error"] = result["error"]
            rows.append(row)
        return pd.DataFrame(rows).sort_values("score", ascending=False)

    def get_top_n(self, n: int = 10) -> pd.DataFrame:
        return self.get_results_df().head(n)
