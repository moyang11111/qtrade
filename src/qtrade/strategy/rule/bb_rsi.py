"""Bollinger Band + RSI mean reversion strategy.

Buy: price breaks below lower Bollinger Band AND RSI < 20
Sell: RSI > 65

Logic: extreme oversold bounce — catch the knife when both technical
indicators scream "too cheap", then exit when momentum recovers.
"""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("bb_rsi")
class BBRsiSignal(SignalGenerator):
    """Bollinger Band + RSI mean reversion strategy."""

    def __init__(self, config: dict):
        super().__init__(config)
        # Bollinger Band parameters
        self.bb_period = config.get("bb_period", 20)
        self.bb_std = config.get("bb_std", 2.0)
        # RSI parameters
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_buy = config.get("rsi_buy", 20)    # buy threshold
        self.rsi_sell = config.get("rsi_sell", 65)   # sell threshold

    def _calc_rsi(self, close: pd.Series) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(self.rsi_period).mean()
        loss = (-delta.clip(upper=0)).rolling(self.rsi_period).mean()
        rs = gain / loss
        rsi = 100 - 100 / (1 + rs)
        return rsi

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]

        # Bollinger Bands
        bb_mid = close.rolling(self.bb_period).mean()
        bb_std = close.rolling(self.bb_period).std()
        bb_upper = bb_mid + self.bb_std * bb_std
        bb_lower = bb_mid - self.bb_std * bb_std

        # RSI
        rsi = self._calc_rsi(close)

        # BB position (0 = lower band, 1 = upper band)
        bb_range = bb_upper - bb_lower
        bb_pos = (close - bb_lower) / bb_range

        # ── Buy: price below lower BB AND RSI < threshold ──
        below_lower = close < bb_lower
        rsi_oversold = rsi < self.rsi_buy
        buy_signal = below_lower & rsi_oversold

        # ── Sell: RSI > threshold ──
        sell_signal = rsi > self.rsi_sell

        # Score: how far below lower band (more negative = more oversold)
        result[SIGNAL_SCORE] = ((bb_pos - 0.5) * 2).clip(-1, 1)

        # Signals
        result[SIGNAL_ACTION] = 0
        result.loc[buy_signal, SIGNAL_ACTION] = 1
        result.loc[sell_signal, SIGNAL_ACTION] = -1

        # Strength: deeper oversold = stronger signal
        # RSI distance from buy threshold (more oversold = stronger)
        rsi_strength = ((self.rsi_buy - rsi) / self.rsi_buy).clip(0, 1)
        # BB distance below lower band (further = stronger)
        bb_strength = ((bb_lower - close) / close * 100).clip(0, 1)
        combined_strength = (rsi_strength * 0.5 + bb_strength * 0.5).clip(0.3, 1.0)

        result[SIGNAL_STRENGTH] = 0.0
        result.loc[buy_signal, SIGNAL_STRENGTH] = combined_strength[buy_signal]
        result.loc[sell_signal, SIGNAL_STRENGTH] = (rsi[sell_signal] / 100).clip(0.5, 1.0)

        # Fill NaN
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
