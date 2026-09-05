"""Offline contracts for the PR27B3 generation-bound research pipeline."""

from __future__ import annotations

import json
import multiprocessing
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from qtrade_adapters.deepseek_harness import portal_refresh, snapshot_pipeline
from qtrade_adapters.deepseek_harness.portal_refresh_provider import (
    PROVIDER_VERSION,
    PortalPlanError,
    build_bound_plan,
)
from qtrade_adapters.deepseek_harness.portal_refresh_worker import PortalRefreshPlan


TARGET = "2026-08-28"
SYMBOLS = ["600519", "000001", "000002", "000003", "000004"]
_ACTIVATE_EVENTS: list[str] = []


def _accept_activation(_payload):
    _ACTIVATE_EVENTS.append("activate")
    return True


def _reject_activation(payload):
    _ACTIVATE_EVENTS.append("restore" if payload is None else "activate")
    return payload is None


def _track_activation(payload):
    _ACTIVATE_EVENTS.append("restore" if payload is None else "activate")
    return True


def _reject_prepare(_pipeline):
    return False


def _blocking_factor_records(_portal):
    while True:
        pass


def _blocking_plan(**_kwargs):
    while True:
        pass


def _blocking_callback(_value):
    while True:
        pass


def _fixture_plan_builder(**_kwargs):
    return PortalRefreshPlan(
        tuple(SYMBOLS), TARGET, "portal-universe-v1", True, "calendar-token", PROVIDER_VERSION,
    ), object()


def _fixture_plan_inputs_builder(*, target_date, **_kwargs):
    return {
        "symbols": tuple(SYMBOLS),
        "metadata": {
            symbol: {
                "code": symbol,
                "name": symbol,
                "exchange": "SH" if symbol.startswith("6") else "SZ",
                "risk_warning": None,
                "suspended": False,
                "listed": True,
                "tradable": True,
                "history_rows": 320,
                "latest_trade_date": target_date,
                "computable": True,
                "eligible_reason": None,
            }
            for symbol in SYMBOLS
        },
    }


def _fixture(tmp_path: Path, *, legacy: bool = False, universe_token: str = "portal-universe-v1"):
    user_data = tmp_path / "user-data"
    state = user_data / "state"
    dates = [TARGET] if legacy else [
        (date.fromisoformat(TARGET) - timedelta(days=319 - index)).isoformat()
        for index in range(320)
    ]
    rows = {
        symbol: [{
            "code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
            "date": trade_date,
            "open": 10.0 + index + offset / 100,
            "high": 11.0 + index + offset / 100,
            "low": 9.0 + index + offset / 100,
            "close": 10.5 + index + offset / 100,
            "volume": 1000 + index,
            "adjust": "qfq",
        } for offset, trade_date in enumerate(dates)]
        for index, symbol in enumerate(SYMBOLS)
    }
    metadata = [{
        "code": symbol,
        "name": symbol,
        "exchange": "SH" if symbol.startswith("6") else "SZ",
        "risk_warning": None,
        "suspended": False,
        "listed": True,
        "tradable": True,
        "history_rows": 1 if legacy else 320,
        "latest_trade_date": TARGET,
        "computable": True,
        "eligible_reason": None,
    } for symbol in SYMBOLS]
    publish = portal_refresh.publish_snapshot if legacy else portal_refresh.publish_snapshot_v2
    portal = publish(SYMBOLS, TARGET, rows, metadata, state_dir=state, user_data_dir=user_data, universe_token=universe_token)
    return user_data, state, portal


def _factors(scores=None):
    scores = scores or [1.25, 1.0, 0.0, -1.0, -1.25]
    return {
        "total": len(SYMBOLS),
        "computable": len(SYMBOLS),
        "valid_count": len(SYMBOLS),
        "records": [
            {"symbol": symbol, "values": {key: None for key in snapshot_pipeline.FACTOR_KEYS}, "score": score, "as_of": TARGET}
            for symbol, score in zip(sorted(SYMBOLS), scores)
        ],
    }


def test_pipeline_publish_is_generation_bound_and_idempotent(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    factors = _factors()
    decisions = snapshot_pipeline.build_decision_records(factors, target_date=TARGET)
    assert [item["action"] for item in decisions["records"]] == ["buy", "buy", "hold", "sell", "sell"]
    first = snapshot_pipeline.publish_pipeline(
        portal, factors, decisions, state_dir=state, user_data_dir=user_data,
    )
    pointer = json.loads((state / "snapshot_pipeline" / "current.json").read_text(encoding="utf-8"))
    second = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data)
    assert second is not None
    assert first.manifest["generation"] == second.manifest["generation"]
    assert pointer["portal_generation"] == portal.manifest["generation"]
    assert second.manifest["candidate"] == 2


