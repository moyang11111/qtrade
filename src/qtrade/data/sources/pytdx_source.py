"""Pytdx DataSource implementation."""

import logging

import pandas as pd

from qtrade.data.source import DataSource, DataSpec
from qtrade.data.pytdx_client import PytdxClient

logger = logging.getLogger("qtrade.data.sources.pytdx")


class PytdxSource(DataSource):
    """通达信 data source via pytdx."""

    @property
    def name(self) -> str:
        return "pytdx"

    def fetch_stock(self, spec: DataSpec) -> pd.DataFrame:
        try:
            with PytdxClient() as client:
                if spec.adjust == "qfq":
                    df = client.get_bars_qfq(spec.symbol, start=spec.start_date, end=spec.end_date)
                else:
                    df = client.get_all_daily_bars(spec.symbol)
                    if not df.empty:
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
                        if spec.start_date:
                            df = df[df.index >= pd.to_datetime(spec.start_date)]
                        if spec.end_date:
                            df = df[df.index <= pd.to_datetime(spec.end_date)]
            return df
        except Exception as e:
            logger.error("pytdx fetch failed: %s", e)
            raise

    def fetch_index(self, symbol: str, start: str, end: str = None) -> pd.DataFrame:
        try:
            with PytdxClient() as client:
                return client.get_index_bars(symbol, start=start, end=end)
        except Exception as e:
            logger.error("pytdx index fetch failed: %s", e)
            raise

    def fetch_finance(self, symbol: str) -> dict:
        try:
            with PytdxClient() as client:
                return client.get_finance_info(symbol)
        except Exception:
            return {}

    def available(self) -> bool:
        try:
            with PytdxClient():
                return True
        except Exception:
            return False
