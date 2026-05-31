"""Volatility features."""

import pandas as pd
import numpy as np


def compute_realized_vol(close: pd.Series, period: int = 20) -> pd.Series:
    returns = close.pct_change()
    return (returns.rolling(period).std() * np.sqrt(252)).shift(1)


def compute_vol_regime(close: pd.Series, short: int = 20, long: int = 60) -> pd.Series:
    returns = close.pct_change()
    vol_short = returns.rolling(short).std()
    vol_long = returns.rolling(long).std()
    return (vol_short / vol_long).shift(1)
