"""百度/腾讯实时行情数据源 — 用于模拟盘。

使用腾讯财经 HTTP API（qt.gtimg.cn）获取实时报价，
按固定间隔轮询，生成 Tick 事件推送。

优点：免费、无需注册、不封IP、支持批量查询。
"""

from __future__ import annotations

import threading
import time
import urllib.request
from datetime import datetime
from typing import Callable, Optional, List

from loguru import logger

from qtrade.live_trading.data_feed import RealtimeDataFeed, Tick

# 腾讯财经实时行情 API
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="


def _normalize_symbol(symbol: str) -> str:
    """将 6 位代码转为腾讯格式 sh600519 / sz000001 / bj920xxx"""
    symbol = symbol.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    if symbol.startswith("6") or symbol.startswith("9"):
        return f"sh{symbol}"
    elif symbol.startswith("8") or symbol.startswith("4"):
        return f"bj{symbol}"
    else:
        return f"sz{symbol}"


def _denormalize(qt_symbol: str) -> str:
    """腾讯格式 → 6位代码"""
    return qt_symbol[2:]


def _parse_tencent_quote(raw: str) -> dict[str, Tick]:
    """解析腾讯财经实时行情返回。

    返回: {6位代码: Tick}
    """
    ticks = {}
    for line in raw.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        qt_key = line.split("=")[0].split("_")[-1]  # sh600519
        code = _denormalize(qt_key)
        vals = line.split('"')[1].split("~")
        if len(vals) < 40:
            continue
        try:
            price = float(vals[3]) if vals[3] else 0
            if price <= 0:
                continue  # 停牌或无效

            ticks[code] = Tick(
                symbol=code,
                timestamp=datetime.now(),
                price=price,
                volume=float(vals[6]) if vals[6] else 0,   # 成交量(手)
                bid=float(vals[9]) if vals[9] else None,    # 买一价
                ask=float(vals[19]) if vals[19] else None,  # 卖一价
                bid_size=float(vals[10]) if vals[10] else None,
                ask_size=float(vals[20]) if vals[20] else None,
            )
        except (ValueError, IndexError):
            continue
    return ticks


class BaiduQuoteFeed(RealtimeDataFeed):
    """基于腾讯财经的轮询式实时行情。

    非交易时间返回的价格为最新收盘价（仍可用于测试模拟盘逻辑）。
    """

    def __init__(self, poll_interval: float = 5.0):
        """
        Args:
            poll_interval: 轮询间隔（秒），默认 5 秒
        """
        self.poll_interval = poll_interval
        self._symbols: set[str] = set()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tick_callbacks: list[Callable] = []
        self._bar_callbacks: list[Callable] = []
        self._latest_prices: dict[str, float] = {}
        self._connected = False

    # ── 连接管理 ──

    def connect(self) -> bool:
        if self._running:
            return True
        self._running = True
        self._connected = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("BaiduQuoteFeed connected (polling {:.1f}s)", self.poll_interval)
        return True

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BaiduQuoteFeed disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # ── 订阅 ──

    def subscribe(self, symbols: List[str]) -> None:
        self._symbols.update(symbols)
        logger.info("Subscribed to {} symbols", len(self._symbols))

    def unsubscribe(self, symbols: List[str]) -> None:
        self._symbols.difference_update(symbols)

    # ── 回调 ──

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    def on_bar(self, callback) -> None:
        self._bar_callbacks.append(callback)

    # ── 便捷方法 ──

    def get_price(self, symbol: str) -> Optional[float]:
        """获取某只股票的最新价格（可能缓存了最后一次轮询的值）。"""
        return self._latest_prices.get(symbol)

    # ── 内部轮询逻辑 ──

    def _poll_loop(self):
        """后台线程：定时拉取腾讯行情，生成 Tick。"""
        while self._running:
            try:
                if self._symbols:
                    ticks = self._fetch_quotes()
                    for code, tick in ticks.items():
                        self._latest_prices[code] = tick.price
                        for cb in self._tick_callbacks:
                            try:
                                cb(tick)
                            except Exception:
                                pass
            except Exception as e:
                logger.warning("Quote poll error: %s", e)

            time.sleep(self.poll_interval)

    def _fetch_quotes(self) -> dict[str, Tick]:
        """拉取腾讯行情，分批请求（每批最多50只）。"""
        all_ticks = {}
        symbols_list = list(self._symbols)

        for i in range(0, len(symbols_list), 50):
            batch = symbols_list[i:i+50]
            qt_symbols = [_normalize_symbol(s) for s in batch]
            url = TENCENT_QUOTE_URL + ",".join(qt_symbols)

            req = urllib.request.Request(url)
            req.add_header("User-Agent", "Mozilla/5.0")
            try:
                resp = urllib.request.urlopen(req, timeout=10)
                raw = resp.read().decode("gbk")
                ticks = _parse_tencent_quote(raw)
                all_ticks.update(ticks)
            except Exception as e:
                logger.warning("Tencent quote batch {} failed: {}", i//50, e)

        return all_ticks
