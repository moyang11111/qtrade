"""Grid search optimization for strategy parameters."""
from typing import Dict, List, Callable, Any
import itertools
import pandas as pd
from loguru import logger
from tqdm import tqdm


class GridSearchOptimizer:
    """Grid search optimizer for strategy parameters."""

    def __init__(self, strategy_class, param_grid: Dict[str, List],
                 objective_func: Callable):
        """Initialize grid search optimizer.

        Args:
            strategy_class: Strategy class to optimize
            param_grid: Parameter grid to search
            objective_func: Function to maximize (takes strategy, returns score)
        """
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.objective_func = objective_func
        self.results = []

    def optimize(self, df: pd.DataFrame, **fit_kwargs) -> Dict:
        """Run grid search optimization.

        Args:
            df: Training data
            **fit_kwargs: Additional arguments for strategy.fit()

        Returns:
            Best parameters and results
        """
        # Generate all parameter combinations
        keys = self.param_grid.keys()
        values = self.param_grid.values()
        combinations = list(itertools.product(*values))

        logger.info(f"Grid search: {len(combinations)} parameter combinations")

        best_score = float('-inf')
        best_params = None
        self.results = []

        for combo in tqdm(combinations, desc="Grid Search"):
            params = dict(zip(keys, combo))

            try:
                # Create strategy instance
                strategy = self.strategy_class(**params)

                # Fit strategy
                strategy.fit(df, **fit_kwargs)

                # Evaluate
                score = self.objective_func(strategy, df)

                result = {
                    'params': params,
                    'score': score,
                }
                self.results.append(result)

                if score > best_score:
                    best_score = score
                    best_params = params

            except Exception as e:
                logger.warning(f"Failed with params {params}: {e}")
                self.results.append({
                    'params': params,
                    'score': None,
                    'error': str(e),
                })

        logger.info(f"Best score: {best_score:.4f}")
        logger.info(f"Best params: {best_params}")

        return {
            'best_params': best_params,
            'best_score': best_score,
            'all_results': self.results,
        }

    def get_results_df(self) -> pd.DataFrame:
        """Get results as DataFrame."""
        if not self.results:
            return pd.DataFrame()

        rows = []
        for result in self.results:
            row = {'score': result['score']}
            row.update(result['params'])
            if 'error' in result:
                row['error'] = result['error']
            rows.append(row)

        return pd.DataFrame(rows).sort_values('score', ascending=False)

    def get_top_n(self, n: int = 10) -> pd.DataFrame:
        """Get top N results."""
        df = self.get_results_df()
        return df.head(n)
