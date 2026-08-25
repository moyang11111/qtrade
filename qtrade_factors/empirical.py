# -*- coding: utf-8 -*-
"""QTrade-owned adaptations of the empirical factor families.

See ``THIRD_PARTY_NOTICES.md`` for upstream attribution.  The module contains
only computation logic and never ships third-party market data.
"""

from __future__ import annotations

import pandas as pd

from .common import _ret


def lowvol_60(df: pd.DataFrame) -> pd.Series:
    return _ret(df).rolling(60).std()


def mom_20(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float) / df["close"].shift(20) - 1


def mom_120(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float) / df["close"].shift(120) - 1


def _prev_high_250(df: pd.DataFrame) -> pd.Series:
    """前 250 日的最高价（不含当日，避免用当日最高价定义‘新高/接近新高’）。"""
    return df["high"].astype(float).shift(1).rolling(250).max()


def near_high_250(df: pd.DataFrame) -> pd.Series:
    """接近 52 周高点（越接近 0 越强）。"""
    hh = _prev_high_250(df)
    return df["close"].astype(float) / hh - 1


def new_high_250(df: pd.DataFrame) -> pd.Series:
    """52 周新高标记（收盘价突破前 250 日最高价）。"""
    hh = _prev_high_250(df)
    return (df["close"].astype(float) > hh).astype(float)


def _limit_streaks(df: pd.DataFrame, up: bool) -> pd.Series:
    close = df["close"].astype(float)
    if up:
        hit = close >= close.shift(1) * 1.095
    else:
        hit = close <= close.shift(1) * 0.905
    out = pd.Series(0.0, index=df.index)
    s = 0.0
    vals = hit.values
    for i in range(len(vals)):
        s = s + 1 if vals[i] else 0
        out.iloc[i] = s
    return out


def consec_limit_up(df: pd.DataFrame) -> pd.Series:
    return _limit_streaks(df, up=True)


def consec_limit_down(df: pd.DataFrame) -> pd.Series:
    return _limit_streaks(df, up=False)


def limit_up_flag(df: pd.DataFrame) -> pd.Series:
    close = df["close"].astype(float)
    return (close >= close.shift(1) * 1.095).astype(float)


def limit_down_flag(df: pd.DataFrame) -> pd.Series:
    close = df["close"].astype(float)
    return (close <= close.shift(1) * 0.905).astype(float)


__all__ = [
    "lowvol_60",
    "mom_20",
    "mom_120",
    "near_high_250",
    "new_high_250",
    "consec_limit_up",
    "consec_limit_down",
    "limit_up_flag",
    "limit_down_flag",
]
