"""Optional HARNESS detection and app-lifecycle daily update scheduling."""

from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config


DAILY_UPDATE_TIME = datetime.time(18, 30)
DAILY_UPDATE_TIMEOUT_SECONDS = 7200
_AUTO_UPDATE_LOCK = threading.Lock()
_AUTO_UPDATE_SCHEDULER = None
_AUTO_UPDATE_THREAD = None
_TRANSIENT_UPDATE_REASONS = frozenset({
    "lock_busy",
    "portal_date_missing",
    "portal_stale",
    "portal_coverage_insufficient",
    "sync_target_missing",
    "sync_target_stale_or_incomplete",
})


def ensure_harness(
    *,
    base_dir_fn=None,
    default_src_base: Path | None = None,
    harness_port: int | None = None,
    env=None,
    socket_module=None,
    shutil_module=None,
    subprocess_module=None,
    os_name: str | None = None,
):
    """Optionally start a compatible local HARNESS, preserving safe skip behavior."""

    environment = os.environ if env is None else env
    resolve_base = base_dir_fn or config.resolve_base_dir
    source_base = config.DEFAULT_SRC_BASE if default_src_base is None else Path(default_src_base)
    port = (
        config.resolve_harness_port(env=environment)
        if harness_port is None
        else config.resolve_harness_port(
            env={config.HARNESS_PORT_ENV: str(harness_port)},
        )
    )
    sockets = socket if socket_module is None else socket_module
    shell = shutil if shutil_module is None else shutil_module
    processes = subprocess if subprocess_module is None else subprocess_module
    platform_name = os.name if os_name is None else os_name
    if environment.get("QTRADE_NO_HARNESS"):
        print(f"[HARNESS({port})] QTRADE_NO_HARNESS 已设置，跳过自动启动")
        return
    try:
        connection = sockets.socket()
        connection.settimeout(0.3)
        try:
            connection.connect(("127.0.0.1", port))
            print(f"[HARNESS({port})] 已在运行")
            return
        except Exception:
            pass
        finally:
            connection.close()
        node = shell.which("node")
        if not node:
            print(f"[HARNESS({port})] 未找到 Node.js，跳过")
            return
        self_harness = resolve_base() / "harness"
        source_harness = source_base / "harness"
        harness = None
        for candidate in (source_harness, self_harness):
            if (
                (candidate / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").exists()
                and (candidate / "home" / "profiles" / "web" / "plugins" / "dsq-quant-bridge.js").exists()
                and (candidate / "home" / ".credentials.yaml").exists()
            ):
                harness = candidate
                break
        if harness is None:
            print(
                f"[HARNESS({port})] 未找到可用的底座 HARNESS 运行时（需安装 node_modules 与 v16 桥接插件），"
                "跳过（可运行 harness\\install.cmd）"
            )
            return
        dsh = harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        process_env = dict(environment)
        process_env["DSH_HOME"] = str(harness / "home")
        flags = processes.DETACHED_PROCESS if platform_name == "nt" else 0
        processes.Popen(
            [node, str(dsh), "web", "--port", str(port)],
            cwd=str(harness),
            env=process_env,
            stdout=processes.DEVNULL,
            stderr=processes.DEVNULL,
            creationflags=flags,
        )
        print(f"[HARNESS({port})] 已自动启动（底座量化桥接）")
    except Exception as error:
        print(f"[HARNESS({port})] 自动启动失败（忽略）: {error}")


def next_daily_update_at(
    now: datetime.datetime,
    *,
    cutoff: datetime.time = DAILY_UPDATE_TIME,
    handled_date: datetime.date | None = None,
) -> datetime.datetime:
    """Return the next check time without sleeping or touching external state."""

    candidate = datetime.datetime.combine(now.date(), cutoff)
    if handled_date == now.date():
        candidate += datetime.timedelta(days=1)
    elif now >= candidate:
        return now
    return candidate


def seconds_until_next_check(
    now: datetime.datetime,
    *,
    cutoff: datetime.time = DAILY_UPDATE_TIME,
    handled_date: datetime.date | None = None,
) -> float:
    """Return seconds until the scheduler should check again."""

    return max(0.0, (next_daily_update_at(now, cutoff=cutoff, handled_date=handled_date) - now).total_seconds())


class DailyUpdateScheduler:
    """Small stoppable scheduler used for the lifetime of the QTrade app."""

    def __init__(
        self,
        update_fn,
        *,
        clock=None,
        stop_event=None,
        cutoff: datetime.time = DAILY_UPDATE_TIME,
    ):
        self.update_fn = update_fn
        self.clock = clock or datetime.datetime.now
        self.stop_event = stop_event or threading.Event()
        self.cutoff = cutoff
        self.handled_date: datetime.date | None = None
        self.last_result: int | None = None

    def run_pending(self, now: datetime.datetime | None = None) -> int | None:
        """Run at most once after the cutoff for the supplied/current day."""

        current = now or self.clock()
        if self.handled_date == current.date():
            return None
        cutoff_at = datetime.datetime.combine(current.date(), self.cutoff)
        if current < cutoff_at:
            return None
        self.handled_date = current.date()
        try:
            result = self.update_fn(current.date())
            self.last_result = 0 if result is None else int(result)
        except Exception as error:  # noqa: BLE001 - one failed day must not spin
            print(f"[auto-update] 调度执行失败：{error}", flush=True)
            self.last_result = 1
        return self.last_result

    def seconds_until_next_check(self, now: datetime.datetime | None = None) -> float:
        current = now or self.clock()
        return seconds_until_next_check(
            current,
            cutoff=self.cutoff,
            handled_date=self.handled_date,
        )

    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            current = self.clock()
            self.run_pending(current)
            self.stop_event.wait(self.seconds_until_next_check(current))

    def stop(self) -> None:
        self.stop_event.set()


def build_daily_update_command(
    base: Path,
    target: datetime.date,
    *,
    project_root: Path | None = None,
    status_file: Path | None = None,
    python_executable: str | None = None,
) -> list[str]:
    """Build an argv-only daily-update command with explicit paths."""

    root = config.PROJECT_ROOT if project_root is None else Path(project_root)
    status = status_file or root / "logs" / "daily_update_1830.status.json"
    script = root / "scripts" / "daily_update_1830.py"
    return [
        python_executable or sys.executable,
        "-X",
        "utf8",
        str(script),
        "--date",
        target.isoformat(),
        "--deck-dir",
        str(base),
        "--status-file",
        str(status),
    ]


def _status_has_transient_failure(path: Path) -> bool:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    reasons = [payload.get("reason")]
    freshness = payload.get("freshness")
    if isinstance(freshness, dict):
        reasons.extend(
            value.get("reason")
            for value in freshness.values()
            if isinstance(value, dict)
        )
    return any(reason in _TRANSIENT_UPDATE_REASONS for reason in reasons)


def _record_retry(path: Path, attempt: int, max_attempts: int, next_attempt_at: datetime.datetime) -> None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return
        payload["retry"] = {
            "attempt": attempt,
            "max_attempts": max_attempts,
            "next_attempt_at": next_attempt_at.isoformat(timespec="seconds"),
        }
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return


def run_daily_update(
    base: Path,
    target: datetime.date,
    *,
    environment=None,
    subprocess_module=None,
    project_root: Path | None = None,
    status_file: Path | None = None,
    python_executable: str | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 300.0,
    sleep_fn=None,
    stop_event=None,
    clock=None,
) -> int:
    """Run the daily script, retrying only explicitly transient freshness failures."""

    processes = subprocess if subprocess_module is None else subprocess_module
    process_env = dict(os.environ if environment is None else environment)
    process_env["QTRADE_DECK_DIR"] = str(base)
    root = config.PROJECT_ROOT if project_root is None else Path(project_root)
    status_path = Path(status_file or root / "logs" / "daily_update_1830.status.json")
    attempts = max(1, int(max_attempts))
    sleeper = time.sleep if sleep_fn is None else sleep_fn
    now = datetime.datetime.now if clock is None else clock
    command = build_daily_update_command(
        Path(base),
        target,
        project_root=root,
        status_file=status_file,
        python_executable=python_executable,
    )
    for attempt in range(1, attempts + 1):
        try:
            result = processes.run(
                command,
                cwd=str(root),
                env=process_env,
                timeout=DAILY_UPDATE_TIMEOUT_SECONDS,
            )
        except Exception as error:  # noqa: BLE001 - scheduler records a failed day
            print(f"[auto-update] daily_update_1830 启动失败：{error}", flush=True)
            return 1
        returncode = int(getattr(result, "returncode", 0))
        if returncode == 0:
            return 0
        if attempt >= attempts or not _status_has_transient_failure(status_path):
            return returncode
        next_attempt = now() + datetime.timedelta(seconds=retry_delay_seconds)
        _record_retry(status_path, attempt, attempts, next_attempt)
        if stop_event is not None and stop_event.wait(retry_delay_seconds):
            return 1
        if stop_event is None:
            sleeper(retry_delay_seconds)
    return 1


def _legacy_injected_update(
    *,
    base,
    environment,
    processes,
    os_name,
    today_fn,
    python_executable,
):
    """Keep the PR5 explicit-clock test seam without affecting production."""

    platform_name = os.name if os_name is None else os_name
    current_day = today_fn()
    if not (base / "logs" / "pipeline_full_v2_done.txt").exists():
        print("[auto-update] 全量回填未完成，跳过自动增量（等 run_pipeline_full_v2.py 跑完即可启用）")
        return
    marker = base / "data" / "cache" / "last_auto_update.txt"
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == current_day:
        print("[auto-update] 今天已更新过，跳过")
        return
    script = base / "scripts" / "auto_update_daily.py"
    if not script.exists():
        print("[auto-update] 自动增量脚本缺失，跳过")
        return
    process_env = dict(environment)
    process_env["LWQUANT_CACHE_DIR"] = str(base / "data" / "cache")
    flags = processes.DETACHED_PROCESS if platform_name == "nt" else 0
    processes.Popen(
        [python_executable or sys.executable, "-X", "utf8", str(script)],
        cwd=str(base),
        env=process_env,
        stdout=processes.DEVNULL,
        stderr=processes.DEVNULL,
        creationflags=flags,
    )
    print("[auto-update] 已在后台启动增量更新（当天补最近 7 天日线）")


def maybe_auto_update(
    *,
    base_dir_fn=None,
    env=None,
    subprocess_module=None,
    os_name: str | None = None,
    today_fn=None,
    python_executable: str | None = None,
    project_root: Path | None = None,
    status_file: Path | None = None,
):
    """Start one daemon scheduler for the application's lifetime."""

    environment = os.environ if env is None else env
    if environment.get("QTRADE_NO_AUTOUPDATE"):
        print("[auto-update] 已通过 QTRADE_NO_AUTOUPDATE 关闭自动增量")
        return None

    resolve_base = base_dir_fn or config.resolve_base_dir
    base = Path(resolve_base())
    if today_fn is not None:
        _legacy_injected_update(
            base=base,
            environment=environment,
            processes=subprocess if subprocess_module is None else subprocess_module,
            os_name=os_name,
            today_fn=today_fn,
            python_executable=python_executable,
        )
        return None

    processes = subprocess if subprocess_module is None else subprocess_module
    root = config.PROJECT_ROOT if project_root is None else Path(project_root)

    global _AUTO_UPDATE_SCHEDULER, _AUTO_UPDATE_THREAD
    with _AUTO_UPDATE_LOCK:
        if _AUTO_UPDATE_THREAD is not None and _AUTO_UPDATE_THREAD.is_alive():
            return _AUTO_UPDATE_SCHEDULER

        stop_event = threading.Event()

        def invoke(target: datetime.date) -> int:
            return run_daily_update(
                base,
                target,
                environment=environment,
                subprocess_module=processes,
                project_root=root,
                status_file=status_file,
                python_executable=python_executable,
            )

        scheduler = DailyUpdateScheduler(invoke, stop_event=stop_event)
        thread = threading.Thread(
            target=scheduler.run_forever,
            name="qtrade-daily-update",
            daemon=True,
        )
        _AUTO_UPDATE_SCHEDULER = scheduler
        _AUTO_UPDATE_THREAD = thread
        thread.start()
    print("[auto-update] 已启动交易日 18:30 生命周期调度器")
    return scheduler


def stop_auto_update(timeout: float = 2.0) -> None:
    """Stop the singleton scheduler; safe to call repeatedly."""

    global _AUTO_UPDATE_SCHEDULER, _AUTO_UPDATE_THREAD
    with _AUTO_UPDATE_LOCK:
        scheduler = _AUTO_UPDATE_SCHEDULER
        thread = _AUTO_UPDATE_THREAD
        if scheduler is None:
            return
        scheduler.stop()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=timeout)
    with _AUTO_UPDATE_LOCK:
        if _AUTO_UPDATE_THREAD is None or not _AUTO_UPDATE_THREAD.is_alive():
            _AUTO_UPDATE_SCHEDULER = None
            _AUTO_UPDATE_THREAD = None
