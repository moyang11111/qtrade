"""Deterministic end-to-end smoke test for the local CSV service."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER = PROJECT_ROOT / "server.py"
SYMBOL = "000001"


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_csv(data_dir: Path) -> None:
    data_dir.mkdir()
    path = data_dir / f"{SYMBOL}.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["date", "open", "high", "low", "close", "volume"])
        for day in range(1, 61):
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


def _terminate(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stdout.read() if process.stdout is not None else ""


def _start_server(tmp_path: Path, data_dir: Path, port: int) -> subprocess.Popen[str]:
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
        str(SERVER),
        "--data-dir",
        str(data_dir),
        "--port",
        str(port),
        "--csv-only",
        "--no-browser",
        "--single-instance",
    ]
    options: dict[str, object] = {
        "cwd": str(tmp_path),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(command, **options)


def _get_json(opener, port: int, path: str):
    request = Request(f"http://127.0.0.1:{port}{path}")
    with opener.open(request, timeout=3) as response:
        assert response.status == 200
        return json.loads(response.read().decode("utf-8"))


def test_csv_service_smoke_uses_only_local_data(tmp_path):
    """Start the real server and exercise static, health, symbol, and K-line APIs."""

    data_dir = tmp_path / "data"
    _write_csv(data_dir)
    port = _available_port()
    process = _start_server(tmp_path, data_dir, port)
    opener = build_opener(ProxyHandler({}))

    try:
        deadline = time.monotonic() + 15
        last_error = "service did not become ready"
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = _terminate(process)
                raise AssertionError(f"server exited before readiness:\n{output}")
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except (OSError, URLError) as exc:
                last_error = str(exc)
                time.sleep(0.1)
        else:
            output = _terminate(process)
            raise AssertionError(f"{last_error}\nserver output:\n{output}")

        with opener.open(f"http://127.0.0.1:{port}/", timeout=3) as response:
            assert response.status == 200
            assert b"QTrade" in response.read()

        health = _get_json(opener, port, "/api/health")
        assert health["status"] == "ok"
        assert health["mode"] == "csv"
        assert health["symbols"] >= 1

        symbols = _get_json(opener, port, "/api/symbols")
        assert SYMBOL in symbols

        klines = _get_json(opener, port, f"/api/kline/{SYMBOL}?limit=3")
        assert len(klines) == 3
        assert set(klines[-1]) == {"time", "open", "high", "low", "close", "volume"}
    finally:
        was_running = process.poll() is None
        output = _terminate(process)
        if not was_running and process.returncode not in (0, None):
            raise AssertionError(f"server exited with {process.returncode}:\n{output}")
