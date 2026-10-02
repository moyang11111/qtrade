from __future__ import annotations

import builtins
import hashlib
import json
import sqlite3
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from qtrade_adapters.deepseek_harness.next_day_probability import (
    MODEL_VERSION,
    ResearchError,
    ResearchJobManager,
    _build_dataset_from_portal,
    _model_quality_gate,
    _NumpyLogistic,
    _NumpyPlatt,
    _features,
    _snapshot_freshness,
    _verified_portal_symbol_names,
    current_scoring_windows,
    dataset_version,
    is_same_origin_local_request,
    run_research,
    temporal_windows,
)
from qtrade_adapters.deepseek_harness import next_day_probability as probability_module
from qtrade_adapters.deepseek_harness import portal_refresh


REPO_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def test_temporal_windows_require_enough_dates_and_purge_label_boundaries():
    with pytest.raises(ResearchError, match="320"):
        temporal_windows(319)

    windows = temporal_windows(320)
    assert len(windows["rolling"]) == 3
    assert windows["final"]["purge_dates"] == 2
    for split in [*windows["rolling"], windows["final"]]:
        train_start, train_stop = split["train"]
        cal_start, cal_stop = split["calibration"]
        test_start, test_stop = split["test"]
        assert train_start < train_stop <= cal_start - 1
        assert cal_start < cal_stop <= test_start - 1
        assert test_start < test_stop <= 320
        # The label on the last train/calibration row ends in its purge date,
        # never on a date assigned to the next partition.
        assert train_stop - 1 + 1 < cal_start
        assert cal_stop - 1 + 1 < test_start


def test_current_scoring_window_uses_recent_labels_with_separate_calibration():
    windows = current_scoring_windows(320)
    train_start, train_stop = windows["train"]
    cal_start, cal_stop = windows["calibration"]
    assert train_start == 0
    assert train_stop - 1 + 1 < cal_start
    assert cal_stop == 319  # index 319 is as-of itself and has no known label
    assert cal_start < cal_stop


def test_features_do_not_change_when_future_bars_are_appended():
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=45, freq="B").strftime("%Y-%m-%d"),
        "open": np.linspace(10, 14, 45),
        "high": np.linspace(10.2, 14.2, 45),
        "low": np.linspace(9.8, 13.8, 45),
        "close": np.linspace(10, 14, 45),
        "volume": np.linspace(1000, 5000, 45),
    })
    original = _features(frame.iloc[:30].copy())
    with_future = _features(frame.copy()).iloc[:30]
    pd.testing.assert_frame_equal(original, with_future)


def test_validation_selection_ignores_final_test_and_excludes_unfinished_models():
    choose = probability_module._select_validation_candidate
    models = {
        "pooled_logistic": {"validation_eligible": True,
            "rolling_oos": {"log_loss": 0.70, "brier_score": 0.25, "roc_auc": 0.55},
            "final_test": {"log_loss": 0.1, "roc_auc": 1.0}},
        "lightgbm": {"validation_eligible": True,
            "rolling_oos": {"log_loss": 0.68, "brier_score": 0.24, "roc_auc": 0.54},
            "final_test": {"log_loss": 9.0, "roc_auc": 0.0}},
        "unfinished": {"validation_eligible": False,
            "rolling_oos": {"log_loss": 0.0, "brier_score": 0.0, "roc_auc": 1.0}},
    }
    assert choose(models) == "lightgbm"
    models["lightgbm"]["rolling_oos"]["log_loss"] = float("nan")
    assert choose(models) == "pooled_logistic"


def test_native_lightgbm_learns_interaction_without_sklearn(monkeypatch):
    pytest.importorskip("lightgbm")
    real_import = builtins.__import__
    def deny_sklearn(name, *args, **kwargs):
        if name.split(".", 1)[0] == "sklearn":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", deny_sklearn)
    rng = np.random.default_rng(883)
    X = rng.normal(size=(12_000, 3)).astype(np.float32)
    y = (X[:, 0] * X[:, 1] > 0).astype(np.int8)
    model = probability_module._fit_model(X[:10_000], y[:10_000], "lightgbm")
    p = model.predict_proba(X[10_000:])[:, 1]
    assert np.isfinite(p).all() and ((p > 0) & (p < 1)).all()
    assert np.mean((p > 0.5) == y[10_000:]) > 0.9


