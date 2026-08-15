import numpy as np
import pandas as pd

from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy.rule.dual_ma import DualMASignal


def make_df(n=300):
    idx = pd.date_range("2023-01-01", periods=n)
    rng = np.random.default_rng(7)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": rng.integers(10000, 20000, n),
    }, index=idx)


def test_backtest_runs_with_ashare_costs():
    df = make_df()
    sig = DualMASignal({"name": "dual_ma", "fast_period": 5, "slow_period": 20}).generate_signals(df)
    cfg = {
        "backtest": {
            "initial_capital": 100000,
            "commission": 0.0003,
            "min_commission": 5.0,
            "commission_type": "percentage",
            "stamp_duty": 0.001,
            "stamp_duty_side": "sell",
            "slippage": 0.001,
            "slippage_type": "percentage",
            "lot_size": 100,
            "t_plus_n": 1,
            "stop_loss_pct": 0.15,
            "trail_stop_pct": 0.10,
            "max_position_pct": 0.95,
        }
    }
    result = BacktestEngine(cfg).run(sig)
    assert "total_return" in result.metrics
    assert "final_value" in result.metrics
    assert result.equity_curve is not None
