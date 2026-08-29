"""Offline contracts for observable and recoverable manual update jobs."""

from __future__ import annotations

import datetime as dt
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import qtrade_adapters.deepseek_harness.runtime as update_runtime
import scripts.daily_update_1830 as daily_update
import server


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _patch_freshness(monkeypatch, target: dt.date, deck: Path) -> None:
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_artifacts",
        lambda _deck: daily_update.freshness.ArtifactSnapshot({}),
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_portal_baseline",
        lambda _deck: {"coverage": 1},
    )
    def verified(*_args, **_kwargs):
        return {
            "verified": True,
            "as_of": target.isoformat(),
            "source": "factor_artifacts",
            "reason": "verified",
        }
    monkeypatch.setattr(daily_update.freshness, "verify_portal", verified)
    monkeypatch.setattr(daily_update.freshness, "verify_factors", verified)
    monkeypatch.setattr(
        daily_update.freshness,
        "verify_decision",
        lambda *_args, **kwargs: {
            "verified": True,
            "as_of": target.isoformat(),
            "source": "decision_artifact",
            "reason": "verified",
            "_pool_path": deck / "logs" / f"opp_pool_{target:%Y%m%d}.json",
            "pitch_verified": kwargs.get("require_pitch", False),
        },
    )
    monkeypatch.setattr(daily_update.freshness, "resolve_sync_destination", lambda _deck: None)
    monkeypatch.setattr(daily_update.freshness, "verify_sync", verified)


def test_daily_status_publishes_current_step_before_each_child(tmp_path, monkeypatch):
    target = dt.date(2026, 8, 28)
    deck = tmp_path / "deck"
    (deck / "logs").mkdir(parents=True)
    status = tmp_path / "state" / "daily.status.json"
    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", tmp_path / "state" / "daily.log")
    monkeypatch.setattr(daily_update, "PY", "python-test")
    _patch_freshness(monkeypatch, target, deck)
    observed = []

    def fake_run(command, **kwargs):
        payload = _read_json(status)
        observed.append((payload["step"], payload["progress"], payload["finished_at"]))
        assert kwargs.get("shell", False) is False
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)

    assert daily_update.main(
        ["--force", "--status-file", str(status)],
        today=target,
    ) == 0

    assert [item[0] for item in observed] == [
        "portal",
        "factors",
        "decision_scan",
        "decision_pitch_v2",
        "sync",
    ]
    assert [item[1]["completed"] for item in observed] == [0, 2, 4, 6, 8]
    assert all(item[1]["total"] == daily_update.PIPELINE_STEP_COUNT for item in observed)
    assert all(item[2] is None for item in observed)

    payload = _read_json(status)
    assert payload["state"] == "success"
    assert payload["finished_at"]
    assert payload["heartbeat_at"]
    assert payload["elapsed_seconds"] >= 0
    assert payload["progress"] == {
        "completed": daily_update.PIPELINE_STEP_COUNT,
        "total": daily_update.PIPELINE_STEP_COUNT,
        "current": None,
    }
    assert len(payload["steps"]) == daily_update.PIPELINE_STEP_COUNT


class _ObservableProcess:
    def __init__(self, output: bytes):
        self.stdout = BytesIO(output)
        self.stderr = None
        self.pid = 92001
        self.returncode = 0

    def poll(self):
        return self.returncode


