"""Trusted production inputs for the portal refresh worker.

This module is intentionally small and server-owned. It builds a plan from
the read-only mainboard adapter and uses fixed Tencent and Eastmoney AkShare
interfaces, with TDX as an unadjusted diagnostic. Callers cannot provide
symbols, dates, URLs, commands, or provider options.
"""

from __future__ import annotations

import datetime as _datetime
from contextlib import contextmanager
import hashlib
import json
import math
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
import time
from urllib.parse import urlsplit

from .market_data import MainboardMarketDataAdapter, normalize_code
from .portal_refresh import HISTORY_WINDOW
from .portal_refresh_worker import PortalRefreshPlan, _plan_universe_token


PROVIDER_VERSION = "akshare-em-tx-cache-qfq-v4"
_DATE_FORMAT = "%Y-%m-%d"
# Keep a generous finite bound so malformed calendar responses fail closed.
_MAX_CALENDAR_DATES = 20_000
_NETWORK_CONNECT_TIMEOUT = 10.0
_NETWORK_READ_TIMEOUT = 20.0
_NETWORK_TOTAL_TIMEOUT = 30.0
_MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024
_SAFE_METADATA_KEYS = (
    "code",
    "name",
    "exchange",
    "risk_warning",
    "suspended",
    "listed",
    "tradable",
    "history_rows",
    "latest_trade_date",
    "computable",
    "eligible_reason",
)


class PortalPlanError(RuntimeError):
    """A stable, non-sensitive plan construction failure."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class PortalHistoryError(RuntimeError):
    """A stable, non-sensitive history validation classification."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _bounded_response(response):
    """Consume a provider response through a small, fail-closed byte budget."""

    status = getattr(response, "status_code", 0)
    if isinstance(status, int) and 300 <= status < 400:
        response.close()
        raise RuntimeError("provider redirect rejected")
    chunks = []
    total = 0
    deadline = time.monotonic() + _NETWORK_TOTAL_TIMEOUT
    try:
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() >= deadline:
                raise RuntimeError("provider response timeout")
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_PROVIDER_RESPONSE_BYTES:
                raise RuntimeError("provider response too large")
            chunks.append(chunk)
        if time.monotonic() >= deadline:
            raise RuntimeError("provider response timeout")
        response._content = b"".join(chunks)
        response._content_consumed = True
        response.close()
        return response
    except Exception:
        response.close()
        raise


@contextmanager
def _akshare_network_guard():
    """Constrain requests used by the fixed AkShare calls in this child.

    The coordinator's owned child supplies the total deadline.  This local
    seam supplies connect/read deadlines, disables environment proxies, turns
    off redirects, and bounds every response body before AkShare sees it.
    """

    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("provider transport unavailable") from exc

    session_type = requests.sessions.Session
    original_request = session_type.request
    original_send = session_type.send

    def guarded_request(session, method, url, **kwargs):
        parsed = urlsplit(str(url))
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise RuntimeError("provider URL rejected")
        session.trust_env = False
        kwargs["proxies"] = {}
        kwargs["allow_redirects"] = False
        kwargs["timeout"] = (_NETWORK_CONNECT_TIMEOUT, _NETWORK_READ_TIMEOUT)
        kwargs["stream"] = True
        return original_request(session, method, url, **kwargs)

    def guarded_send(session, request, **kwargs):
        parsed = urlsplit(str(getattr(request, "url", "")))
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            raise RuntimeError("provider URL rejected")
        session.trust_env = False
        kwargs["allow_redirects"] = False
        kwargs["timeout"] = (_NETWORK_CONNECT_TIMEOUT, _NETWORK_READ_TIMEOUT)
        kwargs["stream"] = True
        return _bounded_response(original_send(session, request, **kwargs))

    session_type.request = guarded_request
    session_type.send = guarded_send
    try:
        yield
    finally:
        session_type.request = original_request
        session_type.send = original_send


def _date_text(value: object) -> str:
    if isinstance(value, _datetime.datetime):
        value = value.date()
    if isinstance(value, _datetime.date):
        return value.isoformat()
    if isinstance(value, str):
        try:
            return _datetime.date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            pass
    raise PortalPlanError("calendar_unavailable")


