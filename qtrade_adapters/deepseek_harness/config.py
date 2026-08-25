"""Configuration and portable path helpers for the DeepSeek adapter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SELF_BASE = PROJECT_ROOT / "third_party" / "deepseek-harness-quant"
DEFAULT_SRC_BASE = Path.cwd() / "third_party" / "deepseek-harness-quant"
HARNESS_PORT = int(os.environ.get("QTRADE_HARNESS_PORT", "3081"))

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
