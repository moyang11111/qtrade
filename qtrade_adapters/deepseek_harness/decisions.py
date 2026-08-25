"""Decision persistence and optional DeepSeek portfolio synchronization."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from . import config


def decide(
    handler,
    rec,
    *,
    auto_paper=None,
    service=None,
    base_dir_fn=None,
    prepare_sys_path_fn=None,
    background_sync_fn=None,
):
    """Preserve the existing decision write/response and background boundary."""

    resolve_base = base_dir_fn or config.resolve_base_dir
    prepare_sys_path = prepare_sys_path_fn or config.prepare_sys_path
    base = resolve_base()
    prepare_sys_path(base)
    if rec.get("action") == "buy" and auto_paper is not None:
        try:
            auto_paper.buy_from_decision(service, rec)
            held = {p.get("symbol") for p in auto_paper.status(service).get("positions", [])}
            if rec.get("code") not in held:
                error = auto_paper.state.get("last_error") or "决策买入未成交"
                handler._json({"ok": False, "error": error})
                return
            print(f"[decide] 已合入统一模拟盘: {rec.get('code')}")
        except Exception as error:
            handler._json({"ok": False, "error": f"统一模拟盘买入失败: {error}"})
            return

    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    files = sorted(logs.glob("deck_decisions_*.json"), key=lambda path: path.stat().st_mtime)
    history = []
    if files:
        try:
            history = json.loads(files[-1].read_text(encoding="utf-8"))
        except Exception:
            history = []
    if not isinstance(history, list):
        history = []
    history.append(rec)
    output = logs / f"deck_decisions_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    if rec.get("action") == "buy":
        target = background_sync_fn or decide_bg_sync
        threading.Thread(
            target=target,
            args=(base, rec, None, None),
            daemon=True,
        ).start()
    handler._json({"ok": True, "saved": output.name, "action": rec.get("action")})


def decide_bg_sync(base, rec=None, auto_paper=None, service=None):
    """Synchronize the optional portfolio/forward pool without blocking HTTP."""

    sys.modules.pop("factors", None)
    if rec is not None and rec.get("action") == "buy":
        try:
            if auto_paper is not None:
                auto_paper.buy_from_decision(service, rec)
                print(f"[decide] 已合入统一模拟盘: {rec.get('code')}")
        except Exception as error:
            print(f"[decide] 统一模拟盘买入失败: {error}")
    try:
        from strategy.portfolio import sync_from_decisions

        sync_from_decisions()
        print("[decide] 已同步决策模拟持仓（持股≤5）")
    except Exception as error:
        print(f"[decide] 持仓同步失败: {error}")
    try:
        import glob as _glob

        pitch_files = sorted(
            _glob.glob(str(Path(base) / "logs" / "pitch_v2*.json")),
            key=lambda path: Path(path).stat().st_mtime,
        )
        if pitch_files:
            from factors.opportunities.pitch_track import append_pitch

            append_pitch(pitch_files[-1])
            print("[decide] 已写入远期池")
    except Exception as error:
        print(f"[decide] 远期池联动失败: {error}")
