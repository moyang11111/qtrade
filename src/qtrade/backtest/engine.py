"""BacktestEngine — orchestrates backtrader with full broker parameterization."""

import logging

import pandas as pd
import backtrader as bt

from qtrade.backtest.analyzers import add_standard_analyzers
from qtrade.backtest.data_feed import SignalPandasData
from qtrade.backtest.signal_strategy import SignalFollower
from qtrade.backtest.broker_config import BrokerConfig, PositionSizingConfig
from qtrade.backtest.performance import BacktestResult, calc_metrics

logger = logging.getLogger("qtrade.backtest.engine")


class BacktestEngine:
    """Parameterized backtest engine wrapping backtrader."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        bt_cfg = cfg.get("backtest", {})
        sizing_cfg = cfg.get("position_sizing", {})

        self.bt_cfg = bt_cfg  # Store for later use in run()
        self.broker_cfg = BrokerConfig.from_dict(bt_cfg)
        self.sizing_cfg = PositionSizingConfig.from_dict(sizing_cfg)

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """Run backtest on DataFrame with signal columns.

        Args:
            df: DataFrame with OHLCV + signal_action/strength/score columns.

        Returns:
            BacktestResult with metrics, trade_log, equity_curve, config.
        """
        cerebro = bt.Cerebro()

        # Data feed
        data = SignalPandasData(dataname=df)
        cerebro.adddata(data)

        # Strategy with full parameters
        cerebro.addstrategy(SignalFollower,
                            lot_size=self.broker_cfg.lot_size,
                            sizing_method=self.sizing_cfg.method,
                            base_position_pct=self.broker_cfg.max_position_pct,
                            min_strength=self.sizing_cfg.min_strength,
                            stop_loss_pct=self.broker_cfg.stop_loss_pct,
                            trail_stop_pct=self.broker_cfg.trail_stop_pct,
                            take_profit_pct=self.bt_cfg.get("take_profit_pct", 0.0),
                            risk_per_trade=self.sizing_cfg.risk_per_trade,
                            atr_period=self.sizing_cfg.atr_period,
                            atr_stop_mult=self.sizing_cfg.atr_stop_mult,
                            martingale_enabled=self.bt_cfg.get("martingale_enabled", False),
                            martingale_drop_pct=self.bt_cfg.get("martingale_drop_pct", 0.05),
                            martingale_size_pct=self.bt_cfg.get("martingale_size_pct", 0.20),
                            martingale_max_levels=self.bt_cfg.get("martingale_max_levels", 5))

        # Broker settings
        bc = self.broker_cfg
        cerebro.broker.setcash(bc.initial_capital)

        # Commission (backtrader doesn't natively support min_commission or stamp_duty,
        # so we use the standard commission + log a note)
        cerebro.broker.setcommission(commission=bc.commission)

        # Slippage
        if bc.slippage_type == "percentage":
            cerebro.broker.set_slippage_perc(perc=bc.slippage)
        else:
            cerebro.broker.set_slippage_fixed(bc.slippage)

        # Analyzers
        add_standard_analyzers(cerebro)

        # Run
        logger.info("Backtest config: capital=%.0f, commission=%.4f%%, slippage=%.4f%%, "
                     "lot=%d, stop=%.1f%%, trail=%.1f%%, sizing=%s",
                     bc.initial_capital, bc.commission * 100, bc.slippage * 100,
                     bc.lot_size, bc.stop_loss_pct * 100, bc.trail_stop_pct * 100,
                     self.sizing_cfg.method)
        logger.info("Running backtest: bars=%d", len(df))

        results = cerebro.run()

        # Collect results
        strat = results[0]
        metrics = calc_metrics(results, bc.initial_capital, len(df))
        trade_log = strat.trade_log if hasattr(strat, "trade_log") else []

        # Equity curve
        equity_data = strat.equity_curve if hasattr(strat, "equity_curve") else []
        equity_curve = pd.Series(dtype=float)
        if equity_data:
            eq_df = pd.DataFrame(equity_data)
            eq_df["date"] = pd.to_datetime(eq_df["date"])
            eq_df.set_index("date", inplace=True)
            equity_curve = eq_df["value"]

        result = BacktestResult(
            metrics=metrics,
            trade_log=trade_log,
            equity_curve=equity_curve,
            config=self.cfg,
        )

        logger.info("Backtest complete: return=%.2f%%, trades=%d, sharpe=%s",
                     metrics["total_return"], metrics["total_trades"],
                     metrics.get("sharpe_ratio", "N/A"))
        return result
