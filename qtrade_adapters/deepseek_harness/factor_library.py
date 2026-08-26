"""QTrade-owned factor screening and library storage.

The adapter reads only explicit, business-dated factor artifacts from the
optional DeepSeek HARNESS data directory.  Saved plans contain screened
metadata, never raw third-party payloads or executable expressions.  See
``THIRD_PARTY_NOTICES.md`` for the upstream attribution and boundary.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from typing import Any


SCHEMA_VERSION = 1
MAX_LIBRARY_ITEMS = 100
MAX_MATCHED_FACTORS = 200
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500
MAX_KEYWORD_LENGTH = 120
MAX_BODY_BYTES = 64 * 1024

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_TOKEN_RE = re.compile(r"^factor-v1-\d{4}-\d{2}-\d{2}-[0-9a-f]{24}$")
_ARTIFACT_DIR = Path("data") / "factorpool" / "output"
_SUPPORTED_CONDITIONS = (
    "status",
    "usage",
    "lifecycle",
    "icir120_min",
    "icir120_max",
    "crowding_max",
    "keyword",
)
_ENUM_CONDITIONS = frozenset({"status", "usage", "lifecycle"})
_NUMERIC_CONDITIONS = frozenset({"icir120_min", "icir120_max", "crowding_max"})
_ITEM_KEYS = frozenset({
    "id",
    "name",
    "description",
    "conditions",
    "matched_factors",
    "as_of",
    "source_token",
    "created_at",
    "updated_at",
    "match_count",
})
_STORE_LOCKS: dict[str, threading.RLock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


class FactorLibraryError(Exception):
    """Base error with a stable public error code and HTTP status."""

    status_code = 503
    code = "factor_library_unavailable"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)
        self.public_message = message or self.code


class FactorValidationError(FactorLibraryError):
    status_code = 422
    code = "invalid_factor_library_request"


class FactorStorageError(FactorLibraryError):
    status_code = 503
    code = "factor_library_storage_unavailable"


class FactorDataError(FactorLibraryError):
    status_code = 503
    code = "factor_data_unavailable"


@dataclass(frozen=True)
class FactorSnapshot:
    records: tuple[dict[str, Any], ...]
    as_of: str
    source_token: str


def resolve_factor_library_path(
    explicit: str | Path | None = None,
    *,
    env: dict[str, str] | None = None,
    user_data_dir: str | Path | None = None,
) -> Path:
    """Resolve CLI, environment, then user-data/default storage locations."""

    configured = str(explicit).strip() if explicit is not None else ""
    if configured:
        return Path(configured).expanduser().resolve()
    values = os.environ if env is None else env
    configured = str(values.get("QTRADE_FACTOR_LIBRARY_FILE", "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if user_data_dir:
        return (Path(user_data_dir).expanduser() / "factor_library.json").resolve()
    return (Path.home() / ".qtrade" / "factor_library.json").resolve()


def normalize_conditions(raw: Any) -> dict[str, Any]:
    """Validate and canonicalize the deliberately small condition language."""

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise FactorValidationError("conditions must be an object")
    unknown = sorted(set(raw) - set(_SUPPORTED_CONDITIONS))
    if unknown:
        raise FactorValidationError(f"unsupported condition: {unknown[0]}")

    normalized: dict[str, Any] = {}
    for key in _SUPPORTED_CONDITIONS:
        if key not in raw:
            continue
        value = raw[key]
        if key in _ENUM_CONDITIONS:
            values = value if isinstance(value, list) else [value]
            if not values or len(values) > 20:
                raise FactorValidationError(f"{key} must contain 1 to 20 values")
            if any(not isinstance(item, str) or not item.strip() for item in values):
                raise FactorValidationError(f"{key} values must be non-empty strings")
            normalized[key] = sorted({item.strip() for item in values})
        elif key in _NUMERIC_CONDITIONS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FactorValidationError(f"{key} must be numeric")
            number = float(value)
            if not math.isfinite(number) or abs(number) > 10000:
                raise FactorValidationError(f"{key} is outside the supported range")
            normalized[key] = number
        elif key == "keyword":
            if not isinstance(value, str):
                raise FactorValidationError("keyword must be a string")
            keyword = value.strip()
            if len(keyword) > MAX_KEYWORD_LENGTH or any(ord(ch) < 32 for ch in keyword):
                raise FactorValidationError("keyword is too long or contains control characters")
            normalized[key] = keyword
    if (
        "icir120_min" in normalized
        and "icir120_max" in normalized
        and normalized["icir120_min"] > normalized["icir120_max"]
    ):
        raise FactorValidationError("icir120_min cannot exceed icir120_max")
    return normalized


def _business_date(value: Any) -> str | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value[:10]):
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise FactorDataError("factor artifact is missing or corrupt") from error
    if not isinstance(value, dict):
        raise FactorDataError("factor artifact schema is unsupported")
    return value


def _candidate_files(directory: Path, pattern: str) -> list[Path]:
    try:
        return sorted((path for path in directory.glob(pattern) if path.is_file()), key=lambda p: p.name)
    except OSError as error:
        raise FactorDataError("factor artifact directory is unavailable") from error


def _select_json_for_date(
    paths: list[Path],
    target: str,
    *,
    date_keys: tuple[str, ...] = ("date", "as_of", "updated"),
) -> tuple[Path, dict[str, Any]] | None:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        value = _read_json(path)
        found = next((_business_date(value.get(key)) for key in date_keys if value.get(key)), None)
        if found == target:
            matches.append((path, value))
    return matches[-1] if matches else None


def _read_health(path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
    except (OSError, UnicodeError, csv.Error) as error:
        raise FactorDataError("factor health artifact is missing or corrupt") from error
    if not rows:
        raise FactorDataError("factor health artifact is empty")
    dates = {_business_date(row.get("test_date")) for row in rows}
    if None in dates or len(dates) != 1:
        raise FactorDataError("factor health artifact date is invalid")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.get("factor", "").strip()
        if not name or name in result:
            raise FactorDataError("factor health artifact schema is unsupported")
        values: dict[str, Any] = {}
        for key in ("icir120", "crowding"):
            raw = row.get(key, "")
            if raw == "":
                values[key] = None
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError) as error:
                raise FactorDataError("factor health metric is invalid") from error
            if not math.isfinite(number):
                raise FactorDataError("factor health metric is invalid")
            values[key] = number
        result[name] = values
    return next(iter(dates - {None})), result


def _read_usage(path: Path, target: str) -> dict[str, list[str]]:
    value = _read_json(path)
    if _business_date(value.get("date")) != target or not isinstance(value.get("layers"), dict):
        raise FactorDataError("factor usage artifact date or schema is invalid")
    result: dict[str, list[str]] = {}
    for layer, factors in value["layers"].items():
        if not isinstance(layer, str) or not isinstance(factors, dict):
            raise FactorDataError("factor usage artifact schema is unsupported")
        for name, enabled in factors.items():
            if not isinstance(name, str) or not isinstance(enabled, bool):
                raise FactorDataError("factor usage artifact schema is unsupported")
            if enabled:
                result.setdefault(name, []).append(layer)
    return {name: sorted(layers) for name, layers in result.items()}


def _read_lifecycle(path: Path, target: str) -> dict[str, str]:
    value = _read_json(path)
    if _business_date(value.get("date")) != target or not isinstance(value.get("lifecycle"), dict):
        raise FactorDataError("factor lifecycle artifact date or schema is invalid")
    result = {}
    for name, phase in value["lifecycle"].items():
        if not isinstance(name, str) or not isinstance(phase, str) or not phase.strip():
            raise FactorDataError("factor lifecycle artifact schema is unsupported")
        result[name] = phase.strip()
    return result


def _artifact_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        try:
            digest.update(path.read_bytes())
        except (OSError, UnicodeError) as error:
            raise FactorDataError("factor artifact cannot be read") from error
    return digest.hexdigest()


def load_factor_snapshot(base_dir: str | Path) -> FactorSnapshot:
    """Load one business-date cohort without mixing historical artifacts."""

    root = Path(base_dir)
    output = root / _ARTIFACT_DIR
    manifests = _candidate_files(output, "factor_manifest_*.json")
    if not manifests:
        raise FactorDataError("factor manifest is unavailable")
    manifest_options: list[tuple[str, Path, dict[str, Any]]] = []
    for path in manifests:
        value = _read_json(path)
        as_of = _business_date(value.get("date"))
        if as_of is not None:
            manifest_options.append((as_of, path, value))
    if not manifest_options:
        raise FactorDataError("factor manifest date is unavailable")
    as_of, manifest_path, manifest = max(manifest_options, key=lambda item: (item[0], item[1].name))
    factors = manifest.get("factors")
    if not isinstance(factors, list) or not factors:
        raise FactorDataError("factor manifest schema is unsupported")

    freshness_match = _select_json_for_date(
        _candidate_files(output, "factor_data_freshness_*.json"),
        as_of,
    )
    if freshness_match is None:
        raise FactorDataError("factor freshness artifact is missing or stale")
    freshness_path, freshness = freshness_match
    if freshness.get("date") is None and freshness.get("updated") is None:
        raise FactorDataError("factor freshness artifact schema is unsupported")

    artifact_paths = [manifest_path, freshness_path]
    health: dict[str, dict[str, Any]] = {}
    health_paths = _candidate_files(output / "health", "health_*.csv")
    if not health_paths:
        health_paths = _candidate_files(output, "health_*.csv")
    health_found = False
    for path in reversed(health_paths):
        health_date, health_values = _read_health(path)
        if health_date == as_of:
            health = health_values
            artifact_paths.append(path)
            health_found = True
            break
    if health_paths and not health_found:
        raise FactorDataError("factor health artifact is stale")

    usage: dict[str, list[str]] = {}
    usage_paths = _candidate_files(output, "factor_usage_*.json")
    usage_match = _select_json_for_date(usage_paths, as_of)
    if usage_match is not None:
        usage_path, _ = usage_match
        usage = _read_usage(usage_path, as_of)
        artifact_paths.append(usage_path)
    elif usage_paths:
        raise FactorDataError("factor usage artifact is stale")

    lifecycle: dict[str, str] = {}
    lifecycle_paths = _candidate_files(output, "factor_lifecycle_*.json")
    lifecycle_match = _select_json_for_date(
        lifecycle_paths, as_of,
    )
    if lifecycle_match is not None:
        lifecycle_path, _ = lifecycle_match
        lifecycle = _read_lifecycle(lifecycle_path, as_of)
        artifact_paths.append(lifecycle_path)
    elif lifecycle_paths:
        raise FactorDataError("factor lifecycle artifact is stale")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in factors:
        if not isinstance(item, dict):
            raise FactorDataError("factor manifest schema is unsupported")
        name = item.get("factor")
        label = item.get("cn")
        eligible = item.get("eligible")
        if (
            not isinstance(name, str)
            or not name.strip()
            or name in seen
            or not isinstance(label, str)
            or not isinstance(eligible, bool)
        ):
            raise FactorDataError("factor manifest schema is unsupported")
        seen.add(name)
        metrics = health.get(name, {})
        records.append({
            "name": name,
            "label": label.strip() or None,
            "status": "eligible" if eligible else "ineligible",
            "usage": usage.get(name, []),
            "lifecycle": lifecycle.get(name),
            "icir120": metrics.get("icir120"),
            "crowding": metrics.get("crowding"),
            "as_of": as_of,
        })
    records.sort(key=lambda item: item["name"])
    digest = _artifact_digest(artifact_paths)[:24]
    return FactorSnapshot(tuple(records), as_of, f"factor-v1-{as_of}-{digest}")


def load_factor_records(base_dir: str | Path) -> list[dict[str, Any]]:
    """Public read-only convenience API for the normalized factor records."""

    return [dict(record) for record in load_factor_snapshot(base_dir).records]


def _matches(record: dict[str, Any], conditions: dict[str, Any]) -> bool:
    for key, expected in conditions.items():
        if key == "status" and record.get("status") not in expected:
            return False
        if key == "usage" and not set(expected).intersection(record.get("usage") or []):
            return False
        if key == "lifecycle" and record.get("lifecycle") not in expected:
            return False
        if key == "icir120_min":
            if record.get("icir120") is None or record["icir120"] < expected:
                return False
        if key == "icir120_max":
            if record.get("icir120") is None or record["icir120"] > expected:
                return False
        if key == "crowding_max":
            if record.get("crowding") is None or record["crowding"] > expected:
                return False
        if key == "keyword":
            haystack = f"{record.get('name', '')} {record.get('label') or ''}".casefold()
            if expected.casefold() not in haystack:
                return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, field: str, maximum: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise FactorValidationError(f"{field} must be a string")
    value = value.strip()
    if required and not value:
        raise FactorValidationError(f"{field} is required")
    if len(value) > maximum or any(ord(ch) < 32 for ch in value):
        raise FactorValidationError(f"{field} is too long or contains control characters")
    return value


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _STORE_LOCKS_GUARD:
        return _STORE_LOCKS.setdefault(key, threading.RLock())


def _validate_stored_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
        raise FactorStorageError("factor library storage is corrupt")
    identifier = item.get("id")
    if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
        raise FactorStorageError("factor library storage is corrupt")
    try:
        result = {
            "id": identifier,
            "name": _safe_text(item.get("name"), "name", MAX_NAME_LENGTH, required=True),
            "description": _safe_text(item.get("description"), "description", MAX_DESCRIPTION_LENGTH),
            "conditions": normalize_conditions(item.get("conditions")),
            "matched_factors": item.get("matched_factors"),
            "as_of": item.get("as_of"),
            "source_token": item.get("source_token"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "match_count": item.get("match_count"),
        }
    except FactorValidationError as error:
        raise FactorStorageError("factor library storage is corrupt") from error
    if (
        not isinstance(result["matched_factors"], list)
        or len(result["matched_factors"]) > MAX_MATCHED_FACTORS
        or any(not isinstance(value, str) for value in result["matched_factors"])
        or result["matched_factors"] != sorted(set(result["matched_factors"]))
        or not isinstance(result["as_of"], str)
        or _business_date(result["as_of"]) != result["as_of"]
        or not isinstance(result["source_token"], str)
        or not _TOKEN_RE.fullmatch(result["source_token"])
        or not isinstance(result["created_at"], str)
        or not isinstance(result["updated_at"], str)
        or not isinstance(result["match_count"], int)
        or isinstance(result["match_count"], bool)
        or result["match_count"] < 0
    ):
        raise FactorStorageError("factor library storage is corrupt")
    return result


class FactorLibrary:
    """Screen current factor artifacts and persist server-owned plan metadata."""

    def __init__(self, store_path: str | Path, data_dir: str | Path):
        self.store_path = Path(store_path).expanduser().resolve()
        self.data_dir = Path(data_dir).expanduser().resolve()
        try:
            self.store_path.relative_to(self.data_dir)
        except ValueError:
            pass
        else:
            raise ValueError("factor library storage must be outside the factor data directory")
        self._lock = _lock_for(self.store_path)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.store_path.exists():
            return []
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise FactorStorageError("factor library storage is corrupt") from error
        if (
            not isinstance(payload, dict)
            or set(payload) != {"schema_version", "items"}
            or payload.get("schema_version") != SCHEMA_VERSION
            or not isinstance(payload.get("items"), list)
            or len(payload["items"]) > MAX_LIBRARY_ITEMS
        ):
            raise FactorStorageError("factor library storage schema is unsupported")
        items = [_validate_stored_item(item) for item in payload["items"]]
        if len({item["id"] for item in items}) != len(items):
            raise FactorStorageError("factor library storage contains duplicate ids")
        return items

    def _write_unlocked(self, items: list[dict[str, Any]]) -> None:
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(
                prefix=f".{self.store_path.name}.", suffix=".tmp", dir=self.store_path.parent,
            )
            os.close(fd)
            with open(temporary, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"schema_version": SCHEMA_VERSION, "items": items}, stream,
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.store_path)
            temporary = None
        except OSError as error:
            raise FactorStorageError("factor library storage cannot be written") from error
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _snapshot(self) -> FactorSnapshot:
        try:
            return load_factor_snapshot(self.data_dir)
        except FactorLibraryError:
            raise
        except (OSError, ValueError) as error:
            raise FactorDataError("factor data is unavailable") from error

    @staticmethod
    def _screen(snapshot: FactorSnapshot, conditions: dict[str, Any]) -> list[str]:
        matched = [record["name"] for record in snapshot.records if _matches(record, conditions)]
        return sorted(set(matched))

    def preview(self, conditions: Any) -> dict[str, Any]:
        normalized = normalize_conditions(conditions)
        snapshot = self._snapshot()
        names = self._screen(snapshot, normalized)
        records = [dict(record) for record in snapshot.records if record["name"] in names]
        return {
            "schema_version": SCHEMA_VERSION,
            "as_of": snapshot.as_of,
            "source_token": snapshot.source_token,
            "matched_factors": names,
            "match_count": len(names),
            "factors": records,
            "conditions": normalized,
        }

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._read_unlocked()]

    def get(self, identifier: str) -> dict[str, Any] | None:
        if not isinstance(identifier, str) or not _ID_RE.fullmatch(identifier):
            return None
        with self._lock:
            return next((dict(item) for item in self._read_unlocked() if item["id"] == identifier), None)

    def create(self, name: Any, description: Any, conditions: Any) -> dict[str, Any]:
        name = _safe_text(name, "name", MAX_NAME_LENGTH, required=True)
        description = _safe_text(description, "description", MAX_DESCRIPTION_LENGTH)
        normalized = normalize_conditions(conditions)
        snapshot = self._snapshot()
        matched = self._screen(snapshot, normalized)
        now = _now()
        item = {
            "id": secrets.token_urlsafe(18),
            "name": name,
            "description": description,
            "conditions": normalized,
            "matched_factors": matched,
            "as_of": snapshot.as_of,
            "source_token": snapshot.source_token,
            "created_at": now,
            "updated_at": now,
            "match_count": len(matched),
        }
        _validate_stored_item(item)
        with self._lock:
            items = self._read_unlocked()
            if len(items) >= MAX_LIBRARY_ITEMS:
                raise FactorValidationError("factor library item limit reached")
            items.append(item)
            self._write_unlocked(items)
        return dict(item)

    def update(
        self,
        identifier: str,
        *,
        name: Any = None,
        description: Any = None,
        conditions: Any = None,
        update_conditions: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            items = self._read_unlocked()
            item = next((item for item in items if item["id"] == identifier), None)
            if item is None:
                return None
            if name is not None:
                item["name"] = _safe_text(name, "name", MAX_NAME_LENGTH, required=True)
            if description is not None:
                item["description"] = _safe_text(description, "description", MAX_DESCRIPTION_LENGTH)
            if update_conditions:
                normalized = normalize_conditions(conditions)
                snapshot = self._snapshot()
                item["conditions"] = normalized
                item["matched_factors"] = self._screen(snapshot, normalized)
                item["as_of"] = snapshot.as_of
                item["source_token"] = snapshot.source_token
                item["match_count"] = len(item["matched_factors"])
            item["updated_at"] = _now()
            _validate_stored_item(item)
            self._write_unlocked(items)
            return dict(item)

    def refresh(self, identifier: str) -> dict[str, Any] | None:
        with self._lock:
            items = self._read_unlocked()
            item = next((item for item in items if item["id"] == identifier), None)
            if item is None:
                return None
            snapshot = self._snapshot()
            item["matched_factors"] = self._screen(snapshot, item["conditions"])
            item["as_of"] = snapshot.as_of
            item["source_token"] = snapshot.source_token
            item["match_count"] = len(item["matched_factors"])
            item["updated_at"] = _now()
            _validate_stored_item(item)
            self._write_unlocked(items)
            return dict(item)

    def delete(self, identifier: str) -> bool:
        with self._lock:
            items = self._read_unlocked()
            remaining = [item for item in items if item["id"] != identifier]
            if len(remaining) == len(items):
                return False
            self._write_unlocked(remaining)
            return True


__all__ = [
    "FactorDataError",
    "FactorLibrary",
    "FactorLibraryError",
    "FactorSnapshot",
    "FactorStorageError",
    "FactorValidationError",
    "MAX_BODY_BYTES",
    "SCHEMA_VERSION",
    "load_factor_records",
    "load_factor_snapshot",
    "normalize_conditions",
    "resolve_factor_library_path",
]
