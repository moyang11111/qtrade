"""Target variable — forward return (for training only)."""

import pandas as pd


def compute_forward_return(close: pd.Series, horizon: int = 5,
                           threshold: float = 0.02) -> pd.Series:
    """Binary target: will close rise > threshold in horizon days?

    IMPORTANT: Uses FUTURE data. Only valid for training, never for inference.
    """
    future_ret = close.shift(-horizon) / close - 1
    return (future_ret > threshold).astype(float)
