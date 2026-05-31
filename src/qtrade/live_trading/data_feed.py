"""
Real-time market data feeds.

Provides:
- WebSocket-based streaming data
- Polling-based data feeds
- Data normalization and aggregation
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List, Callable
from dataclasses import dataclass
from datetime import datetime
import pandas as pd
import asyncio
import websockets
import json
from loguru import logger


@dataclass
class Tick:
    """Single market tick."""
    symbol: str
    timestamp: datetime
    price: float
    volume: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None


@dataclass
class Bar:
    """OHLCV bar."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None


class RealtimeDataFeed(ABC):
    """Abstract base class for real-time data feeds."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to data feed."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from data feed."""
        pass

    @abstractmethod
    def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to symbols."""
        pass

    @abstractmethod
    def unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from symbols."""
        pass

    @abstractmethod
    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        """Register tick callback."""
        pass

    @abstractmethod
    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        """Register bar callback."""
        pass


class WebSocketFeed(RealtimeDataFeed):
    """WebSocket-based real-time data feed."""

    def __init__(self, url: str, auth_token: Optional[str] = None):
        self.url = url
        self.auth_token = auth_token
        self.websocket = None
        self.connected = False
        self.subscribed_symbols = set()
        self.tick_callbacks = []
        self.bar_callbacks = []
        self._task = None

        logger.info(f"WebSocketFeed initialized: {url}")

    def connect(self) -> bool:
        try:
            asyncio.create_task(self._connect_async())
            self.connected = True
            logger.info("WebSocket feed connecting...")
            return True
        except Exception as e:
            logger.error(f"Failed to connect WebSocket: {e}")
            return False

    async def _connect_async(self):
        """Async connection handler."""
        try:
            async with websockets.connect(self.url) as ws:
                self.websocket = ws
                logger.info("WebSocket connected")

                # Authenticate if needed
                if self.auth_token:
                    await self._authenticate()

                # Listen for messages
                async for message in ws:
                    await self._handle_message(message)

        except Exception as e:
            logger.error(f"WebSocket error: {e}")
            self.connected = False

    async def _authenticate(self):
        """Authenticate with WebSocket server."""
        auth_msg = json.dumps({
            "action": "auth",
            "params": {"token": self.auth_token}
        })
        await self.websocket.send(auth_msg)
        response = await self.websocket.recv()
        logger.info(f"Auth response: {response}")

    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)

            # Parse tick data
            if 'type' in data and data['type'] == 'tick':
                tick = Tick(
                    symbol=data['symbol'],
                    timestamp=datetime.fromisoformat(data['timestamp']),
                    price=float(data['price']),
                    volume=float(data.get('volume', 0)),
                    bid=float(data['bid']) if 'bid' in data else None,
                    ask=float(data['ask']) if 'ask' in data else None,
                )

                # Call tick callbacks
                for callback in self.tick_callbacks:
                    callback(tick)

            # Parse bar data
            elif 'type' in data and data['type'] == 'bar':
                bar = Bar(
                    symbol=data['symbol'],
                    timestamp=datetime.fromisoformat(data['timestamp']),
                    open=float(data['open']),
                    high=float(data['high']),
                    low=float(data['low']),
                    close=float(data['close']),
                    volume=float(data['volume']),
                )

                # Call bar callbacks
                for callback in self.bar_callbacks:
                    callback(bar)

        except Exception as e:
            logger.error(f"Error handling message: {e}")

    def disconnect(self) -> None:
        self.connected = False
        if self._task:
            self._task.cancel()
        logger.info("WebSocket feed disconnected")

    def subscribe(self, symbols: List[str]) -> None:
        if not self.connected:
            logger.warning("Not connected, cannot subscribe")
            return

        self.subscribed_symbols.update(symbols)

        # Send subscription message
        msg = json.dumps({
            "action": "subscribe",
            "symbols": symbols
        })

        asyncio.create_task(self.websocket.send(msg))
        logger.info(f"Subscribed to: {symbols}")

    def unsubscribe(self, symbols: List[str]) -> None:
        self.subscribed_symbols -= set(symbols)

        msg = json.dumps({
            "action": "unsubscribe",
            "symbols": symbols
        })

        asyncio.create_task(self.websocket.send(msg))
        logger.info(f"Unsubscribed from: {symbols}")

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self.tick_callbacks.append(callback)

    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        self.bar_callbacks.append(callback)


