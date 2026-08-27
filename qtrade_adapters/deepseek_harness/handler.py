"""HTTP/page/API adapter for the optional DeepSeek HARNESS base."""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config, decisions


PAGES = config.PAGES
STATIC_FILES = config.STATIC_FILES
LIVE_PREFIX = config.LIVE_PREFIX
PROXY_PREFIX = config.PROXY_PREFIX
HARNESS_STATUS_PATH = "/api/harness/status"
_PROXY_TARGET_PREFIXES = ("quantapi/", "niuapi/")
_STATUS_TIMEOUT_SECONDS = 2.0

ADAPTER_STYLE_LINKS = (
    '<link rel="stylesheet" href="/css/tokens.css" data-qtrade-adapter="tokens">\n'
    '<link rel="stylesheet" href="/css/deepseek-adapter.css" data-qtrade-adapter="external">\n'
    '<!-- qtrade-adapter-legacy-selector: #sidebar{display:none!important} -->\n'
)
_ADAPTER_LINK_RE = re.compile(
    r'<link\b[^>]*href=["\']/css/(?:tokens|deepseek-adapter)\.css(?:\?[^"\']*)?["\'][^>]*>\s*',
    re.IGNORECASE,
)
_ADAPTER_COMPAT_RE = re.compile(
    r'<!--\s*qtrade-adapter-legacy-selector:\s*#sidebar\{display:none!important\}\s*-->\s*',
    re.IGNORECASE,
)
_PAGE_TITLES = {
    "portal.html": "QTrade — 门户",
    "pitch.html": "QTrade — 决策",
    "control.html": "QTrade — 控制台",
    "factors.html": "QTrade — 因子仪表盘",
}


def _proxy_failure(error: Exception) -> tuple[int, str, str]:
    """Map transport failures to stable, non-sensitive public responses."""

    reason = getattr(error, "reason", None)
    timeout_types = (socket.timeout, TimeoutError)
    if isinstance(error, timeout_types) or isinstance(reason, timeout_types):
        return 504, "upstream_timeout", "HARNESS 上游请求超时"
    if any(
        marker in str(value).lower()
        for value in (error, reason)
        if value is not None
        for marker in ("timed out", "timeout")
    ):
        return 504, "upstream_timeout", "HARNESS 上游请求超时"
    if isinstance(error, (urllib.error.URLError, OSError, ConnectionError)):
        return 502, "upstream_unreachable", "HARNESS 服务不可达"
    return 502, "upstream_unreachable", "HARNESS 服务不可达"


def _add_class_to_first_tag(html: str, tag_name: str, class_name: str) -> str:
    pattern = re.compile(rf'(<{tag_name}\b[^>]*)(>)', re.IGNORECASE)
    class_pattern = re.compile(r'(\bclass\s*=\s*)(["\'])(.*?)(\2)', re.IGNORECASE)

    def replace(match):
        opening, closing = match.groups()
        class_match = class_pattern.search(opening)
        if class_match:
            classes = class_match.group(3).split()
            if class_name not in classes:
                classes.append(class_name)
            replacement = (
                class_match.group(1)
                + class_match.group(2)
                + " ".join(classes)
                + class_match.group(4)
            )
            opening = opening[:class_match.start()] + replacement + opening[class_match.end():]
        else:
            opening += f' class="{class_name}"'
        return opening + closing

    return pattern.sub(replace, html, count=1)


def _add_attribute_to_first_tag(html: str, tag_name: str, attribute: str, value: str) -> str:
    pattern = re.compile(rf'(<{tag_name}\b[^>]*)(>)', re.IGNORECASE)
    attribute_pattern = re.compile(rf'\b{re.escape(attribute)}\s*=', re.IGNORECASE)

    def replace(match):
        opening, closing = match.groups()
        if not attribute_pattern.search(opening):
            opening += f' {attribute}="{value}"'
        return opening + closing

    return pattern.sub(replace, html, count=1)


def _replace_page_title(html: str, page: str) -> str:
    title = _PAGE_TITLES.get(page)
    if not title:
        return html
    pattern = re.compile(r'(<title\b[^>]*>).*?(</title\s*>)', re.IGNORECASE | re.DOTALL)
    return pattern.sub(rf'\g<1>{title}\g<2>', html, count=1)


