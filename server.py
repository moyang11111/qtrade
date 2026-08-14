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

# ---- 修复 Windows GBK 编码问题（保留 write_through 避免缓冲丢失输出） ----
# pythonw.exe 下无控制台，sys.stdout 为 None，需判空
if sys.platform == "win32":
    if sys.stdout is not None:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)
    if sys.stderr is not None:
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", write_through=True)

import json
import time
import os
import sqlite3
import socket
import argparse
import urllib.request
import urllib.parse
import webbrowser
from pathlib import Path
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pandas as pd
import numpy as np

# DSA 分析引擎（可选依赖，import 失败则降级为内置分析）
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent / "daily_stock_analysis"))
    from src.stock_analyzer import StockTrendAnalyzer
    _DSA_AVAILABLE = True
except Exception:
    StockTrendAnalyzer = None
    _DSA_AVAILABLE = False

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

    # ---------- 数据源解析 ----------

    def _resolve_df(self, symbol: str, count: int = 320) -> pd.DataFrame | None:
        """实时优先拉取，失败回退内存缓存/CSV；仅内存缓存，不落盘。"""
        if self.live and self.live_src:
            try:
                df = self.live_src.fetch_kline(symbol, count)  # 内部 TTL 缓存
                self._df_cache[symbol] = df
                return df
            except Exception:
                pass  # 回退
        if symbol in self._df_cache:
            return self._df_cache[symbol]
        return self._load_csv(symbol)

    def _load_csv(self, symbol: str) -> pd.DataFrame | None:
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

    # ---------- 股票列表 ----------

    def scan(self) -> list[str]:
        """股票列表：CSV 文件名（存在时）∪ 内置常用池。"""
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
        if self.live and self.live_src:
            try:
                quote = self.live_src.fetch_quote(symbol)
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

    # ---------- 回测 ----------

    def run_backtest(self, symbol: str, strategy: str, capital: float,
                     commission: float, stop_loss: float, take_profit: float) -> dict:
        df = self._resolve_df(symbol)
        if df is None:
            return {"error": f"无法加载 {symbol}"}

        close = df["close"]
        if strategy == "rsi_layered":
            # RSI 分层买入策略：专用模拟器（逐层建仓，与信号模型不同）
            result = self._simulate_rsi_layered(df, capital, commission)
        else:
            signals = self._generate_signals(df, strategy)
            result = self._simulate(df, signals, capital, commission, stop_loss, take_profit)
        result.update({"symbol": symbol, "strategy": strategy})
        return result

    @staticmethod
    def _generate_signals(df: pd.DataFrame, strategy: str) -> pd.Series:
        close = df["close"]
        high, low, vol = df["high"], df["low"], df["volume"]
        signals = pd.Series(0, index=df.index)

        if strategy == "dual_ma":
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

        return signals

    @staticmethod
    def _simulate(df, signals, capital, commission, stop_loss, take_profit) -> dict:
        close = df["close"]
        trades, equity = [], [capital]
        position, entry_price, cash = 0, 0, capital

        for i in range(1, len(df)):
            sig, price = signals.iloc[i], float(close.iloc[i])
            date = str(df.index[i])[:10]

            if sig == 1 and position == 0:
                position = cash * 0.95 / price
                entry_price = price
                cash -= position * price * (1 + commission)
                trades.append({"type": "buy", "price": round(price, 2), "date": date, "pnl": 0})

            elif sig == -1 and position > 0:
                pnl = round((price / entry_price - 1) * 100, 2)
                cash += position * price * (1 - commission)
                trades.append({"type": "sell", "price": round(price, 2), "date": date, "pnl": pnl})
                position, entry_price = 0, 0

            elif position > 0:
                pnl_pct = price / entry_price - 1
                if pnl_pct <= -stop_loss:
                    cash += position * price * (1 - commission)
                    trades.append({"type": "stop_loss", "price": round(price, 2), "date": date, "pnl": round(pnl_pct * 100, 2)})
                    position, entry_price = 0, 0
                elif pnl_pct >= take_profit:
                    cash += position * price * (1 - commission)
                    trades.append({"type": "take_profit", "price": round(price, 2), "date": date, "pnl": round(pnl_pct * 100, 2)})
                    position, entry_price = 0, 0

            equity.append(round(cash + position * price, 2))

        if position > 0:
            final_price = float(close.iloc[-1])
            cash += position * final_price * (1 - commission)
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
        }

    @staticmethod
    def _simulate_rsi_layered(df: pd.DataFrame, capital: float,
                              commission: float = 0.0003) -> dict:
        """
        RSI 分层买入策略（网格建仓）：
          - RSI14 < 25   → 买入 1 层仓（1 层 = 10% 初始资金）
          - 此后每从上次买入价下跌 5% → 买入 2 层仓（20%）
          - 总仓位 ≤ 80%
          - RSI14 > 70   → 全部清仓
        """
        close = df["close"]
        r14 = rsi(close, 14)
        layer_pct = 0.10          # 1 层 = 10% 总资金
        max_pos = 0.80            # 最大 80% 仓位
        total_ref = capital       # 层仓基准 = 初始资金

        cash = capital
        shares = 0.0
        last_buy_price = None
        in_position = False
        trades = []
        equity = []

        for i in range(len(df)):
            price = float(close.iloc[i])
            date = str(df.index[i])[:10]
            r = r14.iloc[i]

            # —— 卖出：RSI > 70 清仓 ——
            if in_position and not pd.isna(r) and r > 70:
                pnl_pct = round((price / last_buy_price - 1) * 100, 2) if last_buy_price else 0
                cash += shares * price * (1 - commission)
                trades.append({
                    "type": "sell", "price": round(price, 2), "date": date,
                    "pnl": pnl_pct, "shares": round(shares, 0),
                    "reason": f"RSI={r:.1f}>70 清仓",
                })
                shares = 0.0
                in_position = False
                last_buy_price = None

            # —— 首次建仓：RSI < 25，买入 1 层 ——
            if not in_position and not pd.isna(r) and r < 25:
                amount = total_ref * layer_pct
                buy_shares = amount / price
                cost = buy_shares * price * (1 + commission)
                if cost <= cash:
                    cash -= cost
                    shares = buy_shares
                    in_position = True
                    last_buy_price = price
                    trades.append({
                        "type": "buy", "price": round(price, 2), "date": date,
                        "pnl": 0, "shares": round(buy_shares, 0),
                        "reason": f"RSI={r:.1f}<25 首仓1层",
                    })

            # —— 加仓：每跌 5% 买入 2 层（最大仓位 80%）——
            elif in_position and last_buy_price and not pd.isna(r):
                pos_pct = shares * price / total_ref
                if price <= last_buy_price * 0.95 and pos_pct + 2 * layer_pct <= max_pos:
                    amount = total_ref * 2 * layer_pct
                    buy_shares = amount / price
                    cost = buy_shares * price * (1 + commission)
                    if cost <= cash:
                        cash -= cost
                        shares += buy_shares
                        last_buy_price = price   # 以新加仓价为基准
                        trades.append({
                            "type": "buy", "price": round(price, 2), "date": date,
                            "pnl": 0, "shares": round(buy_shares, 0),
                            "reason": f"跌5%加仓2层 (仓位{pos_pct*100:.0f}%)",
                        })

            equity.append(round(cash + shares * price, 2))

        # —— 期末清仓 ——
        if in_position:
            final_price = float(close.iloc[-1])
            pnl_pct = round((final_price / last_buy_price - 1) * 100, 2) if last_buy_price else 0
            cash += shares * final_price * (1 - commission)
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
        }


