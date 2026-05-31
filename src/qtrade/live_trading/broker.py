"""
Broker adapter interfaces and implementations.

Provides abstract base class and concrete implementations for:
- Mock broker (testing)
- Alpaca (US stocks)
- Interactive Brokers (multi-market)
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import pandas as pd
from loguru import logger


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(Enum):
    DAY = "day"
    GTC = "gtc"  # Good Till Cancel
    IOC = "ioc"  # Immediate Or Cancel
    FOK = "fok"  # Fill Or Kill


@dataclass
class Position:
    """Position data class."""
    symbol: str
    quantity: float
    avg_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float  # Percentage


@dataclass
class AccountInfo:
    """Account information."""
    cash: float
    buying_power: float
    portfolio_value: float
    equity: float
    long_market_value: float
    short_market_value: float


class BrokerAdapter(ABC):
    """Abstract base class for broker adapters."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to broker API."""
        pass

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from broker API."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if connected to broker."""
        pass

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get account information."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """Get all open positions."""
        pass

    @abstractmethod
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for specific symbol."""
        pass

    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> str:
        """
        Submit order to broker.

        Returns:
            Order ID
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel open order."""
        pass

    @abstractmethod
    def get_order(self, order_id: str) -> Dict:
        """Get order details."""
        pass

    @abstractmethod
    def get_orders(self, status: Optional[str] = None) -> List[Dict]:
        """Get list of orders."""
        pass

    @abstractmethod
    def get_latest_price(self, symbol: str) -> float:
        """Get latest market price for symbol."""
        pass


