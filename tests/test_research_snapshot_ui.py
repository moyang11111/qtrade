"""The embedded research pages must show the active QTrade generation."""

from types import SimpleNamespace
from pathlib import Path

from server import build_research_snapshot_payload
from qtrade_adapters.deepseek_harness.handler import adapt_page_html


ROOT = Path(__file__).resolve().parents[1]


def test_research_payload_uses_one_generation_and_supports_exact_symbol():
    pipeline = SimpleNamespace(
        manifest={"target_date": "2026-09-22", "generation": "abc123", "total": 2},
        portal=SimpleNamespace(metadata=[{"code": "600001", "name": "甲", "tradable": True}, {"code": "600002", "name": "乙", "tradable": False}]),
        factors={"computable": 2, "valid_count": 2, "records": [
            {"symbol": "600001", "score": 0.2, "values": {"momentum": 0.2}, "as_of": "2026-09-22"},
            {"symbol": "600002", "score": 1.4, "values": {"momentum": 1.4}, "as_of": "2026-09-22"},
        ]},
        decision={"candidate": 1, "records": [
            {"symbol": "600001", "score": 0.2, "action": "hold", "as_of": "2026-09-22"},
            {"symbol": "600002", "score": 1.4, "action": "buy", "as_of": "2026-09-22"},
        ]},
    )
    factors = build_research_snapshot_payload(pipeline, "factors")
    decisions = build_research_snapshot_payload(pipeline, "decisions", "600001")
    portal = build_research_snapshot_payload(pipeline, "portal")
    assert factors["target_date"] == decisions["target_date"] == "2026-09-22"
    assert portal["target_date"] == "2026-09-22"
    assert factors["generation"] == decisions["generation"] == "abc123"
    assert portal["tradable_count"] == 1
    assert portal["actions"] == {"buy": 1, "hold": 1, "sell": 0}
    assert portal["leaders"][0]["symbol"] == "600002"
    assert [item["symbol"] for item in factors["records"]] == ["600002", "600001"]
    assert decisions["records"] == [{"symbol": "600001", "score": 0.2, "action": "hold", "as_of": "2026-09-22", "name": "甲"}]


def test_research_embeds_use_qtrade_snapshot_and_portal_keeps_original_view():
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    api = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    app = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
    dashboard = (ROOT / "static/js/portal-dashboard.js").read_text(encoding="utf-8")
    page = (ROOT / "static/research.html").read_text(encoding="utf-8")
    assert 'src="/portal"' in index
    assert 'src="/research.html?view=decisions"' in index
    assert 'src="/research.html?view=factors"' in index
    assert "pitch: '/research.html?view=decisions'" in api
    assert "factorboard: '/research.html?view=factors'" in api
    assert "portal: '/portal'" in api
    assert 'id="legacy"' not in page
    assert '/api/research/snapshot?view=portal' in dashboard
    assert '/api/live/portal_dash' not in dashboard
    assert 'qtrade:portal-open-control' in dashboard
    assert 'qtrade:portal-open-control' in app


def test_original_portal_markup_uses_qtrade_renderer():
    original = """<html><head><title>Portal</title></head><body>
    <div id="timing-box"></div>
    <div id="traffic-light-box"></div>
    <div id="kpi-box"></div>
    <div id="brief-box"></div>
    <script>
    (function () {
      LW.sidebar.render('sidebar', {active: 'portal'});
      LW.api.get('/api/live/portal_dash');
    })();
    </script>
    </body></html>"""
    adapted = adapt_page_html(original, "portal.html")
    assert 'id="timing-box"' in adapted
    assert 'id="traffic-light-box"' in adapted
    assert 'id="kpi-box"' in adapted
    assert 'id="brief-box"' in adapted
    assert '<script src="/js/portal-dashboard.js"></script>' in adapted
    assert "LW.api.get('/api/live/portal_dash')" not in adapted
