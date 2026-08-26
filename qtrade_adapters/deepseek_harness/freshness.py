"""QTrade-owned freshness checks for the optional evening data pipeline.

The checks read only the external adapter's documented databases and output
artifacts.  They validate business dates and explicit content fields rather
than treating filenames or filesystem timestamps as data dates.  See
``THIRD_PARTY_NOTICES.md`` for the upstream attribution and boundary.
"""

from __future__ import annotations

import ast
import csv
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from math import ceil
from pathlib import Path

from .market_data import MainboardMarketDataAdapter


_FACTOR_PATTERNS = (
    "data/factorpool/output/factor_manifest_*.json",
    "data/factorpool/output/factor_data_freshness_*.json",
    "data/factorpool/output/health_*.csv",
)
_DECISION_PATTERNS = ("logs/opp_pool_*.json", "logs/pitch_v2_*.json")


@dataclass(frozen=True)
class ArtifactInfo:
    relative: str
    digest: str
    size: int


@dataclass(frozen=True)
class ArtifactSnapshot:
    files: dict[str, ArtifactInfo]

    def changed(self, relative: str) -> bool:
        return relative not in self.files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(deck: Path, path: Path) -> str:
    return path.relative_to(deck).as_posix()


def capture_artifacts(deck: str | Path) -> ArtifactSnapshot:
    """Capture hashes of the small, date-bearing pipeline artifacts."""

    root = Path(deck)
    files: dict[str, ArtifactInfo] = {}
    patterns = _FACTOR_PATTERNS + _DECISION_PATTERNS
    for pattern in patterns:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            try:
                relative = _relative(root, path)
                files[relative] = ArtifactInfo(relative, _sha256(path), path.stat().st_size)
            except (OSError, ValueError):
                continue
    return ArtifactSnapshot(files)


def _changed_since(before: ArtifactSnapshot | None, path: Path, deck: Path) -> bool:
    if before is None:
        return True
    relative = _relative(deck, path)
    previous = before.files.get(relative)
    if previous is None:
        return True
    try:
        return previous.digest != _sha256(path)
    except OSError:
        return False


def _empty_result(reason: str, *, source: str = "unavailable") -> dict[str, object]:
    return {
        "verified": False,
        "as_of": None,
        "source": source,
        "reason": reason,
    }


