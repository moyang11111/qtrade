"""Dual Thrust Signal — 经典区间突破策略（日线适配版）。

来源：Dual Thrust 算法，参考 fmzquant/strategies 经典实现与
vnpy（GitHub 44k+ star）官方示例 dual_thrust_strategy.py（K1=0.4, K2=0.6）。

经典公式（过去 N 日，不含当日）：
  HH = 最高高价   HC = 最高收盘价   LC = 最低收盘价   LL = 最低低价
  Range = max(HH − LC, HC − LL)
  买入线 = 当日开盘 + K1 × Range；卖出线 = 当日开盘 − K2 × Range

日线适配（A 股 T+1）：收盘价上破买入线开多，收盘价下破卖出线平仓。
"""

import numpy as np
import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("dual_thrust")
class DualThrustSignal(SignalGenerator):
    """Dual Thrust(N=4, K1=0.4, K2=0.6)：开盘 ± K×Range 突破。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.lookback = config.get("lookback", 4)
        self.k1 = config.get("k1", 0.4)
        self.k2 = config.get("k2", 0.6)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        high = result["high"]
        low = result["low"]
        close = result["close"]
        open_ = result["open"]
        n = len(result)

        hh = high.rolling(self.lookback).max().shift(1)
        hc = close.rolling(self.lookback).max().shift(1)
        lc = close.rolling(self.lookback).min().shift(1)
        ll = low.rolling(self.lookback).min().shift(1)
        rng = np.maximum(hh - lc, hc - ll)

        buy_line = open_ + self.k1 * rng
        sell_line = open_ - self.k2 * rng

        buy_break = close > buy_line
        sell_break = close < sell_line

        action = np.zeros(n, dtype=int)
        holding = False
        for i in range(n):
            if not holding and buy_break.iloc[i] and not np.isnan(rng.iloc[i]):
                action[i] = 1
                holding = True
            elif holding and sell_break.iloc[i]:
                action[i] = -1
                holding = False

        # 突破力度：收盘超出买入线的幅度 / Range
        exceed = (close - buy_line) / rng.replace(0, np.nan)

        result[SIGNAL_ACTION] = action
        result[SIGNAL_STRENGTH] = np.where(action == 1, np.clip(exceed / 0.5, 0.3, 1.0), 0.0)
        result[SIGNAL_SCORE] = np.where(action == 1, np.clip(exceed / 0.5, 0, 1), 0.0)

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0).astype(int)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0.0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0.0)
        return result
