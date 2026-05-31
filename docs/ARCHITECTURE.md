# qtrade Architecture Design

> Quantitative trading framework for A-share markets.
> Data: pytdx | Backtest: backtrader | ML: scikit-learn, XGBoost, PyTorch

---

## 1. Directory Structure

```
qtrade/
|-- pyproject.toml                    # PEP 621 project metadata, dependencies
|-- README.md                         # Project overview, quickstart
|-- CLAUDE.md                         # Claude Code skill instructions (future)
|-- Makefile                          # Common commands: make backtest, make train
|-- .gitignore                        # Python + data + model ignores
|
|-- configs/
|   |-- default.yaml                  # Default full config (reference)
|   |-- quick.yaml                    # Minimal config for fast iteration
|   +-- ml_xgboost.yaml              # ML-specific config example
|
|-- data/
|   |-- cache/                        # Auto-downloaded CSV files (gitignored)
|   +-- raw/                          # Manually placed data files
|
|-- models/                           # Trained model artifacts (gitignored)
|   +-- registry.json                 # Model metadata index
|
|-- logs/                             # Runtime log output (gitignored)
|
|-- notebooks/
|   |-- 01_data_exploration.ipynb
|   |-- 02_feature_analysis.ipynb
|   +-- 03_model_experiments.ipynb
|
|-- src/
|   +-- qtrade/
|       |-- __init__.py               # Package version, top-level imports
|       |-- __main__.py               # python -m qtrade entry point
|       |-- cli.py                    # argparse CLI
|       |-- config.py                 # YAML config loader + validator
|       |-- logging_setup.py          # Structured logging configuration
|       |-- constants.py              # Shared constants
|       |
|       |-- data/
|       |   |-- __init__.py
|       |   |-- fetcher.py            # DataFetcher orchestrator
|       |   |-- pytdx_client.py       # PytdxClient: raw pytdx wrapper
|       |   |-- cache.py              # CSVCache: read/write/validate
|       |   +-- schema.py            # Data schema validation
|       |
|       |-- strategy/
|       |   |-- __init__.py
|       |   |-- base.py               # SignalGenerator ABC + Signal dataclass
|       |   |-- registry.py           # Strategy name -> class mapping
|       |   |-- signals.py            # Signal column definitions
|       |   |-- rule/
|       |   |   |-- __init__.py
|       |   |   |-- dual_ma.py        # Dual moving average crossover
|       |   |   |-- rsi_bb.py         # RSI + Bollinger Bands
|       |   |   |-- atr_adaptive.py   # ATR-based adaptive stops
|       |   |   +-- momentum.py       # Volume breakout + momentum
|       |   +-- ml/
|       |       |-- __init__.py
|       |       |-- ml_signal.py      # MLSignalGenerator
|       |       +-- ensemble.py       # Combine multiple ML models
|       |
|       |-- features/
|       |   |-- __init__.py
|       |   |-- engine.py             # FeatureEngine orchestrator
|       |   |-- technical.py          # RSI, MACD, BB, ATR, etc.
|       |   |-- momentum.py           # Return-based, volume-based
|       |   |-- volatility.py         # Volatility regime features
|       |   +-- target.py             # Forward return target
|       |
|       |-- ml/
|       |   |-- __init__.py
|       |   |-- pipeline.py           # MLPipeline: end-to-end
|       |   |-- models/
|       |   |   |-- __init__.py
|       |   |   |-- base.py           # BaseModel ABC
|       |   |   |-- sklearn_model.py  # LogisticRegression, RF
|       |   |   |-- xgboost_model.py  # XGBoost
|       |   |   |-- lightgbm_model.py # LightGBM (future)
|       |   |   +-- lstm_model.py     # PyTorch LSTM
|       |   |-- cv.py                 # TimeSeriesCV
|       |   |-- registry.py           # ModelRegistry
|       |   +-- evaluation.py         # Model metrics
|       |
|       |-- backtest/
|       |   |-- __init__.py
|       |   |-- engine.py             # BacktestEngine
|       |   |-- data_feed.py          # SignalPandasData
|       |   |-- signal_strategy.py    # SignalFollower (bt.Strategy)
|       |   |-- analyzers.py          # Custom bt analyzers
|       |   |-- trade_log.py          # Per-trade CSV output
|       |   +-- performance.py        # Sharpe, DD, returns, etc.
|       |
|       |-- screening/
|       |   |-- __init__.py
|       |   +-- screener.py           # StockScreener (migrated)
|       |
|       +-- visualization/
|           |-- __init__.py
|           |-- charts.py             # Equity curve, drawdown
|           +-- comparison.py         # Multi-strategy comparison
|
+-- tests/
    |-- conftest.py                   # Shared fixtures
    |-- test_config.py
    |-- test_data/
    |   |-- test_fetcher.py
    |   +-- test_cache.py
    |-- test_strategy/
    |   |-- test_signals.py
    |   +-- test_dual_ma.py
    |-- test_backtest/
    |   |-- test_engine.py
    |   +-- test_performance.py
    |-- test_ml/
    |   |-- test_features.py
    |   |-- test_pipeline.py
    |   |-- test_cv.py
    |   +-- test_anti_lookahead.py    # CRITICAL tests
    +-- test_integration/
        +-- test_full_pipeline.py
```

---

## 2. Unified Signal Interface

### The Problem

