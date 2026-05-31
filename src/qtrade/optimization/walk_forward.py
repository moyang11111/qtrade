"""Walk-forward validation for strategy optimization."""
from typing import Dict, List, Callable, Tuple
import pandas as pd
import numpy as np
from loguru import logger
from tqdm import tqdm


class WalkForwardValidator:
    """Walk-forward validation for time series strategies."""

    def __init__(self, strategy_class, param_grid: Dict[str, List],
                 objective_func: Callable,
                 n_splits: int = 5,
                 train_ratio: float = 0.7,
                 gap: int = 0,
                 retrain_every: int = 1):
        """Initialize walk-forward validator.

        Args:
            strategy_class: Strategy class to validate
            param_grid: Parameter grid to search
            objective_func: Function to maximize
            n_splits: Number of splits
            train_ratio: Ratio of training data
            gap: Gap between train and test (to prevent lookahead)
            retrain_every: Retrain every N periods
        """
        self.strategy_class = strategy_class
        self.param_grid = param_grid
        self.objective_func = objective_func
        self.n_splits = n_splits
        self.train_ratio = train_ratio
        self.gap = gap
        self.retrain_every = retrain_every
        self.results = []

    def _generate_splits(self, data: pd.DataFrame) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """Generate walk-forward splits."""
        n = len(data)
        split_size = n // self.n_splits
        splits = []

        for i in range(self.n_splits):
            train_end = int(split_size * (i + 1) * self.train_ratio)
            test_start = train_end + self.gap
            test_end = min(split_size * (i + 1), n)

            if test_start >= test_end:
                continue

            train_data = data.iloc[:train_end]
            test_data = data.iloc[test_start:test_end]
            splits.append((train_data, test_data))

        return splits

    def validate(self, df: pd.DataFrame, **fit_kwargs) -> Dict:
        """Run walk-forward validation.

        Args:
            df: Full dataset
            **fit_kwargs: Additional arguments for strategy.fit()

        Returns:
            Validation results
        """
        splits = self._generate_splits(df)
        logger.info(f"Walk-forward validation: {len(splits)} splits")

        self.results = []

        for i, (train_df, test_df) in enumerate(tqdm(splits, desc="Walk-Forward")):
            logger.debug(f"Split {i+1}/{len(splits)}: train={len(train_df)}, test={len(test_df)}")

            # Optimize on training data
            from .grid_search import GridSearchOptimizer
            optimizer = GridSearchOptimizer(
                self.strategy_class,
                self.param_grid,
                self.objective_func
            )
            opt_results = optimizer.optimize(train_df, **fit_kwargs)

            best_params = opt_results['best_params']
            train_score = opt_results['best_score']

            # Test on out-of-sample data
            try:
                strategy = self.strategy_class(**best_params)
                strategy.fit(train_df, **fit_kwargs)
                test_score = self.objective_func(strategy, test_df)

                result = {
                    'split': i + 1,
                    'train_size': len(train_df),
                    'test_size': len(test_df),
                    'best_params': best_params,
                    'train_score': train_score,
                    'test_score': test_score,
                    'score_degradation': train_score - test_score,
                }
                self.results.append(result)

            except Exception as e:
                logger.warning(f"Test failed for split {i+1}: {e}")
                self.results.append({
                    'split': i + 1,
                    'train_size': len(train_df),
                    'test_size': len(test_df),
                    'best_params': best_params,
                    'train_score': train_score,
                    'test_score': None,
                    'error': str(e),
                })

        # Aggregate results
        test_scores = [r['test_score'] for r in self.results if r['test_score'] is not None]

        summary = {
            'n_splits': len(splits),
            'avg_test_score': np.mean(test_scores) if test_scores else None,
            'std_test_score': np.std(test_scores) if test_scores else None,
            'min_test_score': np.min(test_scores) if test_scores else None,
            'max_test_score': np.max(test_scores) if test_scores else None,
            'avg_score_degradation': np.mean([r['score_degradation'] for r in self.results if 'score_degradation' in r]),
            'results': self.results,
        }

        logger.info(f"Avg test score: {summary['avg_test_score']:.4f} ± {summary['std_test_score']:.4f}")

        return summary

    def get_results_df(self) -> pd.DataFrame:
        """Get results as DataFrame."""
        if not self.results:
            return pd.DataFrame()

        rows = []
        for result in self.results:
            row = {
                'split': result['split'],
                'train_size': result['train_size'],
                'test_size': result['test_size'],
                'train_score': result['train_score'],
                'test_score': result.get('test_score'),
                'score_degradation': result.get('score_degradation'),
            }
            if 'best_params' in result:
                for k, v in result['best_params'].items():
                    row[f'param_{k}'] = v
            rows.append(row)

        return pd.DataFrame(rows)

    def analyze_stability(self) -> Dict:
        """Analyze parameter stability across splits."""
        if not self.results:
            return {}

        # Collect all parameters
        all_params = {}
        for result in self.results:
            if 'best_params' in result:
                for key, value in result['best_params'].items():
                    if key not in all_params:
                        all_params[key] = []
                    all_params[key].append(value)

        # Analyze stability
        stability = {}
        for param, values in all_params.items():
            if len(set(values)) == 1:
                stability[param] = 'stable'
            elif len(set(values)) <= 3:
                stability[param] = 'moderate'
            else:
                stability[param] = 'unstable'

        return {
            'parameter_stability': stability,
            'parameter_values': all_params,
        }
