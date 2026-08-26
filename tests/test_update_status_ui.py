"""Deterministic contracts for the scheduled-update status API and UI monitor."""

from __future__ import annotations

import json
import csv
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener
from pathlib import Path
from types import SimpleNamespace

import pytest

import server


PROJECT_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _fallback_status() -> dict:
    return {
        "schema_version": 1,
        "trade_date": None,
        "state": "unknown",
        "reason": "status_unavailable",
        "started_at": None,
        "finished_at": None,
        "outputs": {"portal": False, "decision": False, "factors": False},
    }


def test_update_status_missing_file_is_stable(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "UPDATE_STATUS_PATH", tmp_path / "missing.json")

    assert server.read_update_status() == _fallback_status()


def test_update_status_corrupt_or_partial_file_is_safe(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    monkeypatch.setattr(server, "UPDATE_STATUS_PATH", path)
    path.write_text("{not-json", encoding="utf-8")
    assert server.read_update_status() == _fallback_status()

    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "state": ["success"],
                "trade_date": "not-a-date",
                "finished_at": "C:\\private\\command.log",
                "outputs": {"portal": True, "secret": True},
                "command": "python scripts/daily_update_1830.py",
            }
        ),
        encoding="utf-8",
    )
    result = server.read_update_status()
    assert result == _fallback_status()
    assert "private" not in json.dumps(result)
    assert "command" not in result


def test_update_status_filters_fields_and_preserves_safe_success(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    monkeypatch.setattr(server, "UPDATE_STATUS_PATH", path)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "trade_date": "2026-08-25",
                "state": "success",
                "reason": "completed",
                "started_at": "2026-08-25T18:30:00",
                "finished_at": "2026-08-25T18:35:00",
                "outputs": {"portal": True, "decision": True, "factors": False, "sync": True},
                "absolute_path": "C:\\private\\qtrade",
                "argv": ["secret"],
            }
        ),
        encoding="utf-8",
    )

    assert server.read_update_status() == {
        "schema_version": 1,
        "trade_date": "2026-08-25",
        "state": "success",
        "reason": "completed",
        "started_at": "2026-08-25T18:30:00",
        "finished_at": "2026-08-25T18:35:00",
        "outputs": {"portal": True, "decision": True, "factors": False},
    }


def test_update_status_handler_is_read_only_and_uses_safe_payload(monkeypatch, tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "running", "reason": "pipeline_running"}), encoding="utf-8")
    monkeypatch.setattr(server, "UPDATE_STATUS_PATH", path)
    captured = []
    handler = SimpleNamespace(_json=lambda payload: captured.append(payload))

    server.APIHandler._update_status(handler, {})

    assert captured == [
        {
            "schema_version": 1,
            "trade_date": None,
            "state": "running",
            "reason": "pipeline_running",
            "started_at": None,
            "finished_at": None,
            "outputs": {"portal": False, "decision": False, "factors": False},
        }
    ]


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_smoke_csv(data_dir: Path) -> None:
    data_dir.mkdir()
    with (data_dir / "000001.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for day in range(1, 81):
            close = 10.0 + day / 100
            writer.writerow(
                [
                    f"2024-01-{day:02d}" if day <= 31 else f"2024-02-{day - 31:02d}",
                    f"{close - 0.05:.2f}",
                    f"{close + 0.10:.2f}",
                    f"{close - 0.15:.2f}",
                    f"{close:.2f}",
                    100000 + day * 100,
                ]
            )


def _get_json(opener, port: int, path: str) -> dict:
    with opener.open(Request(f"http://127.0.0.1:{port}{path}"), timeout=3) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_update_status_http_smoke_handles_missing_success_and_corrupt(tmp_path):
    data_dir = tmp_path / "data"
    _write_smoke_csv(data_dir)
    port = _available_port()
    status_path = server.UPDATE_STATUS_PATH
    had_status = status_path.exists()
    old_status = status_path.read_bytes() if had_status else None
    env = os.environ.copy()
    env.update(
        {
            "QTRADE_NO_HARNESS": "1",
            "QTRADE_NO_AUTOUPDATE": "1",
            "QTRADE_BASE_DIR": str(tmp_path / "missing-base"),
        }
    )
    command = [
        sys.executable,
        str(PROJECT_ROOT / "server.py"),
        "--data-dir",
        str(data_dir),
        "--port",
        str(port),
        "--csv-only",
        "--no-browser",
        "--single-instance",
    ]
    options: dict[str, object] = {
        "cwd": str(PROJECT_ROOT),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True

    process = subprocess.Popen(command, **options)
    opener = build_opener(ProxyHandler({}))
    try:
        deadline = time.monotonic() + 8
        while True:
            try:
                assert _get_json(opener, port, "/api/health")["status"] == "ok"
                break
            except (AssertionError, URLError, OSError):
                if process.poll() is not None or time.monotonic() >= deadline:
                    output = process.stdout.read() if process.stdout is not None else ""
                    raise AssertionError(f"CSV service did not start: {output}")
                time.sleep(0.05)

        with opener.open(Request(f"http://127.0.0.1:{port}/"), timeout=3) as response:
            assert response.status == 200
            assert b"QTrade" in response.read()
        assert _get_json(opener, port, "/api/update/status") == _fallback_status()

        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "trade_date": "2026-08-25",
                    "state": "success",
                    "reason": "completed",
                    "started_at": "2026-08-25T18:30:00",
                    "finished_at": "2026-08-25T18:35:00",
                    "outputs": {"portal": True, "decision": True, "factors": True},
                }
            ),
            encoding="utf-8",
        )
        success = _get_json(opener, port, "/api/update/status")
        assert success["state"] == "success"
        assert success["outputs"] == {"portal": True, "decision": True, "factors": True}

        status_path.write_text("{broken", encoding="utf-8")
        assert _get_json(opener, port, "/api/update/status") == _fallback_status()
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if had_status:
            status_path.write_bytes(old_status)
        elif status_path.exists():
            status_path.unlink()


