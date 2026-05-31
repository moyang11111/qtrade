"""Model registry — save/load trained models with metadata."""

import json
import logging
from datetime import datetime
from pathlib import Path

from qtrade.ml.models.base import BaseModel

logger = logging.getLogger("qtrade.ml.registry")


class ModelRegistry:
    """Persist trained models with versioned metadata."""

    def __init__(self, registry_dir: str = "models"):
        self.dir = Path(registry_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.dir / "registry.json"
        self._index = self._load_index()

    def _load_index(self) -> list[dict]:
        if self._index_path.exists():
            with open(self._index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _save_index(self):
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2, default=str)

    def save(self, model: BaseModel, metadata: dict) -> str:
        """Save model + metadata. Returns model_id."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_id = f"model_{ts}"
        model_path = self.dir / f"{model_id}.pkl"
        model.save(model_path)

        entry = {
            "model_id": model_id,
            "path": str(model_path),
            "saved_at": ts,
            "metadata": metadata,
        }
        self._index.append(entry)
        self._save_index()
        logger.info("[REGISTRY] Saved %s -> %s", model_id, model_path)
        return model_id

    def load(self, model_id: str, model: BaseModel) -> BaseModel:
        """Load model by ID."""
        for entry in self._index:
            if entry["model_id"] == model_id:
                model.load(Path(entry["path"]))
                logger.info("[REGISTRY] Loaded %s", model_id)
                return model
        raise KeyError(f"Model not found: {model_id}")

    def list_models(self) -> list[dict]:
        return list(self._index)
