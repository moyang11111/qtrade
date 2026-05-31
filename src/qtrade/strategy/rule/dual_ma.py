"""Dual Moving Average crossover signal generator."""

import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("dual_ma")
class DualMASignal(SignalGenerator):
    """Golden cross -> BUY, Death cross -> SELL."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.fast = config.get("fast_period", 5)
        self.slow = config.get("slow_period", 20)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result["ma_fast"] = result["close"].rolling(self.fast).mean()
        result["ma_slow"] = result["close"].rolling(self.slow).mean()

        # Score: normalized MA distance
        result[SIGNAL_SCORE] = (
            (result["ma_fast"] - result["ma_slow"]) / result["ma_slow"]
        ).clip(-0.1, 0.1) / 0.1

        # Crossover detection
        prev_fast = result["ma_fast"].shift(1)
        prev_slow = result["ma_slow"].shift(1)

        golden = (prev_fast <= prev_slow) & (result["ma_fast"] > result["ma_slow"])
        death = (prev_fast >= prev_slow) & (result["ma_fast"] < result["ma_slow"])

        result[SIGNAL_ACTION] = 0
        result.loc[golden, SIGNAL_ACTION] = 1
        result.loc[death, SIGNAL_ACTION] = -1
        result[SIGNAL_STRENGTH] = result[SIGNAL_SCORE].abs()

        return result.drop(columns=["ma_fast", "ma_slow"])
