"""Event-driven strategy — detects market events via volume/price anomalies."""

import pandas as pd
import numpy as np

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.strategy.registry import register


@register("event_driven")
class EventDrivenSignal(SignalGenerator):
    """Event-driven strategy that detects market events via anomalies.

    Detects "events" through:
    - Volume surges (unusual trading activity)
    - Price gaps (overnight news reaction)
    - Breakout patterns (sector rotation signals)

    Confirms events with:
    - Fund flow direction (smart money validation)
    - Volume ratio (liquidity confirmation)
    - Accumulation/distribution trend

    Generates signals when events are detected and confirmed.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.vol_surge_period = config.get("vol_surge_period", 20)
        self.vol_surge_threshold = config.get("vol_surge_threshold", 2.0)
        self.gap_threshold = config.get("gap_threshold", 0.03)  # 3% gap
        self.fund_flow_confirm = config.get("fund_flow_confirm", True)
        self.vol_ratio_confirm = config.get("vol_ratio_confirm", True)
        self.lookback_days = config.get("lookback_days", 5)

    def detect_events(self, df: pd.DataFrame) -> pd.DataFrame:
        """Detect market events via anomalies.

        Returns DataFrame with event indicators.
        """
        events = pd.DataFrame(index=df.index)

        # 1. Volume surge detection
        vol_ma = df["volume"].rolling(self.vol_surge_period).mean()
        vol_ratio = df["volume"] / vol_ma
        events["vol_surge"] = vol_ratio > self.vol_surge_threshold
        events["vol_ratio"] = vol_ratio

        # 2. Price gap detection (overnight gap)
        prev_close = df["close"].shift(1)
        gap_pct = (df["open"] - prev_close) / prev_close
        events["gap_up"] = gap_pct > self.gap_threshold
        events["gap_down"] = gap_pct < -self.gap_threshold
        events["gap_pct"] = gap_pct

        # 3. Breakout detection (new highs/lows)
        rolling_high = df["high"].rolling(self.lookback_days).max().shift(1)
        rolling_low = df["low"].rolling(self.lookback_days).min().shift(1)
        events["breakout_up"] = df["close"] > rolling_high
        events["breakout_down"] = df["close"] < rolling_low

        # 4. Fund flow proxy (accumulation/distribution)
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        money_flow = typical_price * df["volume"]
        money_flow_ma = money_flow.rolling(self.vol_surge_period).mean()
        events["fund_flow_ratio"] = money_flow / money_flow_ma

        # Positive fund flow: close in upper half of daily range
        daily_range = df["high"] - df["low"]
        close_position = (df["close"] - df["low"]) / daily_range
        events["positive_flow"] = close_position > 0.6
        events["negative_flow"] = close_position < 0.4

        return events

    def confirm_event(self, events: pd.DataFrame, df: pd.DataFrame) -> pd.Series:
        """Confirm events using multiple signals.

        Returns confidence score [0, 1].
        """
        confidence = pd.Series(0.0, index=events.index)

        # Base confidence from volume surge
        confidence[events["vol_surge"]] = 0.5

        # Boost from gaps
        confidence[events["gap_up"]] += 0.2
        confidence[events["gap_down"]] += 0.2

        # Boost from breakouts
        confidence[events["breakout_up"]] += 0.2
        confidence[events["breakout_down"]] += 0.2

        # Fund flow confirmation
        if self.fund_flow_confirm:
            # Positive flow boosts up events
            positive_event = (events["gap_up"] | events["breakout_up"] | events["vol_surge"])
            confidence[positive_event & events["positive_flow"]] += 0.2

            # Negative flow boosts down events
            negative_event = (events["gap_down"] | events["breakout_down"] | events["vol_surge"])
            confidence[negative_event & events["negative_flow"]] += 0.2

        # Volume ratio confirmation
        if self.vol_ratio_confirm:
            high_vol = events["vol_ratio"] > 1.5
            confidence[high_vol] += 0.1

        # Cap at 1.0
        confidence = confidence.clip(0, 1)

        return confidence

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # Detect events
        events = self.detect_events(df)

        # Confirm events
        confidence = self.confirm_event(events, df)

        # Determine event direction
        # Positive events: gap_up, breakout_up, vol_surge with positive flow
        positive_event = (
            events["gap_up"] |
            events["breakout_up"] |
            (events["vol_surge"] & events["positive_flow"])
        )

        # Negative events: gap_down, breakout_down, vol_surge with negative flow
        negative_event = (
            events["gap_down"] |
            events["breakout_down"] |
            (events["vol_surge"] & events["negative_flow"])
        )

        # Generate signals
        result[SIGNAL_ACTION] = 0
        result[SIGNAL_STRENGTH] = 0.0

        # Buy on positive events with high confidence
        buy_mask = positive_event & (confidence > 0.6)
        result.loc[buy_mask, SIGNAL_ACTION] = 1
        result.loc[buy_mask, SIGNAL_STRENGTH] = confidence[buy_mask]

        # Sell on negative events with high confidence
        sell_mask = negative_event & (confidence > 0.6)
        result.loc[sell_mask, SIGNAL_ACTION] = -1
        result.loc[sell_mask, SIGNAL_STRENGTH] = confidence[sell_mask]

        # Score: direction * strength
        result[SIGNAL_SCORE] = result[SIGNAL_ACTION] * result[SIGNAL_STRENGTH]

        # Fill NaN
        result[SIGNAL_ACTION] = result[SIGNAL_ACTION].fillna(0)
        result[SIGNAL_STRENGTH] = result[SIGNAL_STRENGTH].fillna(0)
        result[SIGNAL_SCORE] = result[SIGNAL_SCORE].fillna(0)

        return result
