"""Risk management middleware."""
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger

from .limits import PositionLimits, PortfolioLimits
from .stop_loss import StopLossManager
from .circuit_breaker import CircuitBreaker, DrawdownBreaker


class RiskMiddleware:
    """Comprehensive risk management middleware."""

    def __init__(self, position_limits: Optional[PositionLimits] = None,
                 portfolio_limits: Optional[PortfolioLimits] = None,
                 stop_loss_manager: Optional[StopLossManager] = None,
                 circuit_breaker: Optional[CircuitBreaker] = None,
                 drawdown_breaker: Optional[DrawdownBreaker] = None):
        """Initialize risk middleware.

        Args:
            position_limits: Position limit controls
            portfolio_limits: Portfolio limit controls
            stop_loss_manager: Stop-loss manager
            circuit_breaker: Circuit breaker
            drawdown_breaker: Drawdown breaker
        """
        self.position_limits = position_limits or PositionLimits()
        self.portfolio_limits = portfolio_limits or PortfolioLimits()
        self.stop_loss_manager = stop_loss_manager or StopLossManager()
        self.circuit_breaker = circuit_breaker
        self.drawdown_breaker = drawdown_breaker
        self.risk_events = []

    def check_trade(self, symbol: str, proposed_shares: int,
                   current_price: float, current_positions: Dict[str, float],
                   portfolio_value: float, current_date=None) -> Dict:
        """Check if trade is allowed.

        Args:
            symbol: Stock symbol
            proposed_shares: Proposed shares to buy
            current_price: Current price
            current_positions: Current positions (symbol -> value)
            portfolio_value: Total portfolio value
            current_date: Current date

        Returns:
            Dictionary with trade approval status
        """
        # Check circuit breakers
        if self.circuit_breaker:
            breaker_status = self.circuit_breaker.is_trading_allowed(current_date)
            if not breaker_status['allowed']:
                return {
                    'approved': False,
                    'reason': breaker_status['reason'],
                    'risk_type': 'circuit_breaker',
                }

        if self.drawdown_breaker:
            breaker_status = self.drawdown_breaker.is_trading_allowed()
            if not breaker_status['allowed']:
                return {
                    'approved': False,
                    'reason': breaker_status['reason'],
                    'risk_type': 'drawdown_breaker',
                }

        # Check position limits
        current_shares = int(current_positions.get(symbol, 0) / current_price)
        position_check = self.position_limits.check_limit(
            symbol, current_shares, proposed_shares, current_price, portfolio_value
        )

        if not position_check['approved']:
            return {
                'approved': False,
                'reason': 'Position limit violated',
                'risk_type': 'position_limit',
                'details': position_check,
            }

        # Check portfolio limits
        new_positions = current_positions.copy()
        new_positions[symbol] = current_positions.get(symbol, 0) + proposed_shares * current_price

        total_exposure_check = self.portfolio_limits.check_total_exposure(
            new_positions, portfolio_value
        )

        if not total_exposure_check['approved']:
            return {
                'approved': False,
                'reason': 'Total exposure limit violated',
                'risk_type': 'portfolio_limit',
                'details': total_exposure_check,
            }

        concentration_check = self.portfolio_limits.check_concentration(
            new_positions, portfolio_value
        )

        if not concentration_check['approved']:
            return {
                'approved': False,
                'reason': 'Position concentration limit violated',
                'risk_type': 'concentration_limit',
                'details': concentration_check,
            }

        # All checks passed
        return {
            'approved': True,
            'reason': None,
        }

    def check_exit(self, symbol: str, current_price: float,
                  current_date=None) -> Dict:
        """Check if position should be exited.

        Args:
            symbol: Stock symbol
            current_price: Current price
            current_date: Current date

        Returns:
            Dictionary with exit recommendation
        """
        # Check stop-loss
        stop_check = self.stop_loss_manager.check_stop_loss(
            symbol, current_price, current_date
        )

        if stop_check['triggered']:
            return {
                'should_exit': True,
                'reason': stop_check['reason'],
                'risk_type': 'stop_loss',
            }

        return {
            'should_exit': False,
            'reason': None,
        }

    def update_portfolio_value(self, value: float, date=None, pnl_pct: float = 0):
        """Update portfolio value and check circuit breakers.

        Args:
            value: Current portfolio value
            date: Current date
            pnl_pct: Daily P&L percentage
        """
        if self.drawdown_breaker:
            self.drawdown_breaker.update_portfolio_value(value)

        if self.circuit_breaker and date is not None:
            self.circuit_breaker.update_daily_pnl(date, pnl_pct)

    def record_risk_event(self, event_type: str, details: Dict):
        """Record a risk event.

        Args:
            event_type: Type of risk event
            details: Event details
        """
        self.risk_events.append({
            'type': event_type,
            'details': details,
            'timestamp': pd.Timestamp.now(),
        })
        logger.info(f"Risk event recorded: {event_type}")

    def get_risk_events(self) -> List[Dict]:
        """Get all risk events."""
        return self.risk_events

    def summary(self) -> Dict:
        """Get risk middleware summary."""
        return {
            'position_limits': self.position_limits.__dict__,
            'portfolio_limits': {
                'max_total_position_pct': self.portfolio_limits.max_total_position_pct,
                'max_single_position_pct': self.portfolio_limits.max_single_position_pct,
            },
            'active_stops': self.stop_loss_manager.summary(),
            'circuit_breaker_active': self.circuit_breaker.triggered if self.circuit_breaker else False,
            'drawdown_breaker_active': self.drawdown_breaker.triggered if self.drawdown_breaker else False,
            'risk_events_count': len(self.risk_events),
        }
