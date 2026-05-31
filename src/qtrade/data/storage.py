"""Abstract Storage interface — unified persistence layer."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import pandas as pd


class Storage(ABC):
    """Abstract storage backend for market data."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Storage type name (e.g., 'csv', 'parquet', 'sqlite')."""
        ...

    @abstractmethod
    def exists(self, symbol: str) -> bool:
        """Check if data for symbol is cached."""
        ...

    @abstractmethod
    def load(self, symbol: str) -> pd.DataFrame:
        """Load cached data for symbol."""
        ...

    @abstractmethod
    def save(self, symbol: str, df: pd.DataFrame) -> None:
        """Save data for symbol."""
        ...

    def load_range(self, symbol: str, start: str = None, end: str = None) -> Optional[pd.DataFrame]:
        """Load data with date filtering."""
        if not self.exists(symbol):
            return None
        df = self.load(symbol)
        if start:
            df = df[df.index >= pd.to_datetime(start)]
        if end:
            df = df[df.index <= pd.to_datetime(end)]
        return df if len(df) > 0 else None

    def delete(self, symbol: str) -> None:
        """Delete cached data for symbol."""
        pass

    def list_symbols(self) -> list[str]:
        """List all cached symbols."""
        return []
