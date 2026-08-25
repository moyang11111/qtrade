"""Optional HARNESS process detection/start and daily update scheduling."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config


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
    port = config.HARNESS_PORT if harness_port is None else harness_port
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


def maybe_auto_update(
    *,
    base_dir_fn=None,
    env=None,
    subprocess_module=None,
    os_name: str | None = None,
    today_fn=None,
    python_executable: str | None = None,
):
    """Optionally schedule the existing once-per-day incremental update."""

    environment = os.environ if env is None else env
    resolve_base = base_dir_fn or config.resolve_base_dir
    processes = subprocess if subprocess_module is None else subprocess_module
    platform_name = os.name if os_name is None else os_name
    current_day = today_fn or (lambda: time.strftime("%Y-%m-%d"))
    if environment.get("QTRADE_NO_AUTOUPDATE"):
        print("[auto-update] 已通过 QTRADE_NO_AUTOUPDATE 关闭自动增量")
        return
    base = resolve_base()
    if not (base / "logs" / "pipeline_full_v2_done.txt").exists():
        print("[auto-update] 全量回填未完成，跳过自动增量（等 run_pipeline_full_v2.py 跑完即可启用）")
        return
    marker = base / "data" / "cache" / "last_auto_update.txt"
    today = current_day()
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
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