@pytest.mark.parametrize("field", ["manifest_sha256", "target_date", "candidate"])
def test_pipeline_pointer_tampering_fails_closed(tmp_path: Path, field: str) -> None:
    user_data, state, portal = _fixture(tmp_path)
    pipeline = snapshot_pipeline.publish_pipeline(
        portal, _factors(), snapshot_pipeline.build_decision_records(_factors(), target_date=TARGET),
        state_dir=state, user_data_dir=user_data,
    )
    path = state / "snapshot_pipeline" / "current.json"
    pointer = json.loads(path.read_text(encoding="utf-8"))
    pointer[field] = "0" * 64 if field == "manifest_sha256" else "2026-08-27" if field == "target_date" else 999
    path.write_text(json.dumps(pointer), encoding="utf-8")
    assert snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data) is None
    assert pipeline.portal.database.exists()


def test_pipeline_reader_rejects_unknown_residue_and_keeps_old_pointer(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    decisions = snapshot_pipeline.build_decision_records(_factors(), target_date=TARGET)
    snapshot_pipeline.publish_pipeline(portal, _factors(), decisions, state_dir=state, user_data_dir=user_data)
    root = state / "snapshot_pipeline"
    (root / "unexpected.tmp").write_text("reject", encoding="utf-8")
    assert snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data) is None
    assert (root / "current.json").exists()


def test_decision_builder_is_advisory_only_and_deterministic() -> None:
    result = snapshot_pipeline.build_decision_records(_factors(), target_date=TARGET)
    assert result["candidate"] == 2
    assert all(set(item) == {"symbol", "action", "score", "reason", "as_of"} for item in result["records"])
    assert not any(key in json.dumps(result) for key in ("qty", "order", "position", "account"))


def test_default_factor_builder_fails_closed_without_history(tmp_path: Path) -> None:
    _, _, portal = _fixture(tmp_path, legacy=True)
    with pytest.raises(snapshot_pipeline.SnapshotPipelineError, match="portal_schema_unsupported"):
        snapshot_pipeline.build_factor_records(portal)


def test_full_pipeline_rejects_legacy_v1_portal_generation(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path, legacy=True)
    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "success", "published_generation": portal.manifest["generation"]}

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
    )

    assert result == 1
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failure"
    assert status["reason"] == "portal_schema_unsupported"


def test_manifest_artifact_path_must_bind_to_generation(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    snapshot_pipeline.publish_pipeline(
        portal, _factors(), snapshot_pipeline.build_decision_records(_factors(), target_date=TARGET),
        state_dir=state, user_data_dir=user_data,
    )
    current = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data)
    assert current is not None
    manifest_path = state / "snapshot_pipeline" / "generations" / current.manifest["generation"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["factors_path"] = "generations/other/factors.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data) is None


def test_full_runner_orders_portal_factors_decision_sync_without_external_pipeline(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    _ACTIVATE_EVENTS.clear()
    events: list[str] = []

    class FakeWorker:
        def __init__(self, **kwargs):
            events.append("portal")

        def run(self, plan, **kwargs):
            return {
                "state": "success",
                "published_generation": portal.manifest["generation"],
                "published_content_sha256": portal.manifest["content_sha256"],
            }

    status_path = state / "daily_update_1830.status.json"
    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=status_path,
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
        commit_fn=_accept_activation,
    )
    assert result == 0
    assert events == ["portal"]
    assert _ACTIVATE_EVENTS == ["activate"]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "success"
    assert status["outputs"] == {"portal": True, "factors": True, "decision": True, "sync": True}
    current = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data)
    assert current is not None
    assert current.manifest["target_date"] == TARGET
    assert all(set(item["values"]) == set(snapshot_pipeline.FACTOR_KEYS) for item in current.factors["records"])
    assert all(item["as_of"] == TARGET for item in current.factors["records"])
    assert all(item["action"] in {"buy", "sell", "hold"} for item in current.decision["records"])


def test_pipeline_activation_failure_restores_pointer_and_reader_state(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    _ACTIVATE_EVENTS.clear()

    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "success", "published_generation": portal.manifest["generation"]}

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
        commit_fn=_reject_activation,
    )
    assert result == 1
    assert _ACTIVATE_EVENTS == ["activate", "restore"]
    assert snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data) is None
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failure"
    assert status["reason"] == "reload_failed"


def test_pipeline_requires_explicit_user_data_root(tmp_path: Path) -> None:
    with pytest.raises(snapshot_pipeline.SnapshotPipelineError, match="user_data_unavailable"):
        snapshot_pipeline.pipeline_paths(tmp_path / "state", user_data_dir=None)


