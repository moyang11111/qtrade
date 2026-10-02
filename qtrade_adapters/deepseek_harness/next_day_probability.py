"""Bounded, local research for next-session close direction.

This module is deliberately separate from QTrade's decision snapshots and
paper/live execution. It reads the committed local history snapshot (or CSV
fallback), saves only its research status, and never places orders or writes
into the market-data cache.
"""

from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
from typing import Callable
import urllib.parse

import numpy as np
import pandas as pd


MODEL_VERSION = "next-close-up-validation-selected-v5"
FEATURE_VERSION = "ohlcv-close-known-v2"
MAX_CSV_FILES = 5000
MAX_SAMPLE_ROWS = 2_200_000
MAX_ANALYSIS_DAYS = 730
MAX_CALENDAR_DAYS = 820
MIN_CALENDAR_DATES = 320
FEATURES = (
    "return_1d",
    "return_5d",
    "return_20d",
    "volatility_20d",
    "close_vs_sma5",
    "close_vs_sma20",
    "volume_vs_sma20",
    "intraday_range",
    "return_3d",
    "volatility_5d",
    "opening_gap",
    "candle_body",
    "close_location",
    "upper_wick",
    "lower_wick",
    "volume_change_1d",
    "volume_vs_sma5",
    "range_vs_sma20",
    "downside_volatility_20d",
    "return_volume_interaction",
)
_REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")


