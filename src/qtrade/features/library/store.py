"""Feature store for computed feature values."""
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
from loguru import logger


class FeatureStore:
    """Store and retrieve computed feature values."""

    def __init__(self, store_path: str = 'features/store'):
        self.store_path = Path(store_path)
        self.store_path.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}

    def _get_feature_path(self, symbol: str, feature_name: str) -> Path:
        """Get path for feature file."""
        return self.store_path / f"{symbol}_{feature_name}.parquet"

    def save(self, symbol: str, feature_name: str, values: pd.Series,
            metadata: Optional[Dict] = None) -> None:
        """Save computed feature values."""
        path = self._get_feature_path(symbol, feature_name)

        # Convert to DataFrame
        df = pd.DataFrame({feature_name: values})
        if metadata:
            df.attrs['metadata'] = metadata
        df.attrs['computed_at'] = datetime.now().isoformat()

        # Save
        df.to_parquet(path)
        self._cache[f"{symbol}:{feature_name}"] = df

        logger.debug(f"Saved feature '{feature_name}' for {symbol} ({len(values)} values)")

    def load(self, symbol: str, feature_name: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[pd.Series]:
        """Load computed feature values."""
        cache_key = f"{symbol}:{feature_name}"

        # Check cache
        if cache_key in self._cache:
            df = self._cache[cache_key]
        else:
            # Load from disk
            path = self._get_feature_path(symbol, feature_name)
            if not path.exists():
                return None

            df = pd.read_parquet(path)
            self._cache[cache_key] = df

        # Filter by date
        if start_date:
            df = df[df.index >= pd.to_datetime(start_date)]
        if end_date:
            df = df[df.index <= pd.to_datetime(end_date)]

        return df[feature_name]

    def exists(self, symbol: str, feature_name: str) -> bool:
        """Check if feature exists in store."""
        cache_key = f"{symbol}:{feature_name}"
        if cache_key in self._cache:
            return True
        return self._get_feature_path(symbol, feature_name).exists()

    def delete(self, symbol: str, feature_name: str) -> None:
        """Delete feature from store."""
        path = self._get_feature_path(symbol, feature_name)
        if path.exists():
            path.unlink()

        cache_key = f"{symbol}:{feature_name}"
        if cache_key in self._cache:
            del self._cache[cache_key]

        logger.debug(f"Deleted feature '{feature_name}' for {symbol}")

    def list_features(self, symbol: str) -> List[str]:
        """List all features for a symbol."""
        features = []
        for path in self.store_path.glob(f"{symbol}_*.parquet"):
            feature_name = path.stem.replace(f"{symbol}_", "")
            features.append(feature_name)
        return features

    def load_multiple(self, symbol: str, feature_names: List[str],
                     start_date: Optional[str] = None,
                     end_date: Optional[str] = None) -> pd.DataFrame:
        """Load multiple features into a DataFrame."""
        dfs = []
        for name in feature_names:
            series = self.load(symbol, name, start_date, end_date)
            if series is not None:
                dfs.append(series)

        if not dfs:
            return pd.DataFrame()

        return pd.concat(dfs, axis=1)

    def get_metadata(self, symbol: str, feature_name: str) -> Optional[Dict]:
        """Get metadata for a stored feature."""
        cache_key = f"{symbol}:{feature_name}"

        if cache_key in self._cache:
            df = self._cache[cache_key]
        else:
            path = self._get_feature_path(symbol, feature_name)
            if not path.exists():
                return None
            df = pd.read_parquet(path)

        return {
            'computed_at': df.attrs.get('computed_at'),
            'metadata': df.attrs.get('metadata'),
            'n_values': len(df),
            'date_range': (df.index.min(), df.index.max()),
        }

    def clear_cache(self):
        """Clear in-memory cache."""
        self._cache.clear()
        logger.debug("Cleared feature store cache")

    def summary(self, symbol: str) -> Dict:
        """Get summary of stored features."""
        features = self.list_features(symbol)
        total_size = 0
        for name in features:
            path = self._get_feature_path(symbol, name)
            total_size += path.stat().st_size

        return {
            'symbol': symbol,
            'n_features': len(features),
            'features': features,
            'total_size_mb': total_size / 1024 / 1024,
        }
