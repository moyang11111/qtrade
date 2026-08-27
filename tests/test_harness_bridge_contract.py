"""Offline contracts for the local HARNESS port, proxy, and readiness surface."""

from __future__ import annotations

import json
import socket
import types
import urllib.error
from io import BytesIO
from pathlib import Path

import pytest

from qtrade_adapters.deepseek_harness import config, runtime
from qtrade_adapters.deepseek_harness.handler import HARNESS_STATUS_PATH, QtradeDeckHandler


class _FakeResponse:
    def __init__(self, body=b'{"ok": true}', status=200, content_type="application/json"):
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.closed = False

    def read(self):
        return self.body

    def close(self):
        self.closed = True


class _FakeHTTPHandler:
    def __init__(self, path="/", body=b"", headers=None):
        self.path = path
        self.headers = headers or {}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.json_calls = []
        self.byte_calls = []
        self.server = types.SimpleNamespace(server_address=("127.0.0.1", 49123))

    def _json(self, data, status=200):
        self.json_calls.append((status, data))
        return data

    def send_response(self, status):
        self.byte_calls.append({"status": status})

    def send_header(self, name, value):
        self.byte_calls[-1][name] = value

    def end_headers(self):
        pass


class _FakeSocket:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.address = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def connect(self, address):
        self.address = address
        if self.connect_error is not None:
            raise self.connect_error

    def close(self):
        pass


class _FakeSocketModule:
    def __init__(self, connect_error=None):
        self.connect_error = connect_error
        self.instances = []

    def socket(self):
        instance = _FakeSocket(self.connect_error)
        self.instances.append(instance)
        return instance


class _FakeProcessModule:
    DEVNULL = "DEVNULL"
    DETACHED_PROCESS = 64

    def __init__(self):
        self.calls = []

    def Popen(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return types.SimpleNamespace()


def _handler(path="/", body=b"", headers=None):
    return _FakeHTTPHandler(path, body=body, headers=headers)


def test_default_port_is_shared_by_proxy_and_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("QTRADE_HARNESS_PORT", raising=False)
    assert config.resolve_harness_port(env={}) == config.DEFAULT_HARNESS_PORT == 3080

    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, request.get_method(), timeout))
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    get_handler = _handler("/api/proxy/quantapi/sessions?view=all")
    assert QtradeDeckHandler(get_handler)._proxy_get("/api/proxy/quantapi/sessions") is True
    post_handler = _handler(
        "/api/proxy/quantapi/chat2?session=s1",
        body=b'{"message":"status"}',
        headers={"Content-Length": "20", "Content-Type": "application/json"},
    )
    assert QtradeDeckHandler(post_handler)._proxy_post("/api/proxy/quantapi/chat2") is True
    assert [entry[0].split("/", 3)[2] for entry in captured] == ["127.0.0.1:3080", "127.0.0.1:3080"]
    assert captured[0][2] == 40
    assert captured[1][2] == 60

    runtime.ensure_harness(
        base_dir_fn=lambda: tmp_path,
        env={"QTRADE_NO_HARNESS": "1"},
        socket_module=types.SimpleNamespace(
            socket=lambda: pytest.fail("disabled runtime must not probe")
        ),
        subprocess_module=_FakeProcessModule(),
    )


