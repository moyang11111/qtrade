import numpy as np
import pandas as pd
import pytest

from qtrade.strategy.registry import get_signal_generator, list_strategies
from qtrade.constants import SIGNAL_ACTION


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


def test_all_registered_strategies_generate_valid_signals():
    assert len(list_strategies()) >= 15
    df = make_df()
    for name in list_strategies():
        cls = get_signal_generator(name)
        sig = cls({"name": name}).generate_signals(df)
        for col in list(df.columns) + ["signal_action", "signal_strength", "signal_score"]:
            assert col in sig.columns, f"{name}: missing column {col}"
        assert set(sig[SIGNAL_ACTION].dropna().unique()).issubset({-1, 0, 1}), f"{name}: bad action values"


def test_dual_ma_has_no_nan_actions():
    df = make_df()
    sig = get_signal_generator("dual_ma")({"name": "dual_ma"}).generate_signals(df)
    assert sig[SIGNAL_ACTION].notna().all()
