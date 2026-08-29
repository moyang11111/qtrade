#!/usr/bin/env python
"""
QTrade Desktop — 现代前端交易终端（后端服务）
================================================
零依赖 HTTP 服务：
  - 静态文件服务（前端 HTML/CSS/JS）
  - JSON API（股票列表 / K线 / 行情 / 指标 / 回测）

数据模式：
  live（默认）— 通过腾讯实时接口获取日K与快照，仅内存缓存，不落盘，关闭即失
  csv         — 读取本地 CSV 缓存（--csv-only）

用法:
    python server.py
    python server.py --data-dir C:/Users/ASUS/qtrade/data/cache --port 8765
    python server.py --csv-only           # 只用本地 CSV
    python server.py --no-browser
"""

import sys
import io

# Windows GBK 编码修复移到 main() 内执行：
# 避免直接 import server 时覆盖 sys.stdout，破坏 pytest 捕获输出。
import json
import time
import os
import math
import socket
import argparse
import re
import threading
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import qtrade_base_bridge
from qtrade_adapters.deepseek_harness import runtime as update_runtime

import pandas as pd
import numpy as np

# ---- 首批 A 股实证因子（移植自 deepseek-harness-quant 思路）----
import factors as factors_mod

# ---- 本地仿真盘引擎（a-share-skill, MIT）----
from paper_trading.engine import PaperTradingEngine
from paper_trading.market_data import MarketDataProvider, infer_limit_prices
from qtrade_adapters.deepseek_harness.market_data import (
    MIN_HISTORY_ROWS,
    MainboardMarketDataAdapter,
    normalize_code,
)
from qtrade_adapters.deepseek_harness.factor_library import (
    FactorLibrary,
    FactorLibraryError,
    FactorValidationError,
    MAX_BODY_BYTES,
    resolve_factor_library_path,
)
from qtrade_adapters.deepseek_chat import config as deepseek_chat_config
from qtrade_adapters.deepseek_chat.context import ContextProvider, build_context
from qtrade_adapters.deepseek_chat.service import DeepSeekChatError, DeepSeekChatService

# ============================================================================
# 实时数据源（腾讯）
# ============================================================================

def market_prefix(code: str) -> str:
    """A 股代码 → 腾讯市场前缀。"""
    code = str(code).strip()
    if code.startswith(("6", "9")):
        return "sh" + code
    if code.startswith(("0", "2", "3")):
        return "sz" + code
    if code.startswith(("4", "8")):
        return "bj" + code
    return code


# 无本地 CSV 时的内置常用股票池（沪深主流）
COMMON_STOCKS = [
    "000001", "000002", "000063", "000333", "000338", "000425", "000538", "000568",
    "000651", "000725", "000768", "000776", "000858", "000895", "000938",
    "002027", "002049", "002230", "002304", "002352", "002371", "002415", "002460",
    "002475", "002594", "002714", "002812", "002841",
    "300014", "300015", "300059", "300122", "300124", "300274", "300308", "300347",
    "300408", "300413", "300433", "300498", "300502", "300750", "300759",
    "600000", "600009", "600016", "600019", "600028", "600030", "600031", "600036",
    "600048", "600050", "600061", "600104", "600111", "600150", "600276", "600309",
    "600340", "600346", "600362", "600438", "600519", "600547", "600570", "600585",
    "600588", "600690", "600745", "600795", "600809", "600837", "600887", "600893",
    "600900", "600941", "600958", "600989", "600999",
    "601012", "601066", "601088", "601111", "601127", "601138", "601166", "601169",
    "601186", "601211", "601225", "601288", "601318", "601328", "601336", "601377",
    "601398", "601601", "601628", "601633", "601658", "601668", "601669", "601688",
    "601728", "601766", "601800", "601818", "601857", "601888", "601899", "601919",
    "601939", "601988", "601989", "601995", "601998",
    "603019", "603160", "603259", "603288", "603501", "603986", "603993",
    "688008", "688009", "688012", "688036", "688111", "688126", "688169", "688180",
    "688185", "688187", "688256", "688271", "688303", "688363", "688390", "688396",
    "688425", "688536", "688599", "688728", "688779", "688981",
]