def test_explicit_port_controls_proxy_and_status_without_touching_3080(monkeypatch):
    monkeypatch.setenv("QTRADE_HARNESS_PORT", "3081")
    monkeypatch.delenv("QTRADE_NO_HARNESS", raising=False)
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request.full_url, timeout))
        return _FakeResponse(b'{"sessions": []}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    adapter = QtradeDeckHandler(_handler("/api/proxy/quantapi/health?mode=test"))
    assert adapter._proxy_get("/api/proxy/quantapi/health") is True
    post = _handler(
        "/api/proxy/quantapi/chat2?mode=test",
        body=b"{}",
        headers={"Content-Length": "2", "Content-Type": "application/json"},
    )
    assert QtradeDeckHandler(post)._proxy_post("/api/proxy/quantapi/chat2") is True
    status_handler = _handler(HARNESS_STATUS_PATH)
    assert QtradeDeckHandler(status_handler, base_dir_fn=lambda: Path("missing"))\
        .handle_get(HARNESS_STATUS_PATH) is True
    assert all("127.0.0.1:3081/" in url for url, _ in captured)
    assert all("127.0.0.1:3080/" not in url for url, _ in captured)
    assert status_handler.json_calls[-1][1]["port"] == 3081
    assert status_handler.json_calls[-1][1]["state"] == "service_reachable"
    assert status_handler.json_calls[-1][1]["model_ready"] == "unknown"


@pytest.mark.parametrize(
    "value",
    ["0", "65536", "-1", "not-a-port", "3.14", 3080.5, True],
)
def test_invalid_port_falls_back_with_safe_reason(value):
    port, reason = config.harness_port_info(env={"QTRADE_HARNESS_PORT": value})
    assert port == 3080
    assert reason == "invalid HARNESS port configuration; using the default port"


def test_valid_port_and_empty_port_contract():
    assert config.harness_port_info(env={"QTRADE_HARNESS_PORT": " 3081 "}) == (3081, None)
    assert config.harness_port_info(env={"QTRADE_HARNESS_PORT": ""}) == (3080, None)


def test_status_disabled_never_probes_or_starts(monkeypatch):
    monkeypatch.setenv("QTRADE_NO_HARNESS", "1")
    monkeypatch.setenv("QTRADE_HARNESS_PORT", "3081")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("disabled status must not probe"),
    )
    handler = _handler(HARNESS_STATUS_PATH)
    assert QtradeDeckHandler(handler, base_dir_fn=lambda: Path("does-not-exist"))\
        .handle_get(HARNESS_STATUS_PATH) is True
    status = handler.json_calls[-1][1]
    assert status == {
        "enabled": False,
        "port": 3081,
        "state": "disabled",
        "transport": "http",
        "sessions_reachable": False,
        "model_ready": "unknown",
        "reason": "QTRADE_NO_HARNESS is set",
    }


def test_status_sessions_200_is_not_model_ready(monkeypatch):
    monkeypatch.delenv("QTRADE_NO_HARNESS", raising=False)
    monkeypatch.setenv("QTRADE_HARNESS_PORT", "3081")
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, timeout))
        return _FakeResponse(b'{"sessions": [{"id": "safe"}]}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    handler = _handler(HARNESS_STATUS_PATH)
    QtradeDeckHandler(handler).serve_harness_status()
    status = handler.json_calls[-1][1]
    assert requests == [("http://127.0.0.1:3081/quantapi/sessions", 2.0)]
    assert status["state"] == "service_reachable"
    assert status["sessions_reachable"] is True
    assert status["model_ready"] == "unknown"
    assert "sessions" not in status["reason"] or "reachable" in status["reason"]


def test_status_rejects_unrelated_http_200_shape(monkeypatch):
    monkeypatch.delenv("QTRADE_NO_HARNESS", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse(b'{"ok": true}'))
    handler = _handler(HARNESS_STATUS_PATH)
    QtradeDeckHandler(handler).serve_harness_status()
    status = handler.json_calls[-1][1]
    assert status["state"] == "unreachable"
    assert status["sessions_reachable"] is False
    assert status["model_ready"] == "unknown"
    assert status["reason"] == "HARNESS sessions response is invalid"


def test_status_timeout_is_safe_and_does_not_leak_exception(monkeypatch):
    monkeypatch.delenv("QTRADE_NO_HARNESS", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("secret-path")))
    handler = _handler(HARNESS_STATUS_PATH)
    QtradeDeckHandler(handler).serve_harness_status()
    status = handler.json_calls[-1][1]
    assert status["state"] == "unreachable"
    assert status["sessions_reachable"] is False
    assert status["model_ready"] == "unknown"
    assert "timed out" in status["reason"]
    assert "secret-path" not in json.dumps(status)


@pytest.mark.parametrize(
    ("error", "status", "error_code"),
    [
        (ConnectionRefusedError("secret-path"), 502, "upstream_unreachable"),
        (TimeoutError("secret-path"), 504, "upstream_timeout"),
        (urllib.error.URLError(socket.timeout("secret-path")), 504, "upstream_timeout"),
    ],
)
def test_proxy_transport_failures_are_stable(monkeypatch, error, status, error_code):
    monkeypatch.delenv("QTRADE_HARNESS_PORT", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    handler = _handler("/api/proxy/quantapi/health")
    assert QtradeDeckHandler(handler)._proxy_get("/api/proxy/quantapi/health") is True
    actual_status, payload = handler.json_calls[-1]
    assert actual_status == status
    assert payload == {
        "ok": False,
        "error_code": error_code,
        "error": "HARNESS 上游请求超时" if status == 504 else "HARNESS 服务不可达",
    }
    assert "secret-path" not in json.dumps(payload)


def test_proxy_http_json_and_chat_acceptance_are_forwarded_unchanged(monkeypatch):
    upstream_error = urllib.error.HTTPError(
        "http://127.0.0.1:3080/quantapi/health",
        429,
        "busy",
        {"Content-Type": "application/json"},
        BytesIO(b'{"ok": false, "error": "busy"}'),
    )
    responses = [upstream_error, _FakeResponse(b'{"accepted": true, "job_id": "local"}')]
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request.full_url, request.get_method(), request.data, timeout))
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    error_handler = _handler("/api/proxy/quantapi/health")
    QtradeDeckHandler(error_handler)._proxy_get("/api/proxy/quantapi/health")
    assert error_handler.json_calls == [(429, {"ok": False, "error": "busy"})]

    body = b'{"message":"status"}'
    chat_handler = _handler(
        "/api/proxy/quantapi/chat2?session=s1&mode=read-only",
        body=body,
        headers={"Content-Length": str(len(body)), "Content-Type": "application/json; charset=utf-8"},
    )
    QtradeDeckHandler(chat_handler)._proxy_post("/api/proxy/quantapi/chat2")
    assert chat_handler.json_calls == [(200, {"accepted": True, "job_id": "local"})]
    assert requests[-1] == (
        "http://127.0.0.1:3080/quantapi/chat2?session=s1&mode=read-only",
        "POST",
        body,
        60,
    )