def test_manual_step_captures_redacted_output_with_argv_only(tmp_path, monkeypatch):
    process = _ObservableProcess(b"api_key=sk-secret-token C:\\Users\\ASUS\\private\\x\n")
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(daily_update.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(daily_update, "LOG", tmp_path / "logs" / "pipeline.log")

    result = daily_update._execute_observable(
        ["python", "safe.py"],
        deck_dir=tmp_path,
        step_name="portal",
        heartbeat=lambda: None,
    )

    assert result.ok is True
    assert calls[0][1]["shell"] is False
    assert calls[0][1]["stderr"] is daily_update.subprocess.STDOUT
    text = daily_update.LOG.read_text(encoding="utf-8")
    assert "<redacted>" in text
    assert "<path>" in text
    assert "sk-secret-token" not in text
    assert "C:\\Users\\ASUS\\private" not in text


class _ManualProcess:
    def __init__(self):
        self.pid = 92002
        self.returncode = None
        self.terminated = threading.Event()
        self.stdout = BytesIO(b"token=private-value C:\\Users\\ASUS\\secret\n")
        self.stderr = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            if self.terminated.is_set():
                self.returncode = -15
                return self.returncode
            raise subprocess.TimeoutExpired(["daily_update_1830.py"], timeout)
        return self.returncode

    def terminate(self):
        self.terminated.set()

    def kill(self):
        self.terminated.set()
        self.returncode = -9


class _ManualProcesses:
    PIPE = object()
    STDOUT = object()
    CREATE_NEW_PROCESS_GROUP = 0
    CREATE_NO_WINDOW = 0

    def __init__(self):
        self.process = _ManualProcess()
        self.calls = []
        self.started = threading.Event()

    def Popen(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.started.set()
        return self.process


def test_manual_controller_stop_is_bounded_and_output_is_not_devnull(tmp_path):
    processes = _ManualProcesses()
    status = tmp_path / "state" / "status.json"
    log_file = tmp_path / "state" / "manual.log"
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=tmp_path / "state" / "manual.lock",
        pipeline_lock_path=tmp_path / "state" / "pipeline.lock",
        log_file=log_file,
        clock=lambda: dt.datetime(2026, 8, 28, 18, 31),
        subprocess_module=processes,
    )

    assert controller.start()["state"] == "accepted"
    worker = controller._worker
    assert worker is not None and worker.daemon is True
    assert processes.started.wait(timeout=1)
    assert processes.calls[0][1]["stdout"] is processes.PIPE
    assert processes.calls[0][1]["stderr"] is processes.STDOUT
    command = processes.calls[0][0]
    assert command[command.index("--log-file") + 1] == str(log_file)
    assert processes.calls[0][1]["env"]["QTRADE_UPDATE_OBSERVABLE"] == "1"

    started = time.monotonic()
    stopped = controller.stop(timeout=1)
    elapsed = time.monotonic() - started

    assert elapsed < 2
    assert stopped["state"] == "aborted"
    assert stopped["reason"] == "application_shutdown"
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert processes.process.terminated.is_set()
    assert not (tmp_path / "state" / "manual.lock").exists()
    payload = _read_json(status)
    assert payload["state"] == "aborted"
    assert payload["finished_at"]
    text = log_file.read_text(encoding="utf-8")
    assert "private-value" not in text
    assert "C:\\Users\\ASUS\\secret" not in text

    second = controller.stop(timeout=1)
    assert second["state"] == "aborted"
    assert second["reason"] == "application_shutdown"


def test_manual_run_fn_exception_is_persisted_as_terminal_failure(tmp_path):
    target_time = dt.datetime(2026, 8, 28, 18, 31)

    def failing_run(*_args, **_kwargs):
        raise RuntimeError("private path C:/Users/ASUS/secret")

    status = tmp_path / "state" / "status.json"
    lock = tmp_path / "state" / "manual.lock"
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=lock,
        pipeline_lock_path=tmp_path / "state" / "daily.lock",
        clock=lambda: target_time,
        run_fn=failing_run,
    )
    accepted = controller.start()
    worker = controller._worker
    assert accepted["state"] == "accepted"
    assert worker is not None
    worker.join(timeout=1)
    assert not worker.is_alive()

    payload = _read_json(status)
    assert payload["state"] == "failure"
    assert payload["reason"] == "update_failed"
    assert payload["finished_at"]
    assert controller.status()["state"] == "failure"
    assert not lock.exists()
    assert "private path" not in json.dumps(payload)


