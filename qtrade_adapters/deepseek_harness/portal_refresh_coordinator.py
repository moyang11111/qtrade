"""Production coordinator for the portal-only refresh slice.

The resumable worker is deliberately run in a QTrade-owned child process.
This module does not accept a client command, path, date, or provider option;
the server supplies the trusted inputs and the child validates them again.
Only the portal snapshot is produced here.  Factors, decisions, and sync are
reserved for the following pipeline layer.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Callable, Mapping

from .portal_refresh import PortalRefreshError, portal_refresh_paths
from .portal_refresh_provider import PortalPlanError, build_trusted_plan
from .portal_refresh_worker import PortalRefreshWorker
from . import runtime


COORDINATOR_TIMEOUT_SECONDS = 7200.0
COORDINATOR_POLL_SECONDS = 0.5
STARTUP_ACK_TIMEOUT_SECONDS = 2.0
_JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_STATES = frozenset({"accepted", "running", "portal_success", "skip", "failure", "aborted", "timed_out"})
_SAFE_REASONS = frozenset({
    "accepted",
    "running",
    "portal_completed",
    "portal_refresh_failed",
    "calendar_closed",
    "calendar_unavailable",
    "weekend",
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
    "aborted",
    "application_shutdown",
    "process_timeout",
})


def _now() -> str:
    return _datetime.datetime.now().isoformat(timespec="seconds")


def _safe_reason(value: object, *, state: str) -> str:
    if isinstance(value, str) and value in _SAFE_REASONS:
        return value
    if state == "portal_success":
        return "portal_completed"
    if state == "skip":
        return "calendar_closed"
    if state == "aborted":
        return "aborted"
    if state == "timed_out":
        return "process_timeout"
    if state == "failure":
        return "portal_refresh_failed"
    return "status_unavailable"


def _safe_progress(value: Mapping[str, object] | None) -> dict[str, object]:
    value = value if isinstance(value, Mapping) else {}
    completed = value.get("completed")
    total = value.get("total")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        completed = 0
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        total = 0
    current = value.get("current")
    if not isinstance(current, str) or len(current) > 48:
        current = None
    return {"completed": min(completed, 5000), "total": min(total, 5000), "current": current}


def _safe_outputs(portal: bool = False) -> dict[str, bool]:
    return {"portal": bool(portal), "factors": False, "decision": False, "sync": False}


def _safe_freshness(
    *,
    verified: bool,
    target: str | None,
    total: int = 0,
    reason: str = "unavailable",
) -> dict[str, object]:
    return {
        "portal": {
            "verified": bool(verified),
            "as_of": target,
            "source": "qtrade_mirror" if verified else "unavailable",
            "reason": reason if reason in {"verified", "unavailable", "database_unavailable"} else "unavailable",
            "total": max(0, min(int(total), 5000)),
            "coverage": max(0, min(int(total), 5000)),
        }
    }


def _status_payload(
    *,
    state: str,
    reason: str,
    target: str | None,
    job_id: str,
    started_at: str | None,
    finished_at: str | None = None,
    progress: Mapping[str, object] | None = None,
    portal_verified: bool = False,
    total: int = 0,
    retry: Mapping[str, object] | None = None,
    universe_token: str | None = None,
    generation: str | None = None,
    content_sha256: str | None = None,
) -> dict[str, object]:
    safe_state = state if state in _SAFE_STATES else "failure"
    safe_retry = {"attempt": 0, "max_attempts": 3, "next_attempt_at": None}
    if isinstance(retry, Mapping):
        for key in ("attempt", "max_attempts"):
            value = retry.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe_retry[key] = min(value, 3)
    current = finished_at or _now()
    output_meta = {}
    if (
        isinstance(target, str)
        and _JOB_ID_RE.fullmatch(job_id) is not None
        and isinstance(universe_token, str)
        and _TOKEN_RE.fullmatch(universe_token) is not None
        and isinstance(generation, str)
        and _TOKEN_RE.fullmatch(generation) is not None
        and isinstance(content_sha256, str)
        and _TOKEN_RE.fullmatch(content_sha256) is not None
    ):
        output_meta = {
            "portal": {
                "generation": generation,
                "content_sha256": content_sha256,
                "universe_token": universe_token,
                "target_date": target,
                "total": max(0, min(int(total), 5000)),
            },
        }
    return {
        "schema_version": 1,
        "mode": "portal_only",
        "accepted": safe_state == "accepted",
        "state": safe_state,
        "trade_date": target,
        "started_at": started_at,
        "finished_at": finished_at,
        "reason": _safe_reason(reason, state=safe_state),
        "step": "portal",
        "steps": [{"name": "portal", "state": "success" if portal_verified else safe_state}],
        "outputs": _safe_outputs(portal_verified),
        "freshness": _safe_freshness(
            verified=portal_verified,
            target=target if portal_verified else None,
            total=total,
            reason="verified" if portal_verified else "unavailable",
        ),
        "output_meta": output_meta,
        "retry": safe_retry,
        "job_id": job_id,
        "heartbeat_at": current,
        "elapsed_seconds": 0.0,
        "progress": _safe_progress(progress),
    }


def _ready_path(state_dir: Path, job_id: str) -> Path:
    if _JOB_ID_RE.fullmatch(job_id) is None:
        raise PortalRefreshError("status_path_invalid")
    return state_dir / f".portal_refresh.ready.{job_id}"


def _write_ready(path: Path, job_id: str) -> bool:
    try:
        runtime._atomic_write_json(path, {"job_id": job_id, "ready": True})
        return True
    except (OSError, TypeError, ValueError):
        return False


def _read_ready(path: Path, job_id: str) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload == {"job_id": job_id, "ready": True}
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def _remove_ready(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        _safe_log_event(path.parent / "daily_update_1830.log", "ready_cleanup_failed")


def _safe_log_event(path: Path, event: str, *, count: int | None = None) -> None:
    """Append only fixed diagnostic categories, never child output."""

    payload = {"event": event, "at": _now()}
    if isinstance(count, int) and count >= 0:
        payload["bytes"] = min(count, 16 * 1024 * 1024)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _drain_child_output(stream, log_file: Path, done: threading.Event) -> None:
    count = 0
    try:
        while True:
            chunk = stream.readline(4096)
            if not chunk:
                break
            count += len(chunk)
    except (AttributeError, OSError, ValueError):
        pass
    finally:
        _safe_log_event(log_file, "child_output", count=count)
        done.set()


def _validate_runtime_paths(
    *,
    state_dir: str | Path,
    user_data_dir: str | Path,
    status_file: str | Path,
    log_file: str | Path,
):
    paths = portal_refresh_paths(state_dir, user_data_dir=user_data_dir)
    expected_status = paths.state / "daily_update_1830.status.json"
    expected_log = paths.state / "daily_update_1830.log"
    if Path(status_file).resolve() != expected_status.resolve() or Path(log_file).resolve() != expected_log.resolve():
        raise PortalRefreshError("status_path_invalid")
    paths.state.mkdir(parents=True, exist_ok=True)
    return paths


def _write_status(
    status_file: Path,
    payload: Mapping[str, object],
    *,
    expected_job_id: str | None = None,
) -> bool:
    if expected_job_id is not None:
        try:
            current = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            current = None
        if isinstance(current, Mapping) and current.get("job_id") not in {None, expected_job_id}:
            return False
    try:
        runtime._atomic_write_json(status_file, dict(payload))
    except (OSError, TypeError, ValueError):
        return False
    return True


def _worker_status(user_data_dir: Path, state_dir: Path) -> dict[str, object]:
    try:
        return PortalRefreshWorker(user_data_dir=user_data_dir, state_dir=state_dir).status()
    except Exception:  # noqa: BLE001 - only a safe progress fallback is exposed
        return {"state": "failure", "reason": "checkpoint_corrupt", "total": 0, "completed": 0}


def _worker_reason(value: object) -> str:
    return value if isinstance(value, str) and value in _SAFE_REASONS else "portal_refresh_failed"


def _run_child_job(
    *,
    base_dir: str | Path,
    target_date: str,
    state_dir: str | Path,
    user_data_dir: str | Path,
    status_file: str | Path,
    job_id: str,
    ready_file: str | Path | None = None,
    plan_builder: Callable[..., tuple[object, object]] = build_trusted_plan,
    worker_factory: Callable[..., PortalRefreshWorker] = PortalRefreshWorker,
) -> int:
    """Build and execute one trusted portal job in the coordinator child."""

    state_path = Path(state_dir)
    user_path = Path(user_data_dir)
    status_path = Path(status_file)
    started_at = _now()
    ready_path = None
    try:
        _validate_runtime_paths(
            state_dir=state_path,
            user_data_dir=user_path,
            status_file=status_path,
            log_file=state_path / "daily_update_1830.log",
        )
        if ready_file is not None:
            ready_path = _ready_path(state_path, job_id)
            if Path(ready_file).resolve() != ready_path.resolve():
                raise PortalRefreshError("status_path_invalid")
        target = _datetime.date.fromisoformat(target_date).isoformat()
        if not _write_status(
            status_path,
            _status_payload(
                state="running", reason="running", target=target, job_id=job_id,
                started_at=started_at, progress={"completed": 0, "total": 0, "current": "portal"},
            ),
            expected_job_id=job_id,
        ):
            raise PortalRefreshError("checkpoint_io")
        if ready_path is not None and not _write_ready(ready_path, job_id):
            raise PortalRefreshError("checkpoint_io")
        try:
            plan, provider = plan_builder(base_dir=base_dir, target_date=target)
        except PortalPlanError as error:
            state = "skip" if error.reason in {"weekend", "calendar_closed"} else "failure"
            _write_status(
                status_path,
                _status_payload(
                    state=state, reason=error.reason, target=target, job_id=job_id,
                    started_at=started_at, finished_at=_now(),
                ),
                expected_job_id=job_id,
            )
            return 0 if state == "skip" else 1
        worker = worker_factory(
            user_data_dir=user_path,
            state_dir=state_path,
            provider=provider,
        )
        result = worker.run(plan, provider=provider)
        state = result.get("state") if isinstance(result, Mapping) else "failure"
        reason = _worker_reason(result.get("reason") if isinstance(result, Mapping) else None)
        total = result.get("total", 0) if isinstance(result, Mapping) else 0
        completed = result.get("completed", 0) if isinstance(result, Mapping) else 0
        progress = {"completed": completed, "total": total, "current": None}
        if state == "success":
            generation = result.get("published_generation")
            content_sha256 = result.get("published_content_sha256")
            if (
                not isinstance(generation, str)
                or _TOKEN_RE.fullmatch(generation) is None
                or not isinstance(content_sha256, str)
                or _TOKEN_RE.fullmatch(content_sha256) is None
            ):
                _write_status(
                    status_path,
                    _status_payload(
                        state="failure", reason="publish_failed", target=target, job_id=job_id,
                        started_at=started_at, finished_at=_now(), progress=progress,
                    ),
                    expected_job_id=job_id,
                )
                return 1
            _write_status(
                status_path,
                _status_payload(
                    state="portal_success", reason="portal_completed", target=target, job_id=job_id,
                    started_at=started_at, finished_at=_now(), progress=progress,
                    portal_verified=True, total=total, retry=result.get("retry"),
                    universe_token=plan.universe_token,
                    generation=generation,
                    content_sha256=content_sha256,
                ),
                expected_job_id=job_id,
            )
            return 0
        final_state = "timed_out" if state == "timed_out" else "aborted" if state == "aborted" else "failure"
        _write_status(
            status_path,
            _status_payload(
                state=final_state, reason=reason, target=target, job_id=job_id,
                started_at=started_at, finished_at=_now(), progress=progress,
                total=total, retry=result.get("retry"),
            ),
            expected_job_id=job_id,
        )
        return 1
    except (OSError, ValueError, TypeError, PortalRefreshError):
        _write_status(
            status_path,
            _status_payload(
                state="failure", reason="portal_refresh_failed", target=target_date[:10], job_id=job_id,
                started_at=started_at, finished_at=_now(),
            ),
            expected_job_id=job_id,
        )
        return 1


def _child_environment(environment: Mapping[str, str], base_dir: Path) -> dict[str, str]:
    allowed = {key: value for key, value in environment.items() if key in {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "HOME",
    }}
    allowed.update({
        "QTRADE_BASE_DIR": os.fspath(base_dir),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "QTRADE_PORTAL_REFRESH_CHILD": "1",
    })
    return allowed


def _terminate_child(process: subprocess.Popen) -> None:
    runtime._terminate_managed_process(process, subprocess)


def run_portal_refresh(
    base,
    target,
    *,
    environment=None,
    project_root=None,
    status_file,
    log_file,
    python_executable=None,
    stop_event=None,
    job_id,
    user_data_dir=None,
    startup_event=None,
    startup_result=None,
    deadline_seconds: float = COORDINATOR_TIMEOUT_SECONDS,
    subprocess_module=subprocess,
) -> int:
    """Run a portal refresh child and bridge only safe progress/status fields."""

    if user_data_dir is None:
        return 1
    state_path = Path(status_file).parent
    user_path = Path(user_data_dir)
    try:
        _validate_runtime_paths(
            state_dir=state_path,
            user_data_dir=user_path,
            status_file=status_file,
            log_file=log_file,
        )
        target_text = target.isoformat() if isinstance(target, _datetime.date) else _datetime.date.fromisoformat(str(target)).isoformat()
        deadline = float(deadline_seconds)
        if not 0 < deadline <= COORDINATOR_TIMEOUT_SECONDS:
            return 1
    except (OSError, ValueError, TypeError, PortalRefreshError):
        return 1

    executable = str(python_executable or sys.executable)
    root = Path(project_root or Path(__file__).resolve().parents[2])
    ready_path = _ready_path(state_path, str(job_id))
    command = [
        executable,
        "-X", "utf8",
        "-m", "qtrade_adapters.deepseek_harness.portal_refresh_coordinator",
        "--child",
        "--base-dir", os.fspath(Path(base)),
        "--target-date", target_text,
        "--state-dir", os.fspath(state_path),
        "--user-data-dir", os.fspath(user_path),
        "--status-file", os.fspath(Path(status_file)),
        "--ready-file", os.fspath(ready_path),
        "--job-id", str(job_id),
    ]
    child = None
    output = None
    output_pump = None
    output_pump_done = threading.Event()

    def signal_startup(ready: bool) -> None:
        if isinstance(startup_result, dict):
            startup_result["ready"] = bool(ready)
        if startup_event is not None:
            startup_event.set()

    try:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        output = Path(log_file).open("ab")
        _remove_ready(ready_path)
        popen_kwargs = {
            "cwd": os.fspath(root),
            "env": _child_environment(environment or os.environ, Path(base)),
            "shell": False,
            "stdout": getattr(subprocess_module, "PIPE", output),
            "stderr": subprocess_module.STDOUT,
        }
        if os.name == "nt":
            flags = getattr(subprocess_module, "CREATE_NEW_PROCESS_GROUP", 0)
            flags |= getattr(subprocess_module, "CREATE_NO_WINDOW", 0)
            if flags:
                popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True
        child = subprocess_module.Popen(command, **popen_kwargs)
        stream = getattr(child, "stdout", None)
        if stream is not None:
            output_pump = threading.Thread(
                target=_drain_child_output,
                args=(stream, Path(log_file), output_pump_done),
                name="qtrade-portal-output",
                daemon=True,
            )
            output_pump.start()
        ack_deadline = time.monotonic() + STARTUP_ACK_TIMEOUT_SECONDS
        while not _read_ready(ready_path, str(job_id)):
            if child.poll() is not None or time.monotonic() >= ack_deadline:
                _terminate_child(child)
                _safe_log_event(Path(log_file), "child_start_failed")
                signal_startup(False)
                _write_status(
                    Path(status_file),
                    _status_payload(
                        state="failure", reason="process_timeout", target=target_text,
                        job_id=str(job_id), started_at=None, finished_at=_now(),
                    ),
                    expected_job_id=str(job_id),
                )
                return 1
            if stop_event is not None and stop_event.is_set():
                _terminate_child(child)
                signal_startup(False)
                return 1
            time.sleep(0.02)
        signal_startup(True)
        _safe_log_event(Path(log_file), "child_started")
        started = time.monotonic()
        while True:
            returncode = child.poll()
            progress = _worker_status(user_path, state_path)
            try:
                existing_status = runtime.read_manual_update_status(Path(status_file))
                started_at = existing_status.get("started_at")
            except Exception:  # noqa: BLE001 - only a safe display field is needed
                started_at = None
            worker_state = progress.get("state")
            if returncode is None and worker_state in {"running", "publishing"}:
                _write_status(
                    Path(status_file),
                    _status_payload(
                        state="running", reason="running", target=target_text, job_id=job_id,
                        started_at=started_at, progress={
                            "completed": progress.get("completed", 0),
                            "total": progress.get("total", 0),
                            "current": "portal",
                        },
                        total=progress.get("total", 0), retry=progress.get("retry"),
                    ),
                    expected_job_id=job_id,
                )
            if returncode is not None:
                _safe_log_event(Path(log_file), "child_exited")
                break
            if stop_event is not None and stop_event.is_set():
                _terminate_child(child)
                return 1
            if time.monotonic() - started >= deadline:
                _terminate_child(child)
                _write_status(
                    Path(status_file),
                    _status_payload(
                        state="timed_out", reason="process_timeout", target=target_text,
                        job_id=job_id, started_at=started_at, finished_at=_now(),
                        progress={"completed": progress.get("completed", 0), "total": progress.get("total", 0), "current": "portal"},
                    ),
                    expected_job_id=job_id,
                )
                return 1
            time.sleep(COORDINATOR_POLL_SECONDS)
        progress = _worker_status(user_path, state_path)
        if returncode == 0 and progress.get("state") == "success":
            return 0
        return 1
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        if child is not None:
            _terminate_child(child)
        signal_startup(False)
        _safe_log_event(Path(log_file), "child_start_failed")
        _write_status(
            Path(status_file),
            _status_payload(
                state="failure", reason="portal_refresh_failed", target=str(target)[:10],
                job_id=job_id, started_at=None, finished_at=_now(),
            ),
            expected_job_id=job_id,
        )
        return 1
    finally:
        if output_pump is not None:
            output_pump.join(timeout=0.5)
        _remove_ready(ready_path)
        if output is not None:
            output.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--base-dir", required=True)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--user-data-dir", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args(argv)
    if not args.child:
        return 2
    return _run_child_job(
        base_dir=args.base_dir,
        target_date=args.target_date,
        state_dir=args.state_dir,
        user_data_dir=args.user_data_dir,
        status_file=args.status_file,
        job_id=args.job_id,
        ready_file=args.ready_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["COORDINATOR_TIMEOUT_SECONDS", "run_portal_refresh"]
