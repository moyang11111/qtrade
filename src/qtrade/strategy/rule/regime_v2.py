"""Optimized Regime Filter — faster detection, volume confirmation, adaptive coefficients."""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("regime_v2")
class RegimeFilterV2Signal(SignalGenerator):
    """Improved regime filter strategy.

    Improvements over v1:
      1. Fast MA pair (MA5/MA20) for quicker regime detection
      2. Volume confirmation for regime transitions
      3. Adaptive coefficients — stronger boost/reduce in confirmed regimes
      4. Regime persistence filter — avoid rapid flipping
      5. Trend strength weighting — bigger positions in stronger trends
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # Fast regime detection
        self.ma_fast = config.get("ma_fast", 5)
        self.ma_mid = config.get("ma_mid", 20)
        # Slow confirmation
        self.ma_long = config.get("ma_long", 60)
        # Volume confirmation
        self.vol_confirm_period = config.get("vol_confirm_period", 20)
        self.vol_surge_ratio = config.get("vol_surge_ratio", 1.3)
        # Persistence: require N consecutive bars in same regime
        self.persistence_bars = config.get("persistence_bars", 3)
        # Base signal parameters
        self.signal_fast = config.get("signal_fast", 5)
        self.signal_slow = config.get("signal_slow", 20)

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Detect market regime with fast MAs and volume confirmation."""
        close = df["close"]
        volume = df["volume"]

        # Fast regime detection
        ma_f = close.rolling(self.ma_fast).mean()
        ma_m = close.rolling(self.ma_mid).mean()
        ma_l = close.rolling(self.ma_long).mean()

        # Volume average for confirmation
        vol_avg = volume.rolling(self.vol_confirm_period).mean()
        vol_surge = volume > vol_avg * self.vol_surge_ratio

        # Raw regime detection
        raw_regime = pd.Series("sideways", index=df.index)

        # BULL: fast MA > mid MA > long MA
        bull = (ma_f > ma_m) & (ma_m > ma_l)
        raw_regime[bull] = "bull"

        # BEAR: fast MA < mid MA < long MA
        bear = (ma_f < ma_m) & (ma_m < ma_l)
        raw_regime[bear] = "bear"

        # Apply persistence filter: only confirm regime change after N bars
        regime = pd.Series("sideways", index=df.index)
        current_regime = "sideways"
        regime_count = 0

        for i in range(len(raw_regime)):
            if raw_regime.iloc[i] == current_regime:
                regime_count += 1
            else:
                if regime_count >= self.persistence_bars:
                    current_regime = raw_regime.iloc[i]
                    regime_count = 1
                else:
                    regime_count = 0

            regime.iloc[i] = current_regime

        # Volume confirmation: upgrades sideways to bull/bear if volume confirms
        for i in range(len(regime)):
            if regime.iloc[i] == "sideways" and vol_surge.iloc[i]:
                if ma_f.iloc[i] > ma_m.iloc[i]:
                    regime.iloc[i] = "bull"
                elif ma_f.iloc[i] < ma_m.iloc[i]:
                    regime.iloc[i] = "bear"

        return regime

    def calc_trend_strength(self, df: pd.DataFrame) -> pd.Series:
        """Calculate trend strength (0-1) based on MA slope and separation."""
        close = df["close"]
        ma_m = close.rolling(self.ma_mid).mean()
        ma_l = close.rolling(self.ma_long).mean()

        # MA separation normalized by price
        separation = ((ma_m - ma_l) / close).abs()

        # MA slope (20-bar)
        slope = ma_m.pct_change(20).abs()

        # Combine: stronger separation + steeper slope = stronger trend
        strength = (separation * 100 + slope * 50).clip(0, 1)
        return strength.fillna(0.5)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        regime = self.detect_regime(df)
        trend_strength = self.calc_trend_strength(df)

        # Base signals: MA crossover
        close = result["close"]
        ma_fast = close.rolling(self.signal_fast).mean()
        ma_slow = close.rolling(self.signal_slow).mean()

        # Golden cross / death cross
        prev_fast = ma_fast.shift(1)
        prev_slow = ma_slow.shift(1)

        golden_cross = (prev_fast <= prev_slow) & (ma_fast > ma_slow)
        death_cross = (prev_fast >= prev_slow) & (ma_fast < ma_slow)

        # Generate signals with regime adaptation
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0
        result[SIGNAL_SCORE] = 0.0

        # ── BULL regime: aggressive buying, conservative selling ──
        bull_mask = regime == "bull"
        buy_bull = golden_cross & bull_mask
        sell_bull = death_cross & bull_mask

        result.loc[buy_bull, SIGNAL_ACTION] = 1
        # Strength scales with trend strength
        result.loc[buy_bull, SIGNAL_STRENGTH] = (0.6 + 0.4 * trend_strength).clip(0.5, 1.0)

        result.loc[sell_bull, SIGNAL_ACTION] = -1
        result.loc[sell_bull, SIGNAL_STRENGTH] = 0.3  # Weak sell in bull

        # ── BEAR regime: conservative buying, aggressive selling ──
        bear_mask = regime == "bear"
        buy_bear = golden_cross & bear_mask
        sell_bear = death_cross & bear_mask

        result.loc[buy_bear, SIGNAL_ACTION] = 1
        result.loc[buy_bear, SIGNAL_STRENGTH] = 0.3  # Weak buy in bear

        result.loc[sell_bear, SIGNAL_ACTION] = -1
        result.loc[sell_bear, SIGNAL_STRENGTH] = (0.6 + 0.4 * trend_strength).clip(0.5, 1.0)

        # ── SIDEWAYS regime: balanced ──
        side_mask = regime == "sideways"
        buy_side = golden_cross & side_mask
        sell_side = death_cross & side_mask

        result.loc[buy_side, SIGNAL_ACTION] = 1
        result.loc[buy_side, SIGNAL_STRENGTH] = 0.5

        result.loc[sell_side, SIGNAL_ACTION] = -1
        result.loc[sell_side, SIGNAL_STRENGTH] = 0.5

        # Score
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Cleanup
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
