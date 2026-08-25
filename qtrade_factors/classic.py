# -*- coding: utf-8 -*-
"""QTrade-owned adaptations of the classic technical indicators.

See ``THIRD_PARTY_NOTICES.md`` for upstream attribution.  These functions do
not embed symbols, prices, or any other third-party market-data asset.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .common import EPS


def macd_hist(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.Series:
    close = df["close"].astype(float)

    def _ema(s, n):
        return s.ewm(span=n, adjust=False).mean()

    dif = _ema(close, fast) - _ema(close, slow)
    dea = _ema(dif, signal)
    return dif - dea


def roc20(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float).pct_change(20)


def wpr14(df: pd.DataFrame) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    hh = high.rolling(14).max()
    ll = low.rolling(14).min()
    return -100.0 * (hh - close) / (hh - ll).replace(0, np.nan)


def cci20(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]).astype(float) / 3.0
    ma = tp.rolling(20).mean()
    md = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - ma) / (0.015 * md)


def obv_trend(df: pd.DataFrame) -> pd.Series:
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    m21 = obv.rolling(21).mean()
    s21 = obv.rolling(21).std()
    return (obv - m21) / (s21 + EPS)


def kdj_k(df: pd.DataFrame, n=9) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rsv = (close - ll) / (hh - ll).replace(0, np.nan)
    return rsv.ewm(com=2, adjust=False).mean()


def kdj_d(df: pd.DataFrame, n=9) -> pd.Series:
    return kdj_k(df, n).rolling(3).mean()


def kdj_j(df: pd.DataFrame, n=9) -> pd.Series:
    k = kdj_k(df, n)
    d = kdj_d(df, n)
    return 3.0 * k - 2.0 * d


def vol_contract(df: pd.DataFrame) -> pd.Series:
    """缩量标记：当日量 < 20日均量 × 0.7。"""
    v = df["volume"].astype(float)
    return (v / v.rolling(20).mean().replace(0, np.nan) < 0.7).astype(float)


def near_ma250(df: pd.DataFrame) -> pd.Series:
    """收盘距 MA250 的偏差绝对值（越小越贴近长线中枢）。"""
    ma250 = df["close"].astype(float).rolling(250).mean()
    return (df["close"] / ma250 - 1).abs()


def ma50_up(df: pd.DataFrame) -> pd.Series:
    """MA50 走平/上行标记。"""
    ma50 = df["close"].astype(float).rolling(50).mean()
    return (ma50 >= ma50.shift(1) * 0.998).astype(float)


def rsi6(df: pd.DataFrame) -> pd.Series:
    delta = df["close"].astype(float).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    ag = gain.ewm(alpha=1 / 6, adjust=False).mean()
    al = loss.ewm(alpha=1 / 6, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def rps_percentile(sym_ret, universe_rets):
    """RPS（相对强度百分位）：sym_ret 在全市场 universe_rets 中的百分位（0-100）。"""
    arr = np.asarray(universe_rets, dtype=float)
    arr = arr[~np.isnan(arr)]
    if len(arr) == 0 or sym_ret is None or np.isnan(sym_ret):
        return None
    return round(float((arr <= float(sym_ret)).mean() * 100), 3)


def ma200_up(df: pd.DataFrame) -> pd.Series:
    ma200 = df["close"].astype(float).rolling(200).mean()
    return (df["close"].astype(float) > ma200).astype(float)


__all__ = [
    "macd_hist",
    "roc20",
    "wpr14",
    "cci20",
    "obv_trend",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "vol_contract",
    "near_ma250",
    "ma50_up",
    "rsi6",
    "rps_percentile",
    "ma200_up",
]
