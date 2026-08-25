"""Isolated tests for the modular DeepSeek adapter implementation."""

from __future__ import annotations

import json
import sys
import types
from io import BytesIO
from pathlib import Path

import pytest

import qtrade_base_bridge as bridge
from qtrade_adapters.deepseek_harness import config, decisions, runtime
from qtrade_adapters.deepseek_harness.handler import QtradeDeckHandler


class _FakeHandler:
    def __init__(self, path="/", body=b"", headers=None):
        self.path = path
        self.headers = headers or {}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.json_calls = []
        self.byte_calls = []
        self.server = types.SimpleNamespace(server_address=("127.0.0.1", 49123))

    def _json(self, data, status=200):
        self.json_calls.append((status, data))
        return data

    def send_response(self, status):
        self.byte_calls.append({"status": status})

    def send_header(self, name, value):
        self.byte_calls[-1][name] = value

    def end_headers(self):
        pass


def _base_with_deck(tmp_path: Path, name: str) -> Path:
    base = tmp_path / name
    (base / "deck").mkdir(parents=True)
    return base


class _FakeSocket:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        if self.connect_error is not None:
            raise self.connect_error

    def close(self):
        self.closed = True


class _FakeSocketModule:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.instances = []

    def socket(self):
        instance = _FakeSocket(self.connect_error)
        self.instances.append(instance)
        return instance