In the existing project, each `bt.Strategy` subclass contains both signal logic
(when to buy/sell) AND execution logic (position sizing, stop-loss, take-profit).
This coupling means ML models cannot plug into the same backtest flow.

### The Solution: Signal Columns in the Data Feed

All signal generators (rule-based and ML) pre-compute signal values for every bar
in the DataFrame. These signals are added as columns to the OHLCV DataFrame.
A single backtrader strategy class (`SignalFollower`) reads the signal column
and translates it into buy/sell orders.

```
                          +--------------------+
 Rule-based Signal Gen    |                    |   +-------------------+
 (Dual MA, RSI+BB, ...) -->| signal_action      |   |                   |
                          | signal_strength    |-->|  SignalFollower   |--> backtrader
 ML Signal Gen            | signal_score       |   |  (bt.Strategy)    |   execution
 (XGBoost, LSTM, ...)  -->|                    |   |                   |
                          +--------------------+   +-------------------+
```

### Signal Column Definitions

Three columns are appended to the OHLCV DataFrame:

| Column | Type | Values | Meaning |
|--------|------|--------|---------|
| `signal_action` | int | -1, 0, +1 | Sell / Hold / Buy |
| `signal_strength` | float | 0.0 to 1.0 | Confidence (0=weak, 1=strong) |
| `signal_score` | float | -1.0 to +1.0 | Raw score (negative=bearish, positive=bullish) |

**Why three columns?**
- `signal_action` is the final discrete decision the backtrader strategy acts on.
- `signal_strength` enables position sizing (stronger signal = larger position).
- `signal_score` preserves the continuous output from ML models for analysis.

### The SignalGenerator Protocol

```python
# src/qtrade/strategy/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class SignalConfig:
    """Immutable configuration for a signal generator."""
    name: str
    params: dict
    signal_type: str  # "rule" or "ml"


class SignalGenerator(ABC):
    """Abstract base for all signal generators.

    Both rule-based strategies (DualMA, RsiBB) and ML strategies
    (XGBoost, LSTM) implement this interface. The output is always
    a DataFrame with signal_action, signal_strength, signal_score columns.
    """

    def __init__(self, config: dict):
        self._config = config
        self._name = config.get("name", self.__class__.__name__)

    @property
    def name(self) -> str:
        return self._name

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """Generate signals for every bar in the DataFrame.

        Args:
            df: OHLCV DataFrame with DatetimeIndex.

        Returns:
            Copy of df with signal_action, signal_strength, signal_score
            columns added. All three columns must have the same length
            as the input. NaN is acceptable for warmup periods.
        """
        ...

    def get_params(self) -> dict:
        """Return strategy parameters for logging/reproducibility."""
        return dict(self._config)

    def validate(self, df: pd.DataFrame) -> None:
        """Validate that signals are correct."""
        result = self.generate_signals(df)
        assert "signal_action" in result.columns
        assert "signal_strength" in result.columns
        assert "signal_score" in result.columns
        assert len(result) == len(df), "Signal length mismatch!"
        valid_actions = {-1, 0, 1}
        actual = set(result["signal_action"].dropna().unique())
        assert actual.issubset(valid_actions), f"Invalid actions: {actual - valid_actions}"
```

### Example: Dual MA Signal Generator

```python
# src/qtrade/strategy/rule/dual_ma.py

class DualMASignal(SignalGenerator):
    """Dual moving average crossover signal generator.

    Golden cross (fast MA crosses above slow MA) -> BUY
    Death cross  (fast MA crosses below slow MA) -> SELL
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.fast_period = config.get("fast_period", 5)
        self.slow_period = config.get("slow_period", 20)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        result["ma_fast"] = result["close"].rolling(self.fast_period).mean()
        result["ma_slow"] = result["close"].rolling(self.slow_period).mean()

        result["signal_action"] = 0
        result["signal_strength"] = 0.0
        result["signal_score"] = 0.0

        # Score: normalized distance between fast and slow MA
        result["signal_score"] = (
            (result["ma_fast"] - result["ma_slow"]) / result["ma_slow"]
        ).clip(-0.1, 0.1) / 0.1

        # Detect crossovers
        prev_fast = result["ma_fast"].shift(1)
        prev_slow = result["ma_slow"].shift(1)

        golden_cross = (prev_fast <= prev_slow) & (result["ma_fast"] > result["ma_slow"])
        death_cross  = (prev_fast >= prev_slow) & (result["ma_fast"] < result["ma_slow"])

        result.loc[golden_cross, "signal_action"] = 1
        result.loc[death_cross, "signal_action"] = -1

        result["signal_strength"] = result["signal_score"].abs()

        result = result.drop(columns=["ma_fast", "ma_slow"])
        return result
```

### Example: ML Signal Generator

