"""CSV cache for downloaded market data."""

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger("qtrade.data.cache")


class CSVCache:
    """Read/write OHLCV data as CSV files."""

    def __init__(self, cache_dir: str | Path = "data/cache"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.dir / f"{symbol}.csv"

    def exists(self, symbol: str) -> bool:
        return self._path(symbol).exists()

    def load(self, symbol: str) -> pd.DataFrame:
        p = self._path(symbol)
        if not p.exists():
            raise FileNotFoundError(f"Cache miss: {p}")
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        logger.debug("Cache hit: %s (%d rows)", symbol, len(df))
        return df

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        p = self._path(symbol)
        df.to_csv(p, encoding="utf-8")
        logger.debug("Cached: %s (%d rows)", symbol, len(df))
