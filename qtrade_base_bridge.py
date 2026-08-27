# -*- coding: utf-8 -*-
"""Stable compatibility façade for the QTrade DeepSeek HARNESS adapter.

The implementation lives under ``qtrade_adapters.deepseek_harness``. This
module intentionally keeps the historic import names used by ``server.py``
and local integrations, including dynamic path monkeypatching in tests.
"""

from __future__ import annotations

import os
from pathlib import Path

from qtrade_adapters.deepseek_harness import config as _config
from qtrade_adapters.deepseek_harness import decisions as _decisions
from qtrade_adapters.deepseek_harness import runtime as _runtime
from qtrade_adapters.deepseek_harness.handler import (
    QtradeDeckHandler as _AdapterQtradeDeckHandler,
    serve_base_file as _serve_base_file,
)


PAGES = _config.PAGES
STATIC_FILES = _config.STATIC_FILES
LIVE_PREFIX = _config.LIVE_PREFIX
PROXY_PREFIX = _config.PROXY_PREFIX
SPECIAL_ENDPOINTS = _config.SPECIAL_ENDPOINTS
DEFAULT_SELF_BASE = _config.DEFAULT_SELF_BASE
DEFAULT_SRC_BASE = _config.DEFAULT_SRC_BASE
HARNESS_PORT = _config.HARNESS_PORT
NATIVE_CONTROL_PATH = "/control"


def _harness_port() -> int:
    """Resolve the bridge port while retaining façade monkeypatch compatibility."""

    return _config.resolve_harness_port(env=os.environ, default=HARNESS_PORT)


def base_dir() -> Path:
    """Return the selected base while honoring façade-level monkeypatches."""

    return _config.resolve_base_dir(
        default_self_base=DEFAULT_SELF_BASE,
        default_src_base=DEFAULT_SRC_BASE,
    )


class QtradeDeckHandler(_AdapterQtradeDeckHandler):
    """Adapter handler with callbacks bound to this compatibility façade."""

    def __init__(self, handler):
        super().__init__(
            handler,
            base_dir_fn=base_dir,
            prepare_sys_path_fn=_config.prepare_sys_path,
            harness_port_fn=_harness_port,
        )

    def handle_get(self, path: str) -> bool:
        """Serve QTrade's read-only console before consulting the optional base."""

        if path == NATIVE_CONTROL_PATH:
            control_page = Path(__file__).resolve().parent / "static" / "control.html"
            return serve_base_file(self.h, control_page)
        return super().handle_get(path)


def serve_base_file(handler, fspath: Path) -> bool:
    return _serve_base_file(handler, fspath)


def live(handler, sub: str):
    return QtradeDeckHandler(handler).serve_live(sub)


def try_serve(handler, path: str) -> bool:
    return QtradeDeckHandler(handler).handle_get(path)


def decide(handler, rec, auto_paper=None, service=None):
    return QtradeDeckHandler(handler).decide(rec, auto_paper=auto_paper, service=service)


def decide_bg_sync(base, rec=None, auto_paper=None, service=None):
    return _decisions.decide_bg_sync(base, rec, auto_paper, service)


def ensure_harness():
    return _runtime.ensure_harness(
        base_dir_fn=base_dir,
        default_src_base=DEFAULT_SRC_BASE,
        harness_port=_harness_port(),
    )


def maybe_auto_update():
    return _runtime.maybe_auto_update(base_dir_fn=base_dir)


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
    "base_dir",
    "decide",
    "decide_bg_sync",
    "ensure_harness",
    "live",
    "maybe_auto_update",
    "serve_base_file",
    "try_serve",
]
