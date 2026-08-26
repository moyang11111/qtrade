# -*- coding: utf-8 -*-
"""Run the QTrade daily data pipeline once for a confirmed trading day.

The script owns the trading-calendar lookup, idempotency/status record and
fail-fast pipeline. The adapter runtime only decides when to invoke it.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

try:
    from qtrade_adapters.deepseek_harness import freshness
except ModuleNotFoundError:  # pragma: no cover - isolated script packaging probe
    freshness = None

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "third_party" / "deepseek-harness-quant"
DECK_ENV = "QTRADE_DECK_DIR"
PY = sys.executable or "python"
DEFAULT_LOG = ROOT / "logs" / "daily_update_1830.log"
LOG = DEFAULT_LOG
DEFAULT_STATUS = ROOT / "logs" / "daily_update_1830.status.json"
DEFAULT_LOCK = ROOT / "logs" / "daily_update_1830.lock"
DEFAULT_CALENDAR_CACHE = ROOT / "logs" / "cache" / "trading_calendar.json"
STATUS_SCHEMA_VERSION = 1
STEP_TIMEOUT_SECONDS = 7200


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


def resolve_deck_dir(cli_value=None):
    """按 CLI > 环境变量 > 项目默认路径选择底座目录。"""
    if cli_value:
        return Path(cli_value).expanduser()
    env_value = os.environ.get(DECK_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return Path(DECK)


def _date_value(value: object) -> datetime.date | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _parse_date(value: str) -> datetime.date:
    parsed = _date_value(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return parsed


def _normalise_dates(values: Iterable[object]) -> set[datetime.date]:
    dates = {_date_value(value) for value in values}
    return {value for value in dates if value is not None}


def fetch_trade_dates() -> set[datetime.date]:
    """Fetch the exchange calendar through optional akshare integration."""

    import akshare  # type: ignore[import-not-found]

    table = akshare.tool_trade_date_hist_sina()
    if hasattr(table, "columns"):
        for column in ("trade_date", "date", "日期"):
            if column in table.columns:
                return _normalise_dates(table[column].tolist())
        raise ValueError("akshare calendar has no trade_date column")
    return _normalise_dates(table)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def load_calendar_cache(path: Path) -> set[datetime.date] | None:
    """Return cached dates, or ``None`` when the cache is absent/invalid."""

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        values = payload.get("trade_dates")
        if not isinstance(values, list):
            return None
        dates = _normalise_dates(values)
        return dates or None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def save_calendar_cache(path: Path, dates: Iterable[datetime.date], *, now=None) -> None:
    """Atomically cache a normalized exchange calendar."""

    current = now or datetime.datetime.now()
    payload = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "updated_at": current.isoformat(timespec="seconds"),
        "trade_dates": sorted(value.isoformat() for value in _normalise_dates(dates)),
    }
    _atomic_write_json(Path(path), payload)


def _path_for_log(default: Path, explicit=None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    if LOG == DEFAULT_LOG:
        return default
    if default == DEFAULT_CALENDAR_CACHE:
        return LOG.parent / "cache" / default.name
    return LOG.with_name(default.name)


def _status_path(explicit=None) -> Path:
    return _path_for_log(DEFAULT_STATUS, explicit)


def _lock_path(explicit=None) -> Path:
    return _path_for_log(DEFAULT_LOCK, explicit)


def _calendar_cache_path(explicit=None) -> Path:
    return _path_for_log(DEFAULT_CALENDAR_CACHE, explicit)


def read_status(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def write_status(path: Path, **values: object) -> None:
    """Atomically publish a structured update status record."""

    required = {
        "schema_version": STATUS_SCHEMA_VERSION,
        "trade_date": None,
        "state": "failure",
        "reason": "unknown",
        "started_at": None,
        "finished_at": None,
        "step": None,
        "steps": [],
        "outputs": {"portal": False, "decision": False, "factors": False, "sync": False},
        "freshness": {},
        "output_meta": {},
        "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
    }
    required.update(values)
    _atomic_write_json(Path(path), required)


def resolve_trading_day(
    target: datetime.date,
    *,
    cache_path: Path,
    calendar_loader: Callable[[], Iterable[object]] | None = None,
) -> tuple[bool | None, str]:
    """Resolve a day from cache/API, returning ``None`` when unconfirmed."""

    if target.weekday() >= 5:
        return False, "weekend"

    cached = load_calendar_cache(cache_path)
    if cached:
        if target in cached:
            return True, "calendar_cache"
        if target <= max(cached):
            return False, "calendar_cache_closed"

    loader = fetch_trade_dates if calendar_loader is None else calendar_loader
    try:
        dates = _normalise_dates(loader())
        if not dates:
            raise ValueError("empty trading calendar")
        try:
            save_calendar_cache(cache_path, dates)
        except OSError as error:
            log(f"WARN: 交易日历缓存写入失败：{error}")
        return (target in dates, "calendar_api" if target in dates else "calendar_api_closed")
    except Exception as error:  # noqa: BLE001 - fail closed at process boundary
        return None, f"calendar_unavailable: {error}"


@contextmanager
def update_lock(path: Path):
    """Acquire a process lock with atomic create and always release it."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    acquired = False
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        acquired = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = None
            stream.write(f"pid={os.getpid()}\n")
        yield True
    except FileExistsError:
        yield False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if acquired:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


