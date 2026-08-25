"""Public surface for the QTrade DeepSeek HARNESS adapter."""

from .config import (
    DEFAULT_SELF_BASE,
    DEFAULT_SRC_BASE,
    HARNESS_PORT,
    LIVE_PREFIX,
    PAGES,
    PROXY_PREFIX,
    SPECIAL_ENDPOINTS,
    STATIC_FILES,
    prepare_sys_path,
    resolve_base_dir,
)
from .decisions import decide, decide_bg_sync
from .handler import QtradeDeckHandler, serve_base_file
from .runtime import ensure_harness, maybe_auto_update

__all__ = [
    "DEFAULT_SELF_BASE",
    "DEFAULT_SRC_BASE",
    "HARNESS_PORT",
    "LIVE_PREFIX",
    "PAGES",
    "PROXY_PREFIX",
    "QtradeDeckHandler",
    "SPECIAL_ENDPOINTS",
    "STATIC_FILES",
    "decide",
    "decide_bg_sync",
    "ensure_harness",
    "maybe_auto_update",
    "prepare_sys_path",
    "resolve_base_dir",
    "serve_base_file",
]
