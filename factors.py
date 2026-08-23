# -*- coding: utf-8 -*-
"""A 股量价因子库 —— 首批移植自 deepseek-harness-quant 的思路。

注：仓库原始因子依赖 PIT 数据库/基本面字段，本模块先从 OHLCV 能算的部分移植，
后续可再补基本面/行业因子（需接入对应数据源）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EPS = 1e-9


def _ret(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float).pct_change()


# ---------- 因子：返回逐日 Series ----------

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


# ---------- 经典指标（移植自 deepseek-harness-quant factors/classic_indicators.py） ----------

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


# ---------- 实证因子（移植自 deepseek-harness-quant factors/factor_engine.py） ----------

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


# ---------- 集合 ----------

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


def composite_score(df: pd.DataFrame, lookback: int = 120) -> pd.Series:
    """滚动 z-score 加权合成打分（仅使用截至当天的数据，无未来函数）。

    正值=偏多，负值=偏空。
    """
    f = factor_frame(df)
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


# ---------- 全量因子清单（含“待数据源”因子） ----------

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
