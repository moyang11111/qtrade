"""Stop-loss management."""
from enum import Enum
from typing import Dict, Optional
import pandas as pd
from loguru import logger


class StopLossType(Enum):
    """Stop-loss types."""
    FIXED = 'fixed'
    TRAILING = 'trailing'
    ATR = 'atr'
    TIME = 'time'


class StopLossManager:
    """Manage stop-loss levels for positions."""

    def __init__(self):
        self.stop_levels: Dict[str, Dict] = {}

    def set_stop_loss(self, symbol: str, entry_price: float,
                     stop_type: StopLossType, **kwargs):
        """Set stop-loss for a position.

        Args:
            symbol: Stock symbol
            entry_price: Entry price
            stop_type: Type of stop-loss
            **kwargs: Additional parameters for stop-loss type
        """
        stop_info = {
            'symbol': symbol,
            'entry_price': entry_price,
            'stop_type': stop_type,
            'active': True,
        }

        if stop_type == StopLossType.FIXED:
            # Fixed percentage stop-loss
            stop_pct = kwargs.get('stop_pct', 0.05)
            stop_info['stop_price'] = entry_price * (1 - stop_pct)
            stop_info['stop_pct'] = stop_pct

        elif stop_type == StopLossType.TRAILING:
            # Trailing stop-loss
            trail_pct = kwargs.get('trail_pct', 0.05)
            stop_info['trail_pct'] = trail_pct
            stop_info['highest_price'] = entry_price
            stop_info['stop_price'] = entry_price * (1 - trail_pct)

        elif stop_type == StopLossType.ATR:
            # ATR-based stop-loss
            atr = kwargs.get('atr')
            atr_multiplier = kwargs.get('atr_multiplier', 2.0)
            if atr is None:
                raise ValueError("ATR value required for ATR stop-loss")
            stop_info['atr'] = atr
            stop_info['atr_multiplier'] = atr_multiplier
            stop_info['stop_price'] = entry_price - (atr * atr_multiplier)

        elif stop_type == StopLossType.TIME:
            # Time-based stop-loss
            max_hold_days = kwargs.get('max_hold_days', 10)
            stop_info['max_hold_days'] = max_hold_days
            stop_info['entry_date'] = kwargs.get('entry_date')
            stop_info['days_held'] = 0

        self.stop_levels[symbol] = stop_info
        logger.debug(f"Set {stop_type.value} stop-loss for {symbol}")

    def update_trailing_stop(self, symbol: str, current_price: float):
        """Update trailing stop-loss.

        Args:
            symbol: Stock symbol
            current_price: Current price
        """
        if symbol not in self.stop_levels:
            return

        stop_info = self.stop_levels[symbol]

        if stop_info['stop_type'] == StopLossType.TRAILING:
            # Update highest price
            if current_price > stop_info['highest_price']:
                stop_info['highest_price'] = current_price
                stop_info['stop_price'] = current_price * (1 - stop_info['trail_pct'])
                logger.debug(f"Updated trailing stop for {symbol}: {stop_info['stop_price']:.2f}")

    def update_time_stop(self, symbol: str, current_date):
        """Update time-based stop-loss.

        Args:
            symbol: Stock symbol
            current_date: Current date
        """
        if symbol not in self.stop_levels:
            return

        stop_info = self.stop_levels[symbol]

        if stop_info['stop_type'] == StopLossType.TIME:
            if stop_info['entry_date'] is not None:
                days_held = (current_date - stop_info['entry_date']).days
                stop_info['days_held'] = days_held

    def check_stop_loss(self, symbol: str, current_price: float,
                       current_date=None) -> Dict:
        """Check if stop-loss is triggered.

        Args:
            symbol: Stock symbol
            current_price: Current price
            current_date: Current date (for time-based stops)

        Returns:
            Dictionary with stop-loss check results
        """
        if symbol not in self.stop_levels:
            return {'triggered': False, 'reason': None}

        stop_info = self.stop_levels[symbol]

        if not stop_info['active']:
            return {'triggered': False, 'reason': None}

        # Update trailing stop
        self.update_trailing_stop(symbol, current_price)

        # Update time stop
        if current_date is not None:
            self.update_time_stop(symbol, current_date)

        # Check if triggered
        triggered = False
        reason = None

        if stop_info['stop_type'] in [StopLossType.FIXED, StopLossType.TRAILING, StopLossType.ATR]:
            if current_price <= stop_info['stop_price']:
                triggered = True
                reason = f"Price {current_price:.2f} <= stop {stop_info['stop_price']:.2f}"

        elif stop_info['stop_type'] == StopLossType.TIME:
            if stop_info['days_held'] >= stop_info['max_hold_days']:
                triggered = True
                reason = f"Held {stop_info['days_held']} days >= max {stop_info['max_hold_days']}"

        result = {
            'symbol': symbol,
            'triggered': triggered,
            'reason': reason,
            'stop_info': stop_info,
        }

        if triggered:
            logger.warning(f"Stop-loss triggered for {symbol}: {reason}")

        return result

    def deactivate_stop(self, symbol: str):
        """Deactivate stop-loss for a symbol."""
        if symbol in self.stop_levels:
            self.stop_levels[symbol]['active'] = False
            logger.debug(f"Deactivated stop-loss for {symbol}")

    def remove_stop(self, symbol: str):
        """Remove stop-loss for a symbol."""
        if symbol in self.stop_levels:
            del self.stop_levels[symbol]
            logger.debug(f"Removed stop-loss for {symbol}")

    def get_all_stops(self) -> Dict:
        """Get all active stop-losses."""
        return {k: v for k, v in self.stop_levels.items() if v['active']}

    def summary(self) -> Dict:
        """Get stop-loss summary."""
        active_stops = self.get_all_stops()
        by_type = {}

        for stop_info in active_stops.values():
            stop_type = stop_info['stop_type'].value
            by_type[stop_type] = by_type.get(stop_type, 0) + 1

        return {
            'total_active': len(active_stops),
            'by_type': by_type,
        }
