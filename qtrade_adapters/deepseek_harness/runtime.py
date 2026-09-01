"""Optional HARNESS detection and app-lifecycle daily update scheduling."""

from __future__ import annotations

import datetime
import ctypes
import json
import math
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import config


DAILY_UPDATE_TIME = datetime.time(18, 30)
DAILY_UPDATE_TIMEOUT_SECONDS = 7200
MANUAL_UPDATE_PROCESS_POLL_SECONDS = 0.1
MANUAL_UPDATE_STOP_TIMEOUT_SECONDS = 2.0
MANUAL_UPDATE_STARTUP_TIMEOUT_SECONDS = 3.0
MANUAL_UPDATE_LOG_MAX_BYTES = 256 * 1024
_AUTO_UPDATE_LOCK = threading.Lock()
_AUTO_UPDATE_SCHEDULER = None
_AUTO_UPDATE_THREAD = None
_MANUAL_UPDATE_TERMINAL_STATES = frozenset({"success", "portal_success", "skip", "failure", "aborted", "timed_out"})
_MANUAL_UPDATE_STEPS = frozenset({
    "calendar",
    "resolve_deck",
    "freshness",
    "portal",
    "portal_freshness",
    "factors",
    "factor_freshness",
    "decision_scan",
    "decision_pool_freshness",
    "decision_pitch_v2",
    "decision_freshness",
    "sync",
    "sync_freshness",
    "pipeline",
})
MANUAL_UPDATE_STALE_SECONDS = 15 * 60
_MANUAL_UPDATE_REASONS = frozenset({
    "accepted",
    "running",
    "started",
    "pipeline_running",
    "forced",
    "dry_run",
    "before_cutoff",
    "already_running",
    "already_success",
    "lock_busy",
    "calendar_unavailable",
    "calendar_cache",
    "calendar_cache_closed",
    "calendar_api",
    "calendar_api_closed",
    "weekend",
    "deck_missing",
    "step_failed",
    "update_failed",
    "status_unavailable",
    "completed",
    "application_shutdown",
    "manual_stop",
    "stale_running",
    "timeout",
    "process_timeout",
    "freshness_capture_failed",
    "portal_completed",
    "portal_refresh_failed",
    "calendar_closed",
    "universe_unavailable",
    "provider_schema",
    "provider_failed",
    "provider_unreachable",
    "checkpoint_corrupt",
    "checkpoint_io",
    "lease_busy",
    "stale_running",
    "item_timeout",
    "batch_timeout",
    "job_timeout",
    "publish_timeout",
    "publish_failed",
    "reload_failed",
})
_MANUAL_UPDATE_FRESHNESS_GROUPS = ("portal", "factors", "decision", "sync")
_MANUAL_UPDATE_FRESHNESS_SOURCES = frozenset({
    "external_sqlite",
    "factor_artifacts",
    "decision_artifact",
    "sync_target",
    "qtrade_mirror",
    "dry_run",
    "unavailable",
})
_MANUAL_UPDATE_FRESHNESS_REASONS = frozenset({
    "baseline_captured",
    "verified",
    "dry_run",
    "database_unavailable",
    "metadata_missing",
    "metadata_schema_unsupported",
    "metadata_read_error",
    "bars_missing",
    "bars_schema_unsupported",
    "bars_read_error",
    "factor_artifact_missing",
    "factor_date_mismatch",
    "factor_artifact_unchanged",
    "factor_core_artifact_missing",
    "factor_count_missing",
    "decision_pool_missing_or_stale",
    "decision_empty_result_unconfirmed",
    "decision_pitch_missing_or_stale",
    "sync_target_unavailable",
    "sync_target_missing",
    "sync_target_stale_or_incomplete",
    "portal_date_missing",
    "portal_stale",
    "portal_coverage_insufficient",
    "unavailable",
})
_MANUAL_UPDATE_FRESHNESS_COUNTS = (
    "total",
    "computable",
    "tradable",
    "coverage",
    "coverage_required",
    "factor_count",
    "valid_count",
    "artifact_count",
    "pool_count",
    "pitch_count",
)
_TRANSIENT_UPDATE_REASONS = frozenset({
    "lock_busy",
    "portal_date_missing",
    "portal_stale",
    "portal_coverage_insufficient",
    "sync_target_missing",
    "sync_target_stale_or_incomplete",
})
_MANUAL_UPDATE_IDEMPOTENT_REASONS = frozenset({
    "weekend",
    "calendar_cache_closed",
    "calendar_api_closed",
    "calendar_unavailable",
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
        stop_hook=None,
    ):
        self.update_fn = update_fn
        self.clock = clock or datetime.datetime.now
        self.stop_event = stop_event or threading.Event()
        self.cutoff = cutoff
        self.stop_hook = stop_hook
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
            print(
                f"[auto-update] 调度执行失败：{type(error).__name__}",
                flush=True,
            )
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
        if self.stop_hook is not None:
            try:
                self.stop_hook()
            except Exception:  # noqa: BLE001 - shutdown must remain bounded
                pass


def build_daily_update_command(
    base: Path,
    target: datetime.date,
    *,
    project_root: Path | None = None,
    status_file: Path | None = None,
    log_file: Path | None = None,
    python_executable: str | None = None,
) -> list[str]:
    """Build an argv-only daily-update command with explicit paths."""

    root = config.PROJECT_ROOT if project_root is None else Path(project_root)
    status = status_file or root / "logs" / "daily_update_1830.status.json"
    script = root / "scripts" / "daily_update_1830.py"
    command = [
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
    if log_file is not None:
        command.extend(["--log-file", str(log_file)])
    return command


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


def _atomic_write_json(path: Path, payload: dict) -> None:
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
        _atomic_write_json(Path(path), payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return


def _redact_update_output(value: object) -> str:
    """Keep the manual child log useful without copying secrets or host paths."""

    text = str(value)
    text = re.sub(
        r"(?i)(api[_-]?key|authorization|token|secret|password)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2<redacted>",
        text,
    )
    text = re.sub(r"(?i)\bsk-[a-z0-9_-]{8,}\b", "<redacted>", text)
    text = re.sub(r"(?i)(?:[a-z]:[\\/]|/(?:users|home|tmp)/)[^\s\"']*", "<path>", text)
    return text[:2000]


def _append_manual_log(log_file: Path | None, stream_name: str, value: object) -> None:
    if log_file is None:
        return
    text = _redact_update_output(value).strip()
    if not text:
        return
    destination = Path(log_file)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.stat().st_size >= MANUAL_UPDATE_LOG_MAX_BYTES:
            rotated = destination.with_name(f"{destination.name}.1")
            try:
                rotated.unlink()
            except FileNotFoundError:
                pass
            os.replace(destination, rotated)
        with destination.open("a", encoding="utf-8") as stream:
            stream.write(f"[manual-child:{stream_name}] {text}\n")
    except OSError:
        return


def _capture_manual_output(process, log_file: Path | None):
    """Drain the owned child pipe so it cannot block, retaining bounded output."""

    stream = getattr(process, "stdout", None)
    if stream is None or not hasattr(stream, "readline"):
        return None

    def consume() -> None:
        try:
            while True:
                try:
                    chunk = stream.readline()
                except (OSError, ValueError):
                    break
                if not chunk:
                    break
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", "replace")
                _append_manual_log(log_file, "stdout", chunk)
        finally:
            try:
                stream.close()
            except (AttributeError, OSError):
                pass

    reader = threading.Thread(
        target=consume,
        name="qtrade-manual-update-output",
        daemon=True,
    )
    reader.start()
    return reader


def _close_manual_output(process) -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(process, name, None)
        try:
            if stream is not None and hasattr(stream, "close"):
                stream.close()
        except (AttributeError, OSError):
            pass


def _terminate_managed_process(process, processes) -> None:
    """Stop only the process group created for one manual update attempt."""

    if getattr(process, "poll", lambda: None)() is not None:
        return
    deadline = time.monotonic() + MANUAL_UPDATE_STOP_TIMEOUT_SECONDS
    pid = getattr(process, "pid", None)
    if os.name == "nt" and processes is subprocess and isinstance(pid, int) and pid > 0:
        try:
            remaining = max(0.01, deadline - time.monotonic())
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=remaining,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    elif os.name != "nt" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass

    try:
        process.terminate()
    except (AttributeError, OSError):
        pass
    try:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            process.wait(timeout=remaining)
            return
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.kill()
    except (AttributeError, OSError):
        pass
    try:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            process.wait(timeout=remaining)
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        pass


def _run_interruptible_command(
    command,
    *,
    cwd,
    environment,
    processes,
    stop_event,
    timeout=DAILY_UPDATE_TIMEOUT_SECONDS,
    log_file: Path | None = None,
    process_holder=None,
) -> tuple[int, bool]:
    """Run one fixed command and interrupt only its managed process group.

    The automatic scheduler keeps its historical ``subprocess.run`` path.  The
    manual console opts into this bounded Popen path so application shutdown
    cannot leave its update child running for the full pipeline timeout.
    """

    popen_kwargs = {
        "cwd": str(cwd),
        "env": environment,
        "shell": False,
        "stdout": getattr(processes, "PIPE", subprocess.PIPE),
        "stderr": getattr(processes, "STDOUT", subprocess.STDOUT),
    }
    if os.name == "nt":
        flags = getattr(processes, "CREATE_NEW_PROCESS_GROUP", 0)
        flags |= getattr(processes, "CREATE_NO_WINDOW", 0)
        if flags:
            popen_kwargs["creationflags"] = flags
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = processes.Popen(command, **popen_kwargs)
    except Exception as error:  # noqa: BLE001 - caller records a safe failure
        print(f"[auto-update] daily_update_1830 启动失败：{type(error).__name__}", flush=True)
        return 1, False

    if process_holder is not None:
        process_holder["process"] = process
    reader = _capture_manual_output(process, log_file)
    deadline = time.monotonic() + max(0.0, float(timeout))
    interrupted = False
    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                stopped = stop_event is not None and stop_event.is_set()
                return int(returncode), interrupted or stopped
            if stop_event is not None and stop_event.is_set():
                interrupted = True
                _terminate_managed_process(process, processes)
                return 1, interrupted
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                interrupted = True
                _terminate_managed_process(process, processes)
                return 1, interrupted
            wait_for = min(MANUAL_UPDATE_PROCESS_POLL_SECONDS, remaining)
            try:
                process.wait(timeout=wait_for)
            except subprocess.TimeoutExpired:
                continue
    except Exception as error:  # noqa: BLE001 - terminate before returning
        print(f"[auto-update] daily_update_1830 等待失败：{type(error).__name__}", flush=True)
        interrupted = True
        _terminate_managed_process(process, processes)
        return 1, interrupted
    finally:
        if reader is not None:
            reader.join(timeout=0.5)
        _close_manual_output(process)
        if reader is not None:
            reader.join(timeout=0.5)
        if process_holder is not None and process_holder.get("process") is process:
            process_holder["process"] = None


def _write_parent_terminal_status(
    path: Path,
    target: datetime.date,
    *,
    state: str,
    reason: str,
    expected_job_id: str | None = None,
    now=None,
) -> None:
    """Close a child-owned running record after its parent stops it."""

    current = datetime.datetime.now if now is None else now
    raw = _read_status_record(path) or {}
    if raw.get("state") in _MANUAL_UPDATE_TERMINAL_STATES:
        return
    raw_job_id = raw.get("job_id")
    if (
        expected_job_id is not None
        and isinstance(raw_job_id, str)
        and raw_job_id != expected_job_id
    ):
        # A newer manual generation owns the shared status file.  A stopped
        # child from an older generation must not publish over it.
        return
    safe = read_manual_update_status(path)
    finished_at = current().isoformat(timespec="seconds")
    payload = {
        "schema_version": 1,
        "trade_date": _safe_date_text(target),
        "state": state,
        "reason": _safe_manual_reason(reason, state),
        "started_at": safe.get("started_at"),
        "finished_at": finished_at,
        "step": safe.get("step"),
        "steps": _safe_step_records(raw.get("steps")),
        "outputs": safe.get("outputs", {"portal": False, "factors": False, "decision": False, "sync": False}),
        "freshness": safe.get("freshness", {}),
        "output_meta": safe.get("freshness", {}),
        "retry": safe.get("retry", {"attempt": 0, "max_attempts": 3, "next_attempt_at": None}),
        "job_id": expected_job_id or (raw_job_id if isinstance(raw_job_id, str) else None),
        "owner_pid": os.getpid(),
        "heartbeat_at": finished_at,
        "elapsed_seconds": safe.get("elapsed_seconds", 0.0),
        "progress": safe.get("progress", {"completed": 0, "total": 0, "current": None}),
    }
    try:
        _atomic_write_json(Path(path), payload)
    except OSError:
        # The child may have been stopped while its state directory is being
        # torn down; never replace a terminal record with an unsafe traceback.
        return


def run_daily_update(
    base: Path,
    target: datetime.date,
    *,
    environment=None,
    subprocess_module=None,
    project_root: Path | None = None,
    status_file: Path | None = None,
    log_file: Path | None = None,
    python_executable: str | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 300.0,
    sleep_fn=None,
    stop_event=None,
    clock=None,
    interruptible: bool = False,
    process_holder=None,
    job_id: str | None = None,
) -> int:
    """Run the daily script, retrying only explicitly transient freshness failures."""

    processes = subprocess if subprocess_module is None else subprocess_module
    process_env = dict(os.environ if environment is None else environment)
    process_env["QTRADE_DECK_DIR"] = str(base)
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        job_id = uuid.uuid4().hex
    process_env["QTRADE_UPDATE_JOB_ID"] = job_id
    if interruptible:
        process_env["QTRADE_UPDATE_OBSERVABLE"] = "1"
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
        log_file=log_file,
        python_executable=python_executable,
    )
    for attempt in range(1, attempts + 1):
        try:
            if interruptible:
                returncode, interrupted = _run_interruptible_command(
                    command,
                    cwd=root,
                    environment=process_env,
                    processes=processes,
                    stop_event=stop_event,
                    log_file=log_file,
                    process_holder=process_holder,
                )
                if interrupted:
                    reason = "application_shutdown" if stop_event is not None and stop_event.is_set() else "process_timeout"
                    _write_parent_terminal_status(
                        status_path,
                        target,
                        state="aborted" if reason == "application_shutdown" else "failure",
                        reason=reason,
                        expected_job_id=job_id,
                        now=now,
                    )
                    return 1
            else:
                result = processes.run(
                    command,
                    cwd=str(root),
                    env=process_env,
                    timeout=DAILY_UPDATE_TIMEOUT_SECONDS,
                    shell=False,
                )
                returncode = int(getattr(result, "returncode", 0))
        except Exception as error:  # noqa: BLE001 - scheduler records a failed day
            print(
                f"[auto-update] daily_update_1830 启动失败：{type(error).__name__}",
                flush=True,
            )
            _write_parent_terminal_status(
                status_path,
                target,
                state="failure",
                reason="update_failed",
                expected_job_id=job_id,
                now=now,
            )
            return 1
        if returncode == 0:
            return 0
        if attempt >= attempts or not _status_has_transient_failure(status_path):
            _write_parent_terminal_status(
                status_path,
                target,
                state="failure",
                reason="update_failed",
                expected_job_id=job_id,
                now=now,
            )
            return returncode
        next_attempt = now() + datetime.timedelta(seconds=retry_delay_seconds)
        _record_retry(status_path, attempt, attempts, next_attempt)
        if stop_event is not None and stop_event.wait(retry_delay_seconds):
            _write_parent_terminal_status(
                status_path,
                target,
                state="aborted",
                reason="application_shutdown",
                expected_job_id=job_id,
                now=now,
            )
            return 1
        if stop_event is None:
            sleeper(retry_delay_seconds)
    return 1


def _safe_date_text(value):
    if isinstance(value, datetime.datetime):
        value = value.date()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def _safe_timestamp_text(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return value[:32]


def _safe_manual_reason(value, state):
    if isinstance(value, str) and value.startswith("calendar_unavailable:"):
        return "calendar_unavailable"
    if isinstance(value, str) and value in _MANUAL_UPDATE_REASONS:
        return value
    if state == "failure":
        return "update_failed"
    return "status_unavailable"


def _safe_manual_freshness(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for group in _MANUAL_UPDATE_FRESHNESS_GROUPS:
        item = value.get(group)
        if not isinstance(item, dict):
            continue
        source = item.get("source")
        reason = item.get("reason")
        clean = {
            "verified": item.get("verified") is True,
            "as_of": _safe_date_text(item.get("as_of")),
            "source": (
                source
                if isinstance(source, str) and source in _MANUAL_UPDATE_FRESHNESS_SOURCES
                else "unavailable"
            ),
            "reason": (
                reason
                if isinstance(reason, str) and reason in _MANUAL_UPDATE_FRESHNESS_REASONS
                else "unavailable"
            ),
        }
        for key in _MANUAL_UPDATE_FRESHNESS_COUNTS:
            count = item.get(key)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                clean[key] = count
        for key in ("pitch_verified", "portal", "factors", "decision"):
            if isinstance(item.get(key), bool):
                clean[key] = item[key]
        result[group] = clean
    return result


def _safe_manual_progress(value):
    if not isinstance(value, dict):
        return {"completed": 0, "total": 0, "current": None}
    completed = value.get("completed")
    total = value.get("total")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        completed = 0
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = 0
    current = value.get("current")
    if not isinstance(current, str) or current not in _MANUAL_UPDATE_STEPS:
        current = None
    return {"completed": min(completed, 100), "total": min(total, 100), "current": current}


def read_manual_update_status(path: Path) -> dict[str, object]:
    """Read only the safe fields needed by the native manual-update console."""

    outputs = {"portal": False, "factors": False, "decision": False, "sync": False}
    fallback = {
        "schema_version": 1,
        "mode": "full_pipeline",
        "state": "idle",
        "trade_date": None,
        "started_at": None,
        "finished_at": None,
        "reason": "status_unavailable",
        "outputs": outputs,
        "freshness": {},
        "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
        "step": None,
        "heartbeat_at": None,
        "elapsed_seconds": 0.0,
        "progress": {"completed": 0, "total": 0, "current": None},
    }
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback
    if not isinstance(payload, dict):
        return fallback

    raw_state = payload.get("state")
    allowed_disk_states = _MANUAL_UPDATE_TERMINAL_STATES | {"running"}
    state = raw_state if isinstance(raw_state, str) and raw_state in allowed_disk_states else "idle"
    result = dict(fallback)
    mode = payload.get("mode")
    result["mode"] = mode if mode in {"full_pipeline", "portal_only"} else "full_pipeline"
    result["state"] = state
    result["trade_date"] = _safe_date_text(payload.get("trade_date"))
    result["started_at"] = _safe_timestamp_text(payload.get("started_at"))
    result["finished_at"] = _safe_timestamp_text(payload.get("finished_at"))
    result["heartbeat_at"] = _safe_timestamp_text(payload.get("heartbeat_at"))
    step = payload.get("step")
    result["step"] = step if isinstance(step, str) and step in _MANUAL_UPDATE_STEPS else None
    elapsed = payload.get("elapsed_seconds")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool) and math.isfinite(elapsed) and elapsed >= 0:
        result["elapsed_seconds"] = min(float(elapsed), 86_400.0)
    result["progress"] = _safe_manual_progress(payload.get("progress"))
    result["reason"] = _safe_manual_reason(payload.get("reason"), state)
    raw_outputs = payload.get("outputs")
    if isinstance(raw_outputs, dict):
        result["outputs"] = {
            key: raw_outputs.get(key) is True for key in outputs
        }
    freshness = _safe_manual_freshness(payload.get("freshness"))
    if freshness:
        result["freshness"] = freshness
    retry = payload.get("retry")
    if isinstance(retry, dict):
        safe_retry = dict(fallback["retry"])
        for key in ("attempt", "max_attempts"):
            value = retry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_retry[key] = value
        safe_retry["next_attempt_at"] = _safe_timestamp_text(retry.get("next_attempt_at"))
        result["retry"] = safe_retry
    return result


def _read_status_record(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _pid_is_alive(value: object) -> bool:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return False
    if os.name == "nt":
        return _windows_pid_is_alive(value)
    try:
        os.kill(value, 0)
    except PermissionError:
        return True
    except (OSError, ProcessLookupError):
        return False
    return True


def _windows_pid_is_alive(pid: int) -> bool:
    """Check a Windows PID without sending a console signal."""

    process_handle = None
    kernel32 = None
    try:
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        process_handle = kernel32.OpenProcess(
            0x00100000 | 0x00001000,
            False,
            pid,
        )
        if not process_handle:
            # ERROR_INVALID_PARAMETER means the PID no longer exists. Access
            # denied and all other inspection failures fail safe as alive.
            return ctypes.get_last_error() != 87

        wait_result = kernel32.WaitForSingleObject(process_handle, 0)
        if wait_result == 0:
            return False
        if wait_result != 0x00000102:
            return True

        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == 259
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return True
    finally:
        if process_handle and kernel32 is not None:
            try:
                kernel32.CloseHandle(process_handle)
            except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
                pass


def _read_lease_record(path: Path):
    """Read a lease owner and identity without trusting the status JSON."""

    descriptor = None
    identity = None
    try:
        descriptor = os.open(str(path), os.O_RDONLY)
        identity = os.fstat(descriptor)
        content = os.read(descriptor, 128).decode("ascii", "strict")
        match = re.fullmatch(r"pid=(\d+)\s*", content)
        if match is None:
            return None
        owner_pid = int(match.group(1))
        if owner_pid <= 0:
            return None
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return None
        return owner_pid, (identity.st_dev, identity.st_ino)
    except (OSError, UnicodeError, ValueError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _reclaim_dead_lease(path: Path) -> bool:
    """Remove only a dead lease whose path still has the same inode."""

    lease_path = Path(path)
    record = _read_lease_record(lease_path)
    if record is None:
        return False
    owner_pid, identity = record
    if _pid_is_alive(owner_pid):
        return False
    # Re-read immediately before unlinking.  This prevents a controller from
    # deleting a replacement lease (including one with a reused PID).
    if _read_lease_record(lease_path) != record:
        return False
    try:
        current = lease_path.stat()
        if (current.st_dev, current.st_ino) != identity:
            return False
        lease_path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


class _UpdateLease:
    """Atomic process lease shared by automatic and manual update jobs."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd = None
        self._identity = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                str(self.path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        identity = os.fstat(descriptor)
        try:
            os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            os.fsync(descriptor)
        except Exception:
            try:
                current = self.path.stat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    self.path.unlink()
            except (FileNotFoundError, OSError):
                pass
            os.close(descriptor)
            raise
        self._fd = descriptor
        self._identity = (identity.st_dev, identity.st_ino)
        return True

    def release(self) -> None:
        descriptor = self._fd
        identity = self._identity
        self._fd = None
        self._identity = None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            current = self.path.stat()
            if identity is not None and (current.st_dev, current.st_ino) == identity:
                self.path.unlink()
        except (FileNotFoundError, OSError):
            pass


def _status_is_stale(payload: dict[str, object], now: datetime.datetime) -> bool:
    if payload.get("state") != "running":
        return False
    stamp = payload.get("heartbeat_at") or payload.get("started_at")
    try:
        reference = datetime.datetime.fromisoformat(stamp) if isinstance(stamp, str) else None
    except ValueError:
        reference = None
    if reference is None:
        return not _pid_is_alive(payload.get("owner_pid"))
    if (now - reference).total_seconds() <= MANUAL_UPDATE_STALE_SECONDS:
        return False
    return not _pid_is_alive(payload.get("owner_pid"))


def _safe_step_records(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or name not in _MANUAL_UPDATE_STEPS:
            continue
        state = item.get("state")
        if state not in {"pending", "success", "failure", "planned"}:
            state = "pending"
        clean = {"name": name, "state": state}
        group = item.get("group")
        if isinstance(group, str) and group in {"portal", "factors", "decision", "sync"}:
            clean["group"] = group
        returncode = item.get("returncode")
        if isinstance(returncode, int) and not isinstance(returncode, bool):
            clean["returncode"] = returncode
        result.append(clean)
    return result


class ManualUpdateController:
    """Run the complete daily pipeline once from an explicit UI action.

    The controller accepts no user-supplied command, path or target date.  It
    only gates the existing daily script and keeps a single daemon worker for
    the lifetime of the server process.
    """

    def __init__(
        self,
        *,
        base_dir_fn=None,
        project_root: Path | None = None,
        status_file: Path | None = None,
        lock_path: Path | None = None,
        pipeline_lock_path: Path | None = None,
        log_file: Path | None = None,
        clock=None,
        run_fn=None,
        thread_factory=None,
        subprocess_module=None,
        user_data_dir: Path | None = None,
        mode: str = "full_pipeline",
        on_success=None,
    ):
        self.base_dir_fn = base_dir_fn or config.resolve_base_dir
        self.project_root = config.PROJECT_ROOT if project_root is None else Path(project_root)
        self.status_file = Path(
            status_file or self.project_root / "logs" / "daily_update_1830.status.json"
        )
        self.lock_path = Path(
            lock_path or self.project_root / "logs" / "daily_update_1830.manual.lock"
        )
        self.pipeline_lock_path = Path(
            pipeline_lock_path or self.status_file.with_name("daily_update_1830.lock")
        )
        self.log_file = Path(
            log_file or self.status_file.with_name("daily_update_1830.log")
        )
        self.user_data_dir = Path(user_data_dir) if user_data_dir is not None else None
        self.mode = mode if mode in {"full_pipeline", "portal_only"} else "full_pipeline"
        self.on_success = on_success
        self.clock = clock or datetime.datetime.now
        self._uses_default_run_fn = run_fn is None
        self.run_fn = run_fn or run_daily_update
        self.thread_factory = thread_factory or threading.Thread
        self.subprocess_module = subprocess_module
        self._lock = threading.Lock()
        self._worker = None
        self._generation = 0
        self._stop_event = threading.Event()
        self._lease_fd = None
        self._lease_identity = None
        self._lease_generation = None
        self._job_id = None
        self._startup_event = None
        self._startup_result = None
        self._startup_generation = None
        self._terminal_event = None
        self._terminal_generation = None
        self._snapshot = self._idle_snapshot()

    @staticmethod
    def _outputs():
        return {"portal": False, "factors": False, "decision": False, "sync": False}

    def _idle_snapshot(self):
        return {
            "schema_version": 1,
            "mode": self.mode,
            "accepted": False,
            "state": "idle",
            "trade_date": None,
            "started_at": None,
            "finished_at": None,
            "reason": "status_unavailable",
            "outputs": self._outputs(),
            "freshness": {},
            "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
            "step": None,
            "heartbeat_at": None,
            "elapsed_seconds": 0.0,
            "progress": {"completed": 0, "total": 0, "current": None},
        }

    def _snapshot_for(
        self,
        *,
        state,
        target=None,
        reason="status_unavailable",
        started_at=None,
        finished_at=None,
        step=None,
        progress=None,
        elapsed_seconds=0.0,
    ):
        snapshot = self._idle_snapshot()
        snapshot.update({
            "mode": self.mode,
            "accepted": state == "accepted",
            "state": state,
            "trade_date": _safe_date_text(target),
            "reason": _safe_manual_reason(reason, state),
            "started_at": _safe_timestamp_text(started_at),
            "finished_at": _safe_timestamp_text(finished_at),
            "step": step if step in _MANUAL_UPDATE_STEPS else None,
            "heartbeat_at": _safe_timestamp_text(finished_at or started_at),
            "elapsed_seconds": elapsed_seconds if isinstance(elapsed_seconds, (int, float)) else 0.0,
            "progress": _safe_manual_progress(progress),
        })
        return snapshot

    def _write_terminal_status_locked(
        self,
        *,
        target=None,
        state="aborted",
        reason="application_shutdown",
        started_at=None,
        job_id: str | None = None,
        override_terminal: bool = False,
    ) -> dict[str, object]:
        """Publish a safe terminal record for a stopped owned job."""

        raw = _read_status_record(self.status_file) or {}
        safe = read_manual_update_status(self.status_file)
        raw_job_id = raw.get("job_id")
        owner_job_id = job_id or self._job_id
        if (
            owner_job_id is not None
            and isinstance(raw_job_id, str)
            and raw_job_id != owner_job_id
        ):
            # A newer generation has taken ownership of the status file.
            return safe
        if raw.get("state") in _MANUAL_UPDATE_TERMINAL_STATES and not override_terminal:
            return safe
        current = self.clock().isoformat(timespec="seconds")
        target_text = _safe_date_text(target) or safe.get("trade_date")
        started_text = _safe_timestamp_text(started_at) or safe.get("started_at")
        payload = {
            "schema_version": 1,
            "mode": safe.get("mode", self.mode),
            "trade_date": target_text,
            "state": state,
            "reason": reason,
            "started_at": started_text,
            "finished_at": current,
            "step": safe.get("step"),
            "steps": _safe_step_records(raw.get("steps")),
            "outputs": safe.get("outputs", self._outputs()),
            "freshness": safe.get("freshness", {}),
            "output_meta": safe.get("freshness", {}),
            "retry": safe.get("retry", {"attempt": 0, "max_attempts": 3, "next_attempt_at": None}),
            "heartbeat_at": current,
            "elapsed_seconds": safe.get("elapsed_seconds", 0.0),
            "progress": safe.get("progress", {"completed": 0, "total": 0, "current": None}),
            "job_id": owner_job_id or (raw_job_id if isinstance(raw_job_id, str) else None),
            "owner_pid": os.getpid(),
        }
        _atomic_write_json(self.status_file, payload)
        result = read_manual_update_status(self.status_file)
        result["state"] = state
        result["reason"] = reason
        result["finished_at"] = current
        return result

    def _write_running_status_locked(
        self,
        *,
        target: datetime.date,
        state: str,
        reason: str,
        started_at: str | None,
        progress: dict[str, object],
    ) -> None:
        """Publish this generation before it can spawn or report a child."""

        current = self.clock().isoformat(timespec="seconds")
        _atomic_write_json(
            self.status_file,
            {
                "schema_version": 1,
                "mode": self.mode,
                "accepted": state == "accepted",
                "trade_date": target.isoformat(),
                "state": state,
                "reason": reason,
                "started_at": started_at,
                "finished_at": None,
                "step": None,
                "steps": [],
                "outputs": self._outputs(),
                "freshness": {},
                "output_meta": {},
                "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
                "job_id": self._job_id,
                "owner_pid": os.getpid(),
                "heartbeat_at": current,
                "elapsed_seconds": 0.0,
                "progress": progress,
            },
        )

    def _recover_stale_status_locked(self, now: datetime.datetime, target: datetime.date) -> dict[str, object]:
        """Recover an old status only after checking the owned lease itself."""

        raw = _read_status_record(self.status_file)
        if self.pipeline_lock_path.exists():
            return read_manual_update_status(self.status_file)

        reclaimed = False
        if self.lock_path.exists():
            reclaimed = self._reclaim_stale_lease_locked(raw)
            if not reclaimed:
                return read_manual_update_status(self.status_file)

        if raw is not None and raw.get("state") == "running":
            if reclaimed or _status_is_stale(raw, now):
                return self._write_terminal_status_locked(
                    target=target,
                    state="aborted",
                    reason="stale_running",
                    started_at=raw.get("started_at"),
                )
        return read_manual_update_status(self.status_file)

    def _reclaim_stale_lease_locked(self, payload: dict[str, object] | None = None) -> bool:
        """Remove only a lease proven to belong to a dead stale owner."""

        # ``payload`` is retained only for source compatibility.  The status
        # owner_pid may be the daily child rather than this controller parent,
        # so it is intentionally not consulted for lease ownership.
        return _reclaim_dead_lease(self.lock_path)

    def _set_terminal_from_disk(self, returncode, target, started_at=None):
        disk = read_manual_update_status(self.status_file)
        if (
            disk["trade_date"] == target.isoformat()
            and disk["state"] in _MANUAL_UPDATE_TERMINAL_STATES
        ):
            return disk
        return self._write_terminal_status_locked(
            target=target,
            state="failure",
            reason="status_unavailable" if returncode == 0 else "update_failed",
            started_at=started_at,
        )

    def _acquire_lease_locked(self) -> bool:
        """Atomically own the controller lease until its worker exits."""

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                str(self.lock_path),
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            return False
        identity = os.fstat(descriptor)
        try:
            payload = f"pid={os.getpid()}\n"
            os.write(descriptor, payload.encode("ascii"))
            os.fsync(descriptor)
        except Exception:
            try:
                current = self.lock_path.stat()
                if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                    self.lock_path.unlink()
            except (FileNotFoundError, OSError):
                pass
            os.close(descriptor)
            raise
        self._lease_fd = descriptor
        self._lease_identity = (identity.st_dev, identity.st_ino)
        return True

    def _release_lease_locked(self) -> None:
        descriptor = self._lease_fd
        identity = self._lease_identity
        self._lease_fd = None
        self._lease_identity = None
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            current = self.lock_path.stat()
            if identity is None or (current.st_dev, current.st_ino) == identity:
                self.lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _run(self, generation, base, target):
        started_at = self.clock().isoformat(timespec="seconds")
        with self._lock:
            if generation != self._generation:
                if self._lease_generation == generation:
                    self._release_lease_locked()
                    self._lease_generation = None
                return
            running = self._snapshot_for(
                state="running",
                target=target,
                reason="running",
                started_at=started_at,
                progress={"completed": 0, "total": 10, "current": None},
            )
            self._snapshot = running
            job_id = self._job_id
            try:
                self._write_running_status_locked(
                    target=target,
                    state="running",
                    reason="running",
                    started_at=started_at,
                    progress=running["progress"],
                )
            except OSError:
                # The child still owns the process lease, but status errors are
                # converted to the same stable terminal state below.
                pass
        try:
            run_kwargs = {
                "environment": os.environ,
                "project_root": self.project_root,
                "status_file": self.status_file,
                "log_file": self.log_file,
                "python_executable": sys.executable,
                "stop_event": self._stop_event,
                "job_id": job_id,
            }
            if self._uses_default_run_fn:
                run_kwargs["interruptible"] = True
                if self.subprocess_module is not None:
                    run_kwargs["subprocess_module"] = self.subprocess_module
            else:
                run_kwargs["user_data_dir"] = self.user_data_dir
                if self.mode == "portal_only":
                    run_kwargs["startup_event"] = self._startup_event
                    run_kwargs["startup_result"] = self._startup_result
            returncode = self.run_fn(
                base,
                target,
                **run_kwargs,
            )
            with self._lock:
                stale_generation = generation != self._generation
            if stale_generation:
                # ``stop`` owns the disk terminal record for an invalidated
                # generation.  Reassert it only when the old job token still
                # owns the file; a newer generation is never overwritten.
                with self._lock:
                    terminal = self._write_terminal_status_locked(
                        target=target,
                        state="aborted",
                        reason="application_shutdown",
                        started_at=started_at,
                        job_id=job_id,
                        override_terminal=True,
                    )
            elif returncode == 0 and self.on_success is not None:
                try:
                    reloaded = self.on_success(_read_status_record(self.status_file))
                except Exception:  # noqa: BLE001 - keep the public result stable
                    reloaded = False
                with self._lock:
                    if reloaded is False:
                        terminal = self._write_terminal_status_locked(
                            target=target,
                            state="failure",
                            reason="reload_failed",
                            started_at=started_at,
                            job_id=job_id,
                            override_terminal=True,
                        )
                    else:
                        terminal = self._set_terminal_from_disk(
                            int(returncode or 0), target, started_at=started_at
                        )
            else:
                with self._lock:
                    terminal = self._set_terminal_from_disk(
                        int(returncode or 0), target, started_at=started_at
                    )
        except Exception:  # noqa: BLE001 - UI receives only a stable failure state
            with self._lock:
                if generation == self._generation:
                    try:
                        terminal = self._write_terminal_status_locked(
                            target=target,
                            state="failure",
                            reason="update_failed",
                            started_at=started_at,
                        )
                    except OSError:
                        terminal = self._snapshot_for(
                            state="failure",
                            target=target,
                            reason="update_failed",
                            started_at=started_at,
                            finished_at=self.clock().isoformat(timespec="seconds"),
                        )
                else:
                    terminal = self._snapshot_for(
                        state="aborted",
                        target=target,
                        reason="application_shutdown",
                        started_at=started_at,
                        finished_at=self.clock().isoformat(timespec="seconds"),
                    )
        with self._lock:
            if generation == self._generation:
                terminal["accepted"] = False
                if terminal.get("trade_date") is None:
                    terminal["trade_date"] = target.isoformat()
                if terminal.get("started_at") is None:
                    terminal["started_at"] = started_at
                self._snapshot = terminal
                self._worker = None
            if (
                self._startup_generation == generation
                and self._startup_event is not None
                and self._startup_result is not None
            ):
                self._startup_result.setdefault("ready", False)
                self._startup_event.set()
                self._startup_event = None
                self._startup_result = None
                self._startup_generation = None
            if self._terminal_generation == generation and self._terminal_event is not None:
                self._terminal_event.set()
                self._terminal_event = None
                self._terminal_generation = None
            if self._lease_generation == generation:
                self._release_lease_locked()
                self._lease_generation = None

    def start(self, now=None) -> dict[str, object]:
        """Accept one full pipeline run, or return a safe stable outcome."""

        current = now or self.clock()
        target = current.date()
        cutoff = datetime.datetime.combine(target, DAILY_UPDATE_TIME)
        with self._lock:
            worker = self._worker
            if worker is not None and worker.is_alive():
                result = dict(self._snapshot)
                result["accepted"] = False
                result["reason"] = "already_running"
                return result
            if current < cutoff:
                self._snapshot = self._snapshot_for(
                    state="skip",
                    target=target,
                    reason="before_cutoff",
                    finished_at=current.isoformat(timespec="seconds"),
                )
                return dict(self._snapshot)
            previous = self._recover_stale_status_locked(current, target)
            if previous.get("state") == "running":
                self._snapshot = dict(previous)
                self._snapshot["accepted"] = False
                self._snapshot["reason"] = "already_running"
                return dict(self._snapshot)
            previous_mode = previous.get("mode")
            previous_success = (
                previous.get("state") == "portal_success"
                if self.mode == "portal_only"
                else previous.get("state") == "success"
            )
            if (
                previous.get("trade_date") == target.isoformat()
                and previous_mode == self.mode
                and previous_success
            ):
                self._snapshot = previous
                self._snapshot["reason"] = "already_success"
                return dict(self._snapshot)
            if (
                previous.get("trade_date") == target.isoformat()
                and previous_mode == self.mode
                and previous.get("reason") in _MANUAL_UPDATE_IDEMPOTENT_REASONS
                and previous.get("state") in {"skip", "failure"}
            ):
                self._snapshot = previous
                return dict(self._snapshot)

            try:
                base = Path(self.base_dir_fn())
            except Exception:  # noqa: BLE001 - never expose resolver details to the UI
                self._snapshot = self._snapshot_for(
                    state="failure",
                    target=target,
                    reason="update_failed",
                    finished_at=current.isoformat(timespec="seconds"),
                )
                return dict(self._snapshot)

            try:
                acquired = self._acquire_lease_locked()
            except OSError:  # noqa: BLE001 - no raw filesystem details leave the API
                self._snapshot = self._snapshot_for(
                    state="failure",
                    target=target,
                    reason="update_failed",
                    finished_at=current.isoformat(timespec="seconds"),
                )
                return dict(self._snapshot)
            if not acquired:
                self._snapshot = self._snapshot_for(
                    state="skip",
                    target=target,
                    reason="lock_busy",
                    finished_at=current.isoformat(timespec="seconds"),
                )
                return dict(self._snapshot)

            self._stop_event = threading.Event()
            self._generation += 1
            generation = self._generation
            self._lease_generation = generation
            self._job_id = uuid.uuid4().hex
            startup_event = None
            startup_result = None
            terminal_event = None
            if self.mode == "portal_only" and not self._uses_default_run_fn:
                startup_event = threading.Event()
                startup_result = {}
                terminal_event = threading.Event()
                self._startup_event = startup_event
                self._startup_result = startup_result
                self._startup_generation = generation
                self._terminal_event = terminal_event
                self._terminal_generation = generation
            self._snapshot = self._snapshot_for(
                state="accepted",
                target=target,
                reason="accepted",
                started_at=current.isoformat(timespec="seconds"),
                progress={"completed": 0, "total": 10, "current": None},
            )
            try:
                self._write_running_status_locked(
                    target=target,
                    state="accepted",
                    reason="accepted",
                    started_at=current.isoformat(timespec="seconds"),
                    progress=self._snapshot["progress"],
                )
                worker = self.thread_factory(
                    target=self._run,
                    args=(generation, base, target),
                    name="qtrade-manual-update",
                    daemon=True,
                )
                self._worker = worker
                worker.start()
            except Exception:  # noqa: BLE001 - stable API failure, no raw error
                if startup_event is not None:
                    startup_result["ready"] = False
                    startup_event.set()
                    self._startup_event = None
                    self._startup_result = None
                    self._startup_generation = None
                if terminal_event is not None:
                    terminal_event.set()
                    self._terminal_event = None
                    self._terminal_generation = None
                self._worker = None
                self._release_lease_locked()
                self._lease_generation = None
                try:
                    self._write_terminal_status_locked(
                        target=target,
                        state="failure",
                        reason="update_failed",
                        started_at=current.isoformat(timespec="seconds"),
                    )
                except OSError:
                    pass
                self._snapshot = self._snapshot_for(
                    state="failure",
                    target=target,
                    reason="update_failed",
                    finished_at=current.isoformat(timespec="seconds"),
                )
            result = dict(self._snapshot)
        if startup_event is not None:
            if not startup_event.wait(timeout=MANUAL_UPDATE_STARTUP_TIMEOUT_SECONDS):
                self.stop(timeout=MANUAL_UPDATE_STOP_TIMEOUT_SECONDS)
                terminal_event.wait(timeout=MANUAL_UPDATE_STOP_TIMEOUT_SECONDS)
                return self.status()
            if startup_result.get("ready") is not True:
                if not terminal_event.wait(timeout=MANUAL_UPDATE_STOP_TIMEOUT_SECONDS):
                    self.stop(timeout=MANUAL_UPDATE_STOP_TIMEOUT_SECONDS)
                    terminal_event.wait(timeout=MANUAL_UPDATE_STOP_TIMEOUT_SECONDS)
                return self.status()
        return result

    def status(self) -> dict[str, object]:
        with self._lock:
            snapshot = dict(self._snapshot)
            if self._worker is not None and self._worker.is_alive():
                snapshot["accepted"] = snapshot.get("state") == "accepted"
                if snapshot.get("state") in {"accepted", "running"}:
                    disk = read_manual_update_status(self.status_file)
                    if disk.get("state") == "running":
                        for key in (
                            "outputs",
                            "freshness",
                            "retry",
                            "step",
                            "heartbeat_at",
                            "elapsed_seconds",
                            "progress",
                        ):
                            snapshot[key] = disk[key]
            elif snapshot.get("state") == "idle":
                disk = read_manual_update_status(self.status_file)
                if disk.get("state") != "idle":
                    snapshot = disk
            snapshot["accepted"] = snapshot.get("state") == "accepted"
            return snapshot

    def stop(self, timeout: float = 2.0) -> dict[str, object]:
        join_timeout = min(
            MANUAL_UPDATE_STOP_TIMEOUT_SECONDS,
            max(0.0, float(timeout)),
        )
        stopped_target = None
        stopped_started_at = None
        stop_requested = False
        with self._lock:
            self._stop_event.set()
            worker = self._worker
            if (
                worker is not None
                and worker.is_alive()
                and self._snapshot.get("state") in {"accepted", "running"}
            ):
                self._generation += 1
                stopped_target = self._snapshot.get("trade_date")
                stopped_started_at = self._snapshot.get("started_at")
                stop_requested = True
                self._snapshot = self._snapshot_for(
                    state="aborted",
                    target=stopped_target,
                    reason="application_shutdown",
                    started_at=stopped_started_at,
                    finished_at=self.clock().isoformat(timespec="seconds"),
                    step=self._snapshot.get("step"),
                    progress=self._snapshot.get("progress"),
                    elapsed_seconds=self._snapshot.get("elapsed_seconds", 0.0),
                )
                self._write_terminal_status_locked(
                    target=stopped_target,
                    state="aborted",
                    reason="application_shutdown",
                    started_at=stopped_started_at,
                )
                if self._terminal_generation is not None and self._terminal_event is not None:
                    self._terminal_event.set()
                    self._terminal_event = None
                    self._terminal_generation = None
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=join_timeout)
        with self._lock:
            if self._worker is not None and not self._worker.is_alive():
                self._worker = None
            if worker is None or not worker.is_alive():
                self._release_lease_locked()
                self._lease_generation = None
            if stop_requested:
                # Reassert the terminal record after the managed child has
                # exited so a late status write cannot win the race.
                self._write_terminal_status_locked(
                    target=stopped_target,
                    state="aborted",
                    reason="application_shutdown",
                    started_at=stopped_started_at,
                )
            return dict(self._snapshot)


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
    log_file: Path | None = None,
    clock=None,
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
    state_root_value = environment.get("QTRADE_UPDATE_STATE_DIR")
    state_root = Path(state_root_value).expanduser() if state_root_value else None
    effective_status = Path(status_file) if status_file is not None else None
    if effective_status is None and state_root is not None:
        effective_status = state_root / "daily_update_1830.status.json"
    if effective_status is None:
        effective_status = root / "logs" / "daily_update_1830.status.json"
    effective_log = Path(log_file) if log_file is not None else effective_status.with_name("daily_update_1830.log")
    shared_lock = effective_status.with_name("daily_update_1830.manual.lock")
    active_process = {}

    def stop_active_process() -> None:
        process = active_process.get("process")
        if process is not None:
            _terminate_managed_process(process, processes)

    global _AUTO_UPDATE_SCHEDULER, _AUTO_UPDATE_THREAD
    with _AUTO_UPDATE_LOCK:
        if _AUTO_UPDATE_THREAD is not None and _AUTO_UPDATE_THREAD.is_alive():
            return _AUTO_UPDATE_SCHEDULER

        stop_event = threading.Event()

        def invoke(target: datetime.date) -> int:
            previous = read_manual_update_status(effective_status)
            if (
                previous.get("mode") == "portal_only"
                and previous.get("state") == "portal_success"
                and previous.get("trade_date") == target.isoformat()
            ):
                print("[auto-update] 当天门户已刷新，自动全量更新跳过", flush=True)
                return 0
            _reclaim_dead_lease(shared_lock)
            lease = _UpdateLease(shared_lock)
            if not lease.acquire():
                print("[auto-update] 手动或自动更新正在运行，跳过本次自动任务", flush=True)
                return 1
            try:
                return run_daily_update(
                    base,
                    target,
                    environment=environment,
                    subprocess_module=processes,
                    project_root=root,
                    status_file=effective_status,
                    log_file=effective_log,
                    python_executable=python_executable,
                    stop_event=stop_event,
                    interruptible=True,
                    process_holder=active_process,
                )
            finally:
                lease.release()

        scheduler = DailyUpdateScheduler(
            invoke,
            clock=clock,
            stop_event=stop_event,
            stop_hook=stop_active_process,
        )
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