@pytest.mark.skipif(NODE is None, reason="Node.js is required for the browser-independent JS contract")
def test_update_monitor_and_route_contracts():
    script = r"""
const assert = require('node:assert/strict');
const { API, QTradeUpdate } = require('./static/js/api.js');

(async () => {
  global.fetch = async (url) => ({ ok: true, json: async () => ({ url }) });
  const apiStatus = await API.getUpdateStatus();
  assert.equal(apiStatus.url, '/api/update/status');
  assert.equal(QTradeUpdate.POLL_INTERVAL_MS, 30000);

  const first = {
    state: 'success', trade_date: '2026-08-25', finished_at: '2026-08-25T18:35:00'
  };
  const second = {
    state: 'success', trade_date: '2026-08-26', finished_at: '2026-08-26T18:35:00'
  };
  const statuses = [
    { state: 'running' }, first, first,
    { state: 'failure', reason: 'update_failed' },
    { state: 'skip', reason: 'weekend' }, second
  ];
  const successes = [];
  const monitor = QTradeUpdate.createMonitor({
    getStatus: async () => statuses.shift(),
    getPage: () => 'portal',
    onSuccess: (status, token, page) => successes.push({ status, token, page }),
  });
  for (let i = 0; i < 6; i += 1) await monitor.poll();
  assert.equal(successes.length, 2);
  assert.equal(successes[0].page, 'portal');
  assert.equal(monitor.getLastSuccessToken(), '2026-08-26|2026-08-26T18:35:00');
  assert.deepEqual(QTradeUpdate.updateTargets({ state: 'failure' }), []);
  assert.equal(
    QTradeUpdate.cacheBustedRoute('portal', '2026-08-25|2026-08-25T18:35:00'),
    '/portal?qtrade_update=2026-08-25%7C2026-08-25T18%3A35%3A00'
  );
  assert.equal(QTradeUpdate.cacheBustedRoute('not-allowlisted', 'secret'), null);

  let resolvePending;
  let requests = 0;
  const pending = new Promise((resolve) => { resolvePending = resolve; });
  const overlap = QTradeUpdate.createMonitor({
    getStatus: () => { requests += 1; return pending; },
  });
  const firstPoll = overlap.poll();
  const secondPoll = overlap.poll();
  assert.equal(requests, 1);
  resolvePending({ state: 'skip', reason: 'weekend' });
  await Promise.all([firstPoll, secondPoll]);

  let intervalArgs;
  let cleared = null;
  const timed = QTradeUpdate.createMonitor({
    getStatus: async () => ({ state: 'skip' }),
    setIntervalFn: (fn, ms) => { intervalArgs = { fn, ms }; return 17; },
    clearIntervalFn: (id) => { cleared = id; },
  });
  timed.start();
  timed.start();
  assert.equal(intervalArgs.ms, 30000);
  timed.stop();
  assert.equal(cleared, 17);
  console.log('update monitor contracts passed');
})().catch((error) => { console.error(error); process.exit(1); });
"""
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
