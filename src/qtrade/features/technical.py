"""Technical indicator features — all backward-looking (shift(1) applied)."""

import pandas as pd
import numpy as np


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    return rsi.shift(1) / 100.0  # normalize to [0,1], shift for anti-lookahead


def compute_macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    signal = (ema12 - ema26).ewm(span=9).mean()
    hist = (ema12 - ema26) - signal
    return (hist / close).shift(1)


def compute_bb_position(close: pd.Series, period: int = 20) -> pd.Series:
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    pos = (close - lower) / (upper - lower)
    return pos.clip(0, 1).shift(1)


def compute_ma_ratio(close: pd.Series, fast: int, slow: int) -> pd.Series:
    ma_f = close.rolling(fast).mean()
    ma_s = close.rolling(slow).mean()
    return (ma_f / ma_s - 1).shift(1)


def compute_atr_ratio(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return (atr / df["close"]).shift(1)


def compute_long_ma(close: pd.Series, period: int = 60) -> pd.Series:
    """Long-period moving average ratio (60/120/250 days)."""
    ma = close.rolling(period).mean()
    return (close / ma - 1).shift(1)


def compute_long_atr(df: pd.DataFrame, period: int = 60) -> pd.Series:
    """Long-period ATR ratio for trend strength."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    return (atr / df["close"]).shift(1)


def compute_trend_strength(close: pd.Series, period: int = 20) -> pd.Series:
    """Trend strength: R-squared of linear regression."""
    def r_squared(x):
        if len(x) < 5:
            return 0
        y = np.arange(len(x))
        corr = np.corrcoef(x, y)[0, 1]
        return corr ** 2 if not np.isnan(corr) else 0

    return close.rolling(period).apply(r_squared, raw=True).shift(1)
