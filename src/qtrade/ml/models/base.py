"""BaseModel ABC — unified interface for all ML models."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("qtrade.ml.models.base")


class BaseModel(ABC):
    """Abstract base for ML models with freeze/clone/save/load."""

    def __init__(self, config: dict):
        self._config = config
        self._frozen = False

    @abstractmethod
    def _fit(self, X: pd.DataFrame, y: pd.Series) -> None: ...

    @abstractmethod
    def _predict(self, X: pd.DataFrame) -> np.ndarray: ...

    @abstractmethod
    def save(self, path: Path) -> None: ...

    @abstractmethod
    def load(self, path: Path) -> None: ...

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "BaseModel":
        if self._frozen:
            raise RuntimeError("Cannot train a frozen model!")
        self._fit(X, y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self._predict(X)

    def freeze(self) -> None:
        self._frozen = True
        logger.info("[MODEL] Frozen — no further training allowed.")

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def clone(self) -> "BaseModel":
        """Create a fresh unfrozen copy with same config."""
        return self.__class__(dict(self._config))

    def evaluate(self, X: pd.DataFrame, y: pd.Series) -> dict:
        preds = self.predict(X)
        accuracy = (preds == y).mean()
        return {"accuracy": round(float(accuracy), 4), "n_samples": len(X)}