def test_proxy_http_non_json_error_is_stable(monkeypatch):
    upstream_error = urllib.error.HTTPError(
        "http://127.0.0.1:3080/quantapi/health",
        503,
        "unavailable",
        {"Content-Type": "text/plain"},
        BytesIO(b"local detail with secret-path"),
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(upstream_error))
    handler = _handler("/api/proxy/quantapi/health")
    QtradeDeckHandler(handler)._proxy_get("/api/proxy/quantapi/health")
    status, payload = handler.json_calls[-1]
    assert status == 503
    assert payload == {
        "ok": False,
        "error_code": "upstream_http_error",
        "error": "HARNESS 上游返回 HTTP 503",
    }
    assert "secret-path" not in json.dumps(payload)


def test_proxy_path_allowlist_and_niu_stub_are_safe(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *args, **kwargs: pytest.fail("disallowed proxy path must not access network"),
    )
    external = _handler("/api/proxy/https://evil.example/key")
    assert QtradeDeckHandler(external)._proxy_get("/api/proxy/https://evil.example/key") is True
    assert external.json_calls == [
        (
            404,
            {
                "ok": False,
                "error_code": "proxy_path_not_allowed",
                "error": "HARNESS proxy path is not allowed",
            },
        )
    ]
    niu = _handler("/api/proxy/niuapi/sessions")
    assert QtradeDeckHandler(niu)._proxy_get("/api/proxy/niuapi/sessions") is True
    assert niu.json_calls == [(200, {"personas": []})]
    assert "evil.example" not in json.dumps(external.json_calls)


def test_runtime_port_and_no_harness_contract_are_shared(tmp_path):
    sockets = _FakeSocketModule(ConnectionRefusedError())
    processes = _FakeProcessModule()
    runtime.ensure_harness(
        base_dir_fn=lambda: tmp_path,
        env={"QTRADE_HARNESS_PORT": "3081"},
        socket_module=sockets,
        shutil_module=types.SimpleNamespace(which=lambda _: None),
        subprocess_module=processes,
    )
    assert sockets.instances[0].address == ("127.0.0.1", 3081)
    assert processes.calls == []

    harness = tmp_path / "harness"
    (harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib").mkdir(parents=True)
    (harness / "home" / "profiles" / "web" / "plugins").mkdir(parents=True)
    (harness / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").write_text("", encoding="utf-8")
    (harness / "home" / "profiles" / "web" / "plugins" / "dsq-quant-bridge.js").write_text(
        "", encoding="utf-8"
    )
    (harness / "home" / ".credentials.yaml").write_text("", encoding="utf-8")
    start_processes = _FakeProcessModule()
    runtime.ensure_harness(
        base_dir_fn=lambda: tmp_path,
        default_src_base=tmp_path / "source",
        env={"QTRADE_HARNESS_PORT": "3081"},
        socket_module=_FakeSocketModule(ConnectionRefusedError()),
        shutil_module=types.SimpleNamespace(which=lambda _: "node-test"),
        subprocess_module=start_processes,
        os_name="nt",
    )
    assert start_processes.calls[0][0][0][-2:] == ["--port", "3081"]

    disabled_socket = types.SimpleNamespace(
        socket=lambda: pytest.fail("disabled runtime must not probe")
    )
    runtime.ensure_harness(
        env={"QTRADE_HARNESS_PORT": "3081", "QTRADE_NO_HARNESS": "1"},
        socket_module=disabled_socket,
        subprocess_module=processes,
    )
