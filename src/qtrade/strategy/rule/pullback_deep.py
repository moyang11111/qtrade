"""深度回调策略 V4 — 龙虎榜专用。

在上行趋势中，双入口条件（OR 逻辑）：
  入场A：从高点回落 30%-40%（深度回调抄底）
  入场B：回踩布林带中轨 ±1%（经典回调）

出场：止盈(+10%) / 止损(-5%) 由 SignalFollower 处理，策略只发买入信号。
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("pullback_deep")
class PullbackDeepSignal(SignalGenerator):
    """深度回调双入口策略 — 龙虎榜优化版。

    两个入场条件（满足任一即可）：
      - 深度回调：从 N 日高点回落 30%-40%
      - 布林中轨回踩：价格在 BB 中轨 ±1% 范围内

    前置条件（全部满足）：
      1. 上升趋势：MA5 > MA20 且价格 > MA60
      2. 前期大涨：P 日内涨幅 ≥ min_rally
      3. 不在快速下跌中：当日跌幅 < 7%（排除暴跌接飞刀）
    """

    def __init__(self, config: dict):
        super().__init__(config)

        # ── 布林带 ──
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)

        # ── 趋势确认 ──
        self.ma_short = config.get("ma_short", 5)
        self.ma_long = config.get("ma_long", 20)
        self.ma60_period = config.get("ma60_period", 60)
        self.ma60_premium = config.get("ma60_premium", 0.0)  # V4: 放宽到 > MA60 即可

        # ── 前期大涨 ──
        self.rally_lookback = config.get("rally_lookback", 60)          # 回溯天数
        self.rally_min_return = config.get("rally_min_return", 0.15)   # 60日涨幅 ≥ 15%

        # ── 深度回调入口 ──
        self.peak_lookback = config.get("peak_lookback", 60)           # 高点回溯
        self.deep_drop_min = config.get("deep_drop_min", 0.30)         # 至少回落 30%
        self.deep_drop_max = config.get("deep_drop_max", 0.45)         # 最多回落 45%（太深=崩盘）

        # ── 布林中轨入口 ──
        self.bb_mid_threshold = config.get("bb_mid_threshold", 0.01)   # 中轨 ±1%
        self.was_above_mid_days = config.get("was_above_mid_days", 5)  # 入场前 N 日曾在 BB 中轨上方

        # ── 风控过滤 ──
        self.max_daily_drop = config.get("max_daily_drop", 0.07)       # 当日跌幅 > 7% 不入场

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]

        # ── 1. 布林带 ──
        bb_mid = close.rolling(self.bb_period).mean()
        bb_std = close.rolling(self.bb_period).std()
        bb_upper = bb_mid + self.bb_std * bb_std
        bb_lower = bb_mid - self.bb_std * bb_std

        # ── 2. 均线 ──
        ma5 = close.rolling(self.ma_short).mean()
        ma20 = close.rolling(self.ma_long).mean()
        ma60 = close.rolling(self.ma60_period).mean()

        # ── 3. 趋势确认 ──
        # 注意：深度回调时 MA5 必然 < MA20，不能统一要求短期多头
        trend_short = ma5 > ma20                       # 仅用于 BB 中轨入口
        above_ma60 = close > ma60 * (1 + self.ma60_premium)  # 中期趋势，所有入口必须

        # ── 4. 深度回调入口A（不要求短期多头）──
        peak = close.rolling(self.peak_lookback).max()
        drop_from_peak = (peak - close) / peak
        deep_pullback = (drop_from_peak >= self.deep_drop_min) & (drop_from_peak <= self.deep_drop_max)
        entry_a = above_ma60 & deep_pullback

        # ── 5. 布林中轨入口B（要求短期多头）──
        dist_to_mid = (close - bb_mid) / bb_mid
        near_bb_mid = dist_to_mid.abs() <= self.bb_mid_threshold
        was_above_mid = (close > bb_mid).rolling(self.was_above_mid_days, min_periods=1).max() > 0
        bb_mid_entry = near_bb_mid & was_above_mid
        entry_b = trend_short & above_ma60 & bb_mid_entry

        # ── 6. 风控：排除暴跌日 ──
        daily_ret = close.pct_change()
        not_crashing = daily_ret > -self.max_daily_drop

        # ── 7. 综合入场信号（OR 逻辑）──
        entry = (entry_a | entry_b) & not_crashing

        # ── 8. 构造输出 ──
        actions = np.where(entry, 1, 0)

        # 信号强度：深度回调 > 布林中轨
        strengths = np.where(
            entry & deep_pullback,
            np.clip(drop_from_peak / self.deep_drop_min, 0.5, 1.0),
            np.where(
                entry & bb_mid_entry,
                np.clip(1 - dist_to_mid.abs() / self.bb_mid_threshold, 0.3, 0.8),
                0.0,
            ),
        )

        result[SIGNAL_ACTION] = actions
        result[SIGNAL_STRENGTH] = strengths
        result[SIGNAL_SCORE] = np.where(deep_pullback, drop_from_peak.clip(0, 0.45) / 0.45,
                                np.where(bb_mid_entry, -dist_to_mid.abs().clip(-0.02, 0.02) / 0.02, 0))

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
