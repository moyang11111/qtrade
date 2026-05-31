"""MLPipeline — end-to-end: features → train → evaluate → freeze."""

import logging

import pandas as pd

from qtrade.features.engine import FeatureEngine
from qtrade.ml.cv import TimeSeriesCV
from qtrade.ml.registry import ModelRegistry
from qtrade.ml.models.base import BaseModel

logger = logging.getLogger("qtrade.ml.pipeline")


def _create_model(cfg: dict) -> BaseModel:
    model_type = cfg.get("model_type", "xgboost")
    model_params = cfg.get("model_params", {}).get(model_type, {})
    if model_type == "xgboost":
        from qtrade.ml.models.xgboost_model import XGBoostModel
        return XGBoostModel(model_params)
    elif model_type == "lstm":
        from qtrade.ml.models.lstm_model import LSTMModel
        return LSTMModel(model_params)
    else:
        from qtrade.ml.models.sklearn_model import SklearnModel
        model_params["model_type"] = model_type
        return SklearnModel(model_params)


class MLPipeline:
    """End-to-end ML pipeline with anti-lookahead guarantees."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.ml_cfg = cfg.get("ml", {})
        self.feature_engine = FeatureEngine(self.ml_cfg.get("features", {}))
        self.cv = TimeSeriesCV(self.ml_cfg.get("cv", {}))
        self.registry = ModelRegistry(self.ml_cfg.get("registry", {}).get("dir", "models"))

    def run(self, df: pd.DataFrame, backtest_start: str) -> dict:
        """Train model on data before backtest_start, then freeze.

        Returns:
            dict with model_id and cv_results.
        """
        # === ANTI-LOOKAHEAD: split by date ===
        cutoff = pd.to_datetime(backtest_start)
        ml_data = df[df.index < cutoff].copy()
        assert ml_data.index.max() < cutoff, \
            f"DATA LEAK: ML data ends {ml_data.index.max()}, cutoff {cutoff}"
        logger.info("[ANTI-LOOKAHEAD] ML data: %s to %s (%d bars)",
                     ml_data.index[0].date(), ml_data.index[-1].date(), len(ml_data))

        # 1. Features + target
        feat_target = self.feature_engine.compute_features_and_target(
            ml_data,
            horizon=self.ml_cfg.get("features", {}).get("target", {}).get("horizon", 5),
            threshold=self.ml_cfg.get("features", {}).get("target", {}).get("threshold", 0.02),
        )
        feature_cols = self.feature_engine.get_feature_columns()
        clean = feat_target.dropna(subset=feature_cols + ["target"])
        X, y = clean[feature_cols], clean["target"]
        logger.info("Features: %d, Samples: %d (after warmup)", len(feature_cols), len(X))

        # 2. Cross-validation
        model = _create_model(self.ml_cfg)
        logger.info("Running %d-fold %s CV...", self.cv.n_folds, self.cv.method)
        cv_results = self.cv.evaluate(X, y, model)
        logger.info("CV accuracy: %.3f ± %.3f",
                     cv_results["mean_accuracy"], cv_results["std_accuracy"])

        # 3. Train final model on ALL ML data
        final_model = _create_model(self.ml_cfg)
        final_model.fit(X, y)
        final_model.freeze()  # === ANTI-LOOKAHEAD: freeze ===

        # 4. Register
        model_id = self.registry.save(final_model, metadata={
            "train_start": str(ml_data.index[0].date()),
            "train_end": str(ml_data.index[-1].date()),
            "backtest_start": backtest_start,
            "cv_results": cv_results,
            "feature_columns": feature_cols,
        })

        return {"model_id": model_id, "cv_results": cv_results}
