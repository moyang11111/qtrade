"""Offline contracts for the QTrade-owned read-only control console."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import qtrade_base_bridge as bridge


ROOT = Path(__file__).resolve().parents[1]
CONTROL_HTML = ROOT / "static" / "control.html"
CONTROL_JS = ROOT / "static" / "js" / "control.js"
CONTROL_CSS = ROOT / "static" / "css" / "control-console.css"


class _FakeHandler:
    def __init__(self, path: str):
        self.path = path
        self.headers = {}
        self.wfile = BytesIO()
        self.responses = []

    def send_response(self, status):
        self.responses.append({"status": status})

    def send_header(self, name, value):
        self.responses[-1][name] = value

    def end_headers(self):
        pass


def test_control_route_is_qtrade_owned_without_external_base(monkeypatch):
    missing_base = ROOT / "does-not-exist-for-control-contract"
    monkeypatch.setattr(bridge, "base_dir", lambda: missing_base)
    handler = _FakeHandler("/control")

    assert bridge.QtradeDeckHandler(handler).handle_get("/control") is True
    assert handler.responses[0]["status"] == 200
    html = handler.wfile.getvalue().decode("utf-8")
    assert 'data-qtrade-native-control="true"' in html
    assert "QTrade 运维与研究控制台" in html
    assert "不会执行交易或系统命令" in html
    assert "deepseek-harness-quant" not in html.lower()


def test_control_page_loads_qtrade_assets_and_only_fixed_get_cards():
    html = CONTROL_HTML.read_text(encoding="utf-8")
    js = CONTROL_JS.read_text(encoding="utf-8")

    assert html.index('href="/css/tokens.css"') < html.index(
        'href="/css/control-console.css"'
    )
    assert '<script src="/js/control.js" defer></script>' in html
    for endpoint in (
        "/api/health",
        "/api/update/status",
        "/api/auto/paper?action=status",
        "/api/factor-library",
        "/api/harness/status",
    ):
        assert endpoint in js
    assert "method: 'GET'" in js
    assert "method: 'POST'" not in js
    assert "innerHTML" not in js
    assert "eval(" not in js
    assert "Function(" not in js
    assert "window.open" not in js
    assert "window.location =" not in js
    assert "location.href" not in js


def test_control_navigation_is_same_origin_source_and_page_allowlisted():
    control_js = CONTROL_JS.read_text(encoding="utf-8")
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "window.parent.postMessage({ type: 'qtrade:navigate', page }, window.location.origin)" in control_js
    assert "event.origin !== window.location.origin" in app_js
    assert "event.source !== controlFrame.contentWindow" in app_js
    assert "message.type !== 'qtrade:navigate'" in app_js
    assert "CONTROL_NAVIGATION_PAGES.has(message.page)" in app_js
    for page in ("market", "portal", "pitch", "factorboard", "factors", "autopaper"):
        assert f"'{page}'" in control_js
        assert f"'{page}'" in app_js
    assert "postMessage" not in control_js.replace(
        "window.parent.postMessage({ type: 'qtrade:navigate', page }, window.location.origin)", ""
    )
    assert "'*'" not in control_js


def test_control_cards_render_api_values_as_text_and_redact_diagnostics():
    js = CONTROL_JS.read_text(encoding="utf-8")
    diagnostics = js[js.index("function diagnosticPayload") : js.index("async function copyDiagnostics")]

    assert "textContent = value" in js
    assert "createElement('span')" in js
    assert "generated_at" in js
    for field in (
        "trade_date",
        "state",
        "reason",
        "outputs",
        "mainboard",
        "factor_library",
        "harness",
    ):
        assert field in js
    for secret in ("absolute_path", "QTRADE_BASE_DIR", "api_key", "last_error"):
        assert secret not in diagnostics
    assert "last_error" in js
    assert "hasError" in js
    assert "payload.last_error" in js
    assert "textContent = payload.last_error" not in js


def test_control_console_styles_use_tokens_and_prevent_horizontal_overflow():
    css = CONTROL_CSS.read_text(encoding="utf-8")

    assert "var(--qt-bg-primary)" in css
    assert "var(--qt-color-brand)" in css
    assert "var(--qt-color-down)" in css
    assert "overflow-x: hidden" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_parent_message_listener_is_a_small_static_navigation_contract():
    source = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")

    assert "handleControlNavigationMessage" in source
    assert "window.addEventListener('message', handleControlNavigationMessage)" in source
