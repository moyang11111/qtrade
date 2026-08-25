"""Executable compatibility contract for the DeepSeek adaptation boundary."""

from __future__ import annotations

import ast
from collections import Counter
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import factors
import qtrade_base_bridge as bridge


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FACTOR_COLUMNS = (
    "std20",
    "downside_vol",
    "reversal20",
    "mom20",
    "o2c",
    "amihud",
    "max_ret20",
    "skew20",
    "amp20",
    "volume_ratio",
    "limup_ex_5",
    "pullback",
    "ma_alignment",
    "rsi_revert",
    "macd_hist",
    "roc20",
    "wpr14",
    "cci20",
    "obv_trend",
    "kdj_k",
    "ma200_up",
    "lowvol_60",
    "mom_120",
    "near_high_250",
    "new_high_250",
    "consec_limit_up",
    "consec_limit_down",
    "limit_up_flag",
    "limit_down_flag",
    "kdj_d",
    "kdj_j",
    "vol_contract",
    "near_ma250",
    "ma50_up",
    "rsi6",
)


class _FakeHandler:
    """Small response recorder; it never opens a socket or reaches an upstream."""

    def __init__(self, path: str = "/"):
        self.path = path
        self.headers = {}
        self.json_calls = []
        self.byte_calls = []
        self.wfile = BytesIO()

    def _json(self, data, status=200):
        self.json_calls.append((status, data))
        return data

    def send_response(self, status):
        self.byte_calls.append({"status": status})

    def send_header(self, name, value):
        self.byte_calls[-1][name] = value

    def end_headers(self):
        pass


def _synthetic_ohlcv(rows: int = 260) -> pd.DataFrame:
    """Build deterministic price/volume data without using a market asset."""

    t = np.arange(rows, dtype=float)
    close = 100.0 + 0.2 * t + 2.0 * np.sin(t / 7.0) + 0.5 * np.sin(t / 3.0)
    open_ = close + 0.2 * np.cos(t / 5.0)
    high = np.maximum(open_, close) + 0.7 + 0.1 * np.sin(t)
    low = np.minimum(open_, close) - 0.6 - 0.1 * np.cos(t)
    volume = 100000.0 + 1000.0 * (t % 17) + 5000.0 * np.sin(t / 11.0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=pd.date_range("2025-01-02", periods=rows, freq="B"),
    )


def _base_with_deck(tmp_path: Path, name: str) -> Path:
    base = tmp_path / name
    (base / "deck").mkdir(parents=True)
    return base


def _api_handler_ast() -> ast.ClassDef:
    tree = ast.parse((PROJECT_ROOT / "server.py").read_text(encoding="utf-8"))
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "APIHandler"
    )


