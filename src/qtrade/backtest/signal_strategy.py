"""SignalFollower — universal bt.Strategy with configurable position sizing."""

import logging
from datetime import date

import backtrader as bt

logger = logging.getLogger("qtrade.backtest.signal_strategy")


class SignalFollower(bt.Strategy):
    """Reads signal_action from data feed, executes trades.

    Does NOT know whether signals came from rules or ML.
    Supports multiple position sizing methods.
    """

    params = dict(
        # Broker
        lot_size=100,
        # Position sizing
        sizing_method="strength",  # fixed, strength, atr
        base_position_pct=0.95,
        min_strength=0.3,
        # Risk management
        stop_loss_pct=0.15,
        trail_stop_pct=0.10,
        take_profit_pct=0.0,  # 0 = disabled
        # ATR-based sizing
        risk_per_trade=0.02,
        atr_period=14,
        atr_stop_mult=2.0,
        # Martingale re-buy (after stop loss)
        martingale_enabled=False,
        martingale_drop_pct=0.05,   # re-buy every 5% drop from stop price
        martingale_size_pct=0.20,   # 20% position per re-buy
        martingale_max_levels=5,    # max 5 re-buy levels
    )

    def __init__(self):
        self.order = None
        self.entry_price = None
        self.highest = 0
        self.trade_log = []
        self.equity_curve = []
        self._entry_date = None

        # Martingale state
        self._stop_loss_price = None  # price where stop loss was triggered
        self._martingale_level = 0    # current martingale re-buy level

        # ATR indicator for ATR-based sizing
        if self.p.sizing_method == "atr":
            self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        else:
            self.atr = None

    def notify_order(self, order):
        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price = order.executed.price
                self.highest = order.executed.price
                self._entry_date = self.data.datetime.date(0)
                logger.debug("BUY  @ %.2f x %d on %s",
                             order.executed.price, order.executed.size,
                             self._entry_date)
            elif order.issell():
                pnl = order.executed.pnl
                entry = self.entry_price or 0
                exit_price = order.executed.price
                pnl_pct = (exit_price / entry - 1) * 100 if entry > 0 else 0
                bars = self._count_bars()
                self.trade_log.append({
                    "entry_date": str(self._entry_date),
                    "exit_date": str(self.data.datetime.date(0)),
                    "entry_price": round(entry, 2),
                    "exit_price": round(exit_price, 2),
                    "size": order.executed.size,
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "bars": bars,
                })
                logger.debug("SELL @ %.2f  PnL=%+.2f (%+.2f%%) %d bars",
                             exit_price, pnl, pnl_pct, bars)
                # Track stop loss for martingale
                if self.p.martingale_enabled and self.entry_price:
                    stop_price = self.entry_price * (1 - self.p.stop_loss_pct)
                    if exit_price <= stop_price * 1.01:  # within 1% of stop price
                        self._stop_loss_price = exit_price
                        self._martingale_level = 0
                        logger.debug("STOP LOSS @ %.2f, martingale armed", exit_price)
                self.entry_price = None
            self.order = None
        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            self.order = None

    def next(self):
        # Record equity every bar
        self.equity_curve.append({
            "date": self.data.datetime.date(0),
            "value": self.broker.getvalue(),
            "cash": self.broker.getcash(),
        })

        if self.order:
            return

        action = self.data.signal_action[0]
        strength = max(0.0, self.data.signal_strength[0])

        if self.position:
            self.highest = max(self.highest, self.data.close[0])

            # Take profit
            if (self.p.take_profit_pct > 0 and self.entry_price and
                self.data.close[0] >= self.entry_price * (1 + self.p.take_profit_pct)):
                self.order = self.close()
                self._stop_loss_price = None  # reset martingale on take profit
                self._martingale_level = 0
                return

            # Hard stop-loss
            if self.entry_price and self.data.close[0] < self.entry_price * (1 - self.p.stop_loss_pct):
                self.order = self.close()
                return

            # Trailing stop
            if self.data.close[0] < self.highest * (1 - self.p.trail_stop_pct):
                self.order = self.close()
                return

            # Signal-based exit
            if action == -1:
                self.order = self.close()
                self._stop_loss_price = None  # reset martingale on signal exit
                self._martingale_level = 0
                return

        else:
            # Martingale re-buy: after stop loss, re-buy on further drops
            if (self.p.martingale_enabled and self._stop_loss_price and
                self._martingale_level < self.p.martingale_max_levels):
                re_buy_price = self._stop_loss_price * (1 - self.p.martingale_drop_pct * (self._martingale_level + 1))
                if self.data.close[0] <= re_buy_price:
                    size = self._calc_martingale_size(self.p.martingale_size_pct)
                    if size >= self.p.lot_size:
                        self.order = self.buy(size=size)
                        self._martingale_level += 1
                        logger.debug("MARTINGALE BUY level=%d @ %.2f", self._martingale_level, self.data.close[0])
                        return

            # Signal-based entry
            if action == 1:
                size = self._calc_position_size(strength)
                if size >= self.p.lot_size:
                    self.order = self.buy(size=size)
                    self._stop_loss_price = None  # reset martingale on fresh entry
                    self._martingale_level = 0

    def _calc_position_size(self, strength: float) -> int:
        """Calculate position size based on sizing method."""
        cash = self.broker.getcash()
        price = self.data.close[0]

        if self.p.sizing_method == "fixed":
            pct = self.p.base_position_pct
            size = int(cash * pct / price)

        elif self.p.sizing_method == "strength":
            pct = self.p.base_position_pct
            scaled = max(self.p.min_strength, strength)
            size = int(cash * pct * scaled / price)

        elif self.p.sizing_method == "atr":
            if self.atr is None or self.atr[0] <= 0:
                return 0
            risk_cash = self.broker.getvalue() * self.p.risk_per_trade
            stop_dist = self.p.atr_stop_mult * self.atr[0]
            size = int(risk_cash / stop_dist) if stop_dist > 0 else 0
            max_size = int(cash * self.p.base_position_pct / price)
            size = min(size, max_size)

        else:
            size = int(cash * self.p.base_position_pct / price)

        # A-share lot rounding
        size = (size // self.p.lot_size) * self.p.lot_size
        return size

    def _count_bars(self) -> int:
        if self._entry_date is None:
            return 0
        now = self.data.datetime.date(0)
        # Approximate trading days (5/7 of calendar days)
        return max(1, int((now - self._entry_date).days * 5 / 7))

    def _calc_martingale_size(self, size_pct: float) -> int:
        """Calculate martingale re-buy position size."""
        cash = self.broker.getcash()
        price = self.data.close[0]
        size = int(cash * size_pct / price)
        # A-share lot rounding
        size = (size // self.p.lot_size) * self.p.lot_size
        return size

    def stop(self):
        """Called at end of backtest."""
        logger.debug("Strategy complete: %d trades logged", len(self.trade_log))