def test_pipeline_owned_factor_boundary_times_out_without_child_residue(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "success", "published_generation": portal.manifest["generation"]}

    started = time.monotonic()
    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        deadline_seconds=0.25,
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
        factor_builder=_blocking_factor_records,
    )
    assert result == 1
    assert time.monotonic() - started < 5
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "timed_out"
    assert status["reason"] == "job_timeout"
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("phase", ["plan", "plan_inputs", "prepare", "activate"])
def test_pipeline_plan_and_hooks_have_owned_deadlines(tmp_path: Path, phase: str) -> None:
    user_data, state, portal = _fixture(tmp_path)

    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {
                "state": "success",
                "published_generation": portal.manifest["generation"],
                "published_content_sha256": portal.manifest["content_sha256"],
            }

    options = {
        "plan_builder": _blocking_plan if phase == "plan" else _fixture_plan_builder,
        "worker_factory": FakeWorker,
    }
    if phase == "plan_inputs":
        options["plan_inputs_builder"] = _blocking_plan
        options["calendar_dates"] = [TARGET]
    elif phase == "prepare":
        options["prepare_fn"] = _blocking_callback
    elif phase == "activate":
        options["activate_fn"] = _blocking_callback
    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        deadline_seconds=0.25,
        **options,
    )
    assert result == 1
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "timed_out"
    assert status["reason"] == "job_timeout"
    assert status["finished_at"]
    if phase == "activate":
        assert not (state / "snapshot_pipeline" / "current.json").exists()
    assert not multiprocessing.active_children()


@pytest.mark.parametrize("current_state", ["missing", "invalid"])
def test_bound_plan_inputs_fail_closed_without_current_overlay(
    tmp_path: Path,
    current_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_data = tmp_path / "user-data"
    state = user_data / "state"
    state.mkdir(parents=True)
    if current_state == "invalid":
        root = state / "portal_refresh"
        root.mkdir(parents=True)
        (root / "current.json").write_text("{}", encoding="utf-8")
    external_calls: list[str] = []

    def forbidden_external_adapter(*_args, **_kwargs):
        external_calls.append("adapter")
        raise AssertionError("bound plan input must not use external adapter")

    monkeypatch.setattr(snapshot_pipeline, "MainboardMarketDataAdapter", forbidden_external_adapter)
    with pytest.raises(PortalPlanError, match="universe_unavailable"):
        snapshot_pipeline.load_current_bound_plan_inputs(
            base_dir=tmp_path / "external",
            state_dir=state,
            user_data_dir=user_data,
            target_date=TARGET,
            calendar_dates=[TARGET],
        )
    assert external_calls == []


def test_bound_plan_inputs_use_only_verified_current_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_data, state, _portal = _fixture(tmp_path)
    external_calls: list[str] = []

    def forbidden_external_adapter(*_args, **_kwargs):
        external_calls.append("adapter")
        raise AssertionError("bound plan input must not use external adapter")

    monkeypatch.setattr(snapshot_pipeline, "MainboardMarketDataAdapter", forbidden_external_adapter)
    result = snapshot_pipeline.load_current_bound_plan_inputs(
        base_dir=tmp_path / "external",
        state_dir=state,
        user_data_dir=user_data,
        target_date=TARGET,
        calendar_dates=[TARGET],
    )
    assert tuple(result["symbols"]) == tuple(SYMBOLS)
    assert set(result["metadata"]) == set(SYMBOLS)
    assert all(item["latest_trade_date"] == TARGET for item in result["metadata"].values())
    assert external_calls == []


def test_bound_pipeline_without_current_overlay_is_universe_unavailable(tmp_path: Path) -> None:
    user_data = tmp_path / "user-data"
    state = user_data / "state"
    state.mkdir(parents=True)
    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        calendar_dates=[TARGET],
        plan_inputs_builder=snapshot_pipeline.load_current_bound_plan_inputs,
        deadline_seconds=2.0,
    )
    assert result == 1
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failure"
    assert status["reason"] == "universe_unavailable"


def test_pipeline_preserves_worker_timeout_terminal_reason(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    class TimedOutWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "timed_out", "reason": "batch_timeout"}

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        plan_builder=_fixture_plan_builder,
        worker_factory=TimedOutWorker,
    )
    assert result == 1
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "timed_out"
    assert status["reason"] == "batch_timeout"