# ============================================================================
# DSA 集成：AI 决策信号 + AI 模拟盘
# ============================================================================

class DsaSignalReader:
    """只读 DSA（daily_stock_analysis）的 AI 决策信号。

    数据源：DSA 桌面版的 sqlite 数据库（decision_signals 表）。
    只读打开（mode=ro），不碰 DSA 运行，DSA 未装/未跑时返回空。
    """

    DB_CANDIDATES = [
        Path.home() / "AppData" / "Roaming" / "daily-stock-analysis-desktop" / "data" / "stock_analysis.db",
        Path("data/stock_analysis.db"),
        Path("../daily_stock_analysis/data/stock_analysis.db"),
    ]

    def __init__(self):
        self.db_path = self._find_db()

    def _find_db(self):
        for p in self.DB_CANDIDATES:
            if p.exists():
                return p
        return None

    def available(self) -> bool:
        return self.db_path is not None

    def get_views(self, symbol: str | None = None, limit: int = 30) -> list[dict]:
        """读取活跃 AI 决策信号。symbol 为空返回全部最近 limit 条。"""
        if not self.db_path:
            return []
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            sql = (
                "SELECT stock_code, stock_name, action, action_label, score, confidence, "
                "horizon, entry_low, entry_high, stop_loss, target_price, reason, "
                "risk_summary, catalyst_summary, created_at "
                "FROM decision_signals WHERE status='active'"
            )
            params: list = []
            if symbol:
                sql += " AND stock_code=?"
                params.append(symbol)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)
            rows = cur.execute(sql, params).fetchall()
            conn.close()
            out = []
            for r in rows:
                out.append({
                    "symbol": r["stock_code"],
                    "name": r["stock_name"] or "",
                    "action": r["action"],
                    "action_label": r["action_label"] or r["action"],
                    "score": r["score"],
                    "confidence": r["confidence"],
                    "horizon": r["horizon"],
                    "entry_low": r["entry_low"],
                    "entry_high": r["entry_high"],
                    "stop_loss": r["stop_loss"],
                    "target_price": r["target_price"],
                    "reason": (r["reason"] or "")[:200],
                    "risk": (r["risk_summary"] or "")[:120],
                    "catalyst": (r["catalyst_summary"] or "")[:120],
                    "time": str(r["created_at"])[:19],
                })
            return out
        except Exception:
            return []

    def latest_for(self, symbol: str) -> dict | None:
        views = self.get_views(symbol, limit=1)
        return views[0] if views else None


