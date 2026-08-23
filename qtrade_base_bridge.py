# -*- coding: utf-8 -*-
"""Qtrade × deepseek-harness-quant 底座桥接模块（与原项目 deck_server.Handler 风格对齐）。

职责：
- 同端口挂载门户/决策/控制台/静态资源
- /api/live/* 底座接口复用（复用原 deck/live_api.live_* 函数，保持同源）
- /api/proxy → HARNESS
- 决策审批 → 模拟持仓 + 远期池
- 启动时自动拉起 HARNESS(默认 3081) / 自动增量更新

结构对齐原项目：
- QtradeDeckHandler 对应 deck_server.Handler
- reply_json() 对应原 _send_json()
- serve_page()/serve_static() 对应原 _serve_page()/静态路由
- handle_get()/handle_post() 是路由出口（原 do_GET/do_POST 的分发主体）
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

# ---- 配置（支持环境变量覆盖）----
DEFAULT_SELF_BASE = Path(__file__).resolve().parent / "third_party" / "deepseek-harness-quant"
DEFAULT_SRC_BASE = Path(r"C:\Users\ASUS\qtrade\third_party\deepseek-harness-quant")
HARNESS_PORT = int(os.environ.get("QTRADE_HARNESS_PORT", "3081"))

# ---- 路由表（对齐原项目 ROUTES / 前缀分发）----
PAGES = {
    "/portal": "portal.html", "/portal.html": "portal.html",
    "/pitch": "pitch.html", "/pitch.html": "pitch.html",
    "/control": "control.html", "/control.html": "control.html",
    "/factors": "factors.html", "/factors.html": "factors.html",
}
STATIC_FILES = {
    "/live_ticker.js": ("deck", "live_ticker.js"),
    "/nav_common.js": ("deck", "nav_common.js"),
}
LIVE_PREFIX = "/api/live/"
PROXY_PREFIX = "/api/proxy/"
SPECIAL_ENDPOINTS = ("system_live", "build_mode", "tech_pitch", "endpoints", "brief", "enums")


def base_dir() -> Path:
    """返回可用的底座目录：环境变量 > 运行副本 third_party > 开发源。"""
    env = os.environ.get("QTRADE_BASE_DIR")
    for cand in (Path(env) if env else None, DEFAULT_SELF_BASE, DEFAULT_SRC_BASE):
        if cand is not None and (cand / "deck").exists():
            return cand
    return DEFAULT_SELF_BASE


def _prep_syspath(base: Path):
    """让底座包可导入；注意不可插入 qtrade_features（其下有 factors.py 会挡住底座 factors/ 包）。"""
    sys.modules.pop("factors", None)
    for p in (str(base), str(base / "deck")):
        if p not in sys.path:
            sys.path.insert(0, p)


class QtradeDeckHandler:
    """底座桥接 Handler（风格对齐原项目 deck_server.Handler）。"""

    def __init__(self, handler):
        # handler 为 Qtrade 的 APIHandler 实例
        self.h = handler

    # ---- 基础响应 ----
    def reply_json(self, data, status=200):
        return self.h._json(data, status)

    def _send_bytes(self, status, ctype, body):
        self.h.send_response(status)
        self.h.send_header("Content-Type", ctype)
        self.h.send_header("Content-Length", str(len(body)))
        self.h.end_headers()
        self.h.wfile.write(body)

    # ---- 静态与页面 ----
    def serve_static(self, base: Path, path: str) -> bool:
        if path in STATIC_FILES:
            sub, name = STATIC_FILES[path]
            fsp = base / sub / name
            if fsp.exists():
                self._send_bytes(200, self._guess_ctype(fsp), fsp.read_bytes())
                return True
        if path.startswith("/v2/"):
            rel = path[len("/v2/"):]
            for cand in (base / "ui_v2" / rel, base / "deck" / rel):
                if cand.exists():
                    self._send_bytes(200, self._guess_ctype(cand), cand.read_bytes())
                    return True
            return True  # 即使缺失也拦截，避免落到 Qtrade 静态
        return False

    def serve_page(self, base: Path, page: str) -> bool:
        fsp = base / "ui_v2" / "pages" / page
        if not fsp.exists():
            return False
        html = fsp.read_text(encoding="utf-8")
        inject = ("<style>#sidebar{display:none!important}"
                  "body{padding-left:0!important}.v2-wrap{padding-left:0!important}")
        if page == "portal.html":
            # Qtrade 覆盖：训练营/模拟盘已有左侧入口；五福缺全球ETF数据时隐藏
            inject += "#qt-features-box{display:none!important}#wufu-box{display:none!important}"
        inject += "</style>"
        html = html.replace("</head>", inject + "</head>", 1)
        self._send_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
        return True

    @staticmethod
    def _guess_ctype(fsp: Path) -> str:
        import mimetypes
        return mimetypes.guess_type(fsp.name)[0] or "application/octet-stream"

    # ---- 底座 live 接口 ----
    def serve_live(self, sub: str):
        base = base_dir()
        _prep_syspath(base)
        if sub == "system_live":
            return self.reply_json({"ok": True, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "mode": "qtrade-live", "runtime": "Qtrade 同端口复用"})
        if sub == "build_mode":
            return self.reply_json({"mode": "dev", "hint": "内部测试构建，非公测发布版"})
        if sub == "tech_pitch":
            sub = "tech"
        if sub == "endpoints":
            return self._serve_endpoints()
        if sub == "brief":
            return self._serve_brief()
        import deck.live_api as la
        fn = getattr(la, "live_" + sub, None)
        if fn is None:
            return self.reply_json({"ok": False, "error": f"未知底座接口: {sub}"}, status=404)
        try:
            return self.reply_json(fn())
        except Exception as e:
            return self.reply_json({"ok": False, "error": f"live/{sub}: {e}"})

    def _serve_endpoints(self):
        import deck.live_api as _la
        endpoints = getattr(_la, "_ENDPOINTS", [])
        port = self.h.server.server_address[1]
        import urllib.request as _ur
        from concurrent.futures import ThreadPoolExecutor as _TPE

        def _probe(path):
            st = time.time()
            try:
                with _ur.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15) as r:
                    return {"path": path, "status": r.status,
                            "ms": int((time.time() - st) * 1000), "ok": r.status == 200}
            except Exception as e:
                return {"path": path, "status": 0, "ms": int((time.time() - st) * 1000),
                        "ok": False, "error": str(e)[:80]}

        with _TPE(max_workers=8) as ex:
            results = list(ex.map(_probe, endpoints))
        ok = [r for r in results if r.get("ok")]
        return self.reply_json({"ok": True, "ts": time.strftime("%H:%M:%S"),
                                "total": len(endpoints), "ok_count": len(ok),
                                "fail": [r for r in results if not r.get("ok")],
                                "endpoints": results})

    def _serve_brief(self):
        import glob as _g
        base = base_dir()
        items = []
        fs = sorted(_g.glob(str(base / "logs" / "opp_pool_*.json")),
                    key=lambda p: Path(p).stat().st_mtime)
        if fs:
            try:
                d = json.loads(Path(fs[-1]).read_text(encoding="utf-8"))
                opps = d.get("opportunities") or d.get("entries") or []
                if opps:
                    top = opps[0]
                    items.append({"cat": "机会池", "level": "ok",
                                  "msg": f"今日机会池 {len(opps)} 条；Top1 {top.get('name','')}({top.get('code','')}) score {top.get('score','')}"})
            except Exception:
                pass
        fs2 = sorted(_g.glob(str(base / "output" / "timing_system_*.json")),
                     key=lambda p: Path(p).stat().st_mtime)
        if fs2:
            try:
                tm = json.loads(Path(fs2[-1]).read_text(encoding="utf-8"))
                items.append({"cat": "择时", "level": "ok",
                              "msg": f"市场择时 {tm.get('level')}（{tm.get('score')} 分）"})
            except Exception:
                pass
        return self.reply_json({"ok": True, "n": len(items), "n_high": 0, "items": items})

    # ---- GET 路由 ----
    def handle_get(self, path: str) -> bool:
        base = base_dir()
        if not (base / "deck").exists():
            return False

        if self.serve_static(base, path):
            return True

        if path in PAGES:
            return self.serve_page(base, PAGES[path])

        if path == "/api/pitch_v2":
            import glob as _g
            fs = sorted(_g.glob(str(base / "logs" / "pitch_v2_*.json")))
            target = Path(fs[-1]) if fs else base / "logs" / "pitch_v2.json"
            if target.exists():
                self.reply_json(json.loads(target.read_text(encoding="utf-8")))
            else:
                self.reply_json({"ok": True, "pitch": []})
            return True

        if path == "/api/decisions":
            import glob as _g
            fs = sorted(_g.glob(str(base / "logs" / "deck_decisions_*.json")))
            target = Path(fs[-1]) if fs else base / "logs" / "deck_decisions.json"
            self.reply_json(json.loads(target.read_text(encoding="utf-8")) if target.exists() else [])
            return True

        if path.startswith(PROXY_PREFIX):
            return self._proxy_get(path)

        if path.startswith(LIVE_PREFIX):
            sub = path[len(LIVE_PREFIX):].strip("/").split("?", 1)[0]
            self.serve_live(sub)
            return True
        if path in ("/api/system_live", "/api/build_mode", "/api/live/enums"):
            self.serve_live(path.rsplit("/", 1)[-1])
            return True
        return False

    # ---- POST 路由 ----
    def handle_post(self, path: str) -> bool:
        if path in ("/api/decisions", "/api/decide"):
            length = int(self.h.headers.get("Content-Length", 0) or 0)
            body = self.h.rfile.read(length) if length else b"{}"
            try:
                rec = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                self.reply_json({"ok": False, "error": "invalid JSON"}, status=400)
                return True
            # 从运行中的主模块获取全局（server.py 作为 __main__ 时无法用 from server import）
            _main = sys.modules.get("__main__")
            auto_paper = getattr(_main, "AUTO_PAPER", None) if _main else None
            service = getattr(_main, "SERVICE", None) if _main else None
            self.decide(rec, auto_paper=auto_paper, service=service)
            return True
        if path.startswith(PROXY_PREFIX):
            self._proxy_post(path)
            return True
        return False

    # ---- HARNESS 代理（原项目 /api/proxy 风格）----
    def _proxy_get(self, path: str) -> bool:
        import urllib.request as _ur
        import urllib.error as _ue
        tgt = f"http://127.0.0.1:{HARNESS_PORT}/" + path[len(PROXY_PREFIX):]
        if "?" in self.h.path:
            tgt += "?" + self.h.path.split("?", 1)[1]
        try:
            r = _ur.urlopen(_ur.Request(tgt), timeout=40)
            data = r.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")))
            except Exception:
                self._send_bytes(r.status, r.headers.get("Content-Type", "application/json"), data)
            return True
        except _ue.HTTPError as e:
            d = e.read()
            try:
                self.reply_json(json.loads(d.decode("utf-8")), e.code)
            except Exception:
                self.reply_json({"ok": False, "error": "proxy upstream " + str(e.code)}, e.code)
            return True
        except Exception as e:
            self.reply_json({"ok": False, "error": "proxy: " + str(e)}, 502)
            return True

    def _proxy_post(self, path: str):
        length = int(self.h.headers.get("Content-Length", 0) or 0)
        body = self.h.rfile.read(length) if length else b""
        import urllib.request as _ur
        import urllib.error as _ue
        tgt = f"http://127.0.0.1:{HARNESS_PORT}/" + path[len(PROXY_PREFIX):]
        if "?" in self.h.path:
            tgt += "?" + self.h.path.split("?", 1)[1]
        req = _ur.Request(tgt, data=body, method="POST")
        req.add_header("Content-Type", self.h.headers.get("Content-Type", "application/json"))
        try:
            r = _ur.urlopen(req, timeout=60)
            data = r.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")))
            except Exception:
                self._send_bytes(r.status, r.headers.get("Content-Type", "application/json"), data)
        except _ue.HTTPError as e:
            d = e.read()
            try:
                self.reply_json(json.loads(d.decode("utf-8")), e.code)
            except Exception:
                self.reply_json({"ok": False, "error": "proxy upstream " + str(e.code)}, e.code)
        except Exception as e:
            self.reply_json({"ok": False, "error": "proxy: " + str(e)}, 502)

    # ---- 决策执行 ----
    def decide(self, rec, auto_paper=None, service=None):
        base = base_dir()
        _prep_syspath(base)
        # 买入先尝试执行：成功才写审批记录（避免被 L0/名额拦后前端误以为已买入）
        if rec.get("action") == "buy" and auto_paper is not None:
            try:
                auto_paper.buy_from_decision(service, rec)
                held = {p.get("symbol") for p in auto_paper.status(service).get("positions", [])}
                if rec.get("code") not in held:
                    err = auto_paper.state.get("last_error") or "决策买入未成交"
                    self.reply_json({"ok": False, "error": err})
                    return
                print(f"[decide] 已合入统一模拟盘: {rec.get('code')}")
            except Exception as e:
                self.reply_json({"ok": False, "error": f"统一模拟盘买入失败: {e}"})
                return
        # 写入 deck_decisions 时间戳文件（买成功的 buy / drop / undo 都记录）
        logs = base / "logs"; logs.mkdir(parents=True, exist_ok=True)
        files = sorted(logs.glob("deck_decisions_*.json"), key=lambda p: p.stat().st_mtime)
        hist = []
        if files:
            try:
                hist = json.loads(files[-1].read_text(encoding="utf-8"))
            except Exception:
                hist = []
        if not isinstance(hist, list):
            hist = []
        hist.append(rec)
        out = logs / f"deck_decisions_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
        if rec.get("action") == "buy":
            # 底座决策模拟持仓/远期池（后台记录性质，不再重复买入）
            threading.Thread(target=QtradeDeckHandler.decide_bg_sync,
                             args=(base, rec, None, None), daemon=True).start()
        self.reply_json({"ok": True, "saved": out.name, "action": rec.get("action")})

    @staticmethod
    def decide_bg_sync(base, rec=None, auto_paper=None, service=None):
        sys.modules.pop("factors", None)
        # 1) 决策买入合入统一模拟盘（Qtrade 自动模拟盘同一账本）——放前面，避免被慢同步挡住
        if rec is not None and rec.get("action") == "buy":
            try:
                if auto_paper is not None:
                    auto_paper.buy_from_decision(service, rec)
                    print(f"[decide] 已合入统一模拟盘: {rec.get('code')}")
            except Exception as e:
                print(f"[decide] 统一模拟盘买入失败: {e}")
        # 2) 底座决策模拟持仓/远期池（记录性质）
        try:
            from strategy.portfolio import sync_from_decisions
            sync_from_decisions()
            print("[decide] 已同步决策模拟持仓（持股≤5）")
        except Exception as e:
            print(f"[decide] 持仓同步失败: {e}")
        try:
            import glob as _g
            pf = sorted(_g.glob(str(base / "logs" / "pitch_v2*.json")), key=lambda p: Path(p).stat().st_mtime)
            if pf:
                from factors.opportunities.pitch_track import append_pitch
                append_pitch(pf[-1])
                print("[decide] 已写入远期池")
        except Exception as e:
            print(f"[decide] 远期池联动失败: {e}")


# ---- 兼容旧模块级函数（server.py 现有包装使用）----
def serve_base_file(handler, fspath: Path) -> bool:
    return _legacy_serve_base_file(handler, fspath)


def _legacy_serve_base_file(handler, fspath: Path) -> bool:
    if not fspath.exists():
        return False
    import mimetypes
    body = fspath.read_bytes()
    ctype = mimetypes.guess_type(fspath.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True


def live(handler, sub: str):
    return QtradeDeckHandler(handler).serve_live(sub)


def try_serve(handler, path: str) -> bool:
    return QtradeDeckHandler(handler).handle_get(path)


def decide(handler, rec, auto_paper=None, service=None):
    return QtradeDeckHandler(handler).decide(rec, auto_paper=auto_paper, service=service)


def decide_bg_sync(base, rec=None, auto_paper=None, service=None):
    return QtradeDeckHandler.decide_bg_sync(base, rec, auto_paper, service)


def ensure_harness():
    """Qtrade 启动时自动把底座 HARNESS(HARNESS_PORT) 带上。已运行则跳过。"""
    if os.environ.get("QTRADE_NO_HARNESS"):
        print(f"[HARNESS({HARNESS_PORT})] QTRADE_NO_HARNESS 已设置，跳过自动启动")
        return
    try:
        import socket as _sock
        s = _sock.socket(); s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", HARNESS_PORT))
            print(f"[HARNESS({HARNESS_PORT})] 已在运行")
            return
        except Exception:
            pass
        finally:
            s.close()
        import shutil as _shutil
        import subprocess as _sub
        node = _shutil.which("node")
        if not node:
            print(f"[HARNESS({HARNESS_PORT})] 未找到 Node.js，跳过")
            return
        self_h = base_dir() / "harness"
        src_h = DEFAULT_SRC_BASE / "harness"
        harness = None
        for cand in (src_h, self_h):
            if (cand / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").exists() \
               and (cand / "home" / "profiles" / "web" / "plugins" / "dsq-quant-bridge.js").exists() \
               and (cand / "home" / ".credentials.yaml").exists():
                harness = cand
                break
        if harness is None:
            print(f"[HARNESS({HARNESS_PORT})] 未找到可用的底座 HARNESS 运行时（需安装 node_modules 与 v16 桥接插件），跳过（可运行 harness\\install.cmd）")
            return
        dsh = harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        env = dict(os.environ); env["DSH_HOME"] = str(harness / "home")
        flags = _sub.DETACHED_PROCESS if os.name == "nt" else 0
        _sub.Popen([node, str(dsh), "web", "--port", str(HARNESS_PORT)], cwd=str(harness), env=env,
                   stdout=_sub.DEVNULL, stderr=_sub.DEVNULL, creationflags=flags)
        print(f"[HARNESS({HARNESS_PORT})] 已自动启动（底座量化桥接）")
    except Exception as e:
        print(f"[HARNESS({HARNESS_PORT})] 自动启动失败（忽略）: {e}")


def maybe_auto_update():
    """Qtrade 启动时自动增量更新（一天最多一次；全量回填完成后才启用）。"""
    if os.environ.get("QTRADE_NO_AUTOUPDATE"):
        print("[auto-update] 已通过 QTRADE_NO_AUTOUPDATE 关闭自动增量")
        return
    base = base_dir()
    if not (base / "logs" / "pipeline_full_v2_done.txt").exists():
        print("[auto-update] 全量回填未完成，跳过自动增量（等 run_pipeline_full_v2.py 跑完即可启用）")
        return
    marker = base / "data" / "cache" / "last_auto_update.txt"
    today = time.strftime("%Y-%m-%d")
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
        print("[auto-update] 今天已更新过，跳过")
        return
    script = base / "scripts" / "auto_update_daily.py"
    if not script.exists():
        print("[auto-update] 自动增量脚本缺失，跳过")
        return
    import subprocess as _sub
    env = dict(os.environ); env["LWQUANT_CACHE_DIR"] = str(base / "data" / "cache")
    flags = _sub.DETACHED_PROCESS if os.name == "nt" else 0
    _sub.Popen([sys.executable, "-X", "utf8", str(script)], cwd=str(base), env=env,
               stdout=_sub.DEVNULL, stderr=_sub.DEVNULL, creationflags=flags)
    print("[auto-update] 已在后台启动增量更新（当天补最近 7 天日线）")