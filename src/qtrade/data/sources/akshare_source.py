"""AkShare DataSource implementation."""

import logging

import pandas as pd

from qtrade.data.source import DataSource, DataSpec

logger = logging.getLogger("qtrade.data.sources.akshare")


class AkShareSource(DataSource):
    """AkShare data source (东方财富/新浪等)."""

    @property
    def name(self) -> str:
        return "akshare"

    def fetch_stock(self, spec: DataSpec) -> pd.DataFrame:
        try:
            import akshare as ak

            adjust = spec.adjust if spec.adjust in ("qfq", "hfq", "") else "qfq"
            try:
                df = ak.stock_zh_a_hist(
                    symbol=spec.symbol, period=spec.bar_type,
                    start_date=spec.start_date, end_date=spec.end_date or "",
                    adjust=adjust)
            except Exception:
                df = ak.stock_zh_a_hist(
                    symbol=spec.symbol, period=spec.bar_type,
                    start_date=spec.start_date, end_date=spec.end_date or "",
                    adjust="")

            df = df.rename(columns={
                "日期": "date", "开盘": "open", "收盘": "close",
                "最高": "high", "最低": "low", "成交量": "volume"
            })
            df["date"] = pd.to_datetime(df["date"])
            df = df[["date", "open", "high", "low", "close", "volume"]]
            df.set_index("date", inplace=True)
            return df
        except Exception as e:
            logger.error("akshare fetch failed: %s", e)
            raise

    def fetch_index(self, symbol: str, start: str, end: str = None) -> pd.DataFrame:
        try:
            import akshare as ak
            raw = str(symbol).lstrip("shsz")
            df = ak.stock_zh_index_daily(symbol=f"sh{raw}")
            df = df.rename(columns={
                "date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "volume": "volume"})
            df["date"] = pd.to_datetime(df["date"])
            if start:
                df = df[df["date"] >= start]
            if end:
                df = df[df["date"] <= end]
            df = df[["date", "open", "high", "low", "close", "volume"]]
            df.set_index("date", inplace=True)
            return df
        except Exception as e:
            logger.error("akshare index fetch failed: %s", e)
            raise

    def available(self) -> bool:
        try:
            import akshare
            return True
        except ImportError:
            return False
