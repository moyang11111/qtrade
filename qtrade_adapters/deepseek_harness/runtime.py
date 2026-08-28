"""Optional HARNESS detection and app-lifecycle daily update scheduling."""

from __future__ import annotations

import datetime
import json
import os
import signal
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
MANUAL_UPDATE_PROCESS_POLL_SECONDS = 0.1
MANUAL_UPDATE_STOP_TIMEOUT_SECONDS = 2.0
_AUTO_UPDATE_LOCK = threading.Lock()
_AUTO_UPDATE_SCHEDULER = None
_AUTO_UPDATE_THREAD = None
_MANUAL_UPDATE_TERMINAL_STATES = frozenset({"success", "skip", "failure"})
_MANUAL_UPDATE_REASONS = frozenset({
    "accepted",
    "running",
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
})
_MANUAL_UPDATE_FRESHNESS_GROUPS = ("portal", "factors", "decision", "sync")
_MANUAL_UPDATE_FRESHNESS_SOURCES = frozenset({
    "external_sqlite",
    "factor_artifacts",
    "decision_artifact",
    "sync_target",
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
        "stdout": getattr(processes, "DEVNULL", subprocess.DEVNULL),
        "stderr": getattr(processes, "DEVNULL", subprocess.DEVNULL),
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
        print(f"[auto-update] daily_update_1830 启动失败：{error}", flush=True)
        return 1, False

    deadline = time.monotonic() + max(0.0, float(timeout))
    interrupted = False
    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                return int(returncode), interrupted
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
        print(f"[auto-update] daily_update_1830 等待失败：{error}", flush=True)
        interrupted = True
        _terminate_managed_process(process, processes)
        return 1, interrupted


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
    interruptible: bool = False,
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
            if interruptible:
                returncode, interrupted = _run_interruptible_command(
                    command,
                    cwd=root,
                    environment=process_env,
                    processes=processes,
                    stop_event=stop_event,
                )
                if interrupted:
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
            print(f"[auto-update] daily_update_1830 启动失败：{error}", flush=True)
            return 1
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


def read_manual_update_status(path: Path) -> dict[str, object]:
    """Read only the safe fields needed by the native manual-update console."""

    outputs = {"portal": False, "factors": False, "decision": False, "sync": False}
    fallback = {
        "schema_version": 1,
        "state": "idle",
        "trade_date": None,
        "started_at": None,
        "finished_at": None,
        "reason": "status_unavailable",
        "outputs": outputs,
        "freshness": {},
        "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
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
    result["state"] = state
    result["trade_date"] = _safe_date_text(payload.get("trade_date"))
    result["started_at"] = _safe_timestamp_text(payload.get("started_at"))
    result["finished_at"] = _safe_timestamp_text(payload.get("finished_at"))
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
        clock=None,
        run_fn=None,
        thread_factory=None,
        subprocess_module=None,
    ):
        self.base_dir_fn = base_dir_fn or config.resolve_base_dir
        self.project_root = config.PROJECT_ROOT if project_root is None else Path(project_root)
        self.status_file = Path(
            status_file or self.project_root / "logs" / "daily_update_1830.status.json"
        )
        self.lock_path = Path(
            lock_path or self.project_root / "logs" / "daily_update_1830.manual.lock"
        )
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
        self._snapshot = self._idle_snapshot()

    @staticmethod
    def _outputs():
        return {"portal": False, "factors": False, "decision": False, "sync": False}

    def _idle_snapshot(self):
        return {
            "schema_version": 1,
            "accepted": False,
            "state": "idle",
            "trade_date": None,
            "started_at": None,
            "finished_at": None,
            "reason": "status_unavailable",
            "outputs": self._outputs(),
            "freshness": {},
            "retry": {"attempt": 0, "max_attempts": 3, "next_attempt_at": None},
        }

    def _snapshot_for(self, *, state, target=None, reason="status_unavailable", started_at=None):
        snapshot = self._idle_snapshot()
        snapshot.update({
            "accepted": state == "accepted",
            "state": state,
            "trade_date": _safe_date_text(target),
            "reason": _safe_manual_reason(reason, state),
            "started_at": _safe_timestamp_text(started_at),
        })
        return snapshot

    def _set_terminal_from_disk(self, returncode, target):
        disk = read_manual_update_status(self.status_file)
        if (
            disk["trade_date"] == target.isoformat()
            and disk["state"] in _MANUAL_UPDATE_TERMINAL_STATES
        ):
            return disk
        return self._snapshot_for(
            state="failure",
            reason="status_unavailable" if returncode == 0 else "update_failed",
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
            )
            self._snapshot = running
        try:
            run_kwargs = {
                "environment": os.environ,
                "project_root": self.project_root,
                "status_file": self.status_file,
                "python_executable": sys.executable,
                "stop_event": self._stop_event,
            }
            if self._uses_default_run_fn:
                run_kwargs["interruptible"] = True
                if self.subprocess_module is not None:
                    run_kwargs["subprocess_module"] = self.subprocess_module
            returncode = self.run_fn(
                base,
                target,
                **run_kwargs,
            )
            terminal = self._set_terminal_from_disk(int(returncode or 0), target)
        except Exception:  # noqa: BLE001 - UI receives only a stable failure state
            terminal = self._snapshot_for(
                state="failure",
                target=target,
                reason="update_failed",
                started_at=started_at,
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
                    state="skip", target=target, reason="before_cutoff"
                )
                return dict(self._snapshot)
            previous = read_manual_update_status(self.status_file)
            if (
                previous.get("trade_date") == target.isoformat()
                and previous.get("state") == "success"
            ):
                self._snapshot = previous
                self._snapshot["reason"] = "already_success"
                return dict(self._snapshot)
            if (
                previous.get("trade_date") == target.isoformat()
                and previous.get("reason") in _MANUAL_UPDATE_IDEMPOTENT_REASONS
                and previous.get("state") in {"skip", "failure"}
            ):
                self._snapshot = previous
                return dict(self._snapshot)

            try:
                base = Path(self.base_dir_fn())
            except Exception:  # noqa: BLE001 - never expose resolver details to the UI
                self._snapshot = self._snapshot_for(
                    state="failure", target=target, reason="update_failed"
                )
                return dict(self._snapshot)

            try:
                acquired = self._acquire_lease_locked()
            except OSError:  # noqa: BLE001 - no raw filesystem details leave the API
                self._snapshot = self._snapshot_for(
                    state="failure", target=target, reason="update_failed"
                )
                return dict(self._snapshot)
            if not acquired:
                self._snapshot = self._snapshot_for(
                    state="skip", target=target, reason="lock_busy"
                )
                return dict(self._snapshot)

            self._stop_event = threading.Event()
            self._generation += 1
            generation = self._generation
            self._lease_generation = generation
            self._snapshot = self._snapshot_for(
                state="accepted",
                target=target,
                reason="accepted",
                started_at=current.isoformat(timespec="seconds"),
            )
            try:
                worker = self.thread_factory(
                    target=self._run,
                    args=(generation, base, target),
                    name="qtrade-manual-update",
                    daemon=True,
                )
                self._worker = worker
                worker.start()
            except Exception:  # noqa: BLE001 - stable API failure, no raw error
                self._worker = None
                self._release_lease_locked()
                self._lease_generation = None
                self._snapshot = self._snapshot_for(
                    state="failure", target=target, reason="update_failed"
                )
            return dict(self._snapshot)

    def status(self) -> dict[str, object]:
        with self._lock:
            snapshot = dict(self._snapshot)
            if self._worker is not None and self._worker.is_alive():
                snapshot["accepted"] = snapshot.get("state") == "accepted"
                if snapshot.get("state") in {"accepted", "running"}:
                    disk = read_manual_update_status(self.status_file)
                    if disk.get("state") == "running":
                        for key in ("outputs", "freshness", "retry"):
                            snapshot[key] = disk[key]
            elif snapshot.get("state") == "idle":
                disk = read_manual_update_status(self.status_file)
                if disk.get("state") != "idle":
                    snapshot = disk
            snapshot["accepted"] = snapshot.get("state") == "accepted"
            return snapshot

    def stop(self, timeout: float = 2.0) -> None:
        join_timeout = min(
            MANUAL_UPDATE_STOP_TIMEOUT_SECONDS,
            max(0.0, float(timeout)),
        )
        with self._lock:
            self._stop_event.set()
            worker = self._worker
            if worker is not None and worker.is_alive():
                self._generation += 1
                if self._snapshot.get("state") in {"accepted", "running"}:
                    self._snapshot = self._snapshot_for(
                        state="failure",
                        target=self._snapshot.get("trade_date"),
                        reason="update_failed",
                    )
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=join_timeout)
        with self._lock:
            if self._worker is not None and not self._worker.is_alive():
                self._worker = None
            if worker is None or not worker.is_alive():
                self._release_lease_locked()
                self._lease_generation = None


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
