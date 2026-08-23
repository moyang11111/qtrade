"""SuperTrend Signal — 超级趋势跟踪策略。

来源：SuperTrend(10, 3) 经典指标策略，参考 pandas_ta 参考实现
（tradingstrategy.ai pandas_ta supertrend 文档）与 Investopedia 定义。

公式（Wilder ATR）：
  基础上轨 = (H+L)/2 + m×ATR   基础下轨 = (H+L)/2 − m×ATR
  最终轨只朝有利方向棘轮移动（收盘突破反向轨时重置）
  趋势翻多：收盘上穿最终上轨；翻空：收盘下穿最终下轨
  SuperTrend 线本身即移动止损，天然适合多头日线跟踪。
"""

import numpy as np
import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("supertrend")
class SuperTrendSignal(SignalGenerator):
    """SuperTrend(10, 3)：趋势线翻多买入，翻空卖出。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.period = config.get("period", 10)
        self.multiplier = config.get("multiplier", 3.0)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        high = result["high"].values
        low = result["low"].values
        close = result["close"].values
        n = len(result)

        hl2 = (high + low) / 2
        prev_close = np.roll(close, 1)
        tr = np.maximum(high - low,
                        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        tr[0] = high[0] - low[0]
        atr = pd.Series(tr).ewm(alpha=1 / self.period, adjust=False).mean().values

        upper = hl2 + self.multiplier * atr
        lower = hl2 - self.multiplier * atr
        final_upper = upper.copy()
        final_lower = lower.copy()
        trend = np.ones(n, dtype=int)

        for i in range(1, n):
            # 棘轮：只朝趋势有利方向收紧，被突破后重置
            final_upper[i] = (upper[i] if (upper[i] < final_upper[i - 1] or close[i - 1] > final_upper[i - 1])
                              else final_upper[i - 1])
            final_lower[i] = (lower[i] if (lower[i] > final_lower[i - 1] or close[i - 1] < final_lower[i - 1])
                              else final_lower[i - 1])
            if close[i] > final_upper[i - 1]:
                trend[i] = 1
            elif close[i] < final_lower[i - 1]:
                trend[i] = -1
            else:
                trend[i] = trend[i - 1]

        action = np.zeros(n, dtype=int)
        for i in range(1, n):
            if trend[i] == 1 and trend[i - 1] == -1:
                action[i] = 1   # 翻多
            elif trend[i] == -1 and trend[i - 1] == 1:
                action[i] = -1  # 翻空

        result[SIGNAL_ACTION] = action
        # 信号强度 = 距趋势线（止损线）的距离占比
        dist = np.where(trend == 1, close - final_lower, final_upper - close)
        result[SIGNAL_STRENGTH] = np.where(action != 0, np.clip(np.abs(dist) / atr / 2, 0.3, 1.0), 0.0)
        result[SIGNAL_SCORE] = np.where(action == 1, np.clip(np.abs(dist) / atr / 2, 0, 1), 0.0)

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0).astype(int)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0.0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0.0)
        return result
