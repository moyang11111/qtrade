from __future__ import annotations

import datetime
import json
from pathlib import Path
import subprocess
import sys
import threading

from qtrade_adapters.deepseek_harness import portal_refresh_coordinator as coordinator
from qtrade_adapters.deepseek_harness.portal_refresh import publish_snapshot
from qtrade_adapters.deepseek_harness.portal_refresh_worker import PortalRefreshPlan
import server


TARGET = "2026-08-28"
SYMBOLS = ("600001", "600002", "600003", "000001", "002001")


class FakeProvider:
    PROVIDER_VERSION = "fixture-provider-v1"


class FakeWorker:
    result = {
        "state": "success", "reason": "completed", "total": 5, "completed": 5,
        "published_generation": "2" * 64,
        "published_content_sha256": "3" * 64,
    }

    def __init__(self, **_kwargs):
        pass

    def run(self, _plan, *, provider):
        assert isinstance(provider, FakeProvider)
        return dict(self.result)


def _paths(tmp_path: Path):
    user_data = tmp_path / "user-data"
    state = user_data / "state"
    state.mkdir(parents=True)
    return user_data, state, state / "daily_update_1830.status.json"


def _builder(**_kwargs):
    plan = PortalRefreshPlan(
        symbols=SYMBOLS,
        target_date=TARGET,
        universe_token="0" * 64,
        calendar_verified=True,
        calendar_token="1" * 64,
        provider_version=FakeProvider.PROVIDER_VERSION,
    )
    return plan, FakeProvider()


def test_child_success_is_portal_only_and_safe(tmp_path):
    user_data, state, status = _paths(tmp_path)
    assert coordinator._run_child_job(
        base_dir=tmp_path / "base",
        target_date=TARGET,
        state_dir=state,
        user_data_dir=user_data,
        status_file=status,
        job_id="a" * 32,
        plan_builder=_builder,
        worker_factory=FakeWorker,
    ) == 0

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["mode"] == "portal_only"
    assert payload["state"] == "portal_success"
    assert payload["outputs"] == {"portal": True, "factors": False, "decision": False, "sync": False}
    assert payload["freshness"]["portal"] == {
        "verified": True,
        "as_of": TARGET,
        "source": "qtrade_mirror",
        "reason": "verified",
        "total": 5,
        "coverage": 5,
    }
    assert payload["output_meta"]["portal"] == {
        "generation": "2" * 64,
        "content_sha256": "3" * 64,
        "universe_token": "0" * 64,
        "target_date": TARGET,
        "total": 5,
    }
    assert payload["finished_at"]
    assert "base" not in json.dumps(payload)


def test_child_calendar_closed_is_safe_skip_without_worker(tmp_path):
    user_data, state, status = _paths(tmp_path)
    called = []

    def closed_builder(**_kwargs):
        raise coordinator.PortalPlanError("calendar_closed")

    def forbidden_worker(**_kwargs):
        called.append(True)
        raise AssertionError("closed day must not start a worker")

    assert coordinator._run_child_job(
        base_dir=tmp_path / "base",
        target_date=TARGET,
        state_dir=state,
        user_data_dir=user_data,
        status_file=status,
        job_id="b" * 32,
        plan_builder=closed_builder,
        worker_factory=forbidden_worker,
    ) == 0
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "skip"
    assert payload["reason"] == "calendar_closed"
    assert called == []


def test_child_worker_failure_has_terminal_disk_status(tmp_path):
    user_data, state, status = _paths(tmp_path)

    class Failed(FakeWorker):
        result = {"state": "failure", "reason": "provider_failed", "total": 5, "completed": 2}

    assert coordinator._run_child_job(
        base_dir=tmp_path / "base",
        target_date=TARGET,
        state_dir=state,
        user_data_dir=user_data,
        status_file=status,
        job_id="c" * 32,
        plan_builder=_builder,
        worker_factory=Failed,
    ) == 1
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "failure"
    assert payload["reason"] == "provider_failed"
    assert payload["finished_at"]


