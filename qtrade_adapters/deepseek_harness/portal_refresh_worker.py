"""Bounded, resumable portal snapshot collection.

This module is the synchronous provider/executor core above
:mod:`portal_refresh`.  Provider calls run in short-lived, owned Python child
processes so a stalled item can be stopped at its item deadline.  A job writes
only QTrade-owned checkpoint and batch files below the trusted Electron
user-data directory.  The snapshot ``current.json`` pointer is advanced only
after every batch has been verified and collected.

The server/API and the existing daily pipeline deliberately do not import
this module yet.  A later owned coordinator is responsible for whole-job
cancellation and process lifecycle; this core exposes no background thread or
stop API.  Keeping the executor injectable makes its safety contract testable
without network access or third-party writes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import datetime as _datetime
import hashlib
import json
import math
import multiprocessing
from multiprocessing.connection import BufferTooShort
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

from . import portal_refresh
from .market_data import normalize_code
from .portal_refresh import publish_snapshot, read_current_snapshot
from .runtime import _pid_is_alive


WORKER_SCHEMA_VERSION = 1
WORKER_ROOT_NAME = "portal_refresh_worker"
CHECKPOINT_NAME = "checkpoint.json"
LEASE_NAME = "portal_refresh_worker.lock"
BATCH_DIR_NAME = "batches"

DEFAULT_BATCH_SIZE = 50
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 100
DEFAULT_ITEM_TIMEOUT_SECONDS = 45.0
DEFAULT_BATCH_TIMEOUT_SECONDS = 300.0
DEFAULT_JOB_TIMEOUT_SECONDS = 7200.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_DELAY_SECONDS = 5.0
STALE_LEASE_SECONDS = 15 * 60
MAX_CHECKPOINT_BYTES = 4 * 1024 * 1024
MAX_BATCH_BYTES = 16 * 1024 * 1024
MAX_PUBLISH_PAYLOAD_BYTES = 32 * 1024 * 1024
MAX_STAGED_BYTES = 128 * 1024 * 1024
MAX_CHILD_MESSAGE_BYTES = 2 * 1024
MAX_ITEM_BYTES = 1536
MAX_ITEM_STRING_CHARS = 128
MAX_JSON_DEPTH = 8
MAX_JSON_CONTAINER_ITEMS = 64

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_PROVIDER_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_CHECKPOINT_TEMP_RE = re.compile(r"^\.checkpoint\.json\.[0-9a-f]{32}\.tmp$")
_LEASE_TEMP_RE = re.compile(r"^\.portal_refresh_worker\.lock\.[0-9a-f]{32}\.tmp$")
_BATCH_RE = re.compile(r"^batch-(\d{6})-([0-9a-f]{32})\.json$")
_BATCH_TEMP_RE = re.compile(r"^\.batch-\d{6}-[0-9a-f]{32}\.json\.[0-9a-f]{32}\.tmp$")
_STATES = frozenset({"idle", "accepted", "running", "publishing", "success", "failure", "aborted", "timed_out"})
_BATCH_STATES = frozenset({"partial", "complete"})
_REASONS = frozenset({
    "accepted",
    "running",
    "completed",
    "aborted",
    "lease_busy",
    "stale_running",
    "checkpoint_corrupt",
    "checkpoint_io",
    "universe_unavailable",
    "provider_unreachable",
    "provider_schema",
    "provider_failed",
    "publishing",
    "publish_timeout",
    "item_timeout",
    "batch_timeout",
    "job_timeout",
    "publish_failed",
    "retrying",
    "checkpoint_incompatible",
})

_CHECKPOINT_KEYS = frozenset({
    "schema_version", "job_id", "target_date", "universe_token", "calendar_token", "symbols", "total",
    "provider_version", "batch_size", "batches", "completed", "failed", "retry", "current_batch", "as_of",
    "state", "reason", "started_at", "heartbeat_at", "finished_at", "elapsed_seconds",
    "staged_bytes", "published_generation", "published_content_sha256",
})
_BATCH_RECORD_KEYS = frozenset({"index", "count", "completed", "failed", "file", "sha256", "size"})
_BATCH_KEYS = frozenset({
    "schema_version", "job_id", "target_date", "universe_token", "calendar_token", "provider_version",
    "batch_index", "symbols", "items", "state",
})
_ITEM_KEYS = frozenset({"symbol", "rows", "metadata"})


class PortalWorkerError(RuntimeError):
    """A safe, stable worker error; raw provider details never leave the child."""

    def __init__(self, reason: str, *, transient: bool = False):
        self.reason = reason if reason in _REASONS else "provider_failed"
        self.transient = bool(transient)
        super().__init__(self.reason)


def _normalise_json(value: object, *, depth: int = 0) -> object:
    """Convert only bounded JSON primitives; never accept arbitrary objects."""

    if depth > MAX_JSON_DEPTH:
        raise PortalWorkerError("provider_schema")
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PortalWorkerError("provider_schema")
        return value
    if isinstance(value, str):
        if len(value) > MAX_ITEM_STRING_CHARS:
            raise PortalWorkerError("provider_schema")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise PortalWorkerError("provider_schema")
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > MAX_ITEM_STRING_CHARS:
                raise PortalWorkerError("provider_schema")
            normalized[key] = _normalise_json(item, depth=depth + 1)
        return normalized
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_JSON_CONTAINER_ITEMS:
            raise PortalWorkerError("provider_schema")
        return [_normalise_json(item, depth=depth + 1) for item in value]
    raise PortalWorkerError("provider_schema")


def _json_bytes(value: object, *, maximum: int) -> tuple[object, bytes]:
    normalized = _normalise_json(value)
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise PortalWorkerError("provider_schema") from exc
    if len(encoded) > maximum:
        raise PortalWorkerError("provider_schema")
    return normalized, encoded


def _send_json(connection, payload: object, *, maximum: int = MAX_CHILD_MESSAGE_BYTES) -> None:
    _, encoded = _json_bytes(payload, maximum=maximum)
    connection.send_bytes(encoded)


def _recv_json(connection, *, maximum: int = MAX_CHILD_MESSAGE_BYTES) -> object:
    try:
        encoded = connection.recv_bytes(maxlength=maximum)
    except (EOFError, OSError, ValueError, TypeError, BufferTooShort):
        raise PortalWorkerError("provider_failed") from None
    if len(encoded) > maximum:
        raise PortalWorkerError("provider_failed")
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError) as exc:
        raise PortalWorkerError("provider_failed") from exc
    normalized, _ = _json_bytes(value, maximum=maximum)
    return normalized


@dataclass(frozen=True)
class PortalRefreshPlan:
    """A trusted, fully resolved target universe supplied by a later layer."""

    symbols: tuple[str, ...]
    target_date: str
    universe_token: str
    calendar_verified: bool
    calendar_token: str
    provider_version: str


@dataclass(frozen=True)
class PortalWorkerPaths:
    user_data: Path
    state: Path
    root: Path
    checkpoint: Path
    lease: Path
    batches: Path


@dataclass(frozen=True)
class _ItemResult:
    ok: bool
    item: Mapping[str, object] | None = None
    reason: str = "provider_failed"
    transient: bool = False


def _date_text(value: object) -> str | None:
    if isinstance(value, _datetime.datetime):
        value = value.date()
    if isinstance(value, _datetime.date):
        return value.isoformat()
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        return None
    try:
        _datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return value


def _timestamp(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        _datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def _normalise_symbols(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        code = normalize_code(value)
        if code is None or code in seen:
            continue
        seen.add(code)
        result.append(code)
    if not portal_refresh.MIN_SYMBOLS <= len(result) <= portal_refresh.MAX_SYMBOLS:
        raise PortalWorkerError("universe_unavailable")
    return tuple(result)


def _safe_token(value: object) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise PortalWorkerError("universe_unavailable")
    return value


def _provider_version(provider: object) -> str:
    value = getattr(provider, "PROVIDER_VERSION", None)
    if not isinstance(value, str) or _PROVIDER_VERSION_RE.fullmatch(value) is None:
        raise PortalWorkerError("provider_schema")
    return value


def _calendar_token(value: object) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise PortalWorkerError("universe_unavailable")
    return value


def _plan_universe_token(
    symbols: Sequence[str],
    target_date: str,
    calendar_token: str,
    provider_version: str,
) -> str:
    encoded = json.dumps(
        {
            "schema_version": WORKER_SCHEMA_VERSION,
            "symbols": list(symbols),
            "target_date": target_date,
            "calendar_verified": True,
            "calendar_token": calendar_token,
            "provider_version": provider_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_plan(value: object) -> PortalRefreshPlan:
    if not isinstance(value, PortalRefreshPlan):
        raise PortalWorkerError("universe_unavailable")
    symbols = _normalise_symbols(value.symbols)
    if tuple(symbols) != value.symbols:
        raise PortalWorkerError("universe_unavailable")
    target = _date_text(value.target_date)
    if target is None:
        raise PortalWorkerError("universe_unavailable")
    token = _safe_token(value.universe_token)
    calendar_token = _calendar_token(value.calendar_token)
    provider_version = value.provider_version
    if value.calendar_verified is not True or not isinstance(provider_version, str):
        raise PortalWorkerError("universe_unavailable")
    if _PROVIDER_VERSION_RE.fullmatch(provider_version) is None:
        raise PortalWorkerError("universe_unavailable")
    expected_token = _plan_universe_token(symbols, target, calendar_token, provider_version)
    if token != expected_token:
        raise PortalWorkerError("universe_unavailable")
    return PortalRefreshPlan(symbols, target, token, True, calendar_token, provider_version)


def _safe_worker_paths(state_dir: str | Path | None, user_data_dir: str | Path) -> PortalWorkerPaths:
    trusted = portal_refresh.portal_refresh_paths(state_dir, user_data_dir=user_data_dir)
    root = portal_refresh._contained(trusted.state, trusted.state / WORKER_ROOT_NAME)
    checkpoint = portal_refresh._contained(root, root / CHECKPOINT_NAME)
    lease = portal_refresh._contained(root, root / LEASE_NAME)
    batches = portal_refresh._contained(root, root / BATCH_DIR_NAME)
    return PortalWorkerPaths(
        user_data=trusted.user_data,
        state=trusted.state,
        root=root,
        checkpoint=checkpoint,
        lease=lease,
        batches=batches,
    )


def _validate_worker_layout(paths: PortalWorkerPaths, *, create: bool = False) -> None:
    if not paths.user_data.exists():
        raise PortalWorkerError("checkpoint_io")
    if not paths.user_data.is_dir() or paths.user_data.is_symlink():
        raise PortalWorkerError("checkpoint_corrupt")
    portal_refresh._contained(paths.user_data, paths.state)
    if not paths.state.exists():
        if not create:
            return
        paths.state.mkdir()
    if not paths.state.is_dir() or paths.state.is_symlink():
        raise PortalWorkerError("checkpoint_corrupt")
    portal_refresh._contained(paths.state, paths.root)
    if create:
        if not paths.root.exists():
            paths.root.mkdir()
        if not paths.batches.exists():
            paths.batches.mkdir()
    for candidate in (paths.root, paths.checkpoint, paths.lease, paths.batches):
        if candidate.exists():
            portal_refresh._contained(paths.state, candidate)
            if candidate.is_symlink():
                raise PortalWorkerError("checkpoint_corrupt")
    if not paths.root.exists():
        if not create:
            return
        raise PortalWorkerError("checkpoint_corrupt")
    if not paths.root.is_dir() or not paths.batches.exists() or not paths.batches.is_dir():
        raise PortalWorkerError("checkpoint_corrupt")
    for entry in paths.root.iterdir():
        if entry.name in {CHECKPOINT_NAME, LEASE_NAME, BATCH_DIR_NAME}:
            pass
        elif _CHECKPOINT_TEMP_RE.fullmatch(entry.name) or _LEASE_TEMP_RE.fullmatch(entry.name):
            pass
        else:
            raise PortalWorkerError("checkpoint_corrupt")
        if entry.is_symlink() or portal_refresh._contained(paths.root, entry) != entry:
            raise PortalWorkerError("checkpoint_corrupt")
    for entry in paths.batches.iterdir():
        if _BATCH_RE.fullmatch(entry.name) or _BATCH_TEMP_RE.fullmatch(entry.name):
            if entry.is_symlink() or portal_refresh._contained(paths.batches, entry) != entry:
                raise PortalWorkerError("checkpoint_corrupt")
        else:
            raise PortalWorkerError("checkpoint_corrupt")


def _atomic_write_json(
    path: Path,
    payload: Mapping[str, object],
    *,
    maximum: int,
    trusted_root: Path | None = None,
) -> None:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise PortalWorkerError("checkpoint_io") from exc
    if len(encoded) > maximum:
        raise PortalWorkerError("checkpoint_io")
    destination = Path(path)
    if trusted_root is not None:
        portal_refresh._contained(trusted_root, destination)
    if destination.is_symlink():
        raise PortalWorkerError("checkpoint_io")
    portal_refresh._contained(destination.parent, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    descriptor = None
    try:
        if trusted_root is not None:
            portal_refresh._contained(trusted_root, destination.parent)
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if trusted_root is not None:
            portal_refresh._contained(trusted_root, temporary)
            portal_refresh._contained(trusted_root, destination)
        os.replace(temporary, destination)
        if trusted_root is not None:
            portal_refresh._contained(trusted_root, destination)
    except PortalWorkerError:
        raise
    except (OSError, ValueError) as exc:
        raise PortalWorkerError("checkpoint_io") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            if temporary.exists():
                temporary.unlink()
        except OSError:
            pass


def _read_json(path: Path, *, maximum: int) -> object:
    try:
        portal_refresh._canonical(path)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
            raise PortalWorkerError("checkpoint_corrupt")
        return json.loads(path.read_text(encoding="utf-8"))
    except PortalWorkerError:
        raise
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PortalWorkerError("checkpoint_corrupt") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_item(symbol: str, value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != {"rows", "metadata"}:
        raise PortalWorkerError("provider_schema")
    rows = value.get("rows")
    metadata = value.get("metadata")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise PortalWorkerError("provider_schema")
    # B1 publishes one target-day bar per symbol.  Keep the child frame small;
    # historical expansion belongs to the later provider layer.
    if len(rows) > 1:
        raise PortalWorkerError("provider_schema")
    if not isinstance(metadata, Mapping):
        raise PortalWorkerError("provider_schema")
    if len(metadata) > len(portal_refresh._METADATA_KEYS) or set(metadata) - portal_refresh._METADATA_KEYS:
        raise PortalWorkerError("provider_schema")
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != portal_refresh._ROW_KEYS:
            raise PortalWorkerError("provider_schema")
    item, _ = _json_bytes(
        {"symbol": symbol, "rows": list(rows), "metadata": dict(metadata)},
        maximum=MAX_ITEM_BYTES,
    )
    if not isinstance(item, dict):
        raise PortalWorkerError("provider_schema")
    return item


def _safe_batch_payload(
    payload: object,
    *,
    job_id: str,
    target: str,
    universe_token: str,
    calendar_token: str,
    provider_version: str,
    index: int,
    symbols: Sequence[str],
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _BATCH_KEYS:
        raise PortalWorkerError("checkpoint_corrupt")
    if payload.get("schema_version") != WORKER_SCHEMA_VERSION or payload.get("job_id") != job_id:
        raise PortalWorkerError("checkpoint_corrupt")
    if (
        payload.get("target_date") != target
        or payload.get("universe_token") != universe_token
        or payload.get("calendar_token") != calendar_token
        or payload.get("provider_version") != provider_version
        or payload.get("batch_index") != index
    ):
        raise PortalWorkerError("checkpoint_corrupt")
    raw_symbols = payload.get("symbols")
    if not isinstance(raw_symbols, list) or tuple(raw_symbols) != tuple(symbols):
        raise PortalWorkerError("checkpoint_corrupt")
    state = payload.get("state")
    if state not in _BATCH_STATES:
        raise PortalWorkerError("checkpoint_corrupt")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or len(raw_items) > len(symbols):
        raise PortalWorkerError("checkpoint_corrupt")
    by_symbol: dict[str, dict[str, object]] = {}
    for raw in raw_items:
        if not isinstance(raw, dict) or set(raw) != _ITEM_KEYS:
            raise PortalWorkerError("checkpoint_corrupt")
        symbol = normalize_code(raw.get("symbol"))
        if symbol is None or symbol not in symbols or symbol in by_symbol:
            raise PortalWorkerError("checkpoint_corrupt")
        try:
            normalized_item = _safe_item(
                symbol,
                {"rows": raw.get("rows"), "metadata": raw.get("metadata")},
            )
        except PortalWorkerError:
            raise PortalWorkerError("checkpoint_corrupt") from None
        rows = normalized_item.get("rows")
        metadata = normalized_item.get("metadata")
        if not isinstance(rows, list) or not rows or not isinstance(metadata, dict):
            raise PortalWorkerError("checkpoint_corrupt")
        by_symbol[symbol] = normalized_item
    if state == "complete" and len(by_symbol) != len(symbols):
        raise PortalWorkerError("checkpoint_corrupt")
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "job_id": job_id,
        "target_date": target,
        "universe_token": universe_token,
        "calendar_token": calendar_token,
        "provider_version": provider_version,
        "batch_index": index,
        "symbols": list(symbols),
        "items": [by_symbol[symbol] for symbol in symbols if symbol in by_symbol],
        "state": state,
    }


def _validate_checkpoint(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _CHECKPOINT_KEYS:
        raise PortalWorkerError("checkpoint_corrupt")
    if payload.get("schema_version") != WORKER_SCHEMA_VERSION:
        raise PortalWorkerError("checkpoint_corrupt")
    job_id = payload.get("job_id")
    target = payload.get("target_date")
    token = payload.get("universe_token")
    calendar_token = payload.get("calendar_token")
    provider_version = payload.get("provider_version")
    if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None:
        raise PortalWorkerError("checkpoint_corrupt")
    if (
        _date_text(target) is None
        or not isinstance(token, str)
        or _TOKEN_RE.fullmatch(token) is None
        or not isinstance(calendar_token, str)
        or _TOKEN_RE.fullmatch(calendar_token) is None
        or not isinstance(provider_version, str)
        or _PROVIDER_VERSION_RE.fullmatch(provider_version) is None
    ):
        raise PortalWorkerError("checkpoint_corrupt")
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise PortalWorkerError("checkpoint_corrupt")
    normalized = _normalise_symbols(symbols)
    if list(normalized) != symbols or payload.get("total") != len(symbols):
        raise PortalWorkerError("checkpoint_corrupt")
    batch_size = payload.get("batch_size")
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE:
        raise PortalWorkerError("checkpoint_corrupt")
    batches = payload.get("batches")
    if not isinstance(batches, list):
        raise PortalWorkerError("checkpoint_corrupt")
    expected_batches = (len(symbols) + batch_size - 1) // batch_size
    seen: set[int] = set()
    for record in batches:
        if not isinstance(record, dict) or set(record) != _BATCH_RECORD_KEYS:
            raise PortalWorkerError("checkpoint_corrupt")
        index = record.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or index in seen or not 0 <= index < expected_batches:
            raise PortalWorkerError("checkpoint_corrupt")
        seen.add(index)
        if not isinstance(record.get("count"), int) or not 1 <= record["count"] <= batch_size:
            raise PortalWorkerError("checkpoint_corrupt")
        if not isinstance(record.get("completed"), int) or not 0 <= record["completed"] <= record["count"]:
            raise PortalWorkerError("checkpoint_corrupt")
        if not isinstance(record.get("failed"), int) or record["failed"] < 0:
            raise PortalWorkerError("checkpoint_corrupt")
        expected_symbols = symbols[index * batch_size:(index + 1) * batch_size]
        expected_file = f"{BATCH_DIR_NAME}/batch-{index:06d}-{job_id}.json"
        if record.get("count") != len(expected_symbols):
            raise PortalWorkerError("checkpoint_corrupt")
        if not isinstance(record.get("file"), str) or record["file"] != expected_file:
            raise PortalWorkerError("checkpoint_corrupt")
        if not isinstance(record.get("sha256"), str) or _TOKEN_RE.fullmatch(record["sha256"]) is None:
            raise PortalWorkerError("checkpoint_corrupt")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or not 1 <= size <= MAX_BATCH_BYTES:
            raise PortalWorkerError("checkpoint_corrupt")
    completed = payload.get("completed")
    failed = payload.get("failed")
    if not isinstance(completed, int) or not 0 <= completed <= len(symbols):
        raise PortalWorkerError("checkpoint_corrupt")
    if not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
        raise PortalWorkerError("checkpoint_corrupt")
    staged_bytes = payload.get("staged_bytes")
    if not isinstance(staged_bytes, int) or isinstance(staged_bytes, bool) or not 0 <= staged_bytes <= MAX_STAGED_BYTES:
        raise PortalWorkerError("checkpoint_corrupt")
    if staged_bytes != sum(int(record["size"]) for record in batches):
        raise PortalWorkerError("checkpoint_corrupt")
    retry = payload.get("retry")
    if not isinstance(retry, dict) or set(retry) != {"attempt", "max_attempts", "next_attempt_at"}:
        raise PortalWorkerError("checkpoint_corrupt")
    if not all(isinstance(retry.get(key), int) and not isinstance(retry.get(key), bool) and retry[key] >= 0 for key in ("attempt", "max_attempts")):
        raise PortalWorkerError("checkpoint_corrupt")
    next_attempt = retry.get("next_attempt_at")
    if next_attempt is not None and _timestamp(next_attempt) is None:
        raise PortalWorkerError("checkpoint_corrupt")
    current_batch = payload.get("current_batch")
    if current_batch is not None and (
        not isinstance(current_batch, int) or not 0 <= current_batch < expected_batches
    ):
        raise PortalWorkerError("checkpoint_corrupt")
    if completed != sum(int(record["completed"]) for record in batches):
        raise PortalWorkerError("checkpoint_corrupt")
    as_of = payload.get("as_of")
    if as_of is not None and _date_text(as_of) is None:
        raise PortalWorkerError("checkpoint_corrupt")
    state = payload.get("state")
    reason = payload.get("reason")
    if state not in _STATES or not isinstance(reason, str) or reason not in _REASONS:
        raise PortalWorkerError("checkpoint_corrupt")
    for key in ("started_at", "heartbeat_at", "finished_at"):
        if payload.get(key) is not None and _timestamp(payload.get(key)) is None:
            raise PortalWorkerError("checkpoint_corrupt")
    if state == "success" and (completed != len(symbols) or as_of != target or payload.get("finished_at") is None):
        raise PortalWorkerError("checkpoint_corrupt")
    published_generation = payload.get("published_generation")
    published_content = payload.get("published_content_sha256")
    if published_generation is not None and (
        not isinstance(published_generation, str) or _TOKEN_RE.fullmatch(published_generation) is None
    ):
        raise PortalWorkerError("checkpoint_corrupt")
    if published_content is not None and (
        not isinstance(published_content, str) or _TOKEN_RE.fullmatch(published_content) is None
    ):
        raise PortalWorkerError("checkpoint_corrupt")
    if (published_generation is None) != (published_content is None):
        raise PortalWorkerError("checkpoint_corrupt")
    if state == "success" and published_generation is None:
        raise PortalWorkerError("checkpoint_corrupt")
    elapsed = payload.get("elapsed_seconds")
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or elapsed < 0:
        raise PortalWorkerError("checkpoint_corrupt")
    return dict(payload)


def _lease_record(path: Path) -> tuple[int, str, str, tuple[int, int]] | None:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        identity = os.fstat(descriptor)
        raw = os.read(descriptor, 512)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"pid", "job_id", "heartbeat_at"}:
            return None
        pid = payload.get("pid")
        job_id = payload.get("job_id")
        heartbeat = payload.get("heartbeat_at")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None or _timestamp(heartbeat) is None:
            return None
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return None
        return pid, job_id, heartbeat, (identity.st_dev, identity.st_ino)
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _reclaim_stale_lease(path: Path, *, now: _datetime.datetime | None = None) -> bool:
    record = _lease_record(path)
    if record is None:
        return False
    pid, _, heartbeat, identity = record
    reference = _datetime.datetime.fromisoformat(heartbeat)
    current = _datetime.datetime.now() if now is None else now
    if (current - reference).total_seconds() <= STALE_LEASE_SECONDS or _pid_is_alive(pid):
        return False
    if _lease_record(path) != record:
        return False
    try:
        stat = path.stat()
        if (stat.st_dev, stat.st_ino) != identity:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


class _Lease:
    def __init__(self, path: Path, job_id: str, *, clock=None):
        self.path = path
        self.job_id = job_id
        self.clock = clock or _datetime.datetime.now
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _reclaim_stale_lease(self.path, now=self.clock())
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            stat = os.fstat(self.fd)
            self.identity = (stat.st_dev, stat.st_ino)
            self._write()
            return True
        except FileExistsError:
            self.fd = None
            return False
        except Exception:
            self.release()
            raise

    def _write(self) -> None:
        if self.fd is None:
            return
        payload = json.dumps({
            "pid": os.getpid(),
            "job_id": self.job_id,
            "heartbeat_at": self.clock().isoformat(timespec="seconds"),
        }, separators=(",", ":")).encode("utf-8")
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.ftruncate(self.fd, 0)
        os.write(self.fd, payload)
        os.fsync(self.fd)

    def heartbeat(self) -> None:
        try:
            self._write()
        except OSError:
            pass

    def release(self) -> None:
        fd, identity = self.fd, self.identity
        self.fd = None
        self.identity = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if identity is None:
            return
        try:
            stat = self.path.stat()
            if (stat.st_dev, stat.st_ino) == identity:
                self.path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _child_fetch(provider, symbol: str, target: str, connection) -> None:
    try:
        result = provider.fetch(symbol, target)
        item = _safe_item(symbol, result)
        payload = {"ok": True, "item": item}
    except PortalWorkerError as error:
        payload = {"ok": False, "reason": error.reason, "transient": error.transient}
    except Exception:
        payload = {"ok": False, "reason": "provider_failed", "transient": False}
    try:
        _send_json(connection, payload)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        try:
            connection.close()
        except (AttributeError, OSError):
            pass


def _default_process_factory(provider, symbol: str, target: str, connection):
    context = multiprocessing.get_context("spawn")
    return context.Process(target=_child_fetch, args=(provider, symbol, target, connection), daemon=False)


def _child_publish(
    symbols: Sequence[str],
    target: str,
    rows: Mapping[str, object],
    metadata: Sequence[Mapping[str, object]],
    state_dir: str,
    user_data_dir: str,
    universe_token: str,
    connection,
) -> None:
    try:
        snapshot = publish_snapshot(
            symbols,
            target,
            rows,
            metadata,
            state_dir=state_dir,
            user_data_dir=user_data_dir,
            universe_token=universe_token,
        )
        payload = {
            "ok": True,
            "target_date": target,
            "total": snapshot.manifest["total"],
            "generation": snapshot.manifest.get("generation"),
            "content_sha256": snapshot.manifest.get("content_sha256"),
        }
    except Exception:
        payload = {"ok": False, "reason": "publish_failed"}
    try:
        _send_json(connection, payload)
    except (BrokenPipeError, EOFError, OSError):
        pass
    finally:
        try:
            connection.close()
        except (AttributeError, OSError):
            pass


def _default_publish_process_factory(
    symbols: Sequence[str],
    target: str,
    rows: Mapping[str, object],
    metadata: Sequence[Mapping[str, object]],
    state_dir: str,
    user_data_dir: str,
    universe_token: str,
    connection,
):
    context = multiprocessing.get_context("spawn")
    return context.Process(
        target=_child_publish,
        args=(symbols, target, rows, metadata, state_dir, user_data_dir, universe_token, connection),
        daemon=False,
    )


def _terminate_owned_process(process) -> None:
    pid = getattr(process, "pid", None)
    if os.name == "nt" and isinstance(pid, int) and pid > 0:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=1.5,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        process.terminate()
    except (AttributeError, OSError):
        pass
    try:
        process.join(0.5)
    except (AttributeError, OSError):
        pass
    try:
        alive = process.is_alive()
    except (AttributeError, OSError):
        alive = False
    if alive:
        try:
            process.kill()
        except (AttributeError, OSError):
            pass
        try:
            process.join(0.5)
        except (AttributeError, OSError):
            pass


def _new_checkpoint(
    job_id: str,
    plan: PortalRefreshPlan,
    batch_size: int,
    now: str,
    *,
    state: str = "accepted",
) -> dict[str, object]:
    return {
        "schema_version": WORKER_SCHEMA_VERSION,
        "job_id": job_id,
        "target_date": plan.target_date,
        "universe_token": plan.universe_token,
        "calendar_token": plan.calendar_token,
        "symbols": list(plan.symbols),
        "total": len(plan.symbols),
        "provider_version": plan.provider_version,
        "batch_size": batch_size,
        "batches": [],
        "completed": 0,
        "failed": 0,
        "retry": {"attempt": 0, "max_attempts": DEFAULT_MAX_ATTEMPTS, "next_attempt_at": None},
        "current_batch": None,
        "as_of": None,
        "state": state,
        "reason": state,
        "started_at": now,
        "heartbeat_at": now,
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "staged_bytes": 0,
        "published_generation": None,
        "published_content_sha256": None,
    }


class PortalRefreshWorker:
    """Run the synchronous, resumable portal snapshot core.

    Whole-job cancellation and coordinator process ownership belong to the
    follow-up lifecycle layer; this object never starts a background thread.
    """

    def __init__(
        self,
        *,
        user_data_dir: str | Path,
        state_dir: str | Path | None = None,
        provider=None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        item_timeout_seconds: float = DEFAULT_ITEM_TIMEOUT_SECONDS,
        batch_timeout_seconds: float = DEFAULT_BATCH_TIMEOUT_SECONDS,
        job_timeout_seconds: float = DEFAULT_JOB_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
        process_factory=None,
        publish_process_factory=None,
        clock=None,
        wall_clock=None,
        sleeper=None,
    ):
        if not MIN_BATCH_SIZE <= int(batch_size) <= MAX_BATCH_SIZE:
            raise ValueError("invalid batch size")
        if not 0 < float(item_timeout_seconds) <= 300:
            raise ValueError("invalid item timeout")
        if not 0 < float(batch_timeout_seconds) <= 1800:
            raise ValueError("invalid batch timeout")
        if not 0 < float(job_timeout_seconds) <= 14_400:
            raise ValueError("invalid job timeout")
        if not 1 <= int(max_attempts) <= 3:
            raise ValueError("invalid attempt count")
        self.user_data_dir = Path(user_data_dir)
        self.state_dir = Path(state_dir) if state_dir is not None else self.user_data_dir / "state"
        self.provider = provider
        self.batch_size = int(batch_size)
        self.item_timeout = float(item_timeout_seconds)
        self.batch_timeout = float(batch_timeout_seconds)
        self.job_timeout = float(job_timeout_seconds)
        self.max_attempts = int(max_attempts)
        self.retry_delay = float(retry_delay_seconds)
        self.process_factory = process_factory or _default_process_factory
        self.publish_process_factory = publish_process_factory or _default_publish_process_factory
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or _datetime.datetime.now
        self.sleeper = sleeper or time.sleep
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._active_process = None
        self._memory_status: dict[str, object] = {
            "state": "idle", "reason": "status_unavailable", "total": 0, "completed": 0,
            "failed": 0, "retry": {"attempt": 0, "max_attempts": self.max_attempts, "next_attempt_at": None},
            "current_batch": None, "as_of": None,
        }

    def _paths(self) -> PortalWorkerPaths:
        return _safe_worker_paths(self.state_dir, self.user_data_dir)

    @staticmethod
    def _safe_status(checkpoint: Mapping[str, object] | None, *, state: str | None = None, reason: str | None = None) -> dict[str, object]:
        if checkpoint is None:
            return {
                "schema_version": WORKER_SCHEMA_VERSION,
                "state": state or "idle",
                "reason": reason or "status_unavailable",
                "target_date": None,
                "total": 0,
                "completed": 0,
                "failed": 0,
                "retry": {"attempt": 0, "max_attempts": DEFAULT_MAX_ATTEMPTS, "next_attempt_at": None},
                "current_batch": None,
                "as_of": None,
                "started_at": None,
                "heartbeat_at": None,
                "finished_at": None,
                "elapsed_seconds": 0.0,
                "published_generation": None,
                "published_content_sha256": None,
            }
        safe_reason = reason if reason in _REASONS else str(checkpoint.get("reason", "provider_failed"))
        if safe_reason not in _REASONS:
            safe_reason = "provider_failed"
        safe_state = state if state in _STATES else checkpoint.get("state", "failure")
        if safe_state == "publishing":
            safe_state = "failure"
            safe_reason = "checkpoint_io"
        if safe_state not in _STATES:
            safe_state = "failure"
        retry = checkpoint.get("retry")
        safe_retry = {"attempt": 0, "max_attempts": DEFAULT_MAX_ATTEMPTS, "next_attempt_at": None}
        if isinstance(retry, Mapping):
            for key in ("attempt", "max_attempts"):
                if isinstance(retry.get(key), int) and not isinstance(retry.get(key), bool) and retry[key] >= 0:
                    safe_retry[key] = retry[key]
            if retry.get("next_attempt_at") is None or _timestamp(retry.get("next_attempt_at")):
                safe_retry["next_attempt_at"] = retry.get("next_attempt_at")
        return {
            "schema_version": WORKER_SCHEMA_VERSION,
            "state": safe_state,
            "reason": safe_reason,
            "target_date": checkpoint.get("target_date"),
            "total": checkpoint.get("total", 0),
            "completed": checkpoint.get("completed", 0),
            "failed": checkpoint.get("failed", 0),
            "retry": safe_retry,
            "current_batch": checkpoint.get("current_batch"),
            "as_of": checkpoint.get("as_of"),
            "started_at": checkpoint.get("started_at"),
            "heartbeat_at": checkpoint.get("heartbeat_at"),
            "finished_at": checkpoint.get("finished_at"),
            "elapsed_seconds": checkpoint.get("elapsed_seconds", 0.0),
            # These are opaque content tokens used by the parent coordinator
            # to prove that the service reloads this exact publication.
            "published_generation": checkpoint.get("published_generation"),
            "published_content_sha256": checkpoint.get("published_content_sha256"),
        }

    def _validate_checkpoint_artifacts(
        self,
        paths: PortalWorkerPaths,
        checkpoint: Mapping[str, object],
    ) -> None:
        symbols = tuple(str(symbol) for symbol in checkpoint["symbols"])
        batch_size = int(checkpoint["batch_size"])
        records = checkpoint["batches"]
        if not isinstance(records, list):
            raise PortalWorkerError("checkpoint_corrupt")
        total_completed = 0
        staged_bytes = 0
        for record in records:
            if not isinstance(record, Mapping):
                raise PortalWorkerError("checkpoint_corrupt")
            index = record.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                raise PortalWorkerError("checkpoint_corrupt")
            batch_symbols = symbols[index * batch_size:(index + 1) * batch_size]
            if not batch_symbols:
                raise PortalWorkerError("checkpoint_corrupt")
            payload = self._read_batch(paths, record, checkpoint, batch_symbols)
            item_count = len(payload["items"])
            if int(record["completed"]) != item_count:
                raise PortalWorkerError("checkpoint_corrupt")
            if payload["state"] == "complete" and item_count != len(batch_symbols):
                raise PortalWorkerError("checkpoint_corrupt")
            total_completed += item_count
            staged_bytes += int(record["size"])
        if total_completed != checkpoint["completed"]:
            raise PortalWorkerError("checkpoint_corrupt")
        if staged_bytes != checkpoint["staged_bytes"]:
            raise PortalWorkerError("checkpoint_corrupt")

    def _read_checkpoint(self, paths: PortalWorkerPaths) -> dict[str, object] | None:
        if not paths.checkpoint.exists():
            return None
        checkpoint = _validate_checkpoint(_read_json(paths.checkpoint, maximum=MAX_CHECKPOINT_BYTES))
        self._validate_checkpoint_artifacts(paths, checkpoint)
        return checkpoint

    def _write_checkpoint(self, paths: PortalWorkerPaths, checkpoint: dict[str, object]) -> None:
        _validate_checkpoint(checkpoint)
        _atomic_write_json(
            paths.checkpoint,
            checkpoint,
            maximum=MAX_CHECKPOINT_BYTES,
            trusted_root=paths.user_data,
        )

    def _heartbeat(self, checkpoint: dict[str, object], lease: _Lease, started: float) -> None:
        now = self.wall_clock().isoformat(timespec="seconds")
        checkpoint["heartbeat_at"] = now
        checkpoint["elapsed_seconds"] = round(max(0.0, self.clock() - started), 3)
        lease.heartbeat()

    def _set_memory(self, checkpoint: Mapping[str, object] | None, *, state=None, reason=None) -> dict[str, object]:
        safe = self._safe_status(checkpoint, state=state, reason=reason)
        with self._lock:
            self._memory_status = dict(safe)
        return safe

    def _persist_terminal(
        self,
        paths: PortalWorkerPaths,
        checkpoint: Mapping[str, object],
        *,
        state: str,
        reason: str,
    ) -> dict[str, object]:
        updated = dict(checkpoint)
        updated["state"] = state
        updated["reason"] = reason if reason in _REASONS else "checkpoint_io"
        updated["current_batch"] = None
        finished = self.wall_clock().isoformat(timespec="seconds")
        updated["finished_at"] = finished
        updated["heartbeat_at"] = finished
        try:
            self._write_checkpoint(paths, updated)
        except Exception:
            return self._set_memory(updated, state="failure", reason="checkpoint_io")
        return self._set_memory(updated)

    def _recover_disk_status(
        self,
        paths: PortalWorkerPaths,
        checkpoint: dict[str, object],
    ) -> dict[str, object]:
        def current_matches() -> bool:
            current = read_current_snapshot(self.state_dir, user_data_dir=self.user_data_dir)
            if current is None:
                return False
            return (
                current.manifest.get("target_date") == checkpoint.get("target_date")
                and current.manifest.get("universe_token") == checkpoint.get("universe_token")
                and current.manifest.get("total") == checkpoint.get("total")
                and current.manifest.get("generation") == checkpoint.get("published_generation")
                and current.manifest.get("content_sha256") == checkpoint.get("published_content_sha256")
            )

        state = checkpoint.get("state")
        if state == "success":
            if current_matches():
                return self._set_memory(checkpoint)
            return self._persist_terminal(paths, checkpoint, state="failure", reason="checkpoint_corrupt")
        if state == "publishing":
            if checkpoint.get("published_generation") is not None and current_matches():
                recovered = dict(checkpoint)
                recovered["completed"] = recovered["total"]
                recovered["as_of"] = recovered["target_date"]
                recovered["state"] = "success"
                recovered["reason"] = "completed"
                finished = self.wall_clock().isoformat(timespec="seconds")
                recovered["finished_at"] = finished
                recovered["heartbeat_at"] = finished
                try:
                    self._write_checkpoint(paths, recovered)
                except Exception:
                    return self._set_memory(recovered, state="failure", reason="checkpoint_io")
                return self._set_memory(recovered)
            return self._persist_terminal(paths, checkpoint, state="failure", reason="publish_failed")
        if state not in {"running", "accepted"}:
            return self._set_memory(checkpoint)
        heartbeat = _timestamp(checkpoint.get("heartbeat_at"))
        if heartbeat is None:
            return self._set_memory(None, state="failure", reason="checkpoint_corrupt")
        if _timestamp(heartbeat) is None:
            return self._set_memory(None, state="failure", reason="checkpoint_corrupt")
        lease_present = paths.lease.exists()
        reclaimed = _reclaim_stale_lease(paths.lease) if lease_present else False
        if reclaimed or not lease_present:
            return self._persist_terminal(paths, checkpoint, state="aborted", reason="stale_running")
        return self._set_memory(checkpoint)

    def status(self) -> dict[str, object]:
        try:
            paths = self._paths()
            _validate_worker_layout(paths)
            if not paths.state.exists() or not paths.root.exists():
                return self._set_memory(None, state="idle", reason="status_unavailable")
            checkpoint = self._read_checkpoint(paths)
            if checkpoint is not None:
                return self._recover_disk_status(paths, checkpoint)
        except PortalWorkerError as error:
            return self._set_memory(None, state="failure", reason=error.reason)
        except (OSError, ValueError):
            return self._set_memory(None, state="failure", reason="checkpoint_corrupt")
        with self._lock:
            return dict(self._memory_status)

    def run(self, plan: PortalRefreshPlan, *, provider=None) -> dict[str, object]:
        self._stop_event = threading.Event()
        chosen = provider or self.provider
        if chosen is None:
            return self._set_memory(None, state="failure", reason="universe_unavailable")
        try:
            validated = _validated_plan(plan)
            if _provider_version(chosen) != validated.provider_version:
                raise PortalWorkerError("provider_schema")
        except PortalWorkerError as error:
            return self._set_memory(None, state="failure", reason=error.reason)
        return self._run_job(validated, chosen)

    def _run_publish(
        self,
        symbols: Sequence[str],
        target: str,
        rows: Mapping[str, object],
        metadata: Sequence[Mapping[str, object]],
        universe_token: str,
        started: float,
    ) -> _ItemResult:
        try:
            encoded_size = len(json.dumps(
                {"symbols": list(symbols), "rows": rows, "metadata": list(metadata)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8"))
        except (TypeError, ValueError, OverflowError):
            return _ItemResult(False, reason="provider_schema")
        if encoded_size > MAX_PUBLISH_PAYLOAD_BYTES:
            return _ItemResult(False, reason="provider_schema")
        if self._stop_event.is_set():
            return _ItemResult(False, reason="aborted")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(False)
        process = None
        try:
            process = self.publish_process_factory(
                list(symbols), target, dict(rows), list(metadata),
                os.fspath(self.state_dir), os.fspath(self.user_data_dir), universe_token, child,
            )
            process.start()
            child.close()
            with self._lock:
                self._active_process = process
            end = min(started + self.job_timeout, self.clock() + self.batch_timeout)
            message = None
            while self.clock() < end:
                if self._stop_event.is_set():
                    _terminate_owned_process(process)
                    return _ItemResult(False, reason="aborted")
                remaining = max(0.01, end - self.clock())
                if not process.is_alive():
                    if parent.poll(0):
                        message = _recv_json(parent)
                    break
                # Do not call recv_bytes while the child may still be
                # writing its length-prefixed frame.  The child closes its
                # end after one bounded frame; reading only after exit makes
                # partial-frame teardown fail closed instead of blocking.
                parent.poll(min(0.1, remaining))
            if message is None:
                if self._stop_event.is_set():
                    return _ItemResult(False, reason="aborted")
                _terminate_owned_process(process)
                return _ItemResult(False, reason="publish_timeout", transient=False)
            if (
                not isinstance(message, dict)
                or set(message) != {"ok", "target_date", "total", "generation", "content_sha256"}
                or message.get("ok") is not True
                or message.get("target_date") != target
                or message.get("total") != len(symbols)
                or not isinstance(message.get("generation"), str)
                or _TOKEN_RE.fullmatch(message["generation"]) is None
                or not isinstance(message.get("content_sha256"), str)
                or _TOKEN_RE.fullmatch(message["content_sha256"]) is None
            ):
                return _ItemResult(False, reason="publish_failed")
            return _ItemResult(True, item=message)
        except Exception:
            if self._stop_event.is_set():
                return _ItemResult(False, reason="aborted")
            return _ItemResult(False, reason="publish_failed")
        finally:
            try:
                parent.close()
            except (AttributeError, OSError):
                pass
            try:
                child.close()
            except (AttributeError, OSError):
                pass
            if process is not None:
                try:
                    if process.is_alive():
                        _terminate_owned_process(process)
                    else:
                        process.join(0.5)
                except (AttributeError, OSError):
                    pass
            with self._lock:
                if self._active_process is process:
                    self._active_process = None

    def _run_item(self, provider, symbol: str, target: str, deadline: float) -> _ItemResult:
        if self._stop_event.is_set():
            return _ItemResult(False, reason="aborted")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(False)
        process = None
        try:
            process = self.process_factory(provider, symbol, target, child)
            process.start()
            child.close()
            with self._lock:
                self._active_process = process
            end = min(deadline, self.clock() + self.item_timeout)
            message = None
            while self.clock() < end:
                if self._stop_event.is_set():
                    _terminate_owned_process(process)
                    return _ItemResult(False, reason="aborted")
                remaining = max(0.01, end - self.clock())
                if not process.is_alive():
                    if parent.poll(0):
                        message = _recv_json(parent)
                    break
                parent.poll(min(0.1, remaining))
            if message is None:
                if self._stop_event.is_set():
                    return _ItemResult(False, reason="aborted")
                _terminate_owned_process(process)
                return _ItemResult(False, reason="item_timeout", transient=True)
            if not isinstance(message, dict) or set(message) not in ({"ok", "item"}, {"ok", "reason", "transient"}):
                return _ItemResult(False, reason="provider_failed")
            if message.get("ok") is True:
                item = message.get("item")
                if not isinstance(item, Mapping) or set(item) != _ITEM_KEYS:
                    return _ItemResult(False, reason="provider_schema")
                return _ItemResult(True, item=dict(item))
            reason = message.get("reason")
            transient = message.get("transient") is True
            return _ItemResult(False, reason=reason if reason in _REASONS else "provider_failed", transient=transient)
        except (OSError, EOFError, ValueError, TypeError, PortalWorkerError):
            if self._stop_event.is_set():
                return _ItemResult(False, reason="aborted")
            return _ItemResult(False, reason="provider_failed")
        finally:
            try:
                parent.close()
            except (AttributeError, OSError):
                pass
            try:
                child.close()
            except (AttributeError, OSError):
                pass
            if process is not None:
                try:
                    if process.is_alive():
                        _terminate_owned_process(process)
                    else:
                        process.join(0.5)
                except (AttributeError, OSError):
                    pass
            with self._lock:
                if self._active_process is process:
                    self._active_process = None

    def _write_batch(
        self,
        paths: PortalWorkerPaths,
        checkpoint: Mapping[str, object],
        index: int,
        symbols: Sequence[str],
        items: Sequence[Mapping[str, object]],
        state: str,
    ) -> tuple[str, str, int]:
        job_id = str(checkpoint["job_id"])
        target = str(checkpoint["target_date"])
        payload = {
            "schema_version": WORKER_SCHEMA_VERSION,
            "job_id": job_id,
            "target_date": target,
            "universe_token": str(checkpoint["universe_token"]),
            "calendar_token": str(checkpoint["calendar_token"]),
            "provider_version": str(checkpoint["provider_version"]),
            "batch_index": index,
            "symbols": list(symbols),
            "items": [dict(item) for item in items],
            "state": state,
        }
        _safe_batch_payload(
            payload,
            job_id=job_id,
            target=target,
            universe_token=str(checkpoint["universe_token"]),
            calendar_token=str(checkpoint["calendar_token"]),
            provider_version=str(checkpoint["provider_version"]),
            index=index,
            symbols=symbols,
        )
        encoded_size = len(json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8"))
        current_size = sum(
            int(record["size"])
            for record in checkpoint["batches"]
            if int(record["index"]) != index
        )
        if current_size + encoded_size > MAX_STAGED_BYTES:
            raise PortalWorkerError("checkpoint_io")
        path = portal_refresh._contained(paths.batches, paths.batches / f"batch-{index:06d}-{job_id}.json")
        _atomic_write_json(path, payload, maximum=MAX_BATCH_BYTES, trusted_root=paths.user_data)
        size = path.stat().st_size
        if not 1 <= size <= MAX_BATCH_BYTES:
            raise PortalWorkerError("checkpoint_io")
        return f"{BATCH_DIR_NAME}/{path.name}", _sha256(path), size

    def _read_batch(self, paths: PortalWorkerPaths, record: Mapping[str, object], checkpoint: Mapping[str, object], batch_symbols: Sequence[str]) -> dict[str, object]:
        relative = record["file"]
        if not isinstance(relative, str) or Path(relative).parent.as_posix() != BATCH_DIR_NAME:
            raise PortalWorkerError("checkpoint_corrupt")
        path = portal_refresh._contained(paths.batches, paths.batches / Path(relative).name)
        try:
            size = path.stat().st_size
            digest = _sha256(path)
        except OSError as exc:
            raise PortalWorkerError("checkpoint_corrupt") from exc
        if size != record.get("size") or not 1 <= size <= MAX_BATCH_BYTES:
            raise PortalWorkerError("checkpoint_corrupt")
        if digest != record["sha256"]:
            raise PortalWorkerError("checkpoint_corrupt")
        return _safe_batch_payload(
            _read_json(path, maximum=MAX_BATCH_BYTES),
            job_id=str(checkpoint["job_id"]),
            target=str(checkpoint["target_date"]),
            universe_token=str(checkpoint["universe_token"]),
            calendar_token=str(checkpoint["calendar_token"]),
            provider_version=str(checkpoint["provider_version"]),
            index=int(record["index"]),
            symbols=batch_symbols,
        )

    def _run_batch(self, paths: PortalWorkerPaths, checkpoint: dict[str, object], index: int, batch_symbols: Sequence[str], started: float, lease: _Lease) -> tuple[str, dict[str, object] | None]:
        job_id = str(checkpoint["job_id"])
        target = str(checkpoint["target_date"])
        batch_deadline = min(self.clock() + self.batch_timeout, started + self.job_timeout)
        existing = next((record for record in checkpoint["batches"] if record["index"] == index), None)
        items: dict[str, Mapping[str, object]] = {}
        if existing is not None:
            payload = self._read_batch(paths, existing, checkpoint, batch_symbols)
            items = {str(item["symbol"]): item for item in payload["items"]}
            if payload["state"] == "complete":
                return "complete", payload
        for symbol in batch_symbols:
            if symbol in items:
                continue
            if self._stop_event.is_set():
                return "aborted", None
            if self.clock() >= batch_deadline:
                return "batch_timeout", None
            for attempt in range(1, self.max_attempts + 1):
                checkpoint["retry"] = {"attempt": attempt, "max_attempts": self.max_attempts, "next_attempt_at": None}
                checkpoint["reason"] = "running"
                self._heartbeat(checkpoint, lease, started)
                self._write_checkpoint(paths, checkpoint)
                outcome = self._run_item(provider=self.provider_for_job, symbol=symbol, target=target, deadline=batch_deadline)
                if outcome.ok and outcome.item is not None:
                    items[symbol] = outcome.item
                    relative, digest, size = self._write_batch(
                        paths, checkpoint, index, batch_symbols, list(items.values()),
                        "complete" if len(items) == len(batch_symbols) else "partial",
                    )
                    record = {
                        "index": index,
                        "count": len(batch_symbols),
                        "completed": len(items),
                        "failed": 0,
                        "file": relative,
                        "sha256": digest,
                        "size": size,
                    }
                    checkpoint["batches"] = [old for old in checkpoint["batches"] if old["index"] != index] + [record]
                    checkpoint["batches"].sort(key=lambda old: old["index"])
                    checkpoint["completed"] = sum(old["completed"] for old in checkpoint["batches"])
                    checkpoint["staged_bytes"] = sum(int(old["size"]) for old in checkpoint["batches"])
                    if checkpoint["staged_bytes"] > MAX_STAGED_BYTES:
                        return "checkpoint_io", None
                    checkpoint["current_batch"] = index
                    checkpoint["retry"] = {"attempt": 0, "max_attempts": self.max_attempts, "next_attempt_at": None}
                    self._heartbeat(checkpoint, lease, started)
                    self._write_checkpoint(paths, checkpoint)
                    break
                if outcome.reason == "aborted" or self._stop_event.is_set():
                    return "aborted", None
                if not outcome.transient or attempt >= self.max_attempts:
                    checkpoint["failed"] = int(checkpoint.get("failed", 0)) + 1
                    checkpoint["reason"] = outcome.reason
                    self._write_checkpoint(paths, checkpoint)
                    return outcome.reason, None
                delay = min(self.retry_delay * (2 ** (attempt - 1)), max(0.0, batch_deadline - self.clock()))
                checkpoint["reason"] = "retrying"
                checkpoint["retry"] = {"attempt": attempt, "max_attempts": self.max_attempts, "next_attempt_at": (self.wall_clock() + _datetime.timedelta(seconds=delay)).isoformat(timespec="seconds")}
                self._heartbeat(checkpoint, lease, started)
                self._write_checkpoint(paths, checkpoint)
                if self.sleeper is time.sleep:
                    if self._stop_event.wait(delay):
                        return "aborted", None
                else:
                    self.sleeper(delay)
                if self.clock() >= batch_deadline:
                    return "batch_timeout", None
        payload = {
            "schema_version": WORKER_SCHEMA_VERSION,
            "job_id": job_id,
            "target_date": target,
            "universe_token": str(checkpoint["universe_token"]),
            "calendar_token": str(checkpoint["calendar_token"]),
            "provider_version": str(checkpoint["provider_version"]),
            "batch_index": index,
            "symbols": list(batch_symbols),
            "items": list(items.values()),
            "state": "complete",
        }
        return "complete", payload

    def _terminal(self, paths: PortalWorkerPaths, checkpoint: dict[str, object], *, state: str, reason: str, started: float) -> dict[str, object]:
        checkpoint["state"] = state
        checkpoint["reason"] = reason if reason in _REASONS else "provider_failed"
        checkpoint["current_batch"] = None
        checkpoint["finished_at"] = self.wall_clock().isoformat(timespec="seconds")
        checkpoint["heartbeat_at"] = checkpoint["finished_at"]
        checkpoint["elapsed_seconds"] = round(max(0.0, self.clock() - started), 3)
        try:
            self._write_checkpoint(paths, checkpoint)
        except Exception:
            pass
        return self._set_memory(checkpoint)

    def _run_job(self, plan: PortalRefreshPlan, provider) -> dict[str, object]:
        started = self.clock()
        job_id = uuid.uuid4().hex
        plan = _validated_plan(plan)
        try:
            paths = self._paths()
            _validate_worker_layout(paths, create=True)
        except (PortalWorkerError, OSError, ValueError) as error:
            reason = error.reason if isinstance(error, PortalWorkerError) else "checkpoint_io"
            return self._set_memory(None, state="failure", reason=reason)
        lease = _Lease(paths.lease, job_id, clock=self.wall_clock)
        if not lease.acquire():
            return self._set_memory(None, state="failure", reason="lease_busy")
        self.provider_for_job = provider
        try:
            provider_version = _provider_version(provider)
            if provider_version != plan.provider_version:
                raise PortalWorkerError("provider_schema")
        except PortalWorkerError as error:
            lease.release()
            return self._set_memory(None, state="failure", reason=error.reason)
        checkpoint = None
        try:
            try:
                checkpoint = self._read_checkpoint(paths)
            except PortalWorkerError as error:
                return self._set_memory(None, state="failure", reason=error.reason)
            if (
                checkpoint is not None
                and checkpoint["target_date"] == plan.target_date
                and checkpoint["universe_token"] == plan.universe_token
                and checkpoint["calendar_token"] == plan.calendar_token
                and checkpoint["provider_version"] == provider_version
            ):
                if checkpoint["batch_size"] != self.batch_size:
                    return self._persist_terminal(
                        paths, checkpoint, state="failure", reason="checkpoint_incompatible",
                    )
                if checkpoint["state"] == "success":
                    current = read_current_snapshot(self.state_dir, user_data_dir=self.user_data_dir)
                    if (
                        current is not None
                        and current.manifest.get("target_date") == plan.target_date
                        and current.manifest.get("universe_token") == plan.universe_token
                        and current.manifest.get("generation") == checkpoint.get("published_generation")
                        and current.manifest.get("content_sha256") == checkpoint.get("published_content_sha256")
                    ):
                        return self._set_memory(checkpoint)
            else:
                checkpoint = None
            if checkpoint is None:
                checkpoint = _new_checkpoint(
                    job_id, plan, self.batch_size,
                    self.wall_clock().isoformat(timespec="seconds"),
                )
                self._write_checkpoint(paths, checkpoint)
            checkpoint["state"] = "running"
            checkpoint["reason"] = "running"
            checkpoint["finished_at"] = None
            self._write_checkpoint(paths, checkpoint)
            self._set_memory(checkpoint)
            batches = [
                plan.symbols[offset:offset + self.batch_size]
                for offset in range(0, len(plan.symbols), self.batch_size)
            ]
            aggregate: dict[str, Mapping[str, object]] = {}
            for index, batch_symbols in enumerate(batches):
                if self._stop_event.is_set():
                    return self._terminal(paths, checkpoint, state="aborted", reason="aborted", started=started)
                if self.clock() - started >= self.job_timeout:
                    return self._terminal(paths, checkpoint, state="timed_out", reason="job_timeout", started=started)
                checkpoint["current_batch"] = index
                checkpoint["reason"] = "running"
                self._heartbeat(checkpoint, lease, started)
                self._write_checkpoint(paths, checkpoint)
                result, payload = self._run_batch(paths, checkpoint, index, batch_symbols, started, lease)
                if result == "aborted":
                    return self._terminal(paths, checkpoint, state="aborted", reason="aborted", started=started)
                if result != "complete" or payload is None:
                    terminal_reason = result if result in _REASONS else "provider_failed"
                    state = "timed_out" if terminal_reason in {
                        "item_timeout", "batch_timeout", "job_timeout",
                        "publish_timeout",
                    } else "failure"
                    return self._terminal(paths, checkpoint, state=state, reason=terminal_reason, started=started)
                aggregate.update({str(item["symbol"]): item for item in payload["items"]})
            if len(aggregate) != len(plan.symbols):
                return self._terminal(paths, checkpoint, state="failure", reason="checkpoint_corrupt", started=started)
            rows = {symbol: item["rows"] for symbol, item in aggregate.items()}
            metadata = [aggregate[symbol]["metadata"] for symbol in plan.symbols]
            checkpoint["state"] = "publishing"
            checkpoint["reason"] = "publishing"
            checkpoint["current_batch"] = None
            self._heartbeat(checkpoint, lease, started)
            self._write_checkpoint(paths, checkpoint)
            published = self._run_publish(
                plan.symbols,
                plan.target_date,
                rows,
                metadata,
                plan.universe_token,
                started,
            )
            if not published.ok:
                if published.reason == "aborted":
                    return self._terminal(paths, checkpoint, state="aborted", reason="aborted", started=started)
                terminal_reason = published.reason if published.reason in _REASONS else "publish_failed"
                terminal_state = "timed_out" if terminal_reason == "publish_timeout" else "failure"
                return self._terminal(paths, checkpoint, state=terminal_state, reason=terminal_reason, started=started)
            if published.item is None:
                return self._terminal(paths, checkpoint, state="failure", reason="publish_failed", started=started)
            checkpoint["published_generation"] = published.item["generation"]
            checkpoint["published_content_sha256"] = published.item["content_sha256"]
            checkpoint["completed"] = len(plan.symbols)
            checkpoint["current_batch"] = None
            checkpoint["as_of"] = plan.target_date
            checkpoint["state"] = "success"
            checkpoint["reason"] = "completed"
            checkpoint["finished_at"] = self.wall_clock().isoformat(timespec="seconds")
            checkpoint["heartbeat_at"] = checkpoint["finished_at"]
            checkpoint["elapsed_seconds"] = round(max(0.0, self.clock() - started), 3)
            try:
                self._write_checkpoint(paths, checkpoint)
            except Exception:
                # The pointer is already committed; never report failure over
                # a published generation.  Keep the on-disk marker as
                # ``publishing`` so a restart fails closed and can reconcile
                # the committed pointer instead of showing a stale running
                # job.
                return self._set_memory(checkpoint, state="failure", reason="checkpoint_io")
            return self._set_memory(checkpoint)
        except PortalWorkerError as error:
            if checkpoint is not None:
                return self._terminal(paths, checkpoint, state="failure", reason=error.reason, started=started)
            return self._set_memory(None, state="failure", reason=error.reason)
        except (OSError, ValueError, TypeError):
            if checkpoint is not None:
                return self._terminal(paths, checkpoint, state="failure", reason="checkpoint_io", started=started)
            return self._set_memory(None, state="failure", reason="checkpoint_io")
        except Exception:
            if checkpoint is not None:
                return self._terminal(paths, checkpoint, state="failure", reason="provider_failed", started=started)
            return self._set_memory(None, state="failure", reason="provider_failed")
        finally:
            lease.release()
            with self._lock:
                self._active_process = None


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "PortalRefreshWorker",
    "PortalRefreshPlan",
    "PortalWorkerError",
    "PortalWorkerPaths",
    "WORKER_ROOT_NAME",
]