class AiPaperTrader:
    """AI 信号模拟盘：按 DSA 决策信号在模拟资金上买卖。

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


# ============================================================================
# HTTP 服务
# ============================================================================

SERVICE: DataService = None
STATIC_DIR: Path = None
DSA_READER: DsaSignalReader = None
AI_PAPER: AiPaperTrader = None


class APIHandler(SimpleHTTPRequestHandler):
    """静态文件 + JSON API。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format, *args):  # 静默访问日志
        pass

    # ---------- 路由 ----------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        router = {
            "/api/health": self._health,
            "/api/symbols": self._symbols,
            "/api/backtest": self._backtest,
            "/api/ai/views": self._ai_views,
            "/api/ai/paper": self._ai_paper,
            "/api/training/next": self._training_next,
        }
        handler = router.get(path)

        if handler:
            return handler(query)

        for prefix in ("/api/kline/", "/api/info/", "/api/indicators/"):
            if path.startswith(prefix):
                symbol = path[len(prefix):]
                return self._symbol_query(prefix.strip("/").split("/")[1], symbol, query)

        if path.startswith("/api/ai/views/"):
            return self._ai_views(query, symbol=path[len("/api/ai/views/"):])

        if path.startswith("/api/dsa/analyze/"):
            symbol = path[len("/api/dsa/analyze/"):]
            return self._dsa_analyze(symbol, query)

        if path == "/" or path == "":
            self.path = "/index.html"
        # 静态文件禁用缓存
        if path.endswith(('.css', '.js', '.html')):
            self._nocache = True
        return super().do_GET()

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
        except ValueError:
            return self._json({"error": "invalid parameter"}, status=400)

        result = SERVICE.run_backtest(symbol, strategy, capital, commission, stop_loss, take_profit)
        self._json(result)

    # ---------- DSA 集成 ----------

    def _dsa_analyze(self, symbol: str, query):
        """调用 DSA StockTrendAnalyzer 分析单只股票。"""
        if not _DSA_AVAILABLE:
            return self._json({"error": "DSA 分析引擎不可用", "dsa_available": False}, status=500)

        # 获取 K 线数据
        kline_list = SERVICE.get_kline(symbol, limit=120)
        if not kline_list:
            return self._json({"error": f"未找到 {symbol} 的K线数据"}, status=404)

        # 转为 DataFrame（保持小写列名，DSA 引擎需要）
        df = pd.DataFrame(kline_list)
        df["date"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("date", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]]

        try:
            analyzer = StockTrendAnalyzer()
            result = analyzer.analyze(df, symbol)
        except Exception as e:
            return self._json({"error": f"DSA 分析失败: {e}"}, status=500)

        # 转为可 JSON 序列化的 dict
        self._json({
            "dsa_available": True,
            "symbol": symbol,
            "trend_status": result.trend_status.name if hasattr(result.trend_status, 'name') else str(result.trend_status),
            "trend_strength": result.trend_strength,
            "ma_alignment": result.ma_alignment,
            "ma5": result.ma5, "ma10": result.ma10, "ma20": result.ma20, "ma60": result.ma60,
            "current_price": result.current_price,
            "bias_ma5": result.bias_ma5, "bias_ma10": result.bias_ma10, "bias_ma20": result.bias_ma20,
            "volume_status": result.volume_status.name if hasattr(result.volume_status, 'name') else str(result.volume_status),
            "volume_ratio_5d": result.volume_ratio_5d,
            "volume_trend": result.volume_trend,
            "macd_dif": result.macd_dif, "macd_dea": result.macd_dea, "macd_bar": result.macd_bar,
            "macd_status": result.macd_status.name if hasattr(result.macd_status, 'name') else str(result.macd_status),
            "macd_signal": result.macd_signal,
            "rsi_6": result.rsi_6, "rsi_12": result.rsi_12, "rsi_24": result.rsi_24,
            "rsi_status": result.rsi_status.name if hasattr(result.rsi_status, 'name') else str(result.rsi_status),
            "rsi_signal": result.rsi_signal,
            "buy_signal": result.buy_signal.name if hasattr(result.buy_signal, 'name') else str(result.buy_signal),
            "signal_score": result.signal_score,
            "signal_reasons": result.signal_reasons,
            "risk_factors": result.risk_factors,
            "support_levels": result.support_levels,
            "resistance_levels": result.resistance_levels,
        })

    def _ai_views(self, query, symbol: str | None = None):
        """返回 DSA 的 AI 决策信号。"""
        if DSA_READER is None:
            return self._json({"error": "DSA 集成未初始化"}, status=500)
        if not DSA_READER.available():
            return self._json({
                "available": False,
                "error": "未找到 DSA 数据库（先运行 DSA 桌面版并分析几只股票）",
                "views": [],
            })
        limit = int(query.get("limit", ["20"])[0])
        views = DSA_READER.get_views(symbol=symbol, limit=limit)
        self._json({
            "available": True,
            "db": str(DSA_READER.db_path),
            "count": len(views),
            "views": views,
        })

    def _ai_paper(self, query):
        """AI 信号模拟盘：status=查看, sync=按信号调仓。"""
        if AI_PAPER is None:
            return self._json({"error": "AI 模拟盘未初始化"}, status=500)
        action = query.get("action", ["status"])[0]

        if action == "sync" and DSA_READER is not None and DSA_READER.available():
            signals = DSA_READER.get_views(limit=50)
            price_fn = lambda sym: self._latest_close(sym)
            status = AI_PAPER.sync(signals, price_fn)
        elif action == "mark":
            price_fn = lambda sym: self._latest_close(sym)
            status = AI_PAPER.mark_prices(price_fn)
        else:
            status = AI_PAPER.status()
        self._json(status)

    def _latest_close(self, symbol: str) -> float | None:
        """取某股最新收盘价（实时优先，回退 CSV）。"""
        kline = SERVICE.get_kline(symbol, limit=3)
        return kline[-1]["close"] if kline else None

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
        chosen = None
        for _ in range(20):
            symbols = SERVICE.scan()
            _random.shuffle(symbols)
            for sym in symbols:
                d = SERVICE._resolve_df(sym)
                if d is not None and len(d) >= lookback + horizon + 60:
                    df, chosen = d, sym
                    break
            if df is None:
                return self._json({"error": "没有足够长的K线数据"}, status=500)

            # 随机起点：保证 known 段之前至少 60 根上下文
            start = _random.randint(60, len(df) - lookback - horizon)
            idx = df.index
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

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ============================================================================
# 入口
# ============================================================================

