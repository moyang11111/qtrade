# -*- coding: utf-8 -*-
"""QTrade-owned factor-frame and score adaptations.

See ``THIRD_PARTY_NOTICES.md`` for upstream attribution.  The scoring layer
does not embed market data and preserves the existing causal calculations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .classic import (
    cci20,
    kdj_d,
    kdj_j,
    kdj_k,
    ma200_up,
    ma50_up,
    macd_hist,
    near_ma250,
    obv_trend,
    rsi6,
    roc20,
    vol_contract,
    wpr14,
)
from .common import EPS
from .empirical import (
    consec_limit_down,
    consec_limit_up,
    limit_down_flag,
    limit_up_flag,
    lowvol_60,
    mom_120,
    near_high_250,
    new_high_250,
)
from .price_volume import (
    amihud_proxy,
    amp20,
    downside_vol,
    limup_ex_5,
    ma_alignment,
    max_ret20,
    mom20,
    o2c,
    pullback,
    reversal20,
    rsi_revert,
    skew20,
    std20,
    volume_ratio,
)


def factor_frame(df: pd.DataFrame) -> pd.DataFrame:
    """返回所有因子逐日数值表。"""
    return pd.DataFrame({
        "std20": std20(df),
        "downside_vol": downside_vol(df),
        "reversal20": reversal20(df),
        "mom20": mom20(df),
        "o2c": o2c(df),
        "amihud": amihud_proxy(df),
        "max_ret20": max_ret20(df),
        "skew20": skew20(df),
        "amp20": amp20(df),
        "volume_ratio": volume_ratio(df),
        "limup_ex_5": limup_ex_5(df),
        "pullback": pullback(df),
        "ma_alignment": ma_alignment(df),
        "rsi_revert": rsi_revert(df),
        # 经典指标（classic_indicators）
        "macd_hist": macd_hist(df),
        "roc20": roc20(df),
        "wpr14": wpr14(df),
        "cci20": cci20(df),
        "obv_trend": obv_trend(df),
        "kdj_k": kdj_k(df),
        "ma200_up": ma200_up(df),
        # 实证因子（factor_engine）
        "lowvol_60": lowvol_60(df),
        "mom_120": mom_120(df),
        "near_high_250": near_high_250(df),
        "new_high_250": new_high_250(df),
        "consec_limit_up": consec_limit_up(df),
        "consec_limit_down": consec_limit_down(df),
        "limit_up_flag": limit_up_flag(df),
        "limit_down_flag": limit_down_flag(df),
        # 补充价量因子
        "kdj_d": kdj_d(df),
        "kdj_j": kdj_j(df),
        "vol_contract": vol_contract(df),
        "near_ma250": near_ma250(df),
        "ma50_up": ma50_up(df),
        "rsi6": rsi6(df),
    }, index=df.index)


def composite_score(df: pd.DataFrame, lookback: int = 120,
                    factors: list | None = None, weights: dict | list | None = None) -> pd.Series:
    """滚动 z-score 加权合成打分（仅使用截至当天的数据，无未来函数）。

    正值=偏多，负值=偏空。
    - factors=None 使用内置因子集合
    - 自定义时 factors 为因子名列表，weights 为 dict{因子:权重} 或与 factors 对齐的 list
    """
    f = factor_frame(df)
    if factors is None:
        cols = ["std20", "downside_vol", "reversal20", "mom20", "o2c",
                "amihud", "max_ret20", "amp20", "volume_ratio",
                "limup_ex_5", "pullback", "ma_alignment", "rsi_revert",
                "lowvol_60", "near_high_250", "mom_120", "macd_hist", "roc20",
                "wpr14", "cci20", "obv_trend", "kdj_k", "ma200_up",
                "consec_limit_up", "consec_limit_down", "limit_up_flag", "limit_down_flag",
                "kdj_d", "kdj_j", "vol_contract", "near_ma250", "ma50_up", "rsi6"]
        # 方向：越高越好的 +；越高越差的 -（参考 deepseek-harness-quant 实证方向）
        signs = {
            "std20": -1,          # 低波动更好
            "downside_vol": -1,   # 下行波动低更好
            "reversal20": 1,
            "mom20": 1,
            "o2c": 1,
            "amihud": -1,         # 非流动性越强越差
            "max_ret20": 0.5,
            "amp20": -1,          # 低振幅更稳健
            "volume_ratio": 0.5,
            "limup_ex_5": 1,
            "pullback": -1,       # 回撤越深（负得越多）反而偏高 → 反转
            "ma_alignment": 1,
            "rsi_revert": 1,
            "lowvol_60": -1,      # 60日低波正用（CS-02 最稳因子）
            "near_high_250": 1,   # 接近52周高正用（唯一120日转正）
            "mom_120": -1,        # 120日反转
            "macd_hist": 1,
            "roc20": -0.5,        # 20日ROC短期反转
            "wpr14": -0.5,        # W%R 高位=超买偏空
            "cci20": 0.3,
            "obv_trend": 1,
            "kdj_k": 0.3,
            "ma200_up": 1,
            "consec_limit_up": 1,
            "consec_limit_down": -1,
            "limit_up_flag": 0.3,
            "limit_down_flag": -0.5,
            "kdj_d": 0.2,
            "kdj_j": 0.1,
            "vol_contract": 0.5,     # 缩量（回踩/蓄势）有一定正向
            "near_ma250": 0.5,       # 越贴近 MA250 长线中枢越好
            "ma50_up": 1,
            "rsi6": 0.3,
        }
    else:
        cols = [c for c in factors if c in f.columns]
        if isinstance(weights, dict):
            signs = {k: float(w) for k, w in weights.items() if k in cols}
        else:
            weights = list(weights or [])
            signs = {}
            for i, c in enumerate(cols):
                w = float(weights[i]) if i < len(weights) else 1.0
                signs[c] = w

    def _z(s):
        m = s.mean()
        sd = s.std()
        return (s - m) / (sd + EPS)

    z = f[cols].rolling(lookback, min_periods=30).apply(lambda x: _z(pd.Series(x)).iloc[-1], raw=False)
    score = pd.Series(0.0, index=df.index)
    for c, w in signs.items():
        score += z[c].fillna(0.0) * w
    return score


def latest_factors(df: pd.DataFrame) -> dict:
    """返回最新一根 K 线的全部因子值（用于界面展示/选股）。"""
    if df is None or df.empty:
        return {}
    f = factor_frame(df)
    last = f.iloc[-1]
    out = {"symbol": None, "date": str(df.index[-1])[:10]}
    for k in f.columns:
        v = last[k]
        out[k] = None if (v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v)))) else round(float(v), 6)
    sc = composite_score(df)
    out["composite_score"] = None if (len(sc) == 0 or pd.isna(sc.iloc[-1])) else round(float(sc.iloc[-1]), 4)
    return out


__all__ = ["factor_frame", "composite_score", "latest_factors"]