@pytest.mark.parametrize(
    ("calendar_dates", "expected_state", "expected_reason"),
    [
        ([], "failure", "calendar_unavailable"),
        (["2026-08-27"], "skip", "calendar_closed"),
        ([TARGET], "success", "completed"),
    ],
)
def test_bound_plan_calendar_gate_precedes_service_inputs(
    tmp_path: Path,
    calendar_dates: list[str],
    expected_state: str,
    expected_reason: str,
) -> None:
    token = "portal-universe-v1"
    if expected_state == "success":
        token = build_bound_plan(
            symbols=SYMBOLS,
            metadata=_fixture_plan_inputs_builder(target_date=TARGET)["metadata"],
            target_date=TARGET,
            calendar_dates=calendar_dates,
        )[0].universe_token
    user_data, state, portal = _fixture(tmp_path, universe_token=token)

    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {
                "state": "success",
                "published_generation": portal.manifest["generation"],
                "published_content_sha256": portal.manifest["content_sha256"],
            }

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        calendar_dates=calendar_dates,
        plan_inputs_builder=_fixture_plan_inputs_builder,
        worker_factory=FakeWorker,
        commit_fn=_accept_activation,
    )
    assert (result == 0) is (expected_state in {"skip", "success"})
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == expected_state
    assert status["reason"] == expected_reason


def test_bound_plan_weekend_skips_before_calendar_or_service(tmp_path: Path) -> None:
    user_data, state, _portal = _fixture(tmp_path)
    calls: list[str] = []

    def fail_loader():
        calls.append("calendar")
        raise AssertionError("weekend must not load calendar")

    def fail_inputs(**kwargs):
        calls.append("inputs")
        raise AssertionError("weekend must not read service universe")

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        "2026-08-29",
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        calendar_loader=fail_loader,
        plan_inputs_builder=fail_inputs,
    )
    assert result == 0
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "skip"
    assert status["reason"] == "weekend"
    assert calls == []


def test_pipeline_owns_shared_lease_and_rejects_competitor(tmp_path: Path) -> None:
    user_data, state, _portal = _fixture(tmp_path)
    lease = snapshot_pipeline._PipelineLease(state / "daily_update_1830.manual.lock")
    assert lease.acquire() is True
    try:
        result = snapshot_pipeline.run_snapshot_pipeline(
            tmp_path / "external",
            TARGET,
            user_data_dir=user_data,
            state_dir=state,
            status_file=state / "daily_update_1830.status.json",
            plan_builder=lambda **kwargs: pytest.fail("lease competitor must not build a plan"),
        )
    finally:
        lease.release()
    assert result == 1
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["state"] == "failure"
    assert status["reason"] == "lease_busy"


def test_pipeline_final_status_failure_restores_pointer_and_lkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_data, state, portal = _fixture(tmp_path)
    old = snapshot_pipeline.publish_pipeline(
        portal, _factors(), snapshot_pipeline.build_decision_records(_factors(), target_date=TARGET),
        state_dir=state, user_data_dir=user_data,
    )
    pointer_path = state / "snapshot_pipeline" / "current.json"
    old_pointer = pointer_path.read_bytes()
    _ACTIVATE_EVENTS.clear()
    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "success", "published_generation": portal.manifest["generation"]}

    original_write_status = snapshot_pipeline._write_status

    def fail_final_status(path, payload):
        if payload.get("state") == "success":
            raise OSError("injected final status failure")
        return original_write_status(path, payload)

    monkeypatch.setattr(snapshot_pipeline, "_write_status", fail_final_status)
    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
        commit_fn=_track_activation,
    )
    assert result == 1
    assert pointer_path.read_bytes() == old_pointer
    restored = snapshot_pipeline.read_current_pipeline(state, user_data_dir=user_data)
    assert restored is not None
    assert restored.manifest["generation"] == old.manifest["generation"]
    assert _ACTIVATE_EVENTS == ["activate", "activate"]


def test_pipeline_prepare_failure_does_not_publish_pointer(tmp_path: Path) -> None:
    user_data, state, portal = _fixture(tmp_path)
    class FakeWorker:
        def __init__(self, **kwargs):
            pass

        def run(self, plan, **kwargs):
            return {"state": "success", "published_generation": portal.manifest["generation"]}

    result = snapshot_pipeline.run_snapshot_pipeline(
        tmp_path / "external",
        TARGET,
        user_data_dir=user_data,
        state_dir=state,
        status_file=state / "daily_update_1830.status.json",
        plan_builder=_fixture_plan_builder,
        worker_factory=FakeWorker,
        prepare_fn=_reject_prepare,
    )
    assert result == 1
    assert not (state / "snapshot_pipeline" / "current.json").exists()
    status = json.loads((state / "daily_update_1830.status.json").read_text(encoding="utf-8"))
    assert status["reason"] == "reload_failed"
