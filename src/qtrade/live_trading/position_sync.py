"""
Position synchronization and tracking.

Handles:
- Real-time position tracking
- Broker position sync
- Position P&L calculation
- Position history
"""

from typing import Optional, Dict, List
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd
from loguru import logger

from .broker import BrokerAdapter, Position


@dataclass
class PositionSnapshot:
    """Position snapshot at a point in time."""
    timestamp: datetime
    symbol: str
    quantity: float
    avg_price: float
    market_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float
    realized_pl: float = 0.0


class PositionSynchronizer:
    """Synchronize and track positions."""

    def __init__(self, broker: BrokerAdapter, sync_interval: float = 5.0):
        """
        Args:
            broker: BrokerAdapter instance
            sync_interval: Seconds between position syncs
        """
        self.broker = broker
        self.sync_interval = sync_interval
        self.positions: Dict[str, Position] = {}
        self.position_history: List[PositionSnapshot] = []
        self.realized_pnl: Dict[str, float] = {}
        self._running = False

        logger.info(f"PositionSynchronizer initialized (interval={sync_interval}s)")

    def start(self):
        """Start position synchronization."""
        self._running = True

        import threading
        thread = threading.Thread(target=self._sync_loop, daemon=True)
        thread.start()

        logger.info("Position synchronization started")

    def stop(self):
        """Stop position synchronization."""
        self._running = False
        logger.info("Position synchronization stopped")

    def _sync_loop(self):
        """Background sync loop."""
        import time

        while self._running:
            try:
                self.sync_positions()
                time.sleep(self.sync_interval)
            except Exception as e:
                logger.error(f"Position sync error: {e}")
                time.sleep(self.sync_interval)

    def sync_positions(self):
        """Synchronize positions from broker."""
        try:
            broker_positions = self.broker.get_positions()

            # Update internal position tracking
            current_symbols = set()

            for pos in broker_positions:
                current_symbols.add(pos.symbol)
                self._update_position(pos)

            # Detect closed positions
            closed_symbols = set(self.positions.keys()) - current_symbols
            for symbol in closed_symbols:
                self._close_position(symbol)

        except Exception as e:
            logger.error(f"Failed to sync positions: {e}")

    def _update_position(self, broker_pos: Position):
        """Update position from broker data."""
        symbol = broker_pos.symbol
        is_new = symbol not in self.positions

        # Store previous position for P&L calculation
        prev_pos = self.positions.get(symbol)

        # Update position
        self.positions[symbol] = broker_pos

        # Create snapshot
        snapshot = PositionSnapshot(
            timestamp=datetime.now(),
            symbol=symbol,
            quantity=broker_pos.quantity,
            avg_price=broker_pos.avg_price,
            market_price=broker_pos.market_value / broker_pos.quantity if broker_pos.quantity > 0 else 0,
            market_value=broker_pos.market_value,
            unrealized_pl=broker_pos.unrealized_pl,
            unrealized_plpc=broker_pos.unrealized_plpc,
        )

        self.position_history.append(snapshot)

        if is_new:
            logger.info(f"New position opened: {symbol} - {broker_pos.quantity} @ {broker_pos.avg_price:.2f}")
        elif prev_pos:
            # Check for quantity changes (fills)
            if abs(broker_pos.quantity - prev_pos.quantity) > 0.01:
                qty_change = broker_pos.quantity - prev_pos.quantity
                logger.info(f"Position changed: {symbol} - {qty_change:+.2f} shares")

    def _close_position(self, symbol: str):
        """Handle position closure."""
        if symbol in self.positions:
            pos = self.positions[symbol]
            realized = pos.unrealized_pl  # Last unrealized becomes realized

            # Track realized P&L
            self.realized_pnl[symbol] = self.realized_pnl.get(symbol, 0) + realized

            logger.info(f"Position closed: {symbol} - Realized P&L: ${realized:,.2f}")

            del self.positions[symbol]

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for symbol."""
        return self.positions.get(symbol)

    def get_all_positions(self) -> List[Position]:
        """Get all current positions."""
        return list(self.positions.values())

    def get_position_history(self, symbol: Optional[str] = None,
                           limit: int = 1000) -> List[PositionSnapshot]:
        """Get position history."""
        history = self.position_history

        if symbol:
            history = [h for h in history if h.symbol == symbol]

        return history[-limit:]

    def get_portfolio_value(self) -> float:
        """Get total portfolio market value."""
        return sum(pos.market_value for pos in self.positions.values())

    def get_unrealized_pnl(self) -> float:
        """Get total unrealized P&L."""
        return sum(pos.unrealized_pl for pos in self.positions.values())

    def get_realized_pnl(self) -> float:
        """Get total realized P&L."""
        return sum(self.realized_pnl.values())

    def get_total_pnl(self) -> float:
        """Get total P&L (realized + unrealized)."""
        return self.get_realized_pnl() + self.get_unrealized_pnl()

    def get_position_summary(self) -> pd.DataFrame:
        """Get position summary as DataFrame."""
        if not self.positions:
            return pd.DataFrame()

        data = []
        for pos in self.positions.values():
            data.append({
                'Symbol': pos.symbol,
                'Quantity': pos.quantity,
                'Avg Price': pos.avg_price,
                'Market Value': pos.market_value,
                'Unrealized P&L': pos.unrealized_pl,
                'Unrealized P&L %': pos.unrealized_plpc,
            })

        return pd.DataFrame(data)

    def get_pnl_by_symbol(self) -> Dict[str, float]:
        """Get P&L breakdown by symbol."""
        pnl = {}

        # Unrealized P&L
        for pos in self.positions.values():
            pnl[pos.symbol] = pnl.get(pos.symbol, 0) + pos.unrealized_pl

        # Realized P&L
        for symbol, realized in self.realized_pnl.items():
            pnl[symbol] = pnl.get(symbol, 0) + realized

        return pnl

    def get_exposure_by_symbol(self) -> Dict[str, float]:
        """Get exposure (market value) by symbol."""
        total_value = self.get_portfolio_value()

        if total_value == 0:
            return {}

        exposure = {}
        for pos in self.positions.values():
            exposure[pos.symbol] = pos.market_value / total_value * 100

        return exposure

    def check_position_limits(self, max_position_pct: float = 20.0) -> List[Dict]:
        """Check if any positions exceed limits."""
        violations = []
        total_value = self.get_portfolio_value()

        if total_value == 0:
            return violations

        for pos in self.positions.values():
            pct = pos.market_value / total_value * 100

            if pct > max_position_pct:
                violations.append({
                    'symbol': pos.symbol,
                    'exposure_pct': pct,
                    'limit_pct': max_position_pct,
                    'market_value': pos.market_value,
                })

        return violations

    def wait_for_position(self, symbol: str, timeout: float = 30.0) -> bool:
        """
        Wait for position to appear (after order submission).

        Args:
            symbol: Symbol to wait for
            timeout: Maximum wait time in seconds

        Returns:
            True if position found
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            self.sync_positions()

            if symbol in self.positions:
                return True

            time.sleep(0.5)

        logger.warning(f"Timeout waiting for position: {symbol}")
        return False

    def wait_for_position_close(self, symbol: str, timeout: float = 30.0) -> bool:
        """
        Wait for position to close.

        Args:
            symbol: Symbol to wait for
            timeout: Maximum wait time in seconds

        Returns:
            True if position closed
        """
        import time
        start_time = time.time()

        while time.time() - start_time < timeout:
            self.sync_positions()

            if symbol not in self.positions:
                return True

            time.sleep(0.5)

        logger.warning(f"Timeout waiting for position close: {symbol}")
        return False