```python
# src/qtrade/strategy/ml/ml_signal.py

class MLSignalGenerator(SignalGenerator):
    """Wraps a trained ML model to produce signals.

    The model must already be trained and frozen.
    """

    def __init__(self, config: dict, model, feature_engine):
        super().__init__(config)
        self.model = model
        self.feature_engine = feature_engine
        self.buy_threshold = config.get("buy_threshold", 0.6)
        self.sell_threshold = config.get("sell_threshold", 0.4)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()

        # 1. Generate features (all backward-looking)
        features_df = self.feature_engine.compute_features(result)

        # 2. Run model inference
        predictions = self.model.predict(features_df)

        # 3. Map predictions to signal columns
        result["signal_score"] = predictions
        result["signal_strength"] = np.where(
            predictions > self.buy_threshold,
            (predictions - self.buy_threshold) / (1.0 - self.buy_threshold),
            np.where(
                predictions < self.sell_threshold,
                (self.sell_threshold - predictions) / self.sell_threshold,
                0.0
            )
        )

        result["signal_action"] = 0
        result.loc[predictions >= self.buy_threshold, "signal_action"] = 1
        result.loc[predictions <= self.sell_threshold, "signal_action"] = -1

        # NaN out warmup period
        warmup = self.feature_engine.warmup_period
        result.iloc[:warmup, result.columns.get_loc("signal_action")] = 0
        result.iloc[:warmup, result.columns.get_loc("signal_strength")] = 0.0

        return result
```

### The Backtrader Signal Consumer

```python
# src/qtrade/backtest/data_feed.py

class SignalPandasData(bt.feeds.PandasData):
    """Extended PandasData that carries signal columns."""
    lines = ("signal_action", "signal_strength", "signal_score")
    params = (
        ("signal_action", -1),
        ("signal_strength", -1),
        ("signal_score", -1),
    )


# src/qtrade/backtest/signal_strategy.py

class SignalFollower(bt.Strategy):
    """Universal strategy that reads signal columns from the data feed.

    This is the ONLY bt.Strategy subclass needed.
    It does not know or care whether signals came from rules or ML.
    """

    params = dict(
        base_position_pct=0.95,
        use_strength_sizing=True,
        stop_loss_pct=0.15,
        trail_stop_pct=0.10,
    )

    def __init__(self):
        self.order = None
        self.entry_price = None
        self.highest_since_entry = 0
        self.trade_log = []

    def next(self):
        if self.order:
            return

        action = self.data.signal_action[0]
        strength = self.data.signal_strength[0]

        if self.position:
            self.highest_since_entry = max(
                self.highest_since_entry, self.data.close[0])

            # Hard stop-loss
            if (self.entry_price and
                self.data.close[0] < self.entry_price * (1 - self.p.stop_loss_pct)):
                self.order = self.close()
                return

            # Trailing stop
            if self.data.close[0] < self.highest_since_entry * (1 - self.p.trail_stop_pct):
                self.order = self.close()
                return

            # Signal-based exit
            if action == -1:
                self.order = self.close()
                return
        else:
            # Signal-based entry
            if action == 1:
                cash = self.broker.getcash()
                pct = self.p.base_position_pct
                if self.p.use_strength_sizing:
                    pct *= max(0.3, strength)
                size = int(cash * pct / self.data.close[0])
                size = (size // 100) * 100  # A-share lot size
                if size >= 100:
                    self.order = self.buy(size=size)
```

---

## 3. ML Pipeline Architecture

### End-to-End Flow

```
  Raw OHLCV Data
       |
       v
  +------------------+
  | FeatureEngine    |  Compute technical indicators, momentum, volatility
  | (features/)      |  features using ONLY backward-looking operations.
  +------------------+
       |
       v
  Features DataFrame (N rows x F features + 1 target column)
       |
       v
  +------------------+
  | TimeSeriesCV     |  Split into train/val/test by DATE (never random).
  | (ml/cv.py)       |  Expanding window: train grows, val/test slide forward.
  +------------------+
       |
       v
  +------------------+
  | BaseModel.fit()  |  Train model on train set per fold.
  | (ml/models/)     |  Evaluate on validation set.
  +------------------+
       |
       v
  +------------------+
  | ModelRegistry    |  Save best model with metadata (dates, metrics, hash).
  | (ml/registry.py) |  Assign version ID.
  +------------------+
       |
       v
  +------------------+
  | ML Signal Gen    |  Load frozen model. Run inference on backtest-period data.
  | (strategy/ml/)   |  Convert predictions -> signal_action/strength/score.
  +------------------+
       |
       v
  Signal DataFrame (OHLCV + signal columns)
       |
       v
  +------------------+
  | BacktestEngine   |  Feed signals to backtrader via SignalPandasData.
  | (backtest/)      |  SignalFollower reads signals, executes trades.
  +------------------+
       |
       v
  Performance Report (Sharpe, MaxDD, Return, WinRate, TradeCount)
```

### Feature Engineering

**Technical Indicators** (`features/technical.py`):

| Feature | Description | Lookback |
|---------|-------------|----------|
| `rsi_14` | RSI(14) normalized to [0,1] | 14 bars |
| `macd_hist` | MACD histogram / close | 26 bars |
| `bb_position` | Position in Bollinger Bands [0,1] | 20 bars |
| `ma_ratio_5_20` | SMA(5) / SMA(20) - 1 | 20 bars |
| `ma_ratio_10_50` | SMA(10) / SMA(50) - 1 | 50 bars |
| `atr_ratio` | ATR(14) / close | 14 bars |
| `adx_14` | ADX(14) trend strength | 14 bars |
| `stoch_k` | Stochastic %K | 14 bars |

**Momentum Features** (`features/momentum.py`):

