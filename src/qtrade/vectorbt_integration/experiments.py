"""Batch experiment runner."""
from typing import Dict, List, Callable, Optional
from datetime import datetime
from pathlib import Path
import pandas as pd
import json
from loguru import logger


class ExperimentRunner:
    """Run batch experiments with different configurations."""

    def __init__(self, results_dir: str = 'experiments'):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.experiments: List[Dict] = []

    def run_experiment(self, name: str, func: Callable,
                      params: Dict, **kwargs) -> Dict:
        """Run a single experiment.

        Args:
            name: Experiment name
            func: Function to run (should return results dict)
            params: Parameters for the function
            **kwargs: Additional arguments
        """
        logger.info(f"Running experiment: {name}")
        start_time = datetime.now()

        try:
            # Run the function
            results = func(**params, **kwargs)

            # Record experiment
            experiment = {
                'name': name,
                'params': params,
                'results': results if isinstance(results, dict) else {'value': results},
                'start_time': start_time.isoformat(),
                'end_time': datetime.now().isoformat(),
                'duration_seconds': (datetime.now() - start_time).total_seconds(),
                'status': 'success',
            }

        except Exception as e:
            logger.error(f"Experiment '{name}' failed: {e}")
            experiment = {
                'name': name,
                'params': params,
                'results': {},
                'start_time': start_time.isoformat(),
                'end_time': datetime.now().isoformat(),
                'duration_seconds': (datetime.now() - start_time).total_seconds(),
                'status': 'failed',
                'error': str(e),
            }

        self.experiments.append(experiment)
        return experiment

    def run_batch(self, name_template: str, func: Callable,
                 param_grid: List[Dict], **kwargs) -> List[Dict]:
        """Run batch of experiments with parameter grid.

        Args:
            name_template: Template for experiment names (e.g., "exp_{i}")
            func: Function to run
            param_grid: List of parameter dictionaries
            **kwargs: Additional arguments
        """
        logger.info(f"Running batch of {len(param_grid)} experiments")
        results = []

        for i, params in enumerate(param_grid):
            name = name_template.format(i=i, **params)
            exp = self.run_experiment(name, func, params, **kwargs)
            results.append(exp)

        return results

    def run_grid_search(self, name_template: str, func: Callable,
                       param_space: Dict[str, List], **kwargs) -> List[Dict]:
        """Run grid search over parameter space.

        Args:
            name_template: Template for experiment names
            func: Function to run
            param_space: Dictionary mapping parameter names to lists of values
            **kwargs: Additional arguments
        """
        # Generate parameter grid
        import itertools
        keys = param_space.keys()
        values = param_space.values()
        param_grid = [dict(zip(keys, combo)) for combo in itertools.product(*values)]

        logger.info(f"Grid search: {len(param_grid)} combinations")
        return self.run_batch(name_template, func, param_grid, **kwargs)

    def get_results_df(self) -> pd.DataFrame:
        """Get experiment results as DataFrame."""
        if not self.experiments:
            return pd.DataFrame()

        rows = []
        for exp in self.experiments:
            row = {
                'name': exp['name'],
                'status': exp['status'],
                'duration': exp['duration_seconds'],
                **exp['params'],
                **exp['results'],
            }
            rows.append(row)

        return pd.DataFrame(rows)

    def get_best_experiment(self, metric: str, maximize: bool = True) -> Optional[Dict]:
        """Get best experiment by metric."""
        successful = [e for e in self.experiments if e['status'] == 'success']
        if not successful:
            return None

        best = max(successful,
                  key=lambda e: e['results'].get(metric, float('-inf') if maximize else float('inf')))
        return best

    def save_results(self, filename: str = 'experiment_results.json'):
        """Save all experiment results to JSON."""
        output_path = self.results_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(self.experiments, f, indent=2, ensure_ascii=False, default=str)
        logger.info(f"Saved {len(self.experiments)} experiments to {output_path}")

    def load_results(self, filename: str = 'experiment_results.json'):
        """Load experiment results from JSON."""
        input_path = self.results_dir / filename
        if not input_path.exists():
            logger.warning(f"No results file found at {input_path}")
            return

        with open(input_path, 'r', encoding='utf-8') as f:
            self.experiments = json.load(f)
        logger.info(f"Loaded {len(self.experiments)} experiments from {input_path}")

    def summary(self) -> Dict:
        """Get experiment summary."""
        successful = [e for e in self.experiments if e['status'] == 'success']
        failed = [e for e in self.experiments if e['status'] == 'failed']

        return {
            'total': len(self.experiments),
            'successful': len(successful),
            'failed': len(failed),
            'total_duration': sum(e['duration_seconds'] for e in self.experiments),
            'avg_duration': sum(e['duration_seconds'] for e in self.experiments) / len(self.experiments) if self.experiments else 0,
        }

    def compare_experiments(self, metric: str, top_n: int = 10) -> pd.DataFrame:
        """Compare top experiments by metric."""
        df = self.get_results_df()
        if df.empty or metric not in df.columns:
            return pd.DataFrame()

        df_sorted = df.sort_values(metric, ascending=False).head(top_n)
        return df_sorted
