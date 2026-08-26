from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

import qtrade_base_bridge
from qtrade_adapters.deepseek_harness.market_data import MainboardMarketDataAdapter
import server


def _write_stock_basic(path: Path) -> None:
    rows = [
        ("600519.SH", "Synthetic SH", "", "1", "stock", "SH", 0),
        ("000001.SZ", "Synthetic SZ", "", "1", "stock", "SZ", 0),
        ("002001.SZ", "Synthetic 002", "", "1", "stock", "SZ", 0),
        ("688001.SH", "Synthetic STAR", "", "1", "stock", "SH", 0),
        ("300001.SZ", "Synthetic GEM", "", "1", "stock", "SZ", 0),
        ("301001.SZ", "Synthetic GEM 301", "", "1", "stock", "SZ", 0),
        ("830001.BJ", "Synthetic BJ", "", "1", "stock", "BJ", 0),
        ("430001.BJ", "Synthetic BJ old", "", "1", "stock", "BJ", 0),
        ("000300.SH", "Synthetic index", "", "1", "index", "SH", 0),
        ("600300.SH", "Synthetic index SH", "", "1", "index", "SH", 0),
        ("510300.SH", "Synthetic ETF", "", "1", "ETF", "SH", 0),
        ("600002.SH", "Synthetic delisted", "2025-01-01", "0", "stock", "SH", 0),
        ("600003.SH", "*ST Synthetic", "", "1", "stock", "SH", 0),
        ("600004.SH", "Synthetic suspended", "", "1", "stock", "SH", 1),
        ("600005.SH", "Synthetic short history", "", "1", "stock", "SH", 0),
    ]
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE stock_basic (
                code TEXT PRIMARY KEY,
                name TEXT,
                industry TEXT,
                ipo_date TEXT,
                out_date TEXT,
                status TEXT,
                security_type TEXT,
                exchange TEXT,
                suspended INTEGER
            )
            """
        )
        connection.executemany(
            "INSERT INTO stock_basic VALUES (?, ?, '', '', ?, ?, ?, ?, ?)",
            rows,
        )


def _bar_rows(code: str, count: int, base: float) -> list[tuple]:
    rows = []
    for offset in range(count):
        day = f"2026-01-{offset + 1:02d}"
        close = base + offset
        rows.append((code, day, close - 0.5, close + 0.5, close - 1, close, 1000 + offset, "qfq"))
    return rows


def _write_bars(path: Path, rows: list[tuple]) -> None:
    grouped: dict[str, list[tuple]] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE bar_meta (
                code TEXT,
                adjust TEXT,
                start_date TEXT,
                end_date TEXT,
                rows INTEGER,
                updated_at TEXT
            );
            CREATE TABLE daily_bar (
                code TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                preclose REAL,
                volume REAL,
                amount REAL,
                turn REAL,
                pct_chg REAL,
                is_st INTEGER,
                adjust TEXT,
                source TEXT
            );
            """
        )
        connection.executemany(
            "INSERT INTO daily_bar "
            "(code, date, open, high, low, close, volume, adjust) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO bar_meta(code, adjust, start_date, end_date, rows, updated_at) "
            "VALUES (?, 'qfq', ?, ?, ?, 'synthetic')",
            [
                (code, group[0][1], group[-1][1], len(group))
                for code, group in grouped.items()
            ],
        )


