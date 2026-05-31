"""Volume-price factors — OBV, VWAP, volume ratio, fund flow proxy."""

import pandas as pd
import numpy as np


def compute_obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume: cumulative volume based on price direction."""
    direction = np.sign(df["close"].diff())
    obv = (direction * df["volume"]).cumsum()
    return obv.shift(1)


def compute_obv_slope(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """OBV trend: slope of OBV over period."""
    obv = compute_obv(df)
    return obv.rolling(period).apply(
        lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] if len(x) > 1 else 0,
        raw=True
    ).shift(1)


def compute_vwap_deviation(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """VWAP deviation: how far price is from volume-weighted average."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    vwap = (typical_price * df["volume"]).rolling(period).sum() / df["volume"].rolling(period).sum()
    return ((df["close"] - vwap) / vwap).shift(1)


def compute_volume_ratio(df: pd.DataFrame, period: int = 5) -> pd.Series:
    """Volume ratio (量比): today's volume vs average of past N days."""
    avg_vol = df["volume"].rolling(period).mean().shift(1)
    return (df["volume"] / avg_vol).shift(1)


def compute_volume_surge(df: pd.DataFrame, period: int = 5, threshold: float = 2.0) -> pd.Series:
    """Volume surge detection: binary indicator of unusual volume."""
    vol_ratio = df["volume"] / df["volume"].rolling(period).mean().shift(1)
    return (vol_ratio > threshold).astype(int).shift(1)


def compute_fund_flow_proxy(df: pd.DataFrame) -> pd.Series:
    """Proxy for institutional fund flow.

    Uses price position within daily range weighted by volume:
    - Close near high + high volume = positive flow (buying pressure)
    - Close near low + high volume = negative flow (selling pressure)
    """
    daily_range = df["high"] - df["low"]
    position = (df["close"] - df["low"]) / daily_range.replace(0, np.nan)
    position = position.fillna(0.5)

    # Weight by volume
    vol_weight = df["volume"] / df["volume"].rolling(20).mean().shift(1)
    vol_weight = vol_weight.fillna(1)

    flow = (position - 0.5) * 2 * vol_weight  # Scale to [-1, 1] range
    return flow.shift(1)


def compute_fund_flow_ma(df: pd.DataFrame, period: int = 5) -> pd.Series:
    """Smoothed fund flow proxy (MA of daily flow)."""
    flow = compute_fund_flow_proxy(df)
    return flow.rolling(period).mean().shift(1)


def compute_smart_money_index(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Smart money index: combines fund flow, volume, and price momentum.

    High values suggest institutional accumulation.
    """
    flow = compute_fund_flow_proxy(df)
    vol_ratio = df["volume"] / df["volume"].rolling(20).mean().shift(1)
    momentum = df["close"].pct_change(5).shift(1)

    # Combine: positive flow + high volume + positive momentum = smart money buying
    smi = (flow + vol_ratio.fillna(1) - 1 + momentum.fillna(0)) / 3
    return smi.rolling(period).mean().shift(1)


def compute_volume_price_correlation(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Correlation between volume and price changes.

    Positive correlation: volume confirms price trend (healthy)
    Negative correlation: divergence (potential reversal)
    """
    price_change = df["close"].pct_change()
    vol_change = df["volume"].pct_change()

    return price_change.rolling(period).corr(vol_change).shift(1)


def compute_accumulation_distribution(df: pd.DataFrame) -> pd.Series:
    """Accumulation/Distribution line (Chaikin)."""
    clv = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / (df["high"] - df["low"]).replace(0, np.nan)
    clv = clv.fillna(0)
    ad = (clv * df["volume"]).cumsum()
    return ad.shift(1)


def compute_ad_slope(df: pd.DataFrame, period: int = 10) -> pd.Series:
    """A/D line trend: accumulation vs distribution."""
    ad = compute_accumulation_distribution(df)
    return ad.rolling(period).apply(
        lambda x: np.polyfit(np.arange(len(x)), x, 1)[0] if len(x) > 1 else 0,
        raw=True
    ).shift(1)
