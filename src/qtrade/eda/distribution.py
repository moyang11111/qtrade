"""Feature distribution analyzer."""
from typing import Dict, List
import pandas as pd
import numpy as np
from scipy import stats


class DistributionAnalyzer:
    """Analyze feature distributions and statistical properties."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def basic_statistics(self) -> pd.DataFrame:
        """Compute basic descriptive statistics for all numeric columns."""
        numeric_df = self.df.select_dtypes(include=[np.number])

        stats_dict = {}
        for col in numeric_df.columns:
            series = numeric_df[col].dropna()
            stats_dict[col] = {
                'count': len(series),
                'mean': series.mean(),
                'std': series.std(),
                'min': series.min(),
                '25%': series.quantile(0.25),
                '50%': series.quantile(0.50),
                '75%': series.quantile(0.75),
                'max': series.max(),
                'skewness': series.skew(),
                'kurtosis': series.kurtosis(),
                'range': series.max() - series.min(),
                'iqr': series.quantile(0.75) - series.quantile(0.25),
                'cv': series.std() / series.mean() if series.mean() != 0 else np.nan,
            }

        return pd.DataFrame(stats_dict).T

    def normality_tests(self) -> Dict:
        """Test for normality using Shapiro-Wilk and Kolmogorov-Smirnov tests."""
        numeric_df = self.df.select_dtypes(include=[np.number])
        results = {}

        for col in numeric_df.columns:
            series = numeric_df[col].dropna()
            if len(series) < 8:
                continue

            # Shapiro-Wilk test (better for small samples)
            if len(series) <= 5000:
                try:
                    shapiro_stat, shapiro_p = stats.shapiro(series.sample(min(5000, len(series))))
                except:
                    shapiro_stat, shapiro_p = np.nan, np.nan
            else:
                shapiro_stat, shapiro_p = np.nan, np.nan

            # Kolmogorov-Smirnov test
            try:
                ks_stat, ks_p = stats.kstest(series, 'norm',
                                            args=(series.mean(), series.std()))
            except:
                ks_stat, ks_p = np.nan, np.nan

            # Anderson-Darling test
            try:
                ad_result = stats.anderson(series, dist='norm')
                ad_stat = ad_result.statistic
                ad_critical_5pct = ad_result.critical_values[2]  # 5% significance
                ad_normal = ad_stat < ad_critical_5pct
            except:
                ad_stat, ad_normal = np.nan, False

            results[col] = {
                'shapiro_statistic': shapiro_stat,
                'shapiro_p_value': shapiro_p,
                'shapiro_normal': shapiro_p > 0.05 if not np.isnan(shapiro_p) else False,
                'ks_statistic': ks_stat,
                'ks_p_value': ks_p,
                'ks_normal': ks_p > 0.05 if not np.isnan(ks_p) else False,
                'anderson_statistic': ad_stat,
                'anderson_normal': ad_normal,
                'skewness': series.skew(),
                'kurtosis': series.kurtosis(),
            }

        return results

    def distribution_fitting(self, column: str) -> Dict:
        """Fit various distributions to a column and find best fit."""
        series = self.df[column].dropna()
        if len(series) < 10:
            return {'error': 'Insufficient data'}

        # Test common distributions
        distributions = [
            ('normal', stats.norm),
            ('lognormal', stats.lognorm),
            ('exponential', stats.expon),
            ('gamma', stats.gamma),
            ('beta', stats.beta),
            ('weibull', stats.weibull_min),
        ]

        results = {}
        for name, dist in distributions:
            try:
                params = dist.fit(series)
                ks_stat, ks_p = stats.kstest(series, name, args=params)
                results[name] = {
                    'params': params,
                    'ks_statistic': ks_stat,
                    'ks_p_value': ks_p,
                    'fit_quality': ks_p,
                }
            except:
                results[name] = {'error': 'Fit failed'}

        # Find best fit
        valid_fits = {k: v for k, v in results.items() if 'error' not in v}
        if valid_fits:
            best_fit = max(valid_fits.items(), key=lambda x: x[1]['ks_p_value'])
            results['best_fit'] = {
                'distribution': best_fit[0],
                'ks_p_value': best_fit[1]['ks_p_value'],
                'params': best_fit[1]['params'],
            }

        return results

    def feature_importance_variance(self, threshold: float = 0.01) -> Dict:
        """Identify low-variance features that may not be useful."""
        numeric_df = self.df.select_dtypes(include=[np.number])
        variance = numeric_df.var()
        normalized_variance = variance / variance.sum() if variance.sum() > 0 else variance

        low_variance = normalized_variance[normalized_variance < threshold]

        return {
            'variance': variance.to_dict(),
            'normalized_variance': normalized_variance.to_dict(),
            'low_variance_features': low_variance.index.tolist(),
            'threshold': threshold,
        }

    def analyze_all(self) -> Dict:
        """Run all distribution analyses."""
        result = {
            'basic_statistics': self.basic_statistics().to_dict(),
            'normality_tests': self.normality_tests(),
            'variance_analysis': self.feature_importance_variance(),
        }
        return result
