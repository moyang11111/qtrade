import numpy as np
import pandas as pd

from qtrade.features.engine import FeatureEngine


def make_df(n=400):
    idx = pd.date_range("2023-01-01", periods=n)
    close = 10 + np.sin(np.arange(n) / 10) + np.arange(n) * 0.001
    return pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "volume": np.full(n, 10000),
    }, index=idx)


def test_feature_engine_computes_all_features():
    fe = FeatureEngine({})
    out = fe.compute_features(make_df())
    cols = fe.get_feature_columns()
    assert len(cols) == 27
    assert set(cols).issubset(out.columns)


def test_feature_engine_anti_lookahead_target():
    fe = FeatureEngine({})
    out = fe.compute_features_and_target(make_df(), horizon=5, threshold=0.02)
    assert "target" in out.columns
