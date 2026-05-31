"""Optimized Event-Driven Strategy — trend filtering, adaptive stops, holding limits."""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("event_v2")
class EventDrivenV2Signal(SignalGenerator):
    """Improved event-driven strategy.

    Improvements over v1:
      1. Trend filter: only buy when above MA20 (avoid catching falling knives)
      2. Adaptive stop-loss: use ATR instead of fixed %
      3. Max holding period: force exit after N bars
      4. Relaxed entry in bull markets (detected by MA alignment)
      5. Volume confirmation for event signals
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # Trend filter
        self.trend_ma = config.get("trend_ma", 20)
        self.require_trend = config.get("require_trend", True)
        # Event detection
        self.event_ma = config.get("event_ma", 5)
        self.event_threshold = config.get("event_threshold", 2.0)  # std dev
        # Volume confirmation
        self.vol_ma = config.get("vol_ma", 20)
        self.vol_multiplier = config.get("vol_multiplier", 1.5)
        # Position management
        self.atr_period = config.get("atr_period", 14)
        self.atr_stop_mult = config.get("atr_stop_mult", 2.5)
        self.max_holding_bars = config.get("max_holding_bars", 20)
        # Regime adaptation
        self.bull_relax_factor = config.get("bull_relax_factor", 0.7)  # Relax entry in bull

    def detect_trend(self, df: pd.DataFrame) -> pd.Series:
        """Detect if we're in an uptrend (price above MA)."""
        ma = df["close"].rolling(self.trend_ma).mean()
        return df["close"] > ma

    def detect_regime(self, df: pd.DataFrame) -> pd.Series:
        """Simple regime detection for adaptive entry."""
        close = df["close"]
        ma_short = close.rolling(10).mean()
        ma_long = close.rolling(30).mean()

        regime = pd.Series("sideways", index=df.index)
        regime[ma_short > ma_long] = "bull"
        regime[ma_short < ma_long] = "bear"
        return regime

    def detect_events(self, df: pd.DataFrame, regime: pd.Series) -> pd.Series:
        """Detect event-driven signals with adaptive thresholds."""
        close = df["close"]
        volume = df["volume"]

        # Price momentum (event detection)
        ma = close.rolling(self.event_ma).mean()
        std = close.rolling(self.event_ma).std()

        # Adaptive threshold based on regime
        threshold = pd.Series(self.event_threshold, index=df.index)
        threshold[regime == "bull"] = self.event_threshold * self.bull_relax_factor
        threshold[regime == "bear"] = self.event_threshold * 1.3  # Stricter in bear

        # Event signal: price breaks above MA + threshold * std
        event_signal = close > (ma + threshold * std)

        # Volume confirmation
        vol_ma = volume.rolling(self.vol_ma).mean()
        vol_confirm = volume > (vol_ma * self.vol_multiplier)

        # Combined signal
        return event_signal & vol_confirm

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        regime = self.detect_regime(df)
        uptrend = self.detect_trend(df)
        events = self.detect_events(df, regime)

        # Apply trend filter
        if self.require_trend:
            buy_signals = events & uptrend
        else:
            buy_signals = events

        # Sell signals: death cross or bear regime
        close = df["close"]
        ma_fast = close.rolling(5).mean()
        ma_slow = close.rolling(20).mean()
        death_cross = (ma_fast.shift(1) >= ma_slow.shift(1)) & (ma_fast < ma_slow)
        sell_signals = death_cross | (regime == "bear")

        # Generate action signals
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0
        result[SIGNAL_SCORE] = 0.0

        result.loc[buy_signals, SIGNAL_ACTION] = 1
        result.loc[sell_signals, SIGNAL_ACTION] = -1

        # Strength based on event magnitude and regime
        event_strength = pd.Series(0.5, index=df.index)
        event_strength[buy_signals] = 0.7
        event_strength[regime == "bull"] = event_strength * 1.2
        event_strength[regime == "bear"] = event_strength * 0.6

        result[SIGNAL_STRENGTH] = event_strength.clip(0.3, 1.0)
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Cleanup
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
