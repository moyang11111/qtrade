"""Feature stability analyzer - PSI, KS tests."""
from typing import Dict, Optional
import pandas as pd
import numpy as np
from scipy import stats


class StabilityAnalyzer:
    """Analyze feature stability over time."""

    def __init__(self, df: pd.DataFrame, date_col: Optional[str] = None):
        self.df = df
        self.date_col = date_col or (df.index.name if isinstance(df.index, pd.DatetimeIndex) else None)

    def psi(self, expected: pd.Series, actual: pd.Series,
            bins: int = 10) -> float:
        """Calculate Population Stability Index (PSI).

        PSI < 0.1: No significant change
        PSI 0.1-0.25: Moderate change
        PSI > 0.25: Significant change
        """
        # Create bins based on expected distribution
        breakpoints = np.linspace(0, 100, bins + 1)
        expected_bins = np.percentile(expected.dropna(), breakpoints)
        expected_bins[0] = -np.inf
        expected_bins[-1] = np.inf

        # Calculate proportions
        expected_counts = np.histogram(expected.dropna(), bins=expected_bins)[0]
        actual_counts = np.histogram(actual.dropna(), bins=expected_bins)[0]

        # Avoid division by zero
        expected_props = (expected_counts + 1e-6) / (len(expected) + 1e-6 * bins)
        actual_props = (actual_counts + 1e-6) / (len(actual) + 1e-6 * bins)

        # Calculate PSI
        psi = np.sum((actual_props - expected_props) * np.log(actual_props / expected_props))
        return float(psi)

    def psi_by_period(self, column: str, period: str = 'M') -> Dict:
        """Calculate PSI for a feature across time periods."""
        if not self.date_col:
            return {'error': 'No date column specified'}

        df = self.df.copy()
        if isinstance(df.index, pd.DatetimeIndex):
            df['_date'] = df.index
        else:
            df['_date'] = pd.to_datetime(df[self.date_col])

        df['_period'] = df['_date'].dt.to_period(period)
        periods = sorted(df['_period'].unique())

        if len(periods) < 2:
            return {'error': 'Need at least 2 periods'}

        # Use first period as baseline
        baseline = df[df['_period'] == periods[0]][column].dropna()
        psi_values = {}

        for period_val in periods[1:]:
            current = df[df['_period'] == period_val][column].dropna()
            if len(current) > 0 and len(baseline) > 0:
                psi_val = self.psi(baseline, current)
                psi_values[str(period_val)] = {
                    'psi': psi_val,
                    'stability': 'stable' if psi_val < 0.1 else 'moderate' if psi_val < 0.25 else 'unstable',
                    'sample_size': len(current),
                }

        return {
            'baseline_period': str(periods[0]),
            'baseline_size': len(baseline),
            'psi_by_period': psi_values,
        }

    def ks_test(self, sample1: pd.Series, sample2: pd.Series) -> Dict:
        """Two-sample Kolmogorov-Smirnov test."""
        stat, p_value = stats.ks_2samp(sample1.dropna(), sample2.dropna())
        return {
            'statistic': float(stat),
            'p_value': float(p_value),
            'significant': p_value < 0.05,
            'interpretation': 'different distributions' if p_value < 0.05 else 'same distribution',
        }

    def ks_by_period(self, column: str, period: str = 'M') -> Dict:
        """Run KS test comparing each period to baseline."""
        if not self.date_col:
            return {'error': 'No date column specified'}

        df = self.df.copy()
        if isinstance(df.index, pd.DatetimeIndex):
            df['_date'] = df.index
        else:
            df['_date'] = pd.to_datetime(df[self.date_col])

        df['_period'] = df['_date'].dt.to_period(period)
        periods = sorted(df['_period'].unique())

        if len(periods) < 2:
            return {'error': 'Need at least 2 periods'}

        baseline = df[df['_period'] == periods[0]][column].dropna()
        ks_results = {}

        for period_val in periods[1:]:
            current = df[df['_period'] == period_val][column].dropna()
            if len(current) > 0 and len(baseline) > 0:
                ks_results[str(period_val)] = self.ks_test(baseline, current)

        return {
            'baseline_period': str(periods[0]),
            'ks_by_period': ks_results,
        }

    def rolling_statistics(self, column: str, window: int = 30) -> pd.DataFrame:
        """Calculate rolling statistics for stability monitoring."""
        series = self.df[column]
        rolling_stats = pd.DataFrame({
            'rolling_mean': series.rolling(window).mean(),
            'rolling_std': series.rolling(window).std(),
            'rolling_min': series.rolling(window).min(),
            'rolling_max': series.rolling(window).max(),
            'rolling_skew': series.rolling(window).skew(),
            'rolling_kurtosis': series.rolling(window).kurt(),
        })

        # Calculate coefficient of variation
        rolling_stats['rolling_cv'] = rolling_stats['rolling_std'] / rolling_stats['rolling_mean']

        return rolling_stats

    def drift_detection(self, column: str, window: int = 30) -> Dict:
        """Detect concept drift using statistical tests."""
        series = self.df[column].dropna()
        if len(series) < 2 * window:
            return {'error': 'Insufficient data for drift detection'}

        # Split into windows
        n_windows = len(series) // window
        windows = [series.iloc[i*window:(i+1)*window] for i in range(n_windows)]

        # Compare consecutive windows
        drift_results = []
        for i in range(len(windows) - 1):
            ks_result = self.ks_test(windows[i], windows[i+1])
            drift_results.append({
                'window_1': i,
                'window_2': i + 1,
                'ks_statistic': ks_result['statistic'],
                'p_value': ks_result['p_value'],
                'drift_detected': ks_result['significant'],
            })

        # Overall drift assessment
        drift_count = sum(1 for r in drift_results if r['drift_detected'])
        drift_ratio = drift_count / len(drift_results) if drift_results else 0

        return {
            'n_windows': n_windows,
            'window_size': window,
            'drift_tests': drift_results,
            'drift_count': drift_count,
            'drift_ratio': drift_ratio,
            'drift_detected': drift_ratio > 0.3,
        }

    def analyze_all(self, columns: Optional[list] = None,
                   period: str = 'M') -> Dict:
        """Run all stability analyses."""
        if columns is None:
            columns = self.df.select_dtypes(include=[np.number]).columns.tolist()

        result = {}
        for col in columns:
            col_result = {
                'psi_analysis': self.psi_by_period(col, period),
                'ks_analysis': self.ks_by_period(col, period),
                'drift_detection': self.drift_detection(col),
            }
            result[col] = col_result

        return result
