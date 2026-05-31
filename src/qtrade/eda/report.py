"""EDA report generator with visualizations."""
from typing import Dict, Optional
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from loguru import logger

from .quality import DataQualityAnalyzer
from .distribution import DistributionAnalyzer
from .correlation import CorrelationAnalyzer
from .stability import StabilityAnalyzer


class EDAReportGenerator:
    """Generate comprehensive EDA reports with visualizations."""

    def __init__(self, df: pd.DataFrame, output_dir: str = 'reports/eda'):
        self.df = df
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.quality = DataQualityAnalyzer(df)
        self.distribution = DistributionAnalyzer(df)
        self.correlation = CorrelationAnalyzer(df)
        self.stability = StabilityAnalyzer(df)

    def plot_missing_values(self, figsize: tuple = (12, 6)) -> None:
        """Plot missing value patterns."""
        missing = self.df.isnull().sum()
        missing = missing[missing > 0].sort_values(ascending=False)

        if len(missing) == 0:
            logger.info("No missing values found")
            return

        fig, ax = plt.subplots(figsize=figsize)
        missing_ratio = (missing / len(self.df) * 100)

        bars = ax.barh(range(len(missing)), missing_ratio.values, color='coral')
        ax.set_yticks(range(len(missing)))
        ax.set_yticklabels(missing.index)
        ax.set_xlabel('Missing Value Ratio (%)')
        ax.set_title('Missing Values by Column')
        ax.invert_yaxis()

        # Add value labels
        for i, (bar, val) in enumerate(zip(bars, missing_ratio.values)):
            ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                   f'{val:.1f}%', va='center', fontsize=9)

        plt.tight_layout()
        plt.savefig(self.output_dir / 'missing_values.png', dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"Saved missing values plot to {self.output_dir / 'missing_values.png'}")

    def plot_distributions(self, columns: Optional[list] = None,
                          max_plots: int = 20) -> None:
        """Plot distribution histograms for numeric columns."""
        if columns is None:
            columns = self.df.select_dtypes(include=[np.number]).columns.tolist()

        columns = columns[:max_plots]
        n_cols = min(4, len(columns))
        n_rows = (len(columns) + n_cols - 1) // n_cols

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(4*n_cols, 3*n_rows))
        if n_rows == 1 and n_cols == 1:
            axes = np.array([axes])
        axes = axes.flatten()

        for i, col in enumerate(columns):
            if i >= len(axes):
                break
            ax = axes[i]
            series = self.df[col].dropna()
            ax.hist(series, bins=50, color='steelblue', alpha=0.7, edgecolor='black')
            ax.set_title(col, fontsize=10)
            ax.axvline(series.mean(), color='red', linestyle='--', label=f'Mean: {series.mean():.2f}')
            ax.axvline(series.median(), color='green', linestyle='--', label=f'Median: {series.median():.2f}')
            ax.legend(fontsize=7)

        # Hide empty subplots
        for i in range(len(columns), len(axes)):
            axes[i].set_visible(False)

        plt.tight_layout()
        plt.savefig(self.output_dir / 'distributions.png', dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"Saved distributions plot to {self.output_dir / 'distributions.png'}")

    def plot_correlation_matrix(self, method: str = 'pearson',
                               figsize: tuple = (12, 10)) -> None:
        """Plot correlation matrix heatmap."""
        corr_matrix = self.correlation.correlation_matrix(method)

        fig, ax = plt.subplots(figsize=figsize)
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool))
        sns.heatmap(corr_matrix, mask=mask, annot=False, cmap='coolwarm',
                   center=0, square=True, fmt='.2f', ax=ax,
                   cbar_kws={'shrink': 0.8})
        ax.set_title(f'Correlation Matrix ({method})')
        plt.tight_layout()
        plt.savefig(self.output_dir / f'correlation_{method}.png', dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"Saved correlation matrix to {self.output_dir / f'correlation_{method}.png'}")

    def plot_boxplots(self, columns: Optional[list] = None,
                     max_plots: int = 20) -> None:
        """Plot boxplots for outlier detection."""
        if columns is None:
            columns = self.df.select_dtypes(include=[np.number]).columns.tolist()

        columns = columns[:max_plots]
        n_cols = min(4, len(columns))
        n_rows = (len(columns) + n_cols - 1) // n_cols

        fig, axes = plt.subplots(n_rows, n_cols, figsize=(4*n_cols, 3*n_rows))
        if n_rows == 1 and n_cols == 1:
            axes = np.array([axes])
        axes = axes.flatten()

        for i, col in enumerate(columns):
            if i >= len(axes):
                break
            ax = axes[i]
            ax.boxplot(self.df[col].dropna(), vert=True)
            ax.set_title(col, fontsize=10)
            ax.tick_params(axis='x', labelbottom=False)

        for i in range(len(columns), len(axes)):
            axes[i].set_visible(False)

        plt.tight_layout()
        plt.savefig(self.output_dir / 'boxplots.png', dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"Saved boxplots to {self.output_dir / 'boxplots.png'}")

    def plot_time_series(self, columns: Optional[list] = None,
                        max_plots: int = 10) -> None:
        """Plot time series for selected columns."""
        if not isinstance(self.df.index, pd.DatetimeIndex):
            logger.warning("Index is not DatetimeIndex, skipping time series plots")
            return

        if columns is None:
            columns = self.df.select_dtypes(include=[np.number]).columns.tolist()

        columns = columns[:max_plots]

        fig, axes = plt.subplots(len(columns), 1, figsize=(14, 3*len(columns)))
        if len(columns) == 1:
            axes = [axes]

        for i, col in enumerate(columns):
            ax = axes[i]
            ax.plot(self.df.index, self.df[col], linewidth=0.8, alpha=0.8)
            ax.set_title(col)
            ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig(self.output_dir / 'time_series.png', dpi=150, bbox_inches='tight')
        plt.close()
        logger.info(f"Saved time series plots to {self.output_dir / 'time_series.png'}")

    def generate_html_report(self, filename: str = 'eda_report.html') -> str:
        """Generate comprehensive HTML report."""
        # Run all analyses
        quality_report = self.quality.analyze_all()
        distribution_report = self.distribution.analyze_all()
        correlation_report = self.correlation.analyze_all()

        # Generate plots
        self.plot_missing_values()
        self.plot_distributions()
        self.plot_correlation_matrix()
        self.plot_boxplots()
        self.plot_time_series()

        # Build HTML
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>EDA Report</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                h1 {{ color: #2c3e50; }}
                h2 {{ color: #34495e; border-bottom: 2px solid #3498db; padding-bottom: 5px; }}
                h3 {{ color: #7f8c8d; }}
                table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #3498db; color: white; }}
                tr:nth-child(even) {{ background-color: #f2f2f2; }}
                .metric {{ background-color: #ecf0f1; padding: 10px; margin: 5px 0; border-radius: 5px; }}
                .warning {{ background-color: #fff3cd; padding: 10px; margin: 5px 0; border-left: 4px solid #ffc107; }}
                .error {{ background-color: #f8d7da; padding: 10px; margin: 5px 0; border-left: 4px solid #dc3545; }}
                img {{ max-width: 100%; height: auto; margin: 10px 0; }}
            </style>
        </head>
        <body>
            <h1>📊 Exploratory Data Analysis Report</h1>

            <h2>1. Data Overview</h2>
            <div class="metric">
                <strong>Shape:</strong> {quality_report['shape']['rows']} rows × {quality_report['shape']['columns']} columns<br>
                <strong>Quality Score:</strong> {quality_report['quality_score']}/100
            </div>

            <h3>Data Types</h3>
            <table>
                <tr><th>Column</th><th>Data Type</th></tr>
        """

        for col, dtype in quality_report['dtypes'].items():
            html += f"<tr><td>{col}</td><td>{dtype}</td></tr>"

        html += """
            </table>

            <h2>2. Data Quality Issues</h2>
        """

        # Missing values
        if quality_report['missing']['total_missing'] > 0:
            html += f"""
            <div class="warning">
                <strong>⚠️ Missing Values:</strong> {quality_report['missing']['total_missing']} total missing values
            </div>
            <img src="missing_values.png" alt="Missing Values">
            """
        else:
            html += '<div class="metric">✅ No missing values found</div>'

        # Outliers
        if quality_report['outliers_iqr']['total_outliers'] > 0:
            html += f"""
            <div class="warning">
                <strong>⚠️ Outliers:</strong> {quality_report['outliers_iqr']['total_outliers']} outliers detected (IQR method)
            </div>
            <img src="boxplots.png" alt="Boxplots">
            """
        else:
            html += '<div class="metric">✅ No significant outliers detected</div>'

        # Duplicates
        if quality_report['duplicates']['duplicate_rows'] > 0:
            html += f"""
            <div class="warning">
                <strong>⚠️ Duplicates:</strong> {quality_report['duplicates']['duplicate_rows']} duplicate rows ({quality_report['duplicates']['duplicate_row_ratio']*100:.2f}%)
            </div>
            """
        else:
            html += '<div class="metric">✅ No duplicate rows found</div>'

        html += """
            <h2>3. Feature Distributions</h2>
            <img src="distributions.png" alt="Distributions">

            <h2>4. Correlation Analysis</h2>
            <img src="correlation_pearson.png" alt="Correlation Matrix">
        """

        # High correlations
        high_corr = correlation_report['high_correlations']
        if high_corr:
            html += f"""
            <div class="warning">
                <strong>⚠️ High Correlations:</strong> {len(high_corr)} feature pairs with |r| ≥ 0.9
            </div>
            <table>
                <tr><th>Feature 1</th><th>Feature 2</th><th>Correlation</th></tr>
            """
            for pair in high_corr[:10]:
                html += f"<tr><td>{pair['feature_1']}</td><td>{pair['feature_2']}</td><td>{pair['correlation']:.3f}</td></tr>"
            html += "</table>"
        else:
            html += '<div class="metric">✅ No highly correlated feature pairs found</div>'

        html += f"""
            <h2>5. Recommendations</h2>
            <div class="metric">
                <strong>Features to Consider Removing:</strong><br>
                {', '.join(correlation_report['recommended_removals'][:10]) if correlation_report['recommended_removals'] else 'None'}
            </div>

            <h2>6. Time Series Analysis</h2>
            <img src="time_series.png" alt="Time Series">
        </body>
        </html>
        """

        output_path = self.output_dir / filename
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)

        logger.info(f"Generated HTML report: {output_path}")
        return str(output_path)
