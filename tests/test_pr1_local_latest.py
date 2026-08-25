# -*- coding: utf-8 -*-
"""PR 1 本地最新版：来源、每日更新与桥接回退的确定性回归测试。"""
import datetime as dt
import os
import shutil
import subprocess
import sys
import urllib.request
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

import qtrade_base_bridge as bridge
import scripts.daily_update_1830 as daily_update
import server


class _FakeHandler:
    def __init__(self, path="/"):
        self.path = path
        self.headers = {}
        self.json_calls = []
        self.byte_calls = []
        self.server = type("Server", (), {"server_address": ("127.0.0.1", 49123)})()
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


class _FakeUpstream:
    def __init__(self, body, content_type="application/json", status=200):
        self._body = body
        self.headers = {"Content-Type": content_type}
        self.status = status

    def read(self):
        return self._body


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"source": "决策", "buy_reason": "策略信号"}, "决策"),
        ({"source": "策略", "buy_reason": "决策买入：旧记录"}, "策略"),
        ({"buy_reason": "决策买入：Pitch 审批"}, "决策"),
        ({"buy_reason": "均线趋势信号"}, "策略"),
        ({"source": "未知", "buy_reason": "均线趋势信号"}, "策略"),
    ],
)
def test_position_source_classification(meta, expected):
    assert server.EngineAutoPaperTrader._position_source(meta) == expected


def test_daily_update_dry_run_never_executes_subprocess(tmp_path, monkeypatch):
    deck = tmp_path / "deepseek-harness-quant"
    (deck / "logs").mkdir(parents=True)
    (deck / "logs" / "opp_pool_20260825.json").write_text("{}", encoding="utf-8")
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update, "PY", "python-under-test")

    calls = []

    def forbidden_run(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("dry-run 不应执行子进程")

    monkeypatch.setattr(daily_update.subprocess, "run", forbidden_run)

    assert daily_update.main(["--dry-run"], today=dt.date(2026, 8, 25)) == 0
    text = log.read_text(encoding="utf-8")
    assert "DRY" in text
    assert "pitch_v2.py" in text
    assert text.count("RUN:") == 5
    assert calls == []


def test_daily_update_weekend_skips_before_base_check(tmp_path, monkeypatch):
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", tmp_path / "missing-base")
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update.subprocess, "run", lambda *args, **kwargs: pytest.fail("周末不应执行"))

    assert daily_update.main(["--dry"], today=dt.date(2026, 8, 29)) == 0
    text = log.read_text(encoding="utf-8")
    assert "周末" in text
    assert "底座不存在" not in text


def test_daily_update_missing_base_returns_failure_without_execution(tmp_path, monkeypatch):
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", tmp_path / "missing-base")
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update.subprocess, "run", lambda *args, **kwargs: pytest.fail("底座缺失不应执行"))

    assert daily_update.main([], today=dt.date(2026, 8, 28)) == 1
    text = log.read_text(encoding="utf-8")
    assert "底座不存在" in text
    assert "RUN:" not in text


def test_daily_update_deck_dir_cli_overrides_environment(tmp_path, monkeypatch):
    env_deck = tmp_path / "env-deck"
    cli_deck = tmp_path / "cli-deck"
    cli_deck.mkdir()
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", tmp_path / "default-deck")
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update, "PY", "python-under-test")
    monkeypatch.setenv("QTRADE_DECK_DIR", str(env_deck))

    assert daily_update.main(["--dry-run", "--deck-dir", str(cli_deck)], today=dt.date(2026, 8, 25)) == 0
    text = log.read_text(encoding="utf-8")
    assert str(cli_deck / "scripts" / "auto_update_daily.py") in text
    assert str(env_deck) not in text


def test_daily_update_fails_fast_after_nonzero_step(tmp_path, monkeypatch):
    deck = tmp_path / "deck"
    (deck / "logs").mkdir(parents=True)
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update, "PY", "python-under-test")

    calls = []

    def fake_run(cmd, cwd, timeout):
        calls.append((cmd, cwd, timeout))
        return SimpleNamespace(returncode=7 if len(calls) == 2 else 0)

    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)

    assert daily_update.main([], today=dt.date(2026, 8, 25)) == 1
    assert len(calls) == 2
    text = log.read_text(encoding="utf-8")
    assert "FAIL: 步骤返回 7" in text
    assert "已停止后续步骤" in text
    assert "scan.py" not in " ".join(str(x) for x in calls[1][0])


def test_daily_update_returns_failure_after_step_exception(tmp_path, monkeypatch):
    deck = tmp_path / "deck"
    (deck / "logs").mkdir(parents=True)
    log = tmp_path / "daily.log"
    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", log)
    monkeypatch.setattr(daily_update, "PY", "python-under-test")

    calls = []

    def fake_run(cmd, cwd, timeout):
        calls.append(cmd)
        if len(calls) == 3:
            raise OSError("mock executor failure")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)

    assert daily_update.main([], today=dt.date(2026, 8, 25)) == 1
    assert len(calls) == 3
    text = log.read_text(encoding="utf-8")
    assert "FAIL: 步骤执行异常" in text
    assert "已停止后续步骤" in text