class TencentLiveSource:
    """腾讯实时数据源：日 K（前复权）+ 实时快照。

    仅内存缓存（dict），进程退出即失，不写任何磁盘文件。
    """

    KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    QUOTE_URL = "https://qt.gtimg.cn/q="

    def __init__(self, ttl_kline: float = 60.0, ttl_quote: float = 5.0):
        self.ttl_kline = ttl_kline
        self.ttl_quote = ttl_quote
        self._kline_cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._quote_cache: dict[str, tuple[float, dict]] = {}
        self.last_error: str | None = None

    # ---------- 日 K ----------

    def fetch_kline(self, symbol: str, count: int = 320) -> pd.DataFrame:
        """拉取日 K（前复权），带 TTL 内存缓存。返回 DataFrame(index=date)。"""
        now = time.time()
        hit = self._kline_cache.get(symbol)
        if hit and now - hit[0] < self.ttl_kline:
            return hit[1]

        sym = market_prefix(symbol)
        params = urllib.parse.urlencode({"param": f"{sym},day,,,{count},qfq"})
        url = f"{self.KLINE_URL}?{params}"
        with urllib.request.urlopen(url, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))

        klines = self._parse_kline_response(data, sym)
        if not klines:
            raise RuntimeError(f"腾讯接口无 {symbol} 数据")

        # 腾讯列序: [日期, 开, 收, 高, 低, 量]；个别行可能多带字段，只取前 6 列
        klines = [row[:6] for row in klines]
        df = pd.DataFrame(klines, columns=["date", "open", "close", "high", "low", "volume"])
        for col in ["open", "close", "high", "low", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()

        self._kline_cache[symbol] = (now, df)
        self.last_error = None
        return df

    @staticmethod
    def _parse_kline_response(data: dict, sym: str) -> list:
        node = data.get("data", {}).get(sym, {})
        return node.get("qfqday") or node.get("day") or []

    # ---------- 实时快照 ----------

    def fetch_quote(self, symbol: str) -> dict:
        """拉取实时快照（最新价/涨跌/高低/换手等），带 TTL 缓存。"""
        now = time.time()
        hit = self._quote_cache.get(symbol)
        if hit and now - hit[0] < self.ttl_quote:
            return hit[1]

        sym = market_prefix(symbol)
        with urllib.request.urlopen(self.QUOTE_URL + sym, timeout=8) as r:
            txt = r.read().decode("gbk", errors="replace")

        parts = txt.split("~")
        if len(parts) < 40:
            raise RuntimeError(f"腾讯快照字段不足: {symbol}")

        quote = {
            "symbol": symbol,
            "name": parts[1],
            "price": float(parts[3] or 0),
            "prev_close": float(parts[4] or 0),
            "open": float(parts[5] or 0),
            "volume": float(parts[6] or 0),          # 手
            "change": float(parts[31] or 0),
            "change_pct": float(parts[32] or 0),
            "high": float(parts[33] or 0),
            "low": float(parts[34] or 0),
            "amount": float(parts[37] or 0),         # 万元
            "turnover": float(parts[38] or 0),       # 换手率 %
            "pe": float(parts[39] or 0),             # 市盈率
            "amplitude": float(parts[43] or 0),      # 振幅 %
            "time": parts[30],
        }
        self._quote_cache[symbol] = (now, quote)
        self.last_error = None
        return quote

    # ---------- 可用性 ----------

    @staticmethod
    def available() -> bool:
        try:
            with urllib.request.urlopen("https://qt.gtimg.cn/q=sh600519", timeout=5):
                return True
        except Exception:
            return False


# ============================================================================
# 技术指标
# ============================================================================

def sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line, macd_line - signal_line


def rsi(close: pd.Series, period=14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def bollinger(close: pd.Series, period=20, std=2):
    ma = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    return ma + std * sigma, ma, ma - std * sigma


def _round_or_none(v) -> float | None:
    if pd.isna(v):
        return None
    return round(float(v), 2)


def _ts(index, i) -> int:
    t = index[i]
    return int(t.timestamp()) if hasattr(t, "timestamp") else int(pd.Timestamp(t).timestamp())


# ============================================================================
# 数据服务
# ============================================================================

class DataService:
    """股票数据服务：实时（腾讯）优先，CSV 回退；全部仅内存缓存。"""

    def __init__(self, data_dir: str, live: bool = True):
        self.data_dir = Path(data_dir)
        self.live = live
        self.live_src = TencentLiveSource() if live else None
        self._df_cache: dict[str, pd.DataFrame] = {}
        self._ind_cache: dict[str, dict] = {}
        self._csv_symbols: list[str] | None = None
        self.mainboard_adapter = MainboardMarketDataAdapter(
            base_dir=qtrade_base_bridge.base_dir(),
            csv_dir=self.data_dir,
        )
        self._candidate_symbols: set[str] = set()

    # ---------- 数据源解析 ----------

    def _resolve_df(self, symbol: str, count: int = 320) -> pd.DataFrame | None:
        """实时优先拉取，失败回退内存缓存/CSV；仅内存缓存，不落盘。

        live_src 存在就尝试实时（即使启动时可用性探测失败，也能在运行中恢复实时）。
        """
        if self.live_src:
            try:
                df = self.live_src.fetch_kline(symbol, count)  # 内部 TTL 缓存
                self._df_cache[symbol] = df
                self.live = True
                return df
            except Exception:
                pass  # 回退
        if symbol in self._df_cache:
            return self._df_cache[symbol]
        return self.load_history(symbol)

    def _load_csv(self, symbol: str) -> pd.DataFrame | None:
        # 命中内存缓存直接返回（主板全市场扫描每轮需要三千+只，避免重复磁盘IO）
        if symbol in self._df_cache:
            return self._df_cache[symbol]
        path = self.data_dir / f"{symbol}.csv"
        if not path.exists():
            return None
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df.columns = [c.lower() for c in df.columns]
            self._df_cache[symbol] = df
            return df
        except Exception:
            return None

    def load_history(self, symbol: str, count: int = 320) -> pd.DataFrame | None:
        """Load local qfq history through the read-only adapter, then CSV fallback."""

        if symbol in self._df_cache:
            return self._df_cache[symbol]
        if self.mainboard_adapter.available:
            try:
                frame = self.mainboard_adapter.get_history(symbol, count=count)
                if frame is not None and not frame.empty:
                    self._df_cache[symbol] = frame
                    return frame
            except Exception:
                pass
        return self._load_csv(symbol)

    # ---------- 股票列表 ----------

    def scan(self) -> list[str]:
        """Return the read-only full mainboard list, or the legacy fallback pool."""

        if self.mainboard_adapter.available:
            return self.mainboard_adapter.scan()
        if self._csv_symbols is None:
            symbols = []
            if self.data_dir.exists():
                symbols = [
                    p.stem for p in self.data_dir.glob("*.csv")
                    if p.stat().st_size > 500
                ]
            self._csv_symbols = symbols
        # 合并内置池，去重保持顺序
        seen, merged = set(self._csv_symbols), list(self._csv_symbols)
        for s in COMMON_STOCKS:
            if s not in seen:
                seen.add(s)
                merged.append(s)
        return merged

    def mainboard_symbols(self) -> list[str]:
        """Return listed Shanghai/Shenzhen mainboard symbols in stable order."""

        if self.mainboard_adapter.available:
            return self.mainboard_adapter.scan()
        seen: set[str] = set()
        symbols: list[str] = []
        for raw in self.scan():
            code = normalize_code(raw)
            if code is None or not (code.startswith("60") or code.startswith("00")):
                continue
            if code not in seen:
                seen.add(code)
                symbols.append(code)
        return symbols

    def symbol_metadata(self, symbol: str) -> dict | None:
        """Return safe metadata for a symbol without exposing database paths."""

        if self.mainboard_adapter.available:
            return self.mainboard_adapter.metadata(symbol)
        code = normalize_code(symbol)
        if code not in set(self.mainboard_symbols()):
            return None
        frame = self._load_csv(code)
        rows = len(frame) if frame is not None else 0
        latest = str(frame.index[-1])[:10] if frame is not None and len(frame) else None
        return {
            "code": code,
            "name": code,
            "exchange": "SH" if code.startswith("60") else "SZ",
            "risk_warning": None,
            "listed": True,
            "suspended": False,
            "tradable": True,
            "latest_trade_date": latest,
            "history_rows": rows,
            "computable": rows >= MIN_HISTORY_ROWS,
            "eligible_reason": None if rows >= MIN_HISTORY_ROWS else "history_insufficient",
            "source": "fallback",
        }

    def is_tradable(self, symbol: str) -> bool:
        metadata = self.symbol_metadata(symbol)
        return bool(metadata and metadata.get("tradable") and metadata.get("listed"))

    def set_candidate_symbols(self, symbols) -> None:
        self._candidate_symbols = {
            code for code in (normalize_code(symbol) for symbol in (symbols or [])) if code
        }

    @property
    def universe_summary(self) -> dict:
        """Return total/computable/tradable/candidate counts for the current snapshot."""

        if self.mainboard_adapter.available:
            return self.mainboard_adapter.universe_summary(self._candidate_symbols)
        records = []
        as_of = None
        for code in self.mainboard_symbols():
            frame = self._load_csv(code)
            rows = len(frame) if frame is not None else 0
            latest = str(frame.index[-1])[:10] if frame is not None and len(frame) else None
            if latest and (as_of is None or latest > as_of):
                as_of = latest
            records.append((code, rows))
        computable = {code for code, rows in records if rows >= MIN_HISTORY_ROWS}
        return {
            "total": len(records),
            "computable": len(computable),
            "tradable": len(records),
            "candidate": len(computable & self._candidate_symbols),
            "excluded_by_reason": {
                "history_insufficient": len(records) - len(computable),
            } if len(records) != len(computable) else {},
            "as_of": as_of,
            "source": "fallback",
            "reason": self.mainboard_adapter.last_error or "external_database_unavailable",
        }

    # ---------- 查询 ----------

    def get_kline(self, symbol: str, limit: int = 300) -> list[dict]:
        df = self._resolve_df(symbol)
        if df is None:
            return []
        df = df.tail(limit)
        out = []
        for i in range(len(df)):
            row = df.iloc[i]
            out.append({
                "time": _ts(df.index, i),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
            })
        return out

    def get_info(self, symbol: str) -> dict:
        """行情概要：实时快照 + K线派生指标。"""
        quote = None
        if self.live_src:
            try:
                quote = self.live_src.fetch_quote(symbol)
                if quote:
                    self.live = True
            except Exception:
                quote = None

        df = self._resolve_df(symbol)
        if df is None and quote is None:
            return {}

        info = {"symbol": symbol}
        # 快照字段（实时）
        if quote:
            info.update({
                "name": quote["name"],
                "latest": quote["price"],
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "change": quote["change"],
                "change_pct": quote["change_pct"],
                "volume": quote["volume"],
                "turnover": quote["turnover"],
                "pe": quote["pe"],
                "time": quote["time"],
            })

        # K线派生字段
        if df is not None and not df.empty:
            close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
            info.update({
                "high_60d": round(float(high.tail(60).max()), 2),
                "low_60d": round(float(low.tail(60).min()), 2),
                "vol_avg_20d": int(vol.tail(20).mean()) if not pd.isna(vol.tail(20).mean()) else 0,
                "rows": len(df),
                "date": str(df.index[-1].date()),
            })
            if quote is None:  # CSV 兜底时补基础字段
                latest = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) > 1 else latest
                info["latest"] = round(latest, 2)
                info["open"] = round(float(df["open"].iloc[-1]), 2)
                info["high"] = round(float(df["high"].iloc[-1]), 2)
                info["low"] = round(float(df["low"].iloc[-1]), 2)
                info["change"] = round(latest - prev, 2)
                info["change_pct"] = round((latest / prev - 1) * 100, 2) if prev else 0.0
        return info

    def get_indicators(self, symbol: str) -> dict:
        if symbol in self._ind_cache:
            return self._ind_cache[symbol]

        df = self._resolve_df(symbol)
        if df is None:
            return {}
        close = df["close"]
        n = len(df)
        tail_n = 300
        start = max(0, n - tail_n)

        # MA（带 time）
        mas = {}
        for p in [5, 10, 20, 60]:
            ma = sma(close, p)
            mas[f"ma{p}"] = [
                {"time": _ts(df.index, i), "value": _round_or_none(v)}
                for i, v in enumerate(ma.iloc[start:], start=start)
            ]

        # MACD
        macd_line, signal_line, hist = macd(close)
        macd_data = []
        for i in range(start, n):
            macd_data.append({
                "time": _ts(df.index, i),
                "macd": round(float(macd_line.iloc[i]), 4) if not pd.isna(macd_line.iloc[i]) else 0,
                "signal": round(float(signal_line.iloc[i]), 4) if not pd.isna(signal_line.iloc[i]) else 0,
                "histogram": round(float(hist.iloc[i]), 4) if not pd.isna(hist.iloc[i]) else 0,
            })

        # RSI
        r = rsi(close)
        rsi_data = []
        for i in range(start, n):
            v = r.iloc[i]
            rsi_data.append({
                "time": _ts(df.index, i),
                "value": round(float(v), 1) if not pd.isna(v) else None,
            })

        # BOLL
        upper, mid, lower = bollinger(close)
        boll_data = []
        for i in range(start, n):
            boll_data.append({
                "time": _ts(df.index, i),
                "upper": _round_or_none(upper.iloc[i]),
                "middle": _round_or_none(mid.iloc[i]),
                "lower": _round_or_none(lower.iloc[i]),
            })

        result = {"mas": mas, "macd": macd_data, "rsi": rsi_data, "boll": boll_data}
        self._ind_cache[symbol] = result
        return result

    def get_factors(self, symbol: str) -> dict:
        """返回最新 A 股量价因子（移植自 deepseek-harness-quant）。

        rps_120 属于横截面因子，这里在『最新时点』用全市场 120 日涨幅计算百分位。
        """
        df = self._resolve_df(symbol)
        if df is None:
            return {"error": f"无法加载 {symbol}"}
        out = factors_mod.latest_factors(df)
        if out.get("symbol") is None:
            out["symbol"] = symbol
        # RPS：全市场横截面（限制样本数以免首查太慢）
        try:
            close = df["close"].astype(float)
            sym_ret = float(close.iloc[-1] / close.iloc[-121] - 1) if len(df) > 121 else None
            uni_rets = []
            for s in self.mainboard_symbols()[:300]:
                d = self.load_history(s)
                if d is not None and len(d) > 121:
                    uni_rets.append(float(d["close"].astype(float).iloc[-1] / d["close"].astype(float).iloc[-121] - 1))
            rps = factors_mod.rps_percentile(sym_ret, uni_rets)
            if rps is not None:
                out["rps_120"] = rps
                out["universe_size"] = len(uni_rets)
        except Exception:
            pass
        return out

    # ---------- A股费用模型（对齐 deepseek-harness-quant / a-share-skill） ----------

    @staticmethod
    def _is_sh(symbol) -> bool:
        return bool(symbol and str(symbol).startswith(("6", "9")))

    @staticmethod
    def _fee_buy(amount: float, rate: float, symbol: str = "") -> float:
        fee = max(5.0, round(amount * float(rate), 2))
        if DataService._is_sh(symbol):
            fee += round(amount * 0.00001, 2)   # 沪市过户费
        return fee

    @staticmethod
    def _fee_sell(amount: float, rate: float, symbol: str = "") -> float:
        fee = DataService._fee_buy(amount, rate, symbol)
        fee += round(amount * 0.001, 2)          # 卖出印花税
        return fee

    # ---------- 数据审计：PIT / 覆盖率 / 时效 ----------

    @staticmethod
    def _audit_data(df, symbol: str = "") -> dict:
        if df is None or df.empty:
            return {"available": False, "symbol": symbol, "rows": 0}
        idx = pd.DatetimeIndex(df.index)
        start, end = idx.min(), idx.max()
        rows = len(df)
        years = max((end - start).days / 365.25, 0.01)
        per_year = round(rows / years, 1)
        today = pd.Timestamp.today().normalize()
        stale_days = max(0, (today - pd.Timestamp(end).normalize()).days)
        recent = df[df.index >= (today - pd.Timedelta(days=365))]
        coverage_recent = round(len(recent) / 242.0 * 100, 1)   # A股年均约242个交易日
        columns_ok = all(c in df.columns for c in ("open", "high", "low", "close", "volume"))
        return {
            "available": True,
            "symbol": symbol,
            "rows": rows,
            "start": str(start.date()),
            "end": str(end.date()),
            "years": round(years, 2),
            "bars_per_year": per_year,
            "stale_days": stale_days,
            "coverage_recent_1y_pct": min(100.0, coverage_recent),
            "columns_ok": columns_ok,
            "audit_pass": columns_ok and stale_days <= 10 and coverage_recent >= 50,
        }

    # ---------- 回测 ----------

    def run_backtest(self, symbol: str, strategy: str, capital: float,
                     commission: float, stop_loss: float, take_profit: float,
                     factors: list | None = None, weights: dict | None = None) -> dict:
        df = self._resolve_df(symbol)
        if df is None:
            return {"error": f"无法加载 {symbol}"}

        if strategy == "rsi_layered":
            # RSI 分层买入策略：专用模拟器（逐层建仓，与信号模型不同）
            result = self._simulate_rsi_layered(df, capital, commission, symbol=symbol)
        else:
            signals = self._generate_signals(df, strategy, factors=factors, weights=weights)
            result = self._simulate(df, signals, capital, commission, stop_loss, take_profit, symbol=symbol)
        result.update({"symbol": symbol, "strategy": strategy})
        result["audit"] = self._audit_data(df, symbol)
        return result

    @staticmethod
    def _generate_signals(df: pd.DataFrame, strategy: str,
                          factors: list | None = None, weights: dict | None = None) -> pd.Series:
        close = df["close"]
        high, low, vol = df["high"], df["low"], df["volume"]
        signals = pd.Series(0, index=df.index)

        if strategy in ("factor_score", "factor_score_custom"):
            # 首批 A 股实证因子合成打分（偏多>1，偏空<-1）
            score = factors_mod.composite_score(df, factors=factors, weights=weights)
            signals[score >= 1.0] = 1
            signals[score <= -1.0] = -1

        elif strategy == "dual_ma":
            ma5, ma20 = sma(close, 5), sma(close, 20)
            signals[ma5 > ma20] = 1
            signals[ma5 < ma20] = -1

        elif strategy == "trend_5d":
            ma5, ma10 = sma(close, 5), sma(close, 10)
            signals[(ma5 > ma10) & (close > ma5)] = 1
            signals[(ma5 < ma10) | (close < ma5)] = -1

        elif strategy == "bollinger":
            upper, mid, lower = bollinger(close)
            signals[close < lower] = 1
            signals[close > upper] = -1

        elif strategy == "bb_rsi":
            _, mid, lower = bollinger(close)
            r = rsi(close)
            signals[(close < lower) & (r < 35)] = 1
            signals[(close > mid) & (r > 60)] = -1

        elif strategy == "pullback_20d":
            high_60 = high.rolling(60).max()
            pullback = close / high_60 - 1
            vol_5, vol_20 = vol.rolling(5).mean(), vol.rolling(20).mean()
            ma60 = sma(close, 60)
            cond = (pullback < -0.15) & (pullback > -0.40) & (vol_5 / vol_20 < 0.7) & (close > ma60)
            signals[cond] = 1
            for idx in signals[signals == 1].index:
                pos = df.index.get_loc(idx)
                exit_pos = min(pos + 20, len(df) - 1)
                if exit_pos > pos:
                    signals.iloc[pos + 1:exit_pos] = 1
                    signals.iloc[exit_pos] = -1

        elif strategy == "pullback_deep":
            high_60 = high.rolling(60).max()
            pullback = (close / high_60 - 1) * 100
            cond = (pullback < -25) & (pullback > -50)
            signals[cond] = 1
            for i in range(1, len(signals)):
                prev = signals.iloc[i - 1]
                if prev == 1 and pullback.iloc[i] > -10:
                    signals.iloc[i] = -1
                elif prev == 1:
                    signals.iloc[i] = 1

        elif strategy == "breakout":
            high_20 = high.rolling(20).max()
            signals[close > high_20.shift(1)] = 1
            for i in range(1, len(signals)):
                if signals.iloc[i - 1] == 1:
                    signals.iloc[i] = -1 if close.iloc[i] < close.iloc[i - 1] * 0.95 else 1

        elif strategy == "reversal_20":
            """超跌反弹（反转效应）：过去20日跌>20%买入，持有20日后卖出"""
            mom20 = close.pct_change(20) * 100
            signals[mom20 < -20] = 1
            for idx in signals[signals == 1].index:
                pos = df.index.get_loc(idx)
                exit_pos = min(pos + 20, len(df) - 1)
                if exit_pos > pos:
                    signals.iloc[pos + 1:exit_pos] = 1
                    signals.iloc[exit_pos] = -1

        elif strategy == "reversal_combo":
            """超跌反弹增强版：RSI<25 且 距60日高点回撤<-30%，持有20日卖出"""
            mom20 = close.pct_change(20) * 100
            high60 = high.rolling(60).max()
            dd60 = (close / high60 - 1) * 100
            r14 = rsi(close, 14)
            cond = (mom20 < -20) | ((r14 < 25) & (dd60 < -30))
            signals[cond] = 1
            for idx in signals[signals == 1].index:
                pos = df.index.get_loc(idx)
                exit_pos = min(pos + 20, len(df) - 1)
                if exit_pos > pos:
                    signals.iloc[pos + 1:exit_pos] = 1
                    signals.iloc[exit_pos] = -1

        elif strategy == "turtle":
            """海龟交易法（vnpy 44k★ 官方示例）：20日突破入场，10日低点+2×ATR离场"""
            prev_c = close.shift(1)
            tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1 / 20, adjust=False).mean()
            entry_high = high.rolling(20).max().shift(1)
            exit_low = low.rolling(10).min().shift(1)
            holding, stop = False, np.nan
            for i in range(len(signals)):
                c = close.iloc[i]
                if not holding:
                    if not np.isnan(entry_high.iloc[i]) and c > entry_high.iloc[i]:
                        signals.iloc[i] = 1
                        holding, stop = True, c - 2.0 * atr.iloc[i]
                else:
                    eff_stop = np.nanmax([exit_low.iloc[i], stop])
                    if c < eff_stop:
                        signals.iloc[i] = -1
                        holding, stop = False, np.nan
                    else:
                        signals.iloc[i] = 1
                        stop = max(stop, c - 2.0 * atr.iloc[i])

        elif strategy == "supertrend":
            """SuperTrend(10,3)：趋势线翻多买入，翻空卖出（pandas_ta 参考）"""
            hl2 = (high + low) / 2
            prev_c = close.shift(1)
            tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
            atr = tr.ewm(alpha=1 / 10, adjust=False).mean()
            upper, lower = hl2 + 3.0 * atr, hl2 - 3.0 * atr
            fu, fl, trend = upper.copy(), lower.copy(), np.ones(len(signals))
            for i in range(1, len(signals)):
                fu.iloc[i] = upper.iloc[i] if (upper.iloc[i] < fu.iloc[i - 1] or close.iloc[i - 1] > fu.iloc[i - 1]) else fu.iloc[i - 1]
                fl.iloc[i] = lower.iloc[i] if (lower.iloc[i] > fl.iloc[i - 1] or close.iloc[i - 1] < fl.iloc[i - 1]) else fl.iloc[i - 1]
                if close.iloc[i] > fu.iloc[i - 1]:
                    trend[i] = 1
                elif close.iloc[i] < fl.iloc[i - 1]:
                    trend[i] = -1
                else:
                    trend[i] = trend[i - 1]
            signals[trend == 1] = 1
            signals[trend == -1] = -1

        elif strategy == "dual_thrust":
            """Dual Thrust(N=4, K1=0.4, K2=0.6)：开盘±K×Range 突破（fmzquant/vnpy）"""
            hh = high.rolling(4).max().shift(1)
            hc = close.rolling(4).max().shift(1)
            lc = close.rolling(4).min().shift(1)
            ll = low.rolling(4).min().shift(1)
            rng = pd.concat([hh - lc, hc - ll], axis=1).max(axis=1)
            open_ = df["open"]
            buy_break = close > open_ + 0.4 * rng
            sell_break = close < open_ - 0.6 * rng
            holding = False
            for i in range(len(signals)):
                if np.isnan(rng.iloc[i]):
                    continue
                if not holding and buy_break.iloc[i]:
                    signals.iloc[i] = 1
                    holding = True
                elif holding:
                    if sell_break.iloc[i]:
                        signals.iloc[i] = -1
                        holding = False
                    else:
                        signals.iloc[i] = 1

        elif strategy == "boll_reversion":
            """布林带(20,2)均值回归：下轨超卖买入，中轨回归卖出，MA60过滤"""
            mid = close.rolling(20).mean()
            std = close.rolling(20).std()
            lower = mid - 2.0 * std
            ma_trend = close.rolling(60).mean()
            buy = (close < lower) & (close > ma_trend)
            reach_mid = close >= mid
            holding = False
            for i in range(len(signals)):
                if np.isnan(lower.iloc[i]) or np.isnan(ma_trend.iloc[i]):
                    continue
                if not holding and buy.iloc[i]:
                    signals.iloc[i] = 1
                    holding = True
                elif holding:
                    if reach_mid.iloc[i]:
                        signals.iloc[i] = -1
                        holding = False
                    else:
                        signals.iloc[i] = 1

        return signals

    @staticmethod
    def _simulate(df, signals, capital, commission, stop_loss, take_profit, symbol="") -> dict:
        close = df["close"]
        open_ = df["open"]
        # 隔离未来函数：T-1 收盘确认信号，T 日开盘成交
        exec_sig = signals.shift(1).fillna(0)
        trades, equity = [], [capital]
        position, entry_price, cash = 0, 0, capital

        for i in range(1, len(df)):
            sig = int(exec_sig.iloc[i])
            price = float(open_.iloc[i])
            mkt = price if (price > 0 and not np.isnan(price)) else float(close.iloc[i]) if not pd.isna(close.iloc[i]) else 0.0
            date = str(df.index[i])[:10]

            prev_close = float(close.iloc[i - 1]) if i >= 1 and not pd.isna(close.iloc[i - 1]) else float("nan")
            limit_up = limit_down = None
            if symbol and not np.isnan(prev_close):
                limit_up, limit_down = infer_limit_prices(symbol, prev_close)
            blocked_buy = limit_up is not None and mkt >= limit_up - 1e-9
            blocked_sell = limit_down is not None and mkt <= limit_down + 1e-9

            if sig == 1 and position == 0 and not blocked_buy:
                position = cash * 0.95 / mkt
                entry_price = mkt
                amount = position * mkt
                cash -= amount + DataService._fee_buy(amount, commission, symbol)
                trades.append({"type": "buy", "price": round(mkt, 2), "date": date, "pnl": 0})

            elif sig == -1 and position > 0 and not blocked_sell:
                pnl = round((mkt / entry_price - 1) * 100, 2)
                amount = position * mkt
                cash += amount - DataService._fee_sell(amount, commission, symbol)
                trades.append({"type": "sell", "price": round(mkt, 2), "date": date, "pnl": pnl})
                position, entry_price = 0, 0

            elif position > 0:
                pnl_pct = mkt / entry_price - 1
                if not blocked_sell and pnl_pct <= -stop_loss:
                    amount = position * mkt
                    cash += amount - DataService._fee_sell(amount, commission, symbol)
                    trades.append({"type": "stop_loss", "price": round(mkt, 2), "date": date, "pnl": round(pnl_pct * 100, 2)})
                    position, entry_price = 0, 0
                elif not blocked_sell and pnl_pct >= take_profit:
                    amount = position * mkt
                    cash += amount - DataService._fee_sell(amount, commission, symbol)
                    trades.append({"type": "take_profit", "price": round(mkt, 2), "date": date, "pnl": round(pnl_pct * 100, 2)})
                    position, entry_price = 0, 0

            equity.append(round(cash + position * mkt, 2))

        if position > 0:
            final_price = float(close.iloc[-1])
            amount = position * final_price
            cash += amount - DataService._fee_sell(amount, commission, symbol)
            trades.append({
                "type": "close", "price": round(final_price, 2),
                "date": str(df.index[-1])[:10],
                "pnl": round((final_price / entry_price - 1) * 100, 2),
            })

        final_value = cash
        total_return = round((final_value / capital - 1) * 100, 2)
        years = max((df.index[-1] - df.index[0]).days / 365.25, 0.1)
        annual_return = round(((final_value / capital) ** (1 / years) - 1) * 100, 2)

        eq = pd.Series(equity)
        max_dd = round(((eq - eq.cummax()) / eq.cummax()).min() * 100, 2)

        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        total = wins + losses

        score = annual_return - 1.5 * abs(max_dd)
        if score >= 20:
            grade, grade_label = "A", "优秀"
        elif score >= 5:
            grade, grade_label = "B", "良好"
        elif score >= -5:
            grade, grade_label = "C", "一般"
        else:
            grade, grade_label = "D", "较差"

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_dd,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "final_value": round(final_value, 2),
            "trades": trades[-30:],
            "equity": equity,
            "grade": grade,
            "grade_label": grade_label,
            "score": round(score, 2),
        }

    @staticmethod
    def _simulate_rsi_layered(df: pd.DataFrame, capital: float,
                              commission: float = 0.0003, symbol: str = "") -> dict:
        """
        RSI 分层买入策略（网格建仓）——已隔离未来函数：
          依据 T-1 收盘 RSI，在 T 日开盘执行；涨跌停时跳过对应成交。
          - RSI14 < 25   → 买入 1 层仓（1 层 = 10% 初始资金）
          - 此后每从上次买入价下跌 5% → 买入 2 层仓（20%）
          - 总仓位 ≤ 80%
          - RSI14 > 70   → 全部清仓
        """
        close = df["close"]
        open_ = df["open"]
        r14 = rsi(close, 14).shift(1)     # 关键：只用昨日收盘 RSI 决策
        layer_pct = 0.10          # 1 层 = 10% 总资金
        max_pos = 0.80            # 最大 80% 仓位
        total_ref = capital       # 层仓基准 = 初始资金

        cash = capital
        shares = 0.0
        last_buy_price = None
        in_position = False
        trades = []
        equity = []

        for i in range(1, len(df)):
            price = float(open_.iloc[i])
            mkt = price if (price > 0 and not np.isnan(price)) else float(close.iloc[i]) if not pd.isna(close.iloc[i]) else 0.0
            date = str(df.index[i])[:10]
            r = r14.iloc[i]

            prev_close = float(close.iloc[i - 1]) if not pd.isna(close.iloc[i - 1]) else float("nan")
            limit_up = limit_down = None
            if symbol and not np.isnan(prev_close):
                limit_up, limit_down = infer_limit_prices(symbol, prev_close)
            blocked_buy = limit_up is not None and mkt >= limit_up - 1e-9
            blocked_sell = limit_down is not None and mkt <= limit_down + 1e-9

            # —— 卖出：RSI > 70 清仓 ——
            if in_position and not pd.isna(r) and r > 70 and not blocked_sell:
                pnl_pct = round((mkt / last_buy_price - 1) * 100, 2) if last_buy_price else 0
                amount = shares * mkt
                cash += amount - DataService._fee_sell(amount, commission, symbol)
                trades.append({
                    "type": "sell", "price": round(mkt, 2), "date": date,
                    "pnl": pnl_pct, "shares": round(shares, 0),
                    "reason": f"RSI={r:.1f}>70 清仓",
                })
                shares = 0.0
                in_position = False
                last_buy_price = None

            # —— 首次建仓：RSI < 25，买入 1 层 ——
            if not in_position and not pd.isna(r) and r < 25 and not blocked_buy:
                amount = total_ref * layer_pct
                buy_shares = amount / mkt
                cost = buy_shares * mkt + DataService._fee_buy(buy_shares * mkt, commission, symbol)
                if cost <= cash:
                    cash -= cost
                    shares = buy_shares
                    in_position = True
                    last_buy_price = mkt
                    trades.append({
                        "type": "buy", "price": round(mkt, 2), "date": date,
                        "pnl": 0, "shares": round(buy_shares, 0),
                        "reason": f"RSI={r:.1f}<25 首仓1层",
                    })

            # —— 加仓：每跌 5% 买入 2 层（最大仓位 80%）——
            elif in_position and last_buy_price and not pd.isna(r) and not blocked_buy:
                pos_pct = shares * mkt / total_ref
                if mkt <= last_buy_price * 0.95 and pos_pct + 2 * layer_pct <= max_pos:
                    amount = total_ref * 2 * layer_pct
                    buy_shares = amount / mkt
                    cost = buy_shares * mkt + DataService._fee_buy(buy_shares * mkt, commission, symbol)
                    if cost <= cash:
                        cash -= cost
                        shares += buy_shares
                        last_buy_price = mkt   # 以新加仓价为基准
                        trades.append({
                            "type": "buy", "price": round(mkt, 2), "date": date,
                            "pnl": 0, "shares": round(buy_shares, 0),
                            "reason": f"跌5%加仓2层 (仓位{pos_pct*100:.0f}%)",
                        })

            equity.append(round(cash + shares * mkt, 2))

        # —— 期末清仓 ——
        if in_position:
            final_price = float(close.iloc[-1])
            pnl_pct = round((final_price / last_buy_price - 1) * 100, 2) if last_buy_price else 0
            amount = shares * final_price
            cash += amount - DataService._fee_sell(amount, commission, symbol)
            trades.append({
                "type": "close", "price": round(final_price, 2),
                "date": str(df.index[-1])[:10],
                "pnl": pnl_pct, "shares": round(shares, 0),
                "reason": "期末清仓",
            })
            shares = 0.0

        final_value = cash
        total_return = round((final_value / capital - 1) * 100, 2)
        years = max((df.index[-1] - df.index[0]).days / 365.25, 0.1)
        annual_return = round(((final_value / capital) ** (1 / years) - 1) * 100, 2)

        eq = pd.Series(equity)
        max_dd = round(((eq - eq.cummax()) / eq.cummax()).min() * 100, 2)

        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = sum(1 for t in trades if t["pnl"] < 0)
        total = wins + losses

        score = annual_return - 1.5 * abs(max_dd)
        if score >= 20:
            grade, grade_label = "A", "优秀"
        elif score >= 5:
            grade, grade_label = "B", "良好"
        elif score >= -5:
            grade, grade_label = "C", "一般"
        else:
            grade, grade_label = "D", "较差"

        return {
            "total_return": total_return,
            "annual_return": annual_return,
            "max_drawdown": max_dd,
            "win_rate": round(wins / total * 100, 1) if total else 0,
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "final_value": round(final_value, 2),
            "trades": trades[-30:],
            "equity": equity,
            "grade": grade,
            "grade_label": grade_label,
            "score": round(score, 2),
        }


# ============================================================================
# AI 模拟盘
# ============================================================================