def _calendar_token(target: str, dates: Iterable[str]) -> str:
    canonical = sorted({_date_text(value) for value in dates})
    if not canonical or len(canonical) > _MAX_CALENDAR_DATES:
        raise PortalPlanError("calendar_unavailable")
    body = json.dumps(
        {"provider": "tx-em-index-calendar-v1", "dates": canonical},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(body).hexdigest()


def _load_trade_dates() -> list[str]:
    """Read recent exchange sessions from fixed Tencent/Eastmoney index histories."""

    try:
        import akshare as ak

        end_date = _datetime.date.today().strftime("%Y%m%d")
        # A fixed lower bound keeps checkpoint calendar tokens stable across
        # weekends; only a genuinely new exchange session changes the token.
        start_date = f"{_datetime.date.today().year - 2}0101"
        for source in ("tencent", "eastmoney"):
            try:
                with _akshare_network_guard():
                    frame = (
                        ak.stock_zh_index_daily_em(
                            symbol="sh000001", start_date=start_date, end_date=end_date
                        )
                        if source == "eastmoney"
                        else ak.stock_zh_index_daily_tx(
                            symbol="sh000001", start_date=start_date, end_date=end_date
                        )
                    )
                dates = [_date_text(value) for value in frame["date"].tolist()]
                if dates and len(dates) <= _MAX_CALENDAR_DATES:
                    return dates
            except Exception:
                continue
    except Exception as exc:
        raise PortalPlanError("calendar_unavailable") from exc
    raise PortalPlanError("calendar_unavailable")


def _safe_metadata(record: Mapping[str, object], target: str) -> dict[str, object]:
    code = normalize_code(record.get("code"))
    if code is None or not (code.startswith("60") or code.startswith("00")):
        raise PortalPlanError("universe_schema")
    values = {key: record.get(key) for key in _SAFE_METADATA_KEYS}
    values["code"] = code
    values["exchange"] = str(values.get("exchange") or "").upper()
    if values["exchange"] not in {"SH", "SZ"}:
        raise PortalPlanError("universe_schema")
    if values.get("latest_trade_date") not in {None, target}:
        # Metadata from the read-only universe is allowed to lag the target;
        # the provider result must still carry the target date.  Keep the
        # record safe and let the worker/publisher enforce the bar date.
        values["latest_trade_date"] = None
    values["listed"] = values.get("listed") is True
    values["suspended"] = values.get("suspended") is True
    values["risk_warning"] = (
        str(values["risk_warning"])[:64] if values.get("risk_warning") else None
    )
    values["tradable"] = bool(
        values["listed"] and not values["suspended"] and not values["risk_warning"]
    )
    values["name"] = str(values.get("name") or code)[:128]
    rows = values.get("history_rows")
    values["history_rows"] = rows if isinstance(rows, int) and rows > 0 else 1
    values["latest_trade_date"] = target
    values["computable"] = values.get("computable") is True
    reason = values.get("eligible_reason")
    values["eligible_reason"] = str(reason)[:64] if reason else None
    return values


class AksharePortalProvider:
    """Fetch qfq bars from Tencent, then Eastmoney; TDX checks raw availability."""

    PROVIDER_VERSION = PROVIDER_VERSION

    def __init__(self, metadata: Mapping[str, Mapping[str, object]], history_db: Path | None = None):
        self.metadata = {
            code: dict(record) for code, record in metadata.items()
        }
        self.history_db = history_db

    def _cached_rows(self, code: str, target_date: str) -> list[dict[str, object]]:
        if self.history_db is None:
            return []
        db = self.history_db.resolve()
        with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            suffix = ".SH" if code.startswith("6") else ".SZ"
            values = connection.execute(
                "SELECT date, open, high, low, close, volume FROM daily_bar "
                "WHERE code=? AND adjust='qfq' AND date<=? "
                "AND open>0 AND high>0 AND low>0 AND close>0 AND volume>0 "
                "AND high>=max(open,close) AND low<=min(open,close) "
                "ORDER BY date DESC LIMIT ?",
                (code + suffix, target_date, HISTORY_WINDOW),
            ).fetchall()
        return [
            {"code": code, "date": row[0], "open": row[1], "high": row[2],
             "low": row[3], "close": row[4], "volume": row[5], "adjust": "qfq"}
            for row in reversed(values)
        ]

    @staticmethod
    def _ak_symbol(symbol: str) -> str:
        code = normalize_code(symbol)
        if code is None or not (code.startswith("60") or code.startswith("00")):
            raise PortalPlanError("universe_schema")
        return ("sh" if code.startswith("60") else "sz") + code

    @staticmethod
    def _tdx_target_present(code: str, target_date: str) -> bool | None:
        """Use TDX's raw daily bar only as a diagnostic, never as qfq data."""

        try:
            from pytdx.hq import TdxHq_API
            for host, port in (("119.147.212.81", 7709), ("112.74.214.43", 7727), ("221.231.141.60", 7709)):
                api = TdxHq_API()
                try:
                    if not api.connect(host, port, time_out=2):
                        continue
                    rows = api.get_security_bars(4, 1 if code.startswith("6") else 0, code, 0, 800)
                    if rows:
                        return any(str(row.get("datetime", ""))[:10] == target_date for row in rows)
                except Exception:
                    continue
                finally:
                    api.disconnect()
        except Exception:
            pass
        return None

    def _source_rows(self, source: str, code: str, target_date: str, start_date: str) -> list[dict[str, object]]:
        import akshare as ak

        with _akshare_network_guard():
            if source == "eastmoney":
                frame = ak.stock_zh_a_hist(
                    symbol=code, period="daily", start_date=start_date,
                    end_date=target_date.replace("-", ""), adjust="qfq", timeout=20,
                )
            else:
                frame = ak.stock_zh_a_hist_tx(
                    symbol=self._ak_symbol(code), start_date=start_date,
                    end_date=target_date.replace("-", ""), adjust="qfq", timeout=20,
                )
        if frame is None or frame.empty:
            # Empty history is ambiguous (source outage versus new listing).
            # Never exclude a symbol from a publishable universe on that basis.
            raise RuntimeError("provider returned empty history")
        by_date: dict[str, dict[str, object]] = {}
        try:
            candidates = frame.to_dict(orient="records")
            for candidate in candidates:
                date = _date_text(candidate.get("日期" if source == "eastmoney" else "date"))
                if date > target_date:
                    continue
                values = {
                    "code": code,
                    "date": date,
                    "open": float(candidate["开盘" if source == "eastmoney" else "open"]),
                    "high": float(candidate["最高" if source == "eastmoney" else "high"]),
                    "low": float(candidate["最低" if source == "eastmoney" else "low"]),
                    "close": float(candidate["收盘" if source == "eastmoney" else "close"]),
                    # Eastmoney reports lots (100 shares); Tencent reports shares.
                    "volume": float(candidate["成交量" if source == "eastmoney" else "volume"])
                    * (100 if source == "eastmoney" else 1),
                    "adjust": "qfq",
                }
                if any(not math.isfinite(values[key]) or values[key] <= 0 for key in ("open", "high", "low", "close", "volume")):
                    raise ValueError("non-positive history")
                if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
                    raise ValueError("invalid history bar")
                by_date[date] = values
        except (KeyError, TypeError, ValueError, AttributeError, PortalPlanError) as exc:
            raise RuntimeError("provider schema invalid") from exc
        rows = [by_date[key] for key in sorted(by_date)]
        if not rows or rows[-1]["date"] != target_date:
            raise PortalHistoryError("target_date_missing")
        return rows

    def _fetch_rows(self, code: str, target_date: str, start_date: str, minimum: int, *, all_rows: bool = False) -> list[dict[str, object]]:
        quality_reasons: list[str] = []
        provider_failed = False
        for source in ("tencent", "eastmoney"):
            try:
                rows = self._source_rows(source, code, target_date, start_date)
                if len(rows) < minimum:
                    raise PortalHistoryError("insufficient_history")
                return rows if all_rows else rows[-minimum:]
            except PortalHistoryError as exc:
                quality_reasons.append(exc.reason)
            except Exception:
                provider_failed = True
        # TDX is intentionally not a qfq fallback. It can corroborate whether
        # a target-day raw bar exists, preventing a false 'suspended' exclusion.
        tdx_present = self._tdx_target_present(code, target_date)
        if provider_failed or (tdx_present is True and "target_date_missing" in quality_reasons):
            raise RuntimeError("all qfq providers failed")
        reason = "target_date_missing" if "target_date_missing" in quality_reasons else "insufficient_history"
        if self.metadata[code].get("suspended") is True:
            reason = "suspended"
        raise PortalHistoryError(reason)

    def fetch(self, symbol: str, target_date: str) -> dict[str, object]:
        code = normalize_code(symbol)
        if code is None or code not in self.metadata:
            raise RuntimeError("provider symbol unavailable")
        rows = self._fetch_rows(code, target_date, target_date.replace("-", ""), 1)
        return {"rows": rows, "metadata": dict(self.metadata[code])}

    def fetch_history(
        self,
        symbol: str,
        target_date: str,
        history_window: int = HISTORY_WINDOW,
    ) -> dict[str, object]:
        """Fetch the fixed target-anchored history required by the full pipeline."""

        if history_window != HISTORY_WINDOW:
            raise RuntimeError("provider history window rejected")
        code = normalize_code(symbol)
        if code is None or code not in self.metadata:
            raise RuntimeError("provider symbol unavailable")
        if self.history_db is None:
            target = _datetime.date.fromisoformat(target_date)
            start = target - _datetime.timedelta(days=600)
            rows = self._fetch_rows(code, target_date, start.strftime("%Y%m%d"), HISTORY_WINDOW)
            return {"rows": rows, "metadata": dict(self.metadata[code])}
        cached = self._cached_rows(code, target_date)
        if len(cached) < HISTORY_WINDOW:
            raise PortalHistoryError("insufficient_history")
        latest = _datetime.date.fromisoformat(str(cached[-1]["date"]))
        if latest >= _datetime.date.fromisoformat(target_date):
            rows = cached[-HISTORY_WINDOW:]
        else:
            # A short overlap detects stale qfq adjustment factors before merging.
            start = latest - _datetime.timedelta(days=7)
            fresh = self._fetch_rows(code, target_date, start.strftime("%Y%m%d"), 1, all_rows=True)
            old_by_date = {str(row["date"]): row for row in cached}
            overlaps = [row for row in fresh if row["date"] in old_by_date]
            if not overlaps:
                raise RuntimeError("qfq overlap unavailable")
            for row in overlaps:
                old = old_by_date[str(row["date"])]
                for field in ("open", "high", "low", "close"):
                    if not math.isclose(float(row[field]), float(old[field]), abs_tol=0.011, rel_tol=0):
                        raise RuntimeError("qfq overlap mismatch")
            merged = {str(row["date"]): row for row in cached}
            merged.update({str(row["date"]): row for row in fresh})
            rows = [merged[day] for day in sorted(merged)][-HISTORY_WINDOW:]
        if len(rows) != HISTORY_WINDOW or rows[-1]["date"] != target_date:
            raise PortalHistoryError("target_date_missing")
        return {"rows": rows, "metadata": dict(self.metadata[code])}


def _cache_history_counts(path: Path, target_date: str) -> dict[str, int]:
    """Count read-only qfq rows; missing cache is never interpreted as delisting."""
    db = path.resolve()
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT code, COUNT(*) FROM daily_bar WHERE adjust='qfq' AND date<=? "
            "AND open>0 AND high>0 AND low>0 AND close>0 AND volume>0 "
            "AND high>=max(open,close) AND low<=min(open,close) GROUP BY code",
            (target_date,),
        ).fetchall()
    return {str(code).split(".", 1)[0]: int(count) for code, count in rows}


