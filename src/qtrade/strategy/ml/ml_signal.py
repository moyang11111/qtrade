"""ML signal generator — wraps a frozen model to produce signal columns."""

import numpy as np
import pandas as pd

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE
from qtrade.strategy.base import SignalGenerator
from qtrade.features.engine import FeatureEngine


class MLSignalGenerator(SignalGenerator):
    """Generate signals from a trained + frozen ML model."""

    def __init__(self, config: dict, model, feature_engine: FeatureEngine):
        super().__init__(config)
        self.model = model
        self.feature_engine = feature_engine
        self.buy_threshold = config.get("buy_threshold", 0.6)
        self.sell_threshold = config.get("sell_threshold", 0.4)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # 1. Compute features (all backward-looking via shift(1))
        features_df = self.feature_engine.compute_features(result)
        feature_cols = self.feature_engine.get_feature_columns()

        # 2. Model inference
        clean = features_df[feature_cols].fillna(0)
        proba = self.model.predict_proba(clean) if hasattr(self.model, "predict_proba") else self.model.predict(clean).astype(float)

        # 3. Map to signal columns
        result[SIGNAL_SCORE] = (proba * 2 - 1).clip(-1, 1)

        result[SIGNAL_ACTION] = 0
        result.loc[proba >= self.buy_threshold, SIGNAL_ACTION] = 1
        result.loc[proba <= self.sell_threshold, SIGNAL_ACTION] = -1

        result[SIGNAL_STRENGTH] = np.where(
            proba >= self.buy_threshold,
            (proba - self.buy_threshold) / (1.0 - self.buy_threshold),
            np.where(
                proba <= self.sell_threshold,
                (self.sell_threshold - proba) / self.sell_threshold,
                0.0
            )
        )

        # NaN out warmup period
        warmup = self.feature_engine.warmup_period
        result.iloc[:warmup, result.columns.get_loc(SIGNAL_ACTION)] = 0
        result.iloc[:warmup, result.columns.get_loc(SIGNAL_STRENGTH)] = 0.0

        return result