| Feature | Description | Lookback |
|---------|-------------|----------|
| `return_5d` | 5-day return | 5 bars |
| `return_20d` | 20-day return | 20 bars |
| `return_60d` | 60-day return | 60 bars |
| `vol_ratio_5_20` | Volume 5d MA / 20d MA | 20 bars |
| `vol_momentum` | Volume / Volume[5] | 5 bars |
| `close_position` | (Close - Low) / (High - Low) | 0 (same bar) |

**Volatility Features** (`features/volatility.py`):

| Feature | Description | Lookback |
|---------|-------------|----------|
| `realized_vol_20` | Annualized vol from 20d returns | 20 bars |
| `vol_regime` | Current vol / 60d avg vol | 60 bars |
| `vol_skew` | Skewness of 20d returns | 20 bars |

### Target Variable

```python
# src/qtrade/features/target.py

def compute_forward_return(df, horizon=5, threshold=0.02):
    """Binary classification target: will price rise >threshold in N days?

    target = 1  if  close[t+horizon] / close[t] - 1 > threshold
    target = 0  otherwise

    IMPORTANT: This uses FUTURE data. Only valid for training.
    """
    future_return = df["close"].shift(-horizon) / df["close"] - 1
    target = (future_return > threshold).astype(int)
    return target
```

### ML Pipeline Orchestrator

```python
# src/qtrade/ml/pipeline.py

class MLPipeline:
    """End-to-end ML pipeline: features -> train -> evaluate -> signal."""

    def run(self, df, backtest_start):
        # === ANTI-LOOKAHEAD CHECKPOINT #1 ===
        ml_data = df[df.index < pd.to_datetime(backtest_start)].copy()
        assert ml_data.index.max() < pd.to_datetime(backtest_start)

        # 1. Compute features + target on ML data only
        features_target = self.feature_engine.compute_features_and_target(ml_data)
        feature_cols = self.feature_engine.get_feature_columns()
        clean = features_target.dropna(subset=feature_cols + ["target"])
        X, y = clean[feature_cols], clean["target"]

        # 2. Time-series cross-validation
        cv_results = self.cv.evaluate(X, y, self._create_model())

        # 3. Train final model on ALL ML data
        model = self._create_model()
        model.fit(X, y)
        model.freeze()  # === ANTI-LOOKAHEAD CHECKPOINT #2 ===

        # 4. Register model
        model_id = self.registry.save(model=model, metadata={
            "train_start": str(ml_data.index[0].date()),
            "train_end": str(ml_data.index[-1].date()),
            "backtest_start": backtest_start,
            "cv_scores": cv_results,
            "feature_columns": feature_cols,
        })

        return {"model_id": model_id, "cv_results": cv_results}
```

---

## 4. Time-Series Cross-Validation

### Why Not Random Split

Standard `train_test_split(X, y, test_size=0.2)` randomly shuffles rows.
For time-series, this means the model trains on some future bars and tests
on some past bars. This is **lookahead bias** and produces unrealistically
good metrics.

### Expanding Window (Recommended Default)

The training set grows over time while the validation set slides forward:

```
Fold 1:  [=====TRAIN======][==VAL==]
Fold 2:  [========TRAIN========][==VAL==]
Fold 3:  [============TRAIN============][==VAL==]
Fold 4:  [================TRAIN================][==VAL==]
                                             ^
                                        backtest_start
                                        (model frozen here)
```

### Walk-Forward (Alternative)

Fixed-size training window that slides forward:

```
Fold 1:  [==TRAIN==][==VAL==]
Fold 2:     [==TRAIN==][==VAL==]
Fold 3:        [==TRAIN==][==VAL==]
```

Use walk-forward when data is non-stationary and older data may hurt.

### Implementation

```python
# src/qtrade/ml/cv.py

class TimeSeriesCV:
    """Time-series cross-validation.

    Guarantees:
      - Training data always ENDS before validation data STARTS
      - No shuffling, no random splits
      - Gap between train and val prevents target leakage
    """

    def __init__(self, config: dict):
        self.n_folds = config.get("n_folds", 5)
        self.method = config.get("method", "expanding")
        self.val_window = config.get("val_window", 63)   # ~3 months
        self.gap = config.get("gap", 5)                   # days gap

    def split(self, X, y):
        """Generate train/val index splits."""
        n = len(X)
        dates = X.index
        splits = []

        if self.method == "expanding":
            total_val = self.n_folds * self.val_window
            train_end_start = n - total_val - self.gap

            for i in range(self.n_folds):
                train_end = train_end_start + i * self.val_window
                val_start = train_end + self.gap
                val_end = min(val_start + self.val_window, n)

                train_idx = list(range(0, train_end))
                val_idx = list(range(val_start, val_end))
                splits.append((train_idx, val_idx))

        # === ANTI-LOOKAHEAD ASSERTION ===
        for train_idx, val_idx in splits:
            assert max(train_idx) < min(val_idx), \
                f"Train/val overlap! train_max={max(train_idx)}"
            assert dates[max(train_idx)] < dates[min(val_idx)], \
                f"Date overlap!"

        return splits

    def evaluate(self, X, y, model):
        """Run CV and return aggregated metrics."""
        splits = self.split(X, y)
        fold_metrics = []

        for fold_i, (train_idx, val_idx) in enumerate(splits):
            X_train, y_train = X.iloc[train_idx], y.iloc[train_idx]
            X_val, y_val = X.iloc[val_idx], y.iloc[val_idx]

            fold_model = model.clone()
            fold_model.fit(X_train, y_train)
            metrics = fold_model.evaluate(X_val, y_val)
            metrics["fold"] = fold_i + 1
            metrics["train_end"] = str(X.index[train_idx[-1]].date())
            metrics["val_start"] = str(X.index[val_idx[0]].date())
            fold_metrics.append(metrics)

        return {
            "folds": fold_metrics,
            "mean_accuracy": np.mean([m.get("accuracy", 0) for m in fold_metrics]),
            "std_accuracy": np.std([m.get("accuracy", 0) for m in fold_metrics]),
        }
```