def adapt_page_html(html: str, page: str) -> str:
    """Apply the idempotent QTrade container contract to one upstream page."""
    page_slug = re.sub(r"[^a-z0-9]+", "-", Path(page).stem.lower()).strip("-") or "page"
    html = _replace_page_title(html, page)
    html = _add_class_to_first_tag(html, "html", "qtrade-adapted")
    html = _add_class_to_first_tag(html, "html", f"qtrade-page-{page_slug}")
    html = _add_attribute_to_first_tag(html, "html", "data-qtrade-adapted", "true")
    html = _add_class_to_first_tag(html, "body", "qtrade-adapted")
    html = _add_class_to_first_tag(html, "body", f"qtrade-page-{page_slug}")
    html = _add_attribute_to_first_tag(html, "body", "data-qtrade-adapted", "true")

    html = _ADAPTER_LINK_RE.sub("", html)
    html = _ADAPTER_COMPAT_RE.sub("", html)
    closing_head = re.search(r"</head\s*>", html, re.IGNORECASE)
    if closing_head:
        return html[:closing_head.start()] + ADAPTER_STYLE_LINKS + html[closing_head.start():]
    opening_head = re.search(r"<head\b[^>]*>", html, re.IGNORECASE)
    if opening_head:
        return html[:opening_head.end()] + "\n" + ADAPTER_STYLE_LINKS + html[opening_head.end():]
    return ADAPTER_STYLE_LINKS + html