def _make_fixture(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    base = tmp_path / "deepseek-harness-quant"
    cache = base / "data" / "cache"
    cache.mkdir(parents=True)
    _write_stock_basic(cache / "stock_basic.db")

    main_rows: list[tuple] = []
    for code, base_price in (
        ("600519.SH", 10),
        ("000001.SZ", 20),
        ("002001.SZ", 30),
        ("600003.SH", 40),
        ("600004.SH", 50),
        ("600300.SH", 60),
        ("510300.SH", 70),
    ):
        main_rows.extend(_bar_rows(code, 4, base_price))
    main_rows.extend(_bar_rows("600005.SH", 2, 80))
    main_path = cache / "bars.db"
    _write_bars(main_path, main_rows)

    incremental_path = cache / "bars_incr.db"
    incremental_rows = [
        ("600519.SH", "2026-01-03", 31.5, 32.5, 31, 33, 1100, "qfq"),
        ("600519.SH", "2026-01-04", 33.5, 34.5, 33, 34, 1101, "qfq"),
    ]
    _write_bars(incremental_path, incremental_rows)
    return base, {"stock": cache / "stock_basic.db", "bars": main_path, "incr": incremental_path}


def test_mainboard_classification_and_incremental_history(tmp_path: Path) -> None:
    base, _ = _make_fixture(tmp_path)
    adapter = MainboardMarketDataAdapter(base, min_history=3)

    assert adapter.scan() == ["000001", "002001", "600003", "600004", "600005", "600519"]
    assert adapter.scan() == adapter.scan()
    assert adapter.metadata("600003")["risk_warning"] == "ST"
    assert adapter.metadata("600004")["suspended"] is True
    assert adapter.metadata("600005")["computable"] is False
    assert adapter.metadata("688001") is None
    assert adapter.metadata("300001") is None
    assert adapter.metadata("830001") is None
    assert adapter.metadata("600002") is None

    summary = adapter.universe_summary({"600519", "600003", "600005"})
    assert summary == {
        "total": 6,
        "computable": 5,
        "tradable": 4,
        "candidate": 1,
        "excluded_by_reason": {
            "history_insufficient": 1,
            "risk_warning": 1,
            "suspended": 1,
        },
        "as_of": "2026-01-04",
        "source": "external_sqlite",
    }

    history = adapter.get_history("600519", count=10)
    assert list(history.index.strftime("%Y-%m-%d")) == [
        "2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"
    ]
    assert history["close"].tolist() == [10, 11, 33, 34]


def test_snapshot_is_reused_until_database_version_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base, paths = _make_fixture(tmp_path)
    adapter = MainboardMarketDataAdapter(base, min_history=3)
    original = adapter._metadata_rows
    calls = 0

    def counted_metadata():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(adapter, "_metadata_rows", counted_metadata)
    adapter.scan()
    adapter.scan()
    assert calls == 1

    stat = paths["stock"].stat()
    os.utime(paths["stock"], ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    adapter.scan()
    adapter.scan()
    assert calls == 2


def test_sqlite_access_is_read_only_and_fallback_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base, paths = _make_fixture(tmp_path)
    before_hash = hashlib.sha256(paths["stock"].read_bytes()).hexdigest()
    before_mtime = paths["stock"].stat().st_mtime_ns
    adapter = MainboardMarketDataAdapter(base, min_history=3)
    with adapter._connect(paths["stock"]) as connection:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden (value TEXT)")
    assert hashlib.sha256(paths["stock"].read_bytes()).hexdigest() == before_hash
    assert paths["stock"].stat().st_mtime_ns == before_mtime
    assert not list(paths["stock"].parent.glob("stock_basic.db-*"))

    missing_base = tmp_path / "missing-base"
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    frame = pd.DataFrame(
        {
            "open": [10.0] * 130,
            "high": [11.0] * 130,
            "low": [9.0] * 130,
            "close": [10.0] * 130,
            "volume": [1000] * 130,
        },
        index=pd.date_range("2026-01-01", periods=130),
    )
    frame.to_csv(csv_dir / "000001.csv")
    monkeypatch.setattr(qtrade_base_bridge, "base_dir", lambda: missing_base)
    service = server.DataService(str(csv_dir), live=False)
    assert service.mainboard_adapter.available is False
    assert "000001" in service.mainboard_symbols()
    assert service.universe_summary["source"] == "fallback"
    assert service.universe_summary["reason"] == "metadata_missing"


def test_corrupt_or_locked_database_is_an_unavailable_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "corrupt-base"
    cache = base / "data" / "cache"
    cache.mkdir(parents=True)
    (cache / "stock_basic.db").write_bytes(b"not a sqlite database")
    adapter = MainboardMarketDataAdapter(base)
    corrupt_summary = adapter.universe_summary()
    assert adapter.available is False
    assert corrupt_summary["source"] == "unavailable"
    assert corrupt_summary["reason"] == "metadata_read_error"

    bars_base, bars_paths = _make_fixture(tmp_path / "corrupt-bars")
    bars_paths["bars"].write_bytes(b"not a sqlite database")
    bars_summary = MainboardMarketDataAdapter(bars_base, min_history=3).universe_summary()
    assert bars_summary["source"] == "unavailable"
    assert bars_summary["reason"] == "bars_read_error"

    locked_base, _ = _make_fixture(tmp_path / "locked")
    locked = MainboardMarketDataAdapter(locked_base)

    def raise_locked(_path: Path):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(locked, "_connect", raise_locked)
    locked_summary = locked.universe_summary()
    assert locked.available is False
    assert locked_summary["source"] == "unavailable"
    assert locked_summary["reason"] in {"metadata_read_error", "bars_read_error"}


def test_dataservice_status_and_paper_scan_use_public_history_and_safe_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, _ = _make_fixture(tmp_path)
    monkeypatch.setattr(qtrade_base_bridge, "base_dir", lambda: base)
    service = server.DataService(str(tmp_path / "csv"), live=False)
    assert service.live_src is None
    assert service.load_history("600519")["close"].tolist() == [10, 11, 33, 34]
    assert service.is_tradable("600003") is False
    assert service.is_tradable("600004") is False
    assert service.is_tradable("600519") is True

    source = inspect.getsource(server.AutoPaperTrader._mainboard_scan)
    assert "service._load_csv" not in source
    assert "service.load_history" in source
    assert "fetch_kline" not in source

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(server, "SERVICE", service)
    trader = server.EngineAutoPaperTrader()
    status = trader.status(service)
    assert status["universe_size"] == 0
    assert status["universe_summary"]["source"] == "external_sqlite"
    assert set(status["universe_summary"]) == {
        "total", "computable", "tradable", "candidate", "excluded_by_reason", "as_of", "source"
    }


def test_ui_displays_distinct_dynamic_universe_counts() -> None:
    source = (Path(__file__).resolve().parents[1] / "static" / "js" / "auto_paper.js").read_text(
        encoding="utf-8"
    )
    assert "主板总池" in source
    assert "可计算" in source
    assert "候选" in source
    assert "summary.source" in source
    assert "summary.as_of" in source
