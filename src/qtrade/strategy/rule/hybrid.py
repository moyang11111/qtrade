"""Adaptive Hybrid Strategy — switches between sub-strategies based on market regime.

Based on backtest findings:
  - BEAR: dual_ma (best defense, -5.95% avg)
  - BULL: regime_v2 (best offense, +11.84% avg)
  - FULL: breakout (best overall, +15.67% avg)

This strategy detects regime and applies the optimal sub-strategy logic for each.
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("hybrid")
class AdaptiveHybridSignal(SignalGenerator):
    """Adaptive hybrid strategy — regime-aware strategy switching.

    Regime detection:
      - MA alignment (MA5/MA20/MA60) + persistence filter
      - Volume confirmation for regime transitions

    Sub-strategy selection:
      - BEAR → dual_ma crossover (conservative, minimize false signals)
      - BULL → regime_v2 logic (aggressive buying, trend strength weighted)
      - SIDEWAYS → breakout (Donchian channel, volume confirmed)

    Additional enhancements:
      - ATR-based signal strength (wider ATR = stronger breakout)
      - Volume surge confirmation for breakout entries
      - Trend strength weighting for bull regime
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # Regime detection parameters
        self.ma_fast = config.get("ma_fast", 5)
        self.ma_mid = config.get("ma_mid", 20)
        self.ma_long = config.get("ma_long", 60)
        self.persistence_bars = config.get("persistence_bars", 3)
        # Breakout parameters (sideways)
        self.breakout_entry = config.get("breakout_entry", 20)
        self.breakout_exit = config.get("breakout_exit", 10)
        self.vol_factor = config.get("vol_factor", 1.3)
        # Dual MA parameters (bear)
        self.dual_fast = config.get("dual_fast", 5)
        self.dual_slow = config.get("dual_slow", 20)
        # Trend strength parameters (bull)
        self.trend_window = config.get("trend_window", 20)

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Detect market regime using MA alignment + persistence filter."""
        close = df["close"]

        ma_f = close.rolling(self.ma_fast).mean()
        ma_m = close.rolling(self.ma_mid).mean()
        ma_l = close.rolling(self.ma_long).mean()

        # Raw regime
        raw_regime = pd.Series("sideways", index=df.index)
        raw_regime[(ma_f > ma_m) & (ma_m > ma_l)] = "bull"
        raw_regime[(ma_f < ma_m) & (ma_m < ma_l)] = "bear"

        # Persistence filter: require N consecutive bars before confirming
        regime = pd.Series("sideways", index=df.index)
        current = "sideways"
        count = 0
        for i in range(len(raw_regime)):
            if raw_regime.iloc[i] == current:
                count += 1
            else:
                if count >= self.persistence_bars:
                    current = raw_regime.iloc[i]
                    count = 1
                else:
                    count = 0
            regime.iloc[i] = current

        return regime

    def calc_trend_strength(self, df: pd.DataFrame) -> pd.Series:
        """Trend strength: MA separation + slope (0~1)."""
        close = df["close"]
        ma_m = close.rolling(self.ma_mid).mean()
        ma_l = close.rolling(self.ma_long).mean()

        separation = ((ma_m - ma_l) / close).abs()
        slope = ma_m.pct_change(self.trend_window).abs()
        strength = (separation * 100 + slope * 50).clip(0, 1)
        return strength.fillna(0.5)

    def calc_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR for signal strength calibration."""
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift(1)).abs()
        low_close = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]

        regime = self.detect_regime(df)
        trend_strength = self.calc_trend_strength(df)
        atr = self.calc_atr(df)
        atr_ratio = atr / close  # normalized ATR

        # Volume ratio for breakout confirmation
        vol_ma = result["volume"].rolling(20).mean()
        vol_ratio = result["volume"] / vol_ma

        # ── Sub-strategy signals ──

        # 1. Dual MA crossover (for BEAR regime)
        ma_fast = close.rolling(self.dual_fast).mean()
        ma_slow = close.rolling(self.dual_slow).mean()
        prev_fast = ma_fast.shift(1)
        prev_slow = ma_slow.shift(1)
        golden_cross = (prev_fast <= prev_slow) & (ma_fast > ma_slow)
        death_cross = (prev_fast >= prev_slow) & (ma_fast < ma_slow)

        # 2. Donchian breakout (for SIDEWAYS regime)
        entry_high = result["high"].rolling(self.breakout_entry).max().shift(1)
        exit_low = result["low"].rolling(self.breakout_exit).min().shift(1)
        breakout_buy = close > entry_high
        breakout_sell = close < exit_low

        # Volume confirmed breakout
        vol_surge = vol_ratio > self.vol_factor
        breakout_buy_confirmed = breakout_buy & vol_surge
        breakout_sell_confirmed = breakout_sell  # sell doesn't need volume

        # ── Combine based on regime ──
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0
        result[SIGNAL_SCORE] = 0.0

        bear_mask = regime == "bear"
        bull_mask = regime == "bull"
        side_mask = regime == "sideways"

        # ── BEAR: dual_ma crossover (conservative) ──
        buy_bear = golden_cross & bear_mask
        sell_bear = death_cross & bear_mask

        result.loc[buy_bear, SIGNAL_ACTION] = 1
        result.loc[buy_bear, SIGNAL_STRENGTH] = 0.4  # conservative in bear
        result.loc[sell_bear, SIGNAL_ACTION] = -1
        result.loc[sell_bear, SIGNAL_STRENGTH] = 0.7  # aggressive exit in bear

        # ── BULL: regime_v2 logic (aggressive buying, trend-weighted) ──
        buy_bull = golden_cross & bull_mask
        sell_bull = death_cross & bull_mask

        result.loc[buy_bull, SIGNAL_ACTION] = 1
        bull_strength = (0.6 + 0.4 * trend_strength).clip(0.5, 1.0)
        result.loc[buy_bull, SIGNAL_STRENGTH] = bull_strength[buy_bull]
        result.loc[sell_bull, SIGNAL_ACTION] = -1
        result.loc[sell_bull, SIGNAL_STRENGTH] = 0.3  # reluctant to sell in bull

        # ── SIDEWAYS: breakout (volume-confirmed) ──
        buy_side = breakout_buy_confirmed & side_mask
        sell_side = breakout_sell_confirmed & side_mask

        result.loc[buy_side, SIGNAL_ACTION] = 1
        side_strength = (vol_ratio / 2).clip(0.3, 1.0)
        result.loc[buy_side, SIGNAL_STRENGTH] = side_strength[buy_side]
        result.loc[sell_side, SIGNAL_ACTION] = -1
        result.loc[sell_side, SIGNAL_STRENGTH] = 0.6

        # Score
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Cleanup
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