---

## 5. Anti-Lookahead Design

Lookahead bias is the #1 killer of quantitative trading systems. This section
defines every defense layer.

### Threat Model

| Threat | Where | Severity | Defense |
|--------|-------|----------|---------|
| Future data in features | features/ | CRITICAL | shift(1) on all inputs |
| Training on test period | ml/pipeline | CRITICAL | Date-based split assertion |
| Model updated during backtest | ml/ | HIGH | Model freeze() + assertion |
| Random train/test split | ml/cv | HIGH | TimeSeriesCV only |
| Target leakage via gap | ml/cv | MEDIUM | gap parameter between train/val |
| Same-bar price leakage | features/ | MEDIUM | shift(1) convention |
| Signal computed on full data | strategy/ | LOW | Pre-computed signal columns |

### Layer 1: Feature Engineering (shift(1) Convention)

```python
# RULE: Every feature function must follow this pattern:

def compute_rsi(close, period=14):
    """RSI computed from PAST closes only.

    The shift(1) ensures that the signal generated at bar t
    uses data available BEFORE bar t's close.
    """
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - 100 / (1 + rs)
    return rsi.shift(1)  # CRITICAL: prevent same-bar leakage
```

**The shift(1) Convention**: All features are shifted by 1 bar. Feature values
at index `t` are computed from data available at `t-1` or earlier. When the
backtest is at bar `t`, the signal it reads was computed from data that existed
before bar `t` opened.

### Layer 2: Date-Based Data Splitting

```python
def _split_data(self, df, backtest_start):
    """Split data into ML and backtest portions by date."""
    cutoff = pd.to_datetime(backtest_start)
    ml_data = df[df.index < cutoff].copy()
    bt_data = df[df.index >= cutoff].copy()

    # === HARD ASSERTION ===
    if len(ml_data) > 0 and len(bt_data) > 0:
        assert ml_data.index.max() < bt_data.index.min(), (
            f"DATA LEAK: ML data ends at {ml_data.index.max()}, "
            f"backtest starts at {bt_data.index.min()}."
        )

    logger.info(f"[ANTI-LOOKAHEAD] ML data: {ml_data.index[0].date()} "
                f"to {ml_data.index[-1].date()}")
    logger.info(f"[ANTI-LOOKAHEAD] Backtest: {bt_data.index[0].date()} "
                f"to {bt_data.index[-1].date()}")
    return ml_data, bt_data
```

### Layer 3: Model Freeze

```python
class BaseModel(ABC):
    def __init__(self):
        self._frozen = False

    def freeze(self):
        """Freeze the model. After this, fit() raises RuntimeError."""
        self._frozen = True
        logger.info("[MODEL] Model frozen. No further training allowed.")

    def fit(self, X, y):
        if self._frozen:
            raise RuntimeError("Cannot train a frozen model!")
        self._fit(X, y)

    def predict(self, X):
        if not self._frozen:
            logger.warning("[MODEL] predict() called on unfrozen model. "
                          "OK for CV but NOT for backtesting.")
        return self._predict(X)
```

### Layer 4: Automated Anti-Lookahead Tests

```python
# tests/test_ml/test_anti_lookahead.py

class TestAntiLookahead:
    """These tests MUST PASS for the system to be trustworthy."""

    def test_feature_uses_only_past_data(self):
        """Features at t must not change when data at t+1 is perturbed."""
        df = make_sample_ohlcv(500)
        engine = FeatureEngine({"feature_groups": ["technical"]})
        features = engine.compute_features(df)

        # Perturb the LAST row
        df_perturbed = df.copy()
        df_perturbed.iloc[-1, df_perturbed.columns.get_loc("close")] *= 2.0
        features_perturbed = engine.compute_features(df_perturbed)

        # Features at index -2 should NOT change
        for col in engine.get_feature_columns():
            assert features[col].iloc[-2] == features_perturbed[col].iloc[-2], \
                f"Feature {col} at t-1 changed when t perturbed -> LOOKAHEAD!"

    def test_no_future_data_in_training(self):
        """Training data must end before backtest start."""
        df = make_sample_ohlcv(1000)
        backtest_start = str(df.index[700].date())
        pipeline = MLPipeline({"cv": {"n_folds": 3}})
        ml_data, bt_data = pipeline._split_data(df, backtest_start)
        assert ml_data.index.max() < bt_data.index.min()

    def test_cv_splits_are_temporal(self):
        """CV folds must not overlap in time."""
        cv = TimeSeriesCV({"n_folds": 5, "method": "expanding"})
        splits = cv.split(X, y)
        for train_idx, val_idx in splits:
            assert max(train_idx) < min(val_idx)

    def test_frozen_model_cannot_retrain(self):
        """Frozen model must reject fit() calls."""
        model = XGBoostModel({"n_estimators": 10})
        model.fit(X, y)
        model.freeze()
        with pytest.raises(RuntimeError):
            model.fit(X, y)

    def test_signal_consistency(self):
        """Signals on partial data must match signals on full data
        for the overlapping period (proves no future-data dependency)."""
        df_full = make_sample_ohlcv(500)
        df_partial = df_full.iloc[:400].copy()

        gen = DualMASignal({"fast_period": 5, "slow_period": 20})
        signals_full = gen.generate_signals(df_full)
        signals_partial = gen.generate_signals(df_partial)

        pd.testing.assert_series_equal(
            signals_full["signal_action"].iloc[:400],
            signals_partial["signal_action"].iloc[:400],
        )
```

