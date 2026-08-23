#!/usr/bin/env python3
"""Market data adapter for Qtrade — wraps DataService (Tencent live + CSV cache).

Interface-compatible with a-share-skill's MarketDataProvider so the upstream
PaperTradingEngine (MIT) can be reused without pulling in akshare/requests.

Adapted from: https://github.com/shouldnotappearcalm/a-share-skill (MIT)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd


def _code_digits(code: str) -> str:
    c = (code or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if c.startswith(prefix):
            c = c[len(prefix):]
    return c


def normalize_code(code: str) -> str:
    c = (code or "").strip().lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.isdigit():
        if c.startswith(("6", "9")):
            return "sh" + c
        if c.startswith(("4", "8")):
            return "bj" + c
        return "sz" + c
    return c


def normalize_realtime_code(code: str) -> str:
    return normalize_code(code)


def normalize_history_code(code: str) -> str:
    return normalize_code(code)


def infer_limit_ratio(symbol: str, name: str = "") -> float:
    digits = _code_digits(symbol)
    upper_name = str(name or "").upper()
    if digits.startswith(("300", "301", "688", "689")):
        return 0.20
    if "ST" in upper_name:
        return 0.05
    if digits.startswith(("430", "440", "830", "831", "832", "833", "834", "835", "836", "837", "838", "839", "870", "871", "872", "873", "874", "875", "876", "877", "878", "879")):
        return 0.30
    return 0.10


def infer_limit_prices(symbol: str, prev_close: float, name: str = "") -> tuple[Optional[float], Optional[float]]:
    if prev_close <= 0:
        return None, None
    ratio = infer_limit_ratio(symbol, name)
    up = round(prev_close * (1 + ratio), 2)
    down = round(prev_close * (1 - ratio), 2)
    return up, down


@dataclass
class Quote:
    symbol: str
    name: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    change_pct: float
    timestamp: str
    source: str
    limit_up: Optional[float] = None
    limit_down: Optional[float] = None
    stale: bool = False   # True＝行情是历史/缓存，不是当日实时价


def _fmt_time(ts) -> str:
    """腾讯时间 '20260819101415' -> '2026-08-19 10:14:15'。"""
    s = str(ts or "").strip()
    if len(s) == 14 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
    return s


def is_trading_session(now: datetime) -> bool:
    from datetime import time as time_type

    if now.weekday() >= 5:
        return False
    current = now.time()
    return (
        time_type(9, 30) <= current <= time_type(11, 30)
        or time_type(13, 0) <= current <= time_type(15, 0)
    )


class MarketDataProvider:
    """Fetches quotes / K-line from Qtrade's DataService.

    The engine only ever needs a handful of symbols (held positions), so a
    per-call fetch through DataService is fine and reuses the existing
    Tencent-live + CSV fallback pipeline already used by the rest of Qtrade.
    """

    def __init__(self, service=None):
        self.service = service

    def normalize_symbol(self, symbol: str) -> str:
        return _code_digits(symbol).zfill(6)

    # ---------- quote ----------

    def _quote_from_info(self, symbol: str) -> Quote:
        if self.service is None:
            raise ValueError("MarketDataProvider has no DataService")

        info = self.service.get_info(symbol) or {}
        live_ts = bool(info.get("time"))
        is_live_mode = bool(getattr(self.service, "live", True))

        price = float(info.get("latest") or 0)
        if price <= 0:
            df = self.service._resolve_df(symbol)
            if df is None or df.empty:
                raise ValueError(f"no quote data for {symbol}")
            price = float(df["close"].iloc[-1])
            info = {
                "name": "",
                "open": float(df["open"].iloc[-1]),
                "high": float(df["high"].iloc[-1]),
                "low": float(df["low"].iloc[-1]),
                "change_pct": 0.0,
                "volume": 0,
                "latest": price,
                "prev_close": float(df["close"].iloc[-2]) if len(df) > 1 else price,
            }
            live_ts = False

        prev_close = float(info.get("prev_close") or 0)
        if prev_close <= 0 and info.get("change_pct") is not None:
            cp = float(info.get("change_pct") or 0)
            prev_close = price / (1 + cp / 100.0) if cp != -100 else price
        prev_close = prev_close or price

        name = str(info.get("name") or symbol)
        limit_up, limit_down = infer_limit_prices(symbol, prev_close, name)

        ts_raw = info.get("time")
        if not ts_raw:
            try:
                df = self.service._resolve_df(symbol)
                if df is not None and len(df) > 0:
                    ts_raw = str(df.index[-1])
            except Exception:
                pass
        ts = _fmt_time(ts_raw or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        # stale=True：实时模式下拿到的却是历史/缓存行情 → 引擎应拒绝以此价成交
        stale = is_live_mode and not live_ts

        return Quote(
            symbol=self.normalize_symbol(symbol),
            name=name,
            price=round(price, 3),
            open=round(float(info.get("open") or price), 3),
            high=round(float(info.get("high") or price), 3),
            low=round(float(info.get("low") or price), 3),
            prev_close=round(prev_close, 3),
            volume=int(info.get("volume") or 0),
            change_pct=round(float(info.get("change_pct") or 0), 3),
            timestamp=ts,
            source="qtrade-live" if live_ts else "qtrade-csv",
            limit_up=limit_up,
            limit_down=limit_down,
            stale=stale,
        )

    def get_quote(self, symbol: str) -> Quote:
        return self._quote_from_info(symbol)

    def get_quotes(self, symbols: List[str]) -> Dict[str, Quote]:
        out: Dict[str, Quote] = {}
        for s in symbols:
            try:
                q = self.get_quote(s)
                out[q.symbol] = q
            except Exception:
                continue
        return out

    # ---------- history ----------

    def get_history(self, symbol: str, start: str | None = None, end: str | None = None, count: int = 240) -> pd.DataFrame:
        count = int(count or 240)
        df = None
        if self.service is not None:
            df = self.service._resolve_df(symbol, count=max(120, count + 5))
        if df is None or df.empty:
            raise ValueError(f"failed to load history for {symbol}")

        out = df.tail(max(2, count)).copy()
        cols = ["time", "open", "high", "low", "close", "volume"]
        if "time" not in out.columns:
            out = out.reset_index()
            out = out.rename(columns={out.columns[0]: "time"})
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in out.columns:
                return pd.DataFrame(columns=cols)
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out["time"] = pd.to_datetime(out["time"], errors="coerce")
        out = out.dropna(subset=["time", "open", "high", "low", "close"]).sort_values("time")
        if start:
            out = out[out["time"] >= pd.to_datetime(start)]
        if end:
            out = out[out["time"] <= pd.to_datetime(end)]
        return out.tail(count).reset_index(drop=True)[cols]

    def get_intraday_bars(self, symbol: str, freq: str = "1m", count: int = 240) -> pd.DataFrame:
        # Qtrade has no 1m history in CSV cache; engine gracefully falls back
        # to quote-price matching when this is empty.
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def get_mainboard_universe(self, as_of: str | None = None, top_n: int = 80) -> list[str]:
        return []
