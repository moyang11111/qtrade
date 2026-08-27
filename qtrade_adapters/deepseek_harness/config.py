"""Configuration and portable path helpers for the DeepSeek adapter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SELF_BASE = PROJECT_ROOT / "third_party" / "deepseek-harness-quant"
DEFAULT_SRC_BASE = Path.cwd() / "third_party" / "deepseek-harness-quant"
DEFAULT_HARNESS_PORT = 3080
HARNESS_PORT_ENV = "QTRADE_HARNESS_PORT"

PAGES = {
    "/portal": "portal.html",
    "/portal.html": "portal.html",
    "/pitch": "pitch.html",
    "/pitch.html": "pitch.html",
    "/control": "control.html",
    "/control.html": "control.html",
    "/factors": "factors.html",
    "/factors.html": "factors.html",
    "/etf": "etf.html",
    "/etf.html": "etf.html",
}
STATIC_FILES = {
    "/live_ticker.js": ("deck", "live_ticker.js"),
    "/nav_common.js": ("deck", "nav_common.js"),
}
LIVE_PREFIX = "/api/live/"
PROXY_PREFIX = "/api/proxy/"
SPECIAL_ENDPOINTS = ("system_live", "build_mode", "tech_pitch", "endpoints", "brief", "enums")


def _coerce_harness_port(value, default: int = DEFAULT_HARNESS_PORT) -> int:
    """Return a valid loopback port, falling back safely for bad input."""

    try:
        fallback = int(default)
    except (TypeError, ValueError, OverflowError):
        fallback = DEFAULT_HARNESS_PORT
    if not 1 <= fallback <= 65535:
        fallback = DEFAULT_HARNESS_PORT
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        port = int(value.strip()) if isinstance(value, str) else value
    except (TypeError, ValueError, OverflowError):
        return fallback
    return port if 1 <= port <= 65535 else fallback


def harness_port_info(*, env=None, default: int = DEFAULT_HARNESS_PORT) -> tuple[int, str | None]:
    """Resolve the configured port and report only a safe configuration reason."""

    fallback = _coerce_harness_port(default)
    environment = os.environ if env is None else env
    raw = environment.get(HARNESS_PORT_ENV)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return fallback, None
    port = _coerce_harness_port(raw, fallback)
    try:
        valid = (
            not isinstance(raw, bool)
            and isinstance(raw, (int, str))
            and (int(raw.strip()) if isinstance(raw, str) else raw) == port
            and 1 <= port <= 65535
        )
    except (TypeError, ValueError, OverflowError):
        valid = False
    if valid:
        return port, None
    return fallback, "invalid HARNESS port configuration; using the default port"


def resolve_harness_port(*, env=None, default: int = DEFAULT_HARNESS_PORT) -> int:
    """Resolve ``QTRADE_HARNESS_PORT`` to a strict integer in the valid range."""

    return harness_port_info(env=env, default=default)[0]


HARNESS_PORT = resolve_harness_port()


def resolve_base_dir(
    *,
    env=None,
    default_self_base: Path | None = None,
    default_src_base: Path | None = None,
) -> Path:
    """Return a usable base: explicit environment, project copy, then source copy."""

    environment = os.environ if env is None else env
    self_base = DEFAULT_SELF_BASE if default_self_base is None else Path(default_self_base)
    source_base = DEFAULT_SRC_BASE if default_src_base is None else Path(default_src_base)
    env_value = environment.get("QTRADE_BASE_DIR")
    candidates = (Path(env_value) if env_value else None, self_base, source_base)
    for candidate in candidates:
        if candidate is not None and (candidate / "deck").exists():
            return candidate
    return self_base


def prepare_sys_path(base: Path) -> None:
    """Make the external package importable without shadowing QTrade factors."""

    sys.modules.pop("factors", None)
    for path in (str(base), str(base / "deck")):
        if path not in sys.path:
            sys.path.insert(0, path)