def test_stale_running_recovery_never_removes_a_lock(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    lock = tmp_path / "daily.lock"
    stale_payload = {
        "schema_version": 1,
        "state": "running",
        "trade_date": "2026-08-28",
        "started_at": "2026-08-28T10:00:00",
        "heartbeat_at": "2026-08-28T10:00:00",
        "owner_pid": 92003,
        "steps": [],
    }
    status.write_text(
        json.dumps(stale_payload),
        encoding="utf-8",
    )
    monkeypatch.setattr(daily_update, "_pid_is_alive", lambda _pid: False)
    recovered = daily_update.recover_stale_status(
        status,
        lock,
        now=dt.datetime(2026, 8, 28, 11, 0),
    )

    assert recovered is not None
    assert recovered["state"] == "aborted"
    assert recovered["reason"] == "stale_running"
    assert recovered["finished_at"]
    assert not lock.exists()

    live_status = tmp_path / "live.json"
    live_status.write_text(json.dumps(stale_payload), encoding="utf-8")
    monkeypatch.setattr(daily_update, "_pid_is_alive", lambda _pid: True)
    unchanged = daily_update.recover_stale_status(
        live_status,
        tmp_path / "live.lock",
        now=dt.datetime(2026, 8, 28, 11, 0),
    )
    assert unchanged["state"] == "running"


def test_manual_stale_recovery_reclaims_only_matching_dead_lease(tmp_path, monkeypatch):
    now = dt.datetime(2026, 8, 28, 18, 31)
    status = tmp_path / "status.json"
    lock = tmp_path / "manual.lock"
    payload = {
        "schema_version": 1,
        "state": "running",
        "trade_date": now.date().isoformat(),
        "started_at": "2026-08-28T10:00:00",
        "heartbeat_at": "2026-08-28T10:00:00",
        "owner_pid": 92005,
    }
    status.write_text(json.dumps(payload), encoding="utf-8")
    lock.write_text("pid=92005\n", encoding="ascii")
    monkeypatch.setattr(update_runtime, "_pid_is_alive", lambda _pid: False)
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=lock,
        pipeline_lock_path=tmp_path / "pipeline.lock",
        clock=lambda: now,
        run_fn=lambda *args, **kwargs: pytest.fail("recovery must not start a worker"),
    )

    with controller._lock:
        recovered = controller._recover_stale_status_locked(now, now.date())

    assert recovered["state"] == "aborted"
    assert recovered["reason"] == "stale_running"
    assert not lock.exists()

    foreign_status = tmp_path / "foreign-status.json"
    foreign_lock = tmp_path / "foreign.lock"
    foreign_status.write_text(json.dumps(payload), encoding="utf-8")
    foreign_lock.write_text("pid=92006\n", encoding="ascii")
    foreign = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=foreign_status,
        lock_path=foreign_lock,
        pipeline_lock_path=tmp_path / "foreign-pipeline.lock",
        clock=lambda: now,
    )
    monkeypatch.setattr(update_runtime, "_pid_is_alive", lambda pid: pid == 92006)
    with foreign._lock:
        unchanged = foreign._recover_stale_status_locked(now, now.date())

    assert unchanged["state"] == "running"
    assert foreign_lock.exists()


@pytest.mark.parametrize("status_kind", ["missing", "success", "empty"])
def test_manual_dead_lease_recovery_does_not_depend_on_status_owner(
    tmp_path, monkeypatch, status_kind
):
    now = dt.datetime(2026, 8, 28, 18, 31)
    status = tmp_path / status_kind / "status.json"
    lock = status.with_name("manual.lock")
    lock.parent.mkdir(parents=True)
    lock.write_text("pid=92008\n", encoding="ascii")
    if status_kind == "success":
        status.write_text(
            json.dumps({"state": "success", "trade_date": now.date().isoformat()}),
            encoding="utf-8",
        )
    elif status_kind == "empty":
        status.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(update_runtime, "_pid_is_alive", lambda _pid: False)
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=lock,
        pipeline_lock_path=status.with_name("daily_update_1830.lock"),
        clock=lambda: now,
        run_fn=lambda *args, **kwargs: pytest.fail("stale recovery must not start a worker"),
    )

    with controller._lock:
        recovered = controller._recover_stale_status_locked(now, now.date())

    assert not lock.exists()
    if status_kind == "success":
        assert recovered["state"] == "success"
    else:
        assert recovered["state"] == "idle"


