"""qtrade — A-share quantitative trading framework."""

__version__ = "1.0.0"

# Core components
from qtrade.config import load_config
from qtrade.data.fetcher import DataFetcher
from qtrade.backtest.engine import BacktestEngine

__all__ = [
    "__version__",
    "load_config",
    "DataFetcher",
    "BacktestEngine",
]
