"""Offline contracts for the resumable portal provider/executor layer."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import time

import pytest

from qtrade_adapters.deepseek_harness import portal_refresh
from qtrade_adapters.deepseek_harness.portal_refresh import (
    MAX_MANIFEST_BYTES,
    MAX_METADATA_BYTES,
    publish_snapshot,
    read_current_snapshot,
)
from qtrade_adapters.deepseek_harness.portal_refresh_worker import (
    PortalRefreshPlan,
    PortalRefreshWorker,
    PortalWorkerError,
    MAX_CHILD_MESSAGE_BYTES,
    _Lease,
    _child_fetch,
    _recv_json,
    _new_checkpoint,
    _normalise_symbols,
    _plan_universe_token,
    _reclaim_stale_lease,
    _validated_plan,
    _validate_checkpoint,
    _validate_worker_layout,
)


TARGET = "2026-08-28"
SYMBOLS = ("600000", "600001", "000001", "000002", "002001")


def _item(symbol: str) -> dict:
    price = float(int(symbol[-2:]) + 10)
    return {
        "rows": [{
            "code": symbol,
            "date": TARGET,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price + 0.5,
            "volume": 1000,
            "adjust": "qfq",
        }],
        "metadata": {
            "code": symbol,
            "name": f"Fixture {symbol}",
            "exchange": "SH" if symbol.startswith("6") else "SZ",
            "risk_warning": None,
            "suspended": False,
            "listed": True,
            "tradable": True,
            "history_rows": 130,
            "latest_trade_date": TARGET,
            "computable": True,
            "eligible_reason": None,
        },
    }


class FakeProvider:
    PROVIDER_VERSION = "fixture-v1"

    def __init__(self, symbols=SYMBOLS, token="a" * 64, block_symbol=None):
        self.symbols = tuple(symbols)
        self.token = token
        self.block_symbol = block_symbol

    def fetch(self, symbol: str, target_date: str) -> dict:
        if symbol == self.block_symbol:
            time.sleep(60)
        return _item(symbol)


class FakeHistoryProvider(FakeProvider):
    PROVIDER_VERSION = "fixture-v2"

    def fetch_history(self, symbol: str, target_date: str, history_window: int) -> dict:
        target = dt.date.fromisoformat(target_date)
        rows = []
        for offset in range(history_window):
            value = float(offset + 10)
            rows.append({
                "code": symbol,
                "date": (target - dt.timedelta(days=history_window - 1 - offset)).isoformat(),
                "open": value,
                "high": value + 1,
                "low": value - 1,
                "close": value + 0.5,
                "volume": 1000 + offset,
                "adjust": "qfq",
            })
        item = _item(symbol)
        item["rows"] = rows
        item["metadata"]["history_rows"] = history_window
        return item


class BadPublishProvider(FakeProvider):
    def fetch(self, symbol: str, target_date: str) -> dict:
        item = _item(symbol)
        item["rows"][0]["close"] = -1
        return item


class NearLimitProvider(FakeProvider):
    def fetch(self, symbol: str, target_date: str) -> dict:
        item = _item(symbol)
        item["metadata"]["name"] = "N" * 128
        item["metadata"]["risk_warning"] = "R" * 64
        item["metadata"]["eligible_reason"] = "E" * 64
        return item


def _worker(tmp_path: Path, provider=None, **kwargs) -> PortalRefreshWorker:
    user_data = tmp_path / "user-data"
    user_data.mkdir(parents=True, exist_ok=True)
    return PortalRefreshWorker(
        user_data_dir=user_data,
        provider=provider or FakeProvider(),
        batch_size=2,
        item_timeout_seconds=kwargs.pop("item_timeout_seconds", 2),
        batch_timeout_seconds=kwargs.pop("batch_timeout_seconds", 10),
        job_timeout_seconds=kwargs.pop("job_timeout_seconds", 30),
        max_attempts=kwargs.pop("max_attempts", 1),
        retry_delay_seconds=0,
        **kwargs,
    )


def _plan(provider: FakeProvider | None = None, *, symbols=SYMBOLS) -> PortalRefreshPlan:
    chosen = provider or FakeProvider(symbols=symbols)
    token = _plan_universe_token(tuple(symbols), TARGET, "c" * 64, chosen.PROVIDER_VERSION)
    return PortalRefreshPlan(
        tuple(symbols), TARGET, token, True, "c" * 64, chosen.PROVIDER_VERSION,
    )


def _spawn_valid_frame(connection) -> None:
    _child_fetch(NearLimitProvider(), SYMBOLS[0], TARGET, connection)


def test_worker_publishes_complete_generation_and_safe_progress(tmp_path: Path) -> None:
    worker = _worker(tmp_path)

    result = worker.run(_plan())

    assert result["state"] == "success"
    assert result["total"] == len(SYMBOLS)
    assert result["completed"] == len(SYMBOLS)
    assert result["failed"] == 0
    assert result["as_of"] == TARGET
    assert result["reason"] == "completed"
    assert "symbols" not in result
    snapshot = read_current_snapshot(
        tmp_path / "user-data" / "state",
        user_data_dir=tmp_path / "user-data",
    )
    assert snapshot is not None
    assert snapshot.manifest["total"] == len(SYMBOLS)
    assert snapshot.manifest["target_date"] == TARGET
    assert worker.status()["state"] == "success"


def test_history_worker_publishes_v2_target_anchored_snapshot(tmp_path: Path) -> None:
    provider = FakeHistoryProvider()
    worker = _worker(
        tmp_path,
        provider=provider,
        history_window=portal_refresh.HISTORY_WINDOW,
        item_timeout_seconds=10,
        batch_timeout_seconds=30,
        job_timeout_seconds=90,
    )

    result = worker.run(_plan(provider))

    assert result["state"] == "success"
    snapshot = read_current_snapshot(
        tmp_path / "user-data" / "state",
        user_data_dir=tmp_path / "user-data",
    )
    assert snapshot is not None
    assert snapshot.manifest["schema_version"] == portal_refresh.HISTORY_SCHEMA_VERSION
    assert snapshot.manifest["history_window"] == portal_refresh.HISTORY_WINDOW
    assert snapshot.manifest["history_rows"] == [portal_refresh.HISTORY_WINDOW] * len(SYMBOLS)
    checkpoint = json.loads(worker._paths().checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["history_window"] == portal_refresh.HISTORY_WINDOW
    assert checkpoint["history_schema"] == portal_refresh.HISTORY_DB_SCHEMA
    rows = portal_refresh._read_database_rows_history(
        snapshot.database, TARGET, list(SYMBOLS),
    )
    assert rows is not None
    assert all(len(value) == portal_refresh.HISTORY_WINDOW for value in rows.values())


def test_maximum_universe_and_manifest_bounds_are_deterministic(tmp_path: Path) -> None:
    symbols = tuple(f"{600000 + offset:06d}" for offset in range(portal_refresh.MAX_SYMBOLS))
    universe = _plan(FakeProvider(symbols=symbols, token="a" * 64), symbols=symbols)
    checkpoint = _new_checkpoint(
        "b" * 32,
        universe,
        50,
        dt.datetime.now().isoformat(timespec="seconds"),
    )
    _validate_checkpoint(checkpoint)

    rows = {
        symbol: _item(symbol)["rows"]
        for symbol in symbols
    }
    metadata = [_item(symbol)["metadata"] for symbol in symbols]
    snapshot = publish_snapshot(
        symbols,
        TARGET,
        rows,
        metadata,
        state_dir=tmp_path / "user-data" / "state",
        user_data_dir=tmp_path / "user-data",
        universe_token=_plan_universe_token(symbols, TARGET, "c" * 64, FakeProvider.PROVIDER_VERSION),
    )

    assert snapshot.manifest["total"] == portal_refresh.MAX_SYMBOLS
    assert len(json.dumps(snapshot.manifest, ensure_ascii=False).encode("utf-8")) <= MAX_MANIFEST_BYTES
    assert snapshot.manifest["metadata_size"] <= MAX_METADATA_BYTES
    with pytest.raises(PortalWorkerError, match="universe_unavailable"):
        _normalise_symbols((*symbols, "605000"))


def test_worker_timeout_leaves_partial_checkpoint_and_next_run_resumes(tmp_path: Path) -> None:
    first = _worker(
        tmp_path,
        provider=FakeProvider(block_symbol=SYMBOLS[1]),
        item_timeout_seconds=5,
    )

    failed = first.run(_plan(first.provider))

    assert failed["state"] == "timed_out"
    assert failed["completed"] == 1
    assert failed["finished_at"]
    checkpoint = tmp_path / "user-data" / "state" / "portal_refresh_worker" / "checkpoint.json"
    checkpoint_before = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert checkpoint_before["state"] == "timed_out"

    resumed = _worker(tmp_path, provider=FakeProvider(), item_timeout_seconds=2)
    result = resumed.run(_plan(resumed.provider))

    assert result["state"] == "success"
    assert result["completed"] == len(SYMBOLS)
    assert not (tmp_path / "user-data" / "state" / "portal_refresh_worker" / "portal_refresh_worker.lock").exists()


def test_worker_corrupt_checkpoint_and_unknown_residue_fail_closed(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    paths = worker._paths()
    paths.root.mkdir(parents=True)
    paths.batches.mkdir()
    paths.checkpoint.write_text(json.dumps({"state": "running"}), encoding="utf-8")

    result = worker.status()

    assert result["state"] == "failure"
    assert result["reason"] == "checkpoint_corrupt"
    assert paths.checkpoint.read_text(encoding="utf-8") == json.dumps({"state": "running"})

    paths.checkpoint.unlink()
    (paths.root / "unexpected.tmp").write_text("reject", encoding="utf-8")
    assert worker.status()["reason"] == "checkpoint_corrupt"


def test_worker_lease_stale_recovery_is_pid_and_inode_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker(tmp_path)
    paths = worker._paths()
    paths.root.mkdir(parents=True)
    paths.batches.mkdir()
    lease = _Lease(paths.lease, "b" * 32)
    assert lease.acquire()
    lease.release()

    paths.lease.write_text(json.dumps({
        "pid": 999999,
        "job_id": "b" * 32,
        "heartbeat_at": (dt.datetime.now() - dt.timedelta(hours=1)).isoformat(timespec="seconds"),
    }), encoding="utf-8")
    monkeypatch.setattr(
        "qtrade_adapters.deepseek_harness.portal_refresh_worker._pid_is_alive",
        lambda pid: False,
    )
    assert _reclaim_stale_lease(paths.lease, now=dt.datetime.now()) is True
    assert not paths.lease.exists()


def test_two_workers_share_a_single_lease(tmp_path: Path) -> None:
    first = _worker(tmp_path)
    paths = first._paths()
    _validate_worker_layout(paths, create=True)
    lease = _Lease(paths.lease, "b" * 32)
    assert lease.acquire()
    try:
        assert _Lease(paths.lease, "c" * 32).acquire() is False
    finally:
        lease.release()


def test_worker_requires_explicit_trusted_plan(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    result = worker.run(TARGET)
    assert result["state"] == "failure"
    assert result["reason"] == "universe_unavailable"
    assert not worker._paths().lease.exists()


def test_plan_token_is_canonical_not_caller_defined() -> None:
    valid = _plan()
    invalid = PortalRefreshPlan(
        valid.symbols,
        valid.target_date,
        "a" * 64,
        valid.calendar_verified,
        valid.calendar_token,
        valid.provider_version,
    )
    with pytest.raises(PortalWorkerError, match="universe_unavailable"):
        _validated_plan(invalid)


def test_worker_rejects_provider_version_drift(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    plan = PortalRefreshPlan(
        SYMBOLS,
        TARGET,
        _plan_universe_token(SYMBOLS, TARGET, "c" * 64, "different-v1"),
        True,
        "c" * 64,
        "different-v1",
    )
    result = worker.run(plan)
    assert result["state"] == "failure"
    assert result["reason"] == "provider_schema"


def test_worker_process_start_exception_is_terminal_on_disk(tmp_path: Path) -> None:
    class StartFailure:
        pid = None

        def start(self):
            raise RuntimeError("fixture start failure")

        def is_alive(self):
            return False

        def join(self, timeout):
            return None

    worker = _worker(tmp_path, process_factory=lambda *args: StartFailure())
    result = worker.run(_plan(worker.provider))
    assert result["state"] == "failure"
    assert result["reason"] == "provider_failed"
    checkpoint = json.loads(worker._paths().checkpoint.read_text(encoding="utf-8"))
    assert checkpoint["state"] == "failure"
    assert checkpoint["finished_at"]


def test_success_requires_current_snapshot_identity(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    assert worker.run(_plan())[
        "state"
    ] == "success"
    current = worker._paths().state / "portal_refresh" / "current.json"
    current.write_text("{}", encoding="utf-8")
    result = worker.status()
    assert result["state"] == "failure"
    assert result["reason"] == "checkpoint_corrupt"


def test_batch_size_tamper_is_rejected_without_fetch(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    assert worker.run(_plan())[
        "state"
    ] == "success"
    checkpoint_path = worker._paths().checkpoint
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["batches"][0]["size"] += 1
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    result = worker.status()
    assert result["state"] == "failure"
    assert result["reason"] == "checkpoint_corrupt"


def test_child_pipe_rejects_oversized_or_non_json_frames() -> None:
    class FakeConnection:
        def __init__(self, payload):
            self.payload = payload

        def recv_bytes(self, maxlength):
            assert maxlength == MAX_CHILD_MESSAGE_BYTES
            return self.payload

    with pytest.raises(PortalWorkerError, match="provider_failed"):
        _recv_json(FakeConnection(b"x" * (MAX_CHILD_MESSAGE_BYTES + 1)))
    with pytest.raises(PortalWorkerError, match="provider_failed"):
        _recv_json(FakeConnection(b"not-json"))


def test_spawn_child_writes_bounded_frame_before_parent_reads() -> None:
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(False)
    process = context.Process(target=_spawn_valid_frame, args=(child,), daemon=False)
    try:
        process.start()
        child.close()
        process.join(5)
        assert not process.is_alive()
        assert process.exitcode == 0
        assert parent.poll(0)
        payload = _recv_json(parent)
        assert isinstance(payload, dict)
        assert payload["ok"] is True
        frame_size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        assert MAX_CHILD_MESSAGE_BYTES // 4 <= frame_size < MAX_CHILD_MESSAGE_BYTES
    finally:
        try:
            child.close()
        except OSError:
            pass
        try:
            parent.close()
        except OSError:
            pass
        if process.is_alive():
            process.terminate()
            process.join(1)


def test_stale_running_checkpoint_becomes_disk_terminal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = _worker(tmp_path)
    paths = worker._paths()
    _validate_worker_layout(paths, create=True)
    universe = _plan()
    old = dt.datetime.now() - dt.timedelta(hours=1)
    checkpoint = _new_checkpoint(
        "d" * 32,
        universe,
        2,
        old.isoformat(timespec="seconds"),
    )
    checkpoint["heartbeat_at"] = old.isoformat(timespec="seconds")
    worker._write_checkpoint(paths, checkpoint)
    paths.lease.write_text(json.dumps({
        "pid": 999999,
        "job_id": "d" * 32,
        "heartbeat_at": old.isoformat(timespec="seconds"),
    }), encoding="utf-8")
    monkeypatch.setattr(
        "qtrade_adapters.deepseek_harness.portal_refresh_worker._pid_is_alive",
        lambda pid: False,
    )

    result = worker.status()

    assert result["state"] == "aborted"
    assert result["reason"] == "stale_running"
    assert json.loads(paths.checkpoint.read_text(encoding="utf-8"))["state"] == "aborted"
    assert json.loads(paths.checkpoint.read_text(encoding="utf-8"))["finished_at"]


def test_publish_child_failure_and_terminal_write_failure_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_publish = _worker(tmp_path, provider=BadPublishProvider())
    result = failed_publish.run(_plan(failed_publish.provider))
    checkpoint = failed_publish._paths().checkpoint
    assert result["state"] == "failure"
    assert result["reason"] == "publish_failed"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["state"] == "failure"

    second_root = tmp_path / "second"
    worker = _worker(second_root)
    original_write = worker._write_checkpoint

    def fail_success(paths, value):
        if value.get("state") == "success":
            raise OSError("fixture terminal write failure")
        return original_write(paths, value)

    monkeypatch.setattr(worker, "_write_checkpoint", fail_success)
    result = worker.run(_plan(worker.provider))
    paths = worker._paths()
    assert result["state"] == "failure"
    assert result["reason"] == "checkpoint_io"
    assert json.loads(paths.checkpoint.read_text(encoding="utf-8"))["state"] == "publishing"
    assert read_current_snapshot(
        second_root / "user-data" / "state",
        user_data_dir=second_root / "user-data",
    ) is not None
    assert worker.status()["state"] == "failure"


def test_worker_layout_uses_only_trusted_user_data_state(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly user-data/state"):
        PortalRefreshWorker(
            user_data_dir=tmp_path / "user-data",
            state_dir=tmp_path / "other-state",
            provider=FakeProvider(),
        )._paths()
