"""强势回调布林中轨买入策略（Pullback to BB Middle）。

热度高涨幅高的股票，在回调过程中回踩布林中轨时买入。

入场条件（全部满足）：
  1. 上升趋势：MA5 > MA20（短期多头）且价格 > MA60×1.05（中期强势）
  2. 近期涨幅：最近 20 日涨幅 ≥ 8%（"热度高涨幅高"）
  3. 回调中：从 10 日高点回落 ≥ 2%
  4. 回踩确认：入场前 5 日内曾在 BB 中轨上方（证明确是回调摸中轨）
  5. 价格在 BB 中轨 ±0.8% 范围内

出场：止盈(+10%)/止损(-5%) 由 SignalFollower 处理，策略只发买入信号。

SignalFollower 推荐配置：
  take_profit_pct=0.10   # 10% 止盈
  stop_loss_pct=0.05     # 5% 止损
  trail_stop_pct=0.0     # 不设移动止损
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("pullback_bb_mid")
class PullbackBBMidSignal(SignalGenerator):
    """强势回调布林中轨买入策略 V3。"""

    def __init__(self, config: dict):
        super().__init__(config)

        # ── 布林带 ──
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)

        # ── 上升趋势确认 ──
        self.ma_short = config.get("ma_short", 5)
        self.ma_long = config.get("ma_long", 20)
        self.ma60_period = config.get("ma60_period", 60)
        self.ma60_premium = config.get("ma60_premium", 0.05)     # 价格高于 MA60 至少 5%
        self.uptrend_lookback = config.get("uptrend_lookback", 20)
        self.uptrend_min_return = config.get("uptrend_min_return", 0.08)  # 20日涨幅至少 8%

        # ── 回调确认 ──
        self.pullback_peak_lookback = config.get("pullback_peak_lookback", 10)
        self.pullback_min_drop = config.get("pullback_min_drop", 0.02)    # 从高点至少回落 2%
        self.above_mid_lookback = config.get("above_mid_lookback", 5)     # 入场前 N 日内曾在 BB 中轨上方
        
        # ── 入场触发 ──
        self.bb_mid_touch_threshold = config.get("bb_mid_touch_threshold", 0.008)  # 中轨 ±0.8%

        # ── 趋势破位出场 ──
        self.bb_mid_break_bars = config.get("bb_mid_break_bars", 3)  # 连续 3 日在中轨下卖出

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]

        # ── 1. 布林带 ──
        bb_mid = close.rolling(self.bb_period).mean()

        # ── 2. 均线 ──
        ma5 = close.rolling(self.ma_short).mean()
        ma20 = close.rolling(self.ma_long).mean()
        ma60 = close.rolling(self.ma60_period).mean()

        # ── 3. 上升趋势确认 ──
        # A：均线多头（MA5 > MA20），短期趋势向上
        ma_bull = ma5 > ma20
        # B：价格显著在 MA60 上方，中期趋势完好
        price_strong = close > ma60 * (1 + self.ma60_premium)
        # C：近期涨幅达标（"热度高"）
        recent_ret = close.pct_change(self.uptrend_lookback)
        had_rally = recent_ret >= self.uptrend_min_return

        uptrend = ma_bull & price_strong & had_rally

        # ── 4. 回调 + "回踩"确认 ──
        recent_peak = close.rolling(self.pullback_peak_lookback).max()
        drop_from_peak = (recent_peak - close) / recent_peak
        pulling_back = drop_from_peak >= self.pullback_min_drop

        # 入场前 N 天内曾在 BB 中轨上方（证明确是"回踩"而非破位下跌）
        was_above_mid = (close > bb_mid).rolling(self.above_mid_lookback, min_periods=1).max() > 0

        # ── 5. 入场信号 ──
        dist_to_mid = (close - bb_mid) / bb_mid
        near_bb_mid = dist_to_mid.abs() <= self.bb_mid_touch_threshold

        entry = uptrend & pulling_back & near_bb_mid & was_above_mid

        # ── 6. 构造信号（仅买入，卖出一律由引擎 SL/TP 处理） ──
        actions = np.where(entry, 1, 0)
        strengths = np.where(
            entry,
            np.clip(drop_from_peak / 0.05, 0.3, 1.0),
            0.0,
        )

        result[SIGNAL_ACTION] = actions
        result[SIGNAL_STRENGTH] = strengths
        result[SIGNAL_SCORE] = dist_to_mid.clip(-0.05, 0.05) / 0.05

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
