"""Shared constants."""

# A-share trading rules
LOT_SIZE = 100  # Minimum trade unit (shares)
TRADING_DAYS_PER_YEAR = 252

# Signal column names
SIGNAL_ACTION = "signal_action"
SIGNAL_STRENGTH = "signal_strength"
SIGNAL_SCORE = "signal_score"
SIGNAL_COLUMNS = [SIGNAL_ACTION, SIGNAL_STRENGTH, SIGNAL_SCORE]

# Signal action values
ACTION_SELL = -1
ACTION_HOLD = 0
ACTION_BUY = 1

# OHLCV columns
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
