"""SignalGenerator ABC — unified interface for rule and ML strategies."""

from abc import ABC, abstractmethod

import pandas as pd

from qtrade.constants import SIGNAL_COLUMNS, SIGNAL_ACTION


class SignalGenerator(ABC):
    """Abstract base for all signal generators.

    Both rule-based (DualMA) and ML (XGBoost) strategies implement this.
    Output: DataFrame with signal_action, signal_strength, signal_score columns.
    """

    def __init__(self, config: dict):
        self._config = config
        self._name = config.get("name", self.__class__.__name__)

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate signals for every bar.

        Args:
            df: OHLCV DataFrame with DatetimeIndex.

        Returns:
            Copy of df with signal_action/strength/score columns added.
        """
        ...

    def get_params(self) -> dict:
        return dict(self._config)

    def validate(self, df: pd.DataFrame) -> None:
        """Validate generated signals."""
        result = self.generate_signals(df)
        for col in SIGNAL_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"
        assert len(result) == len(df), "Signal length mismatch"
        valid = {-1, 0, 1}
        actual = set(result[SIGNAL_ACTION].dropna().unique())
        assert actual.issubset(valid), f"Invalid actions: {actual - valid}"
