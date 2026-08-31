"""Offline contracts for the PR27A generation snapshot foundation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

import qtrade_adapters.deepseek_harness.portal_refresh as portal_refresh
from qtrade_adapters.deepseek_harness.portal_refresh import (
    PortalRefreshError,
    portal_refresh_paths,
    publish_snapshot,
    read_current_snapshot,
)
import server
from qtrade_adapters.deepseek_harness.market_data import MainboardMarketDataAdapter


TARGET = "2026-08-28"
SYMBOLS = ["600519", "000001", "000002", "000003", "000004"]


def _state(tmp_path: Path) -> tuple[Path, Path]:
    user_data = tmp_path / "user-data"
    return user_data, user_data / "state"


def _rows(symbols: list[str], target: str = TARGET) -> dict[str, list[dict]]:
    result = {}
    for offset, symbol in enumerate(symbols):
        price = 10.0 + offset
        result[symbol] = [{
            "code": f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}",
            "date": target,
            "open": price,
            "high": price + 1,
            "low": price - 1,
            "close": price + 0.5,
            "volume": 1000 + offset,
            "adjust": "qfq",
        }]
    return result


def _metadata(symbols: list[str], target: str = TARGET) -> list[dict]:
    return [{
        "code": symbol,
        "name": f"Name {symbol}",
        "exchange": "SH" if symbol.startswith("6") else "SZ",
        "risk_warning": None,
        "suspended": False,
        "listed": True,
        "tradable": True,
        "history_rows": 1,
        "latest_trade_date": target,
        "computable": True,
        "eligible_reason": None,
    } for symbol in symbols]


def _publish(tmp_path: Path, symbols: list[str] | None = None):
    symbols = symbols or SYMBOLS
    user_data, state = _state(tmp_path)
    snapshot = publish_snapshot(
        symbols,
        TARGET,
        _rows(symbols),
        _metadata(symbols),
        state_dir=state,
        user_data_dir=user_data,
        universe_token="fixture-v1",
    )
    return user_data, state, snapshot


def test_paths_require_trusted_user_data_state_containment(tmp_path: Path) -> None:
    user_data, state = _state(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=user_data)
    assert paths.root == state / "portal_refresh"
    with pytest.raises(ValueError, match="exactly user-data/state"):
        portal_refresh_paths(tmp_path / "other", user_data_dir=user_data)
    with pytest.raises(ValueError, match="explicit user-data root"):
        portal_refresh_paths(tmp_path / "arbitrary")
    with pytest.raises(ValueError, match="explicit user-data root"):
        portal_refresh_paths(state)
    assert paths.generation_dir("a" * 64) == paths.generations / ("a" * 64)
    with pytest.raises(PortalRefreshError, match="invalid_generation"):
        paths.generation_dir("../escape")


def test_five_and_hundred_symbols_publish_as_distinct_verified_generations(tmp_path: Path) -> None:
    _, state, first = _publish(tmp_path / "five")
    assert len(first.manifest["symbols"]) == 5
    assert read_current_snapshot(state, user_data_dir=state.parent) is not None

    hundred = [f"{index:06d}" for index in range(100)]
    user_data, hundred_state = _state(tmp_path / "hundred")
    second = publish_snapshot(
        hundred,
        TARGET,
        _rows(hundred),
        _metadata(hundred),
        state_dir=hundred_state,
        user_data_dir=user_data,
        universe_token="fixture-v2",
    )
    assert second.manifest["total"] == 100
    assert len(list((hundred_state / "portal_refresh" / "generations").iterdir())) == 1


def test_pointer_is_last_and_old_lkg_survives_publish_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, state, old = _publish(tmp_path / "old")
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    old_pointer = paths.current.read_bytes()
    original = portal_refresh._atomic_json

    def fail_pointer(path: Path, payload: dict, **kwargs: object) -> None:
        if path.name == "current.json":
            raise OSError("simulated pointer failure")
        original(path, payload, **kwargs)

    monkeypatch.setattr(portal_refresh, "_atomic_json", fail_pointer)
    with pytest.raises(PortalRefreshError, match="mirror_publish_failed"):
        publish_snapshot(
            SYMBOLS,
            TARGET,
            _rows(SYMBOLS),
            _metadata(SYMBOLS),
            state_dir=state,
            user_data_dir=state.parent,
            universe_token="new-generation",
        )
    assert paths.current.read_bytes() == old_pointer
    current = read_current_snapshot(state, user_data_dir=state.parent)
    assert current is not None
    assert current.manifest["token"] == old.manifest["token"]


def test_generation_directory_fsync_failure_keeps_old_lkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_data, state, old = _publish(tmp_path / "generation-fsync")
    paths = portal_refresh_paths(state, user_data_dir=user_data)
    old_pointer = paths.current.read_bytes()
    original = portal_refresh._fsync_directory

    def fail_generation_directory(path: Path) -> None:
        if path.name == "generations":
            raise OSError("simulated generation publish failure")
        original(path)

    monkeypatch.setattr(portal_refresh, "_fsync_directory", fail_generation_directory)
    with pytest.raises(PortalRefreshError, match="mirror_publish_failed"):
        publish_snapshot(
            SYMBOLS,
            TARGET,
            {**_rows(SYMBOLS), "600519": [{**_rows(SYMBOLS)["600519"][0], "close": 10.75}]},
            _metadata(SYMBOLS),
            state_dir=state,
            user_data_dir=user_data,
            universe_token="fixture-v2",
        )
    assert paths.current.read_bytes() == old_pointer
    current = read_current_snapshot(state, user_data_dir=user_data)
    assert current is not None
    assert current.manifest["generation"] == old.manifest["generation"]


def test_corrupt_or_unknown_manifest_is_fail_closed(tmp_path: Path) -> None:
    _, state, _ = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    snapshot = read_current_snapshot(state, user_data_dir=state.parent)
    assert snapshot is not None
    manifest_path = paths.generation_manifest(snapshot.manifest["generation"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["unexpected"] = "reject"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_current_snapshot(state, user_data_dir=state.parent) is None


@pytest.mark.parametrize("field", ["db_sha256", "db_size", "metadata_sha256", "metadata_size"])
def test_pointer_or_generation_hash_metadata_tampering_is_rejected(tmp_path: Path, field: str) -> None:
    _, state, _ = _publish(tmp_path / field)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    pointer = json.loads(paths.current.read_text(encoding="utf-8"))
    pointer[field] = "0" if field.endswith("sha256") else pointer[field] + 1
    paths.current.write_text(json.dumps(pointer), encoding="utf-8")
    assert read_current_snapshot(state, user_data_dir=state.parent) is None


def test_generation_metadata_and_bars_are_exclusive(tmp_path: Path) -> None:
    _, state, _ = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    snapshot = read_current_snapshot(state, user_data_dir=state.parent)
    assert snapshot is not None
    metadata_path = paths.generation_metadata(snapshot.manifest["generation"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["generation"] = "0" * 64
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    assert read_current_snapshot(state, user_data_dir=state.parent) is None


def test_database_schema_target_and_symbol_set_are_verified(tmp_path: Path) -> None:
    _, state, _ = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    snapshot = read_current_snapshot(state, user_data_dir=state.parent)
    assert snapshot is not None
    with sqlite3.connect(snapshot.database) as connection:
        connection.execute("UPDATE daily_bar SET date = '2026-08-27'")
        connection.commit()
    assert read_current_snapshot(state, user_data_dir=state.parent) is None
    assert paths.current.exists()


def test_reader_rejects_extra_sqlite_objects_and_generation_sidecars(tmp_path: Path) -> None:
    _, state, snapshot = _publish(tmp_path / "schema")
    with sqlite3.connect(snapshot.database) as connection:
        connection.execute("CREATE TABLE unexpected (value TEXT)")
        connection.commit()
    assert read_current_snapshot(state, user_data_dir=state.parent) is None

    _, sidecar_state, sidecar_snapshot = _publish(tmp_path / "sidecar")
    sidecar_paths = portal_refresh_paths(sidecar_state, user_data_dir=sidecar_state.parent)
    generation_dir = sidecar_paths.generation_dir(sidecar_snapshot.manifest["generation"])
    (generation_dir / "unexpected.sidecar").write_text("no", encoding="utf-8")
    assert read_current_snapshot(sidecar_state, user_data_dir=sidecar_state.parent) is None


@pytest.mark.parametrize("field", ["total", "universe_token", "content_sha256", "generation_nonce"])
def test_pointer_fields_must_match_manifest(tmp_path: Path, field: str) -> None:
    _, state, _ = _publish(tmp_path / field)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    pointer = json.loads(paths.current.read_text(encoding="utf-8"))
    if field == "total":
        pointer[field] = pointer[field] + 1
    elif field == "universe_token":
        pointer[field] = "other"
    else:
        pointer[field] = "0" * len(pointer[field])
    paths.current.write_text(json.dumps(pointer), encoding="utf-8")
    assert read_current_snapshot(state, user_data_dir=state.parent) is None


def test_pointer_post_replace_fsync_failure_keeps_published_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    user_data, state, first = _publish(tmp_path / "fsync")
    original = portal_refresh._fsync_directory

    def fail_after_pointer(path: Path) -> None:
        if path.name == "portal_refresh":
            raise OSError("simulated post-pointer fsync failure")
        original(path)

    monkeypatch.setattr(portal_refresh, "_fsync_directory", fail_after_pointer)
    second = publish_snapshot(
        SYMBOLS,
        TARGET,
        {**_rows(SYMBOLS), "600519": [{**_rows(SYMBOLS)["600519"][0], "close": 10.75}]},
        _metadata(SYMBOLS),
        state_dir=state,
        user_data_dir=user_data,
        universe_token="fixture-v2",
    )
    assert second.manifest["generation"] != first.manifest["generation"]
    assert read_current_snapshot(state, user_data_dir=user_data) is not None


def test_changed_content_gets_new_generation_even_for_same_symbols_and_date(tmp_path: Path) -> None:
    user_data, state, first = _publish(tmp_path / "content")
    changed_rows = _rows(SYMBOLS)
    changed_rows["600519"][0]["close"] = 10.75
    second = publish_snapshot(
        SYMBOLS,
        TARGET,
        changed_rows,
        _metadata(SYMBOLS),
        state_dir=state,
        user_data_dir=user_data,
        universe_token="fixture-v1",
    )
    assert second.manifest["generation"] != first.manifest["generation"]
    assert second.manifest["content_sha256"] != first.manifest["content_sha256"]
    assert len(list((state / "portal_refresh" / "generations").iterdir())) == 2


def test_overlay_snapshot_token_does_not_touch_external_metadata_database(tmp_path: Path) -> None:
    _, _, snapshot = _publish(tmp_path / "overlay")
    adapter = MainboardMarketDataAdapter(
        base_dir=tmp_path / "external",
        csv_dir=tmp_path / "csv",
        overlay_db=snapshot.database,
        overlay_only=True,
        overlay_manifest=dict(snapshot.manifest),
        overlay_metadata=list(snapshot.metadata),
    )

    def fail_external_path(name: str) -> Path:
        raise AssertionError(f"external metadata accessed: {name}")

    adapter._db_path = fail_external_path  # type: ignore[method-assign]
    assert "overlay=" in adapter.snapshot_token()


def test_invalid_input_does_not_create_or_replace_current(tmp_path: Path) -> None:
    user_data, state = _state(tmp_path)
    with pytest.raises(PortalRefreshError, match="invalid_symbol_count"):
        publish_snapshot(["600519"], TARGET, _rows(["600519"]), _metadata(["600519"]), state_dir=state, user_data_dir=user_data)
    assert not (state / "portal_refresh").exists()

    with pytest.raises(PortalRefreshError, match="invalid_bar_value"):
        publish_snapshot(SYMBOLS, TARGET, {**_rows(SYMBOLS), "600519": [{**_rows(SYMBOLS)["600519"][0], "close": float("nan")}]}, _metadata(SYMBOLS), state_dir=state, user_data_dir=user_data)
    assert not (state / "portal_refresh" / "current.json").exists()


def test_same_token_is_idempotent_without_replacing_current(tmp_path: Path) -> None:
    user_data, state, first = _publish(tmp_path)
    current_bytes = (state / "portal_refresh" / "current.json").read_bytes()
    second = publish_snapshot(SYMBOLS, TARGET, _rows(SYMBOLS), _metadata(SYMBOLS), state_dir=state, user_data_dir=user_data, universe_token="fixture-v1")
    assert second.manifest["token"] == first.manifest["token"]
    assert (state / "portal_refresh" / "current.json").read_bytes() == current_bytes


def test_no_current_snapshot_keeps_external_fallback_and_current_mirror_is_exclusive(tmp_path: Path) -> None:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "000999.csv").write_text("date,open,high,low,close,volume\n2026-08-28,1,2,1,1.5,10\n", encoding="utf-8")
    service = server.DataService(str(csv_dir), live=False, portal_state_dir=None)
    assert service.portal_mirror_active is False
    assert service.load_history("000999") is not None

    user_data, state, _ = _publish(tmp_path / "mirror")
    service = server.DataService(
        str(csv_dir), live=True, portal_state_dir=state, portal_user_data_dir=user_data
    )
    assert service.portal_mirror_active is True
    assert service.live_src is None
    assert service.scan() == SYMBOLS
    assert service.mainboard_adapter.universe_summary()["source"] == "qtrade_mirror"
    assert service.load_history("000999") is None
    assert service.symbol_metadata("000999") is None
    assert user_data.exists()


def test_corrupt_current_causes_safe_external_fallback_not_mixed_mirror(tmp_path: Path) -> None:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "000999.csv").write_text("date,open,high,low,close,volume\n2026-08-28,1,2,1,1.5,10\n", encoding="utf-8")
    user_data, state, _ = _publish(tmp_path / "mirror")
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    paths.current.write_text("{}", encoding="utf-8")
    service = server.DataService(
        str(csv_dir), live=False, portal_state_dir=state, portal_user_data_dir=user_data
    )
    assert service.portal_mirror_active is False
    assert service.load_history("000999") is not None


def test_published_files_are_hashable_and_third_party_is_not_involved(tmp_path: Path) -> None:
    _, state, snapshot = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=state.parent)
    manifest_path = paths.generation_manifest(snapshot.manifest["generation"])
    assert hashlib.sha256(snapshot.database.read_bytes()).hexdigest() == snapshot.manifest["db_sha256"]
    pointer = json.loads(paths.current.read_text(encoding="utf-8"))
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == pointer["manifest_sha256"]
    assert "third_party" not in os.fspath(snapshot.database)


def test_reader_ignores_trusted_crash_residue_and_reads_old_lkg(tmp_path: Path) -> None:
    user_data, state, snapshot = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=user_data)
    pointer_tmp = paths.root / f".current.json.{'a' * 32}.tmp"
    pointer_tmp.write_text("partial pointer", encoding="utf-8")
    staging = paths.generations / f".staging-{'b' * 32}"
    staging.mkdir()
    (staging / "partial.tmp").write_text("partial generation", encoding="utf-8")

    current = read_current_snapshot(state, user_data_dir=user_data)

    assert current is not None
    assert current.manifest["generation"] == snapshot.manifest["generation"]


def test_reader_rejects_unknown_residue_and_preserves_lkg(tmp_path: Path) -> None:
    user_data, state, snapshot = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=user_data)
    (paths.root / "unexpected.tmp").write_text("unknown", encoding="utf-8")

    assert read_current_snapshot(state, user_data_dir=user_data) is None
    assert paths.current.exists()
    assert snapshot.database.exists()


def test_atomic_pointer_temp_name_is_reader_safe_after_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_data, state, snapshot = _publish(tmp_path)
    paths = portal_refresh_paths(state, user_data_dir=user_data)
    old_pointer = paths.current.read_bytes()
    captured: dict[str, Path] = {}
    real_replace = portal_refresh.os.replace
    real_unlink = Path.unlink

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        if Path(target) == paths.current:
            captured["temporary"] = source_path
            assert source_path.exists()
            raise OSError("simulated pointer replace failure")
        real_replace(source, target)

    def preserve_failed_temp(self: Path, *args: object, **kwargs: object) -> None:
        if self == captured.get("temporary"):
            raise OSError("simulated crash residue")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(portal_refresh.os, "replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", preserve_failed_temp)
    with pytest.raises(OSError, match="simulated pointer replace failure"):
        portal_refresh._atomic_bytes(paths.current, b"partial pointer")

    temporary = captured["temporary"]
    assert portal_refresh._POINTER_TEMP_RE.fullmatch(temporary.name)
    assert temporary.exists()
    assert paths.current.read_bytes() == old_pointer
    current = read_current_snapshot(state, user_data_dir=user_data)
    assert current is not None
    assert current.manifest["generation"] == snapshot.manifest["generation"]

    monkeypatch.undo()
    temporary.unlink()


def test_pr27a_module_has_no_provider_job_execution_surface() -> None:
    source = Path(portal_refresh.__file__).read_text(encoding="utf-8")
    assert "class PortalRefreshJob" not in source
    assert "import subprocess" not in source
    assert "import threading" not in source
