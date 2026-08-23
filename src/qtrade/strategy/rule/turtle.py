"""Turtle Signal — 海龟交易法（Donchian 通道突破 + ATR 止损）。

来源：经典《海龟交易法则》，参考 vnpy（GitHub 44k+ star）官方示例
vnpy_ctastrategy/strategies/turtle_signal_strategy.py。

规则（多头适配 A 股 T+1）：
  入场：收盘突破前 20 日最高价（不含当日）
  离场：收盘跌破前 10 日最低价（不含当日）
  硬止损：入场价 - 2 × ATR(20)（与通道下轨取较高者）
"""

import numpy as np
import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("turtle")
class TurtleSignal(SignalGenerator):
    """海龟交易法：20 日突破入场，10 日低点 + 2×ATR 离场。"""

    def __init__(self, config: dict):
        super().__init__(config)
        self.entry_window = config.get("entry_window", 20)
        self.exit_window = config.get("exit_window", 10)
        self.atr_window = config.get("atr_window", 20)
        self.atr_stop_mult = config.get("atr_stop_mult", 2.0)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"].values
        high = result["high"].values
        low = result["low"].values
        n = len(result)

        # Wilder ATR
        prev_close = np.roll(close, 1)
        tr = np.maximum(high - low,
                        np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
        tr[0] = high[0] - low[0]
        atr = pd.Series(tr).ewm(alpha=1 / self.atr_window, adjust=False).mean().values

        entry_high = pd.Series(high).rolling(self.entry_window).max().shift(1).values
        exit_low = pd.Series(low).rolling(self.exit_window).min().shift(1).values

        action = np.zeros(n, dtype=int)
        holding = False
        stop_price = np.nan

        for i in range(n):
            c = close[i]
            if not holding:
                if not np.isnan(entry_high[i]) and c > entry_high[i]:
                    action[i] = 1
                    holding = True
                    stop_price = c - self.atr_stop_mult * atr[i]
            else:
                # 通道下轨与 ATR 止损取较高者（vnpy 做法）
                effective_stop = np.nanmax([exit_low[i], stop_price])
                if c < effective_stop:
                    action[i] = -1
                    holding = False
                    stop_price = np.nan
                else:
                    # 移动止损跟随 ATR 上移
                    stop_price = max(stop_price, c - self.atr_stop_mult * atr[i])

        result[SIGNAL_ACTION] = action
        # 突破力度：突破日涨幅相对通道宽度
        width = entry_high - exit_low
        result[SIGNAL_STRENGTH] = np.where(
            action == 1,
            np.clip((close - entry_high) / np.where(width > 0, width, np.nan), 0.3, 1.0),
            0.0,
        )
        result[SIGNAL_SCORE] = np.where(action == 1, atr / close, 0.0)

        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0).astype(int)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0.0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0.0)
        return result
