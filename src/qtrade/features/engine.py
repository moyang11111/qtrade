"""FeatureEngine — orchestrates feature computation."""

import logging

import pandas as pd

from qtrade.features import technical, momentum, volatility, volume, target

logger = logging.getLogger("qtrade.features.engine")


class FeatureEngine:
    """Compute all features from OHLCV data."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._feature_cols: list[str] = []

    @property
    def warmup_period(self) -> int:
        return 250  # max lookback across all features (MA250)

    def compute_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute all features. All outputs are shift(1) for anti-lookahead."""
        result = df.copy()
        close = df["close"]
        volume = df["volume"]

        # Technical
        result["rsi_14"] = technical.compute_rsi(close, 14)
        result["macd_hist"] = technical.compute_macd_hist(close)
        result["bb_position"] = technical.compute_bb_position(close, 20)
        result["ma_ratio_5_20"] = technical.compute_ma_ratio(close, 5, 20)
        result["ma_ratio_10_50"] = technical.compute_ma_ratio(close, 10, 50)
        result["atr_ratio"] = technical.compute_atr_ratio(df, 14)

        # Long-period technical
        result["ma_ratio_60"] = technical.compute_long_ma(close, 60)
        result["ma_ratio_120"] = technical.compute_long_ma(close, 120)
        result["ma_ratio_250"] = technical.compute_long_ma(close, 250)
        result["long_atr_60"] = technical.compute_long_atr(df, 60)
        result["trend_strength"] = technical.compute_trend_strength(close, 20)

        # Momentum
        result["return_5d"] = momentum.compute_return(close, 5)
        result["return_20d"] = momentum.compute_return(close, 20)
        result["return_60d"] = momentum.compute_return(close, 60)
        result["vol_ratio_5_20"] = momentum.compute_vol_ratio(volume, 5, 20)
        result["vol_momentum"] = momentum.compute_vol_momentum(volume, 5)

        # Volatility
        result["realized_vol_20"] = volatility.compute_realized_vol(close, 20)
        result["vol_regime"] = volatility.compute_vol_regime(close, 20, 60)

        # Volume-price factors
        result["obv_slope"] = volume.compute_obv_slope(df, 10)
        result["vwap_deviation"] = volume.compute_vwap_deviation(df, 20)
        result["volume_ratio"] = volume.compute_volume_ratio(df, 5)
        result["volume_surge"] = volume.compute_volume_surge(df, 5, 2.0)
        result["fund_flow"] = volume.compute_fund_flow_proxy(df)
        result["fund_flow_ma5"] = volume.compute_fund_flow_ma(df, 5)
        result["smart_money"] = volume.compute_smart_money_index(df, 20)
        result["vol_price_corr"] = volume.compute_volume_price_correlation(df, 20)
        result["ad_slope"] = volume.compute_ad_slope(df, 10)

        self._feature_cols = [c for c in result.columns
                              if c not in ["open", "high", "low", "close", "volume"]]
        return result

    def compute_features_and_target(self, df: pd.DataFrame,
                                    horizon: int = 5, threshold: float = 0.02) -> pd.DataFrame:
        """Compute features + forward return target (for training only)."""
        result = self.compute_features(df)
        result["target"] = target.compute_forward_return(df["close"], horizon, threshold)
        return result

    def get_feature_columns(self) -> list[str]:
        return list(self._feature_cols)
