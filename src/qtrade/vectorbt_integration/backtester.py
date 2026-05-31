"""Vectorbt backtester wrapper."""
from typing import Dict, List, Optional, Union
import pandas as pd
import numpy as np
from loguru import logger


class VectorbtBacktester:
    """Wrapper for vectorbt fast vectorized backtesting."""

    def __init__(self):
        self._vbt = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize vectorbt."""
        try:
            import vectorbt as vbt
            self._vbt = vbt
            self._initialized = True
            logger.info("Vectorbt initialized successfully")
            return True
        except ImportError:
            logger.error("Vectorbt not installed. Install with: pip install vectorbt")
            return False
        except Exception as e:
            logger.error(f"Failed to initialize vectorbt: {e}")
            return False

    def is_initialized(self) -> bool:
        """Check if vectorbt is initialized."""
        return self._initialized

    def backtest_ma_crossover(self, prices: pd.Series,
                              fast_windows: Union[int, List[int]] = 5,
                              slow_windows: Union[int, List[int]] = 20,
                              init_cash: float = 100000,
                              fees: float = 0.001,
                              freq: str = '1D') -> Dict:
        """Backtest moving average crossover strategy.

        Args:
            prices: Price series
            fast_windows: Fast MA window(s)
            slow_windows: Slow MA window(s)
            init_cash: Initial capital
            fees: Trading fees (0.1% = 0.001)
            freq: Data frequency
        """
        if not self._initialized:
            raise RuntimeError("Vectorbt not initialized")

        try:
            # Calculate MAs
            fast_ma = self._vbt.MA.run(prices, fast_windows, short_name='fast')
            slow_ma = self._vbt.MA.run(prices, slow_windows, short_name='slow')

            # Generate signals
            entries = fast_ma.ma_crossed_above(slow_ma)
            exits = fast_ma.ma_crossed_below(slow_ma)

            # Run backtest
            pf = self._vbt.Portfolio.from_signals(
                prices,
                entries=entries,
                exits=exits,
                init_cash=init_cash,
                fees=fees,
                freq=freq,
            )

            # Extract results
            results = {
                'total_return': pf.total_return(),
                'sharpe_ratio': pf.sharpe_ratio(),
                'max_drawdown': pf.max_drawdown(),
                'total_trades': pf.total_trades(),
                'win_rate': pf.win_rate(),
                'profit_factor': pf.profit_factor(),
                'portfolio': pf,
            }

            logger.info(f"Backtest complete: {results['total_return']:.2%} return")
            return results

        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            raise

    def backtest_rsi(self, prices: pd.Series,
                    windows: Union[int, List[int]] = 14,
                    entry_thresholds: Union[float, List[float]] = 30,
                    exit_thresholds: Union[float, List[float]] = 70,
                    init_cash: float = 100000,
                    fees: float = 0.001) -> Dict:
        """Backtest RSI strategy."""
        if not self._initialized:
            raise RuntimeError("Vectorbt not initialized")

        try:
            # Calculate RSI
            rsi = self._vbt.RSI.run(prices, window=windows)

            # Generate signals
            entries = rsi.rsi_below(entry_thresholds)
            exits = rsi.rsi_above(exit_thresholds)

            # Run backtest
            pf = self._vbt.Portfolio.from_signals(
                prices,
                entries=entries,
                exits=exits,
                init_cash=init_cash,
                fees=fees,
            )

            results = {
                'total_return': pf.total_return(),
                'sharpe_ratio': pf.sharpe_ratio(),
                'max_drawdown': pf.max_drawdown(),
                'total_trades': pf.total_trades(),
                'win_rate': pf.win_rate(),
                'portfolio': pf,
            }

            return results

        except Exception as e:
            logger.error(f"RSI backtest failed: {e}")
            raise

    def backtest_custom_signals(self, prices: pd.Series,
                               entries: pd.Series,
                               exits: pd.Series,
                               init_cash: float = 100000,
                               fees: float = 0.001) -> Dict:
        """Backtest with custom entry/exit signals."""
        if not self._initialized:
            raise RuntimeError("Vectorbt not initialized")

        try:
            pf = self._vbt.Portfolio.from_signals(
                prices,
                entries=entries,
                exits=exits,
                init_cash=init_cash,
                fees=fees,
            )

            results = {
                'total_return': pf.total_return(),
                'sharpe_ratio': pf.sharpe_ratio(),
                'max_drawdown': pf.max_drawdown(),
                'total_trades': pf.total_trades(),
                'win_rate': pf.win_rate(),
                'profit_factor': pf.profit_factor(),
                'portfolio': pf,
            }

            return results

        except Exception as e:
            logger.error(f"Custom signal backtest failed: {e}")
            raise

    def compute_metrics(self, portfolio) -> Dict:
        """Compute comprehensive metrics for a portfolio."""
        if not self._initialized:
            raise RuntimeError("Vectorbt not initialized")

        try:
            metrics = {
                'total_return': float(portfolio.total_return()),
                'annual_return': float(portfolio.annual_return()),
                'sharpe_ratio': float(portfolio.sharpe_ratio()),
                'sortino_ratio': float(portfolio.sortino_ratio()),
                'max_drawdown': float(portfolio.max_drawdown()),
                'calmar_ratio': float(portfolio.calmar_ratio()),
                'omega_ratio': float(portfolio.omega_ratio()),
                'total_trades': int(portfolio.total_trades()),
                'win_rate': float(portfolio.win_rate()),
                'profit_factor': float(portfolio.profit_factor()),
                'avg_pnl': float(portfolio.avg_pnl()),
                'value': float(portfolio.value().iloc[-1]),
            }

            return metrics

        except Exception as e:
            logger.error(f"Failed to compute metrics: {e}")
            raise

    def plot_results(self, portfolio, filename: str = 'vectorbt_results.html'):
        """Plot backtest results."""
        if not self._initialized:
            raise RuntimeError("Vectorbt not initialized")

        try:
            fig = portfolio.plot()
            fig.write_html(filename)
            logger.info(f"Saved plot to {filename}")
        except Exception as e:
            logger.error(f"Failed to plot results: {e}")