def build_trusted_plan(
    *,
    base_dir: str | Path,
    target_date: str | _datetime.date,
    calendar_dates: Iterable[str] | None = None,
    calendar_loader: Callable[[], Iterable[str]] | None = None,
    adapter_factory: Callable[..., MainboardMarketDataAdapter] | None = None,
) -> tuple[PortalRefreshPlan, AksharePortalProvider]:
    """Create a plan only from server-owned calendar and universe inputs.

    ``calendar_dates``, ``calendar_loader`` and ``adapter_factory`` are test
    seams.  Production uses the fixed AkShare calendar and read-only adapter.
    """

    target = _date_text(target_date)
    parsed = _datetime.date.fromisoformat(target)
    if parsed.weekday() >= 5:
        raise PortalPlanError("weekend")
    dates = list(calendar_dates) if calendar_dates is not None else (
        list(calendar_loader()) if calendar_loader is not None else _load_trade_dates()
    )
    _calendar_token(target, dates)
    if target not in {_date_text(value) for value in dates}:
        raise PortalPlanError("calendar_closed")
    factory = adapter_factory or MainboardMarketDataAdapter
    try:
        adapter = factory(base_dir=base_dir)
        scanned_symbols = tuple(adapter.scan())
        if not 5 <= len(scanned_symbols) <= 5000 or len(set(scanned_symbols)) != len(scanned_symbols):
            raise PortalPlanError("universe_unavailable")
        metadata = {}
        excluded_by_reason: dict[str, int] = {}
        history_db = Path(base_dir) / "data" / "cache" / "bars.db"
        counts = _cache_history_counts(history_db, target) if adapter_factory is None else None
        for symbol in scanned_symbols:
            code = normalize_code(symbol)
            if code is None or code in metadata:
                raise PortalPlanError("universe_schema")
            record = adapter.metadata(code)
            if not isinstance(record, Mapping):
                raise PortalPlanError("universe_unavailable")
            safe = _safe_metadata(record, target)
            reason = (
                "risk_warning" if safe["risk_warning"]
                else "suspended" if safe["suspended"]
                else "not_tradable" if not safe["tradable"]
                else None
            )
            if reason is not None:
                excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
                continue
            if counts is not None:
                count = counts.get(code, 0)
                if count < HISTORY_WINDOW:
                    reason = "cache_missing" if count == 0 else "cache_insufficient_history"
                    excluded_by_reason[reason] = excluded_by_reason.get(reason, 0) + 1
                    continue
            metadata[code] = safe
    except PortalPlanError:
        raise
    except Exception as exc:
        raise PortalPlanError("universe_unavailable") from exc
    plan, provider = build_bound_plan(
        symbols=tuple(metadata),
        metadata=metadata,
        target_date=target,
        calendar_dates=dates,
    )
    if adapter_factory is None:
        provider.history_db = history_db
    return (
        PortalRefreshPlan(
            plan.symbols, plan.target_date, plan.universe_token,
            plan.calendar_verified, plan.calendar_token, plan.provider_version,
            tuple(sorted(excluded_by_reason.items())),
        ),
        provider,
    )


