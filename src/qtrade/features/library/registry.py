"""Feature registry and metadata management."""
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import hashlib
import inspect
from loguru import logger


@dataclass
class FeatureMetadata:
    """Metadata for a feature."""
    name: str
    description: str
    category: str  # technical, momentum, volatility, etc.
    lookback_period: int
    dependencies: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    version: str = "1.0.0"
    tags: List[str] = field(default_factory=list)
    compute_func_hash: Optional[str] = None
    parameters: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'lookback_period': self.lookback_period,
            'dependencies': self.dependencies,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'version': self.version,
            'tags': self.tags,
            'compute_func_hash': self.compute_func_hash,
            'parameters': self.parameters,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'FeatureMetadata':
        return cls(**data)


class FeatureRegistry:
    """Registry for managing feature definitions and metadata."""

    def __init__(self, registry_path: str = 'features/registry.json'):
        self.registry_path = Path(registry_path)
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        self.features: Dict[str, FeatureMetadata] = {}
        self.compute_functions: Dict[str, Callable] = {}
        self._load()

    def _load(self):
        """Load registry from disk."""
        if self.registry_path.exists():
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for name, metadata in data.get('features', {}).items():
                    self.features[name] = FeatureMetadata.from_dict(metadata)
            logger.info(f"Loaded {len(self.features)} features from registry")

    def _save(self):
        """Save registry to disk."""
        data = {
            'features': {name: meta.to_dict() for name, meta in self.features.items()},
            'updated_at': datetime.now().isoformat(),
        }
        with open(self.registry_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.debug(f"Saved registry with {len(self.features)} features")

    def _hash_function(self, func: Callable) -> str:
        """Hash a function's source code for versioning."""
        source = inspect.getsource(func)
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def register(self, name: str, func: Callable, metadata: FeatureMetadata) -> Callable:
        """Register a feature computation function."""
        # Hash the function
        metadata.compute_func_hash = self._hash_function(func)

        # Check if feature exists and detect changes
        if name in self.features:
            existing = self.features[name]
            if existing.compute_func_hash != metadata.compute_func_hash:
                logger.warning(f"Feature '{name}' function changed, updating version")
                # Increment version
                major, minor, patch = map(int, existing.version.split('.'))
                metadata.version = f"{major}.{minor+1}.0"
            metadata.created_at = existing.created_at

        metadata.updated_at = datetime.now().isoformat()
        self.features[name] = metadata
        self.compute_functions[name] = func
        self._save()

        logger.info(f"Registered feature '{name}' v{metadata.version}")
        return func

    def get(self, name: str) -> Optional[FeatureMetadata]:
        """Get feature metadata."""
        return self.features.get(name)

    def get_function(self, name: str) -> Optional[Callable]:
        """Get feature computation function."""
        return self.compute_functions.get(name)

    def list_features(self, category: Optional[str] = None,
                     tag: Optional[str] = None) -> List[FeatureMetadata]:
        """List features with optional filtering."""
        features = list(self.features.values())

        if category:
            features = [f for f in features if f.category == category]
        if tag:
            features = [f for f in features if tag in f.tags]

        return features

    def get_dependencies(self, name: str) -> List[str]:
        """Get all dependencies for a feature (recursive)."""
        if name not in self.features:
            return []

        deps = set()
        stack = [name]

        while stack:
            current = stack.pop()
            if current in self.features:
                for dep in self.features[current].dependencies:
                    if dep not in deps:
                        deps.add(dep)
                        stack.append(dep)

        return list(deps)

    def get_dependents(self, name: str) -> List[str]:
        """Get all features that depend on this feature."""
        dependents = []
        for feat_name, metadata in self.features.items():
            if name in metadata.dependencies:
                dependents.append(feat_name)
        return dependents

    def validate_dependencies(self) -> Dict:
        """Validate all feature dependencies."""
        issues = []

        for name, metadata in self.features.items():
            for dep in metadata.dependencies:
                if dep not in self.features:
                    issues.append({
                        'feature': name,
                        'issue': 'missing_dependency',
                        'dependency': dep,
                    })

            # Check for circular dependencies
            all_deps = self.get_dependencies(name)
            if name in all_deps:
                issues.append({
                    'feature': name,
                    'issue': 'circular_dependency',
                    'chain': all_deps,
                })

        return {
            'valid': len(issues) == 0,
            'issues': issues,
        }

    def export_feature_set(self, output_path: str):
        """Export feature set definition for reproducibility."""
        feature_set = {
            'exported_at': datetime.now().isoformat(),
            'features': {},
        }

        for name, metadata in self.features.items():
            feature_set['features'][name] = {
                'metadata': metadata.to_dict(),
                'dependencies': self.get_dependencies(name),
            }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(feature_set, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported feature set to {output_path}")

    def import_feature_set(self, input_path: str):
        """Import feature set definition."""
        with open(input_path, 'r', encoding='utf-8') as f:
            feature_set = json.load(f)

        for name, data in feature_set.get('features', {}).items():
            metadata = FeatureMetadata.from_dict(data['metadata'])
            self.features[name] = metadata

        self._save()
        logger.info(f"Imported {len(feature_set.get('features', {}))} features from {input_path}")

    def summary(self) -> Dict:
        """Get registry summary."""
        categories = {}
        for meta in self.features.values():
            cat = meta.category
            categories[cat] = categories.get(cat, 0) + 1

        return {
            'total_features': len(self.features),
            'categories': categories,
            'registry_path': str(self.registry_path),
        }


def feature(name: str, category: str, lookback_period: int,
           description: str = "", dependencies: List[str] = None,
           tags: List[str] = None, parameters: Dict = None):
    """Decorator for registering features."""
    def decorator(func: Callable) -> Callable:
        metadata = FeatureMetadata(
            name=name,
            description=description or func.__doc__ or "",
            category=category,
            lookback_period=lookback_period,
            dependencies=dependencies or [],
            tags=tags or [],
            parameters=parameters or {},
        )

        # Store metadata on function
        func._feature_metadata = metadata
        func._feature_name = name

        return func

    return decorator
