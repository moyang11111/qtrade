"""
Risk monitoring and circuit breakers.

Provides:
- Real-time risk metrics monitoring
- Drawdown detection and circuit breakers
- Position limit enforcement
- Slippage monitoring
- Emergency stop functionality
"""

from typing import Optional, Dict, List, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from loguru import logger


class RiskLevel(Enum):
    """Risk level indicators."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class RiskEvent:
    """Risk event record."""
    timestamp: datetime
    event_type: str
    level: RiskLevel
    message: str
    details: Dict = field(default_factory=dict)
    action_taken: str = ""


class CircuitBreaker:
    """Circuit breaker for emergency stops."""

    def __init__(self):
        self.tripped = False
        self.trip_reason = ""
        self.trip_time: Optional[datetime] = None
        self.cooldown_minutes = 30
        self.callbacks = []

    def trip(self, reason: str):
        """Trip the circuit breaker."""
        if not self.tripped:
            self.tripped = True
            self.trip_reason = reason
            self.trip_time = datetime.now()

            logger.critical(f"🚨 CIRCUIT BREAKER TRIPPED: {reason}")

            # Notify callbacks
            for callback in self.callbacks:
                callback(reason)

    def reset(self):
        """Reset circuit breaker."""
        if self.tripped:
            self.tripped = False
            self.trip_reason = ""
            self.trip_time = None
            logger.info("Circuit breaker reset")

    def is_tripped(self) -> bool:
        """Check if circuit breaker is tripped."""
        if not self.tripped:
            return False

        # Check cooldown
        if self.trip_time:
            elapsed = datetime.now() - self.trip_time
            if elapsed.total_seconds() > self.cooldown_minutes * 60:
                logger.info("Circuit breaker cooldown expired")
                self.reset()
                return False

        return True

    def on_trip(self, callback: Callable[[str], None]):
        """Register trip callback."""
        self.callbacks.append(callback)


class RiskMonitor:
    """Real-time risk monitoring system."""

    def __init__(
        self,
        max_drawdown_pct: float = 10.0,
        max_daily_loss_pct: float = 5.0,
        max_position_pct: float = 20.0,
        max_slippage_pct: float = 1.0,
        max_orders_per_minute: int = 10,
    ):
        """
        Args:
            max_drawdown_pct: Maximum portfolio drawdown percentage
            max_daily_loss_pct: Maximum daily loss percentage
            max_position_pct: Maximum position size as % of portfolio
            max_slippage_pct: Maximum acceptable slippage percentage
            max_orders_per_minute: Rate limit for order submission
        """
        self.max_drawdown_pct = max_drawdown_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_pct = max_position_pct
        self.max_slippage_pct = max_slippage_pct
        self.max_orders_per_minute = max_orders_per_minute

        self.circuit_breaker = CircuitBreaker()
        self.risk_events: List[RiskEvent] = []
        self.alerts_enabled = True

        # Tracking variables
        self.peak_portfolio_value = 0.0
        self.daily_start_value = 0.0
        self.daily_date: Optional[datetime] = None
        self.order_timestamps: List[datetime] = []
        self.expected_prices: Dict[str, float] = {}

        logger.info("RiskMonitor initialized")
        logger.info(f"  Max drawdown: {max_drawdown_pct}%")
        logger.info(f"  Max daily loss: {max_daily_loss_pct}%")
        logger.info(f"  Max position: {max_position_pct}%")

    def update_portfolio_value(self, value: float):
        """Update portfolio value and check risk limits."""
        # Track peak value
        if value > self.peak_portfolio_value:
            self.peak_portfolio_value = value

        # Initialize daily tracking
        today = datetime.now().date()
        if self.daily_date != today:
            self.daily_date = today
            self.daily_start_value = value

        # Check drawdown
        if self.peak_portfolio_value > 0:
            drawdown_pct = (self.peak_portfolio_value - value) / self.peak_portfolio_value * 100

            if drawdown_pct >= self.max_drawdown_pct:
                self._trigger_circuit_breaker(
                    f"Max drawdown exceeded: {drawdown_pct:.2f}% >= {self.max_drawdown_pct}%"
                )
                self._log_risk_event(
                    "drawdown_limit",
                    RiskLevel.CRITICAL,
                    f"Drawdown limit hit: {drawdown_pct:.2f}%",
                    {'drawdown_pct': drawdown_pct, 'limit_pct': self.max_drawdown_pct}
                )

            elif drawdown_pct >= self.max_drawdown_pct * 0.8:
                self._log_risk_event(
                    "drawdown_warning",
                    RiskLevel.HIGH,
                    f"Drawdown approaching limit: {drawdown_pct:.2f}%",
                    {'drawdown_pct': drawdown_pct}
                )

        # Check daily loss
        if self.daily_start_value > 0:
            daily_pnl_pct = (value - self.daily_start_value) / self.daily_start_value * 100

            if daily_pnl_pct <= -self.max_daily_loss_pct:
                self._trigger_circuit_breaker(
                    f"Max daily loss exceeded: {daily_pnl_pct:.2f}% <= -{self.max_daily_loss_pct}%"
                )
                self._log_risk_event(
                    "daily_loss_limit",
                    RiskLevel.CRITICAL,
                    f"Daily loss limit hit: {daily_pnl_pct:.2f}%",
                    {'daily_pnl_pct': daily_pnl_pct, 'limit_pct': self.max_daily_loss_pct}
                )

    def check_position_limit(self, symbol: str, position_value: float,
                            portfolio_value: float) -> bool:
        """
        Check if position exceeds limit.

        Returns:
            True if position is within limit
        """
        if portfolio_value <= 0:
            return True

        position_pct = position_value / portfolio_value * 100

        if position_pct > self.max_position_pct:
            self._log_risk_event(
                "position_limit",
                RiskLevel.HIGH,
                f"Position limit exceeded: {symbol} at {position_pct:.2f}%",
                {'symbol': symbol, 'position_pct': position_pct, 'limit_pct': self.max_position_pct}
            )
            return False

        return True

    def record_order(self):
        """Record order submission for rate limiting."""
        now = datetime.now()
        self.order_timestamps.append(now)

        # Remove old timestamps (older than 1 minute)
        cutoff = now - timedelta(minutes=1)
        self.order_timestamps = [t for t in self.order_timestamps if t > cutoff]

        # Check rate limit
        if len(self.order_timestamps) > self.max_orders_per_minute:
            self._log_risk_event(
                "rate_limit",
                RiskLevel.MEDIUM,
                f"Order rate limit exceeded: {len(self.order_timestamps)} orders/min",
                {'count': len(self.order_timestamps), 'limit': self.max_orders_per_minute}
            )
            return False

        return True

    def set_expected_price(self, symbol: str, price: float):
        """Set expected price for slippage monitoring."""
        self.expected_prices[symbol] = price

    def check_slippage(self, symbol: str, execution_price: float) -> bool:
        """
        Check execution slippage.

        Returns:
            True if slippage is acceptable
        """
        if symbol not in self.expected_prices:
            return True

        expected_price = self.expected_prices[symbol]
        slippage_pct = abs(execution_price - expected_price) / expected_price * 100

        # Clean up
        del self.expected_prices[symbol]

        if slippage_pct > self.max_slippage_pct:
            self._log_risk_event(
                "slippage",
                RiskLevel.MEDIUM,
                f"High slippage on {symbol}: {slippage_pct:.2f}%",
                {
                    'symbol': symbol,
                    'expected_price': expected_price,
                    'execution_price': execution_price,
                    'slippage_pct': slippage_pct,
                }
            )
            return False

        return True

    def can_trade(self) -> bool:
        """Check if trading is allowed."""
        return not self.circuit_breaker.is_tripped()

    def emergency_stop(self, reason: str = "Manual emergency stop"):
        """Trigger emergency stop."""
        self._trigger_circuit_breaker(reason)
        self._log_risk_event(
            "emergency_stop",
            RiskLevel.CRITICAL,
            reason,
            {}
        )

    def _trigger_circuit_breaker(self, reason: str):
        """Trip circuit breaker."""
        self.circuit_breaker.trip(reason)

    def _log_risk_event(self, event_type: str, level: RiskLevel,
                       message: str, details: Dict):
        """Log risk event."""
        event = RiskEvent(
            timestamp=datetime.now(),
            event_type=event_type,
            level=level,
            message=message,
            details=details,
        )

        self.risk_events.append(event)

        # Log based on level
        if level == RiskLevel.CRITICAL:
            logger.critical(f"🚨 RISK: {message}")
        elif level == RiskLevel.HIGH:
            logger.error(f"⚠️ RISK: {message}")
        elif level == RiskLevel.MEDIUM:
            logger.warning(f"⚡ RISK: {message}")
        else:
            logger.info(f"ℹ️ RISK: {message}")

    def get_risk_events(self, limit: int = 100) -> List[RiskEvent]:
        """Get recent risk events."""
        return self.risk_events[-limit:]

    def get_risk_summary(self) -> Dict:
        """Get risk monitoring summary."""
        # Count events by level
        event_counts = {}
        for event in self.risk_events[-100:]:  # Last 100 events
            level = event.level.value
            event_counts[level] = event_counts.get(level, 0) + 1

        return {
            'circuit_breaker_tripped': self.circuit_breaker.is_tripped(),
            'trip_reason': self.circuit_breaker.trip_reason if self.circuit_breaker.tripped else None,
            'event_counts': event_counts,
            'can_trade': self.can_trade(),
            'max_drawdown_pct': self.max_drawdown_pct,
            'max_daily_loss_pct': self.max_daily_loss_pct,
            'max_position_pct': self.max_position_pct,
        }

    def reset(self):
        """Reset risk monitor."""
        self.circuit_breaker.reset()
        self.risk_events.clear()
        self.peak_portfolio_value = 0.0
        self.daily_start_value = 0.0
        self.order_timestamps.clear()
        self.expected_prices.clear()

        logger.info("Risk monitor reset")