def test_manual_dead_lease_rechecks_identity_and_never_touches_pipeline_lock(tmp_path, monkeypatch):
    manual = tmp_path / "manual.lock"
    manual.write_text("pid=92009\n", encoding="ascii")
    identity = (1, 2)
    replacement = (92010, (1, 3))
    reads = iter([(92009, identity), replacement])
    monkeypatch.setattr(update_runtime, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(update_runtime, "_read_lease_record", lambda _path: next(reads))

    assert update_runtime._reclaim_dead_lease(manual) is False
    assert manual.exists()

    pipeline = tmp_path / "daily_update_1830.lock"
    pipeline.write_text("pid=92011\n", encoding="ascii")
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=tmp_path / "status.json",
        lock_path=tmp_path / "another-manual.lock",
        pipeline_lock_path=pipeline,
        clock=lambda: dt.datetime(2026, 8, 28, 18, 31),
    )
    with controller._lock:
        controller._recover_stale_status_locked(
            dt.datetime(2026, 8, 28, 18, 31), dt.date(2026, 8, 28)
        )
    assert pipeline.exists()


def test_late_parent_terminal_write_cannot_replace_newer_job(tmp_path):
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": "running",
                "trade_date": "2026-08-28",
                "job_id": "b" * 32,
                "finished_at": None,
            }
        ),
        encoding="utf-8",
    )

    update_runtime._write_parent_terminal_status(
        status,
        dt.date(2026, 8, 28),
        state="aborted",
        reason="application_shutdown",
        expected_job_id="a" * 32,
        now=lambda: dt.datetime(2026, 8, 28, 18, 32),
    )

    payload = _read_json(status)
    assert payload["state"] == "running"
    assert payload["job_id"] == "b" * 32


def test_active_running_status_blocks_a_second_manual_controller(tmp_path, monkeypatch):
    now = dt.datetime(2026, 8, 28, 18, 31)
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps({
            "schema_version": 1,
            "state": "running",
            "trade_date": now.date().isoformat(),
            "started_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
            "owner_pid": 92004,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(update_runtime, "_pid_is_alive", lambda _pid: True)
    controller = update_runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path,
        status_file=status,
        lock_path=tmp_path / "manual.lock",
        pipeline_lock_path=tmp_path / "pipeline.lock",
        clock=lambda: now,
        run_fn=lambda *args, **kwargs: pytest.fail("live owner must block a second worker"),
    )

    result = controller.start()

    assert result["state"] == "running"
    assert result["reason"] == "already_running"
    assert not (tmp_path / "manual.lock").exists()


def test_windows_managed_stop_targets_only_owned_pid(monkeypatch):
    process = _ManualProcess()
    taskkills = []

    def fake_run(command, **kwargs):
        taskkills.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(update_runtime.os, "name", "nt")
    monkeypatch.setattr(update_runtime.subprocess, "run", fake_run)
    update_runtime._terminate_managed_process(process, update_runtime.subprocess)

    assert taskkills
    assert taskkills[0][0] == ["taskkill", "/PID", str(process.pid), "/T", "/F"]
    assert taskkills[0][1]["shell"] is False
    assert process.terminated.is_set()


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree contract")
def test_windows_owned_child_process_is_stopped_within_short_bound():
    flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        started = time.monotonic()
        update_runtime._terminate_managed_process(process, subprocess)
        elapsed = time.monotonic() - started
        assert elapsed < 4
        assert process.poll() is not None
        process.wait(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)


def test_manual_progress_and_stop_payload_strip_private_fields(tmp_path):
    payload = {
        "state": "running",
        "reason": "pipeline_running",
        "step": "C:/Users/ASUS/private-command",
        "progress": {"completed": 2, "total": 10, "current": "python --secret"},
        "command": "python C:/Users/ASUS/private.py",
        "environment": {"API_KEY": "secret"},
    }
    status = tmp_path / "status.json"
    status.write_text(json.dumps(payload), encoding="utf-8")

    clean = update_runtime.read_manual_update_status(status)
    assert clean["step"] is None
    assert clean["progress"]["current"] is None
    assert "command" not in clean
    assert "environment" not in clean
    assert "C:/Users/ASUS" not in json.dumps(clean)

    api_clean = server._safe_manual_update_payload(payload)
    assert api_clean["step"] is None
    assert api_clean["progress"]["current"] is None
    assert "command" not in api_clean
    assert "environment" not in api_clean
