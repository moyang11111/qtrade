"""OHLCV schema validation."""

import pandas as pd

from qtrade.constants import OHLCV_COLUMNS


def validate_ohlcv(df: pd.DataFrame, label: str = "") -> pd.DataFrame:
    """Validate that a DataFrame has required OHLCV columns and proper types."""
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{label} missing columns: {missing}")

    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError(f"{label} index must be DatetimeIndex, got {type(df.index)}")

    if df.empty:
        raise ValueError(f"{label} DataFrame is empty")

    for col in ["open", "high", "low", "close"]:
        if (df[col] <= 0).any():
            raise ValueError(f"{label} has non-positive {col} values")

    return df
