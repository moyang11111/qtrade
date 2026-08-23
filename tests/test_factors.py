# -*- coding: utf-8 -*-
"""首批 A 股因子移植回归测试。"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import factors  # noqa: E402


def _make_df(n=260):
    idx = pd.date_range("2025-01-02", periods=n, freq="B")
    price = 10 + np.arange(n) * 0.01
    return pd.DataFrame({
        "open": price - 0.1,
        "high": price + 0.3,
        "low": price - 0.3,
        "close": price,
        "volume": [2_000_000] * n,
    }, index=idx)


def test_factor_frame_columns():
    df = _make_df()
    f = factors.factor_frame(df)
    expected = {"std20", "downside_vol", "reversal20", "mom20", "o2c",
                "amihud", "max_ret20", "skew20", "amp20", "volume_ratio",
                "limup_ex_5", "pullback", "ma_alignment", "rsi_revert",
                "macd_hist", "roc20", "wpr14", "cci20", "obv_trend", "kdj_k",
                "ma200_up", "lowvol_60", "mom_120", "near_high_250",
                "new_high_250", "consec_limit_up", "consec_limit_down",
                "limit_up_flag", "limit_down_flag"}
    assert expected.issubset(set(f.columns))
    assert len(f.columns) >= 35
    assert len(f) == len(df)


def test_composite_score_causal_and_values():
    df = _make_df()
    sc = factors.composite_score(df)
    assert len(sc) == len(df)
    # 前 29 根窗口不足 → 中性 0（不得用未来数据回填）
    assert sc.iloc[:29].eq(0).all()
    # 后期应为有效数值
    assert pd.notna(sc.iloc[-10:]).all()


def test_latest_factors():
    df = _make_df()
    r = factors.latest_factors(df)
    assert r["date"] == str(df.index[-1])[:10]
    assert "composite_score" in r
    assert "reversal20" in r and "std20" in r
    assert "lowvol_60" in r and "near_high_250" in r


def test_rps_percentile():
    assert factors.rps_percentile(0.10, [0.01, 0.05, 0.10, 0.20]) == 75.0
    assert factors.rps_percentile(None, [0.01, 0.05]) is None


def test_factor_inventory_counts():
    inv = factors.factor_inventory()
    assert inv["total"] == len(inv["factors"])
    assert inv["available"] == len(factors.AVAILABLE_FACTORS) == 35
    assert inv["need_data"] == inv["total"] - inv["available"]
    assert inv["factors"]["std20"] == "ok"
    assert inv["factors"]["f_score"] == "need_finance"
    assert inv["factors"]["turn_mid_prox"] == "need_cross_section"