def test_current_scoring_uses_selected_kind_and_never_as_of_label(monkeypatch):
    dates = np.repeat(np.arange(320), 150)
    data = {"date_idx": dates, "X": np.zeros((len(dates), 1), dtype=np.float32),
            "y": np.tile([0, 1], len(dates) // 2), "current_X": np.zeros((3, 1)),
            "windows": {"date_count": 320}, "dates": [str(i) for i in range(320)]}
    calls = []
    def fit(X, y, kind):
        calls.append((kind, len(y)))
        return object()
    monkeypatch.setattr(probability_module, "_fit_model", fit)
    monkeypatch.setattr(probability_module, "_calibrate", lambda *args: object())
    monkeypatch.setattr(probability_module, "_calibrated_probability", lambda m, c, x: np.full(len(x), 0.55))
    report = probability_module._fit_current_scoring_model(data, "lightgbm")
    assert calls[0][0] == "lightgbm"
    assert report["window"]["calibration"]["signal_through"] == "318"
    assert report["window"]["train"]["signal_through"] < report["window"]["calibration"]["signal_from"]
    assert report["probability"].shape == (3,)


def test_data_fingerprint_binds_to_csv_generation_and_target(tmp_path: Path):
    csv = tmp_path / "000001.csv"
    csv.write_text("date,close\n2025-01-01,10\n", encoding="utf-8")
    base = dataset_version(tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-02")
    assert base != dataset_version(tmp_path, "2025-01-01", ["000001"], "generation-b", "2025-01-02")
    assert base != dataset_version(tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-03")
    csv.write_text("date,close\n2025-01-01,100\n", encoding="utf-8")
    assert base != dataset_version(tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-02")


def test_data_fingerprint_binds_to_authoritative_sqlite_snapshot(tmp_path: Path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"first")
    first = dataset_version(
        tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-02",
        history_database=database, history_database_sha256="a" * 64,
    )
    assert first != dataset_version(
        tmp_path, "2025-01-01", ["000001"], "generation-b", "2025-01-02",
        history_database=database, history_database_sha256="a" * 64,
    )
    assert first != dataset_version(
        tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-03",
        history_database=database, history_database_sha256="a" * 64,
    )
    database.write_bytes(b"second database")
    assert first != dataset_version(
        tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-02",
        history_database=database, history_database_sha256="b" * 64,
    )
    assert first != dataset_version(
        tmp_path, "2025-01-01", ["000001"], "generation-a", "2025-01-02",
        history_database=database, history_database_sha256="a" * 64,
        history_metadata_sha256="c" * 64,
    )


def test_target_date_freshness_marks_expired_and_unconfirmed_targets():
    now = datetime(2026, 9, 29, 19, 0)
    expired = _snapshot_freshness("2026-09-24", True, now)
    assert expired["status"] == "EXPIRED"
    assert expired["expired"] is True
    same_day_after_close = _snapshot_freshness("2026-09-29", True, now)
    assert same_day_after_close["expired"] is True
    unconfirmed = _snapshot_freshness("2026-09-30", False, now)
    assert unconfirmed["status"] == "UNCONFIRMED"


def _write_synthetic_portal_database(root: Path, *, symbol_count: int = 100):
    dates = pd.bdate_range("2025-01-02", periods=320)
    symbols = [f"{index:06d}" for index in range(1, symbol_count + 1)]
    generation = "a" * 64
    database = root / "portal_refresh" / "generations" / generation / "bars_incr.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute(portal_refresh._DB_CREATE_SQL)
    records = []
    for offset, symbol in enumerate(symbols):
        rng = np.random.default_rng(offset + 17)
        close = 12 * np.cumprod(1 + rng.normal(0.0004, 0.016, len(dates)))
        opening = close * (1 + rng.normal(0, 0.003, len(dates)))
        high = np.maximum(opening, close) * 1.01
        low = np.minimum(opening, close) * 0.99
        volume = rng.integers(50_000, 500_000, len(dates))
        market_code = portal_refresh._market_code(symbol)
        records.extend(
            (market_code, day, float(opn), float(hi), float(lo), float(cl), float(vol), "qfq")
            for day, opn, hi, lo, cl, vol in zip(
                dates.strftime("%Y-%m-%d"), opening, high, low, close, volume,
            )
        )
    connection.executemany(
        "INSERT INTO daily_bar(code,date,open,high,low,close,volume,adjust) VALUES(?,?,?,?,?,?,?,?)",
        records,
    )
    connection.commit()
    connection.close()
    manifest = {
        "schema_version": portal_refresh.HISTORY_SCHEMA_VERSION,
        "generation": generation,
        "history_schema": portal_refresh.HISTORY_DB_SCHEMA,
        "db_schema": portal_refresh.HISTORY_DB_SCHEMA,
        "db_path": f"generations/{generation}/bars_incr.db",
        "history_window": len(dates),
        "history_rows": [len(dates)] * len(symbols),
        "target_date": dates[-1].strftime("%Y-%m-%d"),
        "symbols": symbols,
        "db_size": database.stat().st_size,
        "db_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "metadata_path": f"generations/{generation}/metadata.json",
    }
    metadata_path = database.with_name("metadata.json")
    metadata = {
        "generation": generation,
        "target_date": manifest["target_date"],
        "schema": "portal_metadata.v2",
        "schema_version": 2,
        "items": [{"code": symbol, "name": f"样本{symbol}"} for symbol in symbols],
    }
    metadata_bytes = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    metadata_path.write_bytes(metadata_bytes)
    manifest.update({
        "metadata_size": len(metadata_bytes),
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
    })
    return database, manifest, symbols


def test_portal_sqlite_adapter_streams_verified_qfq_history(tmp_path: Path):
    database, manifest, symbols = _write_synthetic_portal_database(tmp_path)
    dataset = _build_dataset_from_portal(
        database, manifest, manifest["target_date"], set(symbols),
    )
    assert dataset["source_kind"] == "portal_qfq_sqlite"
    assert dataset["source_count"] == len(symbols)
    assert dataset["usable_symbols"] == len(symbols)
    assert dataset["current_symbols"] == symbols
    assert dataset["symbol_names"][symbols[0]] == f"样本{symbols[0]}"
    assert dataset["symbol_name_source"] == "已提交快照名称清单"
    assert len(dataset["dates"]) == 320
    assert len(dataset["windows"]["rolling"]) == 3
    assert len(dataset["y"]) >= 20_000

    tampered_manifest = dict(manifest, db_sha256="0" * 64)
    with pytest.raises(ResearchError, match="校验值"):
        _build_dataset_from_portal(
            database, tampered_manifest, manifest["target_date"], set(symbols),
        )
    unbound_manifest = dict(manifest, db_path=f"generations/{'b' * 64}/bars_incr.db")
    with pytest.raises(ResearchError, match="校验信息"):
        _build_dataset_from_portal(
            database, unbound_manifest, manifest["target_date"], set(symbols),
        )
    short_history_manifest = dict(
        manifest, history_window=280, history_rows=[280] * len(symbols),
    )
    with pytest.raises(ResearchError, match="校验信息"):
        _build_dataset_from_portal(
            database, short_history_manifest, manifest["target_date"], set(symbols),
        )


def test_snapshot_name_placeholders_are_not_reported_as_real_names(tmp_path: Path):
    database, manifest, symbols = _write_synthetic_portal_database(tmp_path, symbol_count=5)
    metadata_path = database.with_name("metadata.json")
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    for item in payload["items"]:
        item["name"] = item["code"]
    content = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    metadata_path.write_bytes(content)
    manifest = dict(
        manifest,
        metadata_size=len(content),
        metadata_sha256=hashlib.sha256(content).hexdigest(),
    )
    names, source = _verified_portal_symbol_names(
        database, manifest, symbols, manifest["target_date"],
    )
    assert names == {}
    assert source == "快照名称字段仅含代码占位"

    tampered = dict(manifest, metadata_sha256="0" * 64)
    names, source = _verified_portal_symbol_names(
        database, tampered, symbols, manifest["target_date"],
    )
    assert names == {}
    assert source == "快照公司名称清单校验失败"


def test_portal_sqlite_adapter_rejects_stock_history_not_ending_on_target(tmp_path: Path):
    database, manifest, symbols = _write_synthetic_portal_database(tmp_path, symbol_count=100)
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE daily_bar SET date='2000-01-01' WHERE code=? AND date=?",
        (portal_refresh._market_code(symbols[-1]), manifest["target_date"]),
    )
    connection.commit()
    connection.close()
    manifest = dict(
        manifest,
        db_size=database.stat().st_size,
        db_sha256=hashlib.sha256(database.read_bytes()).hexdigest(),
    )
    with pytest.raises(ResearchError, match="未以目标日结尾"):
        _build_dataset_from_portal(
            database, manifest, manifest["target_date"], set(symbols),
        )


def test_completed_job_is_stale_when_version_or_target_changes():
    manager = ResearchJobManager()
    manager._status = {
        "state": "complete",
        "report": {
            "as_of": "2025-01-01",
            "target_date": "2025-01-02",
            "snapshot_generation": "g1",
            "data_version": "v1",
        },
    }
    current = manager.snapshot(
        "2025-01-01", "2025-01-02", snapshot_generation="g1", data_version="v1",
    )
    assert current["state"] == "complete"
    assert manager.snapshot(
        "2025-01-01", "2025-01-02", snapshot_generation="g1", data_version="v2",
    )["state"] == "stale"
    assert manager.snapshot(
        "2025-01-01", "2025-01-03", snapshot_generation="g1", data_version="v1",
    )["state"] == "stale"


def test_job_manager_worker_reaches_terminal_state(monkeypatch, tmp_path):
    monkeypatch.setattr(probability_module, "dataset_version", lambda *args, **kwargs: "test-version")
    calls = []

    def fake_run_research(*args, **kwargs):
        calls.append(kwargs)
        return {
            "state": "complete",
            "as_of": kwargs["as_of"],
            "target_date": kwargs["next_trade_date"],
            "snapshot_generation": kwargs["snapshot_generation"],
            "data_version": kwargs["data_version"],
        }

    monkeypatch.setattr(probability_module, "run_research", fake_run_research)
    report_path = tmp_path / "next_day_probability.latest.json"
    manager = ResearchJobManager(report_path)
    queued = manager.start(
        data_dir=tmp_path,
        as_of="2026-09-23",
        eligible_symbols=["600000"],
        next_trade_date="2026-09-24",
        snapshot_generation="generation-1",
    )

    assert queued["state"] == "queued"
    assert manager._job is not None
    manager._job.join(timeout=3)

    assert not manager._job.is_alive()
    assert len(calls) == 1
    assert manager.snapshot(
        "2026-09-23", "2026-09-24", snapshot_generation="generation-1", data_version="test-version",
    )["state"] == "complete"
    persisted = json.loads(report_path.read_text(encoding="utf-8"))
    assert persisted["state"] == "complete"
    restored = ResearchJobManager(report_path)
    assert restored.snapshot(
        "2026-09-23", "2026-09-24", snapshot_generation="generation-1", data_version="test-version",
    )["state"] == "complete"


@pytest.mark.parametrize(
    ("origin", "host", "port", "fetch_site", "allowed"),
    [
        ("http://127.0.0.1:8765", "127.0.0.1:8765", 8765, "same-origin", True),
        ("http://localhost:8765", "localhost:8765", 8765, "", True),
        ("http://attacker.example:8765", "attacker.example:8765", 8765, "same-origin", False),
        ("http://localhost:8766", "localhost:8766", 8765, "same-origin", False),
        ("http://127.0.0.1:8765", "127.0.0.1:8765", 8765, "cross-site", False),
        ("https://127.0.0.1:8765", "127.0.0.1:8765", 8765, "same-origin", False),
    ],
)
def test_local_research_post_origin_guard(origin, host, port, fetch_site, allowed):
    assert is_same_origin_local_request(origin, host, port, fetch_site) is allowed


def test_numpy_logistic_and_platt_are_finite_and_converge():
    rng = np.random.default_rng(31)
    features = rng.normal(size=(12000, 8)).astype(np.float32)
    true_logits = features[:, 0] * 0.3 - features[:, 2] * 0.2 + 0.15
    true_probability = 1 / (1 + np.exp(-true_logits))
    labels = (rng.random(len(features)) < true_probability).astype(np.int8)

    model = _NumpyLogistic(max_iter=40).fit(features, labels)
    assert model.converged_
    assert model.n_iter_ <= 40
    raw = model.predict_proba(features[:1500])[:, 1]
    calibrator = _NumpyPlatt().fit(model.predict_proba(features[1500:8000])[:, 1], labels[1500:8000])
    calibrated = calibrator.predict_proba(np.log(raw / (1 - raw)).reshape(-1, 1))[:, 1]
    assert calibrator.model.converged_
    assert np.isfinite(calibrated).all()
    assert ((calibrated >= 0) & (calibrated <= 1)).all()


def test_model_quality_gate_blocks_scores_that_do_not_beat_constant_baseline():
    gate = _model_quality_gate(
        {"brier_score": 0.257, "log_loss": 0.707, "roc_auc": 0.48},
        {"test_brier_score": 0.251, "test_log_loss": 0.694},
    )
    assert gate["status"] == "BLOCKED"
    assert gate["verified"] is False
    assert "排序仅供研究诊断" in gate["reason"]

    passing = _model_quality_gate(
        {"brier_score": 0.22, "log_loss": 0.61, "roc_auc": 0.56},
        {"test_brier_score": 0.25, "test_log_loss": 0.69},
    )
    assert passing["status"] == "PASS"


def _write_synthetic_daily_data(root: Path, *, symbol_count: int = 220, date_count: int = 360) -> tuple[list[str], str, str]:
    dates = pd.bdate_range("2024-01-02", periods=date_count)
    symbols = [f"{index:06d}" for index in range(1, symbol_count + 1)]
    for offset, symbol in enumerate(symbols):
        rng = np.random.default_rng(offset + 900)
        returns = rng.normal(0.0003, 0.012, date_count)
        close = 15 * np.cumprod(1 + returns)
        open_price = close * (1 + rng.normal(0, 0.004, date_count))
        high = np.maximum(open_price, close) * (1 + rng.uniform(0.001, 0.012, date_count))
        low = np.minimum(open_price, close) * (1 - rng.uniform(0.001, 0.012, date_count))
        volume = rng.integers(100_000, 2_000_000, date_count)
        pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }).to_csv(root / f"{symbol}.csv", index=False)
    next_date = (dates[-1] + pd.offsets.BDay(1)).strftime("%Y-%m-%d")
    return symbols, dates[-1].strftime("%Y-%m-%d"), next_date


def test_end_to_end_research_runs_without_sklearn_and_is_blocked_unverified(tmp_path: Path, monkeypatch):
    symbols, as_of, target_date = _write_synthetic_daily_data(tmp_path)
    real_import = builtins.__import__

    def reject_optional_ml(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"sklearn", "lightgbm"}:
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_optional_ml)
    report = run_research(
        tmp_path,
        as_of=as_of,
        eligible_symbols=symbols,
        next_trade_date=target_date,
        snapshot_generation="synthetic-generation",
    )

    assert report["state"] == "complete"
    assert report["model_id"] == MODEL_VERSION == "next-close-up-validation-selected-v5"
    assert report["target_date"] == target_date
    assert report["models"]["pooled_logistic"]["available"] is True
    assert len(report["models"]["pooled_logistic"]["rolling_folds"]) == 3
    assert report["models"]["lightgbm"]["available"] is False
    assert report["historical_universe"]["status"] == "BLOCKED"
    assert report["historical_universe"]["verified"] is False
    assert report["price_adjustment"]["verified"] is False
    assert report["execution_validation"]["status"] == "BLOCKED"
    assert report["frozen_final_test"]["test_observed_up_rate"] is not None
    baseline = report["constant_up_probability_baseline"]
    assert baseline["probability_source"] == "train_plus_calibration_only"
    assert 0 < baseline["probability"] < 1
    assert baseline["test_brier_score"] is not None
    assert baseline["test_log_loss"] is not None
    current_model = report["current_scoring_model"]
    eval_window = report["models"]["pooled_logistic"]["evaluation_window"]
    assert current_model["training"]["signal_through"] < current_model["calibration_window"]["signal_from"]
    assert current_model["training"]["signal_through"] > eval_window["train"]["signal_through"]
    assert current_model["calibration_window"]["signal_through"] == current_model["latest_labeled_signal_date"]
    assert eval_window["train"]["signal_through"] < eval_window["calibration"]["signal_from"]
    assert eval_window["calibration"]["signal_through"] < eval_window["test"]["signal_from"]
    assert report["sample_counts"]["current_scoring_universe"] == len(symbols)
    assert len(report["predictions"]) == min(100, len(symbols))
    assert report["sample_counts"]["research_candidate_count"] == min(100, len(symbols))
    assert report["sample_counts"]["current_predictions"] == min(100, len(symbols))
    assert report["predictions"][0]["rank"] == 1
    assert report["predictions"][0]["name"] == "名称未知"
    assert report["predictions"][0]["as_of"] == as_of
    assert report["predictions"][0]["target_date"] == target_date
    probabilities = [item["research_probability"] for item in report["predictions"]]
    assert probabilities == sorted(probabilities, reverse=True)
    selection = report["validation_model_selection"]
    assert selection["selected_model"] == "pooled_logistic"
    assert selection["selection_data"] == "rolling_validation_only"
    assert selection["frozen_final_test_used_for_selection"] is False
    assert len(selection["candidate_models"]) == 1
    assert selection["candidate_models"][0]["model"] == "pooled_logistic"
    assert report["validation_integrity"]["verified"] is False
    assert report["model_quality"]["verified"] is False
    assert len(report["feature_names"]) == 20
    assert "不能证明当前评分模型的校准表现" in report["limitations"][0]
    assert report["resource_controls"]["max_samples"] > report["sample_counts"]["final_test"]
    json.dumps(report, ensure_ascii=False, allow_nan=False)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_probability_ui_formats_null_metrics_as_missing_values():
    script = REPO_ROOT / "static" / "js" / "probability.js"
    runner = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() { this.classList = {add() {}, remove() {}, toggle() {}}; this.children = []; }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement() { return new Element(); },
  createDocumentFragment() { return new Element(); },
};
const window = {clearTimeout() {}, setTimeout() { return 1; }};
const fetch = async () => ({ok: true, status: 200, json: async () => ({state: 'idle'})});
const source = fs.readFileSync(process.argv[1], 'utf8');
vm.runInNewContext(source, {document, window, fetch, console});
const format = window.QTradeProbabilityFormat;
assert.equal(format.number(null), '—');
assert.equal(format.number(undefined), '—');
assert.equal(format.percent(null), '—');
assert.equal(format.percent(''), '—');
assert.equal(format.number(0), '0.000');
"""
    subprocess.run([NODE, "-e", runner, str(script)], check=True, capture_output=True, text=True, timeout=15)


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_probability_ui_keeps_blocked_research_candidates_visible_and_warns():
    script = REPO_ROOT / "static" / "js" / "probability.js"
    runner = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor() {
    this.className = '';
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.children = [];
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
  }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
}
const elements = new Map();
const document = {
  getElementById(id) { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); },
  createElement() { return new Element(); },
  createDocumentFragment() { return new Element(); },
};
const report = {
  state: 'complete', model_id: 'next-close-up-pooled-logistic-v4-research-ranking',
  as_of: '2026-09-23', target_date: '2026-09-24', target_date_confirmed: true,
  snapshot_freshness: {status: 'EXPIRED', expired: true, reason: '目标交易日已结束。'},
  model_quality: {status: 'BLOCKED', reason: '未优于事前常数基线。'},
  historical_universe: {verified: false, reason: '历史股票池未验证。'},
  price_adjustment: {verified: false, reason: '复权口径未验证。'},
  execution_validation: {reason: '未验证成交。'},
  symbol_name_source: '名称未知',
  sample_counts: {source_kind: 'portal_qfq_sqlite', source_count: 2, current_scoring_universe: 2, research_candidate_count: 2},
  predictions: [
    {rank: 1, symbol: '600000', name: '名称未知', research_probability: 0.72},
    {rank: 2, symbol: '000001', name: '名称未知', research_probability: 0.61},
  ],
  frozen_final_test: {}, models: {}, validation_model_selection: {reason: '仅有 Logistic 可参与滚动验证。'},
};
const window = {clearTimeout() {}, setTimeout() { return 1; }};
const fetch = async () => ({ok: true, status: 200, json: async () => ({state: 'complete', report, report_saved: true})});
const source = fs.readFileSync(process.argv[1], 'utf8');
vm.runInNewContext(source, {document, window, fetch, console});
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  assert.match(elements.get('research-label').textContent, /BLOCKED.*低于基线/);
  assert.match(elements.get('prediction-note').textContent, /不宜据此交易/);
  assert.match(elements.get('status-message').textContent, /报告已保存在本机/);
  assert.equal(elements.get('freshness-banner').hidden, false);
  assert.match(elements.get('freshness-banner').textContent, /已过期/);
  assert.match(elements.get('prediction-context').textContent, /2026-09-23.*2026-09-24/);
  assert.match(elements.get('prediction-count').textContent, /研究候选 2 只/);
  const body = elements.get('prediction-rows');
  assert.equal(body.children.length, 1);
  const rows = body.children[0].children;
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].children.map(cell => cell.textContent), ['1', '600000', '名称未知', '72.0%']);
  assert.deepEqual(rows[1].children.map(cell => cell.textContent), ['2', '000001', '名称未知', '61.0%']);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([NODE, "-e", runner, str(script)], check=True, capture_output=True, text=True, timeout=15)