### Layer 5: Runtime Logging

Every critical operation logs with `[ANTI-LOOKAHEAD]` prefix:

```
[ANTI-LOOKAHEAD] Feature warmup: 60 bars (max lookback = ma_ratio_10_50)
[ANTI-LOOKAHEAD] ML data: 2020-01-02 to 2023-12-29 (756 bars)
[ANTI-LOOKAHEAD] Backtest: 2024-01-02 to 2025-12-31 (504 bars)
[ANTI-LOOKAHEAD] Gap: 4 calendar days between ML end and backtest start
[ANTI-LOOKAHEAD] CV Fold 1: train=[2020-01-02..2022-06-30] val=[2022-07-08..2022-10-05]
[ANTI-LOOKAHEAD] Model frozen at 2023-12-29. Backtest starts 2024-01-02.
```

---

## 6. Config File Structure

### Full Config (configs/default.yaml)

```yaml
# ============================================================
#  qtrade default configuration
# ============================================================

# -- Data Source --
data:
  source: pytdx                  # pytdx | csv
  symbol: "300750"               # stock code
  start_date: "20200101"         # YYYYMMDD
  end_date: null                 # null = today
  cache:
    enabled: true
    dir: data/cache
    auto_refresh: false
  bar_type: daily

# -- Backtest Settings --
backtest:
  initial_capital: 100000.0
  commission: 0.0003             # per side
  min_commission: 5.0            # minimum yuan per trade
  slippage: 0.001                # 0.1% per trade
  stamp_duty: 0.001              # sell only
  lot_size: 100                  # A-share lot
  stop_loss_pct: 0.15
  trail_stop_pct: 0.10

# -- Strategy --
strategy:
  name: dual_ma
  type: rule                     # rule | ml
  params:
    fast_period: 5
    slow_period: 20

# -- ML Pipeline (only when strategy.type == "ml") --
ml:
  model_type: xgboost            # xgboost | lightgbm | lstm | sklearn
  model_params:
    xgboost:
      n_estimators: 200
      max_depth: 5
      learning_rate: 0.05
      subsample: 0.8
      colsample_bytree: 0.8
    lstm:
      hidden_size: 64
      num_layers: 2
      dropout: 0.3
      epochs: 50
      batch_size: 32
      sequence_length: 20

  train_start: "20200101"
  train_end: "20231231"          # MUST be before backtest start

  cv:
    method: expanding            # expanding | walk_forward
    n_folds: 5
    val_window: 63               # ~3 months per fold
    gap: 5                       # days between train end and val start

  features:
    groups: [technical, momentum, volatility]
    technical: [rsi_14, macd_hist, bb_position, ma_ratio_5_20, atr_ratio]
    momentum: [return_5d, return_20d, vol_ratio_5_20]
    volatility: [realized_vol_20, vol_regime]
    target:
      type: forward_return
      horizon: 5
      threshold: 0.02

  registry:
    dir: models
    auto_version: true
    keep_last_n: 10

# -- Stock Screening --
screening:
  enabled: false
  min_score: 40
  min_liquidity: 1000
  exclude_regimes: [bear]

# -- Logging --
logging:
  level: INFO
  file: logs/qtrade.log
  max_bytes: 10485760
  backup_count: 5
  log_trades: true
  log_signals: false

# -- Output --
output:
  save_results: true
  results_dir: results
  trade_log_csv: true
  plot: false
  plot_style: candlestick
```

### Config Loader

```python
# src/qtrade/config.py

DEFAULTS = {
    "data": {"source": "pytdx", "cache": {"enabled": True, "dir": "data/cache"}},
    "backtest": {
        "initial_capital": 100000, "commission": 0.0003,
        "slippage": 0.001, "lot_size": 100,
        "stop_loss_pct": 0.15, "trail_stop_pct": 0.10,
    },
    "strategy": {"type": "rule", "params": {}},
    "logging": {"level": "INFO"},
    "output": {"save_results": True, "plot": False},
}

def load_config(path):
    """Load YAML config with defaults and validation."""
    with open(path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    config = _deep_merge(DEFAULTS, user_config)
    _validate_config(config)
    return config

def _validate_config(config):
    """Validate critical config values."""
    if config["strategy"].get("type") == "ml":
        ml = config.get("ml", {})
        train_end = ml.get("train_end", "")
        bt_start = config["data"].get("start_date", "")
        if train_end and bt_start:
            assert train_end < bt_start, (
                f"ML train_end ({train_end}) must be before "
                f"data start_date ({bt_start})!"
            )
```

---

## 7. Key Class Hierarchies

### Complete Class Diagram