def test_parent_uses_fixed_owned_child_command_and_no_external_env(tmp_path, monkeypatch):
    user_data, state, status = _paths(tmp_path)
    calls = []

    class Completed:
        pid = 12345

        def poll(self):
            return 0

    class FakeProcesses:
        STDOUT = -2
        PIPE = -1
        CREATE_NEW_PROCESS_GROUP = 0
        CREATE_NO_WINDOW = 0

        @staticmethod
        def Popen(command, **kwargs):
            calls.append((command, kwargs))
            ready = Path(command[command.index("--ready-file") + 1])
            ready.write_text(json.dumps({"job_id": command[-1], "ready": True}), encoding="utf-8")
            return Completed()

    monkeypatch.setattr(
        coordinator,
        "_worker_status",
        lambda *_args: {"state": "success", "total": 5, "completed": 5},
    )
    result = coordinator.run_portal_refresh(
        tmp_path / "base",
        datetime.date(2026, 8, 28),
        environment={
            "PYTHONPATH": "unsafe",
            "HTTPS_PROXY": "unsafe",
            "PATH": "C:/safe",
        },
        project_root=tmp_path,
        status_file=status,
        log_file=state / "daily_update_1830.log",
        python_executable="C:/stable/python.exe",
        stop_event=None,
        job_id="d" * 32,
        user_data_dir=user_data,
        subprocess_module=FakeProcesses,
    )
    assert result == 0
    command, kwargs = calls[0]
    assert command[:5] == ["C:/stable/python.exe", "-X", "utf8", "-m", "qtrade_adapters.deepseek_harness.portal_refresh_coordinator"]
    assert "--child" in command
    assert kwargs["shell"] is False
    assert kwargs["env"]["QTRADE_BASE_DIR"] == str(tmp_path / "base")
    assert "PYTHONPATH" not in kwargs["env"]
    assert "HTTPS_PROXY" not in kwargs["env"]


def test_parent_startup_failure_signals_and_writes_terminal_status(tmp_path):
    user_data, state, status = _paths(tmp_path)
    startup = __import__("threading").Event()
    result = {}

    class FailingProcesses:
        PIPE = -1
        STDOUT = -2

        @staticmethod
        def Popen(_command, **_kwargs):
            raise OSError("private child failure")

    assert coordinator.run_portal_refresh(
        tmp_path / "base",
        datetime.date(2026, 8, 28),
        project_root=tmp_path,
        status_file=status,
        log_file=state / "daily_update_1830.log",
        stop_event=None,
        job_id="e" * 32,
        user_data_dir=user_data,
        startup_event=startup,
        startup_result=result,
        subprocess_module=FailingProcesses,
    ) == 1
    assert startup.is_set()
    assert result == {"ready": False}
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "failure"
    assert payload["finished_at"]
    assert "private child" not in (state / "daily_update_1830.log").read_text(encoding="utf-8")


def test_manual_portal_acceptance_waits_for_owned_startup_ack(tmp_path):
    def acknowledged_run(_base, _target, **kwargs):
        kwargs["startup_result"]["ready"] = True
        kwargs["startup_event"].set()
        return 1

    controller = __import__(
        "qtrade_adapters.deepseek_harness.runtime",
        fromlist=["ManualUpdateController"],
    ).ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "base",
        project_root=tmp_path,
        status_file=tmp_path / "state.json",
        lock_path=tmp_path / "manual.lock",
        clock=lambda: datetime.datetime(2026, 8, 28, 18, 31),
        run_fn=acknowledged_run,
        mode="portal_only",
        user_data_dir=tmp_path / "user-data",
    )
    accepted = controller.start()
    assert accepted["state"] == "accepted"
    worker = controller._worker
    assert worker is not None
    worker.join(timeout=2)
    assert not worker.is_alive()


def test_manual_portal_startup_failure_returns_terminal_status(tmp_path):
    def rejected_run(_base, _target, **kwargs):
        kwargs["startup_result"]["ready"] = False
        kwargs["startup_event"].set()
        return 1

    controller = __import__(
        "qtrade_adapters.deepseek_harness.runtime",
        fromlist=["ManualUpdateController"],
    ).ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "base",
        project_root=tmp_path,
        status_file=tmp_path / "state.json",
        lock_path=tmp_path / "manual.lock",
        clock=lambda: datetime.datetime(2026, 8, 28, 18, 31),
        run_fn=rejected_run,
        mode="portal_only",
        user_data_dir=tmp_path / "user-data",
    )
    result = controller.start()
    assert result["state"] == "failure"
    assert result["accepted"] is False
    assert result["finished_at"]
    worker = controller._worker
    if worker is not None:
        worker.join(timeout=1)
        assert not worker.is_alive()


def test_owned_child_termination_is_bounded_and_only_uses_local_process(tmp_path):
    popen_kwargs = {
        "cwd": tmp_path,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "shell": False,
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        [coordinator.sys.executable, "-c", "import time; time.sleep(30)"],
        **popen_kwargs,
    )
    try:
        coordinator._terminate_child(process)
        assert process.wait(timeout=3) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)