def test_batch_launcher_runs_under_cmd_and_fails_safely(tmp_path):
    if os.name != "nt":
        pytest.skip("需要 Windows cmd.exe")

    repo_root = Path(__file__).resolve().parents[1]
    temp_root = tmp_path / "qtrade-cmd"
    scripts_dir = temp_root / "scripts"
    deck = tmp_path / "empty-deck"
    fake_local_app_data = tmp_path / "LocalAppData"
    fake_python_dir = fake_local_app_data / "Programs" / "Python" / "PythonTest"
    scripts_dir.mkdir(parents=True)
    deck.mkdir()
    fake_python_dir.mkdir(parents=True)

    batch_src = repo_root / "scripts" / "daily_update_1830.bat"
    shutil.copyfile(batch_src, scripts_dir / batch_src.name)
    shutil.copyfile(repo_root / "scripts" / "daily_update_1830.py", scripts_dir / "daily_update_1830.py")

    # Put a runnable copy only in the user-level install pattern; PATH has no Python.
    runtime_exe = Path(sys.executable)
    shutil.copy2(runtime_exe, fake_python_dir / "python.exe")
    for dll in runtime_exe.parent.glob("*.dll"):
        shutil.copy2(dll, fake_python_dir / dll.name)
    pyvenv_cfg = runtime_exe.parent.parent / "pyvenv.cfg"
    if pyvenv_cfg.exists():
        shutil.copy2(pyvenv_cfg, fake_python_dir / "pyvenv.cfg")

    env = os.environ.copy()
    env["QTRADE_DECK_DIR"] = str(deck)
    env["LocalAppData"] = str(fake_local_app_data)
    if pyvenv_cfg.exists():
        env.pop("PYTHONHOME", None)
    else:
        env["PYTHONHOME"] = sys.prefix
    system_root = Path(env.get("SystemRoot", r"C:\Windows"))
    env["PATH"] = str(system_root / "System32")
    batch_path = scripts_dir / batch_src.name
    cmd = env.get("ComSpec", str(system_root / "System32" / "cmd.exe"))
    result = subprocess.run(
        [cmd, "/d", "/c", str(batch_path)],
        cwd=str(temp_root),
        env=env,
        capture_output=True,
        timeout=60,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert result.returncode != 255
    assert b"not recognized" not in output.lower()
    assert b"unexpected" not in output.lower()
    assert b"leDelayedExpansion" not in output
    batch_log = temp_root / "logs" / "daily_update_1830_bat.log"
    daily_log = temp_root / "logs" / "daily_update_1830.log"
    assert b"Python=" in batch_log.read_bytes()
    assert b"FAIL:" in daily_log.read_bytes()


def test_bridge_niuapi_fallback_is_local(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("不应访问真实端口"))
    handler = _FakeHandler("/api/proxy/niuapi/sessions")

    assert bridge.QtradeDeckHandler(handler)._proxy_get("/api/proxy/niuapi/sessions") is True
    assert handler.json_calls == [(200, {"personas": []})]


def test_bridge_proxy_html_fallback_returns_json_error(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *args, **kwargs: _FakeUpstream(b"<html>not mounted</html>", "text/html"),
    )
    handler = _FakeHandler("/api/proxy/quantapi/health")

    assert bridge.QtradeDeckHandler(handler)._proxy_get("/api/proxy/quantapi/health") is True
    status, payload = handler.json_calls[-1]
    assert status == 200
    assert payload["ok"] is False
    assert "HTML" in payload["error"]


def test_bridge_quantapi_proxy_uses_fixed_local_upstream_without_network(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeUpstream(b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    handler = _FakeHandler("/api/proxy/quantapi/health?mode=test")

    assert bridge.QtradeDeckHandler(handler)._proxy_get("/api/proxy/quantapi/health") is True
    assert captured == {
        "url": "http://127.0.0.1:3080/quantapi/health?mode=test",
        "timeout": 40,
    }
    assert handler.json_calls == [(200, {"ok": True})]


def test_bridge_missing_snapshot_route_is_handled_without_upstream(tmp_path, monkeypatch):
    (tmp_path / "deck").mkdir()
    monkeypatch.setattr(bridge, "base_dir", lambda: tmp_path)
    handler = _FakeHandler("/api/harness")

    assert bridge.QtradeDeckHandler(handler).handle_get("/api/harness") is True
    status, payload = handler.json_calls[-1]
    assert status == 200
    assert payload["ok"] is False
    assert "harness_state.json" in payload["error"]


def test_batch_launcher_is_portable():
    bat = Path(__file__).resolve().parents[1] / "scripts" / "daily_update_1830.bat"
    text = bat.read_text(encoding="utf-8")
    assert "%~dp0" in text
    assert "C:\\Users\\ASUS\\qtrade" not in text
    assert "Python312" not in text


def test_batch_launcher_interpreter_discovery_and_failure_text():
    bat = Path(__file__).resolve().parents[1] / "scripts" / "daily_update_1830.bat"
    text = bat.read_text(encoding="utf-8")
    markers = [
        r"%ROOT_DIR%\.venv\Scripts\python.exe",
        "where python",
        "where py",
        r"%LocalAppData%\Programs\Python\Python*",
        r"%%~fD\python.exe",
        "ERROR: Python interpreter not found.",
        "exit /b 9009",
    ]
    positions = [text.index(marker) for marker in markers]
    assert positions == sorted(positions)
    assert 'set "PYTHON_ARGS=-3"' in text
    assert '"%PYTHON_EXE%" %PYTHON_ARGS% -X utf8' in text
    assert "python -X utf8" not in text
    assert "py -X utf8" not in text


def test_batch_launcher_uses_crlf_without_lone_lf():
    bat = Path(__file__).resolve().parents[1] / "scripts" / "daily_update_1830.bat"
    data = bat.read_bytes()
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