def build_bound_plan(
    *,
    symbols: Iterable[str],
    metadata: Mapping[str, Mapping[str, object]],
    target_date: str | _datetime.date,
    calendar_dates: Iterable[str] | None = None,
    calendar_loader: Callable[[], Iterable[str]] | None = None,
) -> tuple[PortalRefreshPlan, AksharePortalProvider]:
    """Build a plan from a server-bound universe, without reading a base dir."""

    target = _date_text(target_date)
    parsed = _datetime.date.fromisoformat(target)
    if parsed.weekday() >= 5:
        raise PortalPlanError("weekend")
    dates = list(calendar_dates) if calendar_dates is not None else (
        list(calendar_loader()) if calendar_loader is not None else _load_trade_dates()
    )
    calendar_token = _calendar_token(target, dates)
    if target not in {_date_text(value) for value in dates}:
        raise PortalPlanError("calendar_closed")
    ordered = tuple(normalize_code(symbol) for symbol in symbols)
    if (
        not 5 <= len(ordered) <= 5000
        or any(code is None for code in ordered)
        or len(set(ordered)) != len(ordered)
    ):
        raise PortalPlanError("universe_schema")
    safe_metadata: dict[str, dict[str, object]] = {}
    for code in ordered:
        record = metadata.get(code) if isinstance(metadata, Mapping) else None
        if not isinstance(record, Mapping):
            raise PortalPlanError("universe_unavailable")
        safe_metadata[code] = _safe_metadata(record, target)
    token = _plan_universe_token(ordered, target, calendar_token, PROVIDER_VERSION)
    return (
        PortalRefreshPlan(
            symbols=ordered,
            target_date=target,
            universe_token=token,
            calendar_verified=True,
            calendar_token=calendar_token,
            provider_version=PROVIDER_VERSION,
        ),
        AksharePortalProvider(safe_metadata),
    )


__all__ = [
    "AksharePortalProvider",
    "PortalPlanError",
    "PortalHistoryError",
    "PROVIDER_VERSION",
    "build_bound_plan",
    "build_trusted_plan",
]
