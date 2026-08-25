# -*- coding: utf-8 -*-
"""每个交易日 18:30 的自动数据更新程序。

流程：
  1. 跳过周末（周一~周五执行）
  2. akshare 增量更新日线（当天收盘数据）
  3. 因子池引擎刷新（IC/排行榜/ui_pack/dash）
  4. 机会扫描 --pitch + Pitch v2
  5. 同步到 Roaming 运行副本
  6. 写 logs/daily_update_1830.log

用法：
  python scripts/daily_update_1830.py          # 正常执行
  python scripts/daily_update_1830.py --dry    # 只检查交易日并打印要跑的命令
"""
import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "third_party" / "deepseek-harness-quant"
DECK_ENV = "QTRADE_DECK_DIR"
PY = sys.executable or "python"
LOG = ROOT / "logs" / "daily_update_1830.log"


def log(msg):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def resolve_deck_dir(cli_value=None):
    """按 CLI > 环境变量 > 项目默认路径选择底座目录。"""
    if cli_value:
        return Path(cli_value).expanduser()
    env_value = os.environ.get(DECK_ENV)
    if env_value:
        return Path(env_value).expanduser()
    return Path(DECK)


def run(cmd, dry, deck_dir=None):
    log("RUN: " + " ".join(str(x) for x in cmd))
    if dry:
        return True
    try:
        r = subprocess.run(cmd, cwd=str(deck_dir or DECK), timeout=7200)
        if r.returncode != 0:
            log(f"FAIL: 步骤返回 {r.returncode}: {' '.join(str(x) for x in cmd)}")
            return False
    except Exception as e:
        log(f"FAIL: 步骤执行异常：{' '.join(str(x) for x in cmd)}：{e}")
        return False
    return True


def main(argv=None, *, today=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", "--dry-run", dest="dry", action="store_true", help="只检查日期并列出命令")
    ap.add_argument("--deck-dir", help=f"底座目录（优先于 {DECK_ENV}，默认项目内 third_party 路径）")
    args = ap.parse_args(argv)

    today = today or datetime.date.today()
    if today.weekday() >= 5:
        log(f"今天 {today} 是周末，跳过自动更新")
        return 0
    log(f"交易日更新开始：{today}（{'DRY' if args.dry else 'REAL'}）")

    deck = resolve_deck_dir(args.deck_dir)
    if not deck.exists():
        log(f"FAIL: 底座不存在 {deck}")
        return 1

    def run_required(cmd):
        if run(cmd, args.dry, deck):
            return True
        log("FAIL: 已停止后续步骤")
        return False

    if not run_required([PY, "-X", "utf8", str(deck / "scripts" / "auto_update_daily.py")]):
        return 1
    if not run_required([PY, "-X", "utf8", str(deck / "scripts" / "build_factor_pool_engine.py")]):
        return 1
    if not run_required([PY, "-X", "utf8", str(deck / "factors" / "opportunities" / "scan.py"), "--pitch"]):
        return 1

    # 取最新 opp_pool 跑 pitch_v2
    opps = sorted((deck / "logs").glob("opp_pool_*.json"), key=lambda p: p.stat().st_mtime)
    if opps:
        if not run_required([PY, "-X", "utf8", str(deck / "factors" / "opportunities" / "pitch_v2.py"),
                             "--pool", str(opps[-1])]):
            return 1
    else:
        log("WARN: 没有找到 opp_pool_*.json，跳过 pitch_v2")
    if not run_required([PY, "-X", "utf8", str(deck / "scripts" / "sync_data_to_roaming.py")]):
        return 1

    log("=== 交易日更新完成 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
