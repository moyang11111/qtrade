"""Bollinger Band mean reversion signal generator."""

import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("bollinger")
class BollingerSignal(SignalGenerator):
    """Bollinger Band mean reversion.

    Buy when price touches/crosses below the lower band (oversold).
    Sell when price touches/crosses above the upper band (overbought).
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.period = config.get("period", 20)
        self.std_mult = config.get("std_mult", 2.0)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # Compute Bollinger Bands
        mid = result["close"].rolling(self.period).mean()
        std = result["close"].rolling(self.period).std()
        upper = mid + self.std_mult * std
        lower = mid - self.std_mult * std

        # Position within the bands: 0 = lower band, 0.5 = middle, 1 = upper band
        band_width = upper - lower
        position = (result["close"] - lower) / band_width

        # Score: how extreme is the position (-1 at lower, +1 at upper)
        result[SIGNAL_SCORE] = (position - 0.5) * 2  # scale to [-1, +1]

        # Detect band touches (using shift(1) to avoid lookahead)
        close = result["close"]
        prev_lower = lower.shift(1)
        prev_upper = upper.shift(1)

        buy_signal = close <= lower           # price at/below lower band
        sell_signal = close >= upper          # price at/above upper band

        result[SIGNAL_ACTION] = 0
        result.loc[buy_signal, SIGNAL_ACTION] = 1
        result.loc[sell_signal, SIGNAL_ACTION] = -1

        # Strength: how far into the band (more extreme = stronger signal)
        result[SIGNAL_STRENGTH] = 0.0
        result.loc[buy_signal, SIGNAL_STRENGTH] = (1 - position[buy_signal]).clip(0, 1)
        result.loc[sell_signal, SIGNAL_STRENGTH] = position[sell_signal].clip(0, 1)

        # Fill NaN warmup period with hold
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