class QtradeDeckHandler:
    """QTrade handler compatible with the original deck server surface."""

    def __init__(self, handler, *, base_dir_fn=None, prepare_sys_path_fn=None, harness_port_fn=None):
        self.h = handler
        self._base_dir_fn = base_dir_fn or config.resolve_base_dir
        self._prepare_sys_path_fn = prepare_sys_path_fn or config.prepare_sys_path
        self._harness_port_fn = harness_port_fn or (lambda: config.resolve_harness_port())

    def _harness_port(self) -> int:
        """Resolve an injected/default port through the strict config contract."""

        try:
            candidate = self._harness_port_fn()
        except Exception:
            candidate = config.DEFAULT_HARNESS_PORT
        return config.resolve_harness_port(
            env={config.HARNESS_PORT_ENV: str(candidate)},
            default=config.DEFAULT_HARNESS_PORT,
        )

    def _status_port(self) -> tuple[int, str | None]:
        """Return the effective port and a safe configuration diagnostic."""

        return config.harness_port_info(
            env=os.environ,
            default=self._harness_port(),
        )

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
        html = adapt_page_html(html, page)
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

    def serve_harness_status(self):
        """Probe only the loopback sessions endpoint; never start or message HARNESS."""

        port, config_reason = self._status_port()
        result = {
            "enabled": True,
            "port": port,
            "state": "unreachable",
            "transport": "http",
            "sessions_reachable": False,
            "model_ready": "unknown",
            "reason": config_reason or "HARNESS service is unreachable",
        }
        if os.environ.get("QTRADE_NO_HARNESS"):
            result.update(
                enabled=False,
                state="disabled",
                reason="QTRADE_NO_HARNESS is set",
            )
            return self.reply_json(result)

        target = f"http://127.0.0.1:{port}/quantapi/sessions"
        try:
            response = urllib.request.urlopen(
                urllib.request.Request(target),
                timeout=_STATUS_TIMEOUT_SECONDS,
            )
            try:
                try:
                    status = int(getattr(response, "status", getattr(response, "code", 200)))
                except (TypeError, ValueError):
                    status = 200
                response_matches_harness = self._sessions_response_is_expected(response)
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            if 200 <= status < 300 and response_matches_harness:
                result.update(
                    state="service_reachable",
                    sessions_reachable=True,
                    reason=config_reason or "HARNESS sessions endpoint is reachable",
                )
            elif 200 <= status < 300:
                result.update(
                    reason=config_reason or "HARNESS sessions response is invalid",
                )
            else:
                result.update(
                    reason=config_reason or "HARNESS sessions endpoint returned an HTTP error",
                )
        except urllib.error.HTTPError:
            result.update(
                reason=config_reason or "HARNESS sessions endpoint returned an HTTP error",
            )
        except Exception as error:
            _, error_code, message = _proxy_failure(error)
            result.update(
                reason=config_reason or message,
            )
            if error_code == "upstream_timeout":
                result["reason"] = "HARNESS health check timed out"
        return self.reply_json(result)

    @staticmethod
    def _sessions_response_is_expected(response) -> bool:
        """Require the known sessions shape when the probe exposes a response body."""

        reader = getattr(response, "read", None)
        if not callable(reader):
            return True
        try:
            body = reader()
            payload = json.loads(body.decode("utf-8"))
        except (AttributeError, UnicodeDecodeError, TypeError, ValueError, json.JSONDecodeError):
            return False
        return isinstance(payload, dict) and isinstance(payload.get("sessions"), list)

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
        if path == HARNESS_STATUS_PATH:
            self.serve_harness_status()
            return True
        if path.startswith(PROXY_PREFIX):
            return self._proxy_get(path)
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
        path_only = urllib.parse.urlsplit(path).path
        target = self._proxy_target(path_only)
        if target is None:
            return True
        if path_only.startswith(PROXY_PREFIX + "niuapi/"):
            sub = path_only[len(PROXY_PREFIX + "niuapi/"):]
            if sub == "sessions":
                self.reply_json({"personas": []})
            else:
                self.reply_json({"messages": []})
            return True
        try:
            response = urllib.request.urlopen(urllib.request.Request(target), timeout=40)
            self._forward_proxy_response(response)
            return True
        except urllib.error.HTTPError as error:
            self._forward_proxy_http_error(error)
            return True
        except Exception as error:
            self._reply_proxy_failure(error)
            return True

    def _proxy_post(self, path: str):
        length = int(self.h.headers.get("Content-Length", 0) or 0)
        body = self.h.rfile.read(length) if length else b""
        path_only = urllib.parse.urlsplit(path).path

        target = self._proxy_target(path_only)
        if target is None:
            return True
        if path_only.startswith(PROXY_PREFIX + "niuapi/"):
            self.reply_json({"ok": False, "error": "牛散插件未启用"})
            return True
        request = urllib.request.Request(target, data=body, method="POST")
        content_type = self.h.headers.get("Content-Type", "application/json")
        if (
            not isinstance(content_type, str)
            or "\r" in content_type
            or "\n" in content_type
            or content_type.split(";", 1)[0].strip().lower() != "application/json"
        ):
            content_type = "application/json"
        request.add_header("Content-Type", content_type)
        try:
            response = urllib.request.urlopen(request, timeout=60)
            self._forward_proxy_response(response)
        except urllib.error.HTTPError as error:
            self._forward_proxy_http_error(error)
        except Exception as error:
            self._reply_proxy_failure(error)
        return True

    def _proxy_target(self, path: str) -> str | None:
        """Build a fixed-loopback target from a small, path-safe proxy allowlist."""

        if not path.startswith(PROXY_PREFIX):
            self.reply_json(
                {
                    "ok": False,
                    "error_code": "proxy_path_not_allowed",
                    "error": "HARNESS proxy path is not allowed",
                },
                404,
            )
            return None
        relative = path[len(PROXY_PREFIX):]
        if not any(relative.startswith(prefix) for prefix in _PROXY_TARGET_PREFIXES):
            self.reply_json(
                {
                    "ok": False,
                    "error_code": "proxy_path_not_allowed",
                    "error": "HARNESS proxy path is not allowed",
                },
                404,
            )
            return None
        decoded = urllib.parse.unquote(relative)
        if (
            not decoded
            or "\\" in decoded
            or any(ord(char) < 0x20 for char in decoded)
            or any(segment in ("", ".", "..") for segment in decoded.split("/"))
            or any(char in decoded for char in ("?", "#", ":"))
        ):
            self.reply_json(
                {
                    "ok": False,
                    "error_code": "proxy_path_not_allowed",
                    "error": "HARNESS proxy path is not allowed",
                },
                404,
            )
            return None
        raw_request_path = getattr(self.h, "path", path)
        query = urllib.parse.urlsplit(raw_request_path).query
        if "\r" in query or "\n" in query or len(query) > 4096:
            self.reply_json(
                {
                    "ok": False,
                    "error_code": "proxy_query_not_allowed",
                    "error": "HARNESS proxy query is not allowed",
                },
                400,
            )
            return None
        port = config.resolve_harness_port() if relative.startswith("quantapi/") else self._harness_port()
        target = f"http://127.0.0.1:{port}/{relative}"
        return target + (f"?{query}" if query else "")

    def _forward_proxy_response(self, response) -> None:
        try:
            data = response.read()
            status = self._response_status(response)
            try:
                self.reply_json(json.loads(data.decode("utf-8")), status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                content_type = (getattr(response, "headers", {}).get("Content-Type", "") or "").lower()
                if "html" in content_type or data[:1] == b"<":
                    self.reply_json(
                        {"ok": False, "error": "HARNESS 返回了 HTML（接口未挂载/暂不可达）"},
                        status,
                    )
                else:
                    self._send_bytes(
                        status,
                        getattr(response, "headers", {}).get("Content-Type", "application/json"),
                        data,
                    )
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    @staticmethod
    def _response_status(response) -> int:
        try:
            status = int(getattr(response, "status", getattr(response, "code", 200)))
        except (TypeError, ValueError):
            status = 200
        return status if 100 <= status <= 599 else 502

    def _forward_proxy_http_error(self, error: urllib.error.HTTPError) -> None:
        status = int(error.code) if 100 <= int(error.code) <= 599 else 502
        try:
            data = error.read()
            try:
                self.reply_json(json.loads(data.decode("utf-8")), status)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.reply_json(
                    {
                        "ok": False,
                        "error_code": "upstream_http_error",
                        "error": f"HARNESS 上游返回 HTTP {status}",
                    },
                    status,
                )
        finally:
            close = getattr(error, "close", None)
            if close is not None:
                close()

    def _reply_proxy_failure(self, error: Exception) -> None:
        status, error_code, message = _proxy_failure(error)
        self.reply_json(
            {
                "ok": False,
                "error_code": error_code,
                "error": message,
            },
            status,
        )

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
