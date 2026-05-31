"""Signal ensemble methods."""
from typing import Dict, List
import pandas as pd
import numpy as np
from loguru import logger

from qtrade.strategies.interface import StrategyInterface


class SignalEnsemble:
    """Ensemble methods for combining strategy signals."""

    def __init__(self, strategies: List[StrategyInterface],
                 method: str = 'majority_vote'):
        """Initialize signal ensemble.

        Args:
            strategies: List of strategies
            method: Ensemble method ('majority_vote', 'weighted_average', 'meta_learner')
        """
        self.strategies = strategies
        self.method = method
        self.weights = [1.0 / len(strategies)] * len(strategies)
        self.meta_learner = None

    def set_weights(self, weights: List[float]):
        """Set strategy weights.

        Args:
            weights: List of weights (must sum to 1)
        """
        if len(weights) != len(self.strategies):
            raise ValueError(f"Expected {len(self.strategies)} weights, got {len(weights)}")

        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            logger.warning(f"Weights sum to {total:.4f}, normalizing")
            weights = [w / total for w in weights]

        self.weights = weights

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate ensemble signals.

        Args:
            df: Input DataFrame

        Returns:
            DataFrame with ensemble signals
        """
        # Generate signals from all strategies
        all_signals = []
        for strategy in self.strategies:
            signals = strategy.generate_signals(df)
            all_signals.append(signals)

        # Combine based on method
        if self.method == 'majority_vote':
            return self._majority_vote(df, all_signals)
        elif self.method == 'weighted_average':
            return self._weighted_average(df, all_signals)
        elif self.method == 'meta_learner':
            return self._meta_learner(df, all_signals)
        else:
            raise ValueError(f"Unknown method: {self.method}")

    def _majority_vote(self, df: pd.DataFrame,
                      all_signals: List[pd.DataFrame]) -> pd.DataFrame:
        """Majority vote ensemble."""
        result = df.copy()

        # Count votes
        vote_sum = sum(s['signal_action'] for s in all_signals)

        # Majority wins
        result['signal_action'] = np.sign(vote_sum)

        # Strength is agreement ratio
        agreement = vote_sum.abs() / len(all_signals)
        result['signal_strength'] = agreement

        return result

    def _weighted_average(self, df: pd.DataFrame,
                         all_signals: List[pd.DataFrame]) -> pd.DataFrame:
        """Weighted average ensemble."""
        result = df.copy()

        # Weighted sum of actions
        weighted_actions = sum(
            s['signal_action'] * w for s, w in zip(all_signals, self.weights)
        )

        # Weighted sum of strengths
        weighted_strengths = sum(
            s['signal_strength'] * w for s, w in zip(all_signals, self.weights)
        )

        result['signal_action'] = np.sign(weighted_actions)
        result['signal_strength'] = weighted_strengths

        return result

    def _meta_learner(self, df: pd.DataFrame,
                     all_signals: List[pd.DataFrame]) -> pd.DataFrame:
        """Meta-learner ensemble (stacking)."""
        if self.meta_learner is None:
            raise ValueError("Meta-learner not trained. Call train_meta_learner() first.")

        result = df.copy()

        # Create feature matrix from strategy signals
        features = pd.DataFrame({
            f'strategy_{i}_action': s['signal_action']
            for i, s in enumerate(all_signals)
        })

        for i, s in enumerate(all_signals):
            features[f'strategy_{i}_strength'] = s['signal_strength']

        # Predict with meta-learner
        predictions = self.meta_learner.predict_proba(features)

        # Convert to signals
        result['signal_action'] = np.where(predictions[:, 1] > 0.6, 1,
                                          np.where(predictions[:, 1] < 0.4, -1, 0))
        result['signal_strength'] = predictions[:, 1]

        return result

    def train_meta_learner(self, df: pd.DataFrame, target: pd.Series):
        """Train meta-learner on historical data.

        Args:
            df: Feature DataFrame
            target: Target variable (future returns)
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score

        # Generate signals from all strategies
        all_signals = []
        for strategy in self.strategies:
            signals = strategy.generate_signals(df)
            all_signals.append(signals)

        # Create feature matrix
        features = pd.DataFrame({
            f'strategy_{i}_action': s['signal_action']
            for i, s in enumerate(all_signals)
        })

        for i, s in enumerate(all_signals):
            features[f'strategy_{i}_strength'] = s['signal_strength']

        # Train meta-learner
        X_train, X_test, y_train, y_test = train_test_split(
            features, target, test_size=0.2, shuffle=False
        )

        self.meta_learner = RandomForestClassifier(n_estimators=100, random_state=42)
        self.meta_learner.fit(X_train, y_train)

        # Evaluate
        train_acc = accuracy_score(y_train, self.meta_learner.predict(X_train))
        test_acc = accuracy_score(y_test, self.meta_learner.predict(X_test))

        logger.info(f"Meta-learner trained: train_acc={train_acc:.4f}, test_acc={test_acc:.4f}")

    def optimize_weights(self, df: pd.DataFrame, target: pd.Series,
                        n_iterations: int = 100) -> List[float]:
        """Optimize strategy weights using random search.

        Args:
            df: Feature DataFrame
            target: Target variable
            n_iterations: Number of iterations

        Returns:
            Optimized weights
        """
        best_score = float('-inf')
        best_weights = self.weights

        for _ in range(n_iterations):
            # Random weights
            weights = np.random.dirichlet(np.ones(len(self.strategies)))
            self.set_weights(weights.tolist())

            # Generate signals
            signals = self.generate_signals(df)

            # Simple score: correlation with target
            score = signals['signal_action'].corr(target)

            if score > best_score:
                best_score = score
                best_weights = weights.tolist()

        self.set_weights(best_weights)
        logger.info(f"Optimized weights: {best_weights}, score: {best_score:.4f}")

        return best_weights