def test_service_reload_requires_the_exact_published_generation(tmp_path):
    user_data, state, _ = _paths(tmp_path)
    symbols = list(SYMBOLS)

    def rows(close_offset=0.0):
        return {
            symbol: [{
                "code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
                "date": TARGET,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5 + min(close_offset, 0.5),
                "volume": 1000.0,
                "adjust": "qfq",
            }]
            for symbol in symbols
        }

    def metadata():
        return [{
            "code": symbol,
            "name": symbol,
            "exchange": "SH" if symbol.startswith("6") else "SZ",
            "risk_warning": None,
            "suspended": False,
            "listed": True,
            "tradable": True,
            "history_rows": 1,
            "latest_trade_date": TARGET,
            "computable": True,
            "eligible_reason": None,
        } for symbol in symbols]

    first = publish_snapshot(
        symbols, TARGET, rows(), metadata(), state_dir=state, user_data_dir=user_data,
        universe_token="first",
    )
    service = server.DataService(
        str(tmp_path / "csv"), live=False, portal_state_dir=state, portal_user_data_dir=user_data,
    )
    second = publish_snapshot(
        symbols, TARGET, rows(1.0), metadata(), state_dir=state, user_data_dir=user_data,
        universe_token="second",
    )
    expected = {"output_meta": {"portal": {
        "generation": second.manifest["generation"],
        "content_sha256": second.manifest["content_sha256"],
        "universe_token": second.manifest["universe_token"],
        "target_date": TARGET,
        "total": len(symbols),
    }}}

    assert service.reload_portal_snapshot({"output_meta": {"portal": {
        **expected["output_meta"]["portal"], "generation": first.manifest["generation"],
    }}}) is False
    assert service.mainboard_adapter.overlay_manifest["generation"] == first.manifest["generation"]
    assert service.reload_portal_snapshot(expected) is True
    assert service.mainboard_adapter.overlay_manifest["generation"] == second.manifest["generation"]


def test_service_reads_and_snapshot_switch_are_serialized(tmp_path):
    user_data, state, _ = _paths(tmp_path)
    first = publish_snapshot(
        SYMBOLS, TARGET, {
            symbol: [{
                "code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
                "date": TARGET,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "adjust": "qfq",
            }] for symbol in SYMBOLS
        },
        [{
            "code": symbol,
            "name": symbol,
            "exchange": "SH" if symbol.startswith("6") else "SZ",
            "risk_warning": None,
            "suspended": False,
            "listed": True,
            "tradable": True,
            "history_rows": 1,
            "latest_trade_date": TARGET,
            "computable": True,
            "eligible_reason": None,
        } for symbol in SYMBOLS],
        state_dir=state, user_data_dir=user_data, universe_token="first",
    )
    second = publish_snapshot(
        SYMBOLS, TARGET, {
            symbol: [{
                "code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
                "date": TARGET,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.75,
                "volume": 1000.0,
                "adjust": "qfq",
            }] for symbol in SYMBOLS
        },
        [{
            "code": symbol,
            "name": symbol,
            "exchange": "SH" if symbol.startswith("6") else "SZ",
            "risk_warning": None,
            "suspended": False,
            "listed": True,
            "tradable": True,
            "history_rows": 1,
            "latest_trade_date": TARGET,
            "computable": True,
            "eligible_reason": None,
        } for symbol in SYMBOLS],
        state_dir=state, user_data_dir=user_data, universe_token="second",
    )
    service = server.DataService(
        str(tmp_path / "csv"), live=False, portal_state_dir=state, portal_user_data_dir=user_data,
    )
    original_scan = service.mainboard_adapter.scan
    reader_entered = threading.Event()
    release_reader = threading.Event()

    def blocking_scan():
        reader_entered.set()
        release_reader.wait(timeout=2)
        return original_scan()

    service.mainboard_adapter.scan = blocking_scan
    read_result = []
    reload_result = []
    reader = threading.Thread(target=lambda: read_result.append(service.health_snapshot()))
    expected = {"output_meta": {"portal": {
        "generation": second.manifest["generation"],
        "content_sha256": second.manifest["content_sha256"],
        "universe_token": second.manifest["universe_token"],
        "target_date": TARGET,
        "total": len(SYMBOLS),
    }}}
    switched = threading.Event()
    reader.start()
    assert reader_entered.wait(timeout=1)
    switcher = threading.Thread(target=lambda: (
        reload_result.append(service.reload_portal_snapshot(expected)), switched.set()
    ))
    switcher.start()
    assert not switched.wait(timeout=0.1)
    release_reader.set()
    reader.join(timeout=2)
    switcher.join(timeout=2)
    assert not reader.is_alive()
    assert not switcher.is_alive()
    assert read_result == [{"mode": "csv", "symbols": len(SYMBOLS)}]
    assert reload_result == [True]
    assert service.mainboard_adapter.overlay_manifest["generation"] == second.manifest["generation"]
    assert first.manifest["generation"] != second.manifest["generation"]


def test_console_copy_describes_the_full_research_pipeline():
    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "control.html").read_text(encoding="utf-8")
    script = (root / "static" / "js" / "control.js").read_text(encoding="utf-8")
    assert "立即更新门户、因子和决策" in html
    assert "正在提交完整研究数据更新…" in script
    assert "门户数据已刷新；完整流水线仍待确认。" in script
