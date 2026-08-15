"""Bayesian optimization for SignalGenerator strategies (Optuna)."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import pandas as pd
from loguru import logger


class BayesianOptimizer:
    """Bayesian optimizer for SignalGenerator strategies.

    Strategies are created with ``strategy_class(config_dict)`` and scored by
    ``objective_func(strategy, df)``.
    """

    def __init__(self, strategy_class, param_space: Dict[str, Any],
                 objective_func: Callable, direction: str = "maximize",
                 constraints: List[str] | None = None,
                 strategy_name: str | None = None):
        self.strategy_class = strategy_class
        self.param_space = param_space
        self.objective_func = objective_func
        self.direction = direction
        self.constraints = constraints or []
        self.strategy_name = strategy_name or getattr(strategy_class, "__name__", "strategy")
        self.study = None

        try:
            import optuna
            self.optuna = optuna
        except ImportError:
            raise ImportError("Optuna not installed. Install with: pip install optuna")

    def _params_ok(self, params: Dict) -> bool:
        for expr in self.constraints:
            try:
                if not eval(expr, {"__builtins__": {}}, dict(params)):
                    return False
            except Exception:
                return False
        return True

    def _create_trial_params(self, trial) -> Dict:
        params: Dict[str, Any] = {}
        for name, spec in self.param_space.items():
            if isinstance(spec, dict):
                param_type = spec.get("type", "int")
                if param_type == "int":
                    params[name] = trial.suggest_int(name, spec["low"], spec["high"], step=spec.get("step", 1))
                elif param_type == "float":
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"], log=spec.get("log", False))
                elif param_type == "categorical":
                    params[name] = trial.suggest_categorical(name, spec["choices"])
                elif param_type == "discrete_uniform":
                    params[name] = trial.suggest_float(name, spec["low"], spec["high"], step=spec.get("step", 1.0))
                else:
                    raise ValueError(f"Unknown parameter type for {name}: {param_type}")
            elif isinstance(spec, list):
                params[name] = trial.suggest_categorical(name, spec)
            else:
                raise ValueError(f"Unknown parameter spec for {name}: {spec!r}")
        return params

    def optimize(self, df: pd.DataFrame, n_trials: int = 100,
                 timeout: Optional[float] = None) -> Dict:
        """Run Bayesian optimization. Returns best_params / best_score / study."""
        def objective(trial):
            params = self._create_trial_params(trial)
            if not self._params_ok(params):
                return float("-inf") if self.direction == "maximize" else float("inf")

            try:
                strategy = self.strategy_class({"name": self.strategy_name, **params})
                score = self.objective_func(strategy, df)
                return score
            except Exception as e:
                logger.warning("Trial failed: {}", e)
                return float("-inf") if self.direction == "maximize" else float("inf")

        self.study = self.optuna.create_study(direction=self.direction)
        self.study.optimize(objective, n_trials=n_trials, timeout=timeout, show_progress_bar=True)

        logger.info("Best score: {:.4f}", self.study.best_value)
        logger.info("Best params: {}", self.study.best_params)

        return {
            "best_params": self.study.best_params,
            "best_score": self.study.best_value,
            "study": self.study,
        }

    def get_trials_df(self) -> pd.DataFrame:
        if self.study is None:
            return pd.DataFrame()
        return self.study.trials_dataframe()

    def plot_optimization_history(self, filename: str = "optimization_history.html"):
        if self.study is None:
            logger.warning("No study to plot")
            return
        try:
            fig = self.optuna.visualization.plot_optimization_history(self.study)
            fig.write_html(filename)
            logger.info("Saved optimization history to {}", filename)
        except Exception as e:
            logger.error("Failed to plot: {}", e)

    def plot_param_importances(self, filename: str = "param_importances.html"):
        if self.study is None:
            logger.warning("No study to plot")
            return
        try:
            fig = self.optuna.visualization.plot_param_importances(self.study)
            fig.write_html(filename)
            logger.info("Saved parameter importances to {}", filename)
        except Exception as e:
            logger.error("Failed to plot: {}", e)

    def plot_parallel_coordinate(self, filename: str = "parallel_coordinate.html"):
        if self.study is None:
            logger.warning("No study to plot")
            return
        try:
            fig = self.optuna.visualization.plot_parallel_coordinate(self.study)
            fig.write_html(filename)
            logger.info("Saved parallel coordinate plot to {}", filename)
        except Exception as e:
            logger.error("Failed to plot: {}", e)
