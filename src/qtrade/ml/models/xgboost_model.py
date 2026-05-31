"""XGBoost model wrapper."""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from qtrade.ml.models.base import BaseModel


class XGBoostModel(BaseModel):
    """XGBoost classifier for tabular factor data."""

    def __init__(self, config: dict):
        super().__init__(config)
        from xgboost import XGBClassifier
        self._model = XGBClassifier(
            n_estimators=config.get("n_estimators", 200),
            max_depth=config.get("max_depth", 5),
            learning_rate=config.get("learning_rate", 0.05),
            subsample=config.get("subsample", 0.8),
            colsample_bytree=config.get("colsample_bytree", 0.8),
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )

    def _fit(self, X: pd.DataFrame, y: pd.Series) -> None:
        self._model.fit(X, y)

    def _predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict(X)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self._model.predict_proba(X)[:, 1]

    def save(self, path: Path) -> None:
        with open(path, "wb") as f:
            pickle.dump(self._model, f)

    def load(self, path: Path) -> None:
        with open(path, "rb") as f:
            self._model = pickle.load(f)
