"""Behavior and export contract for the normalized QTrade factor modules."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import factors
import qtrade_factors
from qtrade_factors import classic, empirical, price_volume, scoring


EXPECTED_COLUMNS = (
    "std20",
    "downside_vol",
    "reversal20",
    "mom20",
    "o2c",
    "amihud",
    "max_ret20",
    "skew20",
    "amp20",
    "volume_ratio",
    "limup_ex_5",
    "pullback",
    "ma_alignment",
    "rsi_revert",
    "macd_hist",
    "roc20",
    "wpr14",
    "cci20",
    "obv_trend",
    "kdj_k",
    "ma200_up",
    "lowvol_60",
    "mom_120",
    "near_high_250",
    "new_high_250",
    "consec_limit_up",
    "consec_limit_down",
    "limit_up_flag",
    "limit_down_flag",
    "kdj_d",
    "kdj_j",
    "vol_contract",
    "near_ma250",
    "ma50_up",
    "rsi6",
)

PUBLIC_FUNCTIONS = {
    "std20": price_volume.std20,
    "downside_vol": price_volume.downside_vol,
    "reversal20": price_volume.reversal20,
    "mom20": price_volume.mom20,
    "o2c": price_volume.o2c,
    "amihud_proxy": price_volume.amihud_proxy,
    "max_ret20": price_volume.max_ret20,
    "skew20": price_volume.skew20,
    "amp20": price_volume.amp20,
    "volume_ratio": price_volume.volume_ratio,
    "limup_ex_5": price_volume.limup_ex_5,
    "pullback": price_volume.pullback,
    "ma_alignment": price_volume.ma_alignment,
    "rsi14": price_volume.rsi14,
    "rsi_revert": price_volume.rsi_revert,
    "macd_hist": classic.macd_hist,
    "roc20": classic.roc20,
    "wpr14": classic.wpr14,
    "cci20": classic.cci20,
    "obv_trend": classic.obv_trend,
    "kdj_k": classic.kdj_k,
    "kdj_d": classic.kdj_d,
    "kdj_j": classic.kdj_j,
    "vol_contract": classic.vol_contract,
    "near_ma250": classic.near_ma250,
    "ma50_up": classic.ma50_up,
    "rsi6": classic.rsi6,
    "rps_percentile": classic.rps_percentile,
    "ma200_up": classic.ma200_up,
    "lowvol_60": empirical.lowvol_60,
    "mom_20": empirical.mom_20,
    "mom_120": empirical.mom_120,
    "near_high_250": empirical.near_high_250,
    "new_high_250": empirical.new_high_250,
    "consec_limit_up": empirical.consec_limit_up,
    "consec_limit_down": empirical.consec_limit_down,
    "limit_up_flag": empirical.limit_up_flag,
    "limit_down_flag": empirical.limit_down_flag,
}


def _synthetic_ohlcv(rows: int = 320) -> pd.DataFrame:
    t = np.arange(rows, dtype=float)
    close = 100.0 + 0.18 * t + 2.2 * np.sin(t / 7.0) + 0.4 * np.sin(t / 3.0)
    open_ = close + 0.2 * np.cos(t / 5.0)
    high = np.maximum(open_, close) + 0.7 + 0.1 * np.sin(t)
    low = np.minimum(open_, close) - 0.6 - 0.1 * np.cos(t)
    volume = 100000.0 + 1000.0 * (t % 17) + 5000.0 * np.sin(t / 11.0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=pd.date_range("2025-01-02", periods=rows, freq="B"),
    )


def test_facade_exports_every_historical_public_factor_name():
    expected = set(PUBLIC_FUNCTIONS) | {
        "factor_frame",
        "composite_score",
        "latest_factors",
        "AVAILABLE_FACTORS",
        "NEED_FINANCE",
        "NEED_CROSS_SECTION",
        "NEED_LHG",
        "NEED_INDUSTRY",
        "factor_inventory",
        "EPS",
    }
    assert expected <= set(factors.__all__)
    for name in expected:
        assert hasattr(factors, name)
        assert hasattr(qtrade_factors, name)


@pytest.mark.parametrize("name, implementation", PUBLIC_FUNCTIONS.items())
def test_facade_single_factor_matches_normalized_module(name, implementation):
    frame = _synthetic_ohlcv()
    before = frame.copy(deep=True)
    if name == "rps_percentile":
        assert factors.rps_percentile(0.1, [0.01, 0.05, 0.1, 0.2]) == implementation(
            0.1, [0.01, 0.05, 0.1, 0.2]
        )
    else:
        actual = getattr(factors, name)(frame)
        expected = implementation(frame)
        pd.testing.assert_series_equal(actual, expected)
    pd.testing.assert_frame_equal(frame, before)


def test_factor_frame_order_shape_dtype_and_values_are_stable():
    frame = _synthetic_ohlcv()
    before = frame.copy(deep=True)
    facade = factors.factor_frame(frame)
    normalized = scoring.factor_frame(frame)

    assert tuple(facade.columns) == EXPECTED_COLUMNS
    assert facade.shape == (len(frame), 35)
    pd.testing.assert_frame_equal(facade, normalized)
    pd.testing.assert_frame_equal(frame, before)


def test_registry_order_categories_and_counts_are_stable():
    assert tuple(factors.AVAILABLE_FACTORS) == EXPECTED_COLUMNS
    assert factors.AVAILABLE_FACTORS is qtrade_factors.AVAILABLE_FACTORS
    inventory = factors.factor_inventory()
    assert inventory == qtrade_factors.factor_inventory()
    assert inventory["total"] == 78
    assert inventory["available"] == 35
    assert inventory["need_data"] == 43
    counts = {status: list(inventory["factors"].values()).count(status)
              for status in set(inventory["factors"].values())}
    assert counts == {
        "ok": 35,
        "need_finance": 30,
        "need_cross_section": 6,
        "need_lhg": 5,
        "need_industry": 2,
    }


def test_default_and_custom_composite_scores_match_and_preserve_input():
    frame = _synthetic_ohlcv()
    before = frame.copy(deep=True)

    default = factors.composite_score(frame)
    normalized_default = scoring.composite_score(frame)
    pd.testing.assert_series_equal(default, normalized_default)
    assert default.iloc[:29].eq(0.0).all()
    assert default.isna().sum() == 0

    selected = ["mom20", "std20", "near_ma250"]
    custom_list = factors.composite_score(frame, lookback=40, factors=selected, weights=[1, -2])
    normalized_list = scoring.composite_score(
        frame, lookback=40, factors=selected, weights=[1, -2]
    )
    pd.testing.assert_series_equal(custom_list, normalized_list)

    custom_dict = factors.composite_score(
        frame, lookback=40, factors=selected, weights={"mom20": 2, "std20": -1}
    )
    normalized_dict = scoring.composite_score(
        frame, lookback=40, factors=selected, weights={"mom20": 2, "std20": -1}
    )
    pd.testing.assert_series_equal(custom_dict, normalized_dict)
    pd.testing.assert_frame_equal(frame, before)


def test_short_nan_zero_volume_and_empty_inputs_keep_existing_behavior():
    frame = _synthetic_ohlcv()
    short = frame.iloc[:10]
    short_score = factors.composite_score(short)
    assert short_score.eq(0.0).all()
    assert factors.latest_factors(short)["composite_score"] == 0.0

    zero_volume = frame.copy()
    zero_volume.loc[zero_volume.index[20:25], "volume"] = 0.0
    zero_volume.loc[zero_volume.index[30], "close"] = np.nan
    before = zero_volume.copy(deep=True)
    result = factors.factor_frame(zero_volume)
    assert pd.isna(result.loc[result.index[20], "amihud"])
    assert pd.isna(result.loc[result.index[30], "mom20"])
    pd.testing.assert_frame_equal(zero_volume, before)

    empty = frame.iloc[:0]
    empty_frame = factors.factor_frame(empty)
    assert tuple(empty_frame.columns) == EXPECTED_COLUMNS
    assert empty_frame.empty
    assert factors.latest_factors(empty) == {}


def test_non_default_indicator_parameters_match_normalized_modules():
    frame = _synthetic_ohlcv()
    pd.testing.assert_series_equal(
        factors.macd_hist(frame, fast=5, slow=10, signal=3),
        classic.macd_hist(frame, fast=5, slow=10, signal=3),
    )
    for name in ("kdj_k", "kdj_d", "kdj_j"):
        pd.testing.assert_series_equal(
            getattr(factors, name)(frame, n=5),
            getattr(classic, name)(frame, n=5),
        )


def test_latest_factor_fields_rounding_and_nan_contract():
    frame = _synthetic_ohlcv()
    latest = factors.latest_factors(frame)
    normalized = scoring.latest_factors(frame)
    assert latest == normalized
    assert set(latest) == {"symbol", "date", *EXPECTED_COLUMNS, "composite_score"}
    assert latest["symbol"] is None
    assert isinstance(latest["date"], str)
    assert latest["std20"] == round(float(factors.std20(frame).iloc[-1]), 6)
