"""Signal column definitions and helpers."""

from qtrade.constants import SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE


def add_empty_signal_columns(df) -> "pd.DataFrame":
    """Add signal columns with default (hold/neutral) values."""
    import pandas as pd
    result = df.copy()
    result[SIGNAL_ACTION] = 0
    result[SIGNAL_STRENGTH] = 0.0
    result[SIGNAL_SCORE] = 0.0
    return result
