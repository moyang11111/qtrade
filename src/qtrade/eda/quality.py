"""Data quality analyzer - missing values, outliers, extreme values."""
from typing import Dict, Optional
import pandas as pd
import numpy as np
from loguru import logger


class DataQualityAnalyzer:
    """Analyze data quality issues: missing values, outliers, extreme values."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.issues = {}

    def check_missing(self, threshold: float = 0.05) -> Dict:
        """Check missing values and patterns.

        Args:
            threshold: Missing ratio threshold for warning

        Returns:
            Dictionary with missing value statistics
        """
        missing = self.df.isnull()
        missing_count = missing.sum()
        missing_ratio = missing.mean()

        result = {
            'total_missing': int(missing_count.sum()),
            'missing_by_column': missing_count.to_dict(),
            'missing_ratio_by_column': missing_ratio.to_dict(),
            'columns_with_missing': missing_count[missing_count > 0].to_dict(),
            'high_missing_columns': missing_ratio[missing_ratio > threshold].to_dict(),
            'rows_with_any_missing': int(missing.any(axis=1).sum()),
            'rows_all_missing': int(missing.all(axis=1).sum()),
        }

        # Check for missing patterns (consecutive missing)
        missing_patterns = {}
        for col in self.df.columns:
            if missing_count[col] > 0:
                col_missing = missing[col]
                # Find consecutive missing blocks
                blocks = []
                in_block = False
                block_start = None
                for idx, val in col_missing.items():
                    if val and not in_block:
                        in_block = True
                        block_start = idx
                    elif not val and in_block:
                        in_block = False
                        blocks.append((block_start, idx))
                if in_block:
                    blocks.append((block_start, col_missing.index[-1]))

                if blocks:
                    missing_patterns[col] = {
                        'num_blocks': len(blocks),
                        'max_block_size': max((b[1] - b[0]).days if isinstance(b[0], pd.Timestamp) else 1 for b in blocks),
                        'blocks': blocks[:5],  # First 5 blocks
                    }

        result['missing_patterns'] = missing_patterns

        # Log warnings
        if result['total_missing'] > 0:
            logger.warning(f"Found {result['total_missing']} missing values")
        if len(result['high_missing_columns']) > 0:
            logger.warning(f"{len(result['high_missing_columns'])} columns have >{threshold*100}% missing")

        return result

    def check_outliers(self, method: str = 'iqr', threshold: float = 1.5) -> Dict:
        """Detect outliers using IQR or Z-score method.

        Args:
            method: 'iqr' or 'zscore'
            threshold: Outlier threshold (1.5 for IQR, 3.0 for z-score)

        Returns:
            Dictionary with outlier statistics
        """
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns
        outliers = {}

        for col in numeric_cols:
            series = self.df[col].dropna()
            if len(series) == 0:
                continue

            if method == 'iqr':
                q1 = series.quantile(0.25)
                q3 = series.quantile(0.75)
                iqr = q3 - q1
                lower = q1 - threshold * iqr
                upper = q3 + threshold * iqr
                outlier_mask = (series < lower) | (series > upper)
            elif method == 'zscore':
                mean = series.mean()
                std = series.std()
                z_scores = np.abs((series - mean) / std)
                outlier_mask = z_scores > threshold
                lower = mean - threshold * std
                upper = mean + threshold * std
            else:
                raise ValueError(f"Unknown method: {method}")

            outlier_count = outlier_mask.sum()
            if outlier_count > 0:
                outliers[col] = {
                    'count': int(outlier_count),
                    'ratio': float(outlier_count / len(series)),
                    'lower_bound': float(lower),
                    'upper_bound': float(upper),
                    'min_outlier': float(series[outlier_mask].min()),
                    'max_outlier': float(series[outlier_mask].max()),
                    'outlier_indices': series[outlier_mask].index[:10].tolist(),  # First 10
                }

        result = {
            'method': method,
            'threshold': threshold,
            'total_outliers': sum(v['count'] for v in outliers.values()),
            'columns_with_outliers': len(outliers),
            'outliers_by_column': outliers,
        }

        if result['total_outliers'] > 0:
            logger.warning(f"Found {result['total_outliers']} outliers in {len(outliers)} columns")

        return result

    def check_extreme_values(self, percentile_low: float = 0.01,
                            percentile_high: float = 0.99) -> Dict:
        """Check for extreme values (very high or very low).

        Args:
            percentile_low: Lower percentile threshold
            percentile_high: Upper percentile threshold

        Returns:
            Dictionary with extreme value statistics
        """
        numeric_cols = self.df.select_dtypes(include=[np.number]).columns
        extremes = {}

        for col in numeric_cols:
            series = self.df[col].dropna()
            if len(series) == 0:
                continue

            low_threshold = series.quantile(percentile_low)
            high_threshold = series.quantile(percentile_high)

            low_extremes = series[series < low_threshold]
            high_extremes = series[series > high_threshold]

            if len(low_extremes) > 0 or len(high_extremes) > 0:
                extremes[col] = {
                    'low_threshold': float(low_threshold),
                    'high_threshold': float(high_threshold),
                    'low_extreme_count': int(len(low_extremes)),
                    'high_extreme_count': int(len(high_extremes)),
                    'low_extreme_values': low_extremes.head(5).tolist(),
                    'high_extreme_values': high_extremes.head(5).tolist(),
                }

        result = {
            'percentile_range': (percentile_low, percentile_high),
            'columns_with_extremes': len(extremes),
            'extremes_by_column': extremes,
        }

        return result

    def check_duplicates(self) -> Dict:
        """Check for duplicate rows and columns."""
        duplicate_rows = self.df.duplicated().sum()
        duplicate_cols = self.df.T.duplicated().sum()

        result = {
            'duplicate_rows': int(duplicate_rows),
            'duplicate_columns': int(duplicate_cols),
            'duplicate_row_ratio': float(duplicate_rows / len(self.df)) if len(self.df) > 0 else 0,
        }

        if duplicate_rows > 0:
            logger.warning(f"Found {duplicate_rows} duplicate rows")
        if duplicate_cols > 0:
            logger.warning(f"Found {duplicate_cols} duplicate columns")

        return result

    def check_constant_columns(self) -> Dict:
        """Check for constant or near-constant columns."""
        constant_cols = []
        near_constant_cols = []

        for col in self.df.columns:
            nunique = self.df[col].nunique()
            if nunique == 1:
                constant_cols.append(col)
            elif nunique <= 5 and self.df[col].value_counts(normalize=True).max() > 0.95:
                near_constant_cols.append(col)

        result = {
            'constant_columns': constant_cols,
            'near_constant_columns': near_constant_cols,
            'total_constant': len(constant_cols) + len(near_constant_cols),
        }

        if result['total_constant'] > 0:
            logger.warning(f"Found {result['total_constant']} constant/near-constant columns")

        return result

    def analyze_all(self) -> Dict:
        """Run all data quality checks."""
        logger.info("Running comprehensive data quality analysis...")

        result = {
            'shape': {'rows': len(self.df), 'columns': len(self.df.columns)},
            'dtypes': self.df.dtypes.astype(str).to_dict(),
            'missing': self.check_missing(),
            'outliers_iqr': self.check_outliers(method='iqr'),
            'outliers_zscore': self.check_outliers(method='zscore'),
            'extreme_values': self.check_extreme_values(),
            'duplicates': self.check_duplicates(),
            'constant_columns': self.check_constant_columns(),
        }

        # Overall quality score
        issues = []
        if result['missing']['total_missing'] > 0:
            issues.append('missing')
        if result['outliers_iqr']['total_outliers'] > 0:
            issues.append('outliers')
        if result['duplicates']['duplicate_rows'] > 0:
            issues.append('duplicates')
        if result['constant_columns']['total_constant'] > 0:
            issues.append('constant_columns')

        result['quality_score'] = max(0, 100 - len(issues) * 10)
        result['issues_found'] = issues

        self.issues = result
        return result

    def handle_missing(self, strategy: str = 'drop', fill_value: Optional[float] = None) -> pd.DataFrame:
        """Handle missing values.

        Args:
            strategy: 'drop', 'fill', 'ffill', 'bfill', 'interpolate'
            fill_value: Value to use if strategy='fill'

        Returns:
            Cleaned DataFrame
        """
        df_clean = self.df.copy()

        if strategy == 'drop':
            df_clean = df_clean.dropna()
        elif strategy == 'fill':
            if fill_value is None:
                raise ValueError("fill_value required for 'fill' strategy")
            df_clean = df_clean.fillna(fill_value)
        elif strategy == 'ffill':
            df_clean = df_clean.fillna(method='ffill')
        elif strategy == 'bfill':
            df_clean = df_clean.fillna(method='bfill')
        elif strategy == 'interpolate':
            df_clean = df_clean.interpolate()
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        logger.info(f"Handled missing values using '{strategy}' strategy")
        return df_clean

    def handle_outliers(self, method: str = 'clip', threshold: float = 1.5) -> pd.DataFrame:
        """Handle outliers.

        Args:
            method: 'clip', 'remove', 'winsorize'
            threshold: IQR multiplier

        Returns:
            Cleaned DataFrame
        """
        df_clean = self.df.copy()
        numeric_cols = df_clean.select_dtypes(include=[np.number]).columns

        for col in numeric_cols:
            series = df_clean[col].dropna()
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - threshold * iqr
            upper = q3 + threshold * iqr

            if method == 'clip':
                df_clean[col] = df_clean[col].clip(lower, upper)
            elif method == 'remove':
                mask = (df_clean[col] >= lower) & (df_clean[col] <= upper)
                df_clean = df_clean[mask]
            elif method == 'winsorize':
                df_clean.loc[df_clean[col] < lower, col] = lower
                df_clean.loc[df_clean[col] > upper, col] = upper

        logger.info(f"Handled outliers using '{method}' method")
        return df_clean
