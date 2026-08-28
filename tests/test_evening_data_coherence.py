"""Offline contracts for the 18:30 data-to-decision freshness gate."""

from __future__ import annotations

import datetime as dt
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import qtrade_adapters.deepseek_harness.freshness as freshness
import qtrade_adapters.deepseek_harness.runtime as runtime
import scripts.daily_update_1830 as daily_update
import server


TARGET = dt.date(2026, 8, 25)
OLD = dt.date(2026, 8, 24)


def _make_deck(tmp_path: Path, *, bar_date: dt.date = TARGET, codes=None) -> Path:
    codes = codes or ("000001.SZ", "600519.SH")
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    with closing(sqlite3.connect(cache / "stock_basic.db")) as connection:
        connection.execute(
            "CREATE TABLE stock_basic(code TEXT PRIMARY KEY, name TEXT, out_date TEXT, status TEXT)"
        )
        connection.executemany(
            "INSERT INTO stock_basic VALUES (?, ?, ?, ?)",
            [(code, "Synthetic", "", "1") for code in codes],
        )
        connection.commit()
    with closing(sqlite3.connect(cache / "bars.db")) as connection:
        connection.execute(
            "CREATE TABLE bar_meta(code TEXT, adjust TEXT, start_date TEXT, end_date TEXT, rows INTEGER, updated_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE daily_bar(code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adjust TEXT)"
        )
        connection.executemany(
            "INSERT INTO bar_meta VALUES (?, 'qfq', '2020-01-01', ?, 130, '2026-08-25')",
            [(code, bar_date.isoformat()) for code in codes],
        )
        connection.executemany(
            "INSERT INTO daily_bar VALUES (?, ?, 1, 1.1, .9, 1, 100, 'qfq')",
            [(code, bar_date.isoformat()) for code in codes],
        )
        connection.commit()
    (tmp_path / "logs").mkdir()
    (tmp_path / "scripts").mkdir()
    return tmp_path


def _write_factor_artifacts(deck: Path, target: dt.date, *, changed=True) -> None:
    output = deck / "data" / "factorpool" / "output"
    output.mkdir(parents=True, exist_ok=True)
    suffix = target.strftime("%Y%m%d_000000" if changed else "%Y%m%d_000001")
    (output / f"factor_manifest_{suffix}.json").write_text(
        json.dumps({"date": target.isoformat(), "factors": [{"factor": "synthetic", "eligible": True}]}),
        encoding="utf-8",
    )
    (output / f"factor_data_freshness_{suffix}.json").write_text(
        json.dumps({"date": target.isoformat(), "updated": target.isoformat()}),
        encoding="utf-8",
    )
    (output / f"health_{target.isoformat()}.csv").write_text(
        "factor,test_date\nsynthetic," + target.isoformat() + "\n",
        encoding="utf-8",
    )


def _write_decision_artifacts(deck: Path, target: dt.date, *, empty=False) -> Path:
    logs = deck / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    pool = logs / f"opp_pool_{target:%Y%m%d}_000000.json"
    entries = [] if empty else [{"code": "000001.SZ", "pitch_date": target.isoformat()}]
    pool.write_text(
        json.dumps({"date": target.isoformat(), "n": len(entries), "opportunities": entries, "pitch": entries}),
        encoding="utf-8",
    )
    (logs / f"pitch_v2_{target:%Y%m%d}_000001.json").write_text(
        json.dumps(
            {
                "date": target.isoformat(),
                "pool_date": target.isoformat(),
                "source_pool": pool.name,
                "pitch": entries,
            }
        ),
        encoding="utf-8",
    )
    return pool


def test_portal_requires_target_and_dynamic_coverage(tmp_path):
    deck = _make_deck(tmp_path)
    baseline = {"coverage": 2}

    result = freshness.verify_portal(deck, TARGET, baseline=baseline)

    assert result["verified"] is True
    assert result["as_of"] == TARGET.isoformat()
    assert result["total"] == 2
    assert result["coverage"] == 2
    assert result["coverage_required"] == 2
    assert result["source"] == "external_sqlite"

    stale = _make_deck(tmp_path / "stale", bar_date=OLD)
    stale_result = freshness.verify_portal(stale, TARGET, baseline={"coverage": 2})
    assert stale_result["verified"] is False
    assert stale_result["reason"] == "portal_stale"


def test_portal_coverage_drop_fails_closed_and_schema_failure_is_safe(tmp_path):
    deck = _make_deck(tmp_path)
    with closing(sqlite3.connect(deck / "data" / "cache" / "bars.db")) as connection:
        connection.execute("UPDATE bar_meta SET end_date = ? WHERE code = '000001.SZ'", (OLD.isoformat(),))
        connection.commit()
    result = freshness.verify_portal(deck, TARGET, baseline={"coverage": 2})
    assert result["verified"] is False
    assert result["reason"] == "portal_coverage_insufficient"

    broken = tmp_path / "broken"
    (broken / "data" / "cache").mkdir(parents=True)
    (broken / "data" / "cache" / "stock_basic.db").write_bytes(b"not sqlite")
    broken_result = freshness.verify_portal(broken, TARGET)
    assert broken_result["verified"] is False
    assert broken_result["source"] == "unavailable"


