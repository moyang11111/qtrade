"""Pullback20D — 缩量回调 + 持有20日策略。

2026数据验证的最强单因子：
  入口：距60日高点回落15-40% + 成交量收缩（5日/20日均量 < 0.7）
  出场：持有满20个交易日，无条件卖出
  风控：不设止盈止损，纯信号驱动
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("pullback_20d")
class Pullback20DSignal(SignalGenerator):
    """缩量回调 + 持有20日策略。

    买入：距60日高点回落15-40% 且 成交量收缩（vol_5d/vol_20d < 0.7）
    卖出：买入后第20个交易日，无条件卖出
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.peak_lookback = config.get("peak_lookback", 60)
        self.drop_min = config.get("drop_min", 0.15)
        self.drop_max = config.get("drop_max", 0.40)
        self.vol_short = config.get("vol_short", 5)
        self.vol_long = config.get("vol_long", 20)
        self.vol_threshold = config.get("vol_threshold", 0.7)
        self.hold_bars = config.get("hold_bars", 20)
        # MA60 斜率过滤（0=关闭, 0.10=MA60上升10%以上才入场）
        self.ma60_slope_min = config.get("ma60_slope_min", 0.0)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]
        volume = result["volume"]
        n = len(result)

        # ── 指标 ──
        ma60 = close.rolling(60).mean()
        peak = close.rolling(self.peak_lookback).max()
        drop = (peak - close) / peak
        vol_ratio = (volume.rolling(self.vol_short).mean() /
                     volume.rolling(self.vol_long).mean().replace(0, np.nan))

        # ── MA60 斜率（20日）：趋势强度过滤 ──
        ma60_slope = (ma60 / ma60.shift(20) - 1).fillna(0)

        # ── 买入条件 ──
        buy = (
            (close > ma60)
            & (drop >= self.drop_min)
            & (drop <= self.drop_max)
            & (vol_ratio < self.vol_threshold)
            & (ma60_slope >= self.ma60_slope_min)  # MA60趋势过滤
        )

        # ── 卖出条件：买入后第20天 ──
        sell = pd.Series(0, index=result.index)
        buy_indices = np.where(buy.values)[0]

        for idx in buy_indices:
            exit_idx = idx + self.hold_bars
            if exit_idx < n:
                sell.iloc[exit_idx] = -1

        # ── 输出 ──
        result[SIGNAL_ACTION] = np.where(buy, 1, np.where(sell == -1, -1, 0))
        result[SIGNAL_STRENGTH] = np.where(
            buy,
            np.clip(drop / 0.30 * (1 - vol_ratio.fillna(1)), 0.4, 1.0),
            0.0,
        )
        result[SIGNAL_SCORE] = np.where(buy, drop.clip(0, 0.5) / 0.5, 0)

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0).astype(int)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0.0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0.0)

        return result
