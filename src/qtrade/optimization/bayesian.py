"""Bayesian optimization using Optuna."""
from typing import Dict, List, Callable, Any, Optional
import pandas as pd
from loguru import logger


class BayesianOptimizer:
    """Bayesian optimization using Optuna."""

    def __init__(self, strategy_class, param_space: Dict[str, Any],
                 objective_func: Callable, direction: str = 'maximize'):
        """Initialize Bayesian optimizer.

        Args:
            strategy_class: Strategy class to optimize
            param_space: Parameter space definition
            objective_func: Function to optimize
            direction: 'maximize' or 'minimize'
        """
        self.strategy_class = strategy_class
        self.param_space = param_space
        self.objective_func = objective_func
        self.direction = direction
        self.study = None

        try:
            import optuna
            self.optuna = optuna
        except ImportError:
            raise ImportError("Optuna not installed. Install with: pip install optuna")

    def _create_trial_params(self, trial) -> Dict:
        """Create parameters for a trial."""
        params = {}

        for name, spec in self.param_space.items():
            if isinstance(spec, dict):
                param_type = spec.get('type', 'int')

                if param_type == 'int':
                    params[name] = trial.suggest_int(
                        name, spec['low'], spec['high']
                    )
                elif param_type == 'float':
                    params[name] = trial.suggest_float(
                        name, spec['low'], spec['high'],
                        log=spec.get('log', False)
                    )
                elif param_type == 'categorical':
                    params[name] = trial.suggest_categorical(
                        name, spec['choices']
                    )
                elif param_type == 'discrete_uniform':
                    params[name] = trial.suggest_float(
                        name, spec['low'], spec['high'],
                        step=spec.get('step', 1.0)
                    )
            elif isinstance(spec, list):
                # Categorical from list
                params[name] = trial.suggest_categorical(name, spec)
            else:
                raise ValueError(f"Unknown parameter spec: {spec}")

        return params

    def optimize(self, df: pd.DataFrame, n_trials: int = 100,
                 timeout: Optional[float] = None, **fit_kwargs) -> Dict:
        """Run Bayesian optimization.

        Args:
            df: Training data
            n_trials: Number of trials
            timeout: Timeout in seconds
            **fit_kwargs: Additional arguments for strategy.fit()

        Returns:
            Best parameters and study
        """
        # Create objective function for Optuna
        def objective(trial):
            params = self._create_trial_params(trial)

            try:
                # Create strategy instance
                strategy = self.strategy_class(**params)

                # Fit strategy
                strategy.fit(df, **fit_kwargs)

                # Evaluate
                score = self.objective_func(strategy, df)

                return score

            except Exception as e:
                logger.warning(f"Trial failed: {e}")
                return float('-inf') if self.direction == 'maximize' else float('inf')

        # Create study
        self.study = self.optuna.create_study(direction=self.direction)

        # Run optimization
        self.study.optimize(
            objective,
            n_trials=n_trials,
            timeout=timeout,
            show_progress_bar=True,
        )

        logger.info(f"Best score: {self.study.best_value:.4f}")
        logger.info(f"Best params: {self.study.best_params}")

        return {
            'best_params': self.study.best_params,
            'best_score': self.study.best_value,
            'study': self.study,
        }

    def get_trials_df(self) -> pd.DataFrame:
        """Get trials as DataFrame."""
        if self.study is None:
            return pd.DataFrame()

        return self.study.trials_dataframe()

    def plot_optimization_history(self, filename: str = 'optimization_history.html'):
        """Plot optimization history."""
        if self.study is None:
            logger.warning("No study to plot")
            return

        try:
            fig = self.optuna.visualization.plot_optimization_history(self.study)
            fig.write_html(filename)
            logger.info(f"Saved optimization history to {filename}")
        except Exception as e:
            logger.error(f"Failed to plot: {e}")

    def plot_param_importances(self, filename: str = 'param_importances.html'):
        """Plot parameter importances."""
        if self.study is None:
            logger.warning("No study to plot")
            return

        try:
            fig = self.optuna.visualization.plot_param_importances(self.study)
            fig.write_html(filename)
            logger.info(f"Saved parameter importances to {filename}")
        except Exception as e:
            logger.error(f"Failed to plot: {e}")

    def plot_parallel_coordinate(self, filename: str = 'parallel_coordinate.html'):
        """Plot parallel coordinate plot."""
        if self.study is None:
            logger.warning("No study to plot")
            return

        try:
            fig = self.optuna.visualization.plot_parallel_coordinate(self.study)
            fig.write_html(filename)
            logger.info(f"Saved parallel coordinate plot to {filename}")
        except Exception as e:
            logger.error(f"Failed to plot: {e}")
