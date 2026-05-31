"""
Live trading integration module.

Provides:
- Broker API adapters (abstract + implementations)
- Real-time market data feeds
- Order management system
- Position synchronization
- Risk monitoring and circuit breakers
- Alert system
- Audit logging
"""

from .broker import BrokerAdapter, MockBroker, AlpacaBroker
from .data_feed import RealtimeDataFeed, WebSocketFeed, PollingFeed
from .order_manager import OrderManager, Order, OrderStatus
from .position_sync import PositionSynchronizer
from .risk_monitor import RiskMonitor, CircuitBreaker
from .alerts import AlertSystem, EmailAlert, WebhookAlert
from .live_trader import LiveTrader

__all__ = [
    'BrokerAdapter',
    'MockBroker',
    'AlpacaBroker',
    'RealtimeDataFeed',
    'WebSocketFeed',
    'PollingFeed',
    'OrderManager',
    'Order',
    'OrderStatus',
    'PositionSynchronizer',
    'RiskMonitor',
    'CircuitBreaker',
    'AlertSystem',
    'EmailAlert',
    'WebhookAlert',
    'LiveTrader',
]