class ResearchError(Exception):
    """A safe, user-facing research error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def is_same_origin_local_request(
    origin: str,
    host: str,
    listen_port: int,
    fetch_site: str = "",
) -> bool:
    """Reject cross-site and DNS-rebinding hosts before starting CPU work."""
    try:
        parsed_origin = urllib.parse.urlsplit(origin.strip())
        parsed_host = urllib.parse.urlsplit("//" + host.strip())
        host_name = (parsed_host.hostname or "").lower()
        host_port = parsed_host.port or 80
        origin_port = parsed_origin.port or (80 if parsed_origin.scheme.lower() == "http" else None)
    except (AttributeError, TypeError, ValueError):
        return False
    return bool(
        origin
        and host
        and parsed_origin.scheme.lower() == "http"
        and parsed_origin.netloc.lower() == host.strip().lower()
        and parsed_origin.path in {"", "/"}
        and not parsed_origin.query
        and not parsed_origin.fragment
        and host_name in {"127.0.0.1", "localhost"}
        and host_port == int(listen_port)
        and origin_port == int(listen_port)
        and fetch_site.strip().lower() in {"", "same-origin"}
    )


def dataset_version(
    data_dir: str | Path,
    as_of: str,
    symbols: list[str],
    snapshot_generation: str = "",
    target_date: str | None = None,
    *,
    history_database: str | Path | None = None,
    history_database_sha256: str = "",
    history_metadata_sha256: str = "",
) -> str:
    """Fingerprint the exact snapshot inputs without reading full CSV contents."""
    root = Path(data_dir)
    digest = hashlib.sha256()
    digest.update(
        f"{FEATURE_VERSION}|{MODEL_VERSION}|{as_of}|{target_date or 'unconfirmed'}|{snapshot_generation}|".encode()
    )
    if history_database is not None:
        path = Path(history_database)
        try:
            stat = path.stat()
        except OSError:
            digest.update(
                f"sqlite:{path}:missing:{history_database_sha256}:{history_metadata_sha256}\n".encode()
            )
        else:
            digest.update(
                f"sqlite:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{history_database_sha256}:{history_metadata_sha256}\n".encode()
            )
        return digest.hexdigest()[:24]
    for symbol in sorted(set(symbols)):
        path = root / f"{symbol}.csv"
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{symbol}:missing\n".encode())
        else:
            digest.update(f"{symbol}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:24]


def temporal_windows(date_count: int) -> dict:
    """Create shared date-only rolling folds and one final frozen split.

    One date is purged at every train/calibration and calibration/test edge.
    All symbols on any date therefore remain in the same partition.
    """
    n = int(date_count)
    if n < MIN_CALENDAR_DATES:
        raise ResearchError("insufficient_dates", "有效交易日不足，至少需要 320 个交易日。")

    final_test_size = max(40, int(n * 0.15))
    final_cal_size = max(40, int(n * 0.15))
    final_test_start = n - final_test_size
    final_cal_start = final_test_start - final_cal_size
    if final_cal_start < 150:
        raise ResearchError("insufficient_dates", "训练、校准和最终测试区间不足。")

    fold_size = max(20, int(n * 0.08))
    rolling = []
    for offset in (3, 2, 1):
        test_end = final_cal_start - (offset - 1) * fold_size
        test_start = test_end - fold_size
        cal_end = test_start - 1
        cal_start = cal_end - fold_size
        train_end = cal_start - 1
        if train_end < 100 or cal_start < 20 or test_start >= test_end:
            continue
        rolling.append({
            "train": (0, train_end),
            "calibration": (cal_start, cal_end),
            "test": (test_start, test_end),
            "purge_dates": 2,
        })
    if len(rolling) < 2:
        raise ResearchError("insufficient_dates", "无法形成至少两段滚动验证区间。")

    final = {
        "train": (0, final_cal_start - 1),
        "calibration": (final_cal_start, final_test_start - 1),
        "test": (final_test_start, n),
        "purge_dates": 2,
    }
    return {"rolling": rolling, "final": final, "date_count": n}


def current_scoring_windows(date_count: int) -> dict:
    """Use the most recent known labels for today's separately fitted score.

    The as-of date itself has no label yet. The latest labeled signal date is
    therefore one session earlier; a purge date separates fitting from the
    recent calibration window.
    """
    n = int(date_count)
    if n < MIN_CALENDAR_DATES:
        raise ResearchError("insufficient_dates", "有效交易日不足，无法拟合当前研究模型。")
    calibration_size = max(40, int(n * 0.15))
    calibration_start = n - calibration_size - 1
    train_stop = calibration_start - 1
    calibration_stop = n - 1  # exclude the as-of date, whose next close is unknown
    if train_stop < 100 or calibration_start >= calibration_stop:
        raise ResearchError("insufficient_dates", "近期训练与校准区间不足。")
    return {
        "train": (0, train_stop),
        "calibration": (calibration_start, calibration_stop),
        "purge_dates": 1,
    }


def _date_texts(values) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d")


def _read_csv(path: Path) -> pd.DataFrame | None:
    try:
        frame = pd.read_csv(path, usecols=list(_REQUIRED_COLUMNS))
    except (OSError, ValueError, pd.errors.ParserError, UnicodeError):
        return None
    frame.columns = [str(column).strip().lower() for column in frame.columns]
    for column in _REQUIRED_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["date"] = _date_texts(frame["date"])
    frame = frame.dropna(subset=list(_REQUIRED_COLUMNS))
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame.loc[frame["close"] > 0]
    return frame


def _features(frame: pd.DataFrame) -> pd.DataFrame:
    """Build features from bars through t only; the target is attached later."""
    close = frame["close"].astype("float64")
    volume = frame["volume"].astype("float64").clip(lower=0)
    previous_return = close.pct_change(fill_method=None)
    result = pd.DataFrame(index=frame.index)
    result["return_1d"] = previous_return
    result["return_5d"] = close.pct_change(5, fill_method=None)
    result["return_20d"] = close.pct_change(20, fill_method=None)
    result["volatility_20d"] = previous_return.rolling(20, min_periods=20).std()
    result["close_vs_sma5"] = close / close.rolling(5, min_periods=5).mean() - 1
    result["close_vs_sma20"] = close / close.rolling(20, min_periods=20).mean() - 1
    volume_average = volume.rolling(20, min_periods=20).mean().replace(0, np.nan)
    result["volume_vs_sma20"] = (volume / volume_average - 1).clip(-10, 10)
    result["intraday_range"] = (
        (frame["high"].astype("float64") - frame["low"].astype("float64")) / close
    )
    opening = frame["open"].astype("float64")
    high = frame["high"].astype("float64")
    low = frame["low"].astype("float64")
    previous_close = close.shift(1)
    spread = (high - low).replace(0, np.nan)
    result["return_3d"] = close.pct_change(3, fill_method=None)
    result["volatility_5d"] = previous_return.rolling(5, min_periods=5).std()
    result["opening_gap"] = opening / previous_close - 1
    result["candle_body"] = close / opening - 1
    result["close_location"] = ((close - low) / spread).fillna(0.5)
    result["upper_wick"] = (high - np.maximum(opening, close)) / previous_close
    result["lower_wick"] = (np.minimum(opening, close) - low) / previous_close
    result["volume_change_1d"] = volume.pct_change(fill_method=None).clip(-1, 10)
    result["volume_vs_sma5"] = (volume / volume.rolling(5).mean().replace(0, np.nan) - 1).clip(-1, 10)
    mean_range = result["intraday_range"].rolling(20).mean()
    result["range_vs_sma20"] = (result["intraday_range"] / mean_range.replace(0, np.nan) - 1).fillna(0)
    result["downside_volatility_20d"] = previous_return.clip(upper=0).rolling(20).std()
    result["return_volume_interaction"] = previous_return * result["volume_vs_sma20"]
    return result.replace([np.inf, -np.inf], np.nan)


def _scan_calendar(files: list[Path], lower: str, as_of: str, progress: Callable | None) -> list[str]:
    dates: set[str] = set()
    for index, path in enumerate(files, 1):
        try:
            frame = pd.read_csv(path, usecols=["date"], dtype={"date": "string"})
        except (OSError, ValueError, pd.errors.ParserError, UnicodeError):
            continue
        parsed = _date_texts(frame["date"])
        dates.update(value for value in parsed.dropna().unique().tolist() if lower <= value <= as_of)
        if progress and index % 250 == 0:
            progress("calendar", index, len(files))
    return sorted(dates)


def _build_dataset(
    data_dir: str | Path,
    as_of: str,
    eligible_symbols: set[str],
    progress: Callable | None = None,
) -> dict:
    root = Path(data_dir)
    eligible_symbols = {str(symbol).zfill(6) for symbol in eligible_symbols if str(symbol).isdigit() and len(str(symbol)) <= 6}
    if not eligible_symbols:
        raise ResearchError("universe_missing", "当前已验证快照没有可用股票代码。")
    files = sorted(
        path for path in root.glob("*.csv")
        if path.is_file() and path.stem.isdigit() and len(path.stem) == 6
        and path.stem in eligible_symbols
    )
    if not files:
        raise ResearchError("data_missing", "未找到按六位代码命名的日线 CSV。")
    if len(files) > MAX_CSV_FILES:
        raise ResearchError("resource_limit", f"股票文件超过本地研究上限 {MAX_CSV_FILES}。")

    end = date.fromisoformat(as_of)
    analysis_start = (end - timedelta(days=MAX_ANALYSIS_DAYS)).isoformat()
    calendar_start = (end - timedelta(days=MAX_CALENDAR_DAYS)).isoformat()
    calendar = _scan_calendar(files, calendar_start, as_of, progress)
    dates = [value for value in calendar if analysis_start <= value <= as_of]
    if len(dates) < MIN_CALENDAR_DATES:
        raise ResearchError("insufficient_dates", "CSV 中可用的近期交易日不足 320 天。")
    index_by_date = {value: index for index, value in enumerate(calendar)}
    analysis_offset = index_by_date.get(dates[0], 0)
    analysis_end = index_by_date.get(as_of)
    if analysis_end is None:
        raise ResearchError("as_of_missing", "当前已验证交易日不在本地行情 CSV 中。")
    fold_dates = [value for value in calendar[analysis_offset:analysis_end + 1] if value >= analysis_start]
    local_index = {value: index for index, value in enumerate(fold_dates)}
    windows = temporal_windows(len(fold_dates))

    features_parts: list[np.ndarray] = []
    labels_parts: list[np.ndarray] = []
    date_parts: list[np.ndarray] = []
    latest_features: list[np.ndarray] = []
    latest_symbols: list[str] = []
    skipped_files = 0
    usable_symbols = 0
    row_count = 0
    for file_index, path in enumerate(files, 1):
        frame = _read_csv(path)
        if frame is None or frame.empty:
            skipped_files += 1
            continue
        frame = frame.loc[(frame["date"] >= calendar_start) & (frame["date"] <= as_of)].copy()
        if frame.empty:
            continue
        values = _features(frame)
        valid_x = values.notna().all(axis=1)
        date_idx = frame["date"].map(index_by_date)
        next_date = frame["date"].shift(-1)
        target_idx = next_date.map(index_by_date)
        next_close = frame["close"].shift(-1)
        consecutive = target_idx.eq(date_idx + 1)
        training_mask = (
            valid_x
            & consecutive
            & frame["date"].ge(analysis_start)
            & frame["date"].lt(as_of)
            & next_close.notna()
        )
        if training_mask.any():
            x = values.loc[training_mask, list(FEATURES)].to_numpy(dtype=np.float32, copy=True)
            signal_idx = frame.loc[training_mask, "date"].map(local_index).to_numpy(dtype=np.int32)
            y = (next_close.loc[training_mask].to_numpy(dtype=np.float64) >
                 frame.loc[training_mask, "close"].to_numpy(dtype=np.float64)).astype(np.int8)
            sample_count = len(y)
            row_count += sample_count
            if row_count > MAX_SAMPLE_ROWS:
                raise ResearchError(
                    "resource_limit",
                    f"样本超过单次研究上限 {MAX_SAMPLE_ROWS:,} 行；请减少历史范围后重试。",
                )
            features_parts.append(x)
            labels_parts.append(y)
            date_parts.append(signal_idx)
            usable_symbols += 1

        if path.stem in eligible_symbols:
            current_mask = frame["date"].eq(as_of) & valid_x
            if current_mask.any():
                latest_features.append(values.loc[current_mask, list(FEATURES)].to_numpy(dtype=np.float32, copy=True))
                latest_symbols.extend([path.stem] * int(current_mask.sum()))
        if progress and file_index % 100 == 0:
            progress("features", file_index, len(files))

    if not features_parts:
        raise ResearchError("samples_missing", "没有足够的完整日线样本可用于研究。")
    X = np.concatenate(features_parts, axis=0)
    y = np.concatenate(labels_parts, axis=0)
    sample_dates = np.concatenate(date_parts, axis=0)
    if len(X) != len(y) or len(y) < 20_000 or len(np.unique(y)) < 2:
        raise ResearchError("samples_insufficient", "可训练样本不足或目标标签只有一个类别。")
    if latest_features:
        current_X = np.concatenate(latest_features, axis=0)
    else:
        current_X = np.empty((0, len(FEATURES)), dtype=np.float32)
    return {
        "X": X,
        "y": y,
        "date_idx": sample_dates,
        "current_X": current_X,
        "current_symbols": latest_symbols,
        "dates": fold_dates,
        "row_count": len(y),
        "usable_symbols": usable_symbols,
        "csv_count": len(files),
        "skipped_files": skipped_files,
        "windows": windows,
        "source_kind": "csv",
        "source_count": len(files),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_portal_symbol_names(
    database: Path,
    manifest: dict,
    symbols: list[str],
    as_of: str,
) -> tuple[dict[str, str], str]:
    """Read only names bound to this portal generation; code placeholders stay unknown."""
    generation = manifest.get("generation")
    relative = manifest.get("metadata_path")
    expected_relative = f"generations/{generation}/metadata.json"
    expected_size = manifest.get("metadata_size")
    expected_hash = manifest.get("metadata_sha256")
    if (
        not isinstance(generation, str)
        or relative != expected_relative
        or not isinstance(expected_size, int)
        or expected_size <= 0
        or expected_size > 8 * 1024 * 1024
        or not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        return {}, "快照未提供可核验的公司名称"
    path = database.parent / "metadata.json"
    try:
        stat = path.stat()
        if stat.st_size != expected_size or _file_sha256(path) != expected_hash:
            return {}, "快照公司名称清单校验失败"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return {}, "快照公司名称清单不可读取"
    if (
        not isinstance(payload, dict)
        or payload.get("generation") != generation
        or payload.get("target_date") != as_of
        or payload.get("schema") != "portal_metadata.v2"
        or payload.get("schema_version") != 2
        or not isinstance(payload.get("items"), list)
    ):
        return {}, "快照公司名称清单与数据日期不匹配"
    expected = {str(symbol).zfill(6) for symbol in symbols}
    names: dict[str, str] = {}
    seen: set[str] = set()
    for item in payload["items"]:
        if not isinstance(item, dict):
            return {}, "快照公司名称清单结构无效"
        raw_code = str(item.get("code", "")).strip()
        code = raw_code.zfill(6)
        if code not in expected or code in seen:
            return {}, "快照公司名称清单股票范围不匹配"
        seen.add(code)
        name = item.get("name")
        if isinstance(name, str):
            name = name.strip()
            if name and name not in {code, raw_code}:
                names[code] = name[:80]
    if seen != expected:
        return {}, "快照公司名称清单股票数量不匹配"
    return names, "已提交快照名称清单" if names else "快照名称字段仅含代码占位"


def _snapshot_freshness(target_date: str | None, confirmed: bool, now: datetime | None = None) -> dict:
    current = now or datetime.now()
    try:
        target = date.fromisoformat(str(target_date))
    except (TypeError, ValueError):
        target = None
    if target is None or not confirmed:
        return {
            "status": "UNCONFIRMED",
            "expired": False,
            "reason": "目标交易日未能由交易日历确认。",
            "checked_at": current.isoformat(timespec="seconds"),
        }
    expired = target < current.date() or (
        target == current.date() and current.time() >= datetime_time(15, 30)
    )
    return {
        "status": "EXPIRED" if expired else "CURRENT",
        "expired": expired,
        "reason": "目标交易日已结束；下表是历史研究候选，不是当前下一交易日名单。" if expired else "目标交易日尚未结束。",
        "checked_at": current.isoformat(timespec="seconds"),
    }


def _build_dataset_from_portal(
    database: str | Path,
    manifest: dict,
    as_of: str,
    eligible_symbols: set[str],
    progress: Callable | None = None,
) -> dict:
    """Stream a verified, target-anchored qfq portal history into features."""
    from . import portal_refresh

    symbols = sorted({str(symbol).zfill(6) for symbol in eligible_symbols
                      if str(symbol).isdigit() and len(str(symbol)) <= 6})
    history_window = manifest.get("history_window")
    if (
        not symbols
        or manifest.get("schema_version") != portal_refresh.HISTORY_SCHEMA_VERSION
        or manifest.get("history_schema") != portal_refresh.HISTORY_DB_SCHEMA
        or manifest.get("target_date") != as_of
        or manifest.get("db_schema") != portal_refresh.HISTORY_DB_SCHEMA
        or not isinstance(manifest.get("generation"), str)
        or re.fullmatch(r"[0-9a-f]{64}", manifest.get("generation", "")) is None
        or manifest.get("db_path") != f"generations/{manifest.get('generation')}/bars_incr.db"
        or not isinstance(manifest.get("symbols"), list)
        or {str(symbol).zfill(6) for symbol in manifest.get("symbols", [])} != set(symbols)
        or history_window != portal_refresh.HISTORY_WINDOW
        or manifest.get("history_rows") != [history_window] * len(symbols)
    ):
        raise ResearchError("snapshot_invalid", "当前研究快照缺少完整的目标日或历史窗口校验信息。")

    path = Path(database)
    try:
        portal_refresh._canonical(path)
        stat = path.stat()
    except (OSError, ValueError):
        raise ResearchError("snapshot_invalid", "已提交研究快照数据库不可读取。") from None
    generation = manifest["generation"]
    if (
        path.name != "bars_incr.db"
        or path.parent.name != generation
        or path.parent.parent.name != "generations"
    ):
        raise ResearchError("snapshot_invalid", "研究数据库路径与快照 generation 清单不匹配。")
    expected_size = manifest.get("db_size")
    expected_hash = manifest.get("db_sha256")
    if (
        not isinstance(expected_size, int) or stat.st_size != expected_size
        or not isinstance(expected_hash, str) or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        raise ResearchError("snapshot_invalid", "研究快照数据库与已提交清单不匹配。")
    if _file_sha256(path) != expected_hash:
        raise ResearchError("snapshot_invalid", "研究快照数据库校验值与已提交清单不匹配。")
    symbol_names, symbol_name_source = _verified_portal_symbol_names(
        path, manifest, symbols, as_of,
    )

    end = date.fromisoformat(as_of)
    analysis_start = (end - timedelta(days=MAX_ANALYSIS_DAYS)).isoformat()
    calendar_start = (end - timedelta(days=MAX_CALENDAR_DAYS)).isoformat()
    connection = None
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=0.5)
        connection.execute("PRAGMA query_only=ON")
        objects = connection.execute(
            "SELECT type,name FROM sqlite_master WHERE type IN ('table','view','trigger') ORDER BY type,name"
        ).fetchall()
        if objects != [("table", "daily_bar")]:
            raise ResearchError("snapshot_invalid", "研究快照数据库结构不符合日线数据格式。")
        if connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall():
            raise ResearchError("snapshot_invalid", "研究快照数据库包含未识别的索引结构。")
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_bar'"
        ).fetchone()
        if table_sql is None or portal_refresh._normalize_sql(table_sql[0]) != portal_refresh._normalize_sql(portal_refresh._DB_CREATE_SQL):
            raise ResearchError("snapshot_invalid", "研究快照数据库表定义不符合预期。")
        columns = tuple(
            (str(row[1]).lower(), str(row[2]).upper(), int(row[3]), int(row[5]))
            for row in connection.execute("PRAGMA table_info(daily_bar)")
        )
        if columns != portal_refresh._DB_COLUMNS:
            raise ResearchError("snapshot_invalid", "研究快照数据库字段不符合预期。")

        expected_codes = {portal_refresh._market_code(symbol): symbol for symbol in symbols}
        groups = connection.execute(
            "SELECT code,adjust,COUNT(*) FROM daily_bar GROUP BY code,adjust ORDER BY code,adjust"
        ).fetchall()
        if len(groups) != len(symbols) or any(
            code not in expected_codes or adjust != "qfq" or row_count != history_window
            for code, adjust, row_count in groups
        ):
            raise ResearchError("snapshot_invalid", "研究快照股票范围、复权字段或历史条数与清单不一致。")

        calendar = [
            value for (raw_date,) in connection.execute(
                "SELECT DISTINCT date FROM daily_bar WHERE adjust='qfq' AND date<=? ORDER BY date",
                (as_of,),
            )
            if (value := portal_refresh._date_text(raw_date)) is not None
            and calendar_start <= value <= as_of
        ]
        dates = [value for value in calendar if analysis_start <= value <= as_of]
        if len(dates) < MIN_CALENDAR_DATES:
            raise ResearchError("insufficient_dates", "已验证 SQLite 快照中的近期交易日不足 320 天。")
        index_by_date = {value: index for index, value in enumerate(calendar)}
        if as_of not in index_by_date:
            raise ResearchError("as_of_missing", "快照目标日不在 SQLite 日线历史中。")
        fold_dates = [value for value in calendar if analysis_start <= value <= as_of]
        local_index = {value: index for index, value in enumerate(fold_dates)}
        windows = temporal_windows(len(fold_dates))

        features_parts: list[np.ndarray] = []
        labels_parts: list[np.ndarray] = []
        date_parts: list[np.ndarray] = []
        latest_features: list[np.ndarray] = []
        latest_symbols: list[str] = []
        row_count = 0
        usable_symbols = 0
        cursor = connection.execute(
            "SELECT code,date,open,high,low,close,volume,adjust FROM daily_bar ORDER BY code,date,adjust"
        )
        current_code = None
        rows: list[tuple] = []
        symbol_index = 0

        def consume_symbol(code: str, records: list[tuple]) -> None:
            nonlocal row_count, usable_symbols
            symbol = expected_codes[code]
            if len(records) != history_window or not records or records[-1][0] != as_of:
                raise ResearchError("snapshot_invalid", "快照中存在未以目标日结尾的股票历史。")
            frame = pd.DataFrame(records, columns=("date", "open", "high", "low", "close", "volume"))
            frame = frame.loc[(frame["date"] >= calendar_start) & (frame["date"] <= as_of)].copy()
            values = _features(frame)
            valid_x = values.notna().all(axis=1)
            date_idx = frame["date"].map(index_by_date)
            target_idx = frame["date"].shift(-1).map(index_by_date)
            next_close = frame["close"].shift(-1)
            training_mask = (
                valid_x & target_idx.eq(date_idx + 1)
                & frame["date"].ge(analysis_start) & frame["date"].lt(as_of)
                & next_close.notna()
            )
            if training_mask.any():
                x = values.loc[training_mask, list(FEATURES)].to_numpy(dtype=np.float32, copy=True)
                signal_idx = frame.loc[training_mask, "date"].map(local_index).to_numpy(dtype=np.int32)
                y = (next_close.loc[training_mask].to_numpy(dtype=np.float64) >
                     frame.loc[training_mask, "close"].to_numpy(dtype=np.float64)).astype(np.int8)
                row_count += len(y)
                if row_count > MAX_SAMPLE_ROWS:
                    raise ResearchError("resource_limit", f"样本超过单次研究上限 {MAX_SAMPLE_ROWS:,} 行。")
                features_parts.append(x)
                labels_parts.append(y)
                date_parts.append(signal_idx)
                usable_symbols += 1
            current_mask = frame["date"].eq(as_of) & valid_x
            if current_mask.any():
                latest_features.append(values.loc[current_mask, list(FEATURES)].to_numpy(dtype=np.float32, copy=True))
                latest_symbols.append(symbol)

        previous_date = None
        for code, raw_date, opn, high, low, close, volume, adjust in cursor:
            if code not in expected_codes or adjust != "qfq":
                raise ResearchError("snapshot_invalid", "SQLite 快照出现不属于当前已验证股票池的数据。")
            date_text = portal_refresh._date_text(raw_date)
            values = (opn, high, low, close, volume)
            if (
                date_text is None or date_text > as_of
                or any(not np.isfinite(float(value)) or float(value) <= 0 for value in values)
                or float(high) < max(float(opn), float(close))
                or float(low) > min(float(opn), float(close))
                or (current_code == code and previous_date is not None and date_text <= previous_date)
            ):
                raise ResearchError("snapshot_invalid", "SQLite 快照日线值或日期顺序校验失败。")
            if current_code is not None and code != current_code:
                consume_symbol(current_code, rows)
                symbol_index += 1
                rows = []
                if progress and symbol_index % 100 == 0:
                    progress("features", symbol_index, len(symbols))
            if code != current_code:
                if code not in expected_codes:
                    raise ResearchError("snapshot_invalid", "SQLite 快照包含未知股票代码。")
                current_code = code
                previous_date = None
            rows.append((date_text, float(opn), float(high), float(low), float(close), float(volume)))
            previous_date = date_text
        if current_code is not None:
            consume_symbol(current_code, rows)
            symbol_index += 1
        if symbol_index != len(symbols):
            raise ResearchError("snapshot_invalid", "SQLite 快照未包含清单中的全部股票。")
    except ResearchError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError, OverflowError):
        raise ResearchError("snapshot_invalid", "读取已验证 SQLite 历史时发生数据错误。") from None
    finally:
        if connection is not None:
            connection.close()

    if not features_parts:
        raise ResearchError("samples_missing", "没有足够的完整日线样本可用于研究。")
    X = np.concatenate(features_parts, axis=0)
    y = np.concatenate(labels_parts)
    sample_dates = np.concatenate(date_parts)
    if len(y) < 20_000 or len(np.unique(y)) < 2:
        raise ResearchError("samples_insufficient", "可训练样本不足或目标标签只有一个类别。")
    current_X = np.concatenate(latest_features, axis=0) if latest_features else np.empty((0, len(FEATURES)), dtype=np.float32)
    return {
        "X": X, "y": y, "date_idx": sample_dates, "current_X": current_X,
        "current_symbols": latest_symbols, "dates": fold_dates, "row_count": len(y),
        "usable_symbols": usable_symbols, "csv_count": 0, "skipped_files": 0,
        "source_kind": "portal_qfq_sqlite", "source_count": len(symbols), "windows": windows,
        "symbol_names": symbol_names, "symbol_name_source": symbol_name_source,
    }


def _mask_dates(values: np.ndarray, bounds: tuple[int, int]) -> np.ndarray:
    start, stop = bounds
    return (values >= start) & (values < stop)


class _NumpyLogistic:
    """Small dense binary logistic model with batched damped Newton fitting."""

    def __init__(self, *, c: float = 0.5, max_iter: int = 40, batch_size: int = 65536):
        self.c = float(c)
        self.max_iter = int(max_iter)
        self.batch_size = int(batch_size)
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None
        self.coef_: np.ndarray | None = None
        self.n_iter_: int = 0
        self.converged_: bool = False

    @staticmethod
    def _sigmoid(values: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))

    def _design(self, values: np.ndarray) -> np.ndarray:
        standardized = (values.astype(np.float64, copy=False) - self.mean_) / self.scale_
        return np.column_stack((np.ones(len(values), dtype=np.float64), standardized))

    def _loss(self, X: np.ndarray, y: np.ndarray, coefficients: np.ndarray) -> float:
        total = 0.0
        for start in range(0, len(y), self.batch_size):
            stop = min(start + self.batch_size, len(y))
            design = self._design(X[start:stop])
            logits = np.clip(design @ coefficients, -35.0, 35.0)
            total += float(np.sum(np.logaddexp(0.0, logits) - y[start:stop] * logits))
        regularized = coefficients[1:]
        return total + 0.5 * (1.0 / self.c) * float(regularized @ regularized)

    def _derivatives(self, X: np.ndarray, y: np.ndarray, coefficients: np.ndarray):
        width = X.shape[1] + 1
        gradient = np.zeros(width, dtype=np.float64)
        hessian = np.zeros((width, width), dtype=np.float64)
        for start in range(0, len(y), self.batch_size):
            stop = min(start + self.batch_size, len(y))
            design = self._design(X[start:stop])
            labels = y[start:stop].astype(np.float64, copy=False)
            logits = np.clip(design @ coefficients, -35.0, 35.0)
            probability = self._sigmoid(logits)
            residual = probability - labels
            weights = probability * (1.0 - probability)
            gradient += design.T @ residual
            hessian += design.T @ (design * weights[:, None])
        alpha = 1.0 / self.c
        gradient[1:] += alpha * coefficients[1:]
        hessian[1:, 1:] += np.eye(width - 1, dtype=np.float64) * alpha
        hessian.flat[::width + 1] += 1e-10
        return gradient, hessian

    def fit(self, X, y):
        features = np.asarray(X, dtype=np.float32)
        labels = np.asarray(y, dtype=np.float64)
        if features.ndim != 2 or len(features) != len(labels) or not len(labels):
            raise ValueError("invalid logistic training shape")
        if (
            not np.isfinite(features).all()
            or not np.isfinite(labels).all()
            or not np.isin(labels, (0.0, 1.0)).all()
            or len(np.unique(labels)) < 2
        ):
            raise ValueError("logistic training data are not finite or lack a class")
        self.mean_ = np.mean(features, axis=0, dtype=np.float64)
        self.scale_ = np.std(features, axis=0, dtype=np.float64)
        self.scale_[~np.isfinite(self.scale_) | (self.scale_ < 1e-8)] = 1.0
        coefficients = np.zeros(features.shape[1] + 1, dtype=np.float64)
        for iteration in range(1, self.max_iter + 1):
            gradient, hessian = self._derivatives(features, labels, coefficients)
            try:
                direction = np.linalg.solve(hessian, gradient)
            except np.linalg.LinAlgError as error:
                raise ResearchError("model_fit_failed", "Logistic 模型优化矩阵不可解。") from error
            if not np.isfinite(direction).all():
                raise ResearchError("model_fit_failed", "Logistic 模型优化出现非有限数值。")
            gradient_norm = float(np.max(np.abs(gradient)))
            if gradient_norm < 1e-5:
                self.converged_ = True
                self.n_iter_ = iteration
                break
            old_loss = self._loss(features, labels, coefficients)
            directional = float(gradient @ direction)
            step = 1.0
            accepted = False
            for _ in range(18):
                candidate = coefficients - step * direction
                if self._loss(features, labels, candidate) <= old_loss - 1e-4 * step * directional:
                    coefficients = candidate
                    accepted = True
                    break
                step *= 0.5
            if not accepted:
                if gradient_norm < 1e-3:
                    self.converged_ = True
                    self.n_iter_ = iteration
                    break
                raise ResearchError("model_fit_failed", "Logistic 模型未能稳定收敛。")
            self.n_iter_ = iteration
        if not self.converged_:
            gradient, _ = self._derivatives(features, labels, coefficients)
            self.converged_ = float(np.max(np.abs(gradient))) < 1e-3
        if not self.converged_:
            raise ResearchError("model_fit_failed", "Logistic 模型达到迭代上限仍未收敛。")
        self.coef_ = coefficients
        return self

    def predict_proba(self, X) -> np.ndarray:
        if self.coef_ is None:
            raise ValueError("logistic model is not fitted")
        features = np.asarray(X, dtype=np.float32)
        result = np.empty(len(features), dtype=np.float64)
        for start in range(0, len(features), self.batch_size):
            stop = min(start + self.batch_size, len(features))
            logits = self._design(features[start:stop]) @ self.coef_
            result[start:stop] = self._sigmoid(logits)
        return np.column_stack((1.0 - result, result))


class _NumpyPlatt:
    """Sigmoid calibration trained only on the designated calibration dates."""

    def __init__(self):
        self.model = _NumpyLogistic(c=1.0, max_iter=50, batch_size=65536)

    def fit(self, raw_probability, labels):
        raw = np.clip(np.asarray(raw_probability, dtype=np.float32), 1e-6, 1 - 1e-6)
        logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
        self.model.fit(logits, labels)
        return self

    def predict_proba(self, raw_logits):
        return self.model.predict_proba(raw_logits)


class _NativeLightGBM:
    """Bounded native API; does not require the optional sklearn adapter."""

    def fit(self, X, y):
        import lightgbm as lgb
        self.booster = lgb.train({
            "objective": "binary", "metric": "binary_logloss",
            "num_leaves": 15, "max_depth": 5, "learning_rate": 0.05,
            "min_data_in_leaf": 500, "lambda_l2": 5.0,
            "num_threads": 2, "verbosity": -1, "seed": 2026,
            "deterministic": True, "force_col_wise": True,
        }, lgb.Dataset(np.asarray(X, dtype=np.float32), label=y), num_boost_round=140)
        return self

    def predict_proba(self, X):
        p = self.booster.predict(np.asarray(X, dtype=np.float32), num_threads=2)
        return np.column_stack((1.0 - p, p))


def _fit_model(X_train, y_train, model_kind: str = "logistic"):
    if model_kind == "lightgbm":
        return _NativeLightGBM().fit(X_train, y_train)
    if model_kind != "logistic":
        raise ValueError("unsupported model")
    return _NumpyLogistic(c=0.5, max_iter=40).fit(X_train, y_train)


def _calibrate(model, X_cal, y_cal):
    if len(y_cal) < 5000 or len(np.unique(y_cal)) < 2:
        raise ResearchError("calibration_insufficient", "独立校准窗口样本或标签类别不足。")
    raw = np.clip(model.predict_proba(X_cal)[:, 1], 1e-6, 1 - 1e-6)
    return _NumpyPlatt().fit(raw, y_cal)


def _calibrated_probability(model, calibrator, X) -> np.ndarray:
    raw = np.clip(model.predict_proba(X)[:, 1], 1e-6, 1 - 1e-6)
    logits = np.log(raw / (1 - raw)).reshape(-1, 1)
    return np.clip(calibrator.predict_proba(logits)[:, 1], 0.0, 1.0)


def _auc_and_average_precision(labels: np.ndarray, probability: np.ndarray) -> tuple[float | None, float | None]:
    if len(np.unique(labels)) < 2:
        return None, None
    positives = int(labels.sum())
    negatives = len(labels) - positives
    order = np.argsort(probability, kind="mergesort")
    sorted_probability = probability[order]
    sorted_labels = labels[order]
    unique, starts, counts = np.unique(
        sorted_probability, return_index=True, return_counts=True,
    )
    del unique
    group_positives = np.add.reduceat(sorted_labels, starts)
    mean_ranks = starts.astype(np.float64) + (counts.astype(np.float64) + 1.0) / 2.0
    auc = (
        float((mean_ranks @ group_positives - positives * (positives + 1) / 2.0) / (positives * negatives))
    )

    descending = np.argsort(-probability, kind="mergesort")
    descending_probability = probability[descending]
    descending_labels = labels[descending]
    group_starts = np.r_[0, np.flatnonzero(np.diff(descending_probability) != 0) + 1]
    group_positive = np.add.reduceat(descending_labels, group_starts)
    group_sizes = np.diff(np.r_[group_starts, len(labels)])
    cumulative_positive = np.cumsum(group_positive)
    cumulative_total = np.cumsum(group_sizes)
    average_precision = float(np.sum((cumulative_positive / cumulative_total) * group_positive) / positives)
    return auc, average_precision


def _metrics(y_true: np.ndarray, probability: np.ndarray, dates: np.ndarray) -> dict:
    probability = np.clip(np.asarray(probability, dtype=np.float64), 1e-6, 1 - 1e-6)
    labels = np.asarray(y_true, dtype=np.int8)
    unique_dates = np.unique(dates)
    auc, pr_auc = _auc_and_average_precision(labels, probability)
    brier = float(np.mean((probability - labels) ** 2)) if len(labels) else None
    loss = (
        float(-np.mean(labels * np.log(probability) + (1 - labels) * np.log(1 - probability)))
        if len(labels) else None
    )
    rows = []
    bin_idx = np.minimum((probability * 10).astype(int), 9)
    for index in range(10):
        selected = bin_idx == index
        count = int(selected.sum())
        if not count:
            continue
        rows.append({
            "lower": index / 10,
            "upper": (index + 1) / 10,
            "mean_predicted": float(probability[selected].mean()),
            "observed_up_rate": float(labels[selected].mean()),
            "sample_count": count,
        })
    top_k_hits = []
    top_k_values = []
    for trade_date in unique_dates:
        selected = np.flatnonzero(dates == trade_date)
        if not len(selected):
            continue
        k = min(10, len(selected))
        leaders = selected[np.argsort(probability[selected])[-k:]]
        top_k_hits.append(float(labels[leaders].mean()))
        top_k_values.append(k)
    return {
        "sample_count": len(labels),
        "date_count": len(unique_dates),
        "test_observed_up_rate": float(labels.mean()) if len(labels) else None,
        "brier_score": brier,
        "log_loss": loss,
        "roc_auc": auc,
        "pr_auc": pr_auc,
        "roc_auc_meaning": "区分上涨/下跌样本的排序能力，不代表概率可信度。",
        "pr_auc_meaning": "正类排序指标，不代表概率可信度。",
        "top_k": 10,
        "top_k_mean_daily_up_rate": float(np.mean(top_k_hits)) if top_k_hits else None,
        "top_k_selected_per_day": float(np.mean(top_k_values)) if top_k_values else None,
        "reliability_bins": rows,
    }


def _fit_eval_split(data: dict, bounds: dict, model_kind: str):
    dates = data["date_idx"]
    train_mask = _mask_dates(dates, bounds["train"])
    cal_mask = _mask_dates(dates, bounds["calibration"])
    test_mask = _mask_dates(dates, bounds["test"])
    X, y = data["X"], data["y"]
    X_train, y_train = X[train_mask], y[train_mask]
    X_cal, y_cal = X[cal_mask], y[cal_mask]
    X_test, y_test = X[test_mask], y[test_mask]
    if len(y_train) < 20_000 or len(y_test) < 5_000 or len(np.unique(y_train)) < 2:
        raise ResearchError("samples_insufficient", "训练区或测试区样本不足。")
    model = _fit_model(X_train, y_train, model_kind)
    calibrator = _calibrate(model, X_cal, y_cal)
    probability = _calibrated_probability(model, calibrator, X_test)
    prior_up_rate = float(np.concatenate((y_train, y_cal)).mean())
    baseline_probability = np.full(len(y_test), prior_up_rate, dtype=np.float64)
    baseline_scores = _metrics(y_test, baseline_probability, dates[test_mask])
    return {
        "model": model,
        "calibrator": calibrator,
        "test_mask": test_mask,
        "test_y": y_test,
        "test_probability": probability,
        "test_dates": dates[test_mask],
        "metrics": _metrics(y_test, probability, dates[test_mask]),
        "constant_baseline": {
            "probability": prior_up_rate,
            "probability_source": "train_plus_calibration_only",
            "test_brier_score": baseline_scores["brier_score"],
            "test_log_loss": baseline_scores["log_loss"],
        },
        "baseline_probability": baseline_probability,
        "train_count": len(y_train),
        "calibration_count": len(y_cal),
    }


def _window_report(data: dict, bounds: dict, names: tuple[str, ...]) -> dict:
    report = {}
    for name in names:
        mask = _mask_dates(data["date_idx"], bounds[name])
        indices = data["date_idx"][mask]
        report[name] = {
            "signal_from": data["dates"][int(indices.min())] if len(indices) else None,
            "signal_through": data["dates"][int(indices.max())] if len(indices) else None,
            "sample_count": int(mask.sum()),
        }
    return report


def _fit_current_scoring_model(data: dict, model_kind: str = "logistic") -> dict:
    bounds = current_scoring_windows(data["windows"]["date_count"])
    train_mask = _mask_dates(data["date_idx"], bounds["train"])
    cal_mask = _mask_dates(data["date_idx"], bounds["calibration"])
    y_train, y_cal = data["y"][train_mask], data["y"][cal_mask]
    if len(y_train) < 20_000 or len(y_cal) < 5_000 or len(np.unique(y_train)) < 2:
        raise ResearchError("samples_insufficient", "当前模型近期训练区或校准区样本不足。")
    model = _fit_model(data["X"][train_mask], y_train, model_kind)
    calibrator = _calibrate(model, data["X"][cal_mask], y_cal)
    probability = _calibrated_probability(model, calibrator, data["current_X"])
    return {
        "probability": probability,
        "training_sample_count": len(y_train),
        "calibration_sample_count": len(y_cal),
        "purged_boundary_dates": bounds["purge_dates"],
        "window": _window_report(data, bounds, ("train", "calibration")),
    }


def _model_quality_gate(model_metrics: dict, baseline_metrics: dict) -> dict:
    """Require the frozen model to beat the constant prior on proper scores."""
    model_brier = model_metrics.get("brier_score")
    baseline_brier = baseline_metrics.get("test_brier_score")
    model_log_loss = model_metrics.get("log_loss")
    baseline_log_loss = baseline_metrics.get("test_log_loss")
    values = (model_brier, baseline_brier, model_log_loss, baseline_log_loss)
    available = all(isinstance(value, (int, float)) and np.isfinite(value) for value in values)
    beats_baseline = bool(
        available
        and model_brier < baseline_brier
        and model_log_loss < baseline_log_loss
        and isinstance(model_metrics.get("roc_auc"), (int, float))
        and np.isfinite(model_metrics["roc_auc"])
        and model_metrics["roc_auc"] > 0.5
    )
    return {
        "status": "PASS" if beats_baseline else "BLOCKED",
        "verified": beats_baseline,
        "criteria": "历史测试的 Brier 与 Log loss 均低于事前常数基线，且 AUC 高于 0.5",
        "model_brier": model_brier,
        "baseline_brier": baseline_brier,
        "model_log_loss": model_log_loss,
        "baseline_log_loss": baseline_log_loss,
        "roc_auc": model_metrics.get("roc_auc"),
        "reason": (
            "模型通过历史测试的事前基线门槛。"
            if beats_baseline
            else "历史测试未同时达到概率误差与排序门槛；质量门槛仍为 BLOCKED，下方排序仅供研究诊断。"
        ),
    }


def _rolling_candidate(data: dict, kind: str, progress=None, offset: int = 0) -> dict:
    labels, probabilities, dates, baseline_probabilities, folds = [], [], [], [], []
    total = 2 * len(data["windows"]["rolling"])
    for number, bounds in enumerate(data["windows"]["rolling"], 1):
        result = _fit_eval_split(data, bounds, kind)
        labels.append(result["test_y"])
        probabilities.append(result["test_probability"])
        dates.append(result["test_dates"])
        baseline_probabilities.append(result["baseline_probability"])
        folds.append({"fold": number, "train_count": result["train_count"],
                      "calibration_count": result["calibration_count"], "metrics": result["metrics"]})
        if progress:
            progress("rolling_validation", offset + number, total)
    y, d = np.concatenate(labels), np.concatenate(dates)
    return {
        "available": True, "validation_eligible": True,
        "calibration": "独立时间段上的 NumPy Platt sigmoid 校准",
        "rolling_folds": folds,
        "rolling_oos": _metrics(y, np.concatenate(probabilities), d),
        "rolling_constant_baseline": _metrics(y, np.concatenate(baseline_probabilities), d),
    }


def _select_validation_candidate(models: dict) -> str:
    """Select only on rolling validation proper scores, never on test results."""
    eligible = []
    for key, item in models.items():
        metric = item.get("rolling_oos") or {}
        values = (metric.get("log_loss"), metric.get("brier_score"), metric.get("roc_auc"))
        if item.get("validation_eligible") and all(
            isinstance(value, (int, float)) and np.isfinite(value) for value in values
        ):
            eligible.append((values[0], values[1], -values[2], key))
    if not eligible:
        raise ResearchError("model_fit_failed", "没有完成滚动验证的可用研究模型。")
    return min(eligible)[-1]


def run_research(
    data_dir: str | Path,
    *,
    as_of: str,
    eligible_symbols: list[str],
    next_trade_date: str | None = None,
    snapshot_generation: str = "",
    data_version: str | None = None,
    history_database: str | Path | None = None,
    history_manifest: dict | None = None,
    progress: Callable | None = None,
) -> dict:
    """Run rolling development and one untouched final test, then score today."""
    try:
        from threadpoolctl import threadpool_limits
        threadpool_context = threadpool_limits(limits=2)
        threadpool_limit_applied = True
    except ImportError:
        from contextlib import nullcontext
        threadpool_context = nullcontext()
        threadpool_limit_applied = False

    token = data_version or dataset_version(
        data_dir, as_of, eligible_symbols, snapshot_generation, next_trade_date,
        history_database=history_database,
        history_database_sha256=str((history_manifest or {}).get("db_sha256") or ""),
        history_metadata_sha256=str((history_manifest or {}).get("metadata_sha256") or ""),
    )
    if progress:
        progress("loading", 0, 1)
    if history_database is not None:
        data = _build_dataset_from_portal(
            history_database, history_manifest or {}, as_of, set(eligible_symbols), progress,
        )
        data_source = "QTrade 已提交快照 SQLite（日线 qfq；历史股票池及时点仍未核实）"
        sample_source_label = "snapshot_symbols"
        symbol_name_source = data.get("symbol_name_source", "快照未提供可核验的公司名称")
    else:
        data = _build_dataset(data_dir, as_of, set(eligible_symbols), progress)
        data_source = "QTrade 本地日线 CSV（截止已验证快照日）"
        sample_source_label = "csv_files"
        symbol_name_source = "本地 CSV 未提供公司名称"
    if progress:
        progress("rolling_validation", 0, 2 * len(data["windows"]["rolling"]))

    models = {}
    with threadpool_context:
        models["pooled_logistic"] = _rolling_candidate(data, "logistic", progress)
        try:
            models["lightgbm"] = _rolling_candidate(
                data, "lightgbm", progress, len(data["windows"]["rolling"]),
            )
        except Exception as error:
            models["lightgbm"] = {
                "available": False, "validation_eligible": False,
                "reason": f"LightGBM 未完成滚动验证（{type(error).__name__}）；继续使用已完成验证的候选。",
            }
        selected_key = _select_validation_candidate(models)
        selected_kind = "logistic" if selected_key == "pooled_logistic" else "lightgbm"
        # The choice is fixed before accessing final-test labels/metrics.
        final = _fit_eval_split(data, data["windows"]["final"], selected_kind)
        models[selected_key]["final_test"] = final["metrics"]
        models[selected_key]["evaluation_window"] = _window_report(
            data, data["windows"]["final"], ("train", "calibration", "test"),
        )
        models[selected_key]["version"] = MODEL_VERSION
        if progress:
            progress("frozen_test", 1, 1)
            progress("current_scoring", 0, 1)
        current_model = _fit_current_scoring_model(data, selected_kind)
        current_probability = current_model["probability"]
        if progress:
            progress("current_scoring", 1, 1)
        rolling_oos = models[selected_key]["rolling_oos"]
        rolling_baseline = models[selected_key]["rolling_constant_baseline"]
        frozen_test_metrics = final["metrics"]
        model_quality = _model_quality_gate(frozen_test_metrics, final["constant_baseline"])
        model_quality["verified"] = False
        model_quality["reason"] += " 该历史测试区此前已查看；此次仅作历史诊断，仍需新数据前瞻验证。"

    predictions = []
    for symbol, probability in zip(data["current_symbols"], current_probability):
        predictions.append({
            "symbol": symbol,
            "name": data.get("symbol_names", {}).get(symbol, "名称未知"),
            "research_probability": float(probability),
        })
    predictions.sort(key=lambda item: (-item["research_probability"], item["symbol"]))
    for rank, item in enumerate(predictions, 1):
        item["rank"] = rank
        item["as_of"] = as_of
        item["target_date"] = next_trade_date or "下一交易日（交易日历未确认）"
    target_freshness = _snapshot_freshness(next_trade_date, bool(next_trade_date))
    candidate_scores = []
    for key, item in models.items():
        if not item.get("validation_eligible"):
            continue
        metric = item["rolling_oos"]
        baseline = item["rolling_constant_baseline"]
        candidate_scores.append({
            "model": key, "eligible": True,
            "rolling_oos_brier_score": metric["brier_score"],
            "rolling_oos_log_loss": metric["log_loss"],
            "rolling_oos_roc_auc": metric["roc_auc"],
            "rolling_top_k_up_rate": metric["top_k_mean_daily_up_rate"],
            "beats_rolling_constant_baseline": bool(
                metric["brier_score"] < baseline["brier_score"]
                and metric["log_loss"] < baseline["log_loss"]
                and metric["roc_auc"] is not None and metric["roc_auc"] > 0.5
            ),
        })
    validation_selection = {
        "method": "固定候选及参数；在相同的三段滚动验证上，先比较 Log loss，再比较 Brier 与 AUC。模型选定后仅评估该模型的历史测试区。",
        "selection_data": "rolling_validation_only",
        "selected_model": selected_key,
        "candidate_models": candidate_scores,
        "constant_baseline": {
            "rolling_oos_brier_score": rolling_baseline["brier_score"],
            "rolling_oos_log_loss": rolling_baseline["log_loss"],
        },
        "frozen_final_test_used_for_selection": False,
        "reason": f"按滚动验证 Log loss 选用 {selected_key}，同分时比较 Brier 与 AUC；不据本轮历史测试结果选择或调参。",
    }
    if progress:
        progress("complete", 1, 1)

    return {
        "state": "complete",
        "research_status": "研究中/未验证",
        "as_of": as_of,
        "snapshot_generation": snapshot_generation,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": next_trade_date or "下一交易日（交易日历未确认）",
        "target_date_confirmed": bool(next_trade_date),
        "snapshot_freshness": target_freshness,
        "target_definition": "y=1[close(t+1)>close(t)]；t+1 为当前快照中相邻日线推导的下一共同交易日。",
        "model_id": MODEL_VERSION,
        "selected_prediction_model": selected_key,
        "current_scoring_model": {
            "model_id": MODEL_VERSION,
            "model_kind": selected_key,
            "calibration": "最近独立时间段上的 NumPy Platt sigmoid 校准",
            "training": current_model["window"]["train"],
            "calibration_window": current_model["window"]["calibration"],
            "training_sample_count": current_model["training_sample_count"],
            "calibration_sample_count": current_model["calibration_sample_count"],
            "purged_boundary_dates": current_model["purged_boundary_dates"],
            "latest_labeled_signal_date": data["dates"][-2],
        },
        "feature_version": FEATURE_VERSION,
        "feature_names": list(FEATURES),
        "data_version": token,
        "data_source": data_source,
        "symbol_name_source": symbol_name_source,
        "resource_controls": {
            "max_csv_files": MAX_CSV_FILES,
            "max_samples": MAX_SAMPLE_ROWS,
            "max_primary_logistic_iterations": 40,
            "lightgbm_rounds": 140,
            "lightgbm_threads": 2,
            "candidate_model_limit": 2,
            "max_calibration_logistic_iterations": 50,
            "blas_thread_cap": 2 if threadpool_limit_applied else None,
            "blas_thread_cap_applied": threadpool_limit_applied,
        },
        "sample_counts": {
            "csv_files": data["csv_count"],
            "source_kind": data["source_kind"],
            "source_count": data["source_count"],
            "source_count_label": sample_source_label,
            "usable_symbols": data["usable_symbols"],
            "training": final["train_count"],
            "calibration": final["calibration_count"],
            "final_test": final["metrics"]["sample_count"],
            "current_scoring_universe": len(predictions),
            "research_candidate_count": min(100, len(predictions)),
            "current_predictions": min(100, len(predictions)),
            "prediction_rows": min(100, len(predictions)),
            "skipped_csv_files": data["skipped_files"],
            "purged_boundary_dates_per_split": 2,
        },
        "validation_integrity": {
            "status": "REUSED_HISTORICAL_TEST", "verified": False,
            "reason": "历史测试区曾用于旧版本报告；本轮成绩不是新的独立验证，仍需后续新数据前瞻检验。",
            "selection_uses_test": False,
        },
        "models": models,
        "validation_model_selection": validation_selection,
        "frozen_final_test": frozen_test_metrics,
        "constant_up_probability_baseline": final["constant_baseline"],
        "model_quality": model_quality,
        "historical_universe": {
            "verified": False,
            "status": "BLOCKED",
            "reason": "快照仅提供当前可计算股票及有限日线历史，未提供逐日历史股票池、退市和历史 ST 状态；样本存在幸存者偏差风险。",
        },
        "price_adjustment": {
            "verified": False,
            "reason": ("快照标注为 qfq，但未提供公司行动可得时间及历史复权版本元数据，无法核实每个信号日的点时可得性。"
                       if history_database is not None else "CSV 未携带复权口径或公司行动可得时间元数据，价格序列 PIT 性无法核实。"),
        },
        "execution_validation": {
            "status": "BLOCKED",
            "reason": "本研究只判断收盘方向；未建入场/出场、停牌/涨跌停成交规则和费用后的净收益。",
        },
        "predictions": predictions[:100],
        "limitations": [
            "冻结最终测试只独立评估训练截止较早的评估模型；其后当前评分模型使用新近已成熟的标签重新训练与校准，近期校准可能包含冻结测试日期，因此冻结测试指标不能证明当前评分模型的校准表现。",
            "历史股票池、退市状态与复权口径未核实，样本外指标仍可能有偏差，不能称为交易胜率或可靠收益概率。",
            "AUC 和 PR-AUC 仅衡量排序，不能单独证明概率可信。",
            "此功能不会生成交易指令，也不会触发自动下单或模拟盘。",
            model_quality["reason"],
        ],
    }


def _atomic_write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        from .runtime import _replace_with_retry
        _replace_with_retry(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class ResearchJobManager:
    """One explicit, bounded background job; no work is started on app boot."""

    def __init__(self, storage_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._status: dict = {"state": "idle", "message": "等待手动启动研究。"}
        self._job: threading.Thread | None = None
        self._version: str | None = None
        self._storage_path: Path | None = None
        self._persistence_warning: str | None = None
        if storage_path is not None:
            self.configure_storage_path(storage_path)

    def configure_storage_path(self, path: str | Path) -> None:
        """Load and continue the latest saved report without starting research."""
        with self._lock:
            self._storage_path = Path(path).expanduser().resolve()
            try:
                payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
                self._persistence_warning = "上次研究报告文件不可读取。"
                return
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                self._persistence_warning = "上次研究报告格式不受支持。"
                return
            state = payload.get("state")
            if state not in {"complete", "blocked", "failed", "queued", "running"}:
                return
            self._status = {key: value for key, value in payload.items() if key != "schema_version"}
            if state in {"queued", "running"}:
                self._status.update({
                    "state": "failed",
                    "message": "上次研究未完成；请重新启动研究。",
                    "error_code": "interrupted",
                })
            report = self._status.get("report")
            if isinstance(report, dict):
                self._version = report.get("data_version")
            else:
                self._version = self._status.get("data_version")
            if state in {"queued", "running"}:
                self._persist_locked()

    def _persist_locked(self) -> None:
        if self._storage_path is None:
            return
        try:
            _atomic_write_status(
                self._storage_path,
                {"schema_version": 1, **self._status},
            )
            self._persistence_warning = None
        except (OSError, TypeError, ValueError):
            self._persistence_warning = "最新研究状态未能保存到本地文件。"

    def _save_freshness_if_changed(self, report: dict) -> None:
        with self._lock:
            existing = self._status.get("report")
            old_freshness = existing.get("snapshot_freshness", {}) if isinstance(existing, dict) else {}
            new_freshness = report.get("snapshot_freshness", {})
            if (
                old_freshness.get("status") != new_freshness.get("status")
                or old_freshness.get("expired") != new_freshness.get("expired")
                or (existing or {}).get("target_date") != report.get("target_date")
            ):
                self._status = {**self._status, "report": report}
                self._persist_locked()

    def snapshot(
        self,
        as_of: str | None = None,
        next_trade_date: str | None = None,
        *,
        snapshot_generation: str | None = None,
        data_version: str | None = None,
    ) -> dict:
        with self._lock:
            status = dict(self._status)
            persistence_warning = self._persistence_warning
        if as_of:
            status["current_as_of"] = as_of
            report = status.get("report", {})
            stale_date = report.get("as_of") != as_of
            stale_generation = (
                snapshot_generation is not None
                and report.get("snapshot_generation", "") != snapshot_generation
            )
            stale_version = data_version is not None and report.get("data_version") != data_version
            expected_target = next_trade_date or "下一交易日（交易日历未确认）"
            stale_target = report.get("target_date") != expected_target
            if status.get("state") == "complete" and (stale_date or stale_generation or stale_version or stale_target):
                return {
                    "state": "stale",
                    "message": "数据快照已更新；请手动重新运行研究。",
                    "current_as_of": as_of,
                    "report_as_of": report.get("as_of"),
                }
        if next_trade_date and status.get("report"):
            report = dict(status["report"])
            report["target_date"] = next_trade_date
            report["target_date_confirmed"] = True
            report["snapshot_freshness"] = _snapshot_freshness(next_trade_date, True)
            status["report"] = report
            self._save_freshness_if_changed(report)
        elif status.get("report"):
            report = dict(status["report"])
            report["snapshot_freshness"] = _snapshot_freshness(
                report.get("target_date"), bool(report.get("target_date_confirmed")),
            )
            status["report"] = report
            self._save_freshness_if_changed(report)
        if persistence_warning:
            status["persistence_warning"] = persistence_warning
        status["report_saved"] = bool(
            self._storage_path is not None and self._storage_path.is_file()
        )
        return status

    def start(
        self,
        *,
        data_dir: str | Path,
        as_of: str,
        eligible_symbols: list[str],
        next_trade_date: str | None = None,
        snapshot_generation: str = "",
        history_database: str | Path | None = None,
        history_manifest: dict | None = None,
    ) -> dict:
        version = dataset_version(
            data_dir, as_of, eligible_symbols, snapshot_generation, next_trade_date,
            history_database=history_database,
            history_database_sha256=str((history_manifest or {}).get("db_sha256") or ""),
            history_metadata_sha256=str((history_manifest or {}).get("metadata_sha256") or ""),
        )
        with self._lock:
            if self._job is not None and self._job.is_alive():
                return dict(self._status)
            if self._status.get("state") == "complete" and self._version == version:
                return dict(self._status)
            self._version = version
            self._status = {
                "state": "queued",
                "message": "研究任务已排队，只读取本地日线缓存。",
                "as_of": as_of,
                "data_version": version,
                "progress": {"step": "queued", "completed": 0, "total": 1},
            }
            self._persist_locked()
            self._job = threading.Thread(
                target=self._run,
                kwargs={
                    "data_dir": str(data_dir),
                    "as_of": as_of,
                    "eligible_symbols": list(eligible_symbols),
                    "next_trade_date": next_trade_date,
                    "snapshot_generation": snapshot_generation,
                    "history_database": str(history_database) if history_database is not None else None,
                    "history_manifest": dict(history_manifest or {}),
                    "version": version,
                },
                daemon=True,
                name="qtrade-next-day-research",
            )
            self._job.start()
            return dict(self._status)

    def _update_progress(self, step: str, completed: int, total: int) -> None:
        with self._lock:
            self._status = {
                **self._status,
                "state": "running",
                "progress": {"step": step, "completed": int(completed), "total": int(total)},
            }
            self._persist_locked()

    def _run(
        self,
        *,
        data_dir,
        as_of,
        eligible_symbols,
        next_trade_date,
        snapshot_generation,
        history_database,
        history_manifest,
        version,
    ) -> None:
        try:
            report = run_research(
                data_dir,
                as_of=as_of,
                eligible_symbols=eligible_symbols,
                next_trade_date=next_trade_date,
                snapshot_generation=snapshot_generation,
                data_version=version,
                history_database=history_database,
                history_manifest=history_manifest,
                progress=self._update_progress,
            )
            if dataset_version(
                data_dir, as_of, eligible_symbols, snapshot_generation, next_trade_date,
                history_database=history_database,
                history_database_sha256=str((history_manifest or {}).get("db_sha256") or ""),
                history_metadata_sha256=str((history_manifest or {}).get("metadata_sha256") or ""),
            ) != version:
                raise ResearchError("data_changed", "研究期间行情或快照发生变化；请重新启动研究。")
            payload = {"state": "complete", "message": "研究完成；结果仍标记为未验证。", "report": report}
        except ResearchError as error:
            payload = {
                "state": "blocked",
                "message": error.message,
                "error_code": error.code,
                "as_of": as_of,
                "data_version": version,
            }
        except Exception:
            payload = {
                "state": "failed",
                "message": "研究计算失败；本地数据或模型依赖可能不完整。",
                "error_code": "research_failed",
                "as_of": as_of,
                "data_version": version,
            }
        with self._lock:
            self._status = payload
            self._persist_locked()


__all__ = [
    "FEATURES",
    "FEATURE_VERSION",
    "MAX_ANALYSIS_DAYS",
    "MAX_SAMPLE_ROWS",
    "MODEL_VERSION",
    "ResearchError",
    "ResearchJobManager",
    "current_scoring_windows",
    "dataset_version",
    "is_same_origin_local_request",
    "run_research",
    "temporal_windows",
]
