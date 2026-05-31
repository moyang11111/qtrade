"""Momentum features."""

import pandas as pd


def compute_return(close: pd.Series, period: int) -> pd.Series:
    return close.pct_change(period).shift(1)


def compute_vol_ratio(volume: pd.Series, fast: int = 5, slow: int = 20) -> pd.Series:
    return (volume.rolling(fast).mean() / volume.rolling(slow).mean()).shift(1)


def compute_vol_momentum(volume: pd.Series, period: int = 5) -> pd.Series:
    return (volume / volume.shift(period)).shift(1)