def test_factor_verifier_requires_changed_target_dated_core_artifacts(tmp_path):
    deck = _make_deck(tmp_path)
    before = freshness.capture_artifacts(deck)
    _write_factor_artifacts(deck, TARGET)

    result = freshness.verify_factors(deck, TARGET, before=before)

    assert result["verified"] is True
    assert result["as_of"] == TARGET.isoformat()
    assert result["factor_count"] == 1
    assert result["valid_count"] == 1

    unchanged = freshness.verify_factors(deck, TARGET, before=freshness.capture_artifacts(deck))
    assert unchanged["verified"] is False
    assert unchanged["reason"] == "factor_artifact_unchanged"

    old = _make_deck(tmp_path / "old")
    _write_factor_artifacts(old, OLD)
    old_result = freshness.verify_factors(old, TARGET, before=freshness.capture_artifacts(old))
    assert old_result["verified"] is False
    assert old_result["reason"] == "factor_date_mismatch"

    missing_date = _make_deck(tmp_path / "missing-date")
    output = missing_date / "data" / "factorpool" / "output"
    output.mkdir(parents=True, exist_ok=True)
    (output / "factor_manifest_no-date.json").write_text(
        json.dumps({"factors": [{"factor": "synthetic", "eligible": True}]}),
        encoding="utf-8",
    )
    (output / "factor_data_freshness_no-date.json").write_text("not json", encoding="utf-8")
    no_date = freshness.verify_factors(missing_date, TARGET)
    assert no_date["verified"] is False
    assert no_date["reason"] == "factor_date_mismatch"


def test_decision_verifier_rejects_old_pool_and_accepts_explicit_empty_result(tmp_path):
    deck = _make_deck(tmp_path)
    before = freshness.capture_artifacts(deck)
    _write_decision_artifacts(deck, OLD)
    stale = freshness.verify_decision(deck, TARGET, before=before, require_pitch=True)
    assert stale["verified"] is False
    assert stale["reason"] == "decision_pool_missing_or_stale"

    _write_decision_artifacts(deck, TARGET, empty=True)
    result = freshness.verify_decision(deck, TARGET, before=before, require_pitch=True)
    assert result["verified"] is True
    assert result["pitch_count"] == 0
    assert result["pitch_verified"] is True

    mismatch = _make_deck(tmp_path / "pitch-mismatch")
    before_mismatch = freshness.capture_artifacts(mismatch)
    _write_decision_artifacts(mismatch, TARGET)
    pitch = mismatch / "logs" / f"pitch_v2_{TARGET:%Y%m%d}_000001.json"
    payload = json.loads(pitch.read_text(encoding="utf-8"))
    payload["pool_date"] = OLD.isoformat()
    pitch.write_text(json.dumps(payload), encoding="utf-8")
    mismatch_result = freshness.verify_decision(
        mismatch, TARGET, before=before_mismatch, require_pitch=True
    )
    assert mismatch_result["verified"] is False
    assert mismatch_result["reason"] == "decision_pitch_missing_or_stale"


def test_sync_destination_is_read_from_literal_without_running_script(tmp_path):
    deck = _make_deck(tmp_path)
    script = deck / "scripts" / "sync_data_to_roaming.py"
    script.write_text("from pathlib import Path\nDEST = Path(r'C:/synthetic/target')\n", encoding="utf-8")

    assert freshness.resolve_sync_destination(deck) == Path("C:/synthetic/target")


def test_sync_verification_requires_target_artifacts_to_change(tmp_path):
    deck = _make_deck(tmp_path)
    _write_factor_artifacts(deck, TARGET)
    _write_decision_artifacts(deck, TARGET)
    before = freshness.capture_artifacts(deck)

    result = freshness.verify_sync(deck, TARGET, before=before)

    assert result["verified"] is False
    assert result["reason"] == "sync_target_stale_or_incomplete"
    assert result["factors"] is False
    assert result["decision"] is False