@dataclass
class CommandResult:
    ok: bool
    returncode: int | None = None
    error: str | None = None


def _execute(cmd: list[object], dry: bool, deck_dir: Path | None = None) -> CommandResult:
    command_text = " ".join(str(value) for value in cmd)
    log("RUN: " + command_text)
    if dry:
        return CommandResult(True, 0)
    try:
        result = subprocess.run(cmd, cwd=str(deck_dir or DECK), timeout=STEP_TIMEOUT_SECONDS)
    except Exception as error:  # noqa: BLE001 - record and stop the pipeline
        log(f"FAIL: 步骤执行异常：{command_text}：{error}")
        return CommandResult(False, None, str(error))
    returncode = getattr(result, "returncode", 0)
    if returncode != 0:
        log(f"FAIL: 步骤返回 {returncode}: {command_text}")
        return CommandResult(False, returncode, f"returncode={returncode}")
    return CommandResult(True, 0)


def run(cmd, dry, deck_dir=None):
    """Compatibility helper retained for existing local integrations/tests."""

    return _execute(list(cmd), dry, deck_dir).ok


def _step(name: str, group: str, command: list[object]) -> dict[str, object]:
    return {
        "name": name,
        "group": group,
        "state": "pending",
        "returncode": None,
        "command": [str(value) for value in command],
    }


