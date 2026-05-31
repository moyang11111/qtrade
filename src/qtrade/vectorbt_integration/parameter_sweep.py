"""Parameter sweep and optimization."""
from typing import Dict, List, Callable, Optional, Tuple
import itertools
import pandas as pd
import numpy as np
from loguru import logger


class ParameterSweep:
    """Parameter sweep and optimization tools."""

    @staticmethod
    def generate_grid(param_space: Dict[str, List]) -> List[Dict]:
        """Generate parameter grid from parameter space.

        Args:
            param_space: Dictionary mapping parameter names to lists of values
        """
        keys = param_space.keys()
        values = param_space.values()
        grid = [dict(zip(keys, combo)) for combo in itertools.product(*values)]
        return grid

    @staticmethod
    def generate_random_samples(param_space: Dict[str, Tuple],
                               n_samples: int = 100,
                               seed: int = 42) -> List[Dict]:
        """Generate random parameter samples.

        Args:
            param_space: Dictionary mapping parameter names to (min, max) tuples
            n_samples: Number of samples to generate
            seed: Random seed
        """
        np.random.seed(seed)
        samples = []

        for _ in range(n_samples):
            sample = {}
            for name, (min_val, max_val) in param_space.items():
                if isinstance(min_val, int) and isinstance(max_val, int):
                    sample[name] = np.random.randint(min_val, max_val + 1)
                else:
                    sample[name] = np.random.uniform(min_val, max_val)
            samples.append(sample)

        return samples

    @staticmethod
    def generate_log_space(param_space: Dict[str, Tuple],
                          n_samples: int = 50) -> List[Dict]:
        """Generate parameters in log space.

        Args:
            param_space: Dictionary mapping parameter names to (min, max) tuples
            n_samples: Number of samples per parameter
        """
        samples = []

        for _ in range(n_samples):
            sample = {}
            for name, (min_val, max_val) in param_space.items():
                log_min = np.log10(min_val)
                log_max = np.log10(max_val)
                log_val = np.random.uniform(log_min, log_max)
                sample[name] = 10 ** log_val
            samples.append(sample)

        return samples

    @staticmethod
    def walk_forward_split(data: pd.DataFrame,
                          n_splits: int = 5,
                          train_ratio: float = 0.7,
                          gap: int = 0) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """Generate walk-forward train/test splits.

        Args:
            data: Time series data
            n_splits: Number of splits
            train_ratio: Ratio of training data
            gap: Gap between train and test (to prevent lookahead)
        """
        n = len(data)
        split_size = n // n_splits
        splits = []

        for i in range(n_splits):
            train_end = int(split_size * (i + 1) * train_ratio)
            test_start = train_end + gap
            test_end = min(split_size * (i + 1), n)

            if test_start >= test_end:
                continue

            train_data = data.iloc[:train_end]
            test_data = data.iloc[test_start:test_end]
            splits.append((train_data, test_data))

        return splits

    @staticmethod
    def expanding_window_split(data: pd.DataFrame,
                              n_splits: int = 5,
                              initial_train_ratio: float = 0.5,
                              gap: int = 0) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """Generate expanding window train/test splits.

        Args:
            data: Time series data
            n_splits: Number of splits
            initial_train_ratio: Initial training ratio
            gap: Gap between train and test
        """
        n = len(data)
        splits = []

        for i in range(n_splits):
            train_ratio = initial_train_ratio + (1 - initial_train_ratio) * i / n_splits
            train_end = int(n * train_ratio)
            test_start = train_end + gap
            test_end = min(train_end + int(n * (1 - train_ratio) / n_splits), n)

            if test_start >= test_end:
                continue

            train_data = data.iloc[:train_end]
            test_data = data.iloc[test_start:test_end]
            splits.append((train_data, test_data))

        return splits

    def optimize(self, objective: Callable,
                param_space: Dict[str, List],
                maximize: bool = True) -> Dict:
        """Simple grid search optimization.

        Args:
            objective: Function to optimize (takes params dict, returns score)
            param_space: Parameter space to search
            maximize: Whether to maximize or minimize
        """
        grid = self.generate_grid(param_space)
        logger.info(f"Optimizing over {len(grid)} parameter combinations")

        best_score = float('-inf') if maximize else float('inf')
        best_params = None
        all_results = []

        for params in grid:
            try:
                score = objective(**params)
                all_results.append({'params': params, 'score': score})

                if (maximize and score > best_score) or (not maximize and score < best_score):
                    best_score = score
                    best_params = params

            except Exception as e:
                logger.warning(f"Failed with params {params}: {e}")
                all_results.append({'params': params, 'score': None, 'error': str(e)})

        return {
            'best_params': best_params,
            'best_score': best_score,
            'all_results': all_results,
            'n_evaluated': len([r for r in all_results if r['score'] is not None]),
        }

    def sensitivity_analysis(self, objective: Callable,
                           base_params: Dict,
                           param_ranges: Dict[str, List],
                           n_points: int = 10) -> Dict:
        """Analyze parameter sensitivity.

        Args:
            objective: Objective function
            base_params: Base parameter values
            param_ranges: Ranges to test for each parameter
            n_points: Number of points to test per parameter
        """
        sensitivity = {}

        for param_name, param_range in param_ranges.items():
            scores = []
            test_values = np.linspace(param_range[0], param_range[1], n_points)

            for value in test_values:
                params = base_params.copy()
                params[param_name] = value

                try:
                    score = objective(**params)
                    scores.append({'value': value, 'score': score})
                except Exception as e:
                    logger.warning(f"Failed at {param_name}={value}: {e}")

            if scores:
                scores_df = pd.DataFrame(scores)
                sensitivity[param_name] = {
                    'scores': scores,
                    'correlation': scores_df['value'].corr(scores_df['score']),
                    'min_score': scores_df['score'].min(),
                    'max_score': scores_df['score'].max(),
                    'sensitivity': scores_df['score'].std() / scores_df['score'].mean(),
                }

        return sensitivity
