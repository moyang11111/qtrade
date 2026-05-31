"""DataFetcher — unified data access with source fallback and storage caching."""

import logging
from datetime import datetime

import pandas as pd

from qtrade.data.source import DataSpec
from qtrade.data.registry import get_source, get_fallback_chain
from qtrade.data.storage import Storage
from qtrade.data.storages import CSVStorage
from qtrade.data.schema import validate_ohlcv

logger = logging.getLogger("qtrade.data.fetcher")


class DataFetcher:
    """Fetch OHLCV data with multi-source fallback and local caching."""

    def __init__(self, cfg: dict):
        data_cfg = cfg.get("data", {})
        cache_cfg = data_cfg.get("cache", {})

        # Source chain (primary + fallback)
        primary = data_cfg.get("source", "pytdx")
        fallback = data_cfg.get("fallback", [])
        source_names = [primary] + fallback if fallback else [primary, "akshare"]
        self.sources = get_fallback_chain(source_names)
        if not self.sources:
            raise RuntimeError(f"No data sources available from: {source_names}")
        logger.info("Data sources: %s", [s.name for s in self.sources])

        # Storage backend
        storage_type = cache_cfg.get("type", "csv")
        storage_dir = cache_cfg.get("dir", "data/cache")
        self.storage: Storage = self._create_storage(storage_type, storage_dir)
        self.use_cache = cache_cfg.get("enabled", True)

    def _create_storage(self, storage_type: str, storage_dir: str) -> Storage:
        if storage_type == "parquet":
            from qtrade.data.storages import ParquetStorage
            return ParquetStorage(storage_dir)
        return CSVStorage(storage_dir)

    def fetch(self, symbol: str, start: str = "20220101",
              end: str = None) -> pd.DataFrame:
        """Fetch stock OHLCV daily bars with cache + multi-source fallback."""
        if end is None:
            end = datetime.now().strftime("%Y%m%d")

        # Try cache first
        if self.use_cache:
            cached = self.storage.load_range(symbol, start, end)
            if cached is not None and len(cached) > 0:
                logger.debug("Cache hit: %s (%d rows)", symbol, len(cached))
                return validate_ohlcv(cached, f"{symbol}(cache)")

        # Try each source in order
        spec = DataSpec(symbol=symbol, start_date=start, end_date=end)
        last_error = None
        for source in self.sources:
            try:
                df = source.fetch_stock(spec)
                if df.empty:
                    continue
                df = validate_ohlcv(df, f"{symbol}({source.name})")
                if self.use_cache:
                    self.storage.save(symbol, df)
                logger.info("Fetched %s via %s: %d rows [%s ~ %s]",
                            symbol, source.name, len(df),
                            df.index[0].strftime("%Y-%m-%d"),
                            df.index[-1].strftime("%Y-%m-%d"))
                return df
            except Exception as e:
                last_error = e
                logger.warning("Source %s failed for %s: %s", source.name, symbol, e)
                continue

        raise RuntimeError(f"All sources failed for {symbol}: {last_error}")

    def fetch_index(self, symbol: str = "000300",
                    start: str = "20220101", end: str = None) -> pd.DataFrame:
        """Fetch index OHLCV daily bars."""
        if end is None:
            end = datetime.now().strftime("%Y%m%d")

        last_error = None
        for source in self.sources:
            try:
                df = source.fetch_index(symbol, start, end)
                if df.empty:
                    continue
                return validate_ohlcv(df, f"index:{symbol}")
            except Exception as e:
                last_error = e
                logger.warning("Source %s index failed for %s: %s", source.name, symbol, e)
                continue

        raise RuntimeError(f"All sources failed for index {symbol}: {last_error}")
