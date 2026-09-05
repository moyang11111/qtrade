"""QTrade-owned, atomic downstream research snapshot pipeline.

The pipeline consumes one already verified portal generation and writes only
derived, read-only research artifacts below the explicit Electron user-data
root.  A single pointer is the commit record: until it is replaced, readers
continue to see the previous complete generation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import partial
import hashlib
import json
import math
import multiprocessing
import os
import pickle
from pathlib import Path
import re
import sqlite3
import time
import uuid

from . import portal_refresh
from .portal_refresh import PortalRefreshError, PortalSnapshot
from .market_data import MainboardMarketDataAdapter
from .portal_refresh_provider import (
    PortalPlanError,
    _calendar_token,
    _load_trade_dates,
    build_bound_plan,
)
from .portal_refresh_worker import PortalRefreshWorker, _terminate_owned_process


SCHEMA_VERSION = 1
FACTOR_ALGORITHM_VERSION = "qtrade-factors-v1"
DECISION_ALGORITHM_VERSION = "qtrade-advisory-v1"
PIPELINE_ROOT_NAME = "snapshot_pipeline"
CURRENT_NAME = "current.json"
GENERATION_DIR_NAME = "generations"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TMP_RE = re.compile(r"^\.current\.json\.[0-9a-f]{32}\.tmp$")
_STAGING_RE = re.compile(r"^\.staging-[0-9a-f]{32}$")
_GENERATION_FILES = frozenset({"portal_ref.json", "factors.json", "decision.json", "sync.json", "manifest.json"})
FACTOR_KEYS = (
    "std20", "downside_vol", "reversal20", "mom20", "o2c", "amihud", "max_ret20", "skew20",
    "amp20", "volume_ratio", "limup_ex_5", "pullback", "ma_alignment", "rsi_revert", "macd_hist",
    "roc20", "wpr14", "cci20", "obv_trend", "kdj_k", "ma200_up", "lowvol_60", "mom_120",
    "near_high_250", "new_high_250", "consec_limit_up", "consec_limit_down", "limit_up_flag",
    "limit_down_flag", "kdj_d", "kdj_j", "vol_contract", "near_ma250", "ma50_up", "rsi6",
)
_BINDING_KEYS = frozenset({
    "schema_version", "pipeline_generation", "portal_generation", "target_date",
    "universe_token", "portal_content_sha256", "portal_db_schema", "portal_db_size",
    "portal_db_sha256", "portal_metadata_schema", "portal_metadata_size",
    "portal_metadata_sha256", "portal_history_window", "portal_history_rows", "kind",
})
_PIPELINE_POINTER_KEYS = frozenset({
    "schema_version", "generation", "target_date", "portal_generation", "portal_content_sha256",
    "universe_token", "factor_algorithm_version", "decision_algorithm_version", "total",
    "computable", "candidate", "manifest_sha256", "portal_db_schema", "portal_db_size",
    "portal_db_sha256", "portal_metadata_schema", "portal_metadata_size", "portal_metadata_sha256",
    "portal_history_window", "portal_history_rows",
})
_MANIFEST_KEYS = frozenset({
    "schema_version", "state", "generation", "target_date", "portal_generation",
    "portal_content_sha256", "universe_token", "factor_algorithm_version",
    "decision_algorithm_version", "total", "computable", "candidate", "updated_at",
    "portal_ref_path", "portal_ref_size", "portal_ref_sha256", "factors_path", "factors_size",
    "factors_sha256", "decision_path", "decision_size", "decision_sha256", "sync_path",
    "sync_size", "sync_sha256", "portal_db_schema", "portal_db_size", "portal_db_sha256",
    "portal_metadata_schema", "portal_metadata_size", "portal_metadata_sha256",
    "portal_history_window", "portal_history_rows",
})


class SnapshotPipelineError(RuntimeError):
    """Stable pipeline error; no local paths or provider details escape."""

    def __init__(self, reason: str):
        self.reason = reason if re.fullmatch(r"[a-z][a-z0-9_]{0,47}", reason) else "pipeline_failed"
        super().__init__(self.reason)


@dataclass(frozen=True)
class PipelinePaths:
    user_data: Path
    state: Path
    root: Path
    current: Path
    generations: Path

    def generation(self, token: str) -> Path:
        if _TOKEN_RE.fullmatch(token or "") is None:
            raise SnapshotPipelineError("generation_invalid")
        return portal_refresh._contained(self.generations, self.generations / token)


@dataclass(frozen=True)
class PipelineSnapshot:
    manifest: Mapping[str, object]
    portal: PortalSnapshot
    factors: Mapping[str, object]
    decision: Mapping[str, object]
    sync: Mapping[str, object]


class _PipelineLease:
    """One shared lease for direct and coordinator-owned pipeline calls."""

    def __init__(self, path: Path, token=None):
        self.path = Path(path)
        self.token = token
        self._fd = None
        self._identity = None
        self._borrowed = False

    @staticmethod
    def _read_owner(fd: int) -> int | None:
        try:
            content = os.read(fd, 128).decode("ascii", "strict")
        except (OSError, UnicodeError):
            return None
        match = re.fullmatch(r"pid=(\d+)\s*", content)
        if match is None:
            return None
        try:
            owner = int(match.group(1))
        except ValueError:
            return None
        return owner if owner > 0 else None

    def _borrow_existing(self) -> bool:
        token = self.token
        if (
            not isinstance(token, tuple)
            or len(token) != 3
            or token[0] != os.getpid()
            or not all(isinstance(value, int) and value > 0 for value in token[1:])
        ):
            return False
        try:
            fd = os.open(str(self.path), os.O_RDONLY)
            identity = os.fstat(fd)
            owner = self._read_owner(fd)
            current = os.fstat(fd)
            if owner != token[0] or (identity.st_dev, identity.st_ino) != tuple(token[1:]):
                os.close(fd)
                return False
            if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
                os.close(fd)
                return False
            os.close(fd)
            self._identity = (identity.st_dev, identity.st_ino)
            self._borrowed = True
            return True
        except OSError:
            return False

    def acquire(self) -> bool:
        if self.token is not None:
            return self._borrow_existing()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        identity = os.fstat(fd)
        try:
            os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(fd)
        except Exception:
            try:
                os.close(fd)
            finally:
                try:
                    if self.path.stat().st_ino == identity.st_ino:
                        self.path.unlink()
                except (FileNotFoundError, OSError):
                    pass
            raise
        self._fd = fd
        self._identity = (identity.st_dev, identity.st_ino)
        return True

    def release(self) -> None:
        if self._borrowed:
            self._borrowed = False
            return
        fd = self._fd
        identity = self._identity
        self._fd = None
        self._identity = None
        if fd is None:
            return
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            current = self.path.stat()
            if identity is not None and (current.st_dev, current.st_ino) == identity:
                self.path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _owned_call_entry(function, args, kwargs, connection, token: str) -> None:
    """Execute one pure calculation in an owned child process."""

    try:
        value = function(*args, **kwargs)
        connection.send(("ok", token, value))
    except SnapshotPipelineError as exc:
        try:
            connection.send(("error", token, exc.reason))
        except (BrokenPipeError, EOFError, OSError):
            pass
    except PortalPlanError as exc:
        try:
            connection.send(("error", token, exc.reason))
        except (BrokenPipeError, EOFError, OSError):
            pass
    except Exception:
        try:
            connection.send(("error", token, "pipeline_callback_failed"))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        try:
            connection.close()
        except (AttributeError, OSError):
            pass


def _run_owned_call(function, args, kwargs, *, deadline: float, stop_event, token: str):
    """Run a picklable pure phase with a hard, owned process boundary."""

    try:
        pickle.dumps((function, args, kwargs), protocol=pickle.HIGHEST_PROTOCOL)
    except (pickle.PickleError, TypeError, AttributeError):
        raise SnapshotPipelineError("pipeline_executor_unavailable") from None
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(False)
    process = context.Process(
        target=_owned_call_entry,
        args=(function, args, kwargs, child, token),
        daemon=False,
    )
    message = None
    try:
        process.start()
        child.close()
        while time.monotonic() < deadline:
            if stop_event.is_set():
                _terminate_owned_process(process)
                raise SnapshotPipelineError("aborted")
            remaining = max(0.01, deadline - time.monotonic())
            if parent.poll(min(0.1, remaining)):
                try:
                    message = parent.recv()
                except (EOFError, OSError, ValueError):
                    message = None
                break
            if not process.is_alive():
                break
        if message is None:
            _terminate_owned_process(process)
            raise SnapshotPipelineError("job_timeout")
        if not isinstance(message, tuple) or len(message) != 3 or message[1] != token:
            raise SnapshotPipelineError("pipeline_callback_failed")
        if message[0] == "error":
            reason = message[2] if isinstance(message[2], str) else "pipeline_callback_failed"
            raise SnapshotPipelineError(reason)
        if message[0] != "ok":
            raise SnapshotPipelineError("pipeline_callback_failed")
        return message[2]
    finally:
        try:
            parent.close()
        except (AttributeError, OSError):
            pass
        try:
            child.close()
        except (AttributeError, OSError):
            pass
        try:
            if process.is_alive():
                _terminate_owned_process(process)
            else:
                process.join(0.5)
        except (AttributeError, OSError):
            pass


def load_trade_calendar_dates() -> tuple[str, ...]:
    """Load the server-owned calendar through the bounded child seam."""

    return tuple(_load_trade_dates())


def prepare_snapshot_candidate(pipeline: PipelineSnapshot) -> bool:
    """Validate candidate disk data without mutating the parent service."""

    if not isinstance(pipeline, PipelineSnapshot):
        return False
    try:
        adapter = MainboardMarketDataAdapter(
            base_dir=Path.cwd(),
            overlay_db=pipeline.portal.database,
            overlay_only=True,
            overlay_manifest=dict(pipeline.portal.manifest),
            overlay_metadata=list(pipeline.portal.metadata),
        )
        return bool(adapter.available)
    except Exception:  # noqa: BLE001 - child exposes only a safe boolean
        return False


def load_current_bound_plan_inputs(
    *,
    base_dir: str | Path | None = None,
    state_dir: str | Path | None,
    user_data_dir: str | Path,
    target_date: str | date,
    calendar_dates,
) -> dict[str, object]:
    """Return plan inputs only from a verified current history overlay.

    This seam deliberately does not accept or inspect a base directory.  The
    complete pipeline must never rebuild its universe from external SQLite,
    CSV, live adapters, or bridge state after the portal refresh boundary.
    """

    del base_dir
    target = _date(target_date)
    snapshot = portal_refresh.read_current_snapshot(
        state_dir,
        user_data_dir=user_data_dir,
    )
    if snapshot is None:
        raise PortalPlanError("universe_unavailable")
    manifest = snapshot.manifest
    if (
        manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION
        or manifest.get("history_window") != portal_refresh.HISTORY_WINDOW
        or manifest.get("history_schema") != portal_refresh.HISTORY_DB_SCHEMA
        or manifest.get("target_date") != target
    ):
        raise PortalPlanError("universe_unavailable")
    raw_symbols = manifest.get("symbols")
    if not isinstance(raw_symbols, list) or not raw_symbols:
        raise PortalPlanError("universe_unavailable")
    symbols = tuple(str(symbol) for symbol in raw_symbols)
    metadata = {}
    if len(snapshot.metadata) != len(symbols):
        raise PortalPlanError("universe_unavailable")
    for symbol, record in zip(symbols, snapshot.metadata):
        if not isinstance(record, Mapping) or str(record.get("code")) != symbol:
            raise PortalPlanError("universe_unavailable")
        metadata[symbol] = dict(record)
    return {
        "symbols": symbols,
        "metadata": metadata,
        "calendar_dates": tuple(calendar_dates),
        "target_date": target,
    }


def _build_bound_plan_from_inputs(
    *,
    symbols,
    metadata,
    calendar_dates,
    target_date,
):
    """Build a plan only from an already server-bound, serialized input set."""

    return build_bound_plan(
        symbols=symbols,
        metadata=metadata,
        target_date=target_date,
        calendar_dates=calendar_dates,
    )


def _paths(state_dir: str | Path | None, user_data_dir: str | Path | None) -> PipelinePaths:
    if user_data_dir is None:
        raise SnapshotPipelineError("user_data_unavailable")
    try:
        portal_paths = portal_refresh.portal_refresh_paths(state_dir, user_data_dir=user_data_dir)
        root = portal_refresh._contained(portal_paths.state, portal_paths.state / PIPELINE_ROOT_NAME)
        generations = portal_refresh._contained(root, root / GENERATION_DIR_NAME)
        current = portal_refresh._contained(root, root / CURRENT_NAME)
    except (OSError, ValueError, PortalRefreshError) as exc:
        raise SnapshotPipelineError("state_path_invalid") from exc
    return PipelinePaths(portal_paths.user_data, portal_paths.state, root, current, generations)


def pipeline_paths(state_dir: str | Path | None, *, user_data_dir: str | Path | None) -> PipelinePaths:
    """Return the strictly contained pipeline paths for trusted callers/tests."""

    return _paths(state_dir, user_data_dir)


def _validate_layout(paths: PipelinePaths, *, create: bool = False) -> None:
    portal_refresh._canonical(paths.user_data)
    portal_refresh._contained(paths.user_data, paths.state)
    portal_refresh._contained(paths.state, paths.root)
    portal_refresh._contained(paths.root, paths.current)
    portal_refresh._contained(paths.root, paths.generations)
    if create:
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.generations.mkdir(parents=True, exist_ok=True)
    if paths.root.exists() and (paths.root.is_symlink() or not paths.root.is_dir()):
        raise SnapshotPipelineError("state_path_invalid")
    if paths.generations.exists() and (paths.generations.is_symlink() or not paths.generations.is_dir()):
        raise SnapshotPipelineError("state_path_invalid")
    if not paths.root.exists() or not paths.generations.exists():
        return
    for entry in paths.root.iterdir():
        if entry.name not in {CURRENT_NAME, GENERATION_DIR_NAME} and _TMP_RE.fullmatch(entry.name) is None:
            raise SnapshotPipelineError("state_layout_invalid")
        if entry.is_symlink() or portal_refresh._contained(paths.root, entry) != entry:
            raise SnapshotPipelineError("state_path_invalid")
        if entry.name == CURRENT_NAME and not entry.is_file():
            raise SnapshotPipelineError("state_layout_invalid")
        if entry.name == GENERATION_DIR_NAME and not entry.is_dir():
            raise SnapshotPipelineError("state_layout_invalid")
        if _TMP_RE.fullmatch(entry.name) and not entry.is_file():
            raise SnapshotPipelineError("state_layout_invalid")
    for entry in paths.generations.iterdir():
        if entry.is_symlink() or portal_refresh._contained(paths.generations, entry) != entry:
            raise SnapshotPipelineError("state_path_invalid")
        if _TOKEN_RE.fullmatch(entry.name) and entry.is_dir():
            continue
        if _STAGING_RE.fullmatch(entry.name) and entry.is_dir():
            continue
        raise SnapshotPipelineError("state_layout_invalid")


def _read_json(path: Path, maximum: int = 16 * 1024 * 1024):
    try:
        return portal_refresh._read_json(path, maximum)
    except (OSError, ValueError, PortalRefreshError) as exc:
        raise SnapshotPipelineError("pipeline_corrupt") from exc


def _hash(path: Path) -> str:
    try:
        return portal_refresh._hash_file(path)
    except OSError as exc:
        raise SnapshotPipelineError("pipeline_corrupt") from exc


def _date(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and _DATE_RE.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return None
        return value
    return None


def _token(value: object) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise SnapshotPipelineError("pipeline_binding_invalid")
    return value


def _binding(payload: Mapping[str, object], *, generation: str) -> tuple[str, str, str, str]:
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("pipeline_generation") != generation:
        raise SnapshotPipelineError("pipeline_binding_invalid")
    target = _date(payload.get("target_date"))
    portal_generation = _token(payload.get("portal_generation"))
    content = _token(payload.get("portal_content_sha256"))
    universe = payload.get("universe_token")
    if target is None or not isinstance(universe, str) or len(universe) > 256:
        raise SnapshotPipelineError("pipeline_binding_invalid")
    try:
        if portal_refresh._normalize_universe_token(universe) != universe:
            raise SnapshotPipelineError("pipeline_binding_invalid")
    except PortalRefreshError as exc:
        raise SnapshotPipelineError("pipeline_binding_invalid") from exc
    if (
        payload.get("portal_db_schema") != portal_refresh.HISTORY_DB_SCHEMA
        or payload.get("portal_metadata_schema") != portal_refresh.HISTORY_METADATA_SCHEMA
        or payload.get("portal_history_window") != portal_refresh.HISTORY_WINDOW
        or not isinstance(payload.get("portal_history_rows"), list)
        or not 1 <= len(payload.get("portal_history_rows", [])) <= 5000
        or any(row_count != portal_refresh.HISTORY_WINDOW for row_count in payload.get("portal_history_rows", []))
    ):
        raise SnapshotPipelineError("pipeline_binding_invalid")
    for key in ("portal_db_sha256", "portal_metadata_sha256"):
        if not isinstance(payload.get(key), str) or _TOKEN_RE.fullmatch(payload[key]) is None:
            raise SnapshotPipelineError("pipeline_binding_invalid")
    for key in ("portal_db_size", "portal_metadata_size"):
        if not isinstance(payload.get(key), int) or isinstance(payload[key], bool) or payload[key] <= 0:
            raise SnapshotPipelineError("pipeline_binding_invalid")
    return target, portal_generation, content, universe


def _safe_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 5000:
        raise SnapshotPipelineError("pipeline_schema_invalid")
    return value


def _safe_factor_records(value: object, target: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 5000:
        raise SnapshotPipelineError("pipeline_schema_invalid")
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"symbol", "values", "score", "as_of"}:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        symbol = str(item.get("symbol") or "")
        if not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        values = item.get("values")
        if not isinstance(values, Mapping) or set(values) != set(FACTOR_KEYS):
            raise SnapshotPipelineError("pipeline_schema_invalid")
        clean_values: dict[str, float | None] = {}
        for key, raw in values.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
                raise SnapshotPipelineError("pipeline_schema_invalid")
            if raw is not None and (isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw)):
                raise SnapshotPipelineError("pipeline_schema_invalid")
            clean_values[key] = None if raw is None else float(raw)
        score = item.get("score")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
            raise SnapshotPipelineError("pipeline_schema_invalid")
        if item.get("as_of") != target:
            raise SnapshotPipelineError("pipeline_date_mismatch")
        seen.add(symbol)
        result.append({
            "symbol": symbol,
            "values": {key: clean_values[key] for key in FACTOR_KEYS},
            "score": None if score is None else float(score),
            "as_of": target,
        })
    return result


def _safe_decision_records(value: object, target: str) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 5000:
        raise SnapshotPipelineError("pipeline_schema_invalid")
    result = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"symbol", "action", "score", "reason", "as_of"}:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        symbol = item.get("symbol")
        action = item.get("action")
        score = item.get("score")
        if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) or symbol in seen:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        if action not in {"buy", "sell", "hold"} or item.get("reason") != "composite_score":
            raise SnapshotPipelineError("pipeline_schema_invalid")
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)):
            raise SnapshotPipelineError("pipeline_schema_invalid")
        if item.get("as_of") != target:
            raise SnapshotPipelineError("pipeline_date_mismatch")
        expected_action = "hold" if score is None or -1 < score < 1 else "buy" if score >= 1 else "sell"
        if action != expected_action:
            raise SnapshotPipelineError("pipeline_score_mismatch")
        seen.add(symbol)
        result.append({"symbol": symbol, "action": action, "score": None if score is None else float(score), "reason": "composite_score", "as_of": target})
    return result


def _verify_artifacts(paths: PipelinePaths, generation: str, portal: PortalSnapshot | None = None) -> PipelineSnapshot:
    directory = paths.generation(generation)
    if not directory.is_dir() or directory.is_symlink():
        raise SnapshotPipelineError("pipeline_corrupt")
    entries = list(directory.iterdir())
    if {entry.name for entry in entries} != _GENERATION_FILES or any(
        entry.is_symlink() or portal_refresh._contained(directory, entry) != entry for entry in entries
    ):
        raise SnapshotPipelineError("pipeline_corrupt")
    manifest = _read_json(directory / "manifest.json")
    if not isinstance(manifest, Mapping) or set(manifest) != _MANIFEST_KEYS or manifest.get("state") != "complete":
        raise SnapshotPipelineError("pipeline_schema_invalid")
    if manifest.get("generation") != generation or manifest.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotPipelineError("pipeline_binding_invalid")
    manifest_total = _safe_int(manifest.get("total"))
    manifest_computable = _safe_int(manifest.get("computable"))
    manifest_candidate = _safe_int(manifest.get("candidate"))
    if manifest_computable > manifest_total or manifest_candidate > manifest_total:
        raise SnapshotPipelineError("pipeline_count_mismatch")
    if manifest.get("factor_algorithm_version") != FACTOR_ALGORITHM_VERSION or manifest.get("decision_algorithm_version") != DECISION_ALGORITHM_VERSION:
        raise SnapshotPipelineError("algorithm_invalid")
    target, portal_generation, content, universe = _binding(
        {"schema_version": manifest.get("schema_version"), "pipeline_generation": generation, **manifest},
        generation=generation,
    )
    expected = {
        "portal_ref": "portal_ref.json", "factors": "factors.json", "decision": "decision.json", "sync": "sync.json",
    }
    loaded: dict[str, Mapping[str, object]] = {}
    for kind, name in expected.items():
        path = directory / name
        size_key = f"{kind}_size"
        hash_key = f"{kind}_sha256"
        if manifest.get(f"{kind}_path") != f"generations/{generation}/{name}":
            raise SnapshotPipelineError("pipeline_binding_invalid")
        if manifest.get(size_key) != path.stat().st_size or manifest.get(hash_key) != _hash(path):
            raise SnapshotPipelineError("pipeline_corrupt")
        payload = _read_json(path)
        if not isinstance(payload, Mapping):
            raise SnapshotPipelineError("pipeline_schema_invalid")
        required = {
            "portal_ref": _BINDING_KEYS | {"symbols", "total", "as_of"},
            "factors": _BINDING_KEYS | {"algorithm_version", "total", "computable", "valid_count", "records"},
            "decision": _BINDING_KEYS | {"algorithm_version", "candidate", "records", "source_factors_sha256"},
            "sync": _BINDING_KEYS | {"state", "total", "computable", "candidate", "as_of"},
        }[kind]
        if set(payload) != required:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        _binding(payload, generation=generation)
        if payload.get("target_date") != target or payload.get("portal_generation") != portal_generation:
            raise SnapshotPipelineError("pipeline_binding_invalid")
        if payload.get("portal_content_sha256") != content or payload.get("universe_token") != universe:
            raise SnapshotPipelineError("pipeline_binding_invalid")
        if payload.get("kind") != kind:
            raise SnapshotPipelineError("pipeline_schema_invalid")
        loaded[kind] = payload
    portal_ref = loaded["portal_ref"]
    symbols = portal_ref.get("symbols")
    if (
        not isinstance(symbols, list)
        or len(symbols) != len(set(symbols))
        or any(not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) for symbol in symbols)
        or len(symbols) != manifest_total
    ):
        raise SnapshotPipelineError("pipeline_schema_invalid")
    if portal is None:
        portal = portal_refresh.read_generation_snapshot(
            paths.state, user_data_dir=paths.user_data, generation=portal_generation,
        )
    if portal is None or portal.manifest.get("target_date") != target or portal.manifest.get("content_sha256") != content:
        raise SnapshotPipelineError("portal_binding_invalid")
    if portal.manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION:
        raise SnapshotPipelineError("portal_schema_unsupported")
    if (
        portal.manifest.get("db_schema") != portal_refresh.HISTORY_DB_SCHEMA
        or portal.manifest.get("metadata_schema") != portal_refresh.HISTORY_METADATA_SCHEMA
        or portal.manifest.get("history_window") != portal_refresh.HISTORY_WINDOW
        or portal.manifest.get("history_rows") != [portal_refresh.HISTORY_WINDOW] * manifest_total
    ):
        raise SnapshotPipelineError("portal_binding_invalid")
    if (
        portal.manifest.get("db_size") != manifest.get("portal_db_size")
        or portal.manifest.get("db_sha256") != manifest.get("portal_db_sha256")
        or portal.manifest.get("metadata_size") != manifest.get("portal_metadata_size")
        or portal.manifest.get("metadata_sha256") != manifest.get("portal_metadata_sha256")
    ):
        raise SnapshotPipelineError("portal_binding_invalid")
    if list(portal.manifest.get("symbols", ())) != symbols:
        raise SnapshotPipelineError("universe_mismatch")
    factors = loaded["factors"]
    if factors.get("algorithm_version") != FACTOR_ALGORITHM_VERSION:
        raise SnapshotPipelineError("algorithm_invalid")
    factor_records = _safe_factor_records(factors.get("records"), target)
    factor_total = _safe_int(factors.get("total"))
    factor_computable = _safe_int(factors.get("computable"))
    factor_valid_count = _safe_int(factors.get("valid_count"))
    factor_symbols = [item["symbol"] for item in factor_records]
    if (
        factor_total != manifest_total
        or factor_computable != manifest_computable
        or factor_computable > factor_total
        or factor_valid_count > len(factor_records)
        or factor_valid_count > factor_computable
        or factor_valid_count != sum(item["score"] is not None for item in factor_records)
        or set(factor_symbols) != set(symbols)
    ):
        raise SnapshotPipelineError("pipeline_count_mismatch")
    decisions = loaded["decision"]
    if decisions.get("algorithm_version") != DECISION_ALGORITHM_VERSION:
        raise SnapshotPipelineError("algorithm_invalid")
    factor_bytes = json.dumps(loaded["factors"], ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if decisions.get("source_factors_sha256") != hashlib.sha256(factor_bytes).hexdigest():
        raise SnapshotPipelineError("pipeline_binding_invalid")
    decision_candidate = _safe_int(decisions.get("candidate"))
    decision_records = _safe_decision_records(decisions.get("records"), target)
    if [item["symbol"] for item in decision_records] != [item["symbol"] for item in factor_records]:
        raise SnapshotPipelineError("pipeline_symbol_mismatch")
    if any(
        decision["score"] != factor["score"]
        for decision, factor in zip(decision_records, factor_records)
    ):
        raise SnapshotPipelineError("pipeline_score_mismatch")
    if decision_candidate != manifest_candidate or decision_candidate != sum(item["action"] == "buy" for item in decision_records):
        raise SnapshotPipelineError("pipeline_count_mismatch")
    sync = loaded["sync"]
    if set(sync) != _BINDING_KEYS | {
        "state", "total", "computable", "candidate", "as_of",
    } or sync.get("state") != "active" or sync.get("as_of") != target:
        raise SnapshotPipelineError("pipeline_schema_invalid")
    sync_total = _safe_int(sync.get("total"))
    sync_computable = _safe_int(sync.get("computable"))
    sync_candidate = _safe_int(sync.get("candidate"))
    if (sync_total, sync_computable, sync_candidate) != (
        manifest_total, manifest_computable, manifest_candidate
    ):
        raise SnapshotPipelineError("pipeline_count_mismatch")
    return PipelineSnapshot(dict(manifest), portal, dict(factors), dict(decisions), dict(sync))


def read_pipeline_generation(
    state_dir: str | Path | None = None,
    *,
    user_data_dir: str | Path,
    generation: str,
) -> PipelineSnapshot | None:
    try:
        paths = _paths(state_dir, user_data_dir)
        _validate_layout(paths)
        return _verify_artifacts(paths, generation)
    except (OSError, ValueError, SnapshotPipelineError, PortalRefreshError, sqlite3.Error):
        return None


def read_current_pipeline(
    state_dir: str | Path | None = None,
    *,
    user_data_dir: str,
) -> PipelineSnapshot | None:
    try:
        paths = _paths(state_dir, user_data_dir)
        _validate_layout(paths)
        pointer = _read_json(paths.current, 64 * 1024)
        if not isinstance(pointer, Mapping) or set(pointer) != _PIPELINE_POINTER_KEYS or pointer.get("schema_version") != SCHEMA_VERSION:
            return None
        generation = _token(pointer.get("generation"))
        snapshot = _verify_artifacts(paths, generation)
        for key in (
            "target_date", "portal_generation", "portal_content_sha256", "universe_token", "total", "computable", "candidate",
            "portal_db_schema", "portal_db_size", "portal_db_sha256", "portal_metadata_schema", "portal_metadata_size",
            "portal_metadata_sha256", "portal_history_window", "portal_history_rows",
        ):
            if pointer.get(key) != snapshot.manifest.get(key):
                return None
        if pointer.get("factor_algorithm_version") != snapshot.manifest.get("factor_algorithm_version") or pointer.get("decision_algorithm_version") != snapshot.manifest.get("decision_algorithm_version"):
            return None
        if pointer.get("manifest_sha256") != _hash(paths.generation(generation) / "manifest.json"):
            return None
        return snapshot
    except (OSError, ValueError, SnapshotPipelineError, PortalRefreshError, sqlite3.Error):
        return None


def _binding_payload(generation: str, portal: PortalSnapshot) -> dict[str, object]:
    if portal.manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION:
        raise SnapshotPipelineError("portal_schema_unsupported")
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_generation": generation,
        "portal_generation": portal.manifest["generation"],
        "target_date": portal.manifest["target_date"],
        "universe_token": portal.manifest["universe_token"],
        "portal_content_sha256": portal.manifest["content_sha256"],
        "portal_db_schema": portal.manifest["db_schema"],
        "portal_db_size": portal.manifest["db_size"],
        "portal_db_sha256": portal.manifest["db_sha256"],
        "portal_metadata_schema": portal.manifest["metadata_schema"],
        "portal_metadata_size": portal.manifest["metadata_size"],
        "portal_metadata_sha256": portal.manifest["metadata_sha256"],
        "portal_history_window": portal.manifest["history_window"],
        "portal_history_rows": portal.manifest["history_rows"],
    }


def _write(path: Path, payload: Mapping[str, object]) -> None:
    portal_refresh._atomic_json(path, payload)


def _clean_staging(staging: Path) -> None:
    try:
        if not staging.is_dir() or staging.is_symlink():
            return
        for path in staging.iterdir():
            if path.is_file() and not path.is_symlink():
                path.unlink()
        staging.rmdir()
    except OSError:
        pass


def _pointer_payload(snapshot: PipelineSnapshot, manifest_sha256: str) -> dict[str, object]:
    manifest = snapshot.manifest
    return {
        "schema_version": SCHEMA_VERSION,
        "generation": manifest["generation"],
        "target_date": manifest["target_date"],
        "portal_generation": manifest["portal_generation"],
        "portal_content_sha256": manifest["portal_content_sha256"],
        "universe_token": manifest["universe_token"],
        "factor_algorithm_version": manifest["factor_algorithm_version"],
        "decision_algorithm_version": manifest["decision_algorithm_version"],
        "total": manifest["total"],
        "computable": manifest["computable"],
        "candidate": manifest["candidate"],
        "manifest_sha256": manifest_sha256,
        "portal_db_schema": manifest["portal_db_schema"],
        "portal_db_size": manifest["portal_db_size"],
        "portal_db_sha256": manifest["portal_db_sha256"],
        "portal_metadata_schema": manifest["portal_metadata_schema"],
        "portal_metadata_size": manifest["portal_metadata_size"],
        "portal_metadata_sha256": manifest["portal_metadata_sha256"],
        "portal_history_window": manifest["portal_history_window"],
        "portal_history_rows": manifest["portal_history_rows"],
    }


def publish_pipeline(
    portal: PortalSnapshot,
    factors: Mapping[str, object],
    decision: Mapping[str, object],
    *,
    state_dir: str | Path,
    user_data_dir: str | Path,
    factor_algorithm_version: str = FACTOR_ALGORITHM_VERSION,
    decision_algorithm_version: str = DECISION_ALGORITHM_VERSION,
    publish_current: bool = True,
) -> PipelineSnapshot:
    """Write four validated artifacts and atomically publish one pointer."""

    if not isinstance(portal, PortalSnapshot):
        raise SnapshotPipelineError("portal_binding_invalid")
    if portal.manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION:
        raise SnapshotPipelineError("portal_schema_unsupported")
    target = _date(portal.manifest.get("target_date"))
    portal_generation = _token(portal.manifest.get("generation"))
    content = _token(portal.manifest.get("content_sha256"))
    universe = portal.manifest.get("universe_token")
    symbols = portal.manifest.get("symbols")
    if target is None or not isinstance(universe, str) or not isinstance(symbols, list):
        raise SnapshotPipelineError("portal_binding_invalid")
    if factor_algorithm_version != FACTOR_ALGORITHM_VERSION or decision_algorithm_version != DECISION_ALGORITHM_VERSION:
        raise SnapshotPipelineError("algorithm_invalid")
    if not isinstance(factors, Mapping) or not isinstance(decision, Mapping):
        raise SnapshotPipelineError("pipeline_schema_invalid")
    factor_records = _safe_factor_records(factors.get("records"), target)
    decision_records = _safe_decision_records(decision.get("records"), target)
    if [item["symbol"] for item in decision_records] != [item["symbol"] for item in factor_records]:
        raise SnapshotPipelineError("pipeline_symbol_mismatch")
    total = len(symbols)
    computable = _safe_int(factors.get("computable"))
    valid_count = _safe_int(factors.get("valid_count"))
    candidate = _safe_int(decision.get("candidate"))
    if (
        factors.get("total") != total
        or set(item["symbol"] for item in factor_records) != set(symbols)
        or valid_count > len(factor_records)
        or valid_count > computable
        or candidate != sum(item["action"] == "buy" for item in decision_records)
    ):
        raise SnapshotPipelineError("pipeline_count_mismatch")
    identity = uuid.uuid4().hex
    generation = hashlib.sha256(
        "\n".join((target, portal_generation, content, universe, factor_algorithm_version, decision_algorithm_version, identity)).encode("utf-8")
    ).hexdigest()
    paths = _paths(state_dir, user_data_dir)
    _validate_layout(paths, create=True)
    current = read_current_pipeline(paths.state, user_data_dir=paths.user_data)
    if (
        current is not None
        and current.manifest.get("target_date") == target
        and current.manifest.get("portal_generation") == portal_generation
        and current.manifest.get("portal_content_sha256") == content
        and current.manifest.get("universe_token") == universe
        and current.manifest.get("factor_algorithm_version") == factor_algorithm_version
        and current.manifest.get("decision_algorithm_version") == decision_algorithm_version
        and current.factors.get("records") == factor_records
        and current.decision.get("records") == decision_records
    ):
        return current
    staging = portal_refresh._contained(paths.generations, paths.generations / f".staging-{uuid.uuid4().hex}")
    final = paths.generation(generation)
    staging.mkdir()
    binding = _binding_payload(generation, portal)
    portal_ref = {**binding, "kind": "portal_ref", "symbols": symbols, "total": total, "as_of": target}
    factor_payload = {**binding, "kind": "factors", "algorithm_version": factor_algorithm_version, "total": total, "computable": computable, "valid_count": valid_count, "records": factor_records}
    factor_bytes = json.dumps(factor_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    decision_payload = {**binding, "kind": "decision", "algorithm_version": decision_algorithm_version, "source_factors_sha256": hashlib.sha256(factor_bytes).hexdigest(), "candidate": candidate, "records": decision_records}
    sync_payload = {**binding, "kind": "sync", "state": "active", "total": total, "computable": computable, "candidate": candidate, "as_of": target}
    old_pointer = None
    try:
        try:
            old_pointer = paths.current.read_bytes()
        except FileNotFoundError:
            old_pointer = None
        for name, payload in (("portal_ref.json", portal_ref), ("factors.json", factor_payload), ("decision.json", decision_payload), ("sync.json", sync_payload)):
            _write(staging / name, payload)
        manifest = {
            "schema_version": SCHEMA_VERSION, "state": "complete", "generation": generation,
            "target_date": target, "portal_generation": portal_generation, "portal_content_sha256": content,
            "universe_token": universe, "factor_algorithm_version": factor_algorithm_version,
            "decision_algorithm_version": decision_algorithm_version, "total": total,
            "computable": computable, "candidate": candidate,
            "portal_db_schema": binding["portal_db_schema"], "portal_db_size": binding["portal_db_size"],
            "portal_db_sha256": binding["portal_db_sha256"], "portal_metadata_schema": binding["portal_metadata_schema"],
            "portal_metadata_size": binding["portal_metadata_size"], "portal_metadata_sha256": binding["portal_metadata_sha256"],
            "portal_history_window": binding["portal_history_window"], "portal_history_rows": binding["portal_history_rows"],
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        for kind, name in (("portal_ref", "portal_ref.json"), ("factors", "factors.json"), ("decision", "decision.json"), ("sync", "sync.json")):
            path = staging / name
            manifest[f"{kind}_path"] = f"generations/{generation}/{name}"
            manifest[f"{kind}_size"] = path.stat().st_size
            manifest[f"{kind}_sha256"] = _hash(path)
        _write(staging / "manifest.json", manifest)
        os.replace(staging, final)
        portal_refresh._fsync_directory(paths.generations)
        verified = _verify_artifacts(paths, generation, portal=portal)
        if not publish_current:
            return verified
        pointer = _pointer_payload(verified, _hash(final / "manifest.json"))
        portal_refresh._atomic_json(paths.current, pointer, ignore_post_fsync_error=True)
        snapshot = read_current_pipeline(paths.state, user_data_dir=paths.user_data)
        if snapshot is None or snapshot.manifest.get("generation") != generation:
            raise SnapshotPipelineError("pipeline_publish_failed")
        return snapshot
    except (OSError, ValueError, PortalRefreshError, SnapshotPipelineError) as exc:
        if old_pointer is not None:
            try:
                if not paths.current.exists() or paths.current.read_bytes() != old_pointer:
                    portal_refresh._atomic_bytes(paths.current, old_pointer, ignore_post_fsync_error=True)
            except OSError:
                pass
        raise exc if isinstance(exc, SnapshotPipelineError) else SnapshotPipelineError("pipeline_publish_failed") from exc
    finally:
        _clean_staging(staging)


def build_factor_records(portal: PortalSnapshot) -> dict[str, object]:
    """Compute factors only from the verified portal generation database."""

    try:
        import pandas as pd
        import qtrade_factors

        symbols = list(portal.manifest["symbols"])
        if portal.manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION:
            raise SnapshotPipelineError("portal_schema_unsupported")
        rows = portal_refresh._read_database_rows_history(
            portal.database, portal.manifest["target_date"], symbols,
        )
        if rows is None:
            raise SnapshotPipelineError("portal_database_invalid")
        metadata = {str(item["code"]): item for item in portal.metadata}
        records = []
        for symbol in symbols:
            meta = metadata[symbol]
            if meta.get("computable") is not True or meta.get("tradable") is not True:
                records.append({
                    "symbol": symbol,
                    "values": {key: None for key in FACTOR_KEYS},
                    "score": None,
                    "as_of": portal.manifest["target_date"],
                })
                continue
            if len(rows.get(symbol, ())) < 280:
                raise SnapshotPipelineError("factor_history_unavailable")
            frame = pd.DataFrame(rows[symbol])
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame = frame.set_index("date")[["open", "high", "low", "close", "volume"]]
            latest = qtrade_factors.latest_factors(frame)
            if latest.get("date") != portal.manifest["target_date"]:
                raise SnapshotPipelineError("factor_date_mismatch")
            score = latest.pop("composite_score", None)
            latest.pop("symbol", None)
            latest.pop("date", None)
            values = {key: latest.get(key) for key in FACTOR_KEYS}
            records.append({"symbol": symbol, "values": values, "score": score, "as_of": portal.manifest["target_date"]})
        valid_count = sum(item["score"] is not None for item in records)
        records.sort(key=lambda item: item["symbol"])
        return {
            "total": len(symbols),
            "computable": sum(item.get("computable") is True for item in portal.metadata),
            "valid_count": valid_count,
            "records": records,
        }
    except SnapshotPipelineError:
        raise
    except Exception as exc:
        raise SnapshotPipelineError("factor_compute_failed") from exc


def build_decision_records(factors: Mapping[str, object], *, target_date: str) -> dict[str, object]:
    records = []
    for item in _safe_factor_records(factors.get("records"), target_date):
        score = item["score"]
        if score is None:
            action = "hold"
        elif score >= 1:
            action = "buy"
        elif score <= -1:
            action = "sell"
        else:
            action = "hold"
        records.append({"symbol": item["symbol"], "action": action, "score": score, "reason": "composite_score", "as_of": target_date})
    records.sort(key=lambda item: item["symbol"])
    return {"candidate": sum(item["action"] == "buy" for item in records), "records": records}


def _status_payload(state: str, reason: str, target: str, started_at: str, *, step: str | None = None, outputs=None, freshness=None, output_meta=None, finished_at=None, progress=None, job_id=None) -> dict[str, object]:
    return {
        "schema_version": 1, "mode": "full_pipeline", "accepted": state == "accepted", "state": state,
        "trade_date": target, "started_at": started_at, "finished_at": finished_at, "reason": reason,
        "step": step, "outputs": outputs or {"portal": False, "factors": False, "decision": False, "sync": False},
        "freshness": freshness or {}, "output_meta": output_meta or {},
        "retry": {"attempt": 0, "max_attempts": 0, "next_attempt_at": None},
        "job_id": job_id, "heartbeat_at": finished_at or datetime.now().isoformat(timespec="seconds"),
        "elapsed_seconds": 0.0, "progress": progress or {"completed": 0, "total": 4, "current": step},
    }


def _write_status(path: Path, payload: Mapping[str, object]) -> None:
    portal_refresh._atomic_json(path, payload, ignore_post_fsync_error=True)


def _restore_pointer(
    pipeline_root: PipelinePaths | None,
    previous_pointer: bytes | None,
) -> bool:
    """Restore only the pointer that this run replaced."""

    if pipeline_root is None:
        return False
    try:
        if previous_pointer is None:
            pipeline_root.current.unlink(missing_ok=True)
        else:
            portal_refresh._atomic_bytes(
                pipeline_root.current,
                previous_pointer,
                ignore_post_fsync_error=True,
            )
        return True
    except OSError:
        return False


def run_snapshot_pipeline(
    base_dir: str | Path,
    target_date: str | date,
    *,
    user_data_dir: str | Path,
    state_dir: str | Path | None = None,
    status_file: str | Path | None = None,
    log_file: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    project_root: str | Path | None = None,
    python_executable: str | None = None,
    stop_event=None,
    job_id: str | None = None,
    lease_path: str | Path | None = None,
    lease_token: tuple[int, int, int] | None = None,
    plan_builder: Callable[..., object] | None = None,
    plan_inputs_builder: Callable[..., Mapping[str, object]] | None = None,
    calendar_dates=None,
    calendar_loader: Callable[[], object] | None = None,
    worker_factory: Callable[..., PortalRefreshWorker] | None = None,
    factor_builder: Callable[[PortalSnapshot], Mapping[str, object]] = build_factor_records,
    decision_builder: Callable[..., Mapping[str, object]] = build_decision_records,
    prepare_fn: Callable[[PipelineSnapshot], bool] | None = None,
    activate_fn: Callable[[Mapping[str, object]], bool] | None = None,
    commit_fn: Callable[[PipelineSnapshot | None], bool] | None = None,
    deadline_seconds: float = 7200.0,
) -> int:
    """Run one server-owned portal-to-research pipeline with safe status."""

    del log_file, project_root, python_executable
    target = _date(target_date)
    if target is None:
        return 1
    if stop_event is None:
        import threading
        stop_event = threading.Event()
    started_clock = time.monotonic()
    started_at = datetime.now().isoformat(timespec="seconds")
    identifier = job_id if isinstance(job_id, str) and re.fullmatch(r"[0-9a-f]{32}", job_id) else uuid.uuid4().hex
    pointer_published = False
    activation_attempted = False
    activation_rolled_back = False
    pipeline_root = None
    previous_pointer = None
    previous_pipeline = None
    pipeline_lease = None

    def check_deadline() -> None:
        if stop_event.is_set():
            raise SnapshotPipelineError("aborted")
        if time.monotonic() - started_clock > deadline_seconds:
            raise SnapshotPipelineError("job_timeout")

    def owned_hook(function, value):
        """Run disk validation in an owned child with the pipeline deadline."""

        if function is None:
            return True
        try:
            result = _run_owned_call(
                function,
                (value,),
                {},
                deadline=started_clock + deadline_seconds,
                stop_event=stop_event,
                token=identifier,
            )
        except SnapshotPipelineError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001 - never expose callback details
            raise SnapshotPipelineError("pipeline_callback_failed") from exc
        return result

    try:
        paths = _paths(state_dir, user_data_dir)
        _validate_layout(paths, create=True)
        status_path = portal_refresh._contained(paths.state, Path(status_file) if status_file else paths.state / "daily_update_1830.status.json")
        if status_path.name != "daily_update_1830.status.json":
            raise SnapshotPipelineError("status_path_invalid")
        selected_lease = Path(lease_path) if lease_path is not None else paths.state / "daily_update_1830.manual.lock"
        if selected_lease.name != "daily_update_1830.manual.lock":
            raise SnapshotPipelineError("lease_path_invalid")
        portal_refresh._contained(paths.state, selected_lease)
        pipeline_lease = _PipelineLease(selected_lease, lease_token)
        if not pipeline_lease.acquire():
            _write_status(status_path, _status_payload("failure", "lease_busy", target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
            return 1
        _write_status(status_path, _status_payload("running", "pipeline_running", target, started_at, step="portal", job_id=identifier))
        try:
            if plan_inputs_builder is not None:
                if date.fromisoformat(target).weekday() >= 5:
                    _write_status(status_path, _status_payload("skip", "weekend", target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
                    return 0
                dates = calendar_dates
                if dates is None:
                    loader = calendar_loader or load_trade_calendar_dates
                    dates = _run_owned_call(
                        loader,
                        (),
                        {},
                        deadline=started_clock + deadline_seconds,
                        stop_event=stop_event,
                        token=identifier,
                    )
                dates = tuple(dates)
                _calendar_token(target, dates)
                if target not in {_date(value) for value in dates if _date(value) is not None}:
                    _write_status(status_path, _status_payload("skip", "calendar_closed", target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
                    return 0
                inputs = _run_owned_call(
                    plan_inputs_builder,
                    (),
                    {
                        "base_dir": base_dir,
                        "target_date": target,
                        "calendar_dates": dates,
                        "state_dir": state_dir,
                        "user_data_dir": user_data_dir,
                    },
                    deadline=started_clock + deadline_seconds,
                    stop_event=stop_event,
                    token=identifier,
                )
                if not isinstance(inputs, Mapping):
                    raise SnapshotPipelineError("universe_unavailable")
                symbols = tuple(inputs.get("symbols", ()))
                metadata = inputs.get("metadata")
                if not isinstance(metadata, Mapping):
                    raise SnapshotPipelineError("universe_unavailable")
                builder = partial(
                    _build_bound_plan_from_inputs,
                    symbols=symbols,
                    metadata=dict(metadata),
                    calendar_dates=dates,
                )
                plan, provider = _run_owned_call(
                    builder,
                    (),
                    {"target_date": target},
                    deadline=started_clock + deadline_seconds,
                    stop_event=stop_event,
                    token=identifier,
                )
            else:
                if plan_builder is None:
                    _write_status(status_path, _status_payload("failure", "universe_unavailable", target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
                    return 1
                plan, provider = _run_owned_call(
                    plan_builder,
                    (),
                    {"base_dir": base_dir, "target_date": target},
                    deadline=started_clock + deadline_seconds,
                    stop_event=stop_event,
                    token=identifier,
                )
        except PortalPlanError as exc:
            reason = exc.reason if exc.reason in {"weekend", "calendar_closed", "calendar_unavailable"} else "universe_unavailable"
            _write_status(status_path, _status_payload("skip" if reason in {"weekend", "calendar_closed"} else "failure", reason, target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
            return 0 if reason in {"weekend", "calendar_closed"} else 1
        worker_type = worker_factory or PortalRefreshWorker
        worker = worker_type(
            user_data_dir=user_data_dir,
            state_dir=state_dir,
            provider=provider,
            publish_current=False,
            history_window=portal_refresh.HISTORY_WINDOW,
        )
        result = worker.run(plan, provider=provider, stop_event=stop_event)
        check_deadline()
        if result.get("state") != "success":
            worker_state = result.get("state")
            worker_reason = result.get("reason")
            if worker_state == "aborted" or worker_reason == "aborted":
                state, reason = "aborted", "aborted"
            elif worker_state == "timed_out" or worker_reason in {"item_timeout", "batch_timeout", "job_timeout", "publish_timeout"}:
                state, reason = "timed_out", worker_reason if worker_reason in {"item_timeout", "batch_timeout", "job_timeout", "publish_timeout"} else "job_timeout"
            else:
                state, reason = "failure", worker_reason if isinstance(worker_reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,47}", worker_reason) else "portal_refresh_failed"
            _write_status(status_path, _status_payload(state, reason, target, started_at, step="portal", finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
            return 1
        portal_generation = result.get("published_generation")
        portal = portal_refresh.read_generation_snapshot(state_dir, user_data_dir=user_data_dir, generation=portal_generation)
        if portal is None or portal.manifest.get("target_date") != target or portal.manifest.get("universe_token") != plan.universe_token:
            raise SnapshotPipelineError("portal_binding_invalid")
        freshness = {"portal": {"verified": True, "as_of": target, "source": "qtrade_mirror", "reason": "verified", "total": len(plan.symbols), "coverage": len(plan.symbols)}}
        _write_status(status_path, _status_payload("running", "pipeline_running", target, started_at, step="factors", outputs={"portal": True, "factors": False, "decision": False, "sync": False}, freshness=freshness, progress={"completed": 1, "total": 4, "current": "factors"}, job_id=identifier))
        factors = _run_owned_call(
            factor_builder, (portal,), {}, deadline=started_clock + deadline_seconds,
            stop_event=stop_event, token=identifier,
        )
        check_deadline()
        factor_count = len(factors.get("records", []))
        freshness["factors"] = {"verified": True, "as_of": target, "source": "qtrade_mirror", "reason": "verified", "factor_count": factor_count, "valid_count": factor_count}
        _write_status(status_path, _status_payload("running", "pipeline_running", target, started_at, step="decision", outputs={"portal": True, "factors": True, "decision": False, "sync": False}, freshness=freshness, progress={"completed": 2, "total": 4, "current": "decision"}, job_id=identifier))
        decisions = _run_owned_call(
            decision_builder, (factors,), {"target_date": target},
            deadline=started_clock + deadline_seconds, stop_event=stop_event, token=identifier,
        )
        check_deadline()
        freshness["decision"] = {"verified": True, "as_of": target, "source": "qtrade_mirror", "reason": "verified", "pool_count": len(decisions.get("records", []))}
        pipeline_root = _paths(state_dir, user_data_dir)
        try:
            previous_pointer = pipeline_root.current.read_bytes()
        except FileNotFoundError:
            pass
        previous_pipeline = read_current_pipeline(state_dir, user_data_dir=user_data_dir)
        check_deadline()
        pipeline = publish_pipeline(
            portal, factors, decisions, state_dir=state_dir, user_data_dir=user_data_dir, publish_current=False,
        )
        check_deadline()
        if prepare_fn is not None and owned_hook(prepare_fn, pipeline) is not True:
            raise SnapshotPipelineError("reload_failed")
        check_deadline()
        portal_refresh._atomic_json(
            pipeline_root.current,
            _pointer_payload(pipeline, _hash(pipeline_root.generation(pipeline.manifest["generation"]) / "manifest.json")),
            ignore_post_fsync_error=True,
        )
        pointer_published = True
        check_deadline()
        freshness["sync"] = {"verified": True, "as_of": target, "source": "qtrade_mirror", "reason": "verified"}
        output_meta = {"pipeline": {"generation": pipeline.manifest["generation"], "portal_generation": pipeline.manifest["portal_generation"], "content_sha256": pipeline.manifest["portal_content_sha256"], "universe_token": pipeline.manifest["universe_token"], "target_date": target, "total": pipeline.manifest["total"]}}
        final_status = _status_payload("success", "completed", target, started_at, step="sync", outputs={"portal": True, "factors": True, "decision": True, "sync": True}, freshness=freshness, output_meta=output_meta, progress={"completed": 4, "total": 4, "current": None}, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier)
        if activate_fn is not None and owned_hook(activate_fn, pipeline) is not True:
            activation_attempted = True
            _restore_pointer(pipeline_root, previous_pointer)
            activation_rolled_back = True
            raise SnapshotPipelineError("reload_failed")
        if commit_fn is not None:
            activation_attempted = True
            try:
                committed = commit_fn(pipeline)
            except Exception as exc:  # noqa: BLE001 - parent callback is memory-only
                raise SnapshotPipelineError("pipeline_callback_failed") from exc
            if committed is not True:
                _restore_pointer(pipeline_root, previous_pointer)
                try:
                    commit_fn(previous_pipeline)
                except Exception:  # noqa: BLE001 - rollback remains fail-closed
                    pass
                activation_rolled_back = True
                raise SnapshotPipelineError("reload_failed")
        _write_status(status_path, final_status)
        return 0
    except (SnapshotPipelineError, OSError, ValueError, TypeError) as exc:
        try:
            if pointer_published:
                _restore_pointer(pipeline_root, previous_pointer)
            if activation_attempted and not activation_rolled_back and commit_fn is not None:
                try:
                    commit_fn(previous_pipeline)
                except Exception:  # noqa: BLE001 - rollback is fail-closed
                    pass
            status_path = locals().get("status_path")
            if isinstance(status_path, Path):
                error_reason = exc.reason if isinstance(exc, SnapshotPipelineError) else "pipeline_failed"
                terminal_state = "aborted" if error_reason == "aborted" else "timed_out" if error_reason in {"job_timeout", "item_timeout", "batch_timeout", "publish_timeout"} else "failure"
                _write_status(status_path, _status_payload(terminal_state, error_reason, target, started_at, finished_at=datetime.now().isoformat(timespec="seconds"), job_id=identifier))
        except (OSError, ValueError, TypeError):
            pass
        return 1
    finally:
        if pipeline_lease is not None:
            pipeline_lease.release()


__all__ = [
    "DECISION_ALGORITHM_VERSION", "FACTOR_ALGORITHM_VERSION", "PipelinePaths", "PipelineSnapshot",
    "SnapshotPipelineError", "build_decision_records", "build_factor_records", "load_current_bound_plan_inputs",
    "load_trade_calendar_dates",
    "pipeline_paths", "prepare_snapshot_candidate", "publish_pipeline", "read_current_pipeline",
    "read_pipeline_generation", "run_snapshot_pipeline",
]