def _day_text(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()[:10]
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return None
    return None


def _records_for(adapter: MainboardMarketDataAdapter) -> list[dict]:
    records = []
    for symbol in adapter.scan():
        record = adapter.metadata(symbol)
        if record is not None:
            records.append(record)
    return records


def capture_portal_baseline(deck: str | Path) -> dict[str, object]:
    """Capture the previous complete-day coverage used as a dynamic baseline."""

    adapter = MainboardMarketDataAdapter(base_dir=deck)
    if not adapter.available:
        return _empty_result(adapter.last_error or "database_unavailable")
    records = _records_for(adapter)
    summary = adapter.universe_summary()
    as_of = max(
        (
            day
            for record in records
            if (day := _day_text(record.get("latest_trade_date"))) is not None
        ),
        default=None,
    )
    coverage = sum(_day_text(record.get("latest_trade_date")) == as_of for record in records)
    return {
        "verified": bool(records and as_of),
        "as_of": as_of,
        "total": summary["total"],
        "computable": summary["computable"],
        "tradable": summary["tradable"],
        "coverage": coverage,
        "source": summary["source"],
        "token": adapter.snapshot_token(),
        "reason": "baseline_captured" if records and as_of else "portal_date_missing",
    }


def verify_portal(
    deck: str | Path,
    target: date | str,
    *,
    baseline: dict[str, object] | None = None,
) -> dict[str, object]:
    """Verify target-day market coverage against a dynamic prior-day baseline."""

    root = Path(deck)
    target_text = str(target)[:10]
    adapter = MainboardMarketDataAdapter(base_dir=root)
    if not adapter.available:
        return _empty_result(adapter.last_error or "database_unavailable")
    records = _records_for(adapter)
    summary = adapter.universe_summary()
    total = int(summary["total"])
    as_of = max(
        (
            day
            for record in records
            if (day := _day_text(record.get("latest_trade_date"))) is not None
        ),
        default=None,
    )
    coverage = sum(_day_text(record.get("latest_trade_date")) == target_text for record in records)
    previous_coverage = int((baseline or {}).get("coverage") or 0)
    dynamic_baseline = max(previous_coverage, ceil(total * 0.8)) if total else 0
    verified = bool(
        records
        and as_of == target_text
        and coverage >= max(1, dynamic_baseline)
    )
    if not records or not as_of:
        reason = "portal_date_missing"
    elif as_of != target_text:
        reason = "portal_stale"
    elif coverage < max(1, dynamic_baseline):
        reason = "portal_coverage_insufficient"
    else:
        reason = "verified"
    return {
        "verified": verified,
        "as_of": as_of,
        "total": total,
        "computable": summary["computable"],
        "tradable": summary["tradable"],
        "coverage": coverage,
        "coverage_required": max(1, dynamic_baseline),
        "source": summary["source"],
        "reason": reason,
    }


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _json_date(payload: dict) -> str | None:
    for key in ("as_of", "date", "updated", "trade_date"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) >= 10:
            try:
                return date.fromisoformat(value[:10]).isoformat()
            except ValueError:
                continue
    return None


def _csv_dates(path: Path) -> set[str]:
    dates: set[str] = set()
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                for key in ("as_of", "date", "test_date", "trade_date"):
                    value = row.get(key)
                    if isinstance(value, str) and len(value) >= 10:
                        try:
                            dates.add(date.fromisoformat(value[:10]).isoformat())
                        except ValueError:
                            pass
    except (OSError, csv.Error, UnicodeError):
        return set()
    return dates


def _factor_date(path: Path) -> str | None:
    if path.suffix.lower() == ".csv":
        dates = _csv_dates(path)
        return next(iter(dates)) if len(dates) == 1 else None
    payload = _read_json(path)
    return _json_date(payload) if payload is not None else None


def _factor_count(path: Path) -> tuple[int, int]:
    payload = _read_json(path) or {}
    factors = payload.get("factors")
    if isinstance(factors, list):
        valid = sum(
            isinstance(item, dict) and item.get("eligible") is True
            for item in factors
        )
        return len(factors), valid
    if isinstance(factors, dict):
        return len(factors), sum(isinstance(value, dict) for value in factors.values())
    return 0, 0


def verify_factors(
    deck: str | Path,
    target: date | str,
    *,
    before: ArtifactSnapshot | None = None,
) -> dict[str, object]:
    """Verify changed factor core artifacts carry the requested business date."""

    root = Path(deck)
    target_text = str(target)[:10]
    candidates: list[Path] = []
    for pattern in _FACTOR_PATTERNS:
        candidates.extend(path for path in root.glob(pattern) if path.is_file())
    dated = [path for path in candidates if _factor_date(path) == target_text]
    changed = [path for path in dated if _changed_since(before, path, root)]
    manifests = [path for path in changed if path.name.startswith("factor_manifest_")]
    freshness = [path for path in changed if path.name.startswith("factor_data_freshness_")]
    factor_count = valid_count = 0
    for path in manifests:
        factor_count, valid_count = _factor_count(path)
        if factor_count:
            break
    verified = bool(manifests and freshness and factor_count > 0)
    if not candidates:
        reason = "factor_artifact_missing"
    elif not dated:
        reason = "factor_date_mismatch"
    elif not changed:
        reason = "factor_artifact_unchanged"
    elif not manifests or not freshness:
        reason = "factor_core_artifact_missing"
    elif factor_count == 0:
        reason = "factor_count_missing"
    else:
        reason = "verified"
    return {
        "verified": verified,
        "as_of": target_text if dated else None,
        "factor_count": factor_count,
        "valid_count": valid_count,
        "artifact_count": len(changed),
        "source": "factor_artifacts" if candidates else "unavailable",
        "reason": reason,
    }


def _pool_candidates(root: Path, target_text: str, before: ArtifactSnapshot | None) -> list[tuple[Path, dict]]:
    candidates = []
    for path in root.glob("logs/opp_pool_*.json"):
        payload = _read_json(path)
        if payload is None or _json_date(payload) != target_text:
            continue
        if _changed_since(before, path, root):
            candidates.append((path, payload))
    return sorted(candidates, key=lambda item: item[0].name)


def _pool_has_explicit_result(payload: dict) -> bool:
    pitch = payload.get("pitch")
    return isinstance(pitch, list) and isinstance(payload.get("n", len(pitch)), int)


def verify_decision(
    deck: str | Path,
    target: date | str,
    *,
    before: ArtifactSnapshot | None = None,
    require_pitch: bool = True,
) -> dict[str, object]:
    """Verify a newly generated target pool and its matching pitch artifact."""

    root = Path(deck)
    target_text = str(target)[:10]
    pools = _pool_candidates(root, target_text, before)
    if not pools:
        return {
            "verified": False,
            "as_of": None,
            "pool_count": 0,
            "pitch_count": 0,
            "source": "unavailable",
            "reason": "decision_pool_missing_or_stale",
        }
    pool_path, pool = pools[-1]
    if not _pool_has_explicit_result(pool):
        result = {
            "verified": False,
            "as_of": target_text,
            "pool_count": 0,
            "pitch_count": 0,
            "source": "decision_artifact",
            "reason": "decision_empty_result_unconfirmed",
        }
        result["_pool_path"] = pool_path
        return result
    pitch = pool.get("pitch")
    pitch_count = len(pitch)
    matching_pitch: list[tuple[Path, dict]] = []
    for path in root.glob("logs/pitch_v2*.json"):
        payload = _read_json(path)
        if payload is None:
            continue
        if (
            _json_date(payload) == target_text
            and payload.get("pool_date") == target_text
            and payload.get("source_pool") == pool_path.name
            and isinstance(payload.get("pitch"), list)
            and _changed_since(before, path, root)
        ):
            entries = payload["pitch"]
            if all(
                isinstance(item, dict)
                and item.get("pitch_date", target_text) == target_text
                for item in entries
            ):
                matching_pitch.append((path, payload))
    pitch_verified = bool(matching_pitch)
    verified = bool(not require_pitch or pitch_verified)
    if not verified:
        reason = "decision_pitch_missing_or_stale"
    else:
        reason = "verified"
    result = {
        "verified": verified,
        "as_of": target_text,
        "pool_count": int(pool.get("n", len(pool.get("opportunities", [])))),
        "pitch_count": pitch_count,
        "source": "decision_artifact",
        "reason": reason,
        "pitch_verified": pitch_verified,
    }
    result["_pool_path"] = pool_path
    return result


def resolve_sync_destination(deck: str | Path) -> Path | None:
    """Read the sync script's literal DEST without importing or running it."""

    script = Path(deck) / "scripts" / "sync_data_to_roaming.py"
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    except (OSError, SyntaxError, UnicodeError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DEST" for target in node.targets):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "Path"
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            return Path(value.args[0].value)
    return None


def verify_sync(
    destination: str | Path | None,
    target: date | str,
    *,
    before: ArtifactSnapshot | None = None,
) -> dict[str, object]:
    """Confirm the sync destination independently exposes all target outputs."""

    if destination is None:
        return _empty_result("sync_target_unavailable")
    root = Path(destination)
    if not root.exists():
        return _empty_result("sync_target_missing")
    portal = verify_portal(root, target)
    factors = verify_factors(root, target, before=before)
    decision = verify_decision(root, target, before=before, require_pitch=True)
    verified = bool(portal["verified"] and factors["verified"] and decision["verified"])
    return {
        "verified": verified,
        "as_of": _day_text(target) if verified else None,
        "source": "sync_target",
        "reason": "verified" if verified else "sync_target_stale_or_incomplete",
        "portal": bool(portal["verified"]),
        "factors": bool(factors["verified"]),
        "decision": bool(decision["verified"]),
    }
