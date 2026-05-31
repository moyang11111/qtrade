"""Feature version management."""
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
from loguru import logger


@dataclass
class FeatureVersion:
    """Version information for a feature."""
    version: str
    created_at: str
    compute_hash: str
    parameters: Dict
    changelog: str = ""

    def to_dict(self) -> Dict:
        return {
            'version': self.version,
            'created_at': self.created_at,
            'compute_hash': self.compute_hash,
            'parameters': self.parameters,
            'changelog': self.changelog,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'FeatureVersion':
        return cls(**data)


class FeatureVersionManager:
    """Manage feature versions and track changes."""

    def __init__(self, version_path: str = 'features/versions'):
        self.version_path = Path(version_path)
        self.version_path.mkdir(parents=True, exist_ok=True)
        self.versions: Dict[str, List[FeatureVersion]] = {}
        self._load()

    def _load(self):
        """Load version history from disk."""
        version_file = self.version_path / 'version_history.json'
        if version_file.exists():
            with open(version_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for name, versions in data.items():
                    self.versions[name] = [FeatureVersion.from_dict(v) for v in versions]

    def _save(self):
        """Save version history to disk."""
        version_file = self.version_path / 'version_history.json'
        data = {name: [v.to_dict() for v in versions]
                for name, versions in self.versions.items()}
        with open(version_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def add_version(self, feature_name: str, version: str,
                   compute_hash: str, parameters: Dict,
                   changelog: str = "") -> FeatureVersion:
        """Add a new version for a feature."""
        if feature_name not in self.versions:
            self.versions[feature_name] = []

        feat_version = FeatureVersion(
            version=version,
            created_at=datetime.now().isoformat(),
            compute_hash=compute_hash,
            parameters=parameters,
            changelog=changelog,
        )

        self.versions[feature_name].append(feat_version)
        self._save()

        logger.info(f"Added version {version} for feature '{feature_name}'")
        return feat_version

    def get_version(self, feature_name: str, version: str) -> Optional[FeatureVersion]:
        """Get specific version of a feature."""
        if feature_name not in self.versions:
            return None

        for v in self.versions[feature_name]:
            if v.version == version:
                return v
        return None

    def get_latest_version(self, feature_name: str) -> Optional[FeatureVersion]:
        """Get latest version of a feature."""
        if feature_name not in self.versions:
            return None
        return self.versions[feature_name][-1] if self.versions[feature_name] else None

    def list_versions(self, feature_name: str) -> List[FeatureVersion]:
        """List all versions of a feature."""
        return self.versions.get(feature_name, [])

    def compare_versions(self, feature_name: str,
                        version1: str, version2: str) -> Dict:
        """Compare two versions of a feature."""
        v1 = self.get_version(feature_name, version1)
        v2 = self.get_version(feature_name, version2)

        if not v1 or not v2:
            return {'error': 'One or both versions not found'}

        # Compare parameters
        param_diff = {}
        all_keys = set(v1.parameters.keys()) | set(v2.parameters.keys())
        for key in all_keys:
            val1 = v1.parameters.get(key)
            val2 = v2.parameters.get(key)
            if val1 != val2:
                param_diff[key] = {'old': val1, 'new': val2}

        return {
            'version1': v1.to_dict(),
            'version2': v2.to_dict(),
            'compute_hash_changed': v1.compute_hash != v2.compute_hash,
            'parameter_changes': param_diff,
        }

    def rollback(self, feature_name: str, version: str) -> Optional[FeatureVersion]:
        """Rollback to a specific version (creates new version with old parameters)."""
        target = self.get_version(feature_name, version)
        if not target:
            logger.error(f"Version {version} not found for feature '{feature_name}'")
            return None

        # Create new version with old parameters
        latest = self.get_latest_version(feature_name)
        if latest:
            # Increment version
            major, minor, patch = map(int, latest.version.split('.'))
            new_version = f"{major}.{minor+1}.0"
        else:
            new_version = "1.0.0"

        return self.add_version(
            feature_name=feature_name,
            version=new_version,
            compute_hash=target.compute_hash,
            parameters=target.parameters,
            changelog=f"Rolled back to version {version}",
        )

    def export_version(self, feature_name: str, version: str,
                      output_path: str) -> None:
        """Export specific version definition."""
        feat_version = self.get_version(feature_name, version)
        if not feat_version:
            raise ValueError(f"Version {version} not found for feature '{feature_name}'")

        export_data = {
            'feature_name': feature_name,
            'version': feat_version.to_dict(),
            'exported_at': datetime.now().isoformat(),
        }

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Exported version {version} of '{feature_name}' to {output_path}")

    def summary(self) -> Dict:
        """Get version management summary."""
        total_versions = sum(len(versions) for versions in self.versions.values())
        features_with_versions = len(self.versions)

        return {
            'total_features': features_with_versions,
            'total_versions': total_versions,
            'avg_versions_per_feature': total_versions / features_with_versions if features_with_versions > 0 else 0,
        }
