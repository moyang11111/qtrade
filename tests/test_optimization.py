import numpy as np
import pandas as pd

from qtrade.optimization.grid_search import GridSearchOptimizer
from qtrade.strategy.rule.dual_ma import DualMASignal


def make_df(n=300):
    idx = pd.date_range("2023-01-01", periods=n)
    rng = np.random.default_rng(42)
    close = 10 + np.cumsum(rng.normal(0, 0.1, n))
    return pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": rng.integers(10000, 20000, n),
    }, index=idx)


def test_grid_search_returns_best_params():
    df = make_df()

    def objective(strategy, data):
        sig = strategy.generate_signals(data)
        return int((sig["signal_action"] != 0).sum())

    opt = GridSearchOptimizer(
        DualMASignal,
        {"fast_period": [5, 10], "slow_period": [20, 30]},
        objective,
        constraints=["fast_period < slow_period"],
    )
    best = opt.optimize(df)
    assert best["best_params"] is not None
    assert best["best_score"] > 0
    assert best["best_params"]["fast_period"] < best["best_params"]["slow_period"]
