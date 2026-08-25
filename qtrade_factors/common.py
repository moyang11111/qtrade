# -*- coding: utf-8 -*-
"""Shared helpers for QTrade-owned factor adaptations.

The implementation is QTrade-owned adaptation code; see
``THIRD_PARTY_NOTICES.md`` for upstream attribution.  It contains no market
data or third-party data assets.
"""

from __future__ import annotations

import pandas as pd


EPS = 1e-9


def _ret(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float).pct_change()


__all__ = ["EPS", "_ret"]
