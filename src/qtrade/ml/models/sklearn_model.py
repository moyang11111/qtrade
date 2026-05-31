"""Sklearn-based models (LogisticRegression, RandomForest)."""

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from qtrade.ml.models.base import BaseModel


class SklearnModel(BaseModel):
    """Wrapper for sklearn classifiers."""

    def __init__(self, config: dict):
        super().__init__(config)
        model_type = config.get("model_type", "logistic")
        if model_type == "random_forest":
            from sklearn.ensemble import RandomForestClassifier
            self._model = RandomForestClassifier(
                n_estimators=config.get("n_estimators", 100),
                max_depth=config.get("max_depth", 5),
                random_state=42,
            )
        else:
            from sklearn.linear_model import LogisticRegression
            self._model = LogisticRegression(max_iter=1000, random_state=42)

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
