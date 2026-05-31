"""Circuit breaker controls."""
from typing import Dict, Optional
import pandas as pd
from loguru import logger


class CircuitBreaker:
    """Circuit breaker for trading halts."""

    def __init__(self, max_daily_loss_pct: float = 0.05,
                 max_weekly_loss_pct: float = 0.10,
                 cooldown_days: int = 1):
        """Initialize circuit breaker.

        Args:
            max_daily_loss_pct: Maximum daily loss percentage
            max_weekly_loss_pct: Maximum weekly loss percentage
            cooldown_days: Cooldown days after trigger
        """
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_weekly_loss_pct = max_weekly_loss_pct
        self.cooldown_days = cooldown_days
        self.daily_pnl = []
        self.triggered = False
        self.cooldown_until = None

    def update_daily_pnl(self, date, pnl_pct: float):
        """Update daily P&L.

        Args:
            date: Date
            pnl_pct: Daily P&L percentage
        """
        self.daily_pnl.append({
            'date': date,
            'pnl_pct': pnl_pct,
        })

        # Check circuit breaker
        self._check_breaker(date)

    def _check_breaker(self, current_date):
        """Check if circuit breaker should trigger."""
        if not self.daily_pnl:
            return

        # Check daily loss
        latest_pnl = self.daily_pnl[-1]['pnl_pct']
        if latest_pnl <= -self.max_daily_loss_pct:
            self._trigger_breaker(current_date, f"Daily loss: {latest_pnl:.2%}")
            return

        # Check weekly loss (last 5 days)
        if len(self.daily_pnl) >= 5:
            weekly_pnl = sum(d['pnl_pct'] for d in self.daily_pnl[-5:])
            if weekly_pnl <= -self.max_weekly_loss_pct:
                self._trigger_breaker(current_date, f"Weekly loss: {weekly_pnl:.2%}")

    def _trigger_breaker(self, current_date, reason: str):
        """Trigger circuit breaker."""
        self.triggered = True
        self.cooldown_until = current_date + pd.Timedelta(days=self.cooldown_days)
        logger.warning(f"Circuit breaker triggered: {reason}. Cooldown until {self.cooldown_until}")

    def is_trading_allowed(self, current_date) -> Dict:
        """Check if trading is allowed.

        Args:
            current_date: Current date

        Returns:
            Dictionary with trading status
        """
        if not self.triggered:
            return {'allowed': True, 'reason': None}

        if current_date >= self.cooldown_until:
            # Reset circuit breaker
            self.triggered = False
            self.cooldown_until = None
            logger.info("Circuit breaker reset, trading resumed")
            return {'allowed': True, 'reason': None}

        return {
            'allowed': False,
            'reason': f"Circuit breaker active until {self.cooldown_until}",
            'cooldown_until': self.cooldown_until,
        }

    def reset(self):
        """Reset circuit breaker."""
        self.triggered = False
        self.cooldown_until = None
        logger.info("Circuit breaker manually reset")


class DrawdownBreaker:
    """Drawdown-based circuit breaker."""

    def __init__(self, max_drawdown_pct: float = 0.20,
                 recovery_threshold_pct: float = 0.10):
        """Initialize drawdown breaker.

        Args:
            max_drawdown_pct: Maximum drawdown percentage
            recovery_threshold_pct: Recovery threshold to resume trading
        """
        self.max_drawdown_pct = max_drawdown_pct
        self.recovery_threshold_pct = recovery_threshold_pct
        self.peak_value = None
        self.current_value = None
        self.triggered = False

    def update_portfolio_value(self, value: float):
        """Update portfolio value.

        Args:
            value: Current portfolio value
        """
        self.current_value = value

        if self.peak_value is None or value > self.peak_value:
            self.peak_value = value

        # Check drawdown
        self._check_drawdown()

    def _check_drawdown(self):
        """Check if drawdown breaker should trigger."""
        if self.peak_value is None or self.current_value is None:
            return

        drawdown = (self.peak_value - self.current_value) / self.peak_value

        if not self.triggered and drawdown >= self.max_drawdown_pct:
            self.triggered = True
            logger.warning(f"Drawdown breaker triggered: {drawdown:.2%} drawdown")

        elif self.triggered:
            # Check recovery
            recovery = (self.current_value - self.peak_value * (1 - self.max_drawdown_pct)) / self.peak_value
            if recovery >= self.recovery_threshold_pct:
                self.triggered = False
                self.peak_value = self.current_value  # Reset peak
                logger.info(f"Drawdown breaker reset: recovered {recovery:.2%}")

    def is_trading_allowed(self) -> Dict:
        """Check if trading is allowed."""
        if not self.triggered:
            return {'allowed': True, 'reason': None}

        drawdown = (self.peak_value - self.current_value) / self.peak_value if self.peak_value else 0

        return {
            'allowed': False,
            'reason': f"Drawdown breaker active: {drawdown:.2%} drawdown",
            'drawdown': drawdown,
        }

    def get_drawdown(self) -> float:
        """Get current drawdown."""
        if self.peak_value is None or self.current_value is None:
            return 0.0

        return (self.peak_value - self.current_value) / self.peak_value

    def reset(self):
        """Reset drawdown breaker."""
        self.triggered = False
        if self.current_value is not None:
            self.peak_value = self.current_value
        logger.info("Drawdown breaker manually reset")
