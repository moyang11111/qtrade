# -*- coding: utf-8 -*-
"""Qtrade 回归检查：一键验证核心功能 + 底座集成 + 决策审批。

用法：
  python scripts/regression_check.py                # 默认 http://127.0.0.1:8765
  python scripts/regression_check.py --base http://127.0.0.1:8900
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CHECKS = [
    ("health", "/api/health", "GET"),
    ("backtest", "/api/backtest?symbol=000001&strategy=dual_ma&capital=100000&commission=0.03&stop_loss=5&take_profit=15", "GET"),
    ("auto-paper", "/api/auto/paper?action=status", "GET"),
    ("training", "/api/training/next?lookback=60&horizon=5", "GET"),
    ("factors-list", "/api/factors/list", "GET"),
    ("factors-000001", "/api/factors/000001", "GET"),
    ("ai-paper", "/api/ai/paper?action=status", "GET"),
    ("portal-page", "/portal", "GET"),
    ("pitch-page", "/pitch", "GET"),
    ("control-page", "/control", "GET"),
    ("live-opp", "/api/live/opp", "GET"),
    ("pitch_v2", "/api/pitch_v2", "GET"),
    ("decisions-GET", "/api/decisions", "GET"),
    ("brief", "/api/live/brief", "GET"),
    ("turnlow_top", "/api/live/turnlow_top", "GET"),
    ("tech", "/api/live/tech", "GET"),
    ("forward", "/api/live/forward", "GET"),
    ("build_mode", "/api/build_mode", "GET"),
    ("system_live", "/api/system_live", "GET"),
    ("portal_dash", "/api/live/portal_dash", "GET"),
]


def req(base, path, method="GET", body=None, timeout=60):
    url = base + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--with-decisions-post", action="store_true", help="额外测试 POST /api/decisions")
    args = ap.parse_args()

    passed = failed = 0
    for name, path, method in CHECKS:
        status, body = req(args.base, path, method)
        ok = status == 200
        # 对 JSON 接口还检查 body 能解析（非必需）
        if ok and body.strip().startswith(("{", "[")):
            try:
                json.loads(body)
            except Exception:
                ok = False
        if ok:
            passed += 1
            print(f"[PASS] {name} ({status})")
        else:
            failed += 1
            print(f"[FAIL] {name} ({status}) {body[:120]}")

    if args.with_decisions_post:
        status, body = req(args.base, "/api/decisions", "POST",
                           {"code": "000001", "action": "regression_check", "name": "回归测试"})
        ok = status == 200
        if ok:
            passed += 1
            print("[PASS] decisions-POST (200)")
        else:
            failed += 1
            print(f"[FAIL] decisions-POST ({status}) {body[:120]}")

    print(f"\n结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())