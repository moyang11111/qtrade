"""Strategy portfolio with capital allocation."""
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np
from loguru import logger

from qtrade.strategies.interface import StrategyInterface


class StrategyPortfolio:
    """Manage a portfolio of strategies with capital allocation."""

    def __init__(self, initial_capital: float = 1000000):
        """Initialize strategy portfolio.

        Args:
            initial_capital: Initial capital
        """
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.strategies: Dict[str, Dict] = {}
        self.performance_history = []

    def add_strategy(self, name: str, strategy: StrategyInterface,
                    allocation: float, max_position_pct: float = 1.0):
        """Add a strategy to the portfolio.

        Args:
            name: Strategy name
            strategy: Strategy instance
            allocation: Capital allocation (0-1)
            max_position_pct: Maximum position size as fraction of allocation
        """
        if name in self.strategies:
            raise ValueError(f"Strategy '{name}' already exists")

        capital = self.initial_capital * allocation

        self.strategies[name] = {
            'strategy': strategy,
            'allocation': allocation,
            'capital': capital,
            'current_capital': capital,
            'max_position_pct': max_position_pct,
            'trades': [],
            'equity_curve': [],
        }

        logger.info(f"Added strategy '{name}' with {allocation*100:.1f}% allocation")

    def remove_strategy(self, name: str):
        """Remove a strategy from the portfolio."""
        if name not in self.strategies:
            raise ValueError(f"Strategy '{name}' not found")

        del self.strategies[name]
        logger.info(f"Removed strategy '{name}'")

    def generate_signals(self, df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
        """Generate signals for all strategies.

        Args:
            df: Input DataFrame

        Returns:
            Dictionary of strategy name -> signals DataFrame
        """
        all_signals = {}

        for name, strategy_info in self.strategies.items():
            strategy = strategy_info['strategy']
            signals = strategy.generate_signals(df)
            all_signals[name] = signals

        return all_signals

    def rebalance(self, new_allocations: Dict[str, float]):
        """Rebalance capital allocations.

        Args:
            new_allocations: New allocation for each strategy
        """
        total_allocation = sum(new_allocations.values())
        if abs(total_allocation - 1.0) > 1e-6:
            logger.warning(f"Allocations sum to {total_allocation:.4f}, not 1.0")

        for name, allocation in new_allocations.items():
            if name not in self.strategies:
                logger.warning(f"Strategy '{name}' not found, skipping")
                continue

            # Update allocation
            self.strategies[name]['allocation'] = allocation
            self.strategies[name]['capital'] = self.initial_capital * allocation

        logger.info(f"Rebalanced portfolio with {len(new_allocations)} strategies")

    def get_total_equity(self) -> float:
        """Get total portfolio equity."""
        total = sum(info['current_capital'] for info in self.strategies.values())
        return total

    def get_returns(self) -> float:
        """Get total portfolio returns."""
        total_equity = self.get_total_equity()
        returns = (total_equity - self.initial_capital) / self.initial_capital
        return returns

    def update_performance(self, timestamp):
        """Update performance history."""
        total_equity = self.get_total_equity()
        returns = self.get_returns()

        self.performance_history.append({
            'timestamp': timestamp,
            'total_equity': total_equity,
            'returns': returns,
        })

    def get_performance_df(self) -> pd.DataFrame:
        """Get performance history as DataFrame."""
        if not self.performance_history:
            return pd.DataFrame()

        return pd.DataFrame(self.performance_history)

    def get_allocation_summary(self) -> pd.DataFrame:
        """Get allocation summary."""
        rows = []
        for name, info in self.strategies.items():
            rows.append({
                'strategy': name,
                'allocation': info['allocation'],
                'capital': info['capital'],
                'current_capital': info['current_capital'],
                'returns': (info['current_capital'] - info['capital']) / info['capital'],
                'max_position_pct': info['max_position_pct'],
            })

        return pd.DataFrame(rows)

    def summary(self) -> Dict:
        """Get portfolio summary."""
        return {
            'initial_capital': self.initial_capital,
            'current_capital': self.get_total_equity(),
            'returns': self.get_returns(),
            'n_strategies': len(self.strategies),
            'strategies': list(self.strategies.keys()),
        }
