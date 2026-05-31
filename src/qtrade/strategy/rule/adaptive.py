"""Adaptive Strategy — dynamic parameter adjustment based on market conditions."""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("adaptive")
class AdaptiveSignal(SignalGenerator):
    """Adaptive strategy that switches between modes based on market conditions.

    Key features:
      1. Volatility detection: high vol -> wider stops, smaller positions
      2. Trend strength detection: strong trend -> momentum, weak -> mean reversion
      3. Dynamic parameter adjustment:
         - Momentum mode: trend-following with MA crossovers
         - Mean reversion mode: RSI oversold/overbought
      4. Automatic mode switching with smooth transitions
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # Volatility detection
        self.vol_window = config.get("vol_window", 20)
        self.vol_threshold = config.get("vol_threshold", 1.5)  # High vol = 1.5x normal
        # Trend detection
        self.trend_window = config.get("trend_window", 30)
        self.trend_threshold = config.get("trend_threshold", 0.3)  # Slope threshold
        # Momentum mode parameters
        self.momentum_fast = config.get("momentum_fast", 10)
        self.momentum_slow = config.get("momentum_slow", 30)
        # Mean reversion mode parameters
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        # Position sizing
        self.base_position = config.get("base_position", 1.0)
        self.vol_adjust = config.get("vol_adjust", True)

    def calc_volatility_regime(self, df: pd.DataFrame) -> pd.Series:
        """Detect volatility regime: high or low."""
        close = df["close"]
        returns = close.pct_change()
        vol = returns.rolling(self.vol_window).std()
        vol_ma = vol.rolling(self.vol_window * 3).mean()  # Long-term average

        regime = pd.Series("normal", index=df.index)
        regime[vol > vol_ma * self.vol_threshold] = "high"
        regime[vol < vol_ma / self.vol_threshold] = "low"
        return regime

    def calc_trend_strength(self, df: pd.DataFrame) -> pd.Series:
        """Calculate trend strength (slope of MA)."""
        close = df["close"]
        ma = close.rolling(self.trend_window).mean()

        # Calculate slope as percentage change
        slope = ma.pct_change(self.trend_window)

        # Normalize to 0-1 range
        strength = slope.abs() / self.trend_threshold
        return strength.clip(0, 1)

    def calc_rsi(self, close: pd.Series) -> pd.Series:
        """Calculate RSI."""
        delta = close.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        avg_gain = gain.rolling(self.rsi_period).mean()
        avg_loss = loss.rolling(self.rsi_period).mean()

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def momentum_signal(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Generate momentum signals (trend-following)."""
        close = df["close"]
        ma_fast = close.rolling(self.momentum_fast).mean()
        ma_slow = close.rolling(self.momentum_slow).mean()

        # Golden cross / death cross
        prev_fast = ma_fast.shift(1)
        prev_slow = ma_slow.shift(1)

        buy = (prev_fast <= prev_slow) & (ma_fast > ma_slow)
        sell = (prev_fast >= prev_slow) & (ma_fast < ma_slow)

        return buy, sell

    def mean_reversion_signal(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Generate mean reversion signals (RSI-based)."""
        close = df["close"]
        rsi = self.calc_rsi(close)

        buy = rsi < self.rsi_oversold
        sell = rsi > self.rsi_overbought

        return buy, sell

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # Detect market conditions
        vol_regime = self.calc_volatility_regime(df)
        trend_strength = self.calc_trend_strength(df)

        # Generate signals from both modes
        momentum_buy, momentum_sell = self.momentum_signal(df)
        mr_buy, mr_sell = self.mean_reversion_signal(df)

        # Blend signals based on trend strength
        # Strong trend (strength > 0.5) -> momentum mode
        # Weak trend (strength < 0.5) -> mean reversion mode
        trend_weight = trend_strength.clip(0, 1)
        mr_weight = 1 - trend_weight

        # Blend buy signals
        blended_buy = pd.Series(False, index=df.index)
        blended_buy[momentum_buy] = True
        blended_buy[mr_buy & (mr_weight > 0.6)] = True

        # Blend sell signals
        blended_sell = pd.Series(False, index=df.index)
        blended_sell[momentum_sell] = True
        blended_sell[mr_sell & (mr_weight > 0.6)] = True

        # Generate action signals
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0
        result[SIGNAL_SCORE] = 0.0

        result.loc[blended_buy, SIGNAL_ACTION] = 1
        result.loc[blended_sell, SIGNAL_ACTION] = -1

        # Calculate strength with volatility adjustment
        base_strength = pd.Series(0.5, index=df.index)

        # Trend strength increases confidence
        base_strength = base_strength + trend_weight * 0.3

        # Volatility adjustment: reduce position in high vol
        if self.vol_adjust:
            vol_factor = pd.Series(1.0, index=df.index)
            vol_factor[vol_regime == "high"] = 0.5  # Half position in high vol
            vol_factor[vol_regime == "low"] = 1.5   # 1.5x position in low vol
            base_strength = base_strength * vol_factor

        result[SIGNAL_STRENGTH] = base_strength.clip(0.3, 1.0)
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Cleanup
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
