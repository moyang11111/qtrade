"""Feature correlation analyzer."""
from typing import Dict, List, Optional
import pandas as pd
import numpy as np


class CorrelationAnalyzer:
    """Analyze feature correlations and multicollinearity."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def correlation_matrix(self, method: str = 'pearson') -> pd.DataFrame:
        """Compute correlation matrix.

        Args:
            method: 'pearson', 'spearman', or 'kendall'
        """
        numeric_df = self.df.select_dtypes(include=[np.number])
        return numeric_df.corr(method=method)

    def high_correlations(self, threshold: float = 0.9,
                         method: str = 'pearson') -> List[Dict]:
        """Find highly correlated feature pairs."""
        corr_matrix = self.correlation_matrix(method)
        high_corr = []

        for i in range(len(corr_matrix.columns)):
            for j in range(i + 1, len(corr_matrix.columns)):
                corr_val = corr_matrix.iloc[i, j]
                if abs(corr_val) >= threshold:
                    high_corr.append({
                        'feature_1': corr_matrix.columns[i],
                        'feature_2': corr_matrix.columns[j],
                        'correlation': float(corr_val),
                        'abs_correlation': float(abs(corr_val)),
                    })

        # Sort by absolute correlation
        high_corr.sort(key=lambda x: x['abs_correlation'], reverse=True)
        return high_corr

    def vif_analysis(self) -> pd.DataFrame:
        """Calculate Variance Inflation Factor for multicollinearity detection."""
        from statsmodels.stats.outliers_influence import variance_inflation_factor
        from statsmodels.tools.tools import add_constant

        numeric_df = self.df.select_dtypes(include=[np.number]).dropna()
        if numeric_df.empty:
            return pd.DataFrame()

        # Add constant
        X = add_constant(numeric_df)

        vif_data = []
        for i, col in enumerate(X.columns):
            if col == 'const':
                continue
            try:
                vif = variance_inflation_factor(X.values, i)
                vif_data.append({
                    'feature': col,
                    'VIF': vif,
                    'high_vif': vif > 10,
                })
            except:
                vif_data.append({
                    'feature': col,
                    'VIF': np.nan,
                    'high_vif': False,
                })

        return pd.DataFrame(vif_data)

    def correlation_with_target(self, target: str,
                               method: str = 'pearson') -> pd.Series:
        """Compute correlation of all features with target variable."""
        if target not in self.df.columns:
            raise ValueError(f"Target column '{target}' not found")

        numeric_df = self.df.select_dtypes(include=[np.number])
        correlations = numeric_df.corr(method=method)[target].drop(target)
        return correlations.sort_values(key=abs, ascending=False)

    def feature_clusters(self, threshold: float = 0.8) -> List[List[str]]:
        """Cluster features based on correlation."""
        corr_matrix = self.correlation_matrix()
        high_corr = self.high_correlations(threshold)

        # Build adjacency list
        adjacency = {col: set() for col in corr_matrix.columns}
        for pair in high_corr:
            adjacency[pair['feature_1']].add(pair['feature_2'])
            adjacency[pair['feature_2']].add(pair['feature_1'])

        # Find connected components (clusters)
        visited = set()
        clusters = []

        for col in adjacency:
            if col not in visited:
                cluster = []
                stack = [col]
                while stack:
                    node = stack.pop()
                    if node not in visited:
                        visited.add(node)
                        cluster.append(node)
                        stack.extend(adjacency[node] - visited)
                if len(cluster) > 1:
                    clusters.append(cluster)

        return clusters

    def recommend_removals(self, threshold: float = 0.9,
                          vif_threshold: float = 10) -> List[str]:
        """Recommend features to remove based on correlation and VIF."""
        to_remove = set()

        # High correlation pairs - remove one from each pair
        high_corr = self.high_correlations(threshold)
        for pair in high_corr:
            # Remove the one with higher average correlation
            corr_matrix = self.correlation_matrix()
            avg_corr_1 = corr_matrix[pair['feature_1']].abs().mean()
            avg_corr_2 = corr_matrix[pair['feature_2']].abs().mean()
            to_remove.add(pair['feature_1'] if avg_corr_1 > avg_corr_2 else pair['feature_2'])

        # High VIF features
        vif_df = self.vif_analysis()
        if not vif_df.empty:
            high_vif = vif_df[vif_df['high_vif']]['feature'].tolist()
            to_remove.update(high_vif)

        return list(to_remove)

    def analyze_all(self, target: Optional[str] = None) -> Dict:
        """Run all correlation analyses."""
        result = {
            'correlation_matrix_pearson': self.correlation_matrix('pearson').to_dict(),
            'correlation_matrix_spearman': self.correlation_matrix('spearman').to_dict(),
            'high_correlations': self.high_correlations(0.9),
            'vif_analysis': self.vif_analysis().to_dict() if not self.vif_analysis().empty else {},
            'feature_clusters': self.feature_clusters(),
            'recommended_removals': self.recommend_removals(),
        }

        if target:
            result['correlation_with_target'] = self.correlation_with_target(target).to_dict()

        return result