def _pipeline(
    *,
    deck: Path,
    target: datetime.date,
    dry: bool,
    status_path: Path,
    started_at: str,
    steps: list[dict[str, object]],
    outputs: dict[str, bool],
    before_artifacts: freshness.ArtifactSnapshot | None = None,
    portal_baseline: dict[str, object] | None = None,
    freshness_state: dict[str, dict[str, object]] | None = None,
) -> bool:
    freshness_state = {} if freshness_state is None else freshness_state

    def publish(step: str | None = None) -> None:
        write_status(
            status_path,
            trade_date=target.isoformat(),
            state="running",
            reason="pipeline_running",
            started_at=started_at,
            finished_at=None,
            step=step,
            steps=steps,
            outputs=outputs,
            freshness=freshness_state,
            output_meta=freshness_state,
        )

    def execute(name: str, group: str, command: list[object]) -> bool:
        entry = _step(name, group, command)
        steps.append(entry)
        result = _execute(command, dry, deck)
        entry["state"] = "planned" if dry else ("success" if result.ok else "failure")
        entry["returncode"] = result.returncode
        if result.error:
            entry["error"] = result.error
        publish(name)
        if not result.ok:
            log("FAIL: 已停止后续步骤")
            return False
        return True

    def verify(name: str, group: str, result: dict[str, object]) -> bool:
        safe_result = {key: value for key, value in result.items() if not key.startswith("_")}
        freshness_state[group] = safe_result
        entry = _step(name, group, [])
        entry["state"] = "success" if result.get("verified") else "failure"
        entry["reason"] = result.get("reason", "verification_failed")
        steps.append(entry)
        publish(name)
        if not result.get("verified"):
            log(f"FAIL: {group} 新鲜度校验失败：{result.get('reason', 'unknown')}")
            return False
        return True

    common = [PY, "-X", "utf8"]
    if not execute("portal", "portal", common + [deck / "scripts" / "auto_update_daily.py"]):
        return False
    if not dry:
        portal_result = freshness.verify_portal(deck, target, baseline=portal_baseline)
        if not verify("portal_freshness", "portal", portal_result):
            return False
    else:
        freshness_state["portal"] = {"verified": False, "as_of": None, "source": "dry_run", "reason": "dry_run"}
    outputs["portal"] = not dry
    if not execute("factors", "factors", common + [deck / "scripts" / "build_factor_pool_engine.py"]):
        return False
    if not dry:
        factor_result = freshness.verify_factors(
            deck,
            target,
            before=before_artifacts,
        )
        if not verify("factor_freshness", "factors", factor_result):
            return False
    else:
        freshness_state["factors"] = {"verified": False, "as_of": None, "source": "dry_run", "reason": "dry_run"}
    outputs["factors"] = not dry
    scan = common + [deck / "factors" / "opportunities" / "scan.py", "--pitch"]
    if not execute("decision_scan", "decision", scan):
        return False

    if dry:
        pool = deck / "logs" / f"opp_pool_{target:%Y%m%d}.json"
    else:
        pool_result = freshness.verify_decision(
            deck,
            target,
            before=before_artifacts,
            require_pitch=False,
        )
        if not verify("decision_pool_freshness", "decision", pool_result):
            return False
        pool = pool_result.get("_pool_path")
        if not isinstance(pool, Path):
            log("FAIL: 当前交易日机会池路径不可确认")
            return False
    pitch = common + [deck / "factors" / "opportunities" / "pitch_v2.py", "--pool", pool]
    if not execute("decision_pitch_v2", "decision", pitch):
        return False
    if not dry:
        decision_result = freshness.verify_decision(
            deck,
            target,
            before=before_artifacts,
            require_pitch=True,
        )
        if not verify("decision_freshness", "decision", decision_result):
            return False
    else:
        freshness_state["decision"] = {"verified": False, "as_of": None, "source": "dry_run", "reason": "dry_run"}
    outputs["decision"] = not dry

    sync_target = None
    sync_before = None
    if not dry:
        sync_target = freshness.resolve_sync_destination(deck)
        if sync_target is not None and sync_target.exists():
            sync_before = freshness.capture_artifacts(sync_target)
    if not execute("sync", "sync", common + [deck / "scripts" / "sync_data_to_roaming.py"]):
        return False
    if not dry:
        sync_result = freshness.verify_sync(
            sync_target,
            target,
            before=sync_before,
        )
        if not verify("sync_freshness", "sync", sync_result):
            return False
    else:
        freshness_state["sync"] = {"verified": False, "as_of": None, "source": "dry_run", "reason": "dry_run"}
    outputs["sync"] = not dry
    return True