def _method_ast(class_node: ast.ClassDef, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def test_factor_frame_and_available_factor_order_are_stable():
    frame = factors.factor_frame(_synthetic_ohlcv())

    assert tuple(frame.columns) == EXPECTED_FACTOR_COLUMNS
    assert tuple(factors.AVAILABLE_FACTORS) == EXPECTED_FACTOR_COLUMNS
    assert frame.shape == (260, len(EXPECTED_FACTOR_COLUMNS))


def test_factor_inventory_categories_and_counts_are_stable():
    inventory = factors.factor_inventory()

    assert inventory["total"] == 78
    assert inventory["available"] == 35
    assert inventory["need_data"] == 43
    assert Counter(inventory["factors"].values()) == {
        "ok": 35,
        "need_finance": 30,
        "need_cross_section": 6,
        "need_lhg": 5,
        "need_industry": 2,
    }
    assert inventory["factors"]["std20"] == "ok"
    assert inventory["factors"]["f_score"] == "need_finance"
    assert inventory["factors"]["turn_mid_prox"] == "need_cross_section"
    assert inventory["factors"]["north_flow"] == "need_lhg"
    assert inventory["factors"]["ind_rs_20"] == "need_industry"


def test_latest_factors_and_composite_score_keep_numeric_and_nan_behavior():
    frame = _synthetic_ohlcv()
    score = factors.composite_score(frame)
    latest = factors.latest_factors(frame)

    assert score.isna().sum() == 0
    assert score.iloc[:29].eq(0.0).all()
    assert score.iloc[-1] == pytest.approx(8.9334078364, abs=1e-6)
    assert set(latest) == {"symbol", "date", *EXPECTED_FACTOR_COLUMNS, "composite_score"}
    assert latest["symbol"] is None
    assert latest["date"] == "2025-12-31"
    assert latest["std20"] == pytest.approx(0.000729, abs=1e-6)
    assert latest["composite_score"] == pytest.approx(8.9334, abs=1e-4)

    short = factors.latest_factors(frame.iloc[:10])
    assert short["std20"] is None
    assert short["mom20"] is None
    assert short["composite_score"] == 0.0
    assert factors.latest_factors(pd.DataFrame()) == {}


def test_bridge_surface_routes_and_prefixes_are_stable():
    assert bridge.PAGES == {
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
    assert bridge.STATIC_FILES == {
        "/live_ticker.js": ("deck", "live_ticker.js"),
        "/nav_common.js": ("deck", "nav_common.js"),
    }
    assert bridge.LIVE_PREFIX == "/api/live/"
    assert bridge.PROXY_PREFIX == "/api/proxy/"
    assert bridge.SPECIAL_ENDPOINTS == (
        "system_live",
        "build_mode",
        "tech_pitch",
        "endpoints",
        "brief",
        "enums",
    )
    for name in (
        "base_dir",
        "serve_base_file",
        "live",
        "try_serve",
        "decide",
        "decide_bg_sync",
    ):
        assert callable(getattr(bridge, name))


def test_bridge_base_dir_environment_precedence_and_fallback(tmp_path, monkeypatch):
    env_base = _base_with_deck(tmp_path, "env-base")
    self_base = _base_with_deck(tmp_path, "self-base")
    source_base = _base_with_deck(tmp_path, "source-base")
    monkeypatch.setattr(bridge, "DEFAULT_SELF_BASE", self_base)
    monkeypatch.setattr(bridge, "DEFAULT_SRC_BASE", source_base)

    monkeypatch.setenv("QTRADE_BASE_DIR", str(env_base))
    assert bridge.base_dir() == env_base

    monkeypatch.setenv("QTRADE_BASE_DIR", str(tmp_path / "missing-base"))
    assert bridge.base_dir() == self_base

    monkeypatch.delenv("QTRADE_BASE_DIR")
    assert bridge.base_dir() == self_base

    (self_base / "deck").rmdir()
    assert bridge.base_dir() == source_base


def test_bridge_missing_base_is_safe_and_does_not_fall_through(tmp_path, monkeypatch):
    missing = tmp_path / "missing-base"
    monkeypatch.setattr(bridge, "base_dir", lambda: missing)
    handler = _FakeHandler("/portal")

    assert bridge.QtradeDeckHandler(handler).handle_get("/portal") is False
    assert handler.json_calls == []
    assert handler.byte_calls == []


def test_bridge_serves_synthetic_page_static_asset_and_v2_file(tmp_path, monkeypatch):
    base = _base_with_deck(tmp_path, "synthetic-base")
    (base / "ui_v2" / "pages").mkdir(parents=True)
    (base / "ui_v2" / "sample.css").write_text("body { color: red; }", encoding="utf-8")
    (base / "ui_v2" / "pages" / "portal.html").write_text(
        "<html><head></head><body>synthetic portal</body></html>",
        encoding="utf-8",
    )
    (base / "deck" / "live_ticker.js").write_text("window.syntheticTicker = true;", encoding="utf-8")
    monkeypatch.setattr(bridge, "base_dir", lambda: base)

    page_handler = _FakeHandler("/portal")
    assert bridge.QtradeDeckHandler(page_handler).handle_get("/portal") is True
    page = page_handler.wfile.getvalue().decode("utf-8")
    assert page.startswith("<html><head>")
    assert "#sidebar{display:none!important}" in page
    assert "#qt-features-box{display:none!important}#wufu-box{display:none!important}" in page
    assert "synthetic portal" in page

    static_handler = _FakeHandler("/live_ticker.js")
    assert bridge.QtradeDeckHandler(static_handler).handle_get("/live_ticker.js") is True
    assert static_handler.wfile.getvalue() == b"window.syntheticTicker = true;"

    v2_handler = _FakeHandler("/v2/sample.css")
    assert bridge.QtradeDeckHandler(v2_handler).handle_get("/v2/sample.css") is True
    assert v2_handler.wfile.getvalue() == b"body { color: red; }"


def test_server_bridge_calls_and_route_entries_are_explicit_ast_contract():
    api_handler = _api_handler_ast()
    method_names = {
        node.name
        for node in api_handler.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {
        "_base_dir",
        "_serve_base_file",
        "_base_live",
        "_try_base_deck",
        "_decide",
        "_decide_bg_sync",
        "do_GET",
        "do_POST",
    } <= method_names

    bridge_attrs = {
        node.attr
        for node in ast.walk(api_handler)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "qtrade_base_bridge"
    }
    assert {
        "QtradeDeckHandler",
        "base_dir",
        "serve_base_file",
        "live",
        "try_serve",
        "decide",
        "decide_bg_sync",
    } <= bridge_attrs

    do_get = _method_ast(api_handler, "do_GET")
    do_post = _method_ast(api_handler, "do_POST")
    get_literals = {
        node.value
        for node in ast.walk(do_get)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {
        "/api/health",
        "/api/symbols",
        "/api/factors/list",
        "/api/kline/",
        "/api/info/",
        "/api/indicators/",
        "/api/factors/",
    } <= get_literals
    get_attrs = {
        node.attr for node in ast.walk(do_get) if isinstance(node, ast.Attribute)
    }
    post_attrs = {
        node.attr for node in ast.walk(do_post) if isinstance(node, ast.Attribute)
    }
    assert "_try_base_deck" in get_attrs
    assert "handle_post" in post_attrs


def test_adaptation_docs_and_notice_declare_the_boundary():
    standard = (PROJECT_ROOT / "docs" / "DEEPSEEK_ADAPTATION_STANDARD.md").read_text(
        encoding="utf-8"
    )
    notices = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    for phrase in (
        "upstream external source",
        "运行时可选依赖",
        "不作为 QTrade 源码整体复制或随包携带",
        "QTrade adapter",
        "adapted QTrade-owned code",
        "QTrade 用户界面品牌统一",
        "第三方名称、许可证和归属只放在",
        "QTRADE_BASE_DIR",
        "QTRADE_DECK_DIR",
        "A 股界面语义保持“红涨、绿跌”",
        "页面通过动态 API 获取会变化的数据",
        "不得把股票列表、候选池、评分、状态、策略注册表或行情写死",
        "### 失败降级",
        "禁止复用或提交第三方行情 CSV",
    ):
        assert phrase in standard
    assert "deepseek-harness-quant" in notices
    assert "MIT License" in notices
    assert "Copyright (c) 2026 DeepSeek HARNESS Quant (DSHQuant)" in notices
    assert "Permission is hereby granted" in notices
    assert "do not include third-party market data" in notices
    assert "用户需自行合规提供外部底座及其数据" in notices
    assert "不构成 DeepSeek HARNESS Quant、DSHQuant 或其作者对 QTrade 的背书" in notices