def find_data_dir(explicit: str | None) -> Path:
    if explicit and Path(explicit).exists():
        return Path(explicit)
    candidates = [
        Path("C:/Users/ASUS/qtrade/data/cache"),
        Path("../data/cache"),
        Path("data/cache"),
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


def main():
    global SERVICE, STATIC_DIR

    parser = argparse.ArgumentParser(description="QTrade Desktop Trading Terminal")
    parser.add_argument("--data-dir", default=None, help="股票数据 CSV 缓存目录（回退用）")
    parser.add_argument("--port", type=int, default=8765, help="HTTP 服务端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--csv-only", action="store_true", help="只用本地 CSV，不连实时接口")
    args = parser.parse_args()

    data_dir = find_data_dir(args.data_dir)
    live = not args.csv_only

    SERVICE = DataService(data_dir, live=live)
    symbols = SERVICE.scan()

    # DSA 集成初始化
    global DSA_READER, AI_PAPER
    DSA_READER = DsaSignalReader()
    AI_PAPER = AiPaperTrader()
    if DSA_READER.available():
        print(f"🤖 DSA 集成: 已连接信号库 ({DSA_READER.db_path})")
    else:
        print("🤖 DSA 集成: 未找到 DSA 数据库（AI 观点功能暂不可用）")

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
            server = HTTPServer(("127.0.0.1", port), APIHandler)
            break
        except OSError:
            port += 1
    if server is None:
        print(f"❌ 端口 {args.port}~{args.port + 49} 均被占用，无法启动")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    if port != args.port:
        print(f"⚠️  端口 {args.port} 被占用，已自动改用端口 {port}")

    print(f"📊 QTrade Desktop")
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


if __name__ == "__main__":
    main()
