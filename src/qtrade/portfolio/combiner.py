"""Strategy combiner for multi-strategy portfolios."""
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np
from loguru import logger

from qtrade.strategy.base import SignalGenerator as StrategyInterface  # alias for backward compatibility


class StrategyCombiner:
    """Combine multiple strategies with capital allocation."""

    def __init__(self, strategies: List[Tuple[StrategyInterface, float]]):
        """Initialize strategy combiner.

        Args:
            strategies: List of (strategy, weight) tuples
        """
        self.strategies = strategies
        self._validate_weights()

    def _validate_weights(self):
        """Validate that weights sum to 1."""
        total_weight = sum(weight for _, weight in self.strategies)
        if abs(total_weight - 1.0) > 1e-6:
            logger.warning(f"Weights sum to {total_weight:.4f}, not 1.0")

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate combined signals from all strategies.

        Args:
            df: Input DataFrame

        Returns:
            DataFrame with combined signals
        """
        all_signals = []

        for strategy, weight in self.strategies:
            # Generate signals from each strategy
            signals = strategy.generate_signals(df)

            # Weight the signals
            weighted_signals = signals.copy()
            weighted_signals['signal_action'] = signals['signal_action'] * weight
            weighted_signals['signal_strength'] = signals['signal_strength'] * weight

            all_signals.append(weighted_signals)

        # Combine signals
        combined = df.copy()

        # Average the weighted signals
        combined['signal_action'] = sum(s['signal_action'] for s in all_signals)
        combined['signal_strength'] = sum(s['signal_strength'] for s in all_signals)

        # Normalize to [-1, 1] range
        max_action = max(abs(combined['signal_action'].max()), abs(combined['signal_action'].min()))
        if max_action > 0:
            combined['signal_action'] = combined['signal_action'] / max_action

        max_strength = combined['signal_strength'].max()
        if max_strength > 0:
            combined['signal_strength'] = combined['signal_strength'] / max_strength

        # Convert to discrete actions
        combined['signal_action'] = np.sign(combined['signal_action'])

        logger.info(f"Combined signals from {len(self.strategies)} strategies")

        return combined

    def correlation_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """Analyze correlation between strategy signals.

        Args:
            df: Input DataFrame

        Returns:
            Correlation matrix
        """
        all_signals = []

        for i, (strategy, _) in enumerate(self.strategies):
            signals = strategy.generate_signals(df)
            all_signals.append(signals['signal_action'].rename(f'strategy_{i}'))

        signals_df = pd.concat(all_signals, axis=1)
        correlation = signals_df.corr()

        logger.info("Computed strategy correlation matrix")

        return correlation

    def diversification_score(self, df: pd.DataFrame) -> float:
        """Calculate diversification score (1 - avg correlation).

        Args:
            df: Input DataFrame

        Returns:
            Diversification score (higher is better)
        """
        corr_matrix = self.correlation_analysis(df)

        # Get upper triangle (excluding diagonal)
        mask = np.triu(np.ones_like(corr_matrix, dtype=bool), k=1)
        upper_triangle = corr_matrix.where(mask)

        avg_corr = upper_triangle.stack().mean()
        diversification = 1 - avg_corr

        logger.info(f"Diversification score: {diversification:.4f}")

        return diversification

    def add_strategy(self, strategy: StrategyInterface, weight: float):
        """Add a strategy to the combiner."""
        self.strategies.append((strategy, weight))
        self._validate_weights()

    def remove_strategy(self, index: int):
        """Remove a strategy by index."""
        if 0 <= index < len(self.strategies):
            self.strategies.pop(index)
            self._validate_weights()
        else:
            raise IndexError(f"Invalid index: {index}")

    def summary(self) -> Dict:
        """Get combiner summary."""
        return {
            'n_strategies': len(self.strategies),
            'weights': [weight for _, weight in self.strategies],
            'strategy_names': [strategy.name for strategy, _ in self.strategies],
        }
