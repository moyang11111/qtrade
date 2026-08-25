# -*- coding: utf-8 -*-
"""QTrade-owned factor registry and data-dependency inventory.

See ``THIRD_PARTY_NOTICES.md`` for upstream attribution.  Registry entries
describe computation availability only; no business or market data is stored.
"""

from __future__ import annotations


AVAILABLE_FACTORS = [
    "std20", "downside_vol", "reversal20", "mom20", "o2c", "amihud",
    "max_ret20", "skew20", "amp20", "volume_ratio", "limup_ex_5", "pullback",
    "ma_alignment", "rsi_revert", "macd_hist", "roc20", "wpr14", "cci20",
    "obv_trend", "kdj_k", "ma200_up", "lowvol_60", "mom_120", "near_high_250",
    "new_high_250", "consec_limit_up", "consec_limit_down",
    "limit_up_flag", "limit_down_flag",
    "kdj_d", "kdj_j", "vol_contract", "near_ma250", "ma50_up", "rsi6",
]

# 依赖财务数据（finance.db/quality.db）——待接通数据源后实现
NEED_FINANCE = [
    "value", "bp", "pe", "pb", "pe_pct", "pb_pct", "div_yield", "pcf",
    "growth", "sue", "sue_factor", "yoy_accel", "sq_nyoy", "pead",
    "pead_factor", "asset_growth", "inst_surv", "gross_margin_chg",
    "quality", "roe", "roa", "liability", "cfo_health", "accruals",
    "f_score", "gp_a", "c_factor", "accel_factor", "a_factor", "profit_ok",
]

# 依赖全市场横截面（需股票池排名）
NEED_CROSS_SECTION = ["rps_120", "rps", "turnover", "turn_mid_prox", "turn60", "turn_std20"]

# 依赖龙虎榜 / 机构持仓 / 资金流
NEED_LHG = ["lhb_jg_cnt_20", "shebao_chg", "inst_inflow", "main_net_inflow", "north_flow"]

# 依赖行业分类
NEED_INDUSTRY = ["ind_crowd_60", "ind_rs_20"]


def factor_inventory() -> dict:
    """返回因子全量清单：status = ok / need_finance / need_cross_section / need_lhg / need_industry。"""
    inv = {name: "ok" for name in AVAILABLE_FACTORS}
    for name in NEED_FINANCE:
        inv[name] = "need_finance"
    for name in NEED_CROSS_SECTION:
        inv[name] = "need_cross_section"
    for name in NEED_LHG:
        inv[name] = "need_lhg"
    for name in NEED_INDUSTRY:
        inv[name] = "need_industry"
    return {
        "total": len(inv),
        "available": len(AVAILABLE_FACTORS),
        "need_data": len(inv) - len(AVAILABLE_FACTORS),
        "factors": inv,
    }


__all__ = [
    "AVAILABLE_FACTORS",
    "NEED_FINANCE",
    "NEED_CROSS_SECTION",
    "NEED_LHG",
    "NEED_INDUSTRY",
    "factor_inventory",
]
