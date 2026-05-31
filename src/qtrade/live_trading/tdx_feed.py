"""通达信（TDX）实时行情 — 基于 pytdx 原生 TCP 协议。

相比百度/腾讯 HTTP API 的优势：
  - 五档买卖盘口（bid1-5, ask1-5, 量+价）
  - 成交量、成交额、换手率、量比
  - 涨停价、跌停价
  - 46 个字段，远多于腾讯的 ~40 个

用法：替代 BaiduQuoteFeed，速度更快、字段更全。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Optional, List

from loguru import logger

from qtrade.live_trading.data_feed import RealtimeDataFeed, Tick

# 通达信行情服务器（快速连接：只试前5个，5秒超时）
TDX_SERVERS = [
    ("119.147.212.81", 7709),
    ("120.76.152.87", 7709),
    ("124.71.223.19", 7709),
    ("119.29.51.30", 7709),
    ("47.92.127.106", 7709),
]


def _normalize_tdx(symbol: str) -> tuple[int, str]:
    """6位代码 → (market, code)。market: 0=深圳, 1=上海"""
    s = str(symbol).strip()
    if s.startswith(("6", "9")):
        return 1, s
    return 0, s


class TdxQuoteFeed(RealtimeDataFeed):
    """基于通达信 TCP 协议的实时行情推送。

    非交易时间返回的价格为上一交易日收盘价（仍可用于测试）。
    """

    def __init__(self, poll_interval: float = 3.0):
        """
        Args:
            poll_interval: 轮询间隔（秒），默认 3 秒（TDX 协议轻量，可以更快）
        """
        self.poll_interval = poll_interval
        self._symbols: list[str] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._tick_callbacks: list[Callable] = []
        self._bar_callbacks: list[Callable] = []
        self._latest_prices: dict[str, float] = {}
        self._connected = False
        self._api = None

    # ── 连接管理 ──

    def connect(self) -> bool:
        if self._running:
            return True

        # 延迟导入 pytdx（避免未安装时崩溃）
        try:
            from pytdx.hq import TdxHq_API
        except ImportError:
            raise ImportError("TdxQuoteFeed 需要 pytdx 库。运行: pip install pytdx")

        # 断开旧连接
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass

        self._api = TdxHq_API()
        connected = False
        for ip, port in TDX_SERVERS:
            try:
                if self._api.connect(ip, port, time_out=3):
                    logger.info("TDX connected to {}:{}", ip, port)
                    connected = True
                    break
            except Exception:
                continue

        if not connected:
            self._api = None
            return False

        self._running = True
        self._connected = True
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._thread.start()
        return True

    def disconnect(self) -> None:
        self._running = False
        self._connected = False
        if self._thread:
            self._thread.join(timeout=5)
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._api = None
        logger.info("TDX feed disconnected")

    def is_connected(self) -> bool:
        return self._connected

    # ── 订阅 ──

    def subscribe(self, symbols: List[str]) -> None:
        self._symbols = list(symbols)
        logger.info("TDX subscribed to {} symbols", len(self._symbols))

    def unsubscribe(self, symbols: List[str]) -> None:
        self._symbols = [s for s in self._symbols if s not in symbols]

    # ── 回调 ──

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self._tick_callbacks.append(callback)

    def on_bar(self, callback) -> None:
        self._bar_callbacks.append(callback)

    def get_price(self, symbol: str) -> Optional[float]:
        return self._latest_prices.get(symbol)

    # ── 内部轮询 ──

    def _poll_loop(self):
        while self._running and self._api:
            try:
                ticks = self._fetch_quotes()
                for code, tick in ticks.items():
                    if tick.price > 0:
                        self._latest_prices[code] = tick.price
                        for cb in self._tick_callbacks:
                            try:
                                cb(tick)
                            except Exception:
                                pass
            except Exception as e:
                logger.warning("TDX poll error: {}", e)
                # 尝试重连
                try:
                    self._api.disconnect()
                except Exception:
                    pass
                self._api = None
                time.sleep(2)
                self.connect()
                if not self._connected:
                    break

            time.sleep(self.poll_interval)

    def _fetch_quotes(self) -> dict[str, Tick]:
        """批量拉取通达信实时行情。"""
        if not self._api or not self._symbols:
            return {}

        # 按市场分组，分组批量查询
        ticks = {}
        for symbol in self._symbols:
            try:
                market, code = _normalize_tdx(symbol)
                # pytdx quotes 返回 [(market, code, ...46 fields...), ...]
                # 批量查询比单只查询快得多
                quotes = self._api.get_security_quotes(market, code)
                if quotes and len(quotes) > 0:
                    tick = self._parse_quote(quotes[0])
                    if tick:
                        ticks[tick.symbol] = tick
            except Exception:
                continue

        return ticks

    def _parse_quote(self, raw: list) -> Optional[Tick]:
        """解析通达信单只股票报价。

        pytdx 返回的 46 个字段（索引从 0 开始）：
          0: market, 1: code, 2: active1, 3: name, 4: last_close,
          5: open, 6: high, 7: low, 8: price (现价),
          9: bid1, 10: ask1, 11: bid_vol1, 12: ask_vol1,
          ... bid/ask 2-5 ...
          31: volume, 32: amount, 35: high, 36: low,
          38: turnover_rate, 44: limit_up, 45: limit_down
        """
        try:
            if len(raw) < 10:
                return None

            symbol = str(raw[1]).strip()
            price = float(raw[8]) if raw[8] else 0
            if price <= 0:
                return None

            return Tick(
                symbol=symbol,
                timestamp=datetime.now(),
                price=price,
                volume=float(raw[31]) if len(raw) > 31 and raw[31] else 0,
                bid=float(raw[9]) if len(raw) > 9 and raw[9] else None,
                ask=float(raw[10]) if len(raw) > 10 and raw[10] else None,
                bid_size=float(raw[11]) if len(raw) > 11 and raw[11] else None,
                ask_size=float(raw[12]) if len(raw) > 12 and raw[12] else None,
            )
        except (ValueError, IndexError, TypeError):
            return None