class MockBroker(BrokerAdapter):
    """Mock broker for testing and paper trading."""

    def __init__(self, initial_cash: float = 100000.0):
        self.cash = initial_cash
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Dict] = {}
        self.connected = False
        self.order_counter = 0
        self.prices: Dict[str, float] = {}

        logger.info(f"MockBroker initialized with ${initial_cash:,.2f}")

    def connect(self) -> bool:
        self.connected = True
        logger.info("MockBroker connected")
        return True

    def disconnect(self) -> None:
        self.connected = False
        logger.info("MockBroker disconnected")

    def is_connected(self) -> bool:
        return self.connected

    def get_account(self) -> AccountInfo:
        long_value = sum(p.market_value for p in self.positions.values() if p.quantity > 0)
        portfolio_value = self.cash + long_value

        return AccountInfo(
            cash=self.cash,
            buying_power=self.cash,
            portfolio_value=portfolio_value,
            equity=portfolio_value,
            long_market_value=long_value,
            short_market_value=0.0,
        )

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def get_position(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def set_price(self, symbol: str, price: float):
        """Set mock price for symbol."""
        self.prices[symbol] = price

        # Update position market value
        if symbol in self.positions:
            pos = self.positions[symbol]
            old_value = pos.market_value
            pos.market_value = pos.quantity * price
            pos.unrealized_pl = (price - pos.avg_price) * pos.quantity
            pos.unrealized_plpc = (price / pos.avg_price - 1) * 100 if pos.avg_price > 0 else 0

    def get_latest_price(self, symbol: str) -> float:
        return self.prices.get(symbol, 0.0)

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> str:
        self.order_counter += 1
        order_id = f"MOCK-{self.order_counter:06d}"

        price = self.prices.get(symbol)
        if price is None:
            raise ValueError(f"No price available for {symbol}")

        # Calculate order value
        order_value = quantity * price

        # Execute immediately for market orders
        if order_type == OrderType.MARKET:
            if side == OrderSide.BUY:
                if order_value > self.cash:
                    raise ValueError(f"Insufficient cash: need ${order_value:.2f}, have ${self.cash:.2f}")

                self.cash -= order_value

                if symbol in self.positions:
                    pos = self.positions[symbol]
                    total_cost = pos.avg_price * pos.quantity + price * quantity
                    pos.quantity += quantity
                    pos.avg_price = total_cost / pos.quantity
                    pos.market_value = pos.quantity * price
                else:
                    self.positions[symbol] = Position(
                        symbol=symbol,
                        quantity=quantity,
                        avg_price=price,
                        market_value=order_value,
                        unrealized_pl=0.0,
                        unrealized_plpc=0.0,
                    )

            elif side == OrderSide.SELL:
                if symbol not in self.positions or self.positions[symbol].quantity < quantity:
                    raise ValueError(f"Insufficient position: {symbol}")

                self.cash += order_value
                pos = self.positions[symbol]
                pos.quantity -= quantity
                pos.market_value = pos.quantity * price

                if pos.quantity == 0:
                    del self.positions[symbol]

        # Record order
        self.orders[order_id] = {
            'id': order_id,
            'symbol': symbol,
            'side': side.value,
            'quantity': quantity,
            'type': order_type.value,
            'limit_price': limit_price,
            'stop_price': stop_price,
            'time_in_force': time_in_force.value,
            'status': 'filled' if order_type == OrderType.MARKET else 'pending',
            'filled_at': datetime.now() if order_type == OrderType.MARKET else None,
            'filled_qty': quantity if order_type == OrderType.MARKET else 0,
            'filled_avg_price': price if order_type == OrderType.MARKET else None,
            'submitted_at': datetime.now(),
        }

        logger.info(f"Order {order_id}: {side.value} {quantity} {symbol} @ ${price:.2f}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id]['status'] = 'canceled'
            logger.info(f"Order {order_id} canceled")
            return True
        return False

    def get_order(self, order_id: str) -> Dict:
        return self.orders.get(order_id, {})

    def get_orders(self, status: Optional[str] = None) -> List[Dict]:
        orders = list(self.orders.values())
        if status:
            orders = [o for o in orders if o['status'] == status]
        return orders


class AlpacaBroker(BrokerAdapter):
    """Alpaca broker adapter for US stocks."""

    def __init__(self, api_key: str, api_secret: str, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.paper = paper
        self.api = None
        self.connected = False

        base_url = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        self.base_url = base_url

        logger.info(f"AlpacaBroker initialized (paper={paper})")

    def connect(self) -> bool:
        try:
            import alpaca_trade_api as tradeapi
            self.api = tradeapi.REST(
                self.api_key,
                self.api_secret,
                self.base_url,
                api_version='v2'
            )
            # Test connection
            self.api.get_account()
            self.connected = True
            logger.info("Alpaca API connected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Alpaca: {e}")
            self.connected = False
            return False

    def disconnect(self) -> None:
        self.api = None
        self.connected = False
        logger.info("Alpaca API disconnected")

    def is_connected(self) -> bool:
        return self.connected and self.api is not None

    def get_account(self) -> AccountInfo:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        account = self.api.get_account()
        return AccountInfo(
            cash=float(account.cash),
            buying_power=float(account.buying_power),
            portfolio_value=float(account.portfolio_value),
            equity=float(account.equity),
            long_market_value=float(account.long_market_value),
            short_market_value=float(account.short_market_value),
        )

    def get_positions(self) -> List[Position]:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        positions = self.api.list_positions()
        return [
            Position(
                symbol=p.symbol,
                quantity=float(p.qty),
                avg_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_plpc=float(p.unrealized_plpc),
            )
            for p in positions
        ]

    def get_position(self, symbol: str) -> Optional[Position]:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        try:
            p = self.api.get_position(symbol)
            return Position(
                symbol=p.symbol,
                quantity=float(p.qty),
                avg_price=float(p.avg_entry_price),
                market_value=float(p.market_value),
                unrealized_pl=float(p.unrealized_pl),
                unrealized_plpc=float(p.unrealized_plpc),
            )
        except:
            return None

    def get_latest_price(self, symbol: str) -> float:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        snapshot = self.api.get_snapshot(symbol)
        return float(snapshot.latest_trade.price)

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> str:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        try:
            order = self.api.submit_order(
                symbol=symbol,
                qty=quantity,
                side=side.value,
                type=order_type.value,
                time_in_force=time_in_force.value,
                limit_price=limit_price,
                stop_price=stop_price,
            )
            logger.info(f"Alpaca order submitted: {order.id}")
            return order.id
        except Exception as e:
            logger.error(f"Failed to submit order: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        try:
            self.api.cancel_order(order_id)
            logger.info(f"Alpaca order canceled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to cancel order: {e}")
            return False

    def get_order(self, order_id: str) -> Dict:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        order = self.api.get_order(order_id)
        return {
            'id': order.id,
            'symbol': order.symbol,
            'side': order.side,
            'quantity': float(order.qty),
            'type': order.type,
            'status': order.status,
            'filled_qty': float(order.filled_qty) if order.filled_qty else 0,
            'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else None,
            'submitted_at': order.submitted_at,
            'filled_at': order.filled_at,
        }

    def get_orders(self, status: Optional[str] = None) -> List[Dict]:
        if not self.is_connected():
            raise RuntimeError("Not connected to Alpaca")

        orders = self.api.list_orders(status=status)
        return [
            {
                'id': o.id,
                'symbol': o.symbol,
                'side': o.side,
                'quantity': float(o.qty),
                'type': o.type,
                'status': o.status,
                'filled_qty': float(o.filled_qty) if o.filled_qty else 0,
                'filled_avg_price': float(o.filled_avg_price) if o.filled_avg_price else None,
                'submitted_at': o.submitted_at,
                'filled_at': o.filled_at,
            }
            for o in orders
        ]
