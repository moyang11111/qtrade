"""Abstract DataSource interface — unified data access layer."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class DataSpec:
    """Data request specification."""
    symbol: str
    start_date: str  # YYYYMMDD
    end_date: Optional[str] = None
    bar_type: str = "daily"  # daily, weekly, monthly, 5min, 15min, 60min
    adjust: str = "qfq"  # qfq (forward), hfq (backward), "" (none)


class DataSource(ABC):
    """Abstract data source — all providers implement this."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Source identifier (e.g., 'pytdx', 'akshare')."""
        ...

    @abstractmethod
    def fetch_stock(self, spec: DataSpec) -> pd.DataFrame:
        """Fetch stock OHLCV data.

        Returns:
            DataFrame with DatetimeIndex and columns: open, high, low, close, volume
        """
        ...

    @abstractmethod
    def fetch_index(self, symbol: str, start: str, end: str = None) -> pd.DataFrame:
        """Fetch index OHLCV data."""
        ...

    def fetch_finance(self, symbol: str) -> dict:
        """Fetch basic financial info. Optional — returns empty dict if unsupported."""
        return {}

    def fetch_realtime(self, symbols: list[str]) -> pd.DataFrame:
        """Fetch realtime quotes. Optional."""
        return pd.DataFrame()

    def available(self) -> bool:
        """Check if this source is available (e.g., network reachable)."""
        return True
