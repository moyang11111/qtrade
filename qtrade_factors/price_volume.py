# -*- coding: utf-8 -*-
"""QTrade-owned price/volume factor adaptations.

See ``THIRD_PARTY_NOTICES.md`` for upstream attribution.  Only formulas and
runtime behavior are represented here; no market data is bundled or copied.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .common import _ret


def std20(df: pd.DataFrame) -> pd.Series:
    return _ret(df).rolling(20).std()


def downside_vol(df: pd.DataFrame) -> pd.Series:
    r = _ret(df)
    return r.where(r < 0, 0.0).rolling(20).std()


def reversal20(df: pd.DataFrame) -> pd.Series:
    """20 日反转：过去跌得越深，数值越高（越有反弹预期）。"""
    return -(df["close"] / df["close"].shift(20) - 1)


def mom20(df: pd.DataFrame) -> pd.Series:
    return df["close"] / df["close"].shift(20) - 1


def o2c(df: pd.DataFrame) -> pd.Series:
    """开盘到收盘收益（近 10 日均值）。"""
    return (df["close"] / df["open"].replace(0, np.nan) - 1).rolling(10).mean()


def amihud_proxy(df: pd.DataFrame) -> pd.Series:
    """非流动性代理：|收益|/成交量（无成交额时用成交量代替）。"""
    r = _ret(df)
    v = df["volume"].replace(0, np.nan)
    return (r.abs() / v).rolling(20).mean()


def max_ret20(df: pd.DataFrame) -> pd.Series:
    return _ret(df).rolling(20).max()


def skew20(df: pd.DataFrame) -> pd.Series:
    return _ret(df).rolling(20).skew()


def amp20(df: pd.DataFrame) -> pd.Series:
    """20 日平均振幅。"""
    return ((df["high"] - df["low"]) / df["open"].replace(0, np.nan)).rolling(20).mean()


def volume_ratio(df: pd.DataFrame) -> pd.Series:
    """量能比：当日量 / 20 日均量。"""
    return df["volume"] / df["volume"].rolling(20).mean().replace(0, np.nan)


def limup_ex_5(df: pd.DataFrame) -> pd.Series:
    """近 5 日涨停次数（收盘 ≥ 昨收 × 1.095），涨停后强势代理。"""
    limit = df["close"].astype(float) >= df["close"].shift(1) * 1.095
    return limit.astype(float).rolling(5).sum()


def pullback(df: pd.DataFrame) -> pd.Series:
    """距 60 日高点的回撤（越负越超跌）。"""
    return df["close"] / df["high"].rolling(60).max() - 1


def ma_alignment(df: pd.DataFrame) -> pd.Series:
    """均线多头排列评分：MA5>MA10>MA20>MA60 得 1，否则 0~1 分。"""
    ma5 = df["close"].rolling(5).mean()
    ma10 = df["close"].rolling(10).mean()
    ma20 = df["close"].rolling(20).mean()
    ma60 = df["close"].rolling(60).mean()
    score = ((ma5 > ma10).astype(float) + (ma10 > ma20).astype(float)
             + (ma20 > ma60).astype(float)) / 3.0
    return score


def rsi14(df: pd.DataFrame) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def rsi_revert(df: pd.DataFrame) -> pd.Series:
    """RSI 超卖强度：RSI<30 越高越好（用 RSI 减去 50 再取负，越远离 50 下方越好）。"""
    r = rsi14(df)
    return (50 - r) / 50.0


__all__ = [
    "std20",
    "downside_vol",
    "reversal20",
    "mom20",
    "o2c",
    "amihud_proxy",
    "max_ret20",
    "skew20",
    "amp20",
    "volume_ratio",
    "limup_ex_5",
    "pullback",
    "ma_alignment",
    "rsi14",
    "rsi_revert",
]