```
                        +----------------------+
                        |   SignalGenerator     | (ABC)
                        |----------------------|
                        | + generate_signals()  |
                        | + get_params()        |
                        | + validate()          |
                        +----------+-----------+
                                   |
                 +-----------------+-----------------+
                 |                                   |
    +------------+----------+           +------------+-----------+
    |  Rule-based Signals   |           |   ML Signals            |
    |-----------------------|           |-------------------------|
    |  DualMASignal         |           |  MLSignalGenerator      |
    |  RsiBBSignal          |           |  EnsembleSignal (future)|
    |  ATRAdaptiveSignal    |           +------------+-----------+
    |  MomentumSignal       |                        |
    +-----------------------+                        | uses
                                                     |
                                        +------------+-----------+
                                        |                        |
                           +------------+----+     +-------------+--------+
                           | FeatureEngine   |     | BaseModel (ABC)      |
                           |-----------------|     |----------------------|
                           |+ compute_       |     |+ fit(X, y)           |
                           |  features()     |     |+ predict(X)          |
                           |+ warmup_period  |     |+ save(path)          |
                           +-----------------+     |+ load(path)          |
                                                   |+ freeze()            |
                                                   |+ clone()             |
                                                   +----------+-----------+
                                                              |
                                                +-------------+-------------+
                                                |             |             |
                                   +------------+--+ +--------+-----+ +-----+---------+
                                   |SklearnModel | |XGBoostModel| | LSTMModel    |
                                   +-------------+ +------------+ +--------------+

    +----------------------+          +------------------------------+
    |  BacktestEngine      |          |   MLPipeline                  |
    |----------------------|          |------------------------------|
    | + run(config) ->     |          | + run(df, bt_start) -> dict  |
    |   BacktestResult     |          | + generate_signals(df, id)   |
    +----------+-----------+          +--------------+---------------+
               | uses                                | uses
    +----------+-----------+              +----------+----------+
    |                      |              |                     |
+---+---------------+ +----+------+ +-----+---------+ +--------+--------+
|SignalPandasData   | |SignalFol- | | TimeSeriesCV  | | ModelRegistry   |
|-------------------| |lower      | |---------------| |-----------------|
|(bt.feeds.         | |(bt.Strat- | |+ split(X,y)  | |+ save(model)    |
| PandasData)       | | egy)      | |+ evaluate()   | |+ load(id)      |
|+signal_action     | |+trade_log | |+ n_folds      | |+ list_models() |
|+signal_strength   | +-----------+ +---------------+ +----------------+
|+signal_score      |
+-------------------+

    +----------------------+
    |  PerformanceMetrics  |
    |----------------------|
    | + sharpe_ratio()     |
    | + max_drawdown()     |
    | + annual_return()    |
    | + win_rate()         |
    | + profit_factor()    |
    | + to_dict()          |
    +----------------------+
```

### Data Flow

```
  Config (YAML)
     |
     v
  DataFetcher -------> PytdxClient --> pytdx API
     |                      |
     |                  CSVCache (data/cache/*.csv)
     |
     v
  OHLCV DataFrame
     |
     |-- [Rule path] --> DualMASignal.generate_signals(df) --> df + signal cols
     |
     |-- [ML path] --> MLPipeline.run(df, backtest_start)
     |                   |
     |                   +-- FeatureEngine.compute_features_and_target(ml_data)
     |                   +-- TimeSeriesCV.split(X, y)
     |                   +-- XGBoostModel.fit(X_train, y_train)  [per fold]
     |                   +-- XGBoostModel.freeze()
     |                   +-- ModelRegistry.save(model)
     |                   +-- MLSignalGenerator.generate_signals(df) --> df + signal cols
     |
     v
  SignalPandasData (backtrader data feed with signal columns)
     |
     v
  SignalFollower (bt.Strategy) --> reads signal_action, executes trades
     |
     v
  BacktestResult
     |
     +-- PerformanceMetrics (Sharpe, MaxDD, Return, WinRate, TradeCount)
     +-- TradeLog (per-trade CSV)
     +-- Visualization (equity curve, drawdown chart)
```

### Key Interfaces

**DataFetcher**:
```python
class DataFetcher:
    def fetch(self, symbol, start, end=None) -> pd.DataFrame:
        """Fetch OHLCV data. Uses cache if available."""
    def fetch_index(self, symbol, start, end=None) -> pd.DataFrame:
        """Fetch index OHLCV data."""
```

**BacktestEngine**:
```python
@dataclass
class BacktestResult:
    metrics: dict           # sharpe, max_dd, total_return, annual_return, ...
    trade_log: pd.DataFrame # date, action, price, size, pnl, equity
    equity_curve: pd.Series # date -> portfolio value
    config: dict            # the config that produced this result

class BacktestEngine:
    def run(self, df, config) -> BacktestResult:
        """Run complete backtest. df must include signal columns."""
```

**ModelRegistry**:
```python
class ModelRegistry:
    def save(self, model, metadata) -> str:
        """Save model + metadata. Returns model_id."""
    def load(self, model_id) -> BaseModel:
        """Load model by ID."""
    def list_models(self) -> list[dict]:
        """List all registered models."""
    def get_best(self, metric="mean_accuracy") -> str:
        """Return model_id with best metric."""
```

---

## 8. Implementation Order

### Phase 1: Minimum Viable Loop

Build in this order. Each step produces a testable artifact.

