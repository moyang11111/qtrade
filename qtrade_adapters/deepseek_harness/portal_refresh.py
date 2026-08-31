"""QTrade-owned atomic portal snapshot storage.

This layer only publishes and reads a fully typed, already-complete snapshot.
It deliberately does not contain a market-data provider, a worker thread, a
subprocess, a retry loop, a lease, or a job scheduler.  Those concerns belong
to the follow-up provider/executor layer.  A snapshot is published into a new
generation below the Electron user-data directory and becomes visible only
when the final ``current.json`` pointer is atomically replaced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any
import uuid


SCHEMA_VERSION = 1
MIN_SYMBOLS = 5
MAX_SYMBOLS = 100
MAX_ROWS_PER_SYMBOL = 512
MAX_MANIFEST_BYTES = 512 * 1024
MAX_METADATA_BYTES = 512 * 1024
MAX_POINTER_BYTES = 16 * 1024
DB_SCHEMA = "daily_bar.v1"
METADATA_SCHEMA = "portal_metadata.v1"
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_POINTER_TEMP_RE = re.compile(r"^\.current\.json\.[0-9a-f]{32}\.tmp$")
_STAGING_RE = re.compile(r"^\.staging-[0-9a-f]{32}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROW_KEYS = frozenset({"code", "date", "open", "high", "low", "close", "volume", "adjust"})
_METADATA_KEYS = frozenset({
    "code", "name", "exchange", "risk_warning", "suspended", "listed", "tradable",
    "history_rows", "latest_trade_date", "computable", "eligible_reason",
})
_DB_CREATE_SQL = (
    "CREATE TABLE daily_bar (code TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL, "
    "high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL, "
    "adjust TEXT NOT NULL, PRIMARY KEY (code, date, adjust))"
)
_DB_COLUMNS = (
    ("code", "TEXT", 1, 1),
    ("date", "TEXT", 1, 2),
    ("open", "REAL", 1, 0),
    ("high", "REAL", 1, 0),
    ("low", "REAL", 1, 0),
    ("close", "REAL", 1, 0),
    ("volume", "REAL", 1, 0),
    ("adjust", "TEXT", 1, 3),
)
_MANIFEST_KEYS = frozenset({
    "schema_version", "state", "generation", "target_date", "token", "universe_token",
    "generation_nonce", "content_sha256", "symbols", "total", "completed", "as_of", "db_path", "db_schema", "db_size",
    "db_sha256", "metadata_path", "metadata_schema", "metadata_size", "metadata_sha256",
    "updated_at",
})
_POINTER_KEYS = frozenset({
    "schema_version", "generation", "target_date", "token", "total", "universe_token",
    "generation_nonce", "content_sha256", "db_path", "db_size", "db_sha256", "manifest_sha256",
    "metadata_path", "metadata_size", "metadata_sha256",
})


class PortalRefreshError(RuntimeError):
    """A safe snapshot validation or publication failure."""


@dataclass(frozen=True)
class PortalRefreshPaths:
    """Validated paths rooted at ``<userData>/state/portal_refresh``."""

    user_data: Path
    state: Path
    root: Path
    current: Path
    generations: Path

    def generation_dir(self, generation: str) -> Path:
        _validate_token(generation)
        return _contained(self.generations, self.generations / generation)

    def generation_db(self, generation: str) -> Path:
        directory = self.generation_dir(generation)
        return _contained(directory, directory / "bars_incr.db")

    def generation_metadata(self, generation: str) -> Path:
        directory = self.generation_dir(generation)
        return _contained(directory, directory / "metadata.json")

    def generation_manifest(self, generation: str) -> Path:
        directory = self.generation_dir(generation)
        return _contained(directory, directory / "manifest.json")


@dataclass(frozen=True)
class PortalSnapshot:
    """A verified generation; consumers must not mix it with external data."""

    manifest: Mapping[str, Any]
    database: Path
    metadata: tuple[Mapping[str, Any], ...]


def _canonical(path: str | Path) -> Path:
    raw = os.path.abspath(os.fspath(Path(path).expanduser()))
    resolved = os.path.realpath(raw)
    if os.path.normcase(raw) != os.path.normcase(resolved):
        raise ValueError("path must not resolve through a symlink or reparse point")
    probe = Path(raw)
    while True:
        try:
            stat = probe.lstat()
        except FileNotFoundError:
            stat = None
        if stat is not None:
            if probe.is_symlink() or bool(getattr(stat, "st_file_attributes", 0) & 0x400):
                raise ValueError("path must not contain a symlink or reparse point")
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return Path(resolved)


def _contained(root: str | Path, path: str | Path) -> Path:
    canonical_root = _canonical(root)
    canonical_path = _canonical(path)
    try:
        common = os.path.commonpath((os.fspath(canonical_root), os.fspath(canonical_path)))
    except ValueError as exc:
        raise ValueError("path is outside trusted user-data root") from exc
    if os.path.normcase(common) != os.path.normcase(os.fspath(canonical_root)):
        raise ValueError("path is outside trusted user-data root")
    return canonical_path


def portal_refresh_paths(
    state_dir: str | Path | None = None,
    *,
    user_data_dir: str | Path | None = None,
) -> PortalRefreshPaths:
    """Derive a strictly contained snapshot root.

    Production callers pass both values, with ``state_dir`` equal to
    ``<userData>/state``.  A trusted user-data root is mandatory: a state
    directory cannot establish its own trust boundary.
    """

    if user_data_dir is None:
        raise ValueError("portal snapshots require an explicit user-data root")
    user_root = _canonical(user_data_dir)
    expected_state = _canonical(user_root / "state")
    supplied_state = expected_state if state_dir is None else _canonical(state_dir)
    if os.path.normcase(os.fspath(supplied_state)) != os.path.normcase(os.fspath(expected_state)):
        raise ValueError("state directory must be exactly user-data/state")
    root = _contained(expected_state, expected_state / "portal_refresh")
    generations = _contained(root, root / "generations")
    return PortalRefreshPaths(
        user_data=user_root,
        state=expected_state,
        root=root,
        current=_contained(root, root / "current.json"),
        generations=generations,
    )


def _validate_token(value: object) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise PortalRefreshError("invalid_generation")
    return value


def _date_text(value: object) -> str | None:
    if not isinstance(value, str) or _DATE_RE.fullmatch(value) is None:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None
    return value


def _normalize_code(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not text.isdigit() or len(text) > 6:
        return None
    return text.zfill(6)


def _market_code(symbol: str) -> str:
    return f"{symbol}.{'SH' if symbol.startswith(('6', '9')) else 'SZ'}"


def _normalize_symbols(symbols: Iterable[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in symbols:
        code = _normalize_code(value)
        if code is None or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _safe_number(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PortalRefreshError("invalid_bar_value") from exc
    if not math.isfinite(number) or number <= 0:
        raise PortalRefreshError("invalid_bar_value")
    return number


def _safe_int(value: object, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise PortalRefreshError("invalid_metadata")
    return value


def _safe_text(value: object, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise PortalRefreshError("invalid_metadata")
    return value


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open("a+b") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_bytes(path: Path, payload: bytes, *, ignore_post_fsync_error: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        try:
            _fsync_directory(path.parent)
        except OSError:
            if not ignore_post_fsync_error:
                raise
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_json(
    path: Path,
    payload: Mapping[str, Any],
    *,
    ignore_post_fsync_error: bool = False,
) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _atomic_bytes(path, encoded, ignore_post_fsync_error=ignore_post_fsync_error)


def _read_json(path: Path, maximum: int) -> Any:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
        raise PortalRefreshError("snapshot_corrupt")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PortalRefreshError("snapshot_corrupt") from exc


def _snapshot_content_hash(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    metadata: Sequence[Mapping[str, Any]],
) -> str:
    payload = {"rows": rows, "metadata": list(metadata)}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest_token(
    target_date: str,
    symbols: Sequence[str],
    universe_token: str,
    content_sha256: str,
    generation_nonce: str,
) -> str:
    value = "\n".join((target_date, universe_token, "\n".join(symbols), content_sha256, generation_nonce))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_universe_token(value: object) -> str:
    if value is None:
        return ""
    if (
        not isinstance(value, str)
        or len(value) > 256
        or any(ord(char) < 32 for char in value)
        or any(separator in value for separator in ("/", "\\", ":"))
    ):
        raise PortalRefreshError("invalid_universe_token")
    return value


def _validate_layout(paths: PortalRefreshPaths) -> None:
    """Re-check the complete trusted path chain immediately before access."""

    _canonical(paths.user_data)
    _contained(paths.user_data, paths.state)
    _contained(paths.state, paths.root)
    _contained(paths.root, paths.current)
    _contained(paths.root, paths.generations)


def _rows_by_symbol(rows: Mapping[object, object], symbols: Sequence[str], target_date: str) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    if set(_normalize_code(key) for key in rows) != set(symbols):
        raise PortalRefreshError("symbol_set_mismatch")
    for raw_symbol, raw_rows in rows.items():
        symbol = _normalize_code(raw_symbol)
        if symbol is None or symbol not in symbols or not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
            raise PortalRefreshError("invalid_bars")
        if not raw_rows or len(raw_rows) > MAX_ROWS_PER_SYMBOL:
            raise PortalRefreshError("invalid_bars")
        seen: set[tuple[str, str]] = set()
        entries: list[dict[str, Any]] = []
        target_count = 0
        for raw in raw_rows:
            if not isinstance(raw, Mapping) or set(raw) != _ROW_KEYS:
                raise PortalRefreshError("invalid_bar_schema")
            date = _date_text(raw.get("date"))
            if date is None or date != target_date or raw.get("adjust") != "qfq":
                raise PortalRefreshError("invalid_bar_value")
            code = _normalize_code(raw.get("code"))
            if code != symbol:
                raise PortalRefreshError("symbol_set_mismatch")
            key = (date, "qfq")
            if key in seen:
                raise PortalRefreshError("duplicate_bar")
            seen.add(key)
            values = {name: _safe_number(raw.get(name)) for name in ("open", "high", "low", "close", "volume")}
            if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
                raise PortalRefreshError("invalid_bar_value")
            if date == target_date:
                target_count += 1
            entries.append({"code": _market_code(symbol), "date": date, **values, "adjust": "qfq"})
        if target_count != 1 or len(entries) != 1:
            raise PortalRefreshError("target_date_incomplete")
        normalized[symbol] = sorted(entries, key=lambda item: item["date"])
    if set(normalized) != set(symbols):
        raise PortalRefreshError("symbol_set_mismatch")
    return normalized


def _metadata_by_symbol(metadata: Mapping[object, object] | Sequence[object], symbols: Sequence[str], target_date: str) -> list[dict[str, Any]]:
    values: list[object]
    if isinstance(metadata, Mapping):
        values = list(metadata.values())
    elif isinstance(metadata, Sequence) and not isinstance(metadata, (str, bytes)):
        values = list(metadata)
    else:
        raise PortalRefreshError("invalid_metadata")
    if len(values) != len(symbols):
        raise PortalRefreshError("metadata_set_mismatch")
    output: dict[str, dict[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, Mapping) or set(raw) - _METADATA_KEYS:
            raise PortalRefreshError("invalid_metadata_schema")
        code = _normalize_code(raw.get("code"))
        if code is None or code not in symbols or code in output:
            raise PortalRefreshError("metadata_set_mismatch")
        exchange = str(raw.get("exchange") or ("SH" if code.startswith(("6", "9")) else "SZ")).upper()
        if exchange not in {"SH", "SZ"}:
            raise PortalRefreshError("invalid_metadata")
        listed = raw.get("listed", True)
        suspended = raw.get("suspended", False)
        risk_warning = raw.get("risk_warning")
        if not isinstance(listed, bool) or not isinstance(suspended, bool):
            raise PortalRefreshError("invalid_metadata")
        if risk_warning is not None:
            risk_warning = _safe_text(risk_warning, maximum=64)
        tradable = raw.get("tradable", listed and not suspended and not risk_warning)
        if not isinstance(tradable, bool) or tradable != (listed and not suspended and not risk_warning):
            raise PortalRefreshError("invalid_metadata")
        latest = raw.get("latest_trade_date", target_date)
        if latest != target_date:
            raise PortalRefreshError("metadata_date_mismatch")
        rows = _safe_int(raw.get("history_rows", 1), minimum=1)
        computable = raw.get("computable", True)
        if not isinstance(computable, bool):
            raise PortalRefreshError("invalid_metadata")
        reason = raw.get("eligible_reason")
        if reason is not None:
            reason = _safe_text(reason, maximum=64)
        output[code] = {
            "code": code,
            "name": _safe_text(raw.get("name", code)),
            "exchange": exchange,
            "risk_warning": risk_warning,
            "suspended": suspended,
            "listed": listed,
            "tradable": tradable,
            "history_rows": rows,
            "latest_trade_date": target_date,
            "computable": computable,
            "eligible_reason": reason,
        }
    if set(output) != set(symbols):
        raise PortalRefreshError("metadata_set_mismatch")
    return [output[symbol] for symbol in symbols]


def _metadata_payload(generation: str, target_date: str, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "schema": METADATA_SCHEMA,
        "generation": generation,
        "target_date": target_date,
        "items": [dict(item) for item in items],
    }


def _write_database(path: Path, rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(_DB_CREATE_SQL)
        for symbol_rows in rows.values():
            connection.executemany(
                "INSERT INTO daily_bar (code,date,open,high,low,close,volume,adjust) VALUES (?,?,?,?,?,?,?,?)",
                [tuple(row[key] for key in ("code", "date", "open", "high", "low", "close", "volume", "adjust")) for row in symbol_rows],
            )
        connection.commit()
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.rollback()
        raise PortalRefreshError("mirror_write_failed") from exc
    finally:
        if connection is not None:
            connection.close()
    _fsync_file(path)


def _normalize_sql(value: object) -> str:
    return " ".join(str(value or "").split()).lower()


def _read_database_rows(
    path: Path,
    target_date: str,
    symbols: Sequence[str],
) -> dict[str, list[dict[str, Any]]] | None:
    connection: sqlite3.Connection | None = None
    try:
        _canonical(path)
        if not path.is_file() or path.is_symlink():
            return None
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.5)
        connection.execute("PRAGMA query_only=ON")
        objects = connection.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('table','view','trigger') ORDER BY type,name"
        ).fetchall()
        if objects != [("table", "daily_bar")]:
            return None
        explicit_indexes = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall()
        if explicit_indexes:
            return None
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("daily_bar",),
        ).fetchone()
        if table_sql is None or _normalize_sql(table_sql[0]) != _normalize_sql(_DB_CREATE_SQL):
            return None
        columns = tuple(
            (str(row[1]).lower(), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in connection.execute("PRAGMA table_info(daily_bar)")
        )
        if columns != _DB_COLUMNS:
            return None
        result = connection.execute(
            "SELECT code,date,open,high,low,close,volume,adjust "
            "FROM daily_bar ORDER BY code,date,adjust"
        ).fetchall()
        expected = {_market_code(symbol) for symbol in symbols}
        if len(result) != len(expected):
            return None
        rows: dict[str, list[dict[str, Any]]] = {}
        for code, date, opn, high, low, close, volume, adjust in result:
            if code not in expected or _date_text(date) != target_date or adjust != "qfq":
                return None
            values = (opn, high, low, close, volume)
            if any(not math.isfinite(float(value)) or float(value) <= 0 for value in values):
                return None
            symbol = _normalize_code(code)
            if symbol is None or symbol in rows:
                return None
            rows[symbol] = [{
                "code": str(code),
                "date": str(date),
                "open": float(opn),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
                "adjust": str(adjust),
            }]
        if set(rows) != set(symbols):
            return None
        return rows
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return None
    finally:
        if connection is not None:
            connection.close()


def _validate_database(path: Path, target_date: str, symbols: Sequence[str]) -> bool:
    return _read_database_rows(path, target_date, symbols) is not None


def _valid_metadata_payload(payload: object, generation: str, target_date: str, symbols: Sequence[str]) -> list[dict[str, Any]] | None:
    if not isinstance(payload, Mapping) or set(payload) != {"schema_version", "schema", "generation", "target_date", "items"}:
        return None
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("schema") != METADATA_SCHEMA:
        return None
    if payload.get("generation") != generation or payload.get("target_date") != target_date:
        return None
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != len(symbols):
        return None
    try:
        normalized = _metadata_by_symbol(items, symbols, target_date)
    except PortalRefreshError:
        return None
    return normalized


def _valid_manifest(payload: object, generation: str | None = None) -> bool:
    if not isinstance(payload, Mapping) or set(payload) != _MANIFEST_KEYS:
        return False
    token = payload.get("token")
    symbols = payload.get("symbols")
    target = payload.get("target_date")
    if payload.get("schema_version") != SCHEMA_VERSION or payload.get("state") != "complete":
        return False
    if generation is not None and payload.get("generation") != generation:
        return False
    if payload.get("generation") != token or _TOKEN_RE.fullmatch(str(token)) is None:
        return False
    if _date_text(target) is None or payload.get("as_of") != target:
        return False
    if not isinstance(payload.get("universe_token"), str) or len(payload["universe_token"]) > 256:
        return False
    try:
        if _normalize_universe_token(payload["universe_token"]) != payload["universe_token"]:
            return False
    except PortalRefreshError:
        return False
    nonce = payload.get("generation_nonce")
    content_sha256 = payload.get("content_sha256")
    if _NONCE_RE.fullmatch(str(nonce)) is None or _TOKEN_RE.fullmatch(str(content_sha256)) is None:
        return False
    if not isinstance(symbols, list) or not MIN_SYMBOLS <= len(symbols) <= MAX_SYMBOLS:
        return False
    if any(_normalize_code(symbol) != symbol for symbol in symbols) or len(set(symbols)) != len(symbols):
        return False
    if _manifest_token(target, symbols, payload["universe_token"], content_sha256, nonce) != token:
        return False
    total = payload.get("total")
    if total != len(symbols) or payload.get("completed") != total:
        return False
    expected_db_path = f"generations/{token}/bars_incr.db"
    expected_metadata_path = f"generations/{token}/metadata.json"
    if payload.get("db_path") != expected_db_path or payload.get("metadata_path") != expected_metadata_path:
        return False
    if payload.get("db_schema") != DB_SCHEMA or payload.get("metadata_schema") != METADATA_SCHEMA:
        return False
    for name in ("db_sha256", "metadata_sha256"):
        if not isinstance(payload.get(name), str) or re.fullmatch(r"[0-9a-f]{64}", payload[name]) is None:
            return False
    return all(isinstance(payload.get(name), int) and payload[name] > 0 for name in ("db_size", "metadata_size"))


def _safe_relative(path_value: object, expected: str) -> bool:
    return isinstance(path_value, str) and path_value == expected and not Path(path_value).is_absolute() and ".." not in Path(path_value).parts


def _valid_pointer(payload: object) -> bool:
    if not isinstance(payload, Mapping) or set(payload) != _POINTER_KEYS:
        return False
    if payload.get("schema_version") != SCHEMA_VERSION:
        return False
    token = payload.get("token")
    if payload.get("generation") != token or _TOKEN_RE.fullmatch(str(token)) is None:
        return False
    if _date_text(payload.get("target_date")) is None:
        return False
    if not isinstance(payload.get("universe_token"), str) or len(payload["universe_token"]) > 256:
        return False
    try:
        if _normalize_universe_token(payload["universe_token"]) != payload["universe_token"]:
            return False
    except PortalRefreshError:
        return False
    if _NONCE_RE.fullmatch(str(payload.get("generation_nonce"))) is None:
        return False
    if _TOKEN_RE.fullmatch(str(payload.get("content_sha256"))) is None:
        return False
    total = payload.get("total")
    if not isinstance(total, int) or not MIN_SYMBOLS <= total <= MAX_SYMBOLS:
        return False
    for name in ("db_sha256", "manifest_sha256", "metadata_sha256"):
        if not isinstance(payload.get(name), str) or re.fullmatch(r"[0-9a-f]{64}", payload[name]) is None:
            return False
    expected_db_path = f"generations/{token}/bars_incr.db"
    expected_metadata_path = f"generations/{token}/metadata.json"
    if payload.get("db_path") != expected_db_path or payload.get("metadata_path") != expected_metadata_path:
        return False
    return all(isinstance(payload.get(name), int) and payload[name] > 0 for name in ("db_size", "metadata_size"))


def read_current_snapshot(
    state_dir: str | Path | None = None,
    *,
    user_data_dir: str | Path | None = None,
) -> PortalSnapshot | None:
    """Return only a fully verified current generation, never a partial one."""

    try:
        paths = portal_refresh_paths(state_dir, user_data_dir=user_data_dir)
        _validate_layout(paths)
        if not paths.root.is_dir() or not paths.generations.is_dir():
            return None
        root_entries = list(paths.root.iterdir())
        root_names = {entry.name for entry in root_entries}
        if not {"current.json", "generations"}.issubset(root_names):
            return None
        for entry in root_entries:
            if entry.name not in {"current.json", "generations"} and _POINTER_TEMP_RE.fullmatch(entry.name) is None:
                return None
            if entry.is_symlink() or _contained(paths.root, entry) != entry:
                return None
            if entry.name == "current.json" and not entry.is_file():
                return None
            if entry.name == "generations" and not entry.is_dir():
                return None
            if _POINTER_TEMP_RE.fullmatch(entry.name) and not entry.is_file():
                return None
        generation_entries = list(paths.generations.iterdir())
        for entry in generation_entries:
            if entry.is_symlink() or _contained(paths.generations, entry) != entry:
                return None
            if _TOKEN_RE.fullmatch(entry.name) is not None:
                if not entry.is_dir():
                    return None
                continue
            if _STAGING_RE.fullmatch(entry.name) is not None:
                if not entry.is_dir():
                    return None
                continue
            return None
        pointer = _read_json(paths.current, MAX_POINTER_BYTES)
        if not _valid_pointer(pointer):
            return None
        generation = _validate_token(pointer["generation"])
        generation_dir = paths.generation_dir(generation)
        expected_db_path = f"generations/{generation}/bars_incr.db"
        expected_metadata_path = f"generations/{generation}/metadata.json"
        if not _safe_relative(pointer.get("db_path"), expected_db_path) or not _safe_relative(pointer.get("metadata_path"), expected_metadata_path):
            return None
        _canonical(generation_dir)
        if not generation_dir.is_dir():
            return None
        entries = list(generation_dir.iterdir())
        if {entry.name for entry in entries} != {"bars_incr.db", "metadata.json", "manifest.json"}:
            return None
        if any(entry.is_symlink() for entry in entries):
            return None
        manifest_path = _contained(generation_dir, paths.generation_manifest(generation))
        manifest = _read_json(manifest_path, MAX_MANIFEST_BYTES)
        if not _valid_manifest(manifest, generation) or _hash_file(manifest_path) != pointer["manifest_sha256"]:
            return None
        pointer_fields = (
            "generation", "target_date", "token", "total", "universe_token", "generation_nonce",
            "content_sha256", "db_path", "db_size", "db_sha256", "metadata_path", "metadata_size",
            "metadata_sha256",
        )
        if any(manifest.get(field) != pointer.get(field) for field in pointer_fields):
            return None
        database = _contained(generation_dir, paths.generation_db(generation))
        metadata_path = _contained(generation_dir, paths.generation_metadata(generation))
        if not database.is_file() or not metadata_path.is_file() or database.is_symlink() or metadata_path.is_symlink():
            return None
        if database.stat().st_size != manifest["db_size"] or _hash_file(database) != manifest["db_sha256"]:
            return None
        if metadata_path.stat().st_size != manifest["metadata_size"] or _hash_file(metadata_path) != manifest["metadata_sha256"]:
            return None
        metadata_payload = _read_json(metadata_path, MAX_METADATA_BYTES)
        metadata = _valid_metadata_payload(metadata_payload, generation, manifest["target_date"], manifest["symbols"])
        database_rows = _read_database_rows(database, manifest["target_date"], manifest["symbols"])
        if metadata is None or database_rows is None:
            return None
        if _snapshot_content_hash(database_rows, metadata) != manifest["content_sha256"]:
            return None
        if pointer["db_size"] != manifest["db_size"] or pointer["db_sha256"] != manifest["db_sha256"]:
            return None
        if pointer["metadata_size"] != manifest["metadata_size"] or pointer["metadata_sha256"] != manifest["metadata_sha256"]:
            return None
        return PortalSnapshot(dict(manifest), database, tuple(dict(item) for item in metadata))
    except (OSError, PortalRefreshError, ValueError, sqlite3.Error):
        return None


def read_complete_manifest(
    state_dir: str | Path | None = None,
    *,
    user_data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    snapshot = read_current_snapshot(state_dir, user_data_dir=user_data_dir)
    return dict(snapshot.manifest) if snapshot is not None else None


def publish_snapshot(
    symbols: Iterable[object],
    target_date: str,
    rows: Mapping[object, object],
    metadata: Mapping[object, object] | Sequence[object],
    *,
    state_dir: str | Path | None = None,
    user_data_dir: str | Path | None = None,
    universe_token: str | None = None,
) -> PortalSnapshot:
    """Publish a complete typed snapshot and atomically advance ``current``."""

    target = _date_text(target_date)
    if target is None:
        raise PortalRefreshError("invalid_target_date")
    normalized_symbols = _normalize_symbols(symbols)
    if not MIN_SYMBOLS <= len(normalized_symbols) <= MAX_SYMBOLS:
        raise PortalRefreshError("invalid_symbol_count")
    trusted_token = _normalize_universe_token(universe_token)
    normalized_rows = _rows_by_symbol(rows, normalized_symbols, target)
    normalized_metadata = _metadata_by_symbol(metadata, normalized_symbols, target)
    content_sha256 = _snapshot_content_hash(normalized_rows, normalized_metadata)
    paths = portal_refresh_paths(state_dir, user_data_dir=user_data_dir)
    _validate_layout(paths)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.generations.mkdir(parents=True, exist_ok=True)
    _validate_layout(paths)
    current = read_current_snapshot(state_dir, user_data_dir=user_data_dir)
    if (
        current is not None
        and current.manifest.get("target_date") == target
        and current.manifest.get("symbols") == normalized_symbols
        and current.manifest.get("universe_token") == trusted_token
        and current.manifest.get("content_sha256") == content_sha256
    ):
        return current
    generation_nonce = uuid.uuid4().hex
    generation = _manifest_token(
        target,
        normalized_symbols,
        trusted_token,
        content_sha256,
        generation_nonce,
    )
    final_dir = paths.generation_dir(generation)
    if final_dir.exists():
        raise PortalRefreshError("generation_conflict")
    staging = _contained(paths.generations, paths.generations / f".staging-{uuid.uuid4().hex}")
    staging.mkdir()
    try:
        database = _contained(staging, staging / "bars_incr.db")
        metadata_path = _contained(staging, staging / "metadata.json")
        manifest_path = _contained(staging, staging / "manifest.json")
        _write_database(database, normalized_rows)
        metadata_payload = _metadata_payload(generation, target, normalized_metadata)
        _atomic_json(metadata_path, metadata_payload)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "state": "complete",
            "generation": generation,
            "target_date": target,
            "token": generation,
            "universe_token": trusted_token,
            "generation_nonce": generation_nonce,
            "content_sha256": content_sha256,
            "symbols": normalized_symbols,
            "total": len(normalized_symbols),
            "completed": len(normalized_symbols),
            "as_of": target,
            "db_path": f"generations/{generation}/bars_incr.db",
            "db_schema": DB_SCHEMA,
            "db_size": database.stat().st_size,
            "db_sha256": _hash_file(database),
            "metadata_path": f"generations/{generation}/metadata.json",
            "metadata_schema": METADATA_SCHEMA,
            "metadata_size": metadata_path.stat().st_size,
            "metadata_sha256": _hash_file(metadata_path),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _atomic_json(manifest_path, manifest)
        if not _valid_manifest(manifest, generation) or not _validate_database(database, target, normalized_symbols):
            raise PortalRefreshError("manifest_validation_failed")
        os.replace(staging, final_dir)
        _fsync_directory(paths.generations)
        pointer = {
            "schema_version": SCHEMA_VERSION,
            "generation": generation,
            "target_date": target,
            "token": generation,
            "total": len(normalized_symbols),
            "universe_token": trusted_token,
            "generation_nonce": generation_nonce,
            "content_sha256": content_sha256,
            "db_path": manifest["db_path"],
            "db_size": manifest["db_size"],
            "db_sha256": manifest["db_sha256"],
            "manifest_sha256": _hash_file(final_dir / "manifest.json"),
            "metadata_path": manifest["metadata_path"],
            "metadata_size": manifest["metadata_size"],
            "metadata_sha256": manifest["metadata_sha256"],
        }
        _atomic_json(_contained(paths.root, paths.current), pointer, ignore_post_fsync_error=True)
    except PortalRefreshError:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise PortalRefreshError("mirror_publish_failed") from exc
    finally:
        try:
            safe_staging = _contained(paths.generations, staging)
            if safe_staging.is_dir() and not safe_staging.is_symlink():
                for child in safe_staging.iterdir():
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                safe_staging.rmdir()
        except (OSError, ValueError):
            pass
    snapshot = read_current_snapshot(state_dir, user_data_dir=user_data_dir)
    if snapshot is None or snapshot.manifest.get("token") != generation:
        raise PortalRefreshError("mirror_publish_failed")
    return snapshot


__all__ = [
    "DB_SCHEMA",
    "MAX_SYMBOLS",
    "METADATA_SCHEMA",
    "MIN_SYMBOLS",
    "PortalRefreshError",
    "PortalRefreshPaths",
    "PortalSnapshot",
    "portal_refresh_paths",
    "publish_snapshot",
    "read_complete_manifest",
    "read_current_snapshot",
]
