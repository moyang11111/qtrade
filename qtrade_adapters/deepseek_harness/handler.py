"""HTTP/page/API adapter for the optional DeepSeek HARNESS base."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import config, decisions


PAGES = config.PAGES
STATIC_FILES = config.STATIC_FILES
LIVE_PREFIX = config.LIVE_PREFIX
PROXY_PREFIX = config.PROXY_PREFIX


class QtradeDeckHandler:
    """QTrade handler compatible with the original deck server surface."""

    def __init__(self, handler, *, base_dir_fn=None, prepare_sys_path_fn=None, harness_port_fn=None):
        self.h = handler
        self._base_dir_fn = base_dir_fn or config.resolve_base_dir
        self._prepare_sys_path_fn = prepare_sys_path_fn or config.prepare_sys_path
        self._harness_port_fn = harness_port_fn or (lambda: config.HARNESS_PORT)

    def reply_json(self, data, status=200):
        return self.h._json(data, status)

    def _send_bytes(self, status, ctype, body):
        self.h.send_response(status)
        self.h.send_header("Content-Type", ctype)
        self.h.send_header("Content-Length", str(len(body)))
        self.h.end_headers()
        self.h.wfile.write(body)

    def serve_static(self, base: Path, path: str) -> bool:
        if path in STATIC_FILES:
            sub, name = STATIC_FILES[path]
            fspath = base / sub / name
            if fspath.exists():
                self._send_bytes(200, self._guess_ctype(fspath), fspath.read_bytes())
                return True
        if path.startswith("/v2/"):
            relative = path[len("/v2/"):]
            for candidate in (base / "ui_v2" / relative, base / "deck" / relative):
                if candidate.exists():
                    self._send_bytes(200, self._guess_ctype(candidate), candidate.read_bytes())
                    return True
            return True
        return False

    def serve_page(self, base: Path, page: str) -> bool:
        fspath = base / "ui_v2" / "pages" / page
        if not fspath.exists():
            return False
        html = fspath.read_text(encoding="utf-8")
        inject = (
            "<style>#sidebar{display:none!important}"
            "body{padding-left:0!important}.v2-wrap{padding-left:0!important}"
        )
        if page == "portal.html":
            inject += "#qt-features-box{display:none!important}#wufu-box{display:none!important}"
        inject += "</style>"
        html = html.replace("</head>", inject + "</head>", 1)
        self._send_bytes(200, "text/html; charset=utf-8", html.encode("utf-8"))
        return True

    @staticmethod
    def _guess_ctype(fspath: Path) -> str:
        import mimetypes

        return mimetypes.guess_type(fspath.name)[0] or "application/octet-stream"

    def serve_live(self, sub: str):
        base = self._base_dir_fn()
        self._prepare_sys_path_fn(base)
        if sub == "system_live":
            return self.reply_json(
                {
                    "ok": True,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "mode": "qtrade-live",
                    "runtime": "Qtrade 同端口复用",
                }
            )
        if sub == "build_mode":
            return self.reply_json({"mode": "dev", "hint": "内部测试构建，非公测发布版"})
        if sub == "tech_pitch":
            sub = "tech"
        if sub == "endpoints":
            return self._serve_endpoints()
        if sub == "brief":
            return self._serve_brief()
        if sub == "backtest_archive":
            try:
                from backtest.bt_report import list_archives

                return self.reply_json({"ok": True, **list_archives()})
            except Exception as error:
                return self.reply_json({"ok": False, "error": str(error)}, 500)
        if sub == "backtest_strategies":
            try:
                from backtest.bt_runner import list_strategies

                return self.reply_json({"ok": True, "strategies": list_strategies()})
            except Exception as error:
                return self.reply_json({"ok": False, "error": str(error)}, 500)
        if sub == "backtest_run":
            try:
                import urllib.parse as _urlparse

                query = _urlparse.parse_qs(
                    self.h.path.split("?", 1)[1] if "?" in self.h.path else ""
                )

                def get_query(key, default):
                    return (query.get(key) or [default])[0]

                from backtest.bt_runner import run_backtest

                result = run_backtest(
                    strategy=get_query("strategy", "tech3"),
                    topn=int(get_query("topn", "5")),
                    stocks=int(get_query("stocks", "300")),
                    start=get_query("start", "2021-01-01"),
                    end=get_query("end", "2025-12-31"),
                )
                return self.reply_json({"ok": True, **result})
            except Exception as error:
                return self.reply_json({"ok": False, "error": str(error)}, 500)
        import deck.live_api as live_api

        function = getattr(live_api, "live_" + sub, None)
        if function is None:
            return self.reply_json({"ok": False, "error": f"未知底座接口: {sub}"}, status=404)
        try:
            return self.reply_json(function())
        except Exception as error:
            return self.reply_json({"ok": False, "error": f"live/{sub}: {error}"})

    def _serve_endpoints(self):
        import urllib.request as _urlrequest
        from concurrent.futures import ThreadPoolExecutor

        import deck.live_api as live_api

        endpoints = getattr(live_api, "_ENDPOINTS", [])
        port = self.h.server.server_address[1]
        for name in (
            "live_holdings",
            "live_realtime",
            "live_portal_dash",
            "live_chain",
            "live_alerts",
            "live_calendar",
        ):
            try:
                function = getattr(live_api, name, None)
                if function:
                    function()
            except Exception:
                pass

        def probe(path):
            started = time.time()
            try:
                with _urlrequest.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
                    return {
                        "path": path,
                        "status": response.status,
                        "ms": int((time.time() - started) * 1000),
                        "ok": response.status == 200,
                    }
            except Exception as error:
                return {
                    "path": path,
                    "status": 0,
                    "ms": int((time.time() - started) * 1000),
                    "ok": False,
                    "error": str(error)[:80],
                }

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(probe, endpoints))
        successful = [result for result in results if result.get("ok")]
        return self.reply_json(
            {
                "ok": True,
                "ts": time.strftime("%H:%M:%S"),
                "total": len(endpoints),
                "ok_count": len(successful),
                "fail": [result for result in results if not result.get("ok")],
                "endpoints": results,
            }
        )

    def _serve_brief(self):
        import glob as _glob

        base = self._base_dir_fn()
        items = []
        opportunity_files = sorted(
            _glob.glob(str(base / "logs" / "opp_pool_*.json")),
            key=lambda path: Path(path).stat().st_mtime,
        )
        if opportunity_files:
            try:
                payload = json.loads(Path(opportunity_files[-1]).read_text(encoding="utf-8"))
                opportunities = payload.get("opportunities") or payload.get("entries") or []
                if opportunities:
                    top = opportunities[0]
                    items.append(
                        {
                            "cat": "机会池",
                            "level": "ok",
                            "msg": (
                                f"今日机会池 {len(opportunities)} 条；Top1 "
                                f"{top.get('name', '')}({top.get('code', '')}) score {top.get('score', '')}"
                            ),
                        }
                    )
            except Exception:
                pass
        timing_files = sorted(
            _glob.glob(str(base / "output" / "timing_system_*.json")),
            key=lambda path: Path(path).stat().st_mtime,
        )
        if timing_files:
            try:
                timing = json.loads(Path(timing_files[-1]).read_text(encoding="utf-8"))
                items.append(
                    {
                        "cat": "择时",
                        "level": "ok",
                        "msg": f"市场择时 {timing.get('level')}（{timing.get('score')} 分）",
                    }
                )
            except Exception:
                pass
        return self.reply_json({"ok": True, "n": len(items), "n_high": 0, "items": items})

    def handle_get(self, path: str) -> bool:
        base = self._base_dir_fn()
        if not (base / "deck").exists():
            return False
        if self.serve_static(base, path):
            return True
        if path in PAGES:
            return self.serve_page(base, PAGES[path])
        if path == "/api/pitch_v2":
            import glob as _glob

            files = sorted(_glob.glob(str(base / "logs" / "pitch_v2_*.json")))
            target = Path(files[-1]) if files else base / "logs" / "pitch_v2.json"
            if target.exists():
                self.reply_json(json.loads(target.read_text(encoding="utf-8")))
            else:
                self.reply_json({"ok": True, "pitch": []})
            return True
        if path == "/api/decisions":
            import glob as _glob

            files = sorted(_glob.glob(str(base / "logs" / "deck_decisions_*.json")))
            target = Path(files[-1]) if files else base / "logs" / "deck_decisions.json"
            self.reply_json(json.loads(target.read_text(encoding="utf-8")) if target.exists() else [])
            return True
        if path == "/api/tech_pitch":
            self.serve_live("tech_pitch")
            return True
        if path == "/api/harness":
            state_file = base / "output" / "harness_state.json"
            if state_file.exists():
                try:
                    self.reply_json(json.loads(state_file.read_text(encoding="utf-8")))
                except Exception as error:
                    self.reply_json(
                        {"ok": False, "error": f"harness_state.json 解析失败: {error}"},
                        500,
                    )
            else:
                self.reply_json({"ok": False, "error": "HARNESS 快照未生成（output/harness_state.json 不存在）"})
            return True
        if path == "/api/etf_map":
            etf_file = base / "output" / "etf_map.json"
            if etf_file.exists():
                try:
                    self.reply_json(json.loads(etf_file.read_text(encoding="utf-8")))
                except Exception as error:
                    self.reply_json({"ok": False, "error": f"etf_map.json 解析失败: {error}"}, 500)
            else:
                self.reply_json(
                    {"ok": False, "error": "etf_map.json 未生成，请运行 etf/etf_map.py"},
                    status=404,
                )
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

    def handle_post(self, path: str) -> bool:
        if path in ("/api/decisions", "/api/decide"):
            length = int(self.h.headers.get("Content-Length", 0) or 0)
            body = self.h.rfile.read(length) if length else b"{}"
            try:
                record = json.loads(body.decode("utf-8") or "{}")
            except Exception:
                self.reply_json({"ok": False, "error": "invalid JSON"}, status=400)
                return True
            main_module = sys.modules.get("__main__")
            auto_paper = getattr(main_module, "AUTO_PAPER", None) if main_module else None
            service = getattr(main_module, "SERVICE", None) if main_module else None
            self.decide(record, auto_paper=auto_paper, service=service)
            return True
        if path.startswith(PROXY_PREFIX):
            self._proxy_post(path)
            return True
        return False

    def _proxy_get(self, path: str) -> bool:
        import urllib.error as _urlerror
        import urllib.request as _urlrequest

        if path.startswith(PROXY_PREFIX + "niuapi/"):
            sub = path[len(PROXY_PREFIX + "niuapi/"):].split("?", 1)[0]
            if sub == "sessions":
                self.reply_json({"personas": []})
            else:
                self.reply_json({"messages": []})
            return True
        port = 3080 if path.startswith(PROXY_PREFIX + "quantapi/") else self._harness_port_fn()
        target = f"http://127.0.0.1:{port}/" + path[len(PROXY_PREFIX):]
        if "?" in self.h.path:
            target += "?" + self.h.path.split("?", 1)[1]
        try:
            response = _urlrequest.urlopen(_urlrequest.Request(target), timeout=40)
            data = response.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")))
            except Exception:
                content_type = (response.headers.get("Content-Type", "") or "").lower()
                if "html" in content_type or data[:1] == b"<":
                    self.reply_json({"ok": False, "error": "HARNESS 返回了 HTML（接口未挂载/暂不可达）"})
                else:
                    self._send_bytes(response.status, response.headers.get("Content-Type", "application/json"), data)
            return True
        except _urlerror.HTTPError as error:
            data = error.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")), error.code)
            except Exception:
                self.reply_json({"ok": False, "error": "proxy upstream " + str(error.code)}, error.code)
            return True
        except Exception as error:
            self.reply_json({"ok": False, "error": "proxy: " + str(error)}, 502)
            return True

    def _proxy_post(self, path: str):
        length = int(self.h.headers.get("Content-Length", 0) or 0)
        body = self.h.rfile.read(length) if length else b""
        import urllib.error as _urlerror
        import urllib.request as _urlrequest

        if path.startswith(PROXY_PREFIX + "niuapi/"):
            self.reply_json({"ok": False, "error": "牛散插件未启用"})
            return True
        port = 3080 if path.startswith(PROXY_PREFIX + "quantapi/") else self._harness_port_fn()
        target = f"http://127.0.0.1:{port}/" + path[len(PROXY_PREFIX):]
        if "?" in self.h.path:
            target += "?" + self.h.path.split("?", 1)[1]
        request = _urlrequest.Request(target, data=body, method="POST")
        request.add_header("Content-Type", self.h.headers.get("Content-Type", "application/json"))
        try:
            response = _urlrequest.urlopen(request, timeout=60)
            data = response.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")))
            except Exception:
                content_type = (response.headers.get("Content-Type", "") or "").lower()
                if "html" in content_type or data[:1] == b"<":
                    self.reply_json({"ok": False, "error": "HARNESS 返回了 HTML（接口未挂载/暂不可达）"})
                else:
                    self._send_bytes(response.status, response.headers.get("Content-Type", "application/json"), data)
        except _urlerror.HTTPError as error:
            data = error.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")), error.code)
            except Exception:
                self.reply_json({"ok": False, "error": "proxy upstream " + str(error.code)}, error.code)
        except Exception as error:
            self.reply_json({"ok": False, "error": "proxy: " + str(error)}, 502)

    def decide(self, rec, auto_paper=None, service=None):
        return decisions.decide(
            self.h,
            rec,
            auto_paper=auto_paper,
            service=service,
            base_dir_fn=self._base_dir_fn,
            prepare_sys_path_fn=self._prepare_sys_path_fn,
            background_sync_fn=self.decide_bg_sync,
        )

    @staticmethod
    def decide_bg_sync(base, rec=None, auto_paper=None, service=None):
        return decisions.decide_bg_sync(base, rec, auto_paper, service)


def serve_base_file(handler, fspath: Path) -> bool:
    if not fspath.exists():
        return False
    import mimetypes

    body = fspath.read_bytes()
    content_type = mimetypes.guess_type(fspath.name)[0] or "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
    return True