def main(
    argv=None,
    *,
    today=None,
    calendar_loader: Callable[[], Iterable[object]] | None = None,
    cache_path: Path | None = None,
    status_file: Path | None = None,
    lock_path: Path | None = None,
    now_fn: Callable[[], datetime.datetime] | None = None,
):
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", "--dry-run", dest="dry", action="store_true", help="只检查日期并列出命令")
    parser.add_argument("--force", action="store_true", help="手动越过交易日历与同日成功检查")
    parser.add_argument("--date", type=_parse_date, help="目标日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--deck-dir", help=f"底座目录（优先于 {DECK_ENV}，默认项目内 third_party 路径）")
    parser.add_argument("--status-file", help="结构化状态 JSON 路径")
    args = parser.parse_args(argv)

    target = args.date or _date_value(today) or datetime.date.today()
    status = _status_path(status_file or args.status_file)
    lock = _lock_path(lock_path)
    cache = _calendar_cache_path(cache_path)
    previous = read_status(status)
    if (
        not args.force
        and previous
        and previous.get("trade_date") == target.isoformat()
        and previous.get("state") == "success"
    ):
        log(f"今天 {target} 已成功更新，跳过重复执行")
        return 0

    if target.weekday() >= 5 and not args.force:
        log(f"今天 {target} 是周末，跳过自动更新")
        write_status(
            status,
            trade_date=target.isoformat(),
            state="skip",
            reason="weekend",
            started_at=None,
            finished_at=None,
            step=None,
        )
        return 0

    deck = resolve_deck_dir(args.deck_dir)
    if not deck.exists():
        log(f"FAIL: 底座不存在 {deck}")
        write_status(
            status,
            trade_date=target.isoformat(),
            state="failure",
            reason="deck_missing",
            started_at=None,
            finished_at=None,
            step="resolve_deck",
        )
        return 1

    with update_lock(lock) as acquired:
        if not acquired:
            log(f"FAIL: 更新锁已被占用 {lock}")
            if not previous or previous.get("state") != "running":
                write_status(
                    status,
                    trade_date=target.isoformat(),
                    state="skip",
                    reason="lock_busy",
                    started_at=None,
                    finished_at=None,
                    step=None,
                )
            return 1

        now_provider = now_fn or datetime.datetime.now
        started_at = now_provider().isoformat(timespec="seconds")
        steps: list[dict[str, object]] = []
        outputs = {"portal": False, "decision": False, "factors": False, "sync": False}
        freshness_state: dict[str, dict[str, object]] = {}
        write_status(
            status,
            trade_date=target.isoformat(),
            state="running",
            reason="forced" if args.force else "started",
            started_at=started_at,
            finished_at=None,
            step=None,
            steps=steps,
            outputs=outputs,
            freshness=freshness_state,
            output_meta=freshness_state,
        )

        if args.force:
            calendar_state, calendar_reason = True, "forced"
        else:
            calendar_state, calendar_reason = resolve_trading_day(
                target,
                cache_path=cache,
                calendar_loader=calendar_loader,
            )
        if calendar_state is None:
            log(f"FAIL: 无法确认交易日，安全停止：{calendar_reason}")
            write_status(
                status,
                trade_date=target.isoformat(),
                state="failure",
                reason=calendar_reason,
                started_at=started_at,
                finished_at=now_provider().isoformat(timespec="seconds"),
                step="calendar",
                steps=steps,
                outputs=outputs,
            )
            return 1
        if not calendar_state:
            log(f"今天 {target} 非交易日，跳过自动更新（{calendar_reason}）")
            write_status(
                status,
                trade_date=target.isoformat(),
                state="skip",
                reason=calendar_reason,
                started_at=started_at,
                finished_at=now_provider().isoformat(timespec="seconds"),
                step="calendar",
                steps=steps,
                outputs=outputs,
            )
            return 0

        log(f"交易日更新开始：{target}（{'DRY' if args.dry else 'REAL'}）")
        if not args.dry and freshness is None:
            log("FAIL: 新鲜度校验模块不可用，无法安全执行更新")
            write_status(
                status,
                trade_date=target.isoformat(),
                state="failure",
                reason="freshness_adapter_missing",
                started_at=started_at,
                finished_at=now_provider().isoformat(timespec="seconds"),
                step="freshness",
                steps=steps,
                outputs=outputs,
            )
            return 1
        before_artifacts = freshness.capture_artifacts(deck) if freshness is not None else None
        portal_baseline = freshness.capture_portal_baseline(deck) if freshness is not None else None
        try:
            completed = _pipeline(
                deck=deck,
                target=target,
                dry=args.dry,
                status_path=status,
                started_at=started_at,
                steps=steps,
                outputs=outputs,
                before_artifacts=before_artifacts,
                portal_baseline=portal_baseline,
                freshness_state=freshness_state,
            )
        except Exception as error:  # noqa: BLE001 - status must record unexpected failures
            log(f"FAIL: 更新流程异常：{error}")
            completed = False
            if not steps or steps[-1].get("state") != "failure":
                steps.append({"name": "pipeline", "state": "failure", "error": str(error)})

        if not completed:
            write_status(
                status,
                trade_date=target.isoformat(),
                state="failure",
                reason="step_failed",
                started_at=started_at,
                finished_at=now_provider().isoformat(timespec="seconds"),
                step=steps[-1].get("name") if steps else "pipeline",
                steps=steps,
                outputs=outputs,
                freshness=freshness_state,
                output_meta=freshness_state,
            )
            return 1

        reason = "dry_run" if args.dry else "completed"
        log("=== 交易日更新计划完成 ===" if args.dry else "=== 交易日更新完成 ===")
        write_status(
            status,
            trade_date=target.isoformat(),
            state="skip" if args.dry else "success",
            reason=reason,
            started_at=started_at,
            finished_at=now_provider().isoformat(timespec="seconds"),
            step=None,
            steps=steps,
            outputs=outputs,
            freshness=freshness_state,
            output_meta=freshness_state,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
