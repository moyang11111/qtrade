"""Deterministic contracts for the trading-day update script and scheduler."""

import datetime as dt
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import qtrade_adapters.deepseek_harness.runtime as runtime
import scripts.daily_update_1830 as daily_update


class FakeProcesses:
    def __init__(self, returncodes=None):
        self.calls = []
        self.returncodes = list(returncodes or [])

    def run(self, command, **kwargs):
        self.calls.append((command, kwargs))
        code = self.returncodes.pop(0) if self.returncodes else 0
        return SimpleNamespace(returncode=code)


class BlockingAutoProcess:
    def __init__(self):
        self.pid = 93001
        self.returncode = None
        self.started = threading.Event()
        self.terminated = threading.Event()

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


class BlockingAutoProcesses:
    PIPE = object()
    STDOUT = object()
    CREATE_NEW_PROCESS_GROUP = 0
    CREATE_NO_WINDOW = 0

    def __init__(self):
        self.process = BlockingAutoProcess()
        self.calls = []

    def Popen(self, command, **kwargs):
        self.calls.append((command, kwargs))
        self.process.started.set()
        return self.process


class FailingAutoProcesses:
    PIPE = object()
    STDOUT = object()

    def Popen(self, _command, **_kwargs):
        raise OSError("private path")


def _configure_daily(monkeypatch, tmp_path, deck=None):
    deck = tmp_path / "deck" if deck is None else deck
    deck.mkdir(parents=True, exist_ok=True)
    (deck / "logs").mkdir(exist_ok=True)
    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", tmp_path / "daily.log")
    monkeypatch.setattr(daily_update, "PY", "python-under-test")
    return deck


def _calendar(*dates):
    return lambda: set(dates)


