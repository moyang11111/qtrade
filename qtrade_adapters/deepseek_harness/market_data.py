"""Read-only mainboard market-data adapter owned by QTrade.

The adapter consumes the optional DeepSeek HARNESS SQLite cache without
copying it into QTrade or making network requests.  See
``THIRD_PARTY_NOTICES.md`` for the upstream attribution and boundary.
"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from pathlib import Path
import re
import sqlite3

import pandas as pd

from .config import resolve_base_dir


MIN_HISTORY_ROWS = 130
_DATABASE_FILES = ("bars.db", "bars_incr.db")
_MAINBOARD_EXCHANGES = frozenset({"SH", "SZ"})
_LISTED_VALUES = frozenset({"1", "active", "listed", "normal", "上市", "正常", "true"})
_RISK_VALUES = frozenset({"1", "true", "yes", "st", "risk", "风险警示"})
_SUSPENDED_VALUES = frozenset({"1", "true", "yes", "suspend", "suspended", "停牌"})
_EQUITY_VALUES = frozenset({
    "a-share", "a股", "common", "common_stock", "equity", "stock", "股票", "普通股",
})
_NON_EQUITY_HINTS = re.compile(r"ETF|INDEX|LOF|基金|指数", re.IGNORECASE)
_CANONICAL_BAR_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def normalize_code(value: object) -> str | None:
    """Normalize a metadata or API code to six digits without guessing assets."""

    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not text.isdigit():
        return None
    return text.zfill(6) if len(text) <= 6 else None


def _exchange_from_code(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if "." in text:
        suffix = text.rsplit(".", 1)[1]
        if suffix in _MAINBOARD_EXCHANGES:
            return suffix
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix):
            return prefix if prefix in _MAINBOARD_EXCHANGES else None
    return None


def _normalize_exchange(value: object) -> str | None:
    text = str(value or "").strip().upper()
    aliases = {"SSE": "SH", "SZSE": "SZ", "SH": "SH", "SZ": "SZ"}
    return aliases.get(text)


def _quote_identifier(identifier: str) -> str:
    """Quote an identifier obtained from SQLite schema introspection."""

    return '"' + str(identifier).replace('"', '""') + '"'


def _truthy(value: object, values: frozenset[str]) -> bool:
    return str(value or "").strip().lower() in values


def _is_mainboard(code: str, exchange: str | None) -> bool:
    if exchange == "SH":
        return code.startswith("60") and not code.startswith("68")
    if exchange == "SZ":
        return code.startswith("00")
    return False


class MainboardMarketDataAdapter:
    """Read-only metadata/history view over the optional HARNESS databases."""

    def __init__(
        self,
        base_dir: str | Path | None = None,
        csv_dir: str | Path | None = None,
        min_history: int = MIN_HISTORY_ROWS,
        overlay_db: str | Path | None = None,
        overlay_only: bool = False,
        overlay_manifest: dict | None = None,
        overlay_metadata: list[dict] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else resolve_base_dir()
        self.csv_dir = Path(csv_dir) if csv_dir is not None else None
        self.min_history = int(min_history)
        self.overlay_db = Path(overlay_db).expanduser() if overlay_db is not None else None
        self.overlay_only = bool(overlay_only)
        self.overlay_manifest = dict(overlay_manifest or {})
        self.overlay_metadata = [dict(item) for item in (overlay_metadata or [])]
        self.last_error: str | None = None
        self._snapshot_key: str | None = None
        self._records: list[dict] = []
        self._record_by_code: dict[str, dict] = {}
        self._coverage: dict[str, dict] = {}
        self._ready = False
        self._history_cache: dict[tuple[str, int, str], pd.DataFrame] = {}

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / "data" / "cache"

    def _db_path(self, filename: str) -> Path:
        return self.cache_dir / filename

    def _bar_paths(self) -> list[Path]:
        if self.overlay_only:
            return [self.overlay_db] if self.overlay_db is not None else []
        paths = [self._db_path(filename) for filename in _DATABASE_FILES]
        if self.overlay_db is not None:
            paths.append(self.overlay_db)
        return paths

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        """Open a database using SQLite URI mode=ro and query_only."""

        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=0.5,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _find_table(
        connection: sqlite3.Connection,
        expected: str,
        required: tuple[str, ...],
        optional: tuple[str, ...] = (),
    ) -> dict | None:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ? AND lower(name) = ?",
            ("table", expected.lower()),
        ).fetchone()
        if row is None:
            return None
        table_name = str(row[0])
        pragma_rows = connection.execute(
            "PRAGMA table_info(" + _quote_identifier(table_name) + ")"
        ).fetchall()
        columns = {str(item[1]).lower(): str(item[1]) for item in pragma_rows}
        if any(name not in columns for name in required):
            return None
        schema = {"table": table_name}
        schema.update({name: columns[name] for name in required})
        schema.update({name: columns.get(name) for name in optional})
        return schema

    def _metadata_rows(self) -> list[dict] | None:
        if self.overlay_only:
            if self.overlay_db is None or not self.overlay_db.is_file():
                self.last_error = "mirror_missing"
                return None
            if self.overlay_metadata:
                return [
                    {
                        **dict(item),
                        "out_date": "",
                        "status": "1",
                        "security_type": "stock",
                    }
                    for item in self.overlay_metadata
                ]
            symbols = self.overlay_manifest.get("symbols")
            if not isinstance(symbols, list) or not symbols:
                self.last_error = "mirror_metadata_missing"
                return None
            return [
                {
                    "code": str(code),
                    "name": str(code),
                    "out_date": "",
                    "status": "1",
                    "security_type": "stock",
                    "exchange": "SH" if str(code).startswith(("6", "9")) else "SZ",
                }
                for code in symbols
            ]
        path = self._db_path("stock_basic.db")
        if not path.exists():
            self.last_error = "metadata_missing"
            return None
        optional = (
            "industry", "ipo_date", "security_type", "asset_type", "type",
            "instrument_type", "exchange", "market", "suspended", "is_suspended",
            "trade_status", "trading_status",
        )
        try:
            with closing(self._connect(path)) as connection:
                schema = self._find_table(
                    connection,
                    "stock_basic",
                    ("code", "name", "out_date", "status"),
                    optional,
                )
                if schema is None:
                    self.last_error = "metadata_schema_unsupported"
                    return None
                selected = ["code", "name", "out_date", "status"]
                selected.extend(name for name in optional if schema.get(name))
                columns = ", ".join(
                    _quote_identifier(schema[name]) + " AS " + _quote_identifier(name)
                    for name in selected
                )
                query = (
                    "SELECT " + columns + " FROM " + _quote_identifier(schema["table"])
                    + " ORDER BY " + _quote_identifier(schema["code"])
                )
                return [dict(row) for row in connection.execute(query).fetchall()]
        except (OSError, sqlite3.Error):
            self.last_error = "metadata_read_error"
            return None

    def _bar_schema(self, connection: sqlite3.Connection) -> dict | None:
        return self._find_table(
            connection,
            "daily_bar",
            ("code", "date", "open", "high", "low", "close", "volume", "adjust"),
        )

    def _coverage_rows(self, path: Path) -> list[dict] | None:
        if not path.exists():
            return []
        try:
            with closing(self._connect(path)) as connection:
                bar_schema = self._bar_schema(connection)
                if bar_schema is None:
                    self.last_error = "bars_schema_unsupported"
                    return None
                meta_schema = self._find_table(
                    connection,
                    "bar_meta",
                    ("code", "adjust", "rows", "end_date"),
                )
                if meta_schema is not None:
                    columns = ", ".join(
                        _quote_identifier(meta_schema[key]) + " AS " + key
                        for key in ("code", "rows", "end_date")
                    )
                    query = (
                        "SELECT " + columns + " FROM " + _quote_identifier(meta_schema["table"])
                        + " WHERE " + _quote_identifier(meta_schema["adjust"]) + " = ?"
                    )
                    rows = connection.execute(query, ("qfq",)).fetchall()
                else:
                    columns = (
                        _quote_identifier(bar_schema["code"]) + " AS code, "
                        "COUNT(*) AS history_rows, MAX(" + _quote_identifier(bar_schema["date"])
                        + ") AS latest_trade_date"
                    )
                    query = (
                        "SELECT " + columns + " FROM " + _quote_identifier(bar_schema["table"])
                        + " WHERE " + _quote_identifier(bar_schema["adjust"]) + " = ? GROUP BY "
                        + _quote_identifier(bar_schema["code"])
                    )
                    rows = connection.execute(query, ("qfq",)).fetchall()
                return [dict(row) for row in rows]
        except (OSError, sqlite3.Error):
            self.last_error = "bars_read_error"
            return None

    def _coverage_rows_all(self) -> dict[str, dict] | None:
        coverage: dict[str, dict] = {}
        readable_database = False
        for path in self._bar_paths():
            rows = self._coverage_rows(path)
            if rows is None:
                if path.exists():
                    return None
                continue
            if path.exists():
                readable_database = True
            for row in rows:
                code = normalize_code(row.get("code"))
                if code is None:
                    continue
                item = coverage.setdefault(code, {"history_rows": 0, "latest_trade_date": None})
                count = row.get("history_rows", row.get("rows"))
                item["history_rows"] = max(item["history_rows"], int(count or 0))
                latest = row.get("latest_trade_date", row.get("end_date"))
                if latest and (
                    item["latest_trade_date"] is None
                    or str(latest) > str(item["latest_trade_date"])
                ):
                    item["latest_trade_date"] = str(latest)[:10]
        if not readable_database:
            self.last_error = self.last_error or "bars_missing"
            return None
        return coverage

    def snapshot_token(self) -> str:
        """Return a stable DB-version token used to avoid repeated full scans."""

        parts = [f"overlay={self.overlay_manifest.get('token', '')}"] if self.overlay_only else []
        paths = list(self._bar_paths()) if self.overlay_only else [self._db_path("stock_basic.db"), *self._bar_paths()]
        for path in paths:
            try:
                stat = path.stat()
                parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
            except OSError:
                parts.append(f"{path.name}:missing")
        latest = max(
            (
                str(item["latest_trade_date"])
                for item in self._coverage.values()
                if item.get("latest_trade_date")
            ),
            default="",
        )
        return "|".join(parts) + "|latest=" + latest

    def _load_snapshot(self) -> None:
        token = self.snapshot_token()
        if token == self._snapshot_key:
            return
        metadata = self._metadata_rows()
        coverage = self._coverage_rows_all()
        self._records = []
        self._record_by_code = {}
        self._coverage = coverage or {}
        self._ready = metadata is not None and coverage is not None
        self._history_cache.clear()
        if not self._ready:
            self._snapshot_key = token
            return

        for row in metadata:
            code = normalize_code(row.get("code"))
            exchange = _normalize_exchange(row.get("exchange")) or _exchange_from_code(row.get("code"))
            if code is None or not _is_mainboard(code, exchange):
                continue
            if not self._is_listed(row):
                continue
            security_type = self._security_type(row)
            name = str(row.get("name") or "")
            if security_type and security_type not in _EQUITY_VALUES:
                continue
            if security_type is None and _NON_EQUITY_HINTS.search(name):
                continue
            coverage_item = self._coverage.get(code, {})
            history_rows = int(coverage_item.get("history_rows") or 0)
            risk_warning = self._risk_warning(name, row)
            suspended = self._suspended(row)
            reason = "risk_warning" if risk_warning else None
            if suspended:
                reason = "suspended"
            if history_rows < self.min_history and reason is None:
                reason = "history_insufficient"
            record = {
                "code": code,
                "name": name,
                "exchange": exchange,
                "risk_warning": risk_warning,
                "listed": True,
                "suspended": suspended,
                "tradable": not risk_warning and not suspended,
                "latest_trade_date": coverage_item.get("latest_trade_date"),
                "history_rows": history_rows,
                "computable": history_rows >= self.min_history,
                "eligible_reason": reason,
                "source": "qtrade_mirror" if self.overlay_only else "external_sqlite",
            }
            self._records.append(record)
            self._record_by_code[code] = record
        # Coverage contributes the latest complete trade date to the token.  Set
        # the key after loading it so the first successful read is reusable.
        self._snapshot_key = self.snapshot_token()
        self.last_error = None

    @staticmethod
    def _is_listed(row: dict) -> bool:
        if str(row.get("out_date") or "").strip():
            return False
        return _truthy(row.get("status"), _LISTED_VALUES)

    @staticmethod
    def _security_type(row: dict) -> str | None:
        for key in ("security_type", "asset_type", "type", "instrument_type"):
            if row.get(key) is not None and str(row[key]).strip():
                return str(row[key]).strip().lower()
        return None

    @staticmethod
    def _risk_warning(name: str, row: dict) -> str | None:
        upper = name.upper()
        if re.search(r"(?:^|\s)\*?ST(?:\s|$|[\u4e00-\u9fff])", upper) or "风险警示" in name:
            return "ST"
        for key in ("trade_status", "trading_status"):
            value = str(row.get(key) or "").strip().lower()
            if value in _RISK_VALUES:
                return "risk_warning"
        return None

    @staticmethod
    def _suspended(row: dict) -> bool:
        for key in ("suspended", "is_suspended"):
            if row.get(key) is not None and _truthy(row[key], _SUSPENDED_VALUES):
                return True
        for key in ("trade_status", "trading_status"):
            value = str(row.get(key) or "").strip().lower()
            if value in _SUSPENDED_VALUES:
                return True
        return False

    @property
    def available(self) -> bool:
        self._load_snapshot()
        return self._ready

    def scan(self) -> list[str]:
        self._load_snapshot()
        return [record["code"] for record in self._records]

    def metadata(self, symbol: str) -> dict | None:
        self._load_snapshot()
        code = normalize_code(symbol)
        return self._record_by_code.get(code) if code else None

    def universe_summary(self, candidate_symbols: set[str] | None = None) -> dict:
        self._load_snapshot()
        if not self._ready:
            return {
                "total": 0,
                "computable": 0,
                "tradable": 0,
                "candidate": 0,
                "excluded_by_reason": {},
                "as_of": None,
                "source": "unavailable",
                "reason": self.last_error or "database_unavailable",
            }
        candidates = {normalize_code(symbol) for symbol in (candidate_symbols or set())}
        candidates.discard(None)
        excluded = Counter(
            record["eligible_reason"]
            for record in self._records
            if record.get("eligible_reason")
        )
        as_of = max(
            (record["latest_trade_date"] for record in self._records if record["latest_trade_date"]),
            default=None,
        )
        return {
            "total": len(self._records),
            "computable": sum(record["computable"] for record in self._records),
            "tradable": sum(record["tradable"] for record in self._records),
            "candidate": sum(
                record["code"] in candidates and record["computable"] and record["tradable"]
                for record in self._records
            ),
            "excluded_by_reason": dict(sorted(excluded.items())),
            "as_of": as_of,
            "source": "qtrade_mirror" if self.overlay_only else "external_sqlite",
        }

    def _database_code(self, symbol: str) -> str | None:
        code = normalize_code(symbol)
        if code is None:
            return None
        record = self.metadata(code)
        if record and record.get("exchange"):
            return f"{code}.{record['exchange']}"
        exchange = "SH" if code.startswith(("6", "9")) else "SZ"
        return f"{code}.{exchange}"

    def _fetch_history_rows(self, path: Path, database_code: str) -> list[dict] | None:
        if not path.exists():
            return []
        try:
            with closing(self._connect(path)) as connection:
                schema = self._bar_schema(connection)
                if schema is None:
                    return []
                columns = ", ".join(
                    _quote_identifier(schema[name]) + " AS " + _quote_identifier(name)
                    for name in _CANONICAL_BAR_COLUMNS
                )
                query = (
                    "SELECT " + columns + " FROM " + _quote_identifier(schema["table"])
                    + " WHERE " + _quote_identifier(schema["code"]) + " = ? AND "
                    + _quote_identifier(schema["adjust"]) + " = ? ORDER BY "
                    + _quote_identifier(schema["date"])
                )
                return [dict(row) for row in connection.execute(query, (database_code, "qfq"))]
        except (OSError, sqlite3.Error):
            self.last_error = "bars_read_error"
            return None

    def get_history(self, symbol: str, count: int = 320) -> pd.DataFrame | None:
        """Load qfq history, with bars_incr overriding bars on duplicate dates."""

        self._load_snapshot()
        database_code = self._database_code(symbol)
        if not self._ready or database_code is None:
            return None
        count = max(2, int(count or 320))
        cache_key = (database_code, count, self._snapshot_key or "")
        if cache_key in self._history_cache:
            return self._history_cache[cache_key].copy()
        merged: dict[str, dict] = {}
        for path in self._bar_paths():
            rows = self._fetch_history_rows(path, database_code)
            if rows is None:
                return None
            for row in rows:
                date = str(row.get("date") or "")[:10]
                if date:
                    merged[date] = row
        if not merged:
            return None
        frame = pd.DataFrame(sorted(merged.values(), key=lambda row: str(row["date"])))
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
        frame = frame.set_index("date")[["open", "high", "low", "close", "volume"]].tail(count)
        self._history_cache[cache_key] = frame
        return frame.copy()
