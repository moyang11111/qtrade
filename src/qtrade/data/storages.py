"""Storage implementations — CSV and Parquet."""

import logging
from pathlib import Path

import pandas as pd

from qtrade.data.storage import Storage

logger = logging.getLogger("qtrade.data.storages")


class CSVStorage(Storage):
    """CSV file storage."""

    def __init__(self, cache_dir: str | Path = "data/cache"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "csv"

    def _path(self, symbol: str) -> Path:
        return self.dir / f"{symbol}.csv"

    def exists(self, symbol: str) -> bool:
        return self._path(symbol).exists()

    def load(self, symbol: str) -> pd.DataFrame:
        p = self._path(symbol)
        if not p.exists():
            raise FileNotFoundError(f"Cache miss: {p}")
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        logger.debug("CSV load: %s (%d rows)", symbol, len(df))
        return df

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        p = self._path(symbol)
        df.to_csv(p, encoding="utf-8")
        logger.debug("CSV save: %s (%d rows)", symbol, len(df))

    def delete(self, symbol: str) -> None:
        p = self._path(symbol)
        if p.exists():
            p.unlink()

    def list_symbols(self) -> list[str]:
        return [f.stem for f in self.dir.glob("*.csv")]


class ParquetStorage(Storage):
    """Parquet file storage — faster and smaller than CSV."""

    def __init__(self, cache_dir: str | Path = "data/cache"):
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "parquet"

    def _path(self, symbol: str) -> Path:
        return self.dir / f"{symbol}.parquet"

    def exists(self, symbol: str) -> bool:
        return self._path(symbol).exists()

    def load(self, symbol: str) -> pd.DataFrame:
        p = self._path(symbol)
        if not p.exists():
            raise FileNotFoundError(f"Cache miss: {p}")
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index)
        logger.debug("Parquet load: %s (%d rows)", symbol, len(df))
        return df

    def save(self, symbol: str, df: pd.DataFrame) -> None:
        p = self._path(symbol)
        df.to_parquet(p)
        logger.debug("Parquet save: %s (%d rows)", symbol, len(df))

    def delete(self, symbol: str) -> None:
        p = self._path(symbol)
        if p.exists():
            p.unlink()

    def list_symbols(self) -> list[str]:
        return [f.stem for f in self.dir.glob("*.parquet")]