class AiPaperTrader:
    """AI 信号模拟盘：按 AI 决策信号在模拟资金上买卖。

    规则（简单明确，可调）：
      - 初始资金 10 万
      - action ∈ {buy, add} 且 score ≥ 55  → 买入/加仓（等权买入）
      - action ∈ {sell, reduce} 且 score < 45 → 卖出该股
      - 价格取该股最新收盘价；双边成本 0.15%
    状态持久化到 ai_paper_state.json（模拟盘记录，重启保留）。
    """

    INIT_CASH = 100_000.0
    COST = 0.0015

    def __init__(self, state_file: str = "ai_paper_state.json"):
        self.state_file = Path(state_file)
        self.state = self._load()

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"cash": self.INIT_CASH, "positions": {}, "trades": []}

    def _save(self):
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def sync(self, signals: list[dict], price_fn) -> dict:
        """按 AI 信号更新模拟持仓。price_fn(symbol) -> 最新价。"""
        st = self.state
        cash = float(st["cash"])
        positions = st["positions"]
        trade_log = []

        for sig in signals:
            sym = sig["symbol"]
            action = sig["action"]
            score = float(sig["score"] or 0)
            price = price_fn(sym)
            if not price or price <= 0:
                continue

            # 买入
            if action in ("buy", "add") and score >= 55:
                qty = int(cash * 0.25 / (price * (1 + self.COST)))  # 每只约 25% 仓位
                qty = (qty // 100) * 100  # A股一手 = 100 股
                if qty > 0 and sym not in positions:
                    cost = qty * price * (1 + self.COST)
                    cash -= cost
                    positions[sym] = {"qty": qty, "avg_cost": price}
                    trade_log.append({
                        "symbol": sym, "side": "BUY", "price": round(price, 2),
                        "qty": qty, "cost": round(cost, 2),
                        "reason": f"AI {action} score={score:.0f}",
                    })
            # 卖出
            elif action in ("sell", "reduce") and score < 45:
                pos = positions.pop(sym, None)
                if pos:
                    revenue = pos["qty"] * price * (1 - self.COST)
                    cash += revenue
                    pnl = (price / pos["avg_cost"] - 1) * 100
                    trade_log.append({
                        "symbol": sym, "side": "SELL", "price": round(price, 2),
                        "qty": pos["qty"], "cost": round(revenue, 2),
                        "pnl_pct": round(pnl, 2),
                        "reason": f"AI {action} score={score:.0f}",
                    })

        st["cash"] = round(cash, 2)
        st["trades"] = (trade_log + st["trades"])[:200]
        self._save()
        return self.status()

    def status(self) -> dict:
        """当前模拟盘状态（持仓市值按最新价估算）。"""
        st = self.state
        cash = float(st["cash"])
        positions = st["positions"]
        mv = cash
        pos_list = []
        for sym, pos in positions.items():
            price = pos.get("last_price", pos["avg_cost"])
            val = pos["qty"] * price
            mv += val
            pos_list.append({
                "symbol": sym,
                "qty": pos["qty"],
                "avg_cost": round(pos["avg_cost"], 2),
                "last_price": round(price, 2),
                "value": round(val, 2),
                "pnl_pct": round((price / pos["avg_cost"] - 1) * 100, 2),
            })
        return {
            "cash": round(cash, 2),
            "market_value": round(mv, 2),
            "total": round(mv, 2),
            "pnl": round(mv - self.INIT_CASH, 2),
            "pnl_pct": round((mv / self.INIT_CASH - 1) * 100, 2),
            "positions": pos_list,
            "trades": st["trades"][:50],
            "initial": self.INIT_CASH,
        }

    def mark_prices(self, price_fn):
        """用最新价刷新持仓估值。"""
        for sym, pos in self.state["positions"].items():
            p = price_fn(sym)
            if p and p > 0:
                pos["last_price"] = p
        self._save()
        return self.status()


class LearnedSignalEngine:
    """独立学习的买卖信号引擎（不依赖 qtrade 内置策略库）。

    自研多维打分模型，综合趋势/动量/量能/位置四类因子：

    买入（全部必要条件 + 至少一项确认）：
      必要：① 趋势向上（收盘 > MA20 且 MA20 走升）
            ② 中期多头（收盘 > MA60）
            ③ 短动量回升（MA5 向上，回踩企稳信号）
            ④ 位置安全（收盘距 MA10 < 2.5%，不追高）
      确认：MACD 多头或柱体放大 / 放量（量 ≥ 0.9×5日均量）至少一项成立
      防线：RSI < 72（拒绝超买追入）
    卖出（任一成立）：
      趋势破位（收盘 < MA20）／ 短线走坏（收盘 < MA10 且 MACD 死叉）
      极端超买（RSI > 80）
    """

    @staticmethod
    def _rsi(close: pd.Series, n: int = 14) -> float:
        delta = close.diff().dropna()
        if len(delta) < n:
            return 50.0
        up = delta.clip(lower=0).rolling(n).mean().iloc[-1]
        dn = (-delta.clip(upper=0)).rolling(n).mean().iloc[-1]
        if dn == 0:
            return 100.0
        return float(100 - 100 / (1 + up / dn))

    @classmethod
    def analyze(cls, df: pd.DataFrame) -> dict | None:
        """输入日K DataFrame（open/high/low/close/volume），返回最新信号。"""
        if df is None or len(df) < 60:
            return None
        close = df["close"].astype(float)
        vol = df["volume"].astype(float)

        ma5 = close.rolling(5).mean()
        ma10 = close.rolling(10).mean()
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = (dif - dea) * 2
        vol_ma5 = vol.rolling(5).mean()
        rsi = cls._rsi(close)

        c = float(close.iloc[-1])
        checks = {
            # 买入：必要条件
            "trend_up": c > float(ma20.iloc[-1]) and float(ma20.iloc[-1]) > float(ma20.iloc[-4]),
            "above_ma60": c > float(ma60.iloc[-1]),
            "mom_up": float(ma5.iloc[-1]) > float(ma5.iloc[-2]),
            "pullback_ok": abs(c / float(ma10.iloc[-1]) - 1) < 0.025,  # 不过度偏离 MA10
            # 买入：确认条件（至少一项）
            "macd_ok": float(dif.iloc[-1]) > float(dea.iloc[-1]) or float(hist.iloc[-1]) > float(hist.iloc[-2]),
            "vol_ok": float(vol.iloc[-1]) >= float(vol_ma5.iloc[-1]) * 0.9,
            # 卖出条件
            "break_ma20": c < float(ma20.iloc[-1]),
            "short_break": c < float(ma10.iloc[-1]) and float(dif.iloc[-1]) < float(dea.iloc[-1]),
            "overbought": rsi > 80,
        }

        bull = [k for k in ("trend_up", "above_ma60", "mom_up", "pullback_ok") if checks[k]]
        confirm = checks["macd_ok"] or checks["vol_ok"]

        action, reason = "hold", ""
        if len(bull) == 4 and confirm and rsi < 72:
            action = "buy"
            reason = (f"趋势+动量+位置齐备"
                      f"{' +MACD' if checks['macd_ok'] else ''}{' +放量' if checks['vol_ok'] else ''} | RSI={rsi:.0f}")
        elif checks["break_ma20"]:
            action = "sell"
            reason = f"趋势破位：收盘跌破MA20 | RSI={rsi:.0f}"
        elif checks["short_break"]:
            action = "sell"
            reason = f"短线走坏：跌破MA10且MACD死叉 | RSI={rsi:.0f}"
        elif checks["overbought"]:
            action = "sell"
            reason = f"极端超买：RSI={rsi:.0f} > 80"

        return {
            "action": action,
            "strength": 1.0 if action == "buy" else (0.7 if action == "sell" else 0.0),
            "price": c,
            "rsi": round(rsi, 1),
            "reason": reason,
            "date": str(df.index[-1])[:10],
        }


class LearnedSignalEngineV2:
    """自研学习引擎 v2 —— 多策略融合版（不依赖内置策略库）。

    融合自 GitHub 高星项目与经典文献的九个概念：
      · ADX(14) 状态分层（Wilder）：ADX≥20 且 +DI>−DI 走趋势逻辑，否则走回归逻辑
      · TTM Squeeze 挤压释放（John Carter / pandas_ta）：布林收进肯特纳后放量释放突破
      · z 分数均值回归（Ernest Chan / Quantopian 档案）：z(20) < −1.8 超卖低吸
      · OBV 量能确认（Granville）：OBV > OBV-MA21，只买有吸筹迹象的回调
      · 防追高护栏（NostalgiaForInfinity 3.4k★ 设计）：5日涨幅 >8% / 偏离MA10 过远不追
      · 趋势锚（NFI ema200 思想日线化）：收盘须在 MA60 上方且 MA60 走升
      卖出侧（由交易器执行）：钱德利拖曳止损 / 时间止损 / 时间衰减止盈
    """

    Z_ENTRY = -1.8        # z 分数低吸阈值
    PUMP_GUARD = 0.08     # 5日涨幅护栏
    ADX_TREND = 20.0      # 趋势阈值（Wilder 灰区下沿）

    @staticmethod
    def _adx(df: pd.DataFrame, n: int = 14):
        """Wilder ADX：返回 (adx, +di, -di) 最新值。"""
        h, low, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        up, dn = h.diff(), -low.diff()
        plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
        minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
        tr = pd.concat([h - low, (h - c.shift()).abs(), (low - c.shift()).abs()], axis=1).max(axis=1)
        a = 1 / n
        atr = tr.ewm(alpha=a, adjust=False).mean()
        pdi = 100 * plus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
        mdi = 100 * minus_dm.ewm(alpha=a, adjust=False).mean() / atr.replace(0, np.nan)
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        adx = dx.ewm(alpha=a, adjust=False).mean()
        return float(adx.iloc[-1]), float(pdi.iloc[-1]), float(mdi.iloc[-1])

    @classmethod
    def analyze(cls, df: pd.DataFrame) -> dict | None:
        if df is None or len(df) < 80:
            return None
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["volume"].astype(float)
        c = float(close.iloc[-1])

        # ── 基础指标 ──
        ma5, ma10, ma20, ma60 = (close.rolling(w).mean() for w in (5, 10, 20, 60))
        mid, std = ma20, close.rolling(20).std()
        z20 = (close - mid) / std.replace(0, np.nan)
        prev_c = close.shift(1)
        tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
        atr22 = float(tr.ewm(alpha=1 / 22, adjust=False).mean().iloc[-1])
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        dif, dea = ema12 - ema26, (ema12 - ema26).ewm(span=9, adjust=False).mean()
        rsi = LearnedSignalEngine._rsi(close)
        adx, pdi, mdi = cls._adx(df)

        # ── TTM Squeeze：布林(20,2) 收进 肯特纳(20,1.5×TR通道) 后释放 ──
        bb_up, bb_lo = mid + 2 * std, mid - 2 * std
        kc_up, kc_lo = mid + 1.5 * tr.rolling(20).mean(), mid - 1.5 * tr.rolling(20).mean()
        sqz_on = (bb_lo > kc_lo) & (bb_up < kc_up)
        sqz_off = (bb_lo < kc_lo) & (bb_up > kc_up)
        # LazyBear 动量柱近似：收盘距 20日高低中值与中轨均值的偏离
        mom = close - ((high.rolling(20).max() + low.rolling(20).min()) / 2 + mid) / 2
        squeeze_fire = bool(sqz_off.iloc[-1] and mom.iloc[-1] > 0 and sqz_on.iloc[-2])

        # ── OBV 量能确认 ──
        obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
        obv_ok = bool(obv.iloc[-1] > obv.rolling(21).mean().iloc[-1])

        # ── 防追高护栏（NFI pump guard）──
        pump_5d = float(close.iloc[-1] / close.iloc[-6] - 1) if len(close) > 6 else 0.0
        not_pumped = pump_5d < cls.PUMP_GUARD
        near_ma10 = abs(c / float(ma10.iloc[-1]) - 1) < 0.03

        # ── 状态分层 ──
        regime_trend = adx >= cls.ADX_TREND and pdi > mdi
        trend_anchor = c > float(ma60.iloc[-1]) and float(ma60.iloc[-1]) >= float(ma60.iloc[-4])

        # 趋势模式入场：趋势确立 + 站上MA20 + 动量回升 + (挤压点火 或 均线多头) + 量能/位置护栏
        trend_buy = (
            regime_trend and trend_anchor
            and c > float(ma20.iloc[-1]) and float(ma20.iloc[-1]) > float(ma20.iloc[-4])
            and float(ma5.iloc[-1]) > float(ma5.iloc[-2])
            and (squeeze_fire or float(ma5.iloc[-1]) > float(ma10.iloc[-1]))
            and obv_ok and near_ma10 and not_pumped and rsi < 72
        )
        # 回归模式入场：超卖低吸（z<-1.8）+ 趋势锚 + OBV吸筹 + 防追高
        rev_buy = (
            float(z20.iloc[-1]) < cls.Z_ENTRY and trend_anchor
            and obv_ok and not_pumped and rsi < 45
        )

        # 卖出信号（价格类退出由交易器的止损/止盈/拖曳负责）
        sell_reason = None
        if c < float(ma20.iloc[-1]) and float(ma20.iloc[-4]) < float(ma20.iloc[-1]) * 0.995:
            sell_reason = f"趋势走坏：跌破MA20且MA20走平转弱 ADX={adx:.0f}"
        elif c < float(ma10.iloc[-1]) and float(dif.iloc[-1]) < float(dea.iloc[-1]) and not regime_trend:
            sell_reason = f"回归失败：跌破MA10且MACD死叉 z={float(z20.iloc[-1]):.1f}"
        elif rsi > 80:
            sell_reason = f"极端超买 RSI={rsi:.0f}"

        if trend_buy or rev_buy:
            # 综合评分：供组合层动量排名（ADX趋势强度 + 挤压 + z深度 + OBV + 动量）
            mom20 = c / float(close.iloc[-21]) - 1 if len(close) > 21 else 0.0
            score = (
                min(adx / 40, 1.0) * (0.5 if trend_buy else 0.2)
                + (0.25 if squeeze_fire else 0.0)
                + min(abs(float(z20.iloc[-1])) / 4, 1.0) * (0.4 if rev_buy else 0.0)
                + (0.15 if obv_ok else 0.0)
                + min(max(mom20, 0) / 0.25, 1.0) * 0.3
            )
            mode = "trend" if trend_buy else "revert"
            reason = (f"{'趋势突破' if trend_buy else '超卖低吸'}"
                      f"{' +挤压点火' if squeeze_fire else ''} +OBV吸筹"
                      f" | ADX={adx:.0f} z={float(z20.iloc[-1]):.1f} RSI={rsi:.0f}")
            return {"action": "buy", "strength": 1.0, "price": c, "rsi": round(rsi, 1),
                    "reason": reason, "date": str(df.index[-1])[:10],
                    "score": round(min(score, 1.0), 3), "mode": mode,
                    "atr": round(atr22, 3), "mom20": round(mom20, 4), "engine": "v2"}
        if sell_reason:
            return {"action": "sell", "strength": 0.7, "price": c, "rsi": round(rsi, 1),
                    "reason": sell_reason, "date": str(df.index[-1])[:10],
                    "score": 0.0, "mode": None, "atr": round(atr22, 3), "mom20": 0.0, "engine": "v2"}
        return {"action": "hold", "strength": 0.0, "price": c, "rsi": round(rsi, 1),
                "reason": "", "date": str(df.index[-1])[:10],
                "score": 0.0, "mode": None, "atr": round(atr22, 3), "mom20": 0.0, "engine": "v2"}


class SequoiaSignalEngine:
    """Sequoia-X 选股引擎（学习自 sngyai/Sequoia-X，GitHub 5,340★，A股自动选股系统）。

    移植其 V2 六策略中可量价计算的四类入场 + V1 防假突破细节：
      · 海龟突破（turtle_trade）：收盘破20日高点 + 阳线 + 收盘高于昨收 + 成交额≥1亿
      · 均线放量（ma_volume）：MA5 上穿 MA20 + 量 > 1.5×20日均量
      · 高而窄旗形（high_tight_flag）：40日振幅>60% + 近10日收紧(<15%) + 缩量(<0.6×均量)
      · 涨停洗盘（limitup_shakeout）：昨日涨停 + 今日阴线回踩 + 放量2倍 + 未破昨收
    卖出：连续两日跌破 MA20（原项目为收盘后筛选器，无卖出规则，此为配套出场）。
    """

    MIN_TURNOVER = 1e8   # 成交额下限（元）：流动性过滤

    @staticmethod
    def patterns(df: pd.DataFrame) -> list[str]:
        """检测 Sequoia-X 四类入场形态，返回命中的形态名列表（供融合策略复用）。"""
        close = df["close"].astype(float)
        open_ = df["open"].astype(float)
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        vol = df["volume"].astype(float)
        c, o = float(close.iloc[-1]), float(open_.iloc[-1])
        prev_c = float(close.iloc[-2])

        vol20 = vol.rolling(20).mean()
        turnover = vol * close  # 成交额近似：成交量×收盘价
        reasons = []

        # 1) 海龟突破（Sequoia turtle_trade：20日高点 + 阳线防假突破 + 流动性）
        hh20 = float(high.iloc[:-1].rolling(20).max().iloc[-1])
        if (c > hh20 and c > o and c > prev_c
                and float(turnover.iloc[-1]) >= SequoiaSignalEngine.MIN_TURNOVER):
            reasons.append("海龟突破20日高+阳线放量")

        # 2) 均线金叉放量（ma_volume）
        ma5, ma20 = close.rolling(5).mean(), close.rolling(20).mean()
        if (float(ma5.iloc[-2]) <= float(ma20.iloc[-2]) and float(ma5.iloc[-1]) > float(ma20.iloc[-1])
                and float(vol.iloc[-1]) > 1.5 * float(vol20.iloc[-1])):
            reasons.append("MA5上穿MA20+1.5倍量")

        # 3) 高而窄旗形（high_tight_flag：先涨60%后横盘收紧缩量）
        h40, l40 = float(high.iloc[-40:].max()), float(low.iloc[-40:].min())
        h10, l10 = float(high.iloc[-10:].max()), float(low.iloc[-10:].min())
        if (h40 / l40 > 1.6 and h10 / l10 < 1.15
                and l10 >= 0.8 * h40
                and float(vol.iloc[-1]) < 0.6 * float(vol20.iloc[-1])):
            reasons.append("高而窄旗形收紧缩量")

        # 4) 涨停洗盘（limitup_shakeout：昨日涨停今阴回踩不破）
        prev2_c = float(close.iloc[-3])
        if (prev_c >= prev2_c * 1.095 and c < o
                and float(vol.iloc[-1]) >= 2 * float(vol.iloc[-2])
                and float(low.iloc[-1]) >= prev_c):
            reasons.append("涨停洗盘回踩确认")
        return reasons

    @staticmethod
    def analyze(df: pd.DataFrame) -> dict | None:
        if df is None or len(df) < 70:
            return None
        close = df["close"].astype(float)
        c = float(close.iloc[-1])
        ma20 = close.rolling(20).mean()
        reasons = SequoiaSignalEngine.patterns(df)
        sell = (c < float(ma20.iloc[-1]) and float(close.iloc[-2]) < float(ma20.iloc[-2]))

        if reasons:
            return {"action": "buy", "strength": min(1.0, 0.5 + 0.15 * len(reasons)),
                    "price": c, "rsi": None,
                    "reason": "Sequoia:" + " / ".join(reasons),
                    "date": str(df.index[-1])[:10], "engine": "sequoia"}
        if sell:
            return {"action": "sell", "strength": 0.7, "price": c, "rsi": None,
                    "reason": "Sequoia卖出：连续两日跌破MA20", "date": str(df.index[-1])[:10],
                    "engine": "sequoia"}
        return {"action": "hold", "strength": 0.0, "price": c, "rsi": None,
                "reason": "", "date": str(df.index[-1])[:10], "engine": "sequoia"}


class OneilSignalEngine:
    """欧奈尔 CANSLIM 精简版（量价可计算子集）。

    七要素中 C(当季EPS)/A(年度EPS)/I(机构持仓) 需基本面数据，此处实现其余全部：
      · N 新高+基底突破：距52周新高≤15%，杯柄/平台基底（12-30%回调后企稳），
        收盘上穿枢轴（近10日高点）且不追高（≤枢轴+5%），放量≥1.3×50日均量
      · L 相对强度：120日收益在全市场百分位 ≥ 85（IBD RS 80-90 的 A 股代理，
        Sequoia-X 的 RpsBreakout 同款思想）
      · M 大盘方向：市场宽度（成分股站上MA20占比）≥ 40% 才开新仓
      · 卖出铁律：买入价−7.5% 无条件止损；+20% 止盈；突破后15日内涨满20%
        → 触发八周持股规则（40个交易日内不落袋，让利润奔跑）
    """

    RS_MIN = 85          # 120日收益百分位门槛
    BREADTH_MIN = 0.40   # 市场宽度门槛（站上MA20占比）
    STOP_PCT = 0.075     # 欧奈尔铁律：7-8% 止损
    TAKE_PCT = 0.20      # 强势市场止盈 20-25%

    @classmethod
    def gates(cls, df: pd.DataFrame, rs_pct: float, breadth: float,
              rs_min: float | None = None, near_high_max: float = 0.15) -> tuple[bool, str]:
        """欧奈尔质量闸门（供融合策略复用）：M大盘 / L强度 / N新高 / MA50趋势。

        返回 (是否通过, 未通过原因)。rs_min=None 时用类默认 RS_MIN。
        """
        rs_min = cls.RS_MIN if rs_min is None else rs_min
        close = df["close"].astype(float)
        c = float(close.iloc[-1])
        ma50 = close.rolling(50).mean()
        max250 = float(close.iloc[-250:].max()) if len(close) >= 250 else float(close.max())
        if breadth < cls.BREADTH_MIN:
            return False, f"大盘弱(宽度{breadth*100:.0f}%)"
        if rs_pct < rs_min:
            return False, f"强度不足(RPS{rs_pct:.0f}<{rs_min:.0f})"
        if c < (1 - near_high_max) * max250:
            return False, f"距52周高过远({(c/max250-1)*100:+.0f}%)"
        if not (c > float(ma50.iloc[-1]) and float(ma50.iloc[-1]) >= float(ma50.iloc[-5]) * 0.998):
            return False, "MA50趋势不佳"
        return True, f"RPS{rs_pct:.0f} 距高点{(c/max250-1)*100:+.0f}%"

    @classmethod
    def analyze(cls, df: pd.DataFrame, rs_pct: float = 50.0, breadth: float = 1.0) -> dict | None:
        """rs_pct/breadth 由组合层（AutoPaperTrader.cycle）跨股票计算后传入。"""
        if df is None or len(df) < 130:
            return None
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        vol = df["volume"].astype(float)
        c = float(close.iloc[-1])

        ma50 = close.rolling(50).mean()
        vol50 = vol.rolling(50).mean()

        # N-1: 距 52 周新高 ≤ 15%
        max250 = float(close.iloc[-250:].max()) if len(close) >= 250 else float(close.max())
        near_high = c >= 0.85 * max250

        # N-2: 基底形态（近60日回调 12-30% 后企稳，或 ≥25日收在下半区之上的浅平台）
        base_win = close.iloc[-60:]
        base_high, base_low = float(base_win.max()), float(base_win.min())
        depth = (base_high - base_low) / base_high
        base_ok = 0.12 <= depth <= 0.30 or (
            0.05 <= depth < 0.15
            and int((base_win >= base_low + 0.8 * (base_high - base_low)).sum()) >= 25)

        # N-3: 枢轴突破（近10日高点 = 杯柄把手高点），放量确认，不追高
        pivot = float(high.iloc[-10:].max())
        pivot_break = (c >= pivot and c <= pivot * 1.05
                       and float(vol.iloc[-1]) >= 1.3 * float(vol50.iloc[-1]))

        # 趋势与位置：站上走平/上升的 MA50
        trend_ok = c > float(ma50.iloc[-1]) and float(ma50.iloc[-1]) >= float(ma50.iloc[-5]) * 0.998

        buy = (near_high and base_ok and pivot_break and trend_ok
               and rs_pct >= cls.RS_MIN and breadth >= cls.BREADTH_MIN)
        sell = c < float(ma50.iloc[-1]) and float(ma50.iloc[-5]) > float(ma50.iloc[-1]) * 1.002

        if buy:
            return {"action": "buy", "strength": 1.0, "price": c, "rsi": None,
                    "reason": f"CANSLIM: 距52周高{(c/max250-1)*100:+.0f}% 基地回调{depth*100:.0f}% "
                              f"枢轴突破 RPS{rs_pct:.0f} 宽度{breadth*100:.0f}%",
                    "date": str(df.index[-1])[:10], "engine": "oneil",
                    "stop_pct": cls.STOP_PCT, "take_pct": cls.TAKE_PCT}
        if sell:
            return {"action": "sell", "strength": 0.7, "price": c, "rsi": None,
                    "reason": "CANSLIM卖出：跌破MA50且走弱", "date": str(df.index[-1])[:10],
                    "engine": "oneil"}
        return {"action": "hold", "strength": 0.0, "price": c, "rsi": None,
                "reason": "", "date": str(df.index[-1])[:10], "engine": "oneil"}


class SequoiaOneilEngine:
    """红杉×欧奈尔融合策略 —— 主力专用策略。

    设计哲学：欧奈尔管纪律（能不能买），Sequoia-X 管扳机（什么时候扣）。
      ① 欧奈尔闸门（全部通过才允许开仓）：
         M 大盘方向：市场宽度 ≥ 40%（弱市不开新仓）
         L 相对强度：120日收益全市场百分位 RPS ≥ 85（只买领头羊）
         N 新高原则：收盘距 52 周新高 ≤ 15%（不买半山腰）
         趋势底线：站上走平/上升的 MA50
      ② Sequoia-X 扳机（形态触发，任一命中）：
         海龟突破（阳线防假突破+成交额≥1亿）/ MA5-MA20 金叉放量 /
         高而窄旗形收紧缩量 / 涨停洗盘回踩确认
      ③ 欧奈尔入场质量：收盘距枢轴（近10日高点）≤ 3% —— 只在枢轴边缘买，绝不追高
      ④ 欧奈尔卖出铁律（交易器执行）：
         止损 7.5% 无条件 / 止盈 20% / 八周持股规则（15日内涨满20%→40日不落袋）
      ⑤ 信号卖出：连续两日跌破 MA20（Sequoia 出场规则）
      回测（20股×250日弱市样本）：枢轴规则使 -2.5%→+0.6%，胜率 17%→25%，回撤 -6.6%→-6.1%
    """

    RS_MIN = 85          # RPS 门槛（与纯欧奈尔一致）
    PIVOT_EDGE = 0.03    # 枢轴边缘：收盘 ≥ 枢轴 × (1-3%)，只在突破点附近买

    @staticmethod
    def analyze(df: pd.DataFrame, rs_pct: float = 50.0, breadth: float = 1.0) -> dict | None:
        if df is None or len(df) < 130:
            return None
        close = df["close"].astype(float)
        high = df["high"].astype(float)
        c = float(close.iloc[-1])
        ma20 = close.rolling(20).mean()

        sell = (c < float(ma20.iloc[-1]) and float(close.iloc[-2]) < float(ma20.iloc[-2]))
        triggers = SequoiaSignalEngine.patterns(df)

        if triggers:
            ok, why = OneilSignalEngine.gates(df, rs_pct, breadth, rs_min=SequoiaOneilEngine.RS_MIN)
            pivot = float(high.iloc[-10:].max())
            at_pivot = c >= pivot * (1 - SequoiaOneilEngine.PIVOT_EDGE)
            if ok and at_pivot:
                return {"action": "buy", "strength": 1.0, "price": c, "rsi": None,
                        "reason": f"红杉×欧奈尔: {' / '.join(triggers)} | 闸门通过 {why}",
                        "date": str(df.index[-1])[:10], "engine": "fusion",
                        "stop_pct": OneilSignalEngine.STOP_PCT, "take_pct": OneilSignalEngine.TAKE_PCT}
        if sell:
            return {"action": "sell", "strength": 0.7, "price": c, "rsi": None,
                    "reason": "红杉×欧奈尔卖出：连续两日跌破MA20", "date": str(df.index[-1])[:10],
                    "engine": "fusion"}
        return {"action": "hold", "strength": 0.0, "price": c, "rsi": None,
                "reason": "", "date": str(df.index[-1])[:10], "engine": "fusion"}


class EngineLock:
    """跨进程引擎锁：同一账户同一时刻只允许一个进程执行自动交易。

    通过 OS 级文件字节锁实现（Windows msvcrt / POSIX fcntl），
    持锁进程退出/崩溃时锁自动释放，无需手动清理。
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self._fh = None

    def acquired(self) -> bool:
        """尝试持锁；一旦持有（进程存活期间）始终返回 True。"""
        if self._fh is not None:
            return True
        try:
            self._fh = open(self.path, "a+")
            self._fh.seek(0)
            self._fh.write("0")   # 确保首字节存在，供锁定
            self._fh.flush()
            self._fh.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except (ImportError, OSError):
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass
            self._fh = None
            return False

    def release(self):
        if self._fh is not None:
            try:
                self._fh.close()  # 关闭句柄即释放锁
            except OSError:
                pass
            self._fh = None


class AutoPaperTrader:
    """自动模拟盘：独立信号引擎 + 后台线程自动买卖 + 止盈止损 + 持久化。

    - 买入：信号 buy 且未持仓、仓位未满 → 按 20% 总资产买入（整手）
      同时记录买入价、预期卖出价（止盈价 +TAKE_PROFIT）、止损价
    - 卖出：信号 sell，或现价触及预期卖出价（止盈）/止损价 → 自动卖出
    - 状态持久化到 auto_paper_state.json，重启保留
    """

    INIT_CASH = 100_000.0
    COST = 0.0015          # 双边费率
    MAX_POSITIONS = 8      # 最大持仓数
    POS_RATIO = 0.20       # 单只仓位占总资产比例
    TAKE_PROFIT = 0.12     # 预期卖出价 = 买入价 × (1 + 12%)
    STOP_LOSS = 0.06       # 止损 = 买入价 × (1 - 6%)
    CYCLE_SECONDS = 60     # 自动交易轮询间隔
    UNIVERSE_LIMIT = None  # 股票池：全主板扫描（沪60/深00），不再限制 60 只

    # 信号源：sequoia_oneil=红杉×欧奈尔融合（主力专用，默认）；
    # 其余保留可切换：learned_v2 多策略融合 / oneil 纯欧奈尔 / sequoia 纯红杉 / 经典策略
    SIGNAL_MODES = {
        "sequoia_oneil": "红杉×欧奈尔融合(主力)",
        "learned_v2": "自研引擎v2(多策略融合)",
        "learned": "自研学习引擎v1",
        "oneil": "欧奈尔CANSLIM精简版",
        "sequoia": "Sequoia-X选股(5.3k★)",
        "turtle": "海龟交易法(vnpy)",
        "supertrend": "SuperTrend",
        "dual_thrust": "Dual Thrust",
        "boll_reversion": "布林均值回归",
    }
    # v2 组合层参数：每轮最多新开仓数 / 拖曳止损倍数 / 时间止损天数 / 衰减止盈
    V2_BUYS_PER_CYCLE = 2
    V2_CHANDELIER = 3.0      # 钱德利退出：峰值收盘 − 3×ATR(22)
    V2_TIME_STOP = 12        # 持有超12个交易日仍未盈利 → 时间止损
    V2_DECAY_TP_BARS = 15    # 持有超15个交易日且浮盈≥4% → 衰减止盈落袋
    V2_DECAY_TP_PCT = 0.04
    # 欧奈尔八周持股规则：15日内涨满 20% → 40个交易日内不落袋
    ONEIL_HOLD_TRIGGER = 0.20
    # v2 市场宽度闸门（O'Neil M 要素）：宽度低于阈值时不开新仓（回测 +3.5%→+5.0%，回撤 -7.2%→-5.8%）
    V2_BREADTH_GATE = 0.35

    def __init__(self, state_file: str = "auto_paper_state.json"):
        self.state_file = Path(state_file)
        self.lock = threading.RLock()
        self.engine_lock = EngineLock(str(self.state_file) + ".engine.lock")
        self.state = self._load()
        self.state.setdefault("signal_mode", "sequoia_oneil")

    # ---------- 持久化 ----------

    def _load(self) -> dict:
        if self.state_file.exists():
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "cash": self.INIT_CASH, "positions": {}, "trades": [],
            "equity_hist": [], "running": True, "last_run": None,
            "last_error": None, "_sig_date": {}, "signal_mode": "sequoia_oneil",
        }

    def _save(self):
        """原子写入：先写临时文件再替换，保证其他进程读取时不读到半截。"""
        st = dict(self.state)
        st["_sig_date"] = self.state.get("_sig_date", {})
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_file)

    # ---------- 交易动作 ----------

    def _trade(self, side: str, sym: str, qty: int, price: float, reason: str, pos: dict | None):
        st = self.state
        cash = float(st["cash"])
        rec = {"symbol": sym, "side": side, "price": round(price, 2), "qty": qty,
               "time": time.strftime("%Y-%m-%d %H:%M:%S"), "reason": reason}
        if side == "BUY":
            cost = qty * price * (1 + self.COST)
            cash -= cost
            rec["cost"] = round(cost, 2)
        else:
            revenue = qty * price * (1 - self.COST)
            cash += revenue
            rec["revenue"] = round(revenue, 2)
            if pos:
                pnl_pct = (price / pos["buy_price"] - 1) * 100
                rec["pnl_pct"] = round(pnl_pct, 2)
                rec["pnl"] = round((price - pos["buy_price"]) * qty, 2)
        st["cash"] = round(cash, 2)
        st["trades"] = ([rec] + st["trades"])[:1000]

    def _buy(self, sym: str, price: float, sig: dict):
        st = self.state
        total = float(st["cash"]) + sum(p["qty"] * p.get("last_price", p["buy_price"]) for p in st["positions"].values())
        budget = total * self.POS_RATIO
        qty = int(budget / (price * (1 + self.COST))) // 100 * 100
        if qty < 100 or price * qty * (1 + self.COST) > float(st["cash"]):
            return
        pos = {
            "qty": qty,
            "buy_price": round(price, 2),
            "avg_cost": round(price, 2),
            # 预期卖出价（止盈）与止损：默认 12%/-6%，信号可携带专属规则（如欧奈尔 20%/-7.5%）
            "target_price": round(price * (1 + sig.get("take_pct", self.TAKE_PROFIT)), 2),
            "stop_price": round(price * (1 - sig.get("stop_pct", self.STOP_LOSS)), 2),
            "buy_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "buy_date": sig.get("date") or time.strftime("%Y-%m-%d"),
            "buy_reason": sig["reason"],
            "last_price": round(price, 2),
        }
        if sig.get("engine") == "v2":
            # v2 专属：入场模式 / ATR / 持有天数与峰值（拖曳止损原料）
            pos.update({"mode": sig.get("mode"), "atr": sig.get("atr", 0),
                        "bars": 0, "last_date": sig.get("date"), "peak_close": round(price, 2)})
        if sig.get("engine") == "oneil" or sig.get("engine") == "fusion":
            # 欧奈尔/融合专属：持有天数（八周持股规则原料，hold_until: None未触发/0期满）
            pos.update({"bars": 0, "last_date": sig.get("date"), "hold_until": None})
        st["positions"][sym] = pos
        self._trade("BUY", sym, qty, price, sig["reason"], None)

    def _sell(self, sym: str, price: float, reason: str):
        pos = self.state["positions"].pop(sym, None)
        if pos:
            self._trade("SELL", sym, pos["qty"], price, reason, pos)

    # ---------- 自动交易主循环 ----------

    @staticmethod
    def _mainboard_scan(service, symbols: list[str], rs_min: float) -> tuple[dict, float, list[str]]:
        """主板全市场扫描：全主板 RPS(120日)百分位 + 市场宽度 + 便宜预筛。

        只读本地历史缓存，numpy 向量化快筛（~毫秒级/千只），
        绝不触发实时拉取。预筛条件与欧奈尔闸门同参（距52周高≤15%、MA50趋势、RPS），
        幸存者再交完整引擎精析。返回 (rs_map, breadth, 候选列表)。
        """
        closes, highs, lows, opens, vols, rets, above = {}, {}, {}, {}, {}, {}, []
        for sym in symbols:
            d = service.load_history(sym)
            if d is None or len(d) < 130:
                continue
            closes[sym] = d["close"].values.astype(float)
            highs[sym] = d["high"].values.astype(float)
            lows[sym] = d["low"].values.astype(float)
            opens[sym] = d["open"].values.astype(float)
            vols[sym] = d["volume"].values.astype(float)
            arr = closes[sym]
            rets[sym] = arr[-1] / arr[-121] - 1 if len(arr) > 121 else 0.0
            if arr[-1] > arr[-20:].mean():
                above.append(sym)
        if not rets:
            return {}, 1.0, []
        import bisect
        ordered = sorted(rets.values())
        n = len(ordered)
        rs_map = {s: (bisect.bisect_left(ordered, r) / n) * 100 for s, r in rets.items()}
        breadth = len(above) / len(rets)

        def _fast_filter(sym: str) -> bool:
            """numpy 快筛：欧奈尔三关 + Sequoia 形态当天命中 + 枢轴边缘（引擎语义的快速超集）。"""
            arr = closes[sym]
            last = arr[-1]
            max250 = arr[-250:].max() if len(arr) >= 250 else arr.max()
            if last < 0.85 * max250:
                return False
            ma50, ma50_prev = arr[-50:].mean(), arr[-54:-49].mean()
            if not (last > ma50 and ma50 >= ma50_prev * 0.998):
                return False
            if rs_map[sym] < rs_min:
                return False
            # 高点枢轴边缘（近10日高点 × 0.97）
            d = highs[sym]
            if last < d[-10:].max() * 0.97:
                return False
            # Sequoia 四形态之一当天命中（与 patterns() 同参数的 numpy 版）
            o, h, low, v, c = opens[sym], highs[sym], lows[sym], vols[sym], arr
            vol20 = v[-20:].mean()
            turtle = (c[-1] > h[-21:-1].max() and c[-1] > o[-1] and c[-1] > c[-2]
                      and v[-1] * c[-1] >= SequoiaSignalEngine.MIN_TURNOVER)
            ma5_now, ma5_prev = c[-5:].mean(), c[-6:-1].mean()
            ma20_now, ma20_prev = c[-20:].mean(), c[-21:-1].mean()
            goldcross = (ma5_prev <= ma20_prev and ma5_now > ma20_now
                         and v[-1] > 1.5 * vol20)
            h40, l40 = h[-40:].max(), low[-40:].min()
            h10, l10 = h[-10:].max(), low[-10:].min()
            htf = (h40 / l40 > 1.6 and h10 / l10 < 1.15 and l10 >= 0.8 * h40
                   and v[-1] < 0.6 * vol20)
            shakeout = (c[-2] >= c[-3] * 1.095 and c[-1] < o[-1]
                        and v[-1] >= 2 * v[-2] and low[-1] >= c[-2])
            return turtle or goldcross or htf or shakeout

        candidates = [s for s in closes if _fast_filter(s)]
        return rs_map, breadth, candidates

    def _signal_for(self, df, rs_pct: float = 50.0, breadth: float = 1.0) -> dict | None:
        """按当前信号源计算某股最新信号。rs_pct/breadth 供欧奈尔 L/M 要素使用。"""
        mode = self.state.get("signal_mode", "sequoia_oneil")
        if mode == "learned":
            return LearnedSignalEngine.analyze(df)
        if mode == "learned_v2":
            return LearnedSignalEngineV2.analyze(df)
        if mode == "sequoia":
            return SequoiaSignalEngine.analyze(df)
        if mode == "oneil":
            return OneilSignalEngine.analyze(df, rs_pct=rs_pct, breadth=breadth)
        if mode == "sequoia_oneil":
            return SequoiaOneilEngine.analyze(df, rs_pct=rs_pct, breadth=breadth)
        # 经典策略：走 server 回测同一套信号生成，取最后一根K线的信号
        sig = DataService._generate_signals(df, mode)
        if sig is None or len(sig) == 0:
            return None
        last = int(sig.iloc[-1])
        action = "buy" if last == 1 else ("sell" if last == -1 else "hold")
        return {
            "action": action,
            "strength": 1.0 if action != "hold" else 0.0,
            "price": float(df["close"].iloc[-1]),
            "rsi": None,
            "reason": f"{self.SIGNAL_MODES.get(mode, mode)} 信号" if action != "hold" else "",
            "date": str(df.index[-1])[:10],
        }

    def cycle(self, service) -> dict:
        """执行一轮：估值 → 止盈止损/信号卖出 → 信号买入。

        多进程共存（后台服务 + 桌面窗口）时，只有拿到引擎锁的进程真正交易；
        其余进程只读状态。每轮先从磁盘热加载，保证 UI 端的重置/暂停/切信号源即时生效。
        """
        with self.lock:
            self.state = self._load()
            if not self.engine_lock.acquired():
                return self.status()   # 非持锁进程：只读最新状态
            st = self.state
            if not st.get("running", True):
                return self.status()   # 已暂停：不交易只报状态
            st["last_error"] = None
            try:
                sig_dates = st.setdefault("_sig_date", {})
                mode = st.get("signal_mode", "sequoia_oneil")
                is_v2 = mode == "learned_v2"
                is_oneil = mode == "oneil"
                is_fusion = mode == "sequoia_oneil"

                rs_map, breadth = {}, 1.0
                candidate_symbols = []
                # 股票池统一为全主板（沪60/深00），不再截断前 60 只
                mainboard = service.mainboard_symbols()
                st["_universe_n"] = len(mainboard)
                if is_fusion or is_oneil:
                    # 主板全市场：RS百分位与宽度都用全主板口径；
                    # 便宜预筛（新高/MA50/RPS）后只对幸存者做完整分析
                    rs_map, breadth, symbols = self._mainboard_scan(
                        service, mainboard,
                        rs_min=SequoiaOneilEngine.RS_MIN if is_fusion else OneilSignalEngine.RS_MIN)
                    candidate_symbols = list(symbols)
                else:
                    symbols = mainboard
                    if is_v2:
                        # v2 市场宽度闸门（借自 O'Neil M 要素）：全主板口径计算
                        above = []
                        for sym in symbols:
                            d = service.load_history(sym)
                            if d is None or len(d) < 25:
                                continue
                            c = d["close"].astype(float)
                            above.append(float(c.iloc[-1]) > float(c.rolling(20).mean().iloc[-1]))
                        if above:
                            breadth = sum(above) / len(above)

                # 1) 估值 + 止盈止损/拖曳止损/时间退出/信号卖出
                for sym, pos in list(st["positions"].items()):
                    df = service._resolve_df(sym)
                    sig = self._signal_for(df, rs_pct=rs_map.get(sym, 50.0), breadth=breadth)
                    price = sig["price"] if sig else (pos.get("last_price") or pos["buy_price"])
                    entry = pos["buy_price"]
                    last = pos.get("last_price") or entry
                    price_anomaly = False
                    # 主板单日涨跌停 ±10%，这里用 ±20% 做数据异常保护，
                    # 防止实时源返回错价导致“刚买就莫名止损/卖出”
                    if last > 0 and abs(price / last - 1) > 0.20:
                        st["last_error"] = f"{sym} 价格异常 {last:.2f}->{price:.2f}，跳过该持仓本轮"
                        price = last
                        price_anomaly = True
                    pos["last_price"] = round(price, 2)
                    pnl = price / entry - 1

                    # A股 T+1：当日买入不可卖出（含止损/止盈/信号卖出）
                    cur_date = (sig or {}).get("date")
                    if not cur_date and df is not None and len(df) > 0:
                        cur_date = str(df.index[-1])[:10]
                    buy_date = pos.get("buy_date") or str(pos.get("buy_time", ""))[:10]
                    if buy_date and cur_date and buy_date == cur_date:
                        continue

                    if is_v2 and "bars" in pos:
                        # 新交易日 +1；峰值收盘更新；钱德利拖曳止损只升不降
                        if sig and sig.get("date") and sig["date"] != pos.get("last_date"):
                            pos["bars"] = pos.get("bars", 0) + 1
                            pos["last_date"] = sig["date"]
                        pos["peak_close"] = max(pos.get("peak_close", price), price)
                        atr = pos.get("atr") or 0
                        if atr > 0:
                            chandelier = pos["peak_close"] - self.V2_CHANDELIER * atr
                            if chandelier > pos["stop_price"]:
                                pos["stop_price"] = round(chandelier, 2)
                        # 时间衰减止盈：持仓久、有浮盈 → 降低目标落袋
                        if pos.get("bars", 0) >= self.V2_DECAY_TP_BARS and pnl >= self.V2_DECAY_TP_PCT:
                            pos["target_price"] = round(price, 2)

                    if (is_oneil or is_fusion) and "bars" in pos:
                        # 欧奈尔八周持股规则：15日内涨满20% → 40日内不落袋（利润奔跑）
                        if sig and sig.get("date") and sig["date"] != pos.get("last_date"):
                            pos["bars"] = pos.get("bars", 0) + 1
                            pos["last_date"] = sig["date"]
                        if (not pos.get("hold_until") and pos.get("bars", 0) <= 15
                                and pnl >= self.ONEIL_HOLD_TRIGGER):
                            pos["hold_until"] = pos["bars"] + 40
                        if pos.get("hold_until") and pos["bars"] < pos["hold_until"]:
                            pos["target_price"] = round(entry * 10, 2)  # 持股期内不设止盈
                        elif pos.get("hold_until") and pos["bars"] >= pos["hold_until"]:
                            pos["target_price"] = round(price, 2)      # 期满落袋
                            pos["hold_until"] = 0

                    if price >= pos["target_price"]:
                        why = "八周持股期满落袋" if (is_oneil or is_fusion) and pos.get("hold_until") == 0 and "bars" in pos else \
                              f"止盈：触及预期卖出价 {pos['target_price']}"
                        self._sell(sym, price, why)
                        continue
                    if price <= pos["stop_price"]:
                        why = "止损：跌破拖曳止损线" if is_v2 and pos.get("peak_close", 0) > entry * 1.05 else \
                              f"止损：跌破 {pos['stop_price']}"
                        self._sell(sym, price, why)
                        continue
                    if is_v2 and pos.get("bars", 0) >= self.V2_TIME_STOP and pnl < 0:
                        self._sell(sym, price, f"时间止损：持有{pos['bars']}日未盈利")
                        continue
                    if sig and not price_anomaly and sig["action"] == "sell" and sig["date"] != sig_dates.get(sym):
                        self._sell(sym, price, f"信号卖出：{sig['reason']}")

                # 2) 信号买入（同一根日K只处理一次；v2 按评分排名+宽度闸门，每轮只买最优的 N 只）
                if is_v2:
                    candidates = []
                    gate_ok = breadth >= self.V2_BREADTH_GATE
                    for sym in symbols:
                        if sym in st["positions"] or len(st["positions"]) + len(candidates) >= self.MAX_POSITIONS:
                            continue
                        if not service.is_tradable(sym):
                            continue
                        df = service.load_history(sym)
                        sig = self._signal_for(df)
                        if not sig or sig["date"] == sig_dates.get(sym):
                            continue
                        if sig["action"] == "buy":
                            candidates.append((sym, sig))
                            candidate_symbols.append(sym)
                    candidates.sort(key=lambda x: -(x[1].get("score") or 0))
                    if not gate_ok:
                        candidates = []  # 市场宽度不足（弱市）：本轮不开新仓，持仓退出规则照常
                    for sym, sig in candidates[:self.V2_BUYS_PER_CYCLE]:
                        sig_dates[sym] = sig["date"]
                        self._buy(sym, sig["price"], sig)
                        if len(st["positions"]) >= self.MAX_POSITIONS:
                            break
                else:
                    # 弱市短路：宽度不达标时欧奈尔/融合引擎必然拒绝所有新仓，跳过精析
                    breadth_ok = breadth >= OneilSignalEngine.BREADTH_MIN
                    for sym in symbols:
                        if len(st["positions"]) >= self.MAX_POSITIONS:
                            break
                        if sym in st["positions"]:
                            continue
                        if not service.is_tradable(sym):
                            continue
                        if (is_fusion or is_oneil) and not breadth_ok:
                            break  # 全市场禁止开仓，无需逐只分析
                        # 全主板扫描统一用本地CSV日K（避免实时拉取几千只）
                        df = service.load_history(sym)
                        sig = self._signal_for(df, rs_pct=rs_map.get(sym, 50.0), breadth=breadth)
                        if not sig:
                            continue
                        if sig["date"] == sig_dates.get(sym):
                            continue
                        sig_dates[sym] = sig["date"]
                        if sig["action"] == "buy":
                            candidate_symbols.append(sym)
                            self._buy(sym, sig["price"], sig)

                # 3) 资金曲线
                service.set_candidate_symbols(candidate_symbols)
                status = self.status(service)
                st["equity_hist"] = (st["equity_hist"] + [
                    {"time": time.strftime("%Y-%m-%d %H:%M"), "total": status["total"]}
                ])[-500:]
                st["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save()
                return status
            except Exception as e:
                st["last_error"] = str(e)
                self._save()
                return self.status()

    # ---------- 状态 ----------

    def status(self, service=None) -> dict:
        with self.lock:
            if not self.engine_lock.acquired():
                # 其他进程（通常是后台交易服务）在驱动交易，读磁盘最新状态
                self.state = self._load()
            st = self.state
            cash = float(st["cash"])
            positions = st["positions"]
            mv = cash
            pos_list = []
            for sym, pos in positions.items():
                price = pos.get("last_price", pos["buy_price"])
                # 查看状态时顺带刷新持仓现价（最多 8 只，开销很小），让界面接近实时
                if service is not None:
                    try:
                        df = service._resolve_df(sym)
                        if df is not None and len(df) > 0:
                            live = float(df["close"].astype(float).iloc[-1])
                            last = pos.get("last_price") or pos["buy_price"]
                            # 同样做 ±20% 异常保护，避免界面显示错价
                            if last <= 0 or abs(live / last - 1) <= 0.20:
                                price = live
                    except Exception:
                        pass
                val = pos["qty"] * price
                mv += val
                pos_list.append({
                    "symbol": sym,
                    "qty": pos["qty"],
                    "buy_price": pos["buy_price"],            # 买入价
                    "avg_cost": pos["avg_cost"],
                    "target_price": pos["target_price"],      # 预期卖出价（止盈）
                    "stop_price": pos["stop_price"],          # 止损价
                    "last_price": round(price, 2),
                    "value": round(val, 2),
                    "pnl": round((price - pos["buy_price"]) * pos["qty"], 2),
                    "pnl_pct": round((price / pos["buy_price"] - 1) * 100, 2),
                    "target_pct": self.TAKE_PROFIT * 100,
                    "stop_pct": -self.STOP_LOSS * 100,
                    "buy_time": pos.get("buy_time", ""),
                    "buy_reason": pos.get("buy_reason", ""),
                })
            pos_list.sort(key=lambda p: -p["value"])
            summary = getattr(service, "universe_summary", None) if service is not None else None
            if summary is None:
                summary = {
                    "total": st.get("_universe_n", 0), "computable": 0, "tradable": 0,
                    "candidate": 0, "excluded_by_reason": {}, "as_of": None, "source": "unknown",
                }
            return {
                "cash": round(cash, 2),
                "market_value": round(mv - cash, 2),
                "total": round(mv, 2),
                "pnl": round(mv - self.INIT_CASH, 2),
                "pnl_pct": round((mv / self.INIT_CASH - 1) * 100, 2),
                "initial": self.INIT_CASH,
                "positions": pos_list,
                "position_count": len(pos_list),
                "max_positions": self.MAX_POSITIONS,
                "trades": st["trades"][:200],
                "equity_hist": st["equity_hist"][-120:],
                "running": st.get("running", True),
                "last_run": st.get("last_run"),
                "last_error": st.get("last_error"),
                "cycle_seconds": self.CYCLE_SECONDS,
                "signal_mode": st.get("signal_mode", "sequoia_oneil"),
                "signal_mode_label": self.SIGNAL_MODES.get(st.get("signal_mode", "sequoia_oneil"), "sequoia_oneil"),
                "signal_modes": [{"mode": k, "label": v} for k, v in self.SIGNAL_MODES.items()],
                "engine_owner": self.engine_lock.acquired(),
                "universe_size": st.get("_universe_n", 0),
                "universe_summary": summary,
                "rules": {
                    "pos_ratio": self.POS_RATIO, "take_profit": self.TAKE_PROFIT,
                    "stop_loss": self.STOP_LOSS, "cost": self.COST,
                },
            }

    def toggle(self, service=None) -> dict:
        with self.lock:
            self.state = self._load()
            self.state["running"] = not self.state.get("running", True)
            self._save()
            return self.status(service)

    def set_mode(self, mode: str, service=None) -> dict:
        """切换信号源：learned / turtle / supertrend / dual_thrust / boll_reversion。"""
        with self.lock:
            if mode not in self.SIGNAL_MODES:
                raise ValueError(f"未知信号源: {mode}，可选: {', '.join(self.SIGNAL_MODES)}")
            self.state = self._load()
            self.state["signal_mode"] = mode
            self.state["_sig_date"] = {}  # 换信号源后允许立即重新评估
            self._save()
            return self.status(service)

    def reset(self, service=None) -> dict:
        with self.lock:
            signal_mode = self._load().get("signal_mode", "learned_v2")
            self.state = {"cash": self.INIT_CASH, "positions": {}, "trades": [],
                          "equity_hist": [], "running": True, "last_run": None,
                          "last_error": None, "_sig_date": {}, "signal_mode": signal_mode}
            self._save()
            return self.status(service)


class EngineAutoPaperTrader:
    """自动模拟盘：基于 vended a-share-skill PaperTradingEngine（SQLite 账本）。

    - 账本 / 撮合 / T+1 / 涨跌停 / 手续费：PaperTradingEngine（MIT）
    - 信号决策：复用 AutoPaperTrader 的信号引擎
    - 元数据（running / signal_mode / last_run / positions_meta）存 auto_paper_meta.json
    """

    SIGNAL_MODES = AutoPaperTrader.SIGNAL_MODES
    CYCLE_SECONDS = AutoPaperTrader.CYCLE_SECONDS
    INIT_CASH = AutoPaperTrader.INIT_CASH
    ACCOUNT_ID = "default"
    MAX_MOVE_GUARD = 0.20   # 主板 ±10% 涨跌停，用 ±20% 挡错价
    MAX_NEW_PER_CYCLE = 3   # 单轮最大新开仓数
    LOSS_PAUSE_PCT = -0.15  # 总资产回撤超过 15% 时暂停新开仓
    MAX_FORWARD_RECORDS = 300  # 远期验证池最大记录数
    L0_BREADTH_MIN = 0.40   # L0 择时门控：全市场宽度（站上MA20占比）低于该值不开新仓
    MAX_FAMILY_POSITIONS = 4  # 单因子族最大仓位数（单因子暴露控制）

    def __init__(self):
        self.meta_file = Path("auto_paper_meta.json")
        self.db_file = Path("auto_paper_state.db")
        self.engine_lock = EngineLock(str(self.db_file) + ".engine.lock")
        self.state = self._default_meta()
        self._signals = AutoPaperTrader()
        self.engine = PaperTradingEngine(str(self.db_file), market_data=MarketDataProvider(SERVICE))
        self._load_meta()
        try:
            self.engine.get_account(self.ACCOUNT_ID)
        except Exception:
            self.engine.create_account(self.ACCOUNT_ID, self.INIT_CASH)
        try:
            self._migrate_legacy()
        except Exception as e:
            # 迁移失败不能阻塞启动，保留报错供查看
            self.state["last_error"] = f"旧数据迁移失败（不影响新账本）: {e}"

    # ---------- 元数据 ----------

    def _default_meta(self) -> dict:
        return {
            "cash": self.INIT_CASH,
            "running": True,
            "signal_mode": "sequoia_oneil",
            "last_run": None,
            "last_error": None,
            "_sig_date": {},
            "_universe_n": 0,
            "positions_meta": {},
            "forward_pool": [],
            "l0_breadth": None,
            "l0_gate": True,
            "family_exposure": {},
        }

    def _load_meta(self):
        default = self._default_meta()
        try:
            data = json.loads(self.meta_file.read_text(encoding="utf-8"))
            default.update(data if isinstance(data, dict) else {})
        except Exception:
            pass
        self.state = default

    def _save_meta(self):
        self.meta_file.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _position_source(meta: dict) -> str:
        """把持仓元数据归一为界面使用的两类来源标签。"""
        meta = meta if isinstance(meta, dict) else {}
        explicit = str(meta.get("source") or "").strip()
        if explicit in ("决策", "策略"):
            return explicit
        reason = str(meta.get("buy_reason") or "").strip()
        return "决策" if reason.startswith("决策买入") else "策略"

    # ---------- 存量迁移 ----------

    def _migrate_legacy(self):
        legacy = Path("auto_paper_state.json")
        if not legacy.exists():
            return
        with self.engine._connect() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM position_lots").fetchone()["c"]
            if n:
                return
            try:
                data = json.loads(legacy.read_text(encoding="utf-8"))
            except Exception:
                return
        self.state["signal_mode"] = data.get("signal_mode", self.state["signal_mode"])
        self.state["running"] = bool(data.get("running", True))
        self.state["last_run"] = data.get("last_run")
        self.state["_universe_n"] = data.get("_universe_n", 0)
        cash = float(data.get("cash", self.INIT_CASH))
        import uuid as _uuid

        with self.engine._connect() as conn:
            for sym, pos in (data.get("positions") or {}).items():
                d = pos.get("buy_date") or str(pos.get("buy_time", ""))[:10] or "2000-01-01"
                conn.execute(
                    "INSERT INTO position_lots(lot_id, account_id, symbol, acquired_date, qty, remaining_qty, cost_price, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (_uuid.uuid4().hex[:16], self.ACCOUNT_ID, sym, d, int(pos["qty"]), int(pos["qty"]), float(pos["buy_price"]), pos.get("buy_time") or time.strftime("%Y-%m-%d %H:%M:%S")),
                )
                self.state["positions_meta"][sym] = {
                    "buy_price": float(pos["buy_price"]),
                    "buy_date": d,
                    "buy_time": pos.get("buy_time", ""),
                    "buy_reason": pos.get("buy_reason", ""),
                    "source": self._position_source(pos),
                    "target_price": pos.get("target_price"),
                    "stop_price": pos.get("stop_price"),
                }
            for i, t in enumerate((data.get("trades") or [])[:1000]):
                side = str(t.get("side", "buy")).lower()
                price = float(t.get("price") or 0)
                qty = int(t.get("qty") or 0)
                amount = round(price * qty, 2)
                tid = _uuid.uuid4().hex[:16]
                conn.execute(
                    "INSERT INTO trades(trade_id, order_id, account_id, symbol, side, price, qty, amount, commission, tax, created_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (tid, tid, self.ACCOUNT_ID, t.get("symbol", ""), side, price, qty, amount,
                     round(float(t.get("cost") or amount * 0.0015), 2), 0.0, t.get("time", time.strftime("%Y-%m-%d %H:%M:%S"))),
                )
            conn.execute("UPDATE accounts SET initial_cash = ?, cash = ?, updated_at = ? WHERE account_id = ?",
                         (self.INIT_CASH, cash, time.strftime("%Y-%m-%d %H:%M:%S"), self.ACCOUNT_ID))
        self._save_meta()

    # ---------- 状态 ----------

    def _trade_note(self, order_id) -> str:
        if not order_id:
            return ""
        try:
            with self.engine._connect() as conn:
                row = conn.execute("SELECT note FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return row["note"] if row else ""
        except Exception:
            return ""

    def _snapshots_hist(self) -> list:
        try:
            with self.engine._connect() as conn:
                rows = conn.execute(
                    "SELECT snapshot_time AS t, net_asset AS total FROM account_snapshots "
                    "WHERE account_id = ? ORDER BY snapshot_time ASC",
                    (self.ACCOUNT_ID,),
                ).fetchall()
            return [{"time": r["t"], "total": r["total"]} for r in rows][-120:]
        except Exception:
            return []

    def status(self, service=None) -> dict:
        self._load_meta()
        try:
            acc = self.engine.get_account(self.ACCOUNT_ID)
        except Exception:
            self.engine.create_account(self.ACCOUNT_ID, self.INIT_CASH)
            acc = self.engine.get_account(self.ACCOUNT_ID)

        pos_meta = self.state.get("positions_meta", {})
        pos_list = []
        for p in (acc.get("positions") or []):
            m = pos_meta.get(p["symbol"], {})
            buy_price = float(m.get("buy_price") or p.get("avg_cost") or 0)
            last = float(p.get("last_price") or buy_price)
            target = float(m.get("target_price") or (buy_price * (1 + self._signals.TAKE_PROFIT) if buy_price else 0))
            stop = float(m.get("stop_price") or (buy_price * (1 - self._signals.STOP_LOSS) if buy_price else 0))
            pnl = round((last - buy_price) * int(p["qty"]), 2) if buy_price else 0.0
            pnl_pct = round((last / buy_price - 1) * 100, 2) if buy_price else 0.0
            pos_list.append({
                "symbol": p["symbol"], "qty": int(p["qty"]),
                "buy_price": round(buy_price, 2), "avg_cost": round(buy_price, 4),
                "target_price": round(target, 2), "stop_price": round(stop, 2),
                "last_price": round(last, 2), "value": round(last * int(p["qty"]), 2),
                "pnl": pnl, "pnl_pct": pnl_pct,
                "target_pct": self._signals.TAKE_PROFIT * 100,
                "stop_pct": -self._signals.STOP_LOSS * 100,
                "buy_time": m.get("buy_time", ""), "buy_reason": m.get("buy_reason", ""),
                "source": self._position_source(m),
            })
        pos_list.sort(key=lambda x: -x["value"])

        trade_list = []
        for t in (self.engine.list_trades(self.ACCOUNT_ID) or []):
            trade_list.append({
                "symbol": t["symbol"], "side": str(t["side"]).upper(),
                "price": t["price"], "qty": t["qty"],
                "time": t["created_at"], "reason": self._trade_note(t.get("order_id")),
                "commission": t.get("commission"), "tax": t.get("tax"),
                "pnl_pct": None, "pnl": None,
            })

        total = acc.get("net_asset") or 0.0
        pnl = total - self.INIT_CASH
        pnl_pct = round((total / self.INIT_CASH - 1) * 100, 2) if self.INIT_CASH else 0.0
        st = self.state
        summary = getattr(service, "universe_summary", None) if service is not None else None
        if summary is None:
            summary = {
                "total": st.get("_universe_n", 0), "computable": 0, "tradable": 0,
                "candidate": 0, "excluded_by_reason": {}, "as_of": None, "source": "unknown",
            }
        return {
            "cash": round(acc.get("cash") or 0.0, 2),
            "market_value": round(acc.get("market_value") or 0.0, 2),
            "total": round(total, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
            "initial": self.INIT_CASH,
            "positions": pos_list,
            "position_count": len(pos_list),
            "max_positions": self._signals.MAX_POSITIONS,
            "trades": trade_list[:200],
            "equity_hist": self._snapshots_hist(),
            "running": bool(st.get("running", True)),
            "last_run": st.get("last_run"),
            "last_error": st.get("last_error"),
            "cycle_seconds": self.CYCLE_SECONDS,
            "signal_mode": st.get("signal_mode", "sequoia_oneil"),
            "signal_mode_label": self.SIGNAL_MODES.get(st.get("signal_mode", "sequoia_oneil"), "sequoia_oneil"),
            "signal_modes": [{"mode": k, "label": v} for k, v in self.SIGNAL_MODES.items()],
            "engine_owner": True,
            "universe_size": st.get("_universe_n", 0),
            "universe_summary": summary,
            "rules": {
                "pos_ratio": self._signals.POS_RATIO, "take_profit": self._signals.TAKE_PROFIT,
                "stop_loss": self._signals.STOP_LOSS, "cost": self._signals.COST,
            },
            "forward_pool": (st.get("forward_pool") or [])[-50:],
            "risk": {
                "max_positions": self._signals.MAX_POSITIONS,
                "max_new_per_cycle": self.MAX_NEW_PER_CYCLE,
                "loss_pause_pct": self.LOSS_PAUSE_PCT * 100,
                "current_pnl_pct": round(((acc.get("net_asset") or 0) / self.INIT_CASH - 1) * 100, 2) if self.INIT_CASH else 0.0,
                "l0_breadth": st.get("l0_breadth"),
                "l0_gate": st.get("l0_gate", True),
                "l0_breadth_min": self.L0_BREADTH_MIN,
                "max_family_positions": self.MAX_FAMILY_POSITIONS,
                "family_exposure": st.get("family_exposure", {}),
            },
        }

    def toggle(self, service=None) -> dict:
        self._load_meta()
        self.state["running"] = not self.state.get("running", True)
        self._save_meta()
        return self.status(service)

    def set_mode(self, mode: str, service=None) -> dict:
        if mode not in self.SIGNAL_MODES:
            raise ValueError(f"未知信号源: {mode}，可选: {', '.join(self.SIGNAL_MODES)}")
        self._load_meta()
        self.state["signal_mode"] = mode
        self.state["_sig_date"] = {}
        self._save_meta()
        return self.status(service)

    def reset(self, service=None) -> dict:
        self._load_meta()
        mode = self.state.get("signal_mode", "sequoia_oneil")
        self.engine.reset_account(self.ACCOUNT_ID, self.INIT_CASH)
        self.state = self._default_meta()
        self.state["signal_mode"] = mode
        self._save_meta()
        return self.status(service)

    # ---------- 信号 ----------

    def _sig(self, df, rs_pct=50.0, breadth=1.0):
        self._signals.state["signal_mode"] = self.state.get("signal_mode", "sequoia_oneil")
        return self._signals._signal_for(df, rs_pct=rs_pct, breadth=breadth)

    # ---------- 风控门禁 + 远期验证 ----------

    def _risk_gate(self, acc) -> tuple:
        """硬性风控：总资产回撤超阈值 → 暂停新开仓。返回 (是否通过, 原因)。"""
        total = float(acc.get("net_asset") or 0)
        pnl_pct = (total / self.INIT_CASH - 1) * 100 if self.INIT_CASH else 0.0
        if pnl_pct <= self.LOSS_PAUSE_PCT * 100:
            return False, f"总资产回撤 {pnl_pct:.1f}%，超过暂停新开仓阈值"
        return True, ""

    @staticmethod
    def _factor_family(reason: str) -> str:
        """按买入理由把持仓归入信号族（用于单因子暴露控制）。"""
        r = reason or ""
        if any(k in r for k in ("海龟", "突破", "新高", "攻关", "枢轴")):
            return "breakout"      # 突破/新高
        if any(k in r for k in ("MA5", "MA10", "均线", "金叉", "多头", "趋势")):
            return "trend"         # 均线趋势
        if any(k in r for k in ("RSI", "超卖", "反转", "回撤", "反弹", "低波", "lowvol")):
            return "reversal"      # 反转/低波
        if any(k in r for k in ("涨停", "洗盘", "连板")):
            return "limitup"       # 涨停/情绪
        if any(k in r for k in ("量", "缩量", "放量", "OBV")):
            return "volume"        # 量价
        return "other"

    def _family_counts(self) -> dict:
        from collections import Counter
        cnt = Counter()
        held = {p["symbol"] for p in self.engine.get_positions(self.ACCOUNT_ID)}
        for sym in held:
            meta = self.state["positions_meta"].get(sym, {})
            cnt[self._factor_family(meta.get("buy_reason"))] += 1
        return dict(cnt)

    def _record_forward(self, sym, meta, entry, price, cur_date):
        """把一笔已平仓交易写入远期验证池（五池：V1/5/20/60 由 hold_days 归纳）。"""
        buy_date = meta.get("buy_date") or str(meta.get("buy_time", ""))[:10]
        hold_days = None
        if buy_date and cur_date:
            try:
                hold_days = max(0, (pd.Timestamp(cur_date) - pd.Timestamp(buy_date)).days)
            except Exception:
                hold_days = None
        rec = {
            "symbol": sym,
            "entry_date": buy_date,
            "entry_price": round(float(entry), 2),
            "exit_date": cur_date,
            "exit_price": round(float(price), 2),
            "pnl_pct": round((float(price) / float(entry) - 1) * 100, 2) if entry else 0.0,
            "hold_days": hold_days,
            "horizons": [h for h in (1, 5, 20, 60) if hold_days is not None and hold_days >= h],
        }
        pool = self.state.setdefault("forward_pool", [])
        pool.append(rec)
        self.state["forward_pool"] = pool[-self.MAX_FORWARD_RECORDS:]

    # ---------- 交易周期 ----------

    def buy_from_decision(self, service, rec) -> dict:
        """决策审批买入：把 Pitch 批准的 buy 合入统一模拟盘（同一账本，含 T+1/手续费/风控）。"""
        self._load_meta()
        if service is None:
            return self.status(service)
        sym = str(rec.get("code") or "").strip()
        if not sym:
            return {"ok": False, "error": "缺少 code"}
        if not service.is_tradable(sym):
            self.state["last_error"] = f"决策买入 {sym} 失败：标的暂不可交易"
            self._save_meta()
            return self.status(service)
        if not self.state.get("running", True):
            return self.status(service)
        if not self.engine_lock.acquired():
            return self.status(service)
        held = {p["symbol"] for p in self.engine.get_positions(self.ACCOUNT_ID)}
        if sym in held:
            return self.status(service)
        if len(held) >= self._signals.MAX_POSITIONS:
            self.state["last_error"] = f"决策买入 {sym} 失败：持仓已达上限 {self._signals.MAX_POSITIONS}"
            self._save_meta()
            return self.status(service)
        # ★L0 择时门控：与策略买入一致，宽度低于阈值时决策买入也暂缓
        if not self.state.get("l0_gate", True):
            b = self.state.get("l0_breadth", 0.0)
            self.state["last_error"] = (f"L0 择时门控：市场宽度 {b:.1%} < "
                                        f"{self.L0_BREADTH_MIN:.0%}，决策买入暂缓")
            self._save_meta()
            return self.status(service)
        try:
            kline = service.get_kline(sym, limit=1)
            price = float(kline[-1]["close"]) if kline else None
        except Exception:
            price = rec.get("price") or None
        if not price or price <= 0:
            self.state["last_error"] = f"决策买入 {sym} 失败：无有效价格"
            self._save_meta()
            return self.status(service)
        acc = self.engine.get_account(self.ACCOUNT_ID)
        cash = float(acc.get("cash") or 0) - float(acc.get("frozen_cash") or 0)
        total = float(acc.get("net_asset") or 0)
        budget = total * self._signals.POS_RATIO
        cost_per = price * (1 + self._signals.COST)
        qty = int(budget / cost_per) // 100 * 100
        if qty * cost_per > cash + 1e-6:
            qty = int(cash / cost_per) // 100 * 100
        if qty < 100:
            self.state["last_error"] = f"决策买入 {sym} 失败：现金不足或金额过小"
            self._save_meta()
            return self.status(service)
        reason = f"决策买入：{rec.get('name') or rec.get('reason') or 'Pitch 审批'}"
        try:
            order = self.engine.trade_at_quote(self.ACCOUNT_ID, sym, "buy", qty, reason)
            fill = float(order.get("avg_fill_price") or price)
            self.state["positions_meta"][sym] = {
                "buy_price": fill,
                "buy_date": time.strftime("%Y-%m-%d"),
                "buy_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "buy_reason": reason,
                "source": "决策",
                "target_price": round(fill * (1 + self._signals.TAKE_PROFIT), 2),
                "stop_price": round(fill * (1 - self._signals.STOP_LOSS), 2),
            }
            self.state["family_exposure"] = self._family_counts()
            self.engine.snapshot_accounts()
            self.state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta()
        except Exception as e:
            self.state["last_error"] = f"决策买入 {sym} 失败: {e}"
            self._save_meta()
        return self.status(service)

    def cycle(self, service) -> dict:
        if service is None:
            return self.status(service)
        self._load_meta()
        if not self.state.get("running", True):
            return self.status(service)
        # 跨进程互斥：只有拿到引擎锁的进程真正交易，其余只读（与旧版一致）
        if not self.engine_lock.acquired():
            return self.status(service)
        self.state["last_error"] = None
        mode = self.state.get("signal_mode", "sequoia_oneil")
        self._signals.state["signal_mode"] = mode
        is_fusion = mode == "sequoia_oneil"
        is_oneil = mode == "oneil"
        # 股票池：全主板
        candidate_symbols = []
        mainboard = service.mainboard_symbols()
        self.state["_universe_n"] = len(mainboard)
        rs_map, breadth = {}, 1.0
        if is_fusion or is_oneil:
            rs_map, breadth, symbols = self._signals._mainboard_scan(
                service, mainboard,
                rs_min=SequoiaOneilEngine.RS_MIN if is_fusion else OneilSignalEngine.RS_MIN)
            candidate_symbols = list(symbols)
        else:
            symbols = mainboard
            # 全市场宽度（站上 MA20 占比）——用于 L0 择时门控，所有信号源统一计算
            above = []
            for sym in symbols:
                d = service.load_history(sym)
                if d is None or len(d) < 25:
                    continue
                c = d["close"].astype(float)
                above.append(float(c.iloc[-1]) > float(c.rolling(20).mean().iloc[-1]))
            if above:
                breadth = sum(above) / len(above)

        # L0 择时门控：宽度低于阈值时，本轮不开新仓（持仓卖出照常）
        self.state["l0_breadth"] = round(breadth, 4)
        self.state["l0_gate"] = bool(breadth >= self.L0_BREADTH_MIN)

        def held_set():
            return {p["symbol"] for p in self.engine.get_positions(self.ACCOUNT_ID)}

        # 1) 卖出：止盈/止损/信号卖出（引擎负责 T+1 与涨跌停）
        for sym, pos in list({p["symbol"]: p for p in self.engine.get_positions(self.ACCOUNT_ID)}.items()):
            meta = self.state["positions_meta"].get(sym, {})
            df = service._resolve_df(sym)
            sig = self._sig(df, rs_pct=rs_map.get(sym, 50.0), breadth=breadth)
            price = float(sig["price"]) if sig else float(pos.get("last_price") or 0)
            entry = float(meta.get("buy_price") or pos.get("avg_cost") or 0)
            last = float(meta.get("last", pos.get("last_price"))) or entry
            anomaly = last > 0 and abs(price / last - 1) > self.MAX_MOVE_GUARD
            if anomaly:
                self.state["last_error"] = f"{sym} 价格异常 {last:.2f}->{price:.2f}，跳过该持仓本轮"
                price = last
            meta["last"] = price
            cur_date = (sig or {}).get("date")
            if not cur_date and df is not None and len(df) > 0:
                cur_date = str(df.index[-1])[:10]
            buy_date = meta.get("buy_date") or str(meta.get("buy_time", ""))[:10]
            if anomaly or (buy_date and cur_date and buy_date == cur_date):
                continue
            target = float(meta.get("target_price") or (entry * (1 + self._signals.TAKE_PROFIT) if entry else 0))
            stop = float(meta.get("stop_price") or (entry * (1 - self._signals.STOP_LOSS) if entry else 0))
            reason = ""
            if target and price >= target:
                reason = f"止盈：触及 {target}"
            elif stop and price <= stop:
                reason = f"止损：跌破 {stop}"
            elif sig and sig.get("action") == "sell" and sig.get("date") != self.state["_sig_date"].get(sym):
                reason = f"信号卖出：{sig.get('reason', '')}"
            if not reason:
                continue
            sellable = int(pos.get("sellable_qty") or 0)
            qty = min(int(pos["qty"]), sellable)
            if qty <= 0:
                continue
            try:
                order = self.engine.trade_at_quote(self.ACCOUNT_ID, sym, "sell", qty, reason)
                fill = float(order.get("avg_fill_price") or price)
                self._record_forward(sym, meta, entry, fill, cur_date)
            except Exception as e:
                if "sellable" not in str(e).lower():
                    self.state["last_error"] = f"卖出 {sym} 失败: {e}"

        # 2) 买入：信号买入（含 L0 择时门控 + 单因子暴露控制 + 单轮新开仓上限）
        acc0 = self.engine.get_account(self.ACCOUNT_ID)
        risk_ok, risk_reason = self._risk_gate(acc0)
        if risk_ok and not self.state.get("l0_gate", True):
            risk_ok = False
            l0_b = self.state.get("l0_breadth", 0.0)
            risk_reason = f"L0 择时门控：市场宽度 {l0_b:.1%} < {self.L0_BREADTH_MIN:.0%}，暂缓新开仓"
        new_buys = 0
        for sym in symbols:
            if not risk_ok:
                self.state["last_error"] = risk_reason
                break
            if new_buys >= self.MAX_NEW_PER_CYCLE:
                break
            held = held_set()
            if sym in held:
                continue
            if len(held) >= self._signals.MAX_POSITIONS:
                break
            if not service.is_tradable(sym):
                continue
            df = service.load_history(sym)
            sig = self._sig(df, rs_pct=rs_map.get(sym, 50.0), breadth=breadth)
            if not sig or sig.get("action") != "buy" or sig.get("date") == self.state["_sig_date"].get(sym):
                continue
            candidate_symbols.append(sym)
            # 单因子暴露控制：同一信号族持仓数达到上限则跳过该买入
            family = self._factor_family(sig.get("reason", ""))
            if self._family_counts().get(family, 0) >= self.MAX_FAMILY_POSITIONS:
                continue
            acc = self.engine.get_account(self.ACCOUNT_ID)
            cash = float(acc.get("cash") or 0) - float(acc.get("frozen_cash") or 0)
            total = float(acc.get("net_asset") or 0)
            budget = total * self._signals.POS_RATIO
            price = float(sig.get("price") or 0)
            cost_per = price * (1 + self._signals.COST)
            if price <= 0 or cost_per <= 0:
                continue
            qty = int(budget / cost_per) // 100 * 100
            if qty < 100 or qty * cost_per > cash + 1e-6:
                continue
            try:
                order = self.engine.trade_at_quote(self.ACCOUNT_ID, sym, "buy", qty, sig.get("reason", ""))
                fill_price = float(order.get("avg_fill_price") or price)
                buy_date = sig.get("date") or time.strftime("%Y-%m-%d")
                self.state["positions_meta"][sym] = {
                    "buy_price": fill_price,
                    "buy_date": buy_date,
                    "buy_time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "buy_reason": sig.get("reason", ""),
                    "source": "策略",
                    "target_price": round(fill_price * (1 + sig.get("take_pct", self._signals.TAKE_PROFIT)), 2),
                    "stop_price": round(fill_price * (1 - sig.get("stop_pct", self._signals.STOP_LOSS)), 2),
                }
                self.state["_sig_date"][sym] = sig.get("date")
                new_buys += 1
            except Exception as e:
                err = str(e)
                if "limit" not in err.lower():
                    self.state["last_error"] = f"买入 {sym} 失败: {e}"

        # 清除已平仓的 meta，并刷新单因子暴露统计
        held_now = held_set()
        for sym in list(self.state["positions_meta"]):
            if sym not in held_now:
                self.state["positions_meta"].pop(sym, None)
        self.state["family_exposure"] = self._family_counts()
        service.set_candidate_symbols(candidate_symbols)

        # 净值快照 + 保存
        self.engine.snapshot_accounts()
        self.state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._save_meta()
        return self.status(service)


# ============================================================================
# HTTP 服务
# ============================================================================

SERVICE: DataService = None
STATIC_DIR: Path = None
AI_PAPER: AiPaperTrader = None
AUTO_PAPER: AutoPaperTrader = None
FACTOR_LIBRARY: FactorLibrary | None = None
FACTOR_LIBRARY_FILE: Path | None = None
FACTOR_LIBRARY_PREFIX = "/api/factor-library"
DEEPSEEK_CHAT_SERVICE: DeepSeekChatService | None = None

DEEPSEEK_CHAT_PREFIX = "/api/deepseek-chat"
DEEPSEEK_CHAT_STATUS_PATH = f"{DEEPSEEK_CHAT_PREFIX}/status"
DEEPSEEK_CHAT_SEND_PATH = f"{DEEPSEEK_CHAT_PREFIX}/send"
DEEPSEEK_CHAT_POLL_PATH = f"{DEEPSEEK_CHAT_PREFIX}/poll"
DEEPSEEK_CHAT_HISTORY_PATH = f"{DEEPSEEK_CHAT_PREFIX}/history"
DEEPSEEK_CHAT_CANCEL_PATH = f"{DEEPSEEK_CHAT_PREFIX}/cancel"

UPDATE_STATUS_PATH = Path(__file__).resolve().parent / "logs" / "daily_update_1830.status.json"
UPDATE_RUN_PATH = "/api/update/run"
UPDATE_RUN_STATUS_PATH = f"{UPDATE_RUN_PATH}/status"
UPDATE_RUN_STOP_PATH = f"{UPDATE_RUN_PATH}/stop"
UPDATE_LOG_PATH = UPDATE_STATUS_PATH.with_name("daily_update_1830.log")
UPDATE_LOCK_PATH = UPDATE_STATUS_PATH.with_name("daily_update_1830.lock")
MANUAL_UPDATE_MAX_BODY_BYTES = 1024
MANUAL_UPDATE_CONTROLLER = None
_UPDATE_STATUS_STATES = frozenset({"running", "skip", "success", "failure", "aborted", "timed_out"})
_UPDATE_STATUS_REASONS = frozenset({
    "started",
    "pipeline_running",
    "completed",
    "dry_run",
    "forced",
    "weekend",
    "calendar_cache",
    "calendar_cache_closed",
    "calendar_api",
    "calendar_api_closed",
    "deck_missing",
    "step_failed",
    "lock_busy",
    "status_unavailable",
    "update_failed",
    "calendar_unavailable",
    "aborted",
    "application_shutdown",
    "manual_stop",
    "stale_running",
    "timeout",
    "process_timeout",
})
_UPDATE_STATUS_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UPDATE_STATUS_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$"
)
_UPDATE_STATUS_OUTPUTS = ("portal", "decision", "factors")
_UPDATE_STATUS_FRESHNESS_GROUPS = ("portal", "factors", "decision", "sync")
_UPDATE_STATUS_FRESHNESS_SOURCES = frozenset({
    "external_sqlite",
    "factor_artifacts",
    "decision_artifact",
    "sync_target",
    "dry_run",
    "unavailable",
})
_UPDATE_STATUS_FRESHNESS_REASONS = frozenset({
    "baseline_captured",
    "verified",
    "dry_run",
    "database_unavailable",
    "metadata_missing",
    "metadata_schema_unsupported",
    "metadata_read_error",
    "bars_missing",
    "bars_schema_unsupported",
    "bars_read_error",
    "factor_artifact_missing",
    "factor_date_mismatch",
    "factor_artifact_unchanged",
    "factor_core_artifact_missing",
    "factor_count_missing",
    "decision_pool_missing_or_stale",
    "decision_empty_result_unconfirmed",
    "decision_pitch_missing_or_stale",
    "sync_target_unavailable",
    "sync_target_missing",
    "sync_target_stale_or_incomplete",
    "portal_date_missing",
    "portal_stale",
    "portal_coverage_insufficient",
})
_UPDATE_STATUS_FRESHNESS_COUNTS = (
    "total",
    "computable",
    "tradable",
    "coverage",
    "coverage_required",
    "factor_count",
    "valid_count",
    "artifact_count",
    "pool_count",
    "pitch_count",
)


def configure_update_state_dir(path: str | Path | None) -> Path:
    """Use a writable server-owned directory for update lifecycle artifacts."""

    global UPDATE_STATUS_PATH, UPDATE_LOG_PATH, UPDATE_LOCK_PATH
    state_dir = Path(path).expanduser() if path else UPDATE_STATUS_PATH.parent
    state_dir = state_dir.resolve()
    UPDATE_STATUS_PATH = state_dir / "daily_update_1830.status.json"
    UPDATE_LOG_PATH = state_dir / "daily_update_1830.log"
    UPDATE_LOCK_PATH = state_dir / "daily_update_1830.lock"
    return state_dir


def read_update_status(path: Path | None = None) -> dict:
    """Read the daily-update status without exposing local runtime details."""
    fallback = {
        "schema_version": 1,
        "trade_date": None,
        "state": "unknown",
        "reason": "status_unavailable",
        "started_at": None,
        "finished_at": None,
        "outputs": {key: False for key in _UPDATE_STATUS_OUTPUTS},
    }
    try:
        payload = json.loads(Path(path or UPDATE_STATUS_PATH).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return fallback
    if not isinstance(payload, dict):
        return fallback

    result = dict(fallback)
    version = payload.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        result["schema_version"] = version
    trade_date = payload.get("trade_date")
    if isinstance(trade_date, str) and _UPDATE_STATUS_DATE.fullmatch(trade_date):
        result["trade_date"] = trade_date
    state = payload.get("state")
    if isinstance(state, str) and state in _UPDATE_STATUS_STATES:
        result["state"] = state
    reason = payload.get("reason")
    if isinstance(reason, str):
        if reason in _UPDATE_STATUS_REASONS:
            result["reason"] = reason
        elif reason.startswith("calendar_unavailable:"):
            result["reason"] = "calendar_unavailable"
        elif result["state"] == "failure":
            result["reason"] = "update_failed"
    for key in ("started_at", "finished_at"):
        value = payload.get(key)
        if isinstance(value, str) and _UPDATE_STATUS_TIMESTAMP.fullmatch(value):
            result[key] = value
    outputs = payload.get("outputs")
    if isinstance(outputs, dict) and result["state"] != "unknown":
        result["outputs"] = {
            key: outputs.get(key) is True for key in _UPDATE_STATUS_OUTPUTS
        }

    def safe_freshness(value):
        if not isinstance(value, dict):
            return {}
        safe = {}
        for group in _UPDATE_STATUS_FRESHNESS_GROUPS:
            item = value.get(group)
            if not isinstance(item, dict):
                continue
            clean = {"verified": item.get("verified") is True}
            as_of = item.get("as_of")
            if isinstance(as_of, str) and _UPDATE_STATUS_DATE.fullmatch(as_of[:10]):
                clean["as_of"] = as_of[:10]
            else:
                clean["as_of"] = None
            source = item.get("source")
            clean["source"] = source if source in _UPDATE_STATUS_FRESHNESS_SOURCES else "unavailable"
            reason_value = item.get("reason")
            clean["reason"] = (
                reason_value if reason_value in _UPDATE_STATUS_FRESHNESS_REASONS else "unavailable"
            )
            for key in _UPDATE_STATUS_FRESHNESS_COUNTS:
                count = item.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    clean[key] = count
            if isinstance(item.get("pitch_verified"), bool):
                clean["pitch_verified"] = item["pitch_verified"]
            for key in ("portal", "factors", "decision"):
                if isinstance(item.get(key), bool):
                    clean[key] = item[key]
            safe[group] = clean
        return safe

    freshness = safe_freshness(payload.get("freshness"))
    if freshness:
        result["freshness"] = freshness
    output_meta = safe_freshness(payload.get("output_meta"))
    if output_meta:
        result["output_meta"] = output_meta
    retry = payload.get("retry")
    if isinstance(retry, dict):
        safe_retry = {}
        for key in ("attempt", "max_attempts"):
            value = retry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_retry[key] = value
        next_attempt = retry.get("next_attempt_at")
        if isinstance(next_attempt, str) and _UPDATE_STATUS_TIMESTAMP.fullmatch(next_attempt):
            safe_retry["next_attempt_at"] = next_attempt
        if safe_retry:
            result["retry"] = safe_retry
    return result


def get_factor_library() -> FactorLibrary:
    """Return the process-local factor plan store outside packaged resources."""

    global FACTOR_LIBRARY
    if FACTOR_LIBRARY is None:
        path = FACTOR_LIBRARY_FILE or resolve_factor_library_path()
        FACTOR_LIBRARY = FactorLibrary(path, qtrade_base_bridge.base_dir())
    return FACTOR_LIBRARY


def get_manual_update_controller():
    """Lazily create the server-owned single-flight manual update controller."""

    global MANUAL_UPDATE_CONTROLLER
    if MANUAL_UPDATE_CONTROLLER is None:
        MANUAL_UPDATE_CONTROLLER = update_runtime.ManualUpdateController(
            base_dir_fn=qtrade_base_bridge.base_dir,
            project_root=Path(__file__).resolve().parent,
            status_file=UPDATE_STATUS_PATH,
            lock_path=UPDATE_STATUS_PATH.with_name("daily_update_1830.manual.lock"),
            pipeline_lock_path=UPDATE_LOCK_PATH,
            log_file=UPDATE_LOG_PATH,
        )
    return MANUAL_UPDATE_CONTROLLER


_MANUAL_UPDATE_STATES = frozenset({
    "idle", "accepted", "running", "success", "skip", "failure", "aborted", "timed_out",
})
_MANUAL_UPDATE_REASONS = frozenset({
    "accepted",
    "running",
    "started",
    "pipeline_running",
    "forced",
    "dry_run",
    "before_cutoff",
    "already_running",
    "already_success",
    "lock_busy",
    "calendar_unavailable",
    "calendar_cache",
    "calendar_cache_closed",
    "calendar_api",
    "calendar_api_closed",
    "weekend",
    "deck_missing",
    "step_failed",
    "update_failed",
    "status_unavailable",
    "completed",
    "aborted",
    "application_shutdown",
    "manual_stop",
    "stale_running",
    "timeout",
    "process_timeout",
})
_MANUAL_UPDATE_OUTPUTS = ("portal", "factors", "decision", "sync")
_MANUAL_UPDATE_STEPS = frozenset({
    "calendar",
    "resolve_deck",
    "freshness",
    "portal",
    "portal_freshness",
    "factors",
    "factor_freshness",
    "decision_scan",
    "decision_pool_freshness",
    "decision_pitch_v2",
    "decision_freshness",
    "sync",
    "sync_freshness",
    "pipeline",
})


def _safe_manual_update_payload(payload) -> dict:
    """Return only the stable fields exposed to the native control console."""

    fallback = {
        "schema_version": 1,
        "accepted": False,
        "state": "idle",
        "trade_date": None,
        "started_at": None,
        "finished_at": None,
        "reason": "status_unavailable",
        "outputs": {key: False for key in _MANUAL_UPDATE_OUTPUTS},
        "freshness": {},
        "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
        "step": None,
        "heartbeat_at": None,
        "elapsed_seconds": 0.0,
        "progress": {"completed": 0, "total": 0, "current": None},
    }
    if not isinstance(payload, dict):
        return fallback

    result = dict(fallback)
    state = payload.get("state")
    result["state"] = state if isinstance(state, str) and state in _MANUAL_UPDATE_STATES else "idle"
    result["accepted"] = result["state"] == "accepted"
    trade_date = payload.get("trade_date")
    if isinstance(trade_date, str) and _UPDATE_STATUS_DATE.fullmatch(trade_date[:10]):
        result["trade_date"] = trade_date[:10]
    for key in ("started_at", "finished_at"):
        value = payload.get(key)
        if isinstance(value, str) and _UPDATE_STATUS_TIMESTAMP.fullmatch(value[:32]):
            result[key] = value[:32]
    heartbeat = payload.get("heartbeat_at")
    if isinstance(heartbeat, str) and _UPDATE_STATUS_TIMESTAMP.fullmatch(heartbeat[:32]):
        result["heartbeat_at"] = heartbeat[:32]
    step = payload.get("step")
    if isinstance(step, str) and step in _MANUAL_UPDATE_STEPS:
        result["step"] = step
    elapsed = payload.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed >= 0:
        result["elapsed_seconds"] = min(float(elapsed), 86_400.0)
    progress = payload.get("progress")
    if isinstance(progress, dict):
        completed = progress.get("completed")
        total = progress.get("total")
        current = progress.get("current")
        if isinstance(completed, int) and not isinstance(completed, bool) and completed >= 0:
            result["progress"]["completed"] = min(completed, 100)
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            result["progress"]["total"] = min(total, 100)
        if isinstance(current, str) and current in _MANUAL_UPDATE_STEPS:
            result["progress"]["current"] = current
    reason = payload.get("reason")
    if isinstance(reason, str) and reason.startswith("calendar_unavailable:"):
        result["reason"] = "calendar_unavailable"
    elif isinstance(reason, str) and reason in _MANUAL_UPDATE_REASONS:
        result["reason"] = reason
    elif result["state"] == "failure":
        result["reason"] = "update_failed"

    outputs = payload.get("outputs")
    if isinstance(outputs, dict):
        result["outputs"] = {key: outputs.get(key) is True for key in _MANUAL_UPDATE_OUTPUTS}

    freshness = payload.get("freshness")
    if isinstance(freshness, dict):
        clean_freshness = {}
        for group in ("portal", "factors", "decision", "sync"):
            item = freshness.get(group)
            if not isinstance(item, dict):
                continue
            clean = {"verified": item.get("verified") is True}
            as_of = item.get("as_of")
            clean["as_of"] = as_of[:10] if isinstance(as_of, str) and _UPDATE_STATUS_DATE.fullmatch(as_of[:10]) else None
            source = item.get("source")
            clean["source"] = source if isinstance(source, str) and source in _UPDATE_STATUS_FRESHNESS_SOURCES else "unavailable"
            fresh_reason = item.get("reason")
            clean["reason"] = (
                fresh_reason
                if isinstance(fresh_reason, str) and fresh_reason in _UPDATE_STATUS_FRESHNESS_REASONS
                else "unavailable"
            )
            for key in _UPDATE_STATUS_FRESHNESS_COUNTS:
                count = item.get(key)
                if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                    clean[key] = count
            for key in ("pitch_verified", "portal", "factors", "decision"):
                if isinstance(item.get(key), bool):
                    clean[key] = item[key]
            clean_freshness[group] = clean
        result["freshness"] = clean_freshness

    retry = payload.get("retry")
    if isinstance(retry, dict):
        clean_retry = dict(fallback["retry"])
        for key in ("attempt", "max_attempts"):
            value = retry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                clean_retry[key] = value
        next_attempt = retry.get("next_attempt_at")
        if isinstance(next_attempt, str) and _UPDATE_STATUS_TIMESTAMP.fullmatch(next_attempt[:32]):
            clean_retry["next_attempt_at"] = next_attempt[:32]
        result["retry"] = clean_retry
    return result


def _deepseek_health_context() -> dict[str, str]:
    """Return only the health enum allowed in the chat context."""

    return {"status": "ok" if SERVICE is not None else "unavailable"}


def _deepseek_business_date_context() -> dict[str, str | None]:
    """Reduce update status to a date and a small freshness enum."""

    status = read_update_status()
    freshness = status.get("freshness")
    portal = freshness.get("portal") if isinstance(freshness, dict) else None
    portal = portal if isinstance(portal, dict) else {}
    as_of = portal.get("as_of") or status.get("trade_date")
    if not isinstance(as_of, str):
        as_of = None
    if portal.get("verified") is True:
        freshness_value = "fresh"
    elif as_of and status.get("state") in {"running", "failure"}:
        freshness_value = "stale"
    else:
        freshness_value = "unknown"
    return {"as_of": as_of, "freshness": freshness_value}


def _deepseek_mainboard_context() -> dict[str, object]:
    """Read the existing typed universe summary and discard all other fields."""

    if SERVICE is None:
        return {}
    try:
        summary = SERVICE.universe_summary
    except Exception:
        return {}
    return summary if isinstance(summary, dict) else {}


def _deepseek_opportunities_context() -> dict[str, object]:
    """Expose an opportunity count without exposing its symbol members."""

    if SERVICE is None:
        return {}
    try:
        count = len(getattr(SERVICE, "_candidate_symbols", ()))
    except Exception:
        count = 0
    return {"count": count, "categories": {"screened": count}}


def _deepseek_factors_context() -> dict[str, object]:
    """Reduce saved factor plans to counts and a business date."""

    if FACTOR_LIBRARY is None:
        return {}
    try:
        items = FACTOR_LIBRARY.list_items()
    except Exception:
        return {}
    if not isinstance(items, list):
        return {}
    dates = sorted(
        item.get("as_of")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("as_of"), str)
    )
    as_of = dates[-1] if dates else None
    business_date = _deepseek_business_date_context().get("as_of")
    if as_of and business_date:
        freshness = "fresh" if as_of == business_date else "stale"
    else:
        freshness = "unknown"
    return {
        "scheme_count": len(items),
        "active_count": len(items),
        "as_of": as_of,
        "freshness": freshness,
    }


def _build_deepseek_context() -> dict[str, object]:
    """Build the exact context sent by the QTrade-owned chat service."""

    return build_context(
        ContextProvider(
            health=_deepseek_health_context,
            business_date=_deepseek_business_date_context,
            mainboard=_deepseek_mainboard_context,
            opportunities=_deepseek_opportunities_context,
            factors=_deepseek_factors_context,
        )
    )


def get_deepseek_chat_service() -> DeepSeekChatService:
    """Lazily create the service; construction never reads a key or starts a worker."""

    global DEEPSEEK_CHAT_SERVICE
    if DEEPSEEK_CHAT_SERVICE is None:
        DEEPSEEK_CHAT_SERVICE = DeepSeekChatService(context_provider=_build_deepseek_context)
    return DEEPSEEK_CHAT_SERVICE


class APIHandler(SimpleHTTPRequestHandler):
    """静态文件 + JSON API。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):  # 静默访问日志
        pass

    # ---------- 复用 deepseek-harness-quant（门户/决策/控制台，同端口） ----------

    def _base_dir(self) -> Path:
        return qtrade_base_bridge.base_dir()

    def _serve_base_file(self, fspath: Path):
        return qtrade_base_bridge.serve_base_file(self, fspath)

    def _base_live(self, sub: str):
        return qtrade_base_bridge.live(self, sub)

    def _try_base_deck(self, path: str) -> bool:
        return qtrade_base_bridge.try_serve(self, path)

    def _decide(self, rec):
        return qtrade_base_bridge.decide(self, rec, auto_paper=AUTO_PAPER, service=SERVICE)

    def _decide_bg_sync(self, base):
        return qtrade_base_bridge.decide_bg_sync(base)

    @staticmethod
    def _is_factor_library_path(path: str) -> bool:
        return path == FACTOR_LIBRARY_PREFIX or path.startswith(FACTOR_LIBRARY_PREFIX + "/")

    def _factor_error(self, error: FactorLibraryError, *, status: int | None = None):
        self._json({
            "error": error.code,
            "message": error.public_message,
        }, status=status or error.status_code)

    def _factor_unexpected(self):
        self._json({
            "error": "factor_library_unavailable",
            "message": "factor library is temporarily unavailable",
        }, status=503)

    def _read_factor_body(self, *, optional: bool = False) -> dict | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except (TypeError, ValueError):
            self._json({"error": "invalid_request", "message": "invalid request body"}, status=400)
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self._json({"error": "request_too_large", "message": "request body is too large"}, status=413)
            return None
        if length == 0 and optional:
            return {}
        if content_type != "application/json":
            self._json({"error": "unsupported_media_type", "message": "application/json is required"}, status=415)
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._json({"error": "invalid_request", "message": "request body is incomplete"}, status=400)
                return None
            if len(raw) > MAX_BODY_BYTES:
                self.close_connection = True
                self._json({"error": "request_too_large", "message": "request body is too large"}, status=413)
                return None
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "invalid_request", "message": "request body must be valid JSON"}, status=400)
            return None
        if not isinstance(payload, dict):
            self._json({"error": "invalid_request", "message": "request body must be an object"}, status=400)
            return None
        return payload

    @staticmethod
    def _factor_parts(path: str) -> list[str]:
        return [urllib.parse.unquote(part) for part in path[len(FACTOR_LIBRARY_PREFIX):].strip("/").split("/") if part]

    def _factor_path_error(self):
        self._json({"error": "not_found", "message": "factor library resource not found"}, status=404)

    def _factor_get(self, path: str):
        parts = self._factor_parts(path)
        if parts == ["capabilities"]:
            try:
                return self._json(get_factor_library().capabilities())
            except FactorLibraryError as error:
                return self._factor_error(error)
            except Exception:
                return self._factor_unexpected()
        if not parts:
            try:
                return self._json({
                    "schema_version": 1,
                    "items": get_factor_library().list_items(),
                })
            except FactorLibraryError as error:
                return self._factor_error(error)
            except Exception:
                return self._factor_unexpected()
        if len(parts) == 1:
            try:
                item = get_factor_library().get(parts[0])
            except FactorLibraryError as error:
                return self._factor_error(error)
            except Exception:
                return self._factor_unexpected()
            if item is None:
                return self._factor_path_error()
            return self._json(item)
        return self._factor_path_error()

    def _factor_post(self, path: str):
        parts = self._factor_parts(path)
        if parts == ["preview"]:
            body = self._read_factor_body()
            if body is None:
                return
            if set(body) != {"conditions"}:
                return self._factor_error(FactorValidationError("preview accepts only conditions"))

            def operation():
                return get_factor_library().preview(body["conditions"])

            success_status = 200
        elif not parts:
            body = self._read_factor_body()
            if body is None:
                return
            unknown = set(body) - {"name", "description", "conditions"}
            if unknown or "name" not in body:
                return self._factor_error(FactorValidationError("create accepts name, description, and conditions"))

            def operation():
                return get_factor_library().create(
                    body["name"], body.get("description", ""), body.get("conditions", {})
                )

            success_status = 201
        elif len(parts) == 2 and parts[1] == "refresh":
            body = self._read_factor_body(optional=True)
            if body is None:
                return
            if body:
                return self._factor_error(FactorValidationError("refresh does not accept a request body"))

            def operation():
                return get_factor_library().refresh(parts[0])

            success_status = 200
        else:
            return self._factor_path_error()
        try:
            result = operation()
        except FactorLibraryError as error:
            return self._factor_error(error)
        except Exception:
            return self._factor_unexpected()
        if result is None:
            return self._factor_path_error()
        return self._json(result, status=success_status)

    def _factor_put(self, path: str):
        parts = self._factor_parts(path)
        if len(parts) != 1:
            return self._factor_path_error()
        body = self._read_factor_body()
        if body is None:
            return
        unknown = set(body) - {"name", "description", "conditions"}
        if unknown or not body:
            return self._factor_error(FactorValidationError("update accepts name, description, and conditions"))
        try:
            result = get_factor_library().update(
                parts[0],
                name=body.get("name"),
                description=body.get("description"),
                conditions=body.get("conditions"),
                update_conditions="conditions" in body,
            )
        except FactorLibraryError as error:
            return self._factor_error(error)
        except Exception:
            return self._factor_unexpected()
        if result is None:
            return self._factor_path_error()
        return self._json(result)

    def _factor_delete(self, path: str):
        parts = self._factor_parts(path)
        if len(parts) != 1:
            return self._factor_path_error()
        try:
            deleted = get_factor_library().delete(parts[0])
        except FactorLibraryError as error:
            return self._factor_error(error)
        except Exception:
            return self._factor_unexpected()
        if not deleted:
            return self._factor_path_error()
        return self._json({"deleted": True, "id": parts[0]})

    def _read_manual_update_body(self) -> dict | None:
        """Read the deliberately empty JSON object accepted by the run API."""

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._manual_json(
                {"error": "unsupported_media_type", "message": "application/json is required"},
                status=415,
            )
            return None
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except (TypeError, ValueError):
            self._manual_json(
                {"error": "invalid_request", "message": "request body length is required"},
                status=400,
            )
            return None
        if length < 0 or length > MANUAL_UPDATE_MAX_BODY_BYTES:
            self.close_connection = True
            self._manual_json(
                {"error": "request_too_large", "message": "request body is too large"},
                status=413,
            )
            return None
        if length == 0:
            self._manual_json(
                {"error": "invalid_request", "message": "request body must be an empty JSON object"},
                status=400,
            )
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._manual_json(
                    {"error": "invalid_request", "message": "request body is incomplete"},
                    status=400,
                )
                return None
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_json_pairs,
            )
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            self._manual_json(
                {"error": "invalid_request", "message": "request body must be valid JSON"},
                status=400,
            )
            return None
        if not isinstance(payload, dict):
            self._manual_json(
                {"error": "invalid_request", "message": "request body must be an object"},
                status=400,
            )
            return None
        if payload:
            self._manual_json(
                {"error": "unknown_field", "message": "manual update accepts only an empty JSON object"},
                status=400,
            )
            return None
        return payload

    def _update_run(self):
        body = self._read_manual_update_body()
        if body is None:
            return
        try:
            payload = _safe_manual_update_payload(get_manual_update_controller().start())
        except Exception:
            payload = _safe_manual_update_payload({"state": "failure", "reason": "update_failed"})
        state = payload["state"]
        reason = payload["reason"]
        if reason in {"already_running", "lock_busy"}:
            status = 409
            payload["error"] = reason
        elif state in {"accepted", "running"}:
            status = 202
        elif state == "failure":
            status = 503
        else:
            status = 200
        self._manual_json(payload, status=status)

    def _update_run_stop(self):
        body = self._read_manual_update_body()
        if body is None:
            return
        try:
            payload = _safe_manual_update_payload(get_manual_update_controller().stop())
        except Exception:
            payload = _safe_manual_update_payload({"state": "failure", "reason": "update_failed"})
        self._manual_json(payload, status=200)

    def _update_run_status(self, query):
        if query:
            return self._manual_json(
                {"error": "unknown_field", "message": "update status does not accept query fields"},
                status=400,
            )
        try:
            payload = get_manual_update_controller().status()
        except Exception:
            payload = {"state": "idle", "reason": "status_unavailable"}
        self._manual_json(_safe_manual_update_payload(payload))

    def _manual_method_not_allowed(self, message="method is not supported"):
        return self._manual_json(
            {"error": "method_not_allowed", "message": message},
            status=405,
        )

    def _manual_json(self, data, status=200):
        return self._json(data, status=status, cors=False, no_store=True)

    @staticmethod
    def _is_deepseek_chat_path(path: str) -> bool:
        return path == DEEPSEEK_CHAT_PREFIX or path.startswith(DEEPSEEK_CHAT_PREFIX + "/")

    @staticmethod
    def _reject_duplicate_json_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def _read_deepseek_body(self) -> dict | None:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._json(
                {"error": "unsupported_media_type", "message": "application/json is required"},
                status=415,
                cors=False,
            )
            return None
        length_text = self.headers.get("Content-Length", "")
        try:
            length = int(length_text)
        except (TypeError, ValueError):
            self._json(
                {"error": "invalid_request", "message": "request body length is required"},
                status=400,
                cors=False,
            )
            return None
        if length < 0 or length > deepseek_chat_config.MAX_REQUEST_BODY_BYTES:
            # The oversized body has not been consumed.  Closing this HTTP
            # connection prevents a client from reusing a socket whose unread
            # bytes could otherwise be mistaken for the next request (and is
            # especially important on Windows).
            self.close_connection = True
            self._json(
                {"error": "request_too_large", "message": "request body is too large"},
                status=413,
                cors=False,
            )
            return None
        if length == 0:
            self._json(
                {"error": "invalid_request", "message": "request body must be an object"},
                status=400,
                cors=False,
            )
            return None
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._json(
                    {"error": "invalid_request", "message": "request body is incomplete"},
                    status=400,
                    cors=False,
                )
                return None
            payload = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=self._reject_duplicate_json_pairs,
            )
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            self._json(
                {"error": "invalid_request", "message": "request body must be valid JSON"},
                status=400,
                cors=False,
            )
            return None
        if not isinstance(payload, dict):
            self._json(
                {"error": "invalid_request", "message": "request body must be an object"},
                status=400,
                cors=False,
            )
            return None
        return payload

    @staticmethod
    def _query_value(query: dict, name: str, *, required: bool = True) -> str | None:
        values = query.get(name)
        if values is None:
            if required:
                raise DeepSeekChatError("invalid_request")
            return None
        if len(values) != 1 or not isinstance(values[0], str) or not values[0]:
            raise DeepSeekChatError("invalid_request")
        return values[0]

    def _deepseek_error(self, error: DeepSeekChatError):
        return self._json(
            {"error": error.code, "message": error.public_message},
            status=error.status_code,
            cors=False,
        )

    def _deepseek_get(self, path: str, query: dict):
        service = get_deepseek_chat_service()
        try:
            if path == DEEPSEEK_CHAT_STATUS_PATH:
                unknown = set(query) - {"session_id"}
                if unknown:
                    raise DeepSeekChatError("unknown_field")
                session_id = self._query_value(query, "session_id", required=False)
                result = service.status(session_id)
                status = 503 if result.get("state") == "unconfigured" else 200
                return self._json(result, status=status, cors=False)
            if path == DEEPSEEK_CHAT_POLL_PATH:
                unknown = set(query) - {"request_id", "session_id"}
                if unknown:
                    raise DeepSeekChatError("unknown_field")
                request_id = self._query_value(query, "request_id")
                session_id = self._query_value(query, "session_id", required=False)
                return self._json(service.poll(request_id, session_id), cors=False)
            if path == DEEPSEEK_CHAT_HISTORY_PATH:
                unknown = set(query) - {"session_id", "limit"}
                if unknown:
                    raise DeepSeekChatError("unknown_field")
                session_id = self._query_value(query, "session_id")
                limit_text = self._query_value(query, "limit", required=False)
                limit = None
                if limit_text is not None:
                    try:
                        limit = int(limit_text)
                    except ValueError:
                        raise DeepSeekChatError("invalid_request") from None
                return self._json(service.history(session_id, limit), cors=False)
            if path in {DEEPSEEK_CHAT_SEND_PATH, DEEPSEEK_CHAT_CANCEL_PATH}:
                return self._json(
                    {"error": "method_not_allowed", "message": "GET is not supported"},
                    status=405,
                    cors=False,
                )
            return self._json(
                {"error": "not_found", "message": "DeepSeek chat resource not found"},
                status=404,
                cors=False,
            )
        except DeepSeekChatError as error:
            return self._deepseek_error(error)
        except Exception:
            return self._deepseek_error(DeepSeekChatError("internal_error"))

    def _deepseek_post(self, path: str):
        if path not in {DEEPSEEK_CHAT_SEND_PATH, DEEPSEEK_CHAT_CANCEL_PATH}:
            return self._json(
                {"error": "method_not_allowed", "message": "POST is not supported"},
                status=405,
                cors=False,
            )
        body = self._read_deepseek_body()
        if body is None:
            return
        service = get_deepseek_chat_service()
        try:
            if path == DEEPSEEK_CHAT_SEND_PATH:
                result = service.send_payload(body)
                return self._json(result, status=202, cors=False)
            unknown = set(body) - {"session_id", "request_id"}
            if unknown or set(body) != {"session_id", "request_id"}:
                raise DeepSeekChatError("unknown_field" if unknown else "invalid_request")
            result = service.cancel(
                session_id=body["session_id"],
                request_id=body["request_id"],
            )
            return self._json(result, cors=False)
        except DeepSeekChatError as error:
            return self._deepseek_error(error)
        except Exception:
            return self._deepseek_error(DeepSeekChatError("internal_error"))

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == UPDATE_RUN_PATH:
            if parsed.query:
                return self._manual_json(
                    {"error": "unknown_field", "message": "update run does not accept query fields"},
                    status=400,
                )
            return self._update_run()
        if path == UPDATE_RUN_STOP_PATH:
            if parsed.query:
                return self._manual_json(
                    {"error": "unknown_field", "message": "update stop does not accept query fields"},
                    status=400,
                )
            return self._update_run_stop()
        if path == UPDATE_RUN_STATUS_PATH:
            return self._manual_method_not_allowed("GET is required for update status")
        if self._is_factor_library_path(path):
            return self._factor_post(path)
        if self._is_deepseek_chat_path(path):
            return self._deepseek_post(path)
        if qtrade_base_bridge.QtradeDeckHandler(self).handle_post(path):
            return
        self._json({"error": "unsupported POST method"}, status=404)

    def do_PUT(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {UPDATE_RUN_PATH, UPDATE_RUN_STATUS_PATH, UPDATE_RUN_STOP_PATH}:
            return self._manual_method_not_allowed()
        if self._is_factor_library_path(path):
            return self._factor_put(path)
        if self._is_deepseek_chat_path(path):
            return self._json(
                {"error": "method_not_allowed", "message": "PUT is not supported"},
                status=405,
                cors=False,
            )
        self._json({"error": "unsupported method"}, status=405)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {UPDATE_RUN_PATH, UPDATE_RUN_STATUS_PATH, UPDATE_RUN_STOP_PATH}:
            return self._manual_method_not_allowed()
        if self._is_factor_library_path(path):
            return self._factor_delete(path)
        if self._is_deepseek_chat_path(path):
            return self._json(
                {"error": "method_not_allowed", "message": "DELETE is not supported"},
                status=405,
                cors=False,
            )
        self._json({"error": "unsupported method"}, status=405)

    def do_PATCH(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {UPDATE_RUN_PATH, UPDATE_RUN_STATUS_PATH, UPDATE_RUN_STOP_PATH}:
            return self._manual_method_not_allowed()
        if self._is_factor_library_path(path):
            self._json({"error": "method_not_allowed", "message": "PATCH is not supported"}, status=405)
            return
        if self._is_deepseek_chat_path(path):
            return self._json(
                {"error": "method_not_allowed", "message": "PATCH is not supported"},
                status=405,
                cors=False,
            )
        self._json({"error": "unsupported method"}, status=405)

    # ---------- 路由 ----------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if path == UPDATE_RUN_PATH:
            return self._manual_method_not_allowed("POST is required for update run")
        if path == UPDATE_RUN_STOP_PATH:
            return self._manual_method_not_allowed("POST is required for update stop")
        if path == UPDATE_RUN_STATUS_PATH:
            return self._update_run_status(query)
        if self._is_factor_library_path(path):
            return self._factor_get(path)
        if self._is_deepseek_chat_path(path):
            return self._deepseek_get(path, query)
        # 复用 deepseek-harness-quant 门户/决策/控制台（同端口）
        if self._try_base_deck(path):
            return
        router = {
            "/api/health": self._health,
            "/api/update/status": self._update_status,
            "/api/symbols": self._symbols,
            "/api/backtest": self._backtest,
            "/api/ai/paper": self._ai_paper,
            "/api/auto/paper": self._auto_paper,
            "/api/training/next": self._training_next,
        }
        handler = router.get(path)

        if handler:
            return handler(query)

        if path == "/api/factors/list":
            return self._json(factors_mod.factor_inventory())

        for prefix in ("/api/kline/", "/api/info/", "/api/indicators/", "/api/factors/"):
            if path.startswith(prefix):
                symbol = path[len(prefix):]
                return self._symbol_query(prefix.strip("/").split("/")[1], symbol, query)

        if path == "/" or path == "":
            self.path = "/index.html"
        # 静态文件禁用缓存
        if path.endswith(('.css', '.js', '.html')):
            self._nocache = True
        return super().do_GET()

    def do_OPTIONS(self):
        path = urllib.parse.urlparse(self.path).path
        if path in {UPDATE_RUN_PATH, UPDATE_RUN_STATUS_PATH, UPDATE_RUN_STOP_PATH}:
            return self._manual_method_not_allowed()
        return self.send_error(501, "Unsupported method")

    def send_header(self, keyword, value):
        super().send_header(keyword, value)
        if keyword == "Content-Type" and getattr(self, '_nocache', False):
            super().send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self._nocache = False

    # ---------- 各端点 ----------

    def _health(self, query):
        live = SERVICE.live and SERVICE.live_src is not None
        self._json({
            "status": "ok",
            "symbols": len(SERVICE.scan()),
            "mode": "live" if live else "csv",
        })

    def _update_status(self, query):
        self._json(read_update_status())

    def _symbols(self, query):
        self._json(SERVICE.scan())

    def _symbol_query(self, kind: str, symbol: str, query):
        if kind == "kline":
            limit = int(query.get("limit", ["300"])[0])
            data = SERVICE.get_kline(symbol, limit)
        elif kind == "info":
            data = SERVICE.get_info(symbol)
        elif kind == "indicators":
            data = SERVICE.get_indicators(symbol)
        elif kind == "factors":
            data = SERVICE.get_factors(symbol)
        else:
            return self._json({"error": "unknown kind"}, status=400)

        if not data:
            return self._json({"error": f"symbol not found: {symbol}"}, status=404)
        self._json(data)

    def _backtest(self, query):
        try:
            symbol = query.get("symbol", [""])[0]
            strategy = query.get("strategy", ["dual_ma"])[0]
            capital = float(query.get("capital", ["100000"])[0])
            commission = float(query.get("commission", ["0.0003"])[0])
            stop_loss = float(query.get("stop_loss", ["0.05"])[0])
            take_profit = float(query.get("take_profit", ["0.15"])[0])
            factors = None
            weights = None
            fs = query.get("factors", [None])[0]
            ws = query.get("weights", [None])[0]
            if fs:
                factors = [x.strip() for x in fs.split(",") if x.strip()]
                if ws:
                    wl = [float(x) for x in ws.split(",") if x.strip()]
                    weights = {k: wl[i] for i, k in enumerate(factors) if i < len(wl)}
        except ValueError:
            return self._json({"error": "invalid parameter"}, status=400)

        result = SERVICE.run_backtest(symbol, strategy, capital, commission, stop_loss, take_profit,
                                      factors=factors, weights=weights)
        self._json(result)

    def _ai_paper(self, query):
        """AI 信号模拟盘：status=查看, sync=按信号调仓。"""
        if AI_PAPER is None:
            return self._json({"error": "AI 模拟盘未初始化"}, status=500)
        action = query.get("action", ["status"])[0]

        if action == "sync":
            # DSA 信号源已移除，AI 模拟盘保留查看/估值，不再同步外部 AI 信号
            return self._json({"error": "DSA 信号已移除，AI 模拟盘暂不支持同步"}, status=400)
        elif action == "mark":
            def price_fn(sym):
                return self._latest_close(sym)

            status = AI_PAPER.mark_prices(price_fn)
        else:
            status = AI_PAPER.status()
        self._json(status)

    def _latest_close(self, symbol: str) -> float | None:
        """取某股最新收盘价（实时优先，回退 CSV）。"""
        kline = SERVICE.get_kline(symbol, limit=3)
        return kline[-1]["close"] if kline else None

    # ---------- 自动模拟盘 ----------

    def _auto_paper(self, query):
        """自动模拟盘：status=查看, run=立即跑一轮, toggle=启动/暂停, reset=清仓重置, setmode=切信号源。"""
        if AUTO_PAPER is None:
            return self._json({"error": "自动模拟盘未初始化"}, status=500)
        action = query.get("action", ["status"])[0]

        if action == "run":
            status = AUTO_PAPER.cycle(SERVICE)
        elif action == "toggle":
            status = AUTO_PAPER.toggle(SERVICE)
        elif action == "reset":
            status = AUTO_PAPER.reset(SERVICE)
        elif action == "setmode":
            try:
                status = AUTO_PAPER.set_mode(query.get("mode", ["sequoia_oneil"])[0], SERVICE)
            except ValueError as e:
                return self._json({"error": str(e)}, status=400)
        else:
            status = AUTO_PAPER.status(SERVICE)
        self._json(status)

    # ---------- K线训练营 ----------

    def _training_next(self, query):
        """随机抽一道看图猜涨跌题：已知 lookback 根K线，猜未来 horizon 根是涨是跌。

        脱敏策略：
          - 不返回股票代码/名称
          - 时间轴用假日期（2020-01-01 起连续交易日），防止凭记忆猜题
        """
        import random as _random

        try:
            lookback = int(query.get("lookback", ["60"])[0])
            horizon = int(query.get("horizon", ["5"])[0])
        except ValueError:
            return self._json({"error": "invalid parameter"}, status=400)
        lookback = max(20, min(lookback, 250))
        horizon = max(1, min(horizon, 160))

        # 随机挑一只数据足够长的股票（需额外留 60 根预热，保证均线/形态有上下文）
        # 平盘题（|涨跌幅|<0.5%）无训练意义，自动重抽，最多 20 次
        df = None
        for _ in range(20):
            symbols = SERVICE.scan()
            _random.shuffle(symbols)
            for sym in symbols:
                d = SERVICE._resolve_df(sym)
                if d is not None and len(d) >= lookback + horizon + 60:
                    df = d
                    break
            if df is None:
                return self._json({"error": "没有足够长的K线数据"}, status=500)

            # 随机起点：保证 known 段之前至少 60 根上下文
            start = _random.randint(60, len(df) - lookback - horizon)
            b = float(df["close"].iloc[start + lookback - 1])
            e = float(df["close"].iloc[start + lookback + horizon - 1])
            if abs(e / b - 1) * 100 >= 0.5:
                break
            df = None  # 平盘，重抽
        if df is None:
            return self._json({"error": "无法生成有效题目"}, status=500)

        known_raw, future_raw = [], []
        for i in range(start, start + lookback):
            known_raw.append(df.iloc[i])
        for i in range(start + lookback, start + lookback + horizon):
            future_raw.append(df.iloc[i])

        # 假时间轴：2020-01-01 起，跳过周末（交易日连续）
        # 注意：future 的 time 必须从 known 末尾接续，否则图表无法追加
        base = pd.Timestamp("2020-01-01")
        day = pd.offsets.BDay()
        def kline_rows(rows, offset):
            out = []
            for j, row in enumerate(rows):
                dt = base + (offset + j) * day
                out.append({
                    "time": int(dt.timestamp()),
                    "open": round(float(row["open"]), 2),
                    "high": round(float(row["high"]), 2),
                    "low": round(float(row["low"]), 2),
                    "close": round(float(row["close"]), 2),
                    "volume": int(row["volume"]) if not pd.isna(row["volume"]) else 0,
                })
            return out

        known = kline_rows(known_raw, 0)
        future = kline_rows(future_raw, lookback)

        # 答案：未来 horizon 根收盘 vs 已知段末收盘
        base_close = known[-1]["close"]
        end_close = future[-1]["close"]
        change_pct = (end_close / base_close - 1) * 100
        answer = "up" if end_close >= base_close else "down"

        self._json({
            "qid": f"q{_random.randint(100000, 999999)}",
            "lookback": lookback,
            "horizon": horizon,
            "known": known,
            "future": future,
            "answer": answer,
            "change_pct": round(change_pct, 2),
        })

    # ---------- 工具 ----------

    def _json(self, data, status=200, *, cors=True, no_store=False):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        if self.close_connection:
            self.send_header("Connection", "close")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


# ============================================================================
# 入口
# ============================================================================

def find_data_dir(explicit: str | None) -> Path:
    if explicit and Path(explicit).exists():
        return Path(explicit)

    # Prefer paths relative to this file (portable), then common absolute
    # locations as a last resort.
    here = Path(__file__).resolve().parent
    candidates = [
        here / "data" / "cache",
        here.parent / "data" / "cache",
        Path("data/cache"),
        Path("../data/cache"),
        Path.home() / "qtrade" / "data" / "cache",
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path(explicit or "data/cache")


def port_in_use(port: int) -> bool:
    """探测端口是否被占用（Windows SO_REUSEADDR 下 bind 不报错但连接可能被抢）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except (OSError, socket.timeout):
            return False


def _ensure_base_harness():
    """Qtrade 启动时自动把底座 HARNESS(3081，带量化桥接插件)带上。已运行则跳过。"""
    qtrade_base_bridge.ensure_harness()


def _maybe_auto_update():
    """Qtrade 启动时自动增量更新（一天最多一次；全量回填完成后才启用）。"""
    qtrade_base_bridge.maybe_auto_update()


def main():
    global SERVICE, STATIC_DIR, FACTOR_LIBRARY, FACTOR_LIBRARY_FILE

    # ---- 修复 Windows GBK 编码问题（保留 write_through 避免缓冲丢失输出） ----
    # pythonw.exe 下无控制台，sys.stdout 为 None，需判空。
    # 只在真正运行服务时执行，避免 import 时破坏 pytest 输出捕获。
    if sys.platform == "win32":
        if sys.stdout is not None:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)
        if sys.stderr is not None:
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", write_through=True)

    parser = argparse.ArgumentParser(description="QTrade Desktop Trading Terminal")
    parser.add_argument("--data-dir", default=None, help="股票数据 CSV 缓存目录（回退用）")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 服务端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--csv-only", action="store_true", help="只用本地 CSV，不连实时接口")
    parser.add_argument(
        "--factor-library-file",
        default=None,
        help="因子方案 JSON 存储路径（优先于 QTRADE_FACTOR_LIBRARY_FILE）",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="QTrade 更新状态与诊断日志目录（由桌面运行时固定传入）",
    )
    parser.add_argument("--single-instance", action="store_true",
                        help="后台服务模式：端口被占用时直接退出（不换端口），防止重复进程")
    args = parser.parse_args()

    configure_update_state_dir(args.state_dir)
    data_dir = find_data_dir(args.data_dir)
    live = not args.csv_only
    FACTOR_LIBRARY_FILE = resolve_factor_library_path(args.factor_library_file)
    FACTOR_LIBRARY = FactorLibrary(FACTOR_LIBRARY_FILE, qtrade_base_bridge.base_dir())

    # 后台服务模式：已有实例在跑就直接退出，防止重复进程
    if args.single_instance and port_in_use(args.port):
        print(f"ℹ️  端口 {args.port} 已有 QTrade 服务在运行，本实例退出（--single-instance）")
        return

    SERVICE = DataService(data_dir, live=live)
    symbols = SERVICE.scan()

    # 自动带上底座 HARNESS(3081)
    _ensure_base_harness()

    # 自动增量更新（全量回填完成后才启动，一天最多一次）
    _maybe_auto_update()

    # 初始化
    global AI_PAPER, AUTO_PAPER
    AI_PAPER = AiPaperTrader()
    AUTO_PAPER = EngineAutoPaperTrader()

    # 自动模拟盘：后台线程定时按独立信号引擎自动买卖
    def _auto_paper_loop():
        time.sleep(3)  # 等待服务就绪
        while True:
            try:
                AUTO_PAPER.cycle(SERVICE)
            except Exception as e:
                AUTO_PAPER.state["last_error"] = str(e)
            time.sleep(AutoPaperTrader.CYCLE_SECONDS)

    threading.Thread(target=_auto_paper_loop, daemon=True, name="auto-paper").start()
    st = AUTO_PAPER.status()
    print(f"⚙️  自动模拟盘: {'运行中' if st['running'] else '已暂停'}"
          f" | 总资产 ¥{st['total']:,.0f} | 持仓 {st['position_count']}/{st['max_positions']}"
          f" | 轮询 {AutoPaperTrader.CYCLE_SECONDS}s")
    # 实时模式探测
    if live:
        if TencentLiveSource.available():
            print("📡 数据模式: 实时（腾讯接口，仅内存缓存，不落盘）")
        else:
            print("⚠️  实时接口不可用，回退本地 CSV")
            SERVICE.live = False

    STATIC_DIR = Path(__file__).parent / "static"
    if not STATIC_DIR.exists():
        STATIC_DIR = Path("static")

    # 端口被占用时自动顺延
    port = args.port
    server = None
    while port < args.port + 50:
        if port_in_use(port):
            port += 1
            continue
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), APIHandler)
            break
        except OSError:
            port += 1
    if server is None:
        print(f"❌ 端口 {args.port}~{args.port + 49} 均被占用，无法启动")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    if port != args.port:
        print(f"⚠️  端口 {args.port} 被占用，已自动改用端口 {port}")

    print("📊 QTrade Desktop")
    print(f"📂 CSV 回退目录: {data_dir}")
    print(f"📈 股票池: {len(symbols)} 只")
    print(f"🌐 交易终端: {url}")
    print(f"📡 健康检查: {url}/api/health")

    if not args.no_browser:
        webbrowser.open(url)

    print("✅ 服务运行中，按 Ctrl+C 退出...")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 再见!")
        server.shutdown()
    finally:
        if DEEPSEEK_CHAT_SERVICE is not None:
            DEEPSEEK_CHAT_SERVICE.close()
        if MANUAL_UPDATE_CONTROLLER is not None:
            MANUAL_UPDATE_CONTROLLER.stop()


if __name__ == "__main__":
    main()