```
Step 1: Project scaffold
|  +-- Create directory structure
|  +-- pyproject.toml with dependencies
|  +-- .gitignore
|  +-- configs/quick.yaml
|  +-- src/qtrade/__init__.py, __main__.py, constants.py

Step 2: Config system
|  +-- src/qtrade/config.py (YAML loader + validator)
|  +-- src/qtrade/logging_setup.py
|  +-- tests/test_config.py

Step 3: Data layer
|  +-- src/qtrade/data/pytdx_client.py (migrate from existing)
|  +-- src/qtrade/data/cache.py (CSV read/write)
|  +-- src/qtrade/data/fetcher.py (orchestrator)
|  +-- src/qtrade/data/schema.py (column validation)
|  +-- tests/test_data/test_fetcher.py, test_cache.py

Step 4: Signal interface + Dual MA
|  +-- src/qtrade/strategy/base.py (SignalGenerator ABC)
|  +-- src/qtrade/strategy/signals.py (column definitions)
|  +-- src/qtrade/strategy/registry.py (name -> class mapping)
|  +-- src/qtrade/strategy/rule/dual_ma.py (DualMASignal)
|  +-- tests/test_strategy/test_signals.py, test_dual_ma.py

Step 5: Backtest engine
|  +-- src/qtrade/backtest/data_feed.py (SignalPandasData)
|  +-- src/qtrade/backtest/signal_strategy.py (SignalFollower)
|  +-- src/qtrade/backtest/engine.py (BacktestEngine)
|  +-- src/qtrade/backtest/analyzers.py
|  +-- src/qtrade/backtest/trade_log.py
|  +-- src/qtrade/backtest/performance.py
|  +-- tests/test_backtest/test_engine.py, test_performance.py

Step 6: CLI + integration
|  +-- src/qtrade/cli.py
|  +-- src/qtrade/__main__.py
|  +-- tests/test_integration/test_full_pipeline.py

Step 7: Visualization
|  +-- src/qtrade/visualization/charts.py
|  +-- src/qtrade/visualization/comparison.py
```

**Milestone**: `python -m qtrade backtest --config configs/quick.yaml`
runs data fetch -> Dual MA signals -> backtrader -> prints Sharpe/MaxDD/Return.

### Phase 2: ML Strategy Integration

```
Step 8: Feature engineering
|  +-- src/qtrade/features/technical.py
|  +-- src/qtrade/features/momentum.py
|  +-- src/qtrade/features/volatility.py
|  +-- src/qtrade/features/target.py
|  +-- src/qtrade/features/engine.py
|  +-- tests/test_ml/test_features.py

Step 9: ML models
|  +-- src/qtrade/ml/models/base.py (BaseModel ABC)
|  +-- src/qtrade/ml/models/sklearn_model.py
|  +-- src/qtrade/ml/models/xgboost_model.py

Step 10: Time-series CV
|  +-- src/qtrade/ml/cv.py (TimeSeriesCV)
|  +-- src/qtrade/ml/evaluation.py

Step 11: ML pipeline + registry
|  +-- src/qtrade/ml/pipeline.py (MLPipeline)
|  +-- src/qtrade/ml/registry.py (ModelRegistry)

Step 12: ML signal generator
|  +-- src/qtrade/strategy/ml/ml_signal.py
|  +-- Integration with BacktestEngine

Step 13: Anti-lookahead validation suite
|  +-- tests/test_ml/test_anti_lookahead.py
|  +-- Runtime assertions in pipeline

Step 14: Deep learning (optional)
|  +-- src/qtrade/ml/models/lstm_model.py
|  +-- Sequence data preparation utilities
```

**Milestone**: `python -m qtrade train --config configs/ml_xgboost.yaml` trains model,
then `python -m qtrade backtest --config configs/ml_xgboost.yaml` runs ML backtest.

### Dependency Graph

```
config.py -----------+
                     |
logging_setup.py     |
                     |
data/pytdx_client    |
       |             |
data/cache ----------+
       |             |
data/fetcher --------+
                     |
strategy/base -------+
       |             |
strategy/dual_ma ----+
                     |
backtest/data_feed --+
       |             |
backtest/signal_ ----+
  strategy           |
       |             |
backtest/engine -----+
       |             |
backtest/perform. ---+
       |             |
cli.py --------------+    <-- Phase 1 complete

features/tech -------+
       |             |
features/engine -----+
       |             |
ml/models/base ------+
       |             |
ml/models/xgboost --+
       |             |
ml/cv --------------+
       |             |
ml/registry --------+
       |             |
ml/pipeline --------+
       |             |
strategy/ml/ -------+
  ml_signal          |
       |             |
test_anti_lookahead -+    <-- Phase 2 complete
```

### Key Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Signal interface | Pre-computed columns in DataFrame | Simple, no runtime coupling, works with backtrader |
| backtrader Strategy | Single SignalFollower class | Decouples signal logic from execution |
| ML integration | Before backtest (offline) | Prevents lookahead; model frozen; reproducible |
| Feature shift | shift(1) on all features | Prevents same-bar price leakage |
| CV method | Expanding window (default) | Mimics real-world retraining; maximizes training data |
| Config format | YAML | Human-readable, supports nesting |
| Package structure | src layout (src/qtrade/) | Standard Python packaging |
| Position sizing | Strength-scaled in SignalFollower | ML confidence influences position |
| Lot size | 100 shares (A-share) | Matches real A-share trading rules |
