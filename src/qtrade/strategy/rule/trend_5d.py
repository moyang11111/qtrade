"""五日趋势策略 V4 — 增加频率 + 改进出场。

优化点（仅 1+3 组合）：
1. 增加交易频率：vol_multiplier 降低到 1.2，捕捉更多趋势起点
3. 改进出场逻辑：
   - 收盘价确认跌破（而非盘中瞬时）
   - MA10 还在上升时不卖（减少被正常回调震出）
   - 跌破 MA5 但 MA10 仍在上升 → 继续持有，跌破 MA10 才离场
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("trend_5d")
class Trend5DSignal(SignalGenerator):
    """5 日均线趋势跟踪策略 V4（增加频率 + 改进出场）。"""

    def __init__(self, config: dict):
        super().__init__(config)
        # 均线参数
        self.ma_period = config.get("ma_period", 5)
        self.ma10_period = config.get("ma10_period", 10)
        self.ma20_period = config.get("ma20_period", 20)
        self.slope_period = config.get("slope_period", 3)

        # 入场过滤器（1. 降低阈值增加频率）
        self.vol_ma_period = config.get("vol_ma_period", 20)
        self.vol_multiplier = config.get("vol_multiplier", 1.2)  # 从 1.5 降到 1.2
        self.atr_period = config.get("atr_period", 14)
        self.atr_median_period = config.get("atr_median_period", 60)

        # 加仓（保持原来条件）
        self.add_thresh = config.get("add_thresh", 0.01)

        # 出场（3. 改进出场逻辑）
        self.trailing_stop_pct = config.get("trailing_stop_pct", 0.05)
        self.time_stop_days = config.get("time_stop_days", 10)
        self.confirm_days = config.get("confirm_days", 1)  # 收盘价确认跌破天数

    def _calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]
        volume = result["volume"]

        # ── 计算指标 ──
        ma5 = close.rolling(self.ma_period).mean()
        ma10 = close.rolling(self.ma10_period).mean()
        ma20 = close.rolling(self.ma20_period).mean()

        ma5_slope = ma5.pct_change(self.slope_period)
        ma10_slope = ma10.pct_change(self.slope_period)
        ma20_slope = ma20.pct_change(self.slope_period)

        vol_ma = volume.rolling(self.vol_ma_period).mean()
        vol_filter = volume > (vol_ma * self.vol_multiplier)

        atr = self._calc_atr(df, self.atr_period)
        atr_median = atr.rolling(self.atr_median_period).median()
        atr_filter = atr > atr_median

        ma5_dist = (close - ma5) / ma5

        # ── 均线方向 ──
        ma5_up = ma5_slope > 0
        ma10_up = ma10_slope > 0
        ma20_up = ma20_slope > 0

        # ── 趋势层级（用于动态仓位） ──
        # Level 3: 三均线多头排列（最强）
        full_trend = (close > ma5) & (ma5 > ma10) & (ma10 > ma20) & ma5_up & ma10_up & ma20_up
        # Level 2: 双均线向上
        strong_trend = (close > ma5) & (ma5 > ma10) & ma5_up & ma10_up
        # Level 1: 仅 MA5 向上
        weak_trend = (close > ma5) & ma5_up

        # ── 趋势确认 ──
        trend_confirmed = weak_trend & vol_filter & ma20_up & atr_filter

        prev_above = close.shift(1) > ma5.shift(1)
        prev_up = ma5_slope.shift(1) > 0
        prev_vol = volume.shift(1) > (vol_ma.shift(1) * self.vol_multiplier)
        prev_ma20 = ma20_slope.shift(1) > 0
        prev_atr = atr.shift(1) > atr_median.shift(1)
        prev_trend = prev_above & prev_up & prev_vol & prev_ma20 & prev_atr

        # ── 买入信号 ──
        fresh_entry = trend_confirmed & ~prev_trend

        # ── 加仓信号（保持原来条件） ──
        pullback = (
            trend_confirmed
            & (ma5_dist.abs() < self.add_thresh)
            & (close >= ma5)
            & (close.shift(1) > ma5.shift(1) * (1 + self.add_thresh))
        )

        # ── 状态追踪 ──
        n = len(result)
        entry_idx = None
        add_count = 0
        highest_since_entry = None
        broke_below_ma5_days = 0
        actions = [0] * n
        strengths = [0.0] * n

        for i in range(n):
            # 首次入场：根据趋势层级动态仓位
            if fresh_entry.iloc[i] and entry_idx is None:
                entry_idx = i
                add_count = 0
                highest_since_entry = close.iloc[i]
                broke_below_ma5_days = 0

                # 4. 动态仓位：根据趋势层级
                if full_trend.iloc[i]:
                    strength = 0.8  # 满仓
                elif strong_trend.iloc[i]:
                    strength = 0.5  # 半仓
                else:
                    strength = 0.3  # 轻仓

                actions[i] = 1
                strengths[i] = strength
                continue

            # 加仓：根据当前趋势层级动态调整加仓仓位
            if pullback.iloc[i] and entry_idx is not None and add_count < 1:
                add_count += 1
                highest_since_entry = max(highest_since_entry, close.iloc[i]) if highest_since_entry else close.iloc[i]
                broke_below_ma5_days = 0

                # 动态加仓仓位
                if full_trend.iloc[i]:
                    add_strength = 0.4  # 强趋势多加仓
                elif strong_trend.iloc[i]:
                    add_strength = 0.3  # 中等趋势标准加仓
                else:
                    add_strength = 0.2  # 弱趋势少加仓

                actions[i] = 1
                strengths[i] = add_strength
                continue

            # 出场逻辑
            if entry_idx is not None and i > entry_idx:
                hold_days = i - entry_idx
                current_price = close.iloc[i]
                sell = False

                if highest_since_entry is not None:
                    highest_since_entry = max(highest_since_entry, current_price)

                # 移动止损
                if highest_since_entry and current_price < highest_since_entry * (1 - self.trailing_stop_pct):
                    sell = True

                # 时间止损
                elif hold_days >= self.time_stop_days:
                    sell = True

                # 均线止损（改进版：收盘价确认 + MA10 方向判断）
                else:
                    below_ma5 = current_price < ma5.iloc[i]
                    if below_ma5:
                        broke_below_ma5_days += 1
                    else:
                        broke_below_ma5_days = 0

                    confirmed_broke_ma5 = broke_below_ma5_days >= self.confirm_days

                    if confirmed_broke_ma5:
                        if add_count > 0:
                            # 已加仓：MA10 还在上升就不卖，跌破 MA10 才离场
                            if not ma10_up.iloc[i]:
                                sell = True
                            elif current_price < ma10.iloc[i]:
                                sell = True
                        else:
                            # 未加仓：跌破 MA5 确认即离场
                            sell = True

                if sell:
                    actions[i] = -1
                    strengths[i] = 1.0
                    entry_idx = None
                    add_count = 0
                    highest_since_entry = None
                    broke_below_ma5_days = 0

        # ── 写入信号 ──
        result[SIGNAL_ACTION] = actions
        result[SIGNAL_STRENGTH] = strengths
        result[SIGNAL_SCORE] = ma5_dist.clip(-0.1, 0.1) / 0.1

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
