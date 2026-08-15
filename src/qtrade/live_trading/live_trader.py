"""
Live trading orchestrator.

Main controller that ties together:
- Broker connection
- Real-time data feeds
- Order management
- Position synchronization
- Risk monitoring
- Alert system
- Strategy execution
"""

from typing import Optional, Dict, List
from datetime import datetime
import time
import threading
from loguru import logger

from .broker import BrokerAdapter, OrderSide, OrderType, TimeInForce
from .data_feed import RealtimeDataFeed, Tick, Bar
from .order_manager import OrderManager, Order
from .position_sync import PositionSynchronizer
from .risk_monitor import RiskMonitor
from .alerts import AlertSystem, AlertLevel


class LiveTrader:
    """Main live trading controller."""

    def __init__(
        self,
        broker: BrokerAdapter,
        data_feed: RealtimeDataFeed,
        strategy,
        risk_monitor: Optional[RiskMonitor] = None,
        alert_system: Optional[AlertSystem] = None,
        position_sync_interval: float = 5.0,
        signal_interval: float = 30.0,
        lot_size: int = 100,
        historical_data_dir: str = "data/cache",
    ):
        """
        Args:
            broker: BrokerAdapter instance
            data_feed: RealtimeDataFeed instance
            strategy: Strategy instance with generate_signals() method
            risk_monitor: RiskMonitor instance (optional)
            alert_system: AlertSystem instance (optional)
            position_sync_interval: Seconds between position syncs
            signal_interval: Minimum seconds between signal checks per symbol
            lot_size: A-share lot size (shares are rounded down to this multiple)
            historical_data_dir: Directory containing cached daily-bar CSVs
        """
        self.broker = broker
        self.data_feed = data_feed
        self.strategy = strategy
        self.risk_monitor = risk_monitor or RiskMonitor()
        self.alert_system = alert_system or AlertSystem()
        self.signal_interval = signal_interval
        self.lot_size = lot_size
        self.historical_data_dir = historical_data_dir

        # Initialize components
        self.order_manager = OrderManager(broker)
        self.position_sync = PositionSynchronizer(broker, position_sync_interval)

        # State
        self.running = False
        self.symbols: List[str] = []
        self.current_prices: Dict[str, float] = {}
        self.last_signals: Dict[str, int] = {}
        self._last_signal_time: Dict[str, float] = {}
        self._hist_cache: dict = {}

        # Register callbacks
        self.data_feed.on_tick(self._on_tick)
        self.data_feed.on_bar(self._on_bar)
        self.risk_monitor.circuit_breaker.on_trip(self._on_circuit_breaker_trip)

        logger.info("LiveTrader initialized")

    def start(self, symbols: List[str]):
        """
        Start live trading.

        Args:
            symbols: List of symbols to trade
        """
        logger.info(f"Starting live trading for: {symbols}")

        # Connect to broker
        if not self.broker.connect():
            raise RuntimeError("Failed to connect to broker")

        # Start position synchronization
        self.position_sync.start()

        # Initial position sync
        self.position_sync.sync_positions()

        # Connect to data feed
        if not self.data_feed.connect():
            raise RuntimeError("Failed to connect to data feed")

        # Subscribe to symbols
        self.symbols = symbols
        self.data_feed.subscribe(symbols)

        # Initialize risk monitor
        account = self.broker.get_account()
        self.risk_monitor.update_portfolio_value(account.portfolio_value)

        # Send startup alert
        self.alert_system.send_alert(
            AlertLevel.INFO,
            "Live Trading Started",
            f"Trading started for {len(symbols)} symbols",
            {'symbols': symbols, 'portfolio_value': account.portfolio_value}
        )

        self.running = True
        logger.info("Live trading started successfully")

        # Main loop
        try:
            self._main_loop()
        except KeyboardInterrupt:
            logger.info("Received interrupt signal")
            self.stop()

    def stop(self):
        """Stop live trading."""
        logger.info("Stopping live trading...")

        self.running = False

        # Cancel all open orders
        self.order_manager.cancel_all_orders()

        # Stop position sync
        self.position_sync.stop()

        # Disconnect data feed
        self.data_feed.disconnect()

        # Disconnect broker
        self.broker.disconnect()

        # Send shutdown alert
        self.alert_system.send_alert(
            AlertLevel.INFO,
            "Live Trading Stopped",
            "Trading system shut down",
            {}
        )

        logger.info("Live trading stopped")

    def _main_loop(self):
        """Main trading loop."""
        last_order_update = time.time()

        while self.running:
            try:
                # Update order statuses periodically
                if time.time() - last_order_update > 5.0:
                    self.order_manager.update_all_orders()
                    last_order_update = time.time()

                # Update risk metrics
                account = self.broker.get_account()
                self.risk_monitor.update_portfolio_value(account.portfolio_value)

                # Check if trading is allowed
                if not self.risk_monitor.can_trade():
                    time.sleep(1.0)
                    continue

                # Small sleep to prevent busy loop
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                self.alert_system.send_alert(
                    AlertLevel.ERROR,
                    "Main Loop Error",
                    str(e),
                    {}
                )
                time.sleep(1.0)

    def _on_tick(self, tick: Tick):
        """Handle incoming tick: update prices and run throttled signal checks."""
        try:
            # Update current price
            self.current_prices[tick.symbol] = tick.price

            # Check position limits
            position = self.position_sync.get_position(tick.symbol)
            if position:
                account = self.broker.get_account()
                if not self.risk_monitor.check_position_limit(
                    tick.symbol,
                    position.market_value,
                    account.portfolio_value
                ):
                    # Position exceeds limit - reduce position
                    logger.warning(f"Position limit exceeded for {tick.symbol}")

            # Throttled signal evaluation for tick-driven feeds (TDX / Tencent)
            now = time.time()
            if now - self._last_signal_time.get(tick.symbol, 0) >= self.signal_interval:
                self._last_signal_time[tick.symbol] = now
                self._evaluate_symbol(tick.symbol, tick.price)

        except Exception as e:
            logger.error(f"Error processing tick: {e}")

    def _on_bar(self, bar: Bar):
        """Handle incoming bar (bar-driven feeds such as PollingFeed/WebSocketFeed)."""
        try:
            self.current_prices[bar.symbol] = bar.close
            self._evaluate_symbol(bar.symbol, bar.close)
        except Exception as e:
            logger.error(f"Error processing bar: {e}")
            self.alert_system.send_alert(
                AlertLevel.ERROR,
                "Bar Processing Error",
                f"Error processing bar for {bar.symbol}: {str(e)}",
                {'symbol': bar.symbol}
            )

    def _evaluate_symbol(self, symbol: str, current_price: float):
        """Generate strategy signals and execute trades for one symbol."""
        if not self.risk_monitor.can_trade():
            return

        if current_price <= 0:
            return

        # Check for open orders
        open_orders = self.order_manager.get_open_orders(symbol)
        if open_orders:
            logger.debug(f"Skipping {symbol} - has open orders")
            return

        # Get historical data for strategy
        historical_data = self._get_historical_data(symbol)

        if historical_data is None or len(historical_data) < 50:
            logger.debug(f"Skipping {symbol} - insufficient history ({0 if historical_data is None else len(historical_data)})")
            return

        # Generate signals from strategy
        signals = self.strategy.generate_signals(historical_data)

        if signals is None or len(signals) == 0:
            return

        # Get latest signal
        latest_signal = signals.iloc[-1]
        signal_action = int(latest_signal.get('signal_action', 0) or 0)
        signal_strength = float(latest_signal.get('signal_strength', 0) or 0)

        # Check if signal changed
        last_signal = self.last_signals.get(symbol, 0)
        if signal_action == last_signal:
            return

        self.last_signals[symbol] = signal_action

        # Execute signal
        if signal_action == 1:  # Buy signal
            self._execute_buy(symbol, signal_strength, current_price)
        elif signal_action == -1:  # Sell signal
            self._execute_sell(symbol, signal_strength, current_price)

    def _execute_buy(self, symbol: str, strength: float, current_price: float):
        """Execute buy signal."""
        # Check if already have position
        position = self.position_sync.get_position(symbol)
        if position and position.quantity > 0:
            logger.debug(f"Already have position in {symbol}")
            return

        # Calculate position size based on strength and risk limits
        account = self.broker.get_account()
        max_position_value = account.portfolio_value * (self.risk_monitor.max_position_pct / 100)

        # Adjust by signal strength (0-1)
        position_value = max_position_value * strength

        # Calculate quantity (rounded down to whole lots for A-shares)
        quantity = int(position_value / current_price)
        if self.lot_size and self.lot_size > 1:
            quantity = (quantity // self.lot_size) * self.lot_size

        if quantity < 1:
            logger.debug(f"Position too small for {symbol}")
            return

        # Check rate limit
        if not self.risk_monitor.record_order():
            logger.warning("Order rate limit exceeded")
            return

        # Set expected price for slippage monitoring
        self.risk_monitor.set_expected_price(symbol, current_price)

        # Create and submit order
        try:
            order = self.order_manager.create_order(
                symbol=symbol,
                side=OrderSide.BUY,
                quantity=quantity,
                order_type=OrderType.MARKET,
                notes=f"Buy signal (strength={strength:.2f})"
            )

            if self.order_manager.submit_order(order.id):
                logger.info(f"Buy order submitted: {quantity} {symbol} @ ~${current_price:.2f}")

                self.alert_system.send_alert(
                    AlertLevel.INFO,
                    "Buy Order Submitted",
                    f"Buying {quantity} shares of {symbol}",
                    {
                        'symbol': symbol,
                        'quantity': quantity,
                        'price': current_price,
                        'strength': strength,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to submit buy order: {e}")
            self.alert_system.send_alert(
                AlertLevel.ERROR,
                "Buy Order Failed",
                f"Failed to buy {symbol}: {str(e)}",
                {'symbol': symbol}
            )

    def _execute_sell(self, symbol: str, strength: float, current_price: float):
        """Execute sell signal."""
        # Check if we have position
        position = self.position_sync.get_position(symbol)
        if not position or position.quantity <= 0:
            logger.debug(f"No position to sell for {symbol}")
            return

        quantity = position.quantity

        # Check rate limit
        if not self.risk_monitor.record_order():
            logger.warning("Order rate limit exceeded")
            return

        # Set expected price for slippage monitoring
        self.risk_monitor.set_expected_price(symbol, current_price)

        # Create and submit order
        try:
            order = self.order_manager.create_order(
                symbol=symbol,
                side=OrderSide.SELL,
                quantity=quantity,
                order_type=OrderType.MARKET,
                notes=f"Sell signal (strength={strength:.2f})"
            )

            if self.order_manager.submit_order(order.id):
                logger.info(f"Sell order submitted: {quantity} {symbol} @ ~${current_price:.2f}")

                self.alert_system.send_alert(
                    AlertLevel.INFO,
                    "Sell Order Submitted",
                    f"Selling {quantity} shares of {symbol}",
                    {
                        'symbol': symbol,
                        'quantity': quantity,
                        'price': current_price,
                        'strength': strength,
                    }
                )

        except Exception as e:
            logger.error(f"Failed to submit sell order: {e}")
            self.alert_system.send_alert(
                AlertLevel.ERROR,
                "Sell Order Failed",
                f"Failed to sell {symbol}: {str(e)}",
                {'symbol': symbol}
            )

    def _on_circuit_breaker_trip(self, reason: str):
        """Handle circuit breaker trip."""
        logger.critical(f"Circuit breaker tripped: {reason}")

        # Cancel all orders
        self.order_manager.cancel_all_orders()

        # Send critical alert
        self.alert_system.send_alert(
            AlertLevel.CRITICAL,
            "Circuit Breaker Tripped",
            reason,
            {'reason': reason},
            force=True
        )

    def _get_historical_data(self, symbol: str):
        """Get historical daily bars for the strategy.

        Loads from the local CSV cache first, then falls back to the
        framework DataFetcher (pytdx / akshare).
        """
        import pandas as pd
        from pathlib import Path

        if symbol in self._hist_cache:
            return self._hist_cache[symbol]

        # 1. Local CSV cache (e.g. data/cache/000001.csv)
        cache_path = Path(self.historical_data_dir) / f"{symbol}.csv"
        df = None
        if cache_path.exists():
            try:
                df = pd.read_csv(cache_path, index_col=0, parse_dates=True)
                df.columns = [str(c).lower() for c in df.columns]
            except Exception as e:
                logger.warning("Failed to load cached history for {}: {}", symbol, e)
                df = None

        # 2. Fall back to the framework data fetcher
        if df is None:
            try:
                from qtrade.data.fetcher import DataFetcher
                fetcher = DataFetcher()
                df = fetcher.fetch(symbol, "20200101")
            except Exception as e:
                logger.warning("Failed to fetch history for {}: {}", symbol, e)
                return None

        if df is None or df.empty:
            return None

        # Keep only OHLCV columns (lowercase)
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if len(cols) < 4:
            logger.warning("Insufficient OHLCV columns for {}", symbol)
            return None
        df = df[cols].sort_index()

        self._hist_cache[symbol] = df
        return df

    def get_status(self) -> Dict:
        """Get current trading status."""
        account = None
        try:
            account = self.broker.get_account()
        except:
            pass

        return {
            'running': self.running,
            'symbols': self.symbols,
            'portfolio_value': account.portfolio_value if account else 0,
            'cash': account.cash if account else 0,
            'open_orders': len(self.order_manager.get_open_orders()),
            'positions': len(self.position_sync.get_all_positions()),
            'can_trade': self.risk_monitor.can_trade(),
            'risk_summary': self.risk_monitor.get_risk_summary(),
        }

    def get_positions(self):
        """Get current positions."""
        return self.position_sync.get_position_summary()

    def get_orders(self):
        """Get open orders."""
        orders = self.order_manager.get_open_orders()
        return [o.to_dict() for o in orders]

    def get_pnl(self) -> Dict:
        """Get P&L summary."""
        return {
            'unrealized_pnl': self.position_sync.get_unrealized_pnl(),
            'realized_pnl': self.position_sync.get_realized_pnl(),
            'total_pnl': self.position_sync.get_total_pnl(),
        }

    def emergency_stop(self, reason: str = "Manual emergency stop"):
        """Trigger emergency stop."""
        self.risk_monitor.emergency_stop(reason)

        # Cancel all orders
        self.order_manager.cancel_all_orders()

        logger.critical(f"Emergency stop activated: {reason}")
