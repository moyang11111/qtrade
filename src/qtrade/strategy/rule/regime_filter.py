"""Market regime filter strategy — adapts to bull/bear/sideways markets."""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("regime_filter")
class RegimeFilterSignal(SignalGenerator):
    """Market regime filter that adapts strategy based on market state.

    Detects three regimes:
    - BULL: price above MA60, MA60 above MA120 → trend following
    - BEAR: price below MA60, MA60 below MA120 → mean reversion
    - SIDEWAYS: otherwise → reduced position

    This is a meta-strategy that modifies signals from other strategies
    based on market regime.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.ma_short = config.get("ma_short", 20)
        self.ma_long = config.get("ma_long", 60)
        self.ma_very_long = config.get("ma_very_long", 120)
        self.bull_boost = config.get("bull_boost", 1.2)
        self.bear_reduce = config.get("bear_reduce", 0.5)
        self.sideways_reduce = config.get("sideways_reduce", 0.7)

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Detect market regime for each bar.

        Returns:
            Series with regime labels: 'bull', 'bear', 'sideways'
        """
        close = df["close"]

        ma_short = close.rolling(self.ma_short).mean()
        ma_long = close.rolling(self.ma_long).mean()
        ma_very_long = close.rolling(self.ma_very_long).mean()

        regime = pd.Series("sideways", index=df.index)

        # BULL: price > MA_short > MA_long > MA_very_long
        bull_mask = (close > ma_short) & (ma_short > ma_long) & (ma_long > ma_very_long)
        regime[bull_mask] = "bull"

        # BEAR: price < MA_short < MA_long < MA_very_long
        bear_mask = (close < ma_short) & (ma_short < ma_long) & (ma_long < ma_very_long)
        regime[bear_mask] = "bear"

        return regime

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # Detect regime
        regime = self.detect_regime(df)

        # Base signals: simple MA crossover
        ma_fast = result["close"].rolling(5).mean()
        ma_slow = result["close"].rolling(20).mean()

        base_signal = pd.Series(0, index=result.index)
        base_signal[ma_fast > ma_slow] = 1
        base_signal[ma_fast < ma_slow] = -1

        # Adjust signals based on regime
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0

        # BULL regime: boost buy signals, reduce sell signals
        bull_mask = regime == "bull"
        buy_in_bull = (base_signal == 1) & bull_mask
        sell_in_bull = (base_signal == -1) & bull_mask

        result.loc[buy_in_bull, SIGNAL_ACTION] = 1
        result.loc[buy_in_bull, SIGNAL_STRENGTH] = 0.8 * self.bull_boost

        result.loc[sell_in_bull, SIGNAL_ACTION] = -1
        result.loc[sell_in_bull, SIGNAL_STRENGTH] = 0.3  # Weak sell in bull

        # BEAR regime: reduce buy signals, boost sell signals
        bear_mask = regime == "bear"
        buy_in_bear = (base_signal == 1) & bear_mask
        sell_in_bear = (base_signal == -1) & bear_mask

        result.loc[buy_in_bear, SIGNAL_ACTION] = 1
        result.loc[buy_in_bear, SIGNAL_STRENGTH] = 0.3  # Weak buy in bear

        result.loc[sell_in_bear, SIGNAL_ACTION] = -1
        result.loc[sell_in_bear, SIGNAL_STRENGTH] = 0.8 * self.bull_boost

        # SIDEWAYS regime: reduce all signals
        side_mask = regime == "sideways"
        buy_in_side = (base_signal == 1) & side_mask
        sell_in_side = (base_signal == -1) & side_mask

        result.loc[buy_in_side, SIGNAL_ACTION] = 1
        result.loc[buy_in_side, SIGNAL_STRENGTH] = 0.5 * self.sideways_reduce

        result.loc[sell_in_side, SIGNAL_ACTION] = -1
        result.loc[sell_in_side, SIGNAL_STRENGTH] = 0.5 * self.sideways_reduce

        # Score: regime-adjusted
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Add regime info to result (for debugging)
        result["_regime"] = regime

        # Fill NaN
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