def test_dry_run_lists_complete_plan_and_writes_structured_status(tmp_path, monkeypatch):
    deck = _configure_daily(monkeypatch, tmp_path)
    (deck / "logs" / "opp_pool_20260825.json").write_text("{}", encoding="utf-8")
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)
    status = tmp_path / "status.json"

    result = daily_update.main(
        ["--dry-run", "--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        calendar_loader=_calendar(dt.date(2026, 8, 25)),
    )

    assert result == 0
    assert processes.calls == []
    assert daily_update.LOG.read_text(encoding="utf-8").count("RUN:") == 5
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["trade_date"] == "2026-08-25"
    assert payload["state"] == "skip"
    assert payload["reason"] == "dry_run"
    assert [step["name"] for step in payload["steps"]] == [
        "portal",
        "factors",
        "decision_scan",
        "decision_pitch_v2",
        "sync",
    ]
    assert payload["outputs"] == {"portal": False, "decision": False, "factors": False, "sync": False}
    assert list(tmp_path.glob("*.tmp")) == []


def test_packaged_daily_script_imports_freshness_from_external_cwd(tmp_path):
    """The standalone script must find adapters without cwd/PYTHONPATH help."""

    script = Path(daily_update.__file__).resolve()
    probe = (
        "import runpy\n"
        f"namespace = runpy.run_path({str(script)!r}, run_name='qtrade_probe')\n"
        "print(namespace['ROOT'])\n"
        "module = namespace.get('freshness')\n"
        "print(module.__name__ if module is not None else 'NONE')\n"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout.splitlines()
    assert Path(output[-2]).resolve() == daily_update.ROOT
    assert output[-1] == "qtrade_adapters.deepseek_harness.freshness"


def test_packaged_resources_shape_imports_freshness_without_project_path(tmp_path):
    """A resources/qtrade copy must be self-locating when launched standalone."""

    packaged_root = tmp_path / "resources" / "qtrade"
    packaged_script = packaged_root / "scripts" / "daily_update_1830.py"
    packaged_script.parent.mkdir(parents=True)
    packaged_script.write_bytes(Path(daily_update.__file__).read_bytes())

    adapter_root = packaged_root / "qtrade_adapters"
    harness_root = adapter_root / "deepseek_harness"
    harness_root.mkdir(parents=True)
    (adapter_root / "__init__.py").write_text("", encoding="utf-8")
    (harness_root / "__init__.py").write_text("from . import freshness\n", encoding="utf-8")
    (harness_root / "freshness.py").write_text("\"\"\"packaged import probe\"\"\"\n", encoding="utf-8")

    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir()
    probe = (
        "import json\n"
        "import runpy\n"
        "import sys\n"
        f"namespace = runpy.run_path({str(packaged_script)!r}, run_name='qtrade_packaged_probe')\n"
        "module = namespace.get('freshness')\n"
        "print(json.dumps({\n"
        "    'root': str(namespace['ROOT']),\n"
        "    'path0': sys.path[0],\n"
        "    'freshness': module.__name__ if module is not None else None,\n"
        "    'freshness_file': str(module.__file__) if module is not None else None,\n"
        "    'third_party_on_path': any('third_party' in entry.lower() for entry in sys.path if entry),\n"
        "}))\n"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=outside_cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(payload["root"]).resolve() == packaged_root.resolve()
    assert Path(payload["path0"]).resolve() == packaged_root.resolve()
    assert payload["freshness"] == "qtrade_adapters.deepseek_harness.freshness"
    assert Path(payload["freshness_file"]).resolve() == (harness_root / "freshness.py").resolve()
    assert payload["third_party_on_path"] is False


def test_calendar_cache_is_used_when_api_fails(tmp_path, monkeypatch):
    deck = _configure_daily(monkeypatch, tmp_path)
    cache = tmp_path / "cache.json"
    daily_update.save_calendar_cache(cache, [dt.date(2026, 8, 25)])

    state, reason = daily_update.resolve_trading_day(
        dt.date(2026, 8, 25),
        cache_path=cache,
        calendar_loader=lambda: (_ for _ in ()).throw(OSError("offline")),
    )

    assert deck.exists()
    assert state is True
    assert reason == "calendar_cache"


def test_calendar_failure_without_cache_fails_closed(tmp_path, monkeypatch):
    _configure_daily(monkeypatch, tmp_path)
    status = tmp_path / "status.json"
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)

    result = daily_update.main(
        ["--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        calendar_loader=lambda: (_ for _ in ()).throw(OSError("offline")),
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 1
    assert processes.calls == []
    assert payload["state"] == "failure"
    assert payload["step"] == "calendar"
    assert payload["reason"].startswith("calendar_unavailable:")


def test_holiday_from_calendar_skips_without_running_pipeline(tmp_path, monkeypatch):
    _configure_daily(monkeypatch, tmp_path)
    status = tmp_path / "status.json"
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)

    result = daily_update.main(
        ["--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        calendar_loader=_calendar(dt.date(2026, 8, 26)),
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 0
    assert processes.calls == []
    assert payload["state"] == "skip"
    assert payload["reason"] == "calendar_api_closed"


def test_force_bypasses_calendar_and_runs_only_mocked_steps(tmp_path, monkeypatch):
    _configure_daily(monkeypatch, tmp_path)
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)
    status = tmp_path / "status.json"
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_artifacts",
        lambda deck: daily_update.freshness.ArtifactSnapshot({}),
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_portal_baseline",
        lambda deck: {"coverage": 1},
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "verify_portal",
        lambda *args, **kwargs: {"verified": True, "as_of": "2026-08-29", "reason": "verified"},
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "verify_factors",
        lambda *args, **kwargs: {"verified": True, "as_of": "2026-08-29", "reason": "verified"},
    )

    def fake_decision(deck, target, **kwargs):
        return {
            "verified": True,
            "as_of": "2026-08-29",
            "reason": "verified",
            "_pool_path": deck / "logs" / "opp_pool_20260829.json",
        }

    monkeypatch.setattr(daily_update.freshness, "verify_decision", fake_decision)
    monkeypatch.setattr(daily_update.freshness, "resolve_sync_destination", lambda deck: None)
    monkeypatch.setattr(
        daily_update.freshness,
        "verify_sync",
        lambda *args, **kwargs: {"verified": True, "as_of": "2026-08-29", "reason": "verified"},
    )

    result = daily_update.main(
        ["--force", "--status-file", str(status)],
        today=dt.date(2026, 8, 29),
    )

    assert result == 0
    assert len(processes.calls) == 5
    assert all(kwargs["cwd"] == str(daily_update.DECK) for _, kwargs in processes.calls)
    payload = json.loads(status.read_text(encoding="utf-8"))
    assert payload["state"] == "success"
    assert payload["reason"] == "completed"
    assert payload["outputs"] == {"portal": True, "decision": True, "factors": True, "sync": True}


def test_success_status_makes_same_day_run_idempotent(tmp_path, monkeypatch):
    _configure_daily(monkeypatch, tmp_path)
    status = tmp_path / "status.json"
    daily_update.write_status(
        status,
        trade_date="2026-08-25",
        state="success",
        reason="completed",
    )
    monkeypatch.setattr(daily_update.subprocess, "run", lambda *args, **kwargs: pytest.fail("must be idempotent"))

    assert daily_update.main(
        ["--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        calendar_loader=lambda: pytest.fail("calendar is not needed"),
    ) == 0


def test_lock_is_atomic_and_not_removed_when_busy(tmp_path, monkeypatch):
    _configure_daily(monkeypatch, tmp_path)
    lock = tmp_path / "update.lock"
    lock.write_text("other-process", encoding="utf-8")
    status = tmp_path / "status.json"

    result = daily_update.main(
        ["--force", "--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        lock_path=lock,
    )

    assert result == 1
    assert lock.read_text(encoding="utf-8") == "other-process"


def test_failure_stops_following_steps_and_records_exit_code(tmp_path, monkeypatch):
    deck = _configure_daily(monkeypatch, tmp_path)
    (deck / "logs" / "opp_pool_20260825.json").write_text("{}", encoding="utf-8")
    processes = FakeProcesses([0, 7, 0, 0, 0])
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_artifacts",
        lambda deck: daily_update.freshness.ArtifactSnapshot({}),
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "capture_portal_baseline",
        lambda deck: {"coverage": 1},
    )
    monkeypatch.setattr(
        daily_update.freshness,
        "verify_portal",
        lambda *args, **kwargs: {"verified": True, "as_of": "2026-08-25", "reason": "verified"},
    )
    status = tmp_path / "status.json"

    result = daily_update.main(
        ["--status-file", str(status)],
        today=dt.date(2026, 8, 25),
        calendar_loader=_calendar(dt.date(2026, 8, 25)),
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 1
    assert len(processes.calls) == 2
    assert payload["state"] == "failure"
    assert payload["step"] == "factors"
    assert payload["steps"][-1]["returncode"] == 7
    assert payload["outputs"]["portal"] is True
    assert payload["outputs"]["factors"] is False


@pytest.mark.parametrize(
    ("capture_name", "error_type"),
    [("capture_artifacts", OSError), ("capture_portal_baseline", RuntimeError)],
)
def test_freshness_capture_exception_writes_terminal_status_and_releases_lock(
    tmp_path, monkeypatch, capture_name, error_type
):
    _configure_daily(monkeypatch, tmp_path)
    target = dt.date(2026, 8, 28)
    status = tmp_path / "state" / "status.json"
    lock = tmp_path / "state" / "daily.lock"
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)

    def raise_capture(_deck):
        raise error_type("private capture path")

    monkeypatch.setattr(daily_update.freshness, capture_name, raise_capture)
    if capture_name == "capture_portal_baseline":
        monkeypatch.setattr(
            daily_update.freshness,
            "capture_artifacts",
            lambda _deck: daily_update.freshness.ArtifactSnapshot({}),
        )

    result = daily_update.main(
        ["--force", "--status-file", str(status)],
        today=target,
        lock_path=lock,
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 1
    assert processes.calls == []
    assert payload["state"] == "failure"
    assert payload["reason"] == "freshness_capture_failed"
    assert payload["finished_at"]
    assert not lock.exists()
    assert "private capture path" not in json.dumps(payload)


def test_explicit_incomplete_deck_is_weekend_skip_but_weekday_failure(tmp_path, monkeypatch):
    deck = tmp_path / "incomplete-deck"
    deck.mkdir()
    processes = FakeProcesses()
    monkeypatch.setattr(daily_update.subprocess, "run", processes.run)

    weekend_status = tmp_path / "weekend.json"
    weekend = daily_update.main(
        ["--deck-dir", str(deck), "--status-file", str(weekend_status)],
        today=dt.date(2026, 8, 29),
    )
    assert weekend == 0
    weekend_payload = json.loads(weekend_status.read_text(encoding="utf-8"))
    assert weekend_payload["state"] == "skip"
    assert weekend_payload["reason"] == "weekend"
    assert processes.calls == []

    weekday_status = tmp_path / "weekday.json"
    weekday = daily_update.main(
        ["--deck-dir", str(deck), "--status-file", str(weekday_status)],
        today=dt.date(2026, 8, 28),
        calendar_loader=_calendar(dt.date(2026, 8, 28)),
    )
    assert weekday == 1
    weekday_payload = json.loads(weekday_status.read_text(encoding="utf-8"))
    assert weekday_payload["state"] == "failure"
    assert weekday_payload["reason"] == "deck_missing"
    assert weekday_payload["finished_at"]
    assert processes.calls == []


def test_stale_recovery_never_reclaims_legacy_source_tree_lock(tmp_path, monkeypatch):
    package_root = tmp_path / "package"
    legacy_logs = package_root / "logs"
    legacy_logs.mkdir(parents=True)
    monkeypatch.setattr(daily_update, "ROOT", package_root)
    monkeypatch.setattr(daily_update, "_pid_is_alive", lambda _pid: False)
    status = tmp_path / "user-data" / "status.json"
    status.parent.mkdir()
    status.write_text(
        json.dumps(
            {
                "state": "running",
                "owner_pid": 93002,
                "heartbeat_at": "2026-08-28T10:00:00",
            }
        ),
        encoding="utf-8",
    )
    lock = legacy_logs / "daily_update_1830.lock"
    lock.write_text("pid=93002\n", encoding="ascii")

    result = daily_update.recover_stale_status(
        status,
        lock,
        now=dt.datetime(2026, 8, 28, 11, 0),
    )

    assert result["state"] == "running"
    assert lock.exists()


def test_scheduler_waits_until_cutoff_then_runs_once_after_failure():
    calls = []
    scheduler = runtime.DailyUpdateScheduler(lambda date: calls.append(date) or 1)
    before = dt.datetime(2026, 8, 25, 18, 29, 59)
    due = dt.datetime(2026, 8, 25, 18, 30)

    assert scheduler.run_pending(before) is None
    assert scheduler.seconds_until_next_check(before) == 1
    assert runtime.seconds_until_next_check(due) == 0
    assert scheduler.run_pending(due) == 1
    assert scheduler.run_pending(due + dt.timedelta(minutes=1)) is None
    assert calls == [dt.date(2026, 8, 25)]
    assert scheduler.seconds_until_next_check(due) == pytest.approx(24 * 3600)
    assert scheduler.run_pending(dt.datetime(2026, 8, 26, 18, 30)) == 1
    assert calls == [dt.date(2026, 8, 25), dt.date(2026, 8, 26)]


def test_scheduler_fake_clock_and_subprocess_trigger_once(tmp_path):
    processes = FakeProcesses()
    observed = []
    scheduler = runtime.DailyUpdateScheduler(
        lambda date: observed.append(
            runtime.run_daily_update(
                tmp_path / "deck",
                date,
                subprocess_module=processes,
                project_root=tmp_path,
            )
        )
    )
    before = dt.datetime(2026, 8, 25, 18, 29)
    due = dt.datetime(2026, 8, 25, 18, 30)

    assert scheduler.run_pending(before) is None
    assert scheduler.run_pending(due) == 0
    assert scheduler.run_pending(due + dt.timedelta(seconds=1)) is None
    assert observed == [0]
    assert len(processes.calls) == 1


def test_scheduler_command_has_explicit_deck_status_and_no_shell():
    command = runtime.build_daily_update_command(
        Path("/tmp/deck"),
        dt.date(2026, 8, 25),
        project_root=Path("/tmp/qtrade"),
        status_file=Path("/tmp/status.json"),
        python_executable="python-test",
    )
    assert command == [
        "python-test",
        "-X",
        "utf8",
        str(Path("/tmp/qtrade") / "scripts" / "daily_update_1830.py"),
        "--date",
        "2026-08-25",
        "--deck-dir",
        str(Path("/tmp/deck")),
        "--status-file",
        str(Path("/tmp/status.json")),
    ]


def test_run_daily_update_uses_argv_and_explicit_deck_environment(tmp_path):
    processes = FakeProcesses()
    base = tmp_path / "deck"
    root = tmp_path / "qtrade"
    status = tmp_path / "status.json"

    result = runtime.run_daily_update(
        base,
        dt.date(2026, 8, 25),
        environment={"QTRADE_NO_HARNESS": "1"},
        subprocess_module=processes,
        project_root=root,
        status_file=status,
        python_executable="python-test",
    )

    command, kwargs = processes.calls[0]
    assert result == 0
    assert command[0] == "python-test"
    assert command[command.index("--deck-dir") + 1] == str(base)
    assert kwargs["cwd"] == str(root)
    assert kwargs["env"]["QTRADE_DECK_DIR"] == str(base)
    assert kwargs["env"]["QTRADE_NO_HARNESS"] == "1"
    assert kwargs["timeout"] == runtime.DAILY_UPDATE_TIMEOUT_SECONDS
    assert kwargs.get("shell", False) is False


def test_interruptible_start_failure_persists_terminal_status(tmp_path):
    status = tmp_path / "state" / "status.json"
    result = runtime.run_daily_update(
        tmp_path / "deck",
        dt.date(2026, 8, 28),
        subprocess_module=FailingAutoProcesses(),
        project_root=tmp_path / "qtrade",
        status_file=status,
        interruptible=True,
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 1
    assert payload["state"] == "failure"
    assert payload["reason"] == "update_failed"
    assert payload["finished_at"]
    assert "private path" not in json.dumps(payload)


def test_maybe_auto_update_is_singleton_and_can_stop(tmp_path):
    runtime.stop_auto_update()
    processes = FakeProcesses()
    try:
        first = runtime.maybe_auto_update(
            base_dir_fn=lambda: tmp_path,
            env={},
            subprocess_module=processes,
            project_root=tmp_path,
            clock=lambda: dt.datetime(2026, 8, 28, 18, 29),
        )
        second = runtime.maybe_auto_update(
            base_dir_fn=lambda: tmp_path,
            env={},
            subprocess_module=processes,
            project_root=tmp_path,
            clock=lambda: dt.datetime(2026, 8, 28, 18, 29),
        )
        assert first is second
        assert first is not None
    finally:
        runtime.stop_auto_update()
    assert runtime._AUTO_UPDATE_THREAD is None


def test_auto_scheduler_uses_user_state_root_and_stops_owned_child(tmp_path):
    runtime.stop_auto_update()
    processes = BlockingAutoProcesses()
    state_root = tmp_path / "user-data" / "qtrade-state"
    before = dt.datetime(2026, 8, 28, 18, 29)
    due = dt.datetime(2026, 8, 28, 18, 30)
    scheduler = runtime.maybe_auto_update(
        base_dir_fn=lambda: tmp_path / "deck",
        env={"QTRADE_UPDATE_STATE_DIR": str(state_root)},
        subprocess_module=processes,
        project_root=tmp_path / "qtrade",
        python_executable="stable-python",
        clock=lambda: before,
    )
    assert scheduler is not None
    pending = threading.Thread(target=scheduler.run_pending, args=(due,))
    pending.start()
    assert processes.process.started.wait(timeout=1)
    command, kwargs = processes.calls[0]
    assert command[command.index("--status-file") + 1] == str(
        state_root / "daily_update_1830.status.json"
    )
    assert command[command.index("--log-file") + 1] == str(
        state_root / "daily_update_1830.log"
    )
    assert kwargs["env"]["QTRADE_UPDATE_STATE_DIR"] == str(state_root)
    assert kwargs["shell"] is False

    runtime.stop_auto_update(timeout=1)
    pending.join(timeout=1)
    assert not pending.is_alive()
    assert processes.process.terminated.is_set()
    status = state_root / "daily_update_1830.status.json"
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "aborted"
    assert not (state_root / "daily_update_1830.manual.lock").exists()
    assert runtime._AUTO_UPDATE_THREAD is None


def test_manual_and_auto_share_single_flight_lease(tmp_path):
    runtime.stop_auto_update()
    state_root = tmp_path / "user-data" / "qtrade-state"
    shared_lock = state_root / "daily_update_1830.manual.lock"
    target_time = dt.datetime(2026, 8, 28, 18, 31)
    entered = threading.Event()
    release = threading.Event()

    def blocking_run(*_args, **_kwargs):
        entered.set()
        release.wait(timeout=2)
        return 0

    manual = runtime.ManualUpdateController(
        base_dir_fn=lambda: tmp_path / "deck",
        project_root=tmp_path / "qtrade",
        status_file=state_root / "daily_update_1830.status.json",
        lock_path=shared_lock,
        pipeline_lock_path=state_root / "daily_update_1830.lock",
        clock=lambda: target_time,
        run_fn=blocking_run,
    )
    scheduler = runtime.maybe_auto_update(
        base_dir_fn=lambda: tmp_path / "deck",
        env={"QTRADE_UPDATE_STATE_DIR": str(state_root)},
        subprocess_module=BlockingAutoProcesses(),
        project_root=tmp_path / "qtrade",
        clock=lambda: dt.datetime(2026, 8, 28, 18, 29),
    )
    assert scheduler is not None
    try:
        assert manual.start(now=target_time)["state"] == "accepted"
        assert entered.wait(timeout=1)
        assert scheduler.run_pending(target_time) == 1
        assert scheduler.last_result == 1
    finally:
        release.set()
        worker = manual._worker
        if worker is not None:
            worker.join(timeout=1)
        manual.stop(timeout=1)
        runtime.stop_auto_update(timeout=1)
    assert not shared_lock.exists()


def test_no_auto_update_disables_scheduler(tmp_path):
    runtime.stop_auto_update()
    assert runtime.maybe_auto_update(base_dir_fn=lambda: tmp_path, env={"QTRADE_NO_AUTOUPDATE": "1"}) is None
