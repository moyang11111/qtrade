"""Small, typed and deliberately lossy QTrade context for DeepSeek chat."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
import json
import re

from .config import MAX_CONTEXT_BYTES


CONTEXT_SCHEMA_VERSION = 1
APPROVED_OPPORTUNITY_CATEGORIES = ("screened", "fresh")

_HEALTH_STATES = frozenset({"ok", "degraded", "unavailable"})
_FRESHNESS_STATES = frozenset({"fresh", "stale", "unknown"})
_MAINBOARD_SOURCES = frozenset({"external_sqlite", "csv", "fallback", "unknown"})
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ContextError(ValueError):
    """Raised when a context provider cannot produce the fixed schema."""

    def __init__(self, message: str):
        self.code = (
            "context_too_large" if message == "context is too large" else "context_unavailable"
        )
        super().__init__(message)


@dataclass(frozen=True)
class ContextProvider:
    """Callbacks used to build the context without importing the server module."""

    health: Callable[[], object] | None = None
    business_date: Callable[[], object] | None = None
    mainboard: Callable[[], object] | None = None
    opportunities: Callable[[], object] | None = None
    factors: Callable[[], object] | None = None


def _call(provider: Callable[[], object] | None) -> Mapping[str, object]:
    if provider is None:
        return {}
    try:
        value = provider()
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _safe_date(value: object) -> str | None:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _safe_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 1_000_000_000:
        return None
    return value


def _safe_enum(value: object, allowed: frozenset[str], default: str) -> str:
    return value if isinstance(value, str) and value in allowed else default


def _is_allowed(value: object, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _freshness(value: object) -> str:
    return _safe_enum(value, _FRESHNESS_STATES, "unknown")


def build_context(provider: ContextProvider | None = None) -> dict[str, object]:
    """Build only the approved status summary from provider callbacks.

    Providers may return richer internal dictionaries; this function never
    forwards them.  Every output field is selected and validated explicitly.
    """

    source = provider or ContextProvider()
    health = _call(source.health)
    business = _call(source.business_date)
    mainboard = _call(source.mainboard)
    opportunities = _call(source.opportunities)
    factors = _call(source.factors)

    category_values = opportunities.get("categories")
    category_values = category_values if isinstance(category_values, Mapping) else {}
    public_summary = [
        {
            "category": category,
            "count": _safe_count(category_values.get(category)) or 0,
        }
        for category in APPROVED_OPPORTUNITY_CATEGORIES
    ]
    opportunity_count = _safe_count(opportunities.get("count"))
    if opportunity_count is None:
        opportunity_count = sum(item["count"] for item in public_summary)

    context = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "health": {
            "status": _safe_enum(health.get("status"), _HEALTH_STATES, "unavailable"),
        },
        "business_date": {
            "as_of": _safe_date(business.get("as_of")),
            "freshness": _freshness(business.get("freshness")),
        },
        "mainboard": {
            "total": _safe_count(mainboard.get("total")),
            "computable": _safe_count(mainboard.get("computable")),
            "tradable": _safe_count(mainboard.get("tradable")),
            "as_of": _safe_date(mainboard.get("as_of")),
            "source": _safe_enum(mainboard.get("source"), _MAINBOARD_SOURCES, "unknown"),
        },
        "opportunities": {
            "count": opportunity_count,
            "public_summary": public_summary,
        },
        "factors": {
            "scheme_count": _safe_count(factors.get("scheme_count")) or 0,
            "active_count": _safe_count(factors.get("active_count")) or 0,
            "as_of": _safe_date(factors.get("as_of")),
            "freshness": _freshness(factors.get("freshness")),
        },
    }
    validate_context(context)
    if len(serialize_context(context)) > MAX_CONTEXT_BYTES:
        raise ContextError("context is too large")
    return context


def validate_context(value: object) -> None:
    """Validate the exact recursive schema before it reaches the provider."""

    if not isinstance(value, Mapping) or set(value) != {
        "schema_version", "health", "business_date", "mainboard", "opportunities", "factors",
    }:
        raise ContextError("context schema is unsupported")
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != CONTEXT_SCHEMA_VERSION
    ):
        raise ContextError("context schema is unsupported")

    health = value.get("health")
    if not isinstance(health, Mapping) or set(health) != {"status"}:
        raise ContextError("context health schema is unsupported")
    if not _is_allowed(health.get("status"), _HEALTH_STATES):
        raise ContextError("context health value is unsupported")

    business = value.get("business_date")
    if not isinstance(business, Mapping) or set(business) != {"as_of", "freshness"}:
        raise ContextError("context date schema is unsupported")
    if business.get("as_of") is not None and _safe_date(business.get("as_of")) is None:
        raise ContextError("context date value is unsupported")
    if not _is_allowed(business.get("freshness"), _FRESHNESS_STATES):
        raise ContextError("context freshness value is unsupported")

    mainboard = value.get("mainboard")
    if not isinstance(mainboard, Mapping) or set(mainboard) != {
        "total", "computable", "tradable", "as_of", "source",
    }:
        raise ContextError("context mainboard schema is unsupported")
    for key in ("total", "computable", "tradable"):
        count = mainboard.get(key)
        if count is not None and _safe_count(count) is None:
            raise ContextError("context mainboard count is unsupported")
    if mainboard.get("as_of") is not None and _safe_date(mainboard.get("as_of")) is None:
        raise ContextError("context mainboard date is unsupported")
    if not _is_allowed(mainboard.get("source"), _MAINBOARD_SOURCES):
        raise ContextError("context mainboard source is unsupported")

    opportunities = value.get("opportunities")
    if not isinstance(opportunities, Mapping) or set(opportunities) != {"count", "public_summary"}:
        raise ContextError("context opportunities schema is unsupported")
    if _safe_count(opportunities.get("count")) is None:
        raise ContextError("context opportunities count is unsupported")
    public_summary = opportunities.get("public_summary")
    if (
        not isinstance(public_summary, list)
        or len(public_summary) != len(APPROVED_OPPORTUNITY_CATEGORIES)
    ):
        raise ContextError("context opportunity summary is unsupported")
    categories = [
        item.get("category") if isinstance(item, Mapping) else None for item in public_summary
    ]
    if categories != list(APPROVED_OPPORTUNITY_CATEGORIES):
        raise ContextError("context opportunity category is unsupported")
    for item in public_summary:
        if not isinstance(item, Mapping) or set(item) != {"category", "count"}:
            raise ContextError("context opportunity item is unsupported")
        if _safe_count(item.get("count")) is None:
            raise ContextError("context opportunity count is unsupported")

    factors = value.get("factors")
    if not isinstance(factors, Mapping) or set(factors) != {
        "scheme_count", "active_count", "as_of", "freshness",
    }:
        raise ContextError("context factors schema is unsupported")
    for key in ("scheme_count", "active_count"):
        if _safe_count(factors.get(key)) is None:
            raise ContextError("context factor count is unsupported")
    if factors.get("as_of") is not None and _safe_date(factors.get("as_of")) is None:
        raise ContextError("context factor date is unsupported")
    if not _is_allowed(factors.get("freshness"), _FRESHNESS_STATES):
        raise ContextError("context factor freshness is unsupported")


def serialize_context(context: Mapping[str, object]) -> bytes:
    """Serialize the already-validated context deterministically."""

    validate_context(context)
    return json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
