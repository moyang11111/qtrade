"""Public surface for the QTrade DeepSeek HARNESS adapter."""

from .config import (
    DEFAULT_HARNESS_PORT,
    DEFAULT_SELF_BASE,
    DEFAULT_SRC_BASE,
    HARNESS_PORT_ENV,
    HARNESS_PORT,
    LIVE_PREFIX,
    PAGES,
    PROXY_PREFIX,
    SPECIAL_ENDPOINTS,
    STATIC_FILES,
    prepare_sys_path,
    resolve_base_dir,
    resolve_harness_port,
)
from .decisions import decide, decide_bg_sync
from .factor_library import (
    FactorDataError,
    FactorLibrary,
    FactorLibraryError,
    FactorSnapshot,
    FactorStorageError,
    FactorValidationError,
    load_factor_records,
    load_factor_snapshot,
    normalize_conditions,
    resolve_factor_library_path,
)
from .handler import QtradeDeckHandler, serve_base_file
from .market_data import MainboardMarketDataAdapter, normalize_code
from .runtime import ensure_harness, maybe_auto_update

__all__ = [
    "DEFAULT_SELF_BASE",
    "DEFAULT_SRC_BASE",
    "DEFAULT_HARNESS_PORT",
    "HARNESS_PORT_ENV",
    "HARNESS_PORT",
    "LIVE_PREFIX",
    "PAGES",
    "PROXY_PREFIX",
    "QtradeDeckHandler",
    "SPECIAL_ENDPOINTS",
    "STATIC_FILES",
    "decide",
    "decide_bg_sync",
    "FactorDataError",
    "FactorLibrary",
    "FactorLibraryError",
    "FactorSnapshot",
    "FactorStorageError",
    "FactorValidationError",
    "ensure_harness",
    "maybe_auto_update",
    "load_factor_records",
    "load_factor_snapshot",
    "MainboardMarketDataAdapter",
    "normalize_code",
    "prepare_sys_path",
    "resolve_base_dir",
    "resolve_harness_port",
    "resolve_factor_library_path",
    "serve_base_file",
    "normalize_conditions",
]
