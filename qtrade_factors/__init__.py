# -*- coding: utf-8 -*-
"""QTrade-owned normalized factor package.

The package preserves the public behavior of the historical ``factors``
module.  Adaptation attribution is recorded in ``THIRD_PARTY_NOTICES.md``;
the package contains no third-party market-data files.
"""

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
    rps_percentile,
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
    mom_20,
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
    rsi14,
    rsi_revert,
    skew20,
    std20,
    volume_ratio,
)
from .registry import (
    AVAILABLE_FACTORS,
    NEED_CROSS_SECTION,
    NEED_FINANCE,
    NEED_INDUSTRY,
    NEED_LHG,
    factor_inventory,
)
from .scoring import composite_score, factor_frame, latest_factors


__all__ = [
    "EPS",
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
    "lowvol_60",
    "mom_20",
    "mom_120",
    "near_high_250",
    "new_high_250",
    "consec_limit_up",
    "consec_limit_down",
    "limit_up_flag",
    "limit_down_flag",
    "factor_frame",
    "composite_score",
    "latest_factors",
    "AVAILABLE_FACTORS",
    "NEED_FINANCE",
    "NEED_CROSS_SECTION",
    "NEED_LHG",
    "NEED_INDUSTRY",
    "factor_inventory",
]