class PollingFeed(RealtimeDataFeed):
    """Polling-based data feed (fallback when WebSocket unavailable)."""

    def __init__(self, broker, interval: float = 1.0):
        """
        Args:
            broker: BrokerAdapter instance for price queries
            interval: Polling interval in seconds
        """
        self.broker = broker
        self.interval = interval
        self.connected = False
        self.subscribed_symbols = set()
        self.tick_callbacks = []
        self.bar_callbacks = []
        self._running = False

        # Bar aggregation
        self.current_bars: Dict[str, Dict] = {}

        logger.info(f"PollingFeed initialized (interval={interval}s)")

    def connect(self) -> bool:
        self.connected = True
        self._running = True
        logger.info("Polling feed connected")

        # Start polling in background
        import threading
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

        return True

    def _poll_loop(self):
        """Main polling loop."""
        import time

        while self._running:
            try:
                for symbol in self.subscribed_symbols:
                    price = self.broker.get_latest_price(symbol)

                    if price > 0:
                        tick = Tick(
                            symbol=symbol,
                            timestamp=datetime.now(),
                            price=price,
                            volume=0,  # Polling doesn't provide volume
                        )

                        # Call tick callbacks
                        for callback in self.tick_callbacks:
                            callback(tick)

                        # Aggregate into bars
                        self._aggregate_bar(tick)

                time.sleep(self.interval)

            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(self.interval)

    def _aggregate_bar(self, tick: Tick):
        """Aggregate ticks into minute bars."""
        symbol = tick.symbol

        # Initialize bar if needed
        if symbol not in self.current_bars:
            self.current_bars[symbol] = {
                'symbol': symbol,
                'timestamp': tick.timestamp.replace(second=0, microsecond=0),
                'open': tick.price,
                'high': tick.price,
                'low': tick.price,
                'close': tick.price,
                'volume': tick.volume,
            }
        else:
            bar = self.current_bars[symbol]

            # Check if new minute
            current_minute = tick.timestamp.replace(second=0, microsecond=0)
            if current_minute > bar['timestamp']:
                # Emit completed bar
                completed_bar = Bar(**bar)
                for callback in self.bar_callbacks:
                    callback(completed_bar)

                # Start new bar
                self.current_bars[symbol] = {
                    'symbol': symbol,
                    'timestamp': current_minute,
                    'open': tick.price,
                    'high': tick.price,
                    'low': tick.price,
                    'close': tick.price,
                    'volume': tick.volume,
                }
            else:
                # Update current bar
                bar['high'] = max(bar['high'], tick.price)
                bar['low'] = min(bar['low'], tick.price)
                bar['close'] = tick.price
                bar['volume'] += tick.volume

    def disconnect(self) -> None:
        self._running = False
        self.connected = False
        logger.info("Polling feed disconnected")

    def subscribe(self, symbols: List[str]) -> None:
        self.subscribed_symbols.update(symbols)
        logger.info(f"Subscribed to: {symbols}")

    def unsubscribe(self, symbols: List[str]) -> None:
        self.subscribed_symbols -= set(symbols)
        logger.info(f"Unsubscribed from: {symbols}")

    def on_tick(self, callback: Callable[[Tick], None]) -> None:
        self.tick_callbacks.append(callback)

    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        self.bar_callbacks.append(callback)


class BarAggregator:
    """Aggregate ticks into bars of various timeframes."""

    def __init__(self, timeframe: str = '1min'):
        """
        Args:
            timeframe: Bar timeframe ('1min', '5min', '15min', '1hour', '1day')
        """
        self.timeframe = timeframe
        self.current_bars: Dict[str, Dict] = {}
        self.callbacks = []

        # Timeframe mapping
        self.timeframe_seconds = {
            '1min': 60,
            '5min': 300,
            '15min': 900,
            '1hour': 3600,
            '1day': 86400,
        }

    def on_bar(self, callback: Callable[[Bar], None]):
        """Register bar completion callback."""
        self.callbacks.append(callback)

    def process_tick(self, tick: Tick):
        """Process incoming tick."""
        symbol = tick.symbol

        if symbol not in self.current_bars:
            self._start_new_bar(tick)
        else:
            self._update_bar(tick)

    def _start_new_bar(self, tick: Tick):
        """Start a new bar."""
        timestamp = self._align_timestamp(tick.timestamp)

        self.current_bars[tick.symbol] = {
            'symbol': tick.symbol,
            'timestamp': timestamp,
            'open': tick.price,
            'high': tick.price,
            'low': tick.price,
            'close': tick.price,
            'volume': tick.volume,
        }

    def _update_bar(self, tick: Tick):
        """Update current bar or emit completed bar."""
        symbol = tick.symbol
        bar = self.current_bars[symbol]

        current_timestamp = self._align_timestamp(tick.timestamp)

        # Check if bar completed
        if current_timestamp > bar['timestamp']:
            # Emit completed bar
            completed_bar = Bar(**bar)
            for callback in self.callbacks:
                callback(completed_bar)

            # Start new bar
            self._start_new_bar(tick)
        else:
            # Update current bar
            bar['high'] = max(bar['high'], tick.price)
            bar['low'] = min(bar['low'], tick.price)
            bar['close'] = tick.price
            bar['volume'] += tick.volume

    def _align_timestamp(self, timestamp: datetime) -> datetime:
        """Align timestamp to timeframe boundary."""
        seconds = self.timeframe_seconds.get(self.timeframe, 60)

        # Round down to nearest timeframe
        epoch = timestamp.timestamp()
        aligned = (epoch // seconds) * seconds

        return datetime.fromtimestamp(aligned)