def test_pipeline_runs_in_order_and_success_requires_all_freshness_groups(tmp_path, monkeypatch):
    deck = _make_deck(tmp_path / "deck")
    sync = _make_deck(tmp_path / "sync")
    (deck / "scripts" / "auto_update_daily.py").write_text("", encoding="utf-8")
    (deck / "scripts" / "build_factor_pool_engine.py").write_text("", encoding="utf-8")
    (deck / "scripts" / "sync_data_to_roaming.py").write_text("", encoding="utf-8")
    (deck / "factors" / "opportunities").mkdir(parents=True)
    (deck / "factors" / "opportunities" / "scan.py").write_text("", encoding="utf-8")
    (deck / "factors" / "opportunities" / "pitch_v2.py").write_text("", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        name = Path(command[3]).name
        calls.append(name)
        if name == "build_factor_pool_engine.py":
            _write_factor_artifacts(deck, TARGET)
        elif name == "scan.py":
            _write_decision_artifacts(deck, TARGET)
        elif name == "pitch_v2.py":
            pass
        elif name == "sync_data_to_roaming.py":
            for relative in ("data", "logs"):
                source = deck / relative
                target = sync / relative
                if source.exists():
                    if target.exists() and target.is_dir():
                        shutil.rmtree(target)
                    shutil.copytree(source, target)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", tmp_path / "run.log")
    monkeypatch.setattr(daily_update, "PY", "python-under-test")
    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)
    monkeypatch.setattr(daily_update.freshness, "resolve_sync_destination", lambda value: sync)
    status = tmp_path / "status.json"

    result = daily_update.main(
        ["--force", "--status-file", str(status)],
        today=TARGET,
    )

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 0
    assert calls == [
        "auto_update_daily.py",
        "build_factor_pool_engine.py",
        "scan.py",
        "pitch_v2.py",
        "sync_data_to_roaming.py",
    ]
    assert payload["state"] == "success"
    assert payload["outputs"] == {"portal": True, "factors": True, "decision": True, "sync": True}
    assert all(item["verified"] for item in payload["freshness"].values())


def test_stale_portal_stops_before_factor_step(tmp_path, monkeypatch):
    deck = _make_deck(tmp_path / "deck", bar_date=OLD)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(Path(command[3]).name)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(daily_update, "DECK", deck)
    monkeypatch.setattr(daily_update, "LOG", tmp_path / "run.log")
    monkeypatch.setattr(daily_update, "PY", "python-under-test")
    monkeypatch.setattr(daily_update.subprocess, "run", fake_run)
    status = tmp_path / "status.json"

    result = daily_update.main(["--force", "--status-file", str(status)], today=TARGET)

    payload = json.loads(status.read_text(encoding="utf-8"))
    assert result == 1
    assert calls == ["auto_update_daily.py"]
    assert payload["freshness"]["portal"]["reason"] == "portal_stale"
    assert payload["outputs"] == {"portal": False, "factors": False, "decision": False, "sync": False}


def test_runtime_retries_only_transient_failures_at_most_three_times(tmp_path):
    status = tmp_path / "status.json"
    calls = []
    sleeps = []
    seen_retry = []

    class Processes:
        def run(self, command, **kwargs):
            if status.exists():
                seen_retry.append(json.loads(status.read_text(encoding="utf-8")).get("retry"))
            calls.append(1)
            status.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "state": "failure",
                        "reason": "step_failed",
                        "freshness": {"portal": {"reason": "portal_stale"}},
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=1 if len(calls) < 3 else 0)

    result = runtime.run_daily_update(
        tmp_path / "deck",
        TARGET,
        subprocess_module=Processes(),
        project_root=tmp_path,
        status_file=status,
        sleep_fn=sleeps.append,
        clock=lambda: dt.datetime(2026, 8, 25, 18, 30),
    )

    assert result == 0
    assert len(calls) == 3
    assert sleeps == [300.0, 300.0]
    assert seen_retry[0]["attempt"] == 1
    assert seen_retry[1]["attempt"] == 2


def test_runtime_does_not_retry_permanent_step_failure(tmp_path):
    calls = []
    sleeps = []

    class Processes:
        def run(self, command, **kwargs):
            calls.append(1)
            Path(tmp_path / "status.json").write_text(
                json.dumps({"state": "failure", "reason": "step_failed"}), encoding="utf-8"
            )
            return SimpleNamespace(returncode=9)

    result = runtime.run_daily_update(
        tmp_path / "deck",
        TARGET,
        subprocess_module=Processes(),
        project_root=tmp_path,
        status_file=tmp_path / "status.json",
        sleep_fn=sleeps.append,
    )

    assert result == 9
    assert calls == [1]
    assert sleeps == []


def test_status_api_filters_freshness_paths_commands_and_env(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trade_date": TARGET.isoformat(),
                "state": "success",
                "reason": "completed",
                "freshness": {
                    "portal": {
                        "verified": True,
                        "as_of": TARGET.isoformat(),
                        "total": 3193,
                        "coverage": 3193,
                        "source": "external_sqlite",
                        "reason": "verified",
                        "absolute_path": "C:/secret",
                    }
                },
                "output_meta": {"portal": {"reason": "python --secret TOKEN"}},
                "retry": {"attempt": 1, "max_attempts": 3, "next_attempt_at": "2026-08-25T18:35:00"},
                "command": "python secret",
                "env": "TOKEN=secret",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "UPDATE_STATUS_PATH", path)

    result = server.read_update_status()

    assert result["freshness"]["portal"] == {
        "verified": True,
        "as_of": TARGET.isoformat(),
        "source": "external_sqlite",
        "reason": "verified",
        "total": 3193,
        "coverage": 3193,
    }
    assert result["output_meta"]["portal"]["reason"] == "unavailable"
    assert result["retry"] == {
        "attempt": 1,
        "max_attempts": 3,
        "next_attempt_at": "2026-08-25T18:35:00",
    }
    assert "secret" not in json.dumps(result)
