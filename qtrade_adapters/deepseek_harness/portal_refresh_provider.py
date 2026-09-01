"""Trusted production inputs for the portal refresh worker.

This module is intentionally small and server-owned.  It builds a plan from
the read-only mainboard adapter and uses one fixed AkShare function; callers
cannot provide symbols, dates, URLs, commands, or provider options.
"""

from __future__ import annotations

import datetime as _datetime
from contextlib import contextmanager
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
import time
from urllib.parse import urlsplit

from .market_data import MainboardMarketDataAdapter, normalize_code
from .portal_refresh_worker import PortalRefreshPlan, _plan_universe_token


PROVIDER_VERSION = "akshare-sina-daily-qfq-v1"
_DATE_FORMAT = "%Y-%m-%d"
_MAX_CALENDAR_DATES = 8_000
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
        {"provider": "sina-trade-calendar-v1", "dates": canonical},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(body).hexdigest()


def _load_trade_dates() -> list[str]:
    """Read the official AkShare trade calendar through its fixed API."""

    try:
        import akshare as ak

        with _akshare_network_guard():
            frame = ak.tool_trade_date_hist_sina()
        column = "trade_date" if "trade_date" in frame.columns else "date"
        if column not in frame.columns:
            raise PortalPlanError("calendar_unavailable")
        return [_date_text(value) for value in frame[column].tolist()]
    except PortalPlanError:
        raise
    except Exception as exc:
        raise PortalPlanError("calendar_unavailable") from exc


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
    """Fetch one target-day qfq bar through a fixed AkShare call."""

    PROVIDER_VERSION = PROVIDER_VERSION

    def __init__(self, metadata: Mapping[str, Mapping[str, object]]):
        self.metadata = {
            code: dict(record) for code, record in metadata.items()
        }

    @staticmethod
    def _ak_symbol(symbol: str) -> str:
        code = normalize_code(symbol)
        if code is None or not (code.startswith("60") or code.startswith("00")):
            raise PortalPlanError("universe_schema")
        return ("sh" if code.startswith("60") else "sz") + code

    def fetch(self, symbol: str, target_date: str) -> dict[str, object]:
        code = normalize_code(symbol)
        if code is None or code not in self.metadata:
            raise RuntimeError("provider symbol unavailable")
        try:
            import akshare as ak

            with _akshare_network_guard():
                frame = ak.stock_zh_a_daily(
                    symbol=self._ak_symbol(code),
                    start_date=target_date.replace("-", ""),
                    end_date=target_date.replace("-", ""),
                    adjust="qfq",
                )
        except Exception as exc:
            raise RuntimeError("provider request failed") from exc
        if frame is None or frame.empty:
            raise RuntimeError("provider returned no target bar")
        row = None
        for candidate in frame.to_dict(orient="records"):
            if _date_text(candidate.get("date")) == target_date:
                row = candidate
                break
        if row is None:
            raise RuntimeError("provider returned stale bar")
        try:
            values = {
                "code": code,
                "date": target_date,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "adjust": "qfq",
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("provider schema invalid") from exc
        return {"rows": [values], "metadata": dict(self.metadata[code])}


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
    calendar_token = _calendar_token(target, dates)
    if target not in {_date_text(value) for value in dates}:
        raise PortalPlanError("calendar_closed")
    factory = adapter_factory or MainboardMarketDataAdapter
    try:
        adapter = factory(base_dir=base_dir)
        symbols = tuple(adapter.scan())
        if not 5 <= len(symbols) <= 5000 or len(set(symbols)) != len(symbols):
            raise PortalPlanError("universe_unavailable")
        metadata = {}
        for symbol in symbols:
            code = normalize_code(symbol)
            if code is None or code in metadata:
                raise PortalPlanError("universe_schema")
            record = adapter.metadata(code)
            if not isinstance(record, Mapping):
                raise PortalPlanError("universe_unavailable")
            metadata[code] = _safe_metadata(record, target)
    except PortalPlanError:
        raise
    except Exception as exc:
        raise PortalPlanError("universe_unavailable") from exc
    if tuple(metadata) != tuple(symbols):
        raise PortalPlanError("universe_schema")
    token = _plan_universe_token(symbols, target, calendar_token, PROVIDER_VERSION)
    return (
        PortalRefreshPlan(
            symbols=symbols,
            target_date=target,
            universe_token=token,
            calendar_verified=True,
            calendar_token=calendar_token,
            provider_version=PROVIDER_VERSION,
        ),
        AksharePortalProvider(metadata),
    )


__all__ = [
    "AksharePortalProvider",
    "PortalPlanError",
    "PROVIDER_VERSION",
    "build_trusted_plan",
]
