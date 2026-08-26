"""Offline contracts for the factor dashboard and saved-plan interface."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from qtrade_adapters.deepseek_harness.factor_library import FactorLibrary


ROOT = Path(__file__).resolve().parents[1]


def _write_ui_artifacts(root: Path, as_of: str = "2026-08-25") -> None:
    output = root / "data" / "factorpool" / "output"
    health = output / "health"
    health.mkdir(parents=True)
    (output / "factor_manifest_ui.json").write_text(
        json.dumps({
            "date": as_of,
            "factors": [
                {"factor": "alpha", "cn": "Alpha", "eligible": True},
                {"factor": "beta", "cn": "Beta", "eligible": False},
            ],
        }),
        encoding="utf-8",
    )
    (output / "factor_data_freshness_ui.json").write_text(
        json.dumps({"date": as_of, "updated": as_of}), encoding="utf-8"
    )
    (health / "health_ui.csv").write_text(
        "factor,icir120,crowding,test_date\n"
        f"alpha,1.25,0.20,{as_of}\n"
        f"beta,0.30,0.80,{as_of}\n",
        encoding="utf-8",
    )
    (output / "factor_usage_ui.json").write_text(
        json.dumps({"date": as_of, "layers": {"ext_decision": {"alpha": True}}}),
        encoding="utf-8",
    )
    (output / "factor_lifecycle_ui.json").write_text(
        json.dumps({"date": as_of, "lifecycle": {"alpha": "active", "beta": "watch"}}),
        encoding="utf-8",
    )


def _run_node(source: str) -> None:
    result = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_capabilities_are_derived_from_same_date_records(tmp_path):
    data_dir = tmp_path / "deck"
    _write_ui_artifacts(data_dir)
    library = FactorLibrary(tmp_path / "user-data" / "factor_library.json", data_dir)

    capabilities = library.capabilities()

    assert capabilities["as_of"] == "2026-08-25"
    assert capabilities["facets"] == {
        "status": ["eligible", "ineligible"],
        "usage": ["ext_decision"],
        "lifecycle": ["active", "watch"],
    }
    assert capabilities["numeric"] == {
        "icir120": {"min": 0.3, "max": 1.25},
        "crowding": {"min": 0.2, "max": 0.8},
    }
    assert "family" not in capabilities["facets"]
    assert "category" not in capabilities["facets"]
    assert "direction" not in capabilities["facets"]


def test_static_page_contains_factorboard_controls_and_plan_list():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    for element_id in (
        "factorFilterPanel",
        "factorStatusFilter",
        "factorUsageFilter",
        "factorLifecycleFilter",
        "factorIcirMin",
        "factorIcirMax",
        "factorCrowdingMax",
        "factorKeyword",
        "btnFactorPreview",
        "btnFactorSave",
        "factorLibraryList",
        "btnOpenFactorBoard",
    ):
        assert f'id="{element_id}"' in index
    assert "factorTable" not in index
    assert 'id="iframeFactorBoard"' in index


def test_api_serializes_only_allowlisted_conditions_and_uses_json_methods():
    _run_node(
        f"""
        const assert = require('node:assert/strict');
        const {{ API, QTradeFactorLibrary }} = require({json.dumps(str(ROOT / 'static' / 'js' / 'api.js'))});
        assert.deepEqual(QTradeFactorLibrary.serializeConditions({{
          status: ['eligible', 'eligible'], usage: 'ext_decision', lifecycle: [],
          icir120_min: '0.3', crowding_max: '', keyword: '  alpha  ',
          expression: 'alert(1)', matched_factors: ['fake']
        }}), {{
          status: ['eligible'], usage: ['ext_decision'], icir120_min: 0.3, keyword: 'alpha'
        }});
        assert.equal(QTradeFactorLibrary.routeForPlan('../secret', 'refresh'),
          '/api/factor-library/..%2Fsecret/refresh');
        const calls = [];
        global.fetch = async (url, options) => {{
          calls.push([url, options]);
          return {{ ok: true, json: async () => ({{ items: [] }}) }};
        }};
        (async () => {{
          await API.previewFactorLibrary({{ status: ['eligible'] }});
          await API.createFactorLibrary({{ name: 'x', description: '', conditions: {{}} }});
          assert.equal(calls[0][0], '/api/factor-library/preview');
          assert.equal(calls[0][1].method, 'POST');
          assert.equal(calls[0][1].headers['Content-Type'], 'application/json');
          assert.equal(JSON.parse(calls[0][1].body).matched_factors, undefined);
          assert.equal(JSON.parse(calls[1][1].body).matched_factors, undefined);
        }})().catch(error => {{ console.error(error); process.exitCode = 1; }});
        """
    )


def test_app_uses_safe_plan_rendering_and_refresh_contract():
    app = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    factor_page = app[app.index("async function openFactorPage") : app.index("async function openRiskPage")]
    plan_renderer = app[app.index("function renderFactorPlanCard") : app.index("function openFactorBoard")]
    save_renderer = app[app.index("async function saveFactorPlan") : app.index("function formatFactorCondition")]

    assert "API.getFactors(" not in factor_page
    assert "createElement" in plan_renderer
    assert "textContent" in plan_renderer
    assert "innerHTML" not in plan_renderer
    assert "window.confirm" not in plan_renderer
    assert "matched_factors" not in save_renderer
    assert "state.factorCapabilities = null" in app
    assert "state.factorPreview = null" in app
    assert "loadFactorCapabilities(true)" in app
    assert "loadFactorLibrary(true)" in app


def test_capabilities_route_is_allowlisted_and_safe():
    from server import APIHandler

    assert APIHandler._is_factor_library_path("/api/factor-library/capabilities")
    assert APIHandler._factor_parts("/api/factor-library/../../etc/passwd") == [
        "..", "..", "etc", "passwd"
    ]
