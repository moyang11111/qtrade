"""Deterministic contract for the QTrade UI adaptation layer."""

from __future__ import annotations

import re
from pathlib import Path

from qtrade_adapters.deepseek_harness.handler import adapt_page_html


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INDEX = PROJECT_ROOT / "static" / "index.html"
STYLE = PROJECT_ROOT / "static" / "css" / "style.css"
TOKENS = PROJECT_ROOT / "static" / "css" / "tokens.css"
ADAPTER_CSS = PROJECT_ROOT / "static" / "css" / "deepseek-adapter.css"
HANDLER = PROJECT_ROOT / "qtrade_adapters" / "deepseek_harness" / "handler.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tokens_are_unique_first_loaded_and_preserve_legacy_aliases():
    index = _read(INDEX)
    style = _read(STYLE)
    tokens = _read(TOKENS)

    token_link = '<link rel="stylesheet" href="css/tokens.css?v=1">'
    style_link = '<link rel="stylesheet" href="css/style.css?v=7">'
    assert index.count(token_link) == 1
    assert index.count(style_link) == 1
    assert index.index(token_link) < index.index(style_link)
    assert "--qt-bg-primary: #0a0a0b" in tokens
    assert "--qt-color-brand: #5e6ad2" in tokens
    assert "--qt-color-up: #eb5757" in tokens
    assert "--qt-color-down: #27ae60" in tokens
    assert "prefers-reduced-motion: reduce" in tokens
    assert "--bg-primary: var(--qt-bg-primary)" in style
    assert "--up: var(--qt-color-up)" in style
    assert "--down: var(--qt-color-down)" in style


def test_main_page_has_qtrade_brand_and_preserves_embed_hooks():
    index = _read(INDEX)

    assert not re.search(r"deepseek|harness", index, re.IGNORECASE)
    expected_frames = {
        "iframePortal": ("/portal", "QTrade 门户"),
        "iframePitch": ("/pitch", "QTrade 决策台"),
        "iframeFactorBoard": ("/factors", "QTrade 因子仪表盘"),
        "iframeControl": ("/control", "QTrade 控制台"),
    }
    for frame_id, (src, title) in expected_frames.items():
        match = re.search(rf'<iframe\b[^>]*\bid="{frame_id}"[^>]*>', index)
        assert match, frame_id
        tag = match.group(0)
        assert f'src="{src}"' in tag
        assert f'title="{title}"' in tag
        assert 'class="embedded-deck-frame"' in tag
        assert " style=" not in tag

    for page_id in ("pagePortal", "pagePitch", "pageFactorBoard", "pageControl"):
        assert f'id="{page_id}"' in index
        assert f'id="{page_id}"' in index and "embedded-page" in index
    for hook_id in (
        "btnPortalClose",
        "btnPitchClose",
        "btnFactorBoardClose",
        "btnControlClose",
    ):
        assert f'id="{hook_id}"' in index
    assert 'data-page="portal"' in index
    assert 'data-page="pitch"' in index
    assert 'data-page="control"' in index
    assert 'data-page="factorboard"' in index


def test_tokens_and_adapter_preserve_a_share_red_up_green_down_semantics():
    tokens = _read(TOKENS)
    adapter = _read(ADAPTER_CSS)
    style = _read(STYLE)

    assert "--qt-color-up: #eb5757" in tokens
    assert "--qt-color-down: #27ae60" in tokens
    assert ".d-up" in adapter and "var(--qt-color-up)" in adapter
    assert ".d-down" in adapter and "var(--qt-color-down)" in adapter
    assert "--red: var(--qt-color-up)" in style
    assert "--green: var(--qt-color-down)" in style


def test_adapter_css_is_scoped_and_hides_only_verified_duplicates():
    adapter = _read(ADAPTER_CSS)

    assert "var(--qt-" in adapter
    assert "#sidebar" in adapter
    assert "#wufu-box" in adapter
    assert "#qt-features-box" in adapter
    assert ".dsq-brand" in adapter
    assert ":has(.dsq-brand)" in adapter
    assert not re.search(r"html\.qtrade-adapted[^\{]*(?:h1|header|nav)", adapter)
    assert not re.search(r"^[^/\n]*\b(?:h1|header|nav)\b[^\{]*\{[^}]*display:\s*none", adapter, re.MULTILINE)
    assert "qtrade-adapted body.qtrade-adapted" in adapter


def test_embedded_parent_is_flex_sized_and_scrolls_only_inside_content():
    style = _read(STYLE)

    frame_rule = re.search(r"\.embedded-deck-frame\s*\{(?P<body>[^}]+)\}", style)
    assert frame_rule
    frame_body = frame_rule.group("body")
    assert "flex: 1 1 auto" in frame_body
    assert "min-height: 0" in frame_body
    assert "min-width: 0" in frame_body
    assert "overflow: hidden" in frame_body
    assert ".auto-overlay.embedded-page" in style
    assert ".auto-header" in style and "flex-shrink: 0" in style


def test_handler_injection_is_ordered_idempotent_and_head_safe():
    source = _read(HANDLER)
    assert "ADAPTER_STYLE_LINKS" in source
    assert "adapt_page_html" in source
    assert "</head" in source
    assert "</style>" not in source

    html = (
        '<html lang="zh-CN"><head><title>Upstream title</title></head>'
        '<body><main id="content">dynamic body</main><script>window.keep = true;</script></body></html>'
    )
    adapted = adapt_page_html(html, "portal.html")
    adapted_twice = adapt_page_html(adapted, "portal.html")
    assert adapted_twice == adapted
    assert 'data-qtrade-adapted="true"' in adapted
    assert "qtrade-page-portal" in adapted
    assert '<title>QTrade — 门户</title>' in adapted
    assert 'href="/css/tokens.css"' in adapted
    assert 'href="/css/deepseek-adapter.css"' in adapted
    assert adapted.index('href="/css/tokens.css"') < adapted.index(
        'href="/css/deepseek-adapter.css"'
    )
    assert adapted.count('href="/css/tokens.css"') == 1
    assert adapted.count('href="/css/deepseek-adapter.css"') == 1
    assert "dynamic body" in adapted
    assert "window.keep = true;" in adapted
    assert "<style>" not in adapted

    no_head = '<html><body><script>window.keep = true;</script></body></html>'
    safe = adapt_page_html(no_head, "control.html")
    assert 'data-qtrade-adapted="true"' in safe
    assert 'href="/css/tokens.css"' in safe
    assert 'href="/css/deepseek-adapter.css"' in safe
    assert "window.keep = true;" in safe
