"""Main EDA analyzer combining all components."""
from typing import Dict, Optional
import pandas as pd
from loguru import logger

from .quality import DataQualityAnalyzer
from .distribution import DistributionAnalyzer
from .correlation import CorrelationAnalyzer
from .stability import StabilityAnalyzer
from .report import EDAReportGenerator


class EDAAnalyzer:
    """Comprehensive EDA analyzer combining all analysis components."""

    def __init__(self, df: pd.DataFrame, date_col: Optional[str] = None):
        self.df = df
        self.date_col = date_col

        self.quality = DataQualityAnalyzer(df)
        self.distribution = DistributionAnalyzer(df)
        self.correlation = CorrelationAnalyzer(df)
        self.stability = StabilityAnalyzer(df, date_col)

    def analyze_all(self, target: Optional[str] = None) -> Dict:
        """Run all EDA analyses."""
        logger.info("Running comprehensive EDA analysis...")

        result = {
            'quality': self.quality.analyze_all(),
            'distribution': self.distribution.analyze_all(),
            'correlation': self.correlation.analyze_all(target),
            'stability': self.stability.analyze_all(),
        }

        return result

    def generate_report(self, output_dir: str = 'reports/eda',
                       filename: str = 'eda_report.html') -> str:
        """Generate comprehensive EDA report."""
        reporter = EDAReportGenerator(self.df, output_dir)
        return reporter.generate_html_report(filename)

    def clean_data(self, handle_missing: str = 'drop',
                  handle_outliers: str = 'clip',
                  remove_high_corr: bool = True,
                  corr_threshold: float = 0.9) -> pd.DataFrame:
        """Clean data based on EDA findings."""
        logger.info("Cleaning data based on EDA findings...")

        df_clean = self.df.copy()

        # Handle missing values
        if handle_missing:
            analyzer = DataQualityAnalyzer(df_clean)
            df_clean = analyzer.handle_missing(handle_missing)

        # Handle outliers
        if handle_outliers:
            analyzer = DataQualityAnalyzer(df_clean)
            df_clean = analyzer.handle_outliers(handle_outliers)

        # Remove highly correlated features
        if remove_high_corr:
            analyzer = CorrelationAnalyzer(df_clean)
            to_remove = analyzer.recommend_removals(corr_threshold)
            if to_remove:
                logger.info(f"Removing {len(to_remove)} highly correlated features")
                df_clean = df_clean.drop(columns=to_remove)

        logger.info(f"Data cleaning complete: {df_clean.shape[0]} rows × {df_clean.shape[1]} columns")
        return df_clean
