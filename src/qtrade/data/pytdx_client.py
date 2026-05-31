"""Pytdx (通达信) client wrapper — K-line, index, finance data."""

import logging
import pandas as pd
import numpy as np
from pytdx.hq import TdxHq_API

logger = logging.getLogger("qtrade.data.pytdx")

SERVERS = [
    ("119.147.212.81", 7709),
    ("112.74.214.43", 7727),
    ("221.231.141.60", 7709),
    ("101.227.73.20", 7709),
    ("14.215.128.18", 7709),
    ("59.173.18.140", 7709),
    ("60.28.23.80", 7709),
    ("218.75.126.9", 7709),
    ("115.238.56.198", 7709),
    ("124.160.88.183", 7709),
]


class PytdxClient:
    """通达信行情客户端."""

    def __init__(self):
        self._api: TdxHq_API | None = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    def connect(self):
        if self._api:
            return
        self._api = TdxHq_API()
        for ip, port in SERVERS:
            try:
                if self._api.connect(ip, port, time_out=5):
                    logger.debug("Connected to %s:%d", ip, port)
                    return
            except Exception:
                continue
        raise ConnectionError("Cannot connect to any TDX server")

    def disconnect(self):
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._api = None

    @staticmethod
    def _market(symbol: str) -> int:
        s = str(symbol).strip()
        return 1 if s.startswith(("5", "6", "9")) else 0

    def get_bars_qfq(self, symbol: str, start: str = None, end: str = None) -> pd.DataFrame:
        """Get forward-adjusted daily bars."""
        self.connect()
        df = self._get_all_bars(symbol)
        if df.empty:
            return df

        df = df.sort_values("date").reset_index(drop=True)
        xdxr = self._get_xdxr(symbol)
        if not xdxr.empty:
            df = self._apply_qfq(df, xdxr)

        if start:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end:
            df = df[df["date"] <= pd.to_datetime(end)]

        return df.set_index("date")[["open", "high", "low", "close", "volume"]]

    def get_index_bars(self, symbol: str, start: str = None, end: str = None) -> pd.DataFrame:
        """Get index daily bars."""
        self.connect()
        raw = str(symbol).strip().lstrip("shsz")
        market = 1 if str(symbol).startswith(("sh", "5", "0")) else 0

        all_data = []
        offset = 0
        while True:
            data = self._api.get_security_bars(4, market, raw, offset, 800)
            if not data:
                break
            all_data = data + all_data
            if len(data) < 800:
                break
            offset += 800

        if not all_data:
            return pd.DataFrame()

        df = self._api.to_df(all_data)
        df = df.rename(columns={"vol": "volume"})
        df["date"] = self._fix_index_dates(df)

        if start:
            df = df[df["date"] >= pd.to_datetime(start)]
        if end:
            df = df[df["date"] <= pd.to_datetime(end)]

        return df.set_index("date")[["open", "high", "low", "close", "volume"]]

    def get_finance_info(self, symbol: str) -> dict:
        """Get basic financial data (returns dict with English keys)."""
        self.connect()
        market = self._market(symbol)
        data = self._api.get_finance_info(market, symbol)
        if not data:
            return {}

        field_map = {
            "zongzichan": "total_assets", "liudongfuzhai": "current_liabilities",
            "changqifuzhai": "long_liabilities", "jingzichan": "net_assets",
            "zhuyingshouru": "operating_revenue", "jinglirun": "net_profit",
            "meigujingzichan": "net_asset_value_per_share",
        }
        result = {}
        for py_key, en_key in field_map.items():
            if py_key in data:
                result[en_key] = data[py_key]

        current = result.get("current_liabilities", 0)
        long_liab = result.get("long_liabilities", 0)
        result["total_liabilities"] = current + long_liab
        return result

    # --- internal ---

    def _get_all_bars(self, symbol: str) -> pd.DataFrame:
        market = self._market(symbol)
        all_data = []
        start = 0
        while True:
            data = self._api.get_security_bars(4, market, symbol, start, 800)
            if not data:
                break
            all_data = data + all_data
            if len(data) < 800:
                break
            start += 800
        if not all_data:
            return pd.DataFrame()
        df = self._api.to_df(all_data)
        df = df.rename(columns={"datetime": "date", "vol": "volume"})
        df["date"] = pd.to_datetime(df["date"])
        return df[["date", "open", "high", "low", "close", "volume", "amount"]]

    def _get_xdxr(self, symbol: str) -> pd.DataFrame:
        market = self._market(symbol)
        data = self._api.get_xdxr_info(market, symbol)
        if not data:
            return pd.DataFrame()
        return self._api.to_df(data)

    def _apply_qfq(self, df: pd.DataFrame, xdxr: pd.DataFrame) -> pd.DataFrame:
        """Apply forward adjustment to price data."""
        records = self._parse_xdxr(xdxr, df)
        if not records:
            return df

        dates = df["date"].values
        n = len(df)
        cum_factors = np.ones(n)
        for rec in records:
            mask = dates >= np.datetime64(rec["date"])
            cum_factors[mask] *= rec["factor"]

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col] * cum_factors
        return df

    def _parse_xdxr(self, xdxr: pd.DataFrame, bars: pd.DataFrame) -> list[dict]:
        records = []
        bars_dates = bars["date"].values
        bars_close = bars["close"].values

        for _, row in xdxr.iterrows():
            try:
                year, month, day = int(row["year"]), int(row["month"]), int(row["day"])
                if year < 1990 or not (1 <= month <= 12) or not (1 <= day <= 31):
                    continue
                ex_date = pd.Timestamp(year=year, month=month, day=day)

                idx = np.searchsorted(bars_dates, np.datetime64(ex_date)) - 1
                if idx < 0:
                    continue
                prev_close = float(bars_close[idx])
                if prev_close <= 0:
                    continue

                fh = float(row.get("fenhong", 0) or 0)
                szg = float(row.get("songzhuangu", 0) or 0)
                pg = float(row.get("peigu", 0) or 0)
                pgj = float(row.get("peigujia", 0) or 0)

                if any(np.isnan(v) for v in [fh, szg, pg, pgj]):
                    continue
                if fh == 0 and szg == 0 and pg == 0:
                    continue

                num = prev_close - fh / 10.0
                den = prev_close + szg / 10.0
                if pg > 0 and pgj > 0:
                    pgs = pg / 10.0
                    den += pgs * (pgj - prev_close) / (1 + pgs)
                if den <= 0:
                    continue

                factor = num / den
                if 0 < factor <= 2:
                    records.append({"date": ex_date, "factor": factor})
            except (ValueError, KeyError, ZeroDivisionError):
                continue

        records.sort(key=lambda x: x["date"])
        return records

    @staticmethod
    def _fix_index_dates(df: pd.DataFrame) -> pd.Series:
        """Fix garbled dates in pytdx index data via anchor interpolation."""
        valid = (
            (df["year"] >= 1990) & (df["year"] <= 2099)
            & (df["month"] >= 1) & (df["month"] <= 12)
            & (df["day"] >= 1) & (df["day"] <= 31)
        )
        v_idx = df.index[valid].tolist()
        if len(v_idx) < 2:
            return pd.to_datetime(df["datetime"], errors="coerce")

        v_dates = [pd.Timestamp(int(df.loc[i, "year"]), int(df.loc[i, "month"]),
                                int(df.loc[i, "day"])) for i in v_idx]
        result = pd.Series(pd.NaT, index=df.index)
        for i, idx in enumerate(v_idx):
            result.iloc[idx] = v_dates[i]

        for i in range(len(v_idx) - 1):
            gap = v_idx[i + 1] - v_idx[i]
            if gap <= 1:
                continue
            for j in range(1, gap):
                result.iloc[v_idx[i] + j] = v_dates[i] + (v_dates[i + 1] - v_dates[i]) * j / gap

        if v_idx[-1] < len(df) - 1:
            step = (v_dates[-1] - v_dates[-2]) / max(1, v_idx[-1] - v_idx[-2])
            for j in range(v_idx[-1] + 1, len(df)):
                result.iloc[j] = v_dates[-1] + step * (j - v_idx[-1])

        return result.dt.normalize()