class _FakeProcessModule:
    DEVNULL = "DEVNULL"
    DETACHED_PROCESS = 64

    def __init__(self):
        self.calls = []

    def Popen(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return types.SimpleNamespace()


def test_config_and_facade_paths_are_portable_and_prioritized(tmp_path, monkeypatch):
    source = Path(config.__file__).read_text(encoding="utf-8")
    facade_source = Path(bridge.__file__).read_text(encoding="utf-8")
    assert ":\\Users\\" not in source
    assert ":\\Users\\" not in facade_source

    environment_base = _base_with_deck(tmp_path, "environment")
    project_base = _base_with_deck(tmp_path, "project")
    source_base = _base_with_deck(tmp_path, "source")
    monkeypatch.setenv("QTRADE_BASE_DIR", str(environment_base))
    monkeypatch.setattr(bridge, "DEFAULT_SELF_BASE", project_base)
    monkeypatch.setattr(bridge, "DEFAULT_SRC_BASE", source_base)

    assert bridge.base_dir() == environment_base
    monkeypatch.setenv("QTRADE_BASE_DIR", str(tmp_path / "missing"))
    assert bridge.base_dir() == project_base
    monkeypatch.delenv("QTRADE_BASE_DIR")
    assert bridge.base_dir() == project_base
    (project_base / "deck").rmdir()
    assert bridge.base_dir() == source_base


def test_facade_monkeypatch_reaches_handler(tmp_path, monkeypatch):
    base = _base_with_deck(tmp_path, "adapter-base")
    (base / "ui_v2" / "pages").mkdir(parents=True)
    (base / "ui_v2" / "pages" / "portal.html").write_text(
        "<html><head></head><body>synthetic</body></html>",
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "base_dir", lambda: base)

    handler = _FakeHandler("/portal")
    assert bridge.QtradeDeckHandler(handler).handle_get("/portal") is True
    assert b"#sidebar{display:none!important}" in handler.wfile.getvalue()


def test_handler_high_risk_proxy_branches_are_local_or_structured(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("niuapi fallback must not access a port"),
    )
    niuapi = _FakeHandler("/api/proxy/niuapi/sessions")
    assert QtradeDeckHandler(niuapi, base_dir_fn=lambda: Path("."))\
        ._proxy_get("/api/proxy/niuapi/sessions") is True
    assert niuapi.json_calls == [(200, {"personas": []})]

    captured = {}

    class _JsonResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _JsonResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    quantapi = _FakeHandler("/api/proxy/quantapi/health?mode=test")
    assert QtradeDeckHandler(quantapi, harness_port_fn=lambda: 3999)\
        ._proxy_get("/api/proxy/quantapi/health") is True
    assert captured == {
        "url": "http://127.0.0.1:3080/quantapi/health?mode=test",
        "timeout": 40,
    }
    assert quantapi.json_calls == [(200, {"ok": True})]

    build_mode = _FakeHandler()
    QtradeDeckHandler(build_mode, base_dir_fn=lambda: Path("."))\
        .serve_live("build_mode")
    assert build_mode.json_calls == [(200, {"mode": "dev", "hint": "内部测试构建，非公测发布版"})]


def test_decisions_write_and_start_buy_sync_on_background_boundary(tmp_path, monkeypatch):
    thread_calls = []

    class _Thread:
        def __init__(self, target, args, daemon):
            thread_calls.append((target, args, daemon))

        def start(self):
            thread_calls[-1] = (*thread_calls[-1], "started")

    monkeypatch.setattr(decisions.threading, "Thread", _Thread)
    response = _FakeHandler()
    record = {"action": "buy", "code": "SYNTH", "source": "test"}
    decisions.decide(
        response,
        record,
        base_dir_fn=lambda: tmp_path,
        prepare_sys_path_fn=lambda _base: None,
    )

    saved = list((tmp_path / "logs").glob("deck_decisions_*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8")) == [record]
    assert response.json_calls == [(200, {"ok": True, "saved": saved[0].name, "action": "buy"})]
    assert len(thread_calls) == 1
    assert thread_calls[0][1] == (tmp_path, record, None, None)
    assert thread_calls[0][2:] == (True, "started")


def test_decide_bg_sync_uses_mocked_portfolio_without_external_base(tmp_path, monkeypatch):
    calls = []
    strategy = types.ModuleType("strategy")
    strategy.__path__ = []
    portfolio = types.ModuleType("strategy.portfolio")
    portfolio.sync_from_decisions = lambda: calls.append("sync")
    monkeypatch.setitem(sys.modules, "strategy", strategy)
    monkeypatch.setitem(sys.modules, "strategy.portfolio", portfolio)

    decisions.decide_bg_sync(tmp_path)
    assert calls == ["sync"]


def test_runtime_disabled_and_already_running_paths_do_not_spawn():
    forbidden_process = _FakeProcessModule()
    disabled_socket = types.SimpleNamespace(
        socket=lambda: pytest.fail("disabled runtime must not probe a socket")
    )
    runtime.ensure_harness(
        env={"QTRADE_NO_HARNESS": "1"},
        socket_module=disabled_socket,
        subprocess_module=forbidden_process,
    )
    assert forbidden_process.calls == []

    running_process = _FakeProcessModule()
    runtime.ensure_harness(
        env={},
        socket_module=_FakeSocketModule(),
        shutil_module=types.SimpleNamespace(which=lambda _: pytest.fail("running base must stop after probe")),
        subprocess_module=running_process,
    )
    assert running_process.calls == []


def test_runtime_missing_dependency_and_start_command_are_safe(tmp_path):
    missing_process = _FakeProcessModule()
    runtime.ensure_harness(
        base_dir_fn=lambda: tmp_path,
        default_src_base=tmp_path / "source",
        env={},
        socket_module=_FakeSocketModule(ConnectionRefusedError()),
        shutil_module=types.SimpleNamespace(which=lambda _: None),
        subprocess_module=missing_process,
        os_name="nt",
    )
    assert missing_process.calls == []

    harness = tmp_path / "harness"
    (harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib").mkdir(parents=True)
    (harness / "home" / "profiles" / "web" / "plugins").mkdir(parents=True)
    (harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").write_text("", encoding="utf-8")
    (harness / "home" / "profiles" / "web" / "plugins" / "dsq-quant-bridge.js").write_text(
        "", encoding="utf-8"
    )
    (harness / "home" / ".credentials.yaml").write_text("", encoding="utf-8")
    process = _FakeProcessModule()
    runtime.ensure_harness(
        base_dir_fn=lambda: tmp_path,
        default_src_base=tmp_path / "source",
        harness_port=3210,
        env={"TEST_ENV": "1"},
        socket_module=_FakeSocketModule(ConnectionRefusedError()),
        shutil_module=types.SimpleNamespace(which=lambda _: "node-test"),
        subprocess_module=process,
        os_name="nt",
    )
    assert len(process.calls) == 1
    args, kwargs = process.calls[0]
    assert args[0] == [
        "node-test",
        str(harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"),
        "web",
        "--port",
        "3210",
    ]
    assert kwargs["cwd"] == str(harness)
    assert kwargs["env"]["DSH_HOME"] == str(harness / "home")
    assert kwargs["creationflags"] == process.DETACHED_PROCESS


def test_runtime_daily_update_disabled_marker_and_command(tmp_path):
    no_spawn = _FakeProcessModule()
    runtime.maybe_auto_update(
        base_dir_fn=lambda: tmp_path,
        env={"QTRADE_NO_AUTOUPDATE": "1"},
        subprocess_module=no_spawn,
    )
    assert no_spawn.calls == []

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "pipeline_full_v2_done.txt").write_text("done", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "auto_update_daily.py"
    script.write_text("", encoding="utf-8")
    (tmp_path / "data" / "cache").mkdir(parents=True)
    marker = tmp_path / "data" / "cache" / "last_auto_update.txt"
    marker.write_text("2026-08-25", encoding="utf-8")
    runtime.maybe_auto_update(
        base_dir_fn=lambda: tmp_path,
        env={},
        subprocess_module=no_spawn,
        today_fn=lambda: "2026-08-25",
    )
    assert no_spawn.calls == []

    marker.unlink()
    process = _FakeProcessModule()
    runtime.maybe_auto_update(
        base_dir_fn=lambda: tmp_path,
        env={"TEST_ENV": "1"},
        subprocess_module=process,
        os_name="nt",
        today_fn=lambda: "2026-08-25",
        python_executable="python-test",
    )
    args, kwargs = process.calls[0]
    assert args[0] == ["python-test", "-X", "utf8", str(script)]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["LWQUANT_CACHE_DIR"] == str(tmp_path / "data" / "cache")
    assert kwargs["creationflags"] == process.DETACHED_PROCESS
