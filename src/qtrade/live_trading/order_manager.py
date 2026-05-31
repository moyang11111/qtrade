"""
Order management system.

Handles:
- Order creation and validation
- Order lifecycle tracking
- Order status updates
- Order history and audit trail
"""

from typing import Optional, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import uuid
from loguru import logger

from .broker import BrokerAdapter, OrderSide, OrderType, TimeInForce


class OrderStatus(Enum):
    """Order status states."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class Order:
    """Order data class."""
    id: str
    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: float = 0.0
    filled_avg_price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    broker_order_id: Optional[str] = None
    strategy_id: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'id': self.id,
            'symbol': self.symbol,
            'side': self.side.value,
            'quantity': self.quantity,
            'type': self.order_type.value,
            'limit_price': self.limit_price,
            'stop_price': self.stop_price,
            'time_in_force': self.time_in_force.value,
            'status': self.status.value,
            'filled_quantity': self.filled_quantity,
            'filled_avg_price': self.filled_avg_price,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat(),
            'broker_order_id': self.broker_order_id,
            'strategy_id': self.strategy_id,
            'notes': self.notes,
        }


class OrderManager:
    """Order management system."""

    def __init__(self, broker: BrokerAdapter):
        self.broker = broker
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []

        logger.info("OrderManager initialized")

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
        strategy_id: Optional[str] = None,
        notes: str = "",
    ) -> Order:
        """
        Create new order.

        Args:
            symbol: Trading symbol
            side: Buy or sell
            quantity: Order quantity
            order_type: Market, limit, stop, etc.
            limit_price: Limit price for limit orders
            stop_price: Stop price for stop orders
            time_in_force: Order duration
            strategy_id: ID of strategy creating order
            notes: Additional notes

        Returns:
            Created Order object
        """
        # Validate order
        self._validate_order(symbol, side, quantity, order_type, limit_price, stop_price)

        # Create order
        order_id = str(uuid.uuid4())
        order = Order(
            id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            strategy_id=strategy_id,
            notes=notes,
        )

        self.orders[order_id] = order
        logger.info(f"Order created: {order_id} - {side.value} {quantity} {symbol}")

        return order

    def submit_order(self, order_id: str) -> bool:
        """
        Submit order to broker.

        Args:
            order_id: Order ID to submit

        Returns:
            True if successful
        """
        if order_id not in self.orders:
            logger.error(f"Order not found: {order_id}")
            return False

        order = self.orders[order_id]

        if order.status != OrderStatus.PENDING:
            logger.error(f"Order {order_id} not in PENDING status: {order.status.value}")
            return False

        try:
            # Submit to broker
            broker_order_id = self.broker.submit_order(
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                limit_price=order.limit_price,
                stop_price=order.stop_price,
                time_in_force=order.time_in_force,
            )

            # Update order
            order.broker_order_id = broker_order_id
            order.status = OrderStatus.SUBMITTED
            order.updated_at = datetime.now()

            logger.info(f"Order submitted: {order_id} -> {broker_order_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to submit order {order_id}: {e}")
            order.status = OrderStatus.REJECTED
            order.notes = f"Rejection: {str(e)}"
            order.updated_at = datetime.now()
            return False

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if successful
        """
        if order_id not in self.orders:
            logger.error(f"Order not found: {order_id}")
            return False

        order = self.orders[order_id]

        if order.status in [OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED]:
            logger.warning(f"Order {order_id} already in terminal state: {order.status.value}")
            return False

        try:
            if order.broker_order_id:
                success = self.broker.cancel_order(order.broker_order_id)
                if not success:
                    return False

            order.status = OrderStatus.CANCELED
            order.updated_at = datetime.now()

            logger.info(f"Order canceled: {order_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def update_order_status(self, order_id: str):
        """
        Update order status from broker.

        Args:
            order_id: Order ID to update
        """
        if order_id not in self.orders:
            return

        order = self.orders[order_id]

        if not order.broker_order_id:
            return

        try:
            broker_order = self.broker.get_order(order.broker_order_id)

            # Map broker status to our status
            status_map = {
                'new': OrderStatus.SUBMITTED,
                'pending': OrderStatus.SUBMITTED,
                'partially_filled': OrderStatus.PARTIAL,
                'filled': OrderStatus.FILLED,
                'canceled': OrderStatus.CANCELED,
                'rejected': OrderStatus.REJECTED,
                'expired': OrderStatus.EXPIRED,
            }

            new_status = status_map.get(broker_order['status'], order.status)

            if new_status != order.status:
                order.status = new_status
                order.updated_at = datetime.now()

                # Update fill information
                order.filled_quantity = broker_order.get('filled_qty', 0)
                order.filled_avg_price = broker_order.get('filled_avg_price')

                logger.info(f"Order {order_id} status updated: {new_status.value}")

                # Move to history if terminal state
                if new_status in [OrderStatus.FILLED, OrderStatus.CANCELED,
                                 OrderStatus.REJECTED, OrderStatus.EXPIRED]:
                    self._archive_order(order_id)

        except Exception as e:
            logger.error(f"Failed to update order {order_id}: {e}")

    def _archive_order(self, order_id: str):
        """Move order to history."""
        if order_id in self.orders:
            order = self.orders[order_id]
            self.order_history.append(order)
            del self.orders[order_id]
            logger.debug(f"Order {order_id} archived")

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get order by ID."""
        return self.orders.get(order_id)

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get all open orders."""
        orders = [o for o in self.orders.values()
                 if o.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL]]

        if symbol:
            orders = [o for o in orders if o.symbol == symbol]

        return orders

    def get_orders_by_strategy(self, strategy_id: str) -> List[Order]:
        """Get orders by strategy ID."""
        return [o for o in self.orders.values() if o.strategy_id == strategy_id]

    def get_order_history(self, limit: int = 100) -> List[Order]:
        """Get recent order history."""
        return self.order_history[-limit:]

    def cancel_all_orders(self, symbol: Optional[str] = None):
        """Cancel all open orders."""
        orders = self.get_open_orders(symbol)

        for order in orders:
            self.cancel_order(order.id)

        logger.info(f"Canceled {len(orders)} orders")

    def update_all_orders(self):
        """Update status of all open orders."""
        orders = self.get_open_orders()

        for order in orders:
            self.update_order_status(order.id)

    def _validate_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType,
        limit_price: Optional[float],
        stop_price: Optional[float],
    ):
        """Validate order parameters."""
        if not symbol:
            raise ValueError("Symbol is required")

        if quantity <= 0:
            raise ValueError("Quantity must be positive")

        if order_type == OrderType.LIMIT and limit_price is None:
            raise ValueError("Limit price required for limit orders")

        if order_type == OrderType.STOP and stop_price is None:
            raise ValueError("Stop price required for stop orders")

        if order_type == OrderType.STOP_LIMIT and (limit_price is None or stop_price is None):
            raise ValueError("Both limit and stop price required for stop-limit orders")

        if limit_price is not None and limit_price <= 0:
            raise ValueError("Limit price must be positive")

        if stop_price is not None and stop_price <= 0:
            raise ValueError("Stop price must be positive")

    def get_summary(self) -> Dict:
        """Get order manager summary."""
        open_orders = self.get_open_orders()

        status_counts = {}
        for order in self.orders.values():
            status = order.status.value
            status_counts[status] = status_counts.get(status, 0) + 1

        return {
            'total_open_orders': len(open_orders),
            'total_history': len(self.order_history),
            'status_counts': status_counts,
        }
