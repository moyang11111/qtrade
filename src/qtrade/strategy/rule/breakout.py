"""Momentum breakout signal generator."""

import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("breakout")
class BreakoutSignal(SignalGenerator):
    """Momentum breakout strategy (Donchian Channel).

    Buy when price breaks above the N-day high.
    Sell when price breaks below the M-day low.
    Volume confirmation boosts signal strength.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.entry_period = config.get("entry_period", 20)
        self.exit_period = config.get("exit_period", 10)
        self.vol_factor = config.get("vol_factor", 1.0)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # Donchian channels
        entry_high = result["high"].rolling(self.entry_period).max()
        exit_low = result["low"].rolling(self.exit_period).min()

        # Previous bar's channel levels (avoid lookahead)
        prev_high = entry_high.shift(1)
        prev_low = exit_low.shift(1)

        # Breakout detection
        buy_signal = result["close"] > prev_high    # breaks above N-day high
        sell_signal = result["close"] < prev_low    # breaks below M-day low

        # Score: position relative to the channel
        channel_range = prev_high - prev_low
        channel_pos = (result["close"] - prev_low) / channel_range
        result[SIGNAL_SCORE] = ((channel_pos - 0.5) * 2).clip(-1, 1)

        # Volume confirmation
        vol_ma = result["volume"].rolling(20).mean()
        vol_ratio = result["volume"] / vol_ma

        result[SIGNAL_ACTION] = 0
        result.loc[buy_signal, SIGNAL_ACTION] = 1
        result.loc[sell_signal, SIGNAL_ACTION] = -1

        # Strength: volume-confirmed breakouts are stronger
        result[SIGNAL_STRENGTH] = 0.0
        if buy_signal.any():
            result.loc[buy_signal, SIGNAL_STRENGTH] = (vol_ratio[buy_signal] / 2).clip(0.3, 1.0)
        if sell_signal.any():
            result.loc[sell_signal, SIGNAL_STRENGTH] = (vol_ratio[sell_signal] / 2).clip(0.3, 1.0)

        # Fill NaN warmup
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
