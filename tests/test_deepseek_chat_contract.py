"""Offline contracts for the QTrade-owned read-only DeepSeek chat service."""

from __future__ import annotations

import ast
from concurrent.futures import Future
from contextlib import closing
from http.client import HTTPConnection, HTTPResponse
import json
from pathlib import Path
import sqlite3
import socket
import ssl
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

import server
from qtrade_adapters.deepseek_chat import config
from qtrade_adapters.deepseek_chat.context import (
    ContextProvider,
    build_context,
    serialize_context,
)
from qtrade_adapters.deepseek_chat.service import (
    DeepSeekChatError,
    DeepSeekChatService,
    TransportResponse,
    UrllibTransport,
)
import qtrade_adapters.deepseek_chat.service as deepseek_service_module


ROOT = Path(__file__).resolve().parents[1]
SERVICE_SOURCE = ROOT / "qtrade_adapters" / "deepseek_chat" / "service.py"
CONTEXT_SOURCE = ROOT / "qtrade_adapters" / "deepseek_chat" / "context.py"


class _FakeClock:
    def __init__(self):
        self.monotonic_value = 1_000.0
        self.wall_value = 1_704_067_200.0

    def monotonic(self) -> float:
        return self.monotonic_value

    def time(self) -> float:
        return self.wall_value

    def advance(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall_value += seconds


class _ManualExecutor:
    def __init__(self):
        self.tasks = []
        self.shutdown_args = None

    def submit(self, function, *args):
        future = Future()
        self.tasks.append((future, function, args))
        return future

    def run_next(self):
        future, function, args = self.tasks.pop(0)
        if future.cancelled():
            return
        try:
            result = function(*args)
        except BaseException as error:  # pragma: no cover - surfaced by the assertion below
            future.set_exception(error)
        else:
            future.set_result(result)

    def shutdown(self, *, wait=False, cancel_futures=False):
        self.shutdown_args = (wait, cancel_futures)
        if cancel_futures:
            for future, _function, _args in self.tasks:
                future.cancel()


class _Ids:
    def __init__(self):
        self.count = 0

    def __call__(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_test_{self.count}"


class _FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def post(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _FakeSocket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, value):
        self.timeouts.append(value)


class _FakeResponseFile:
    def __init__(self, sock):
        self.raw = SimpleNamespace(_sock=sock)

    def close(self):
        self.raw._sock.closed = True


class _FakeResponse:
    def __init__(self, chunks, status=200, response_socket=None):
        self.chunks = list(chunks)
        self.status = status
        self.read_sizes = []
        self.socket = response_socket or _FakeSocket()
        self.fp = _FakeResponseFile(self.socket)
        self.closed = False

    def read(self, amount):
        self.read_sizes.append(amount)
        if self.chunks:
            return self.chunks.pop(0)
        return b""

    def close(self):
        self.closed = True
        self.fp.close()


class _FakeHTTPSConnection:
    instances = []
    response = _FakeResponse([b"{}"])

    def __init__(self, host, timeout, context):
        self.host = host
        self.timeout = timeout
        self.context = context
        self.sock = _FakeSocket()
        self.transport_socket = self.sock
        self.headers = []
        self.request_line = None
        self.sent_body = None
        self.connected = False
        self.closed = False
        self.__class__.instances.append(self)

    def connect(self):
        self.connected = True

    def putrequest(self, method, path, *, skip_accept_encoding=False):
        self.request_line = (method, path, skip_accept_encoding)

    def putheader(self, name, value):
        self.headers.append((name, value))

    def endheaders(self):
        return None

    def send(self, body):
        self.sent_body = body

    def getresponse(self):
        # Model http.client.HTTPConnection.getresponse() transferring a
        # Connection: close response to its own response file and clearing
        # connection.sock.
        self.sock = None
        return self.__class__.response

    def close(self):
        self.closed = True


def _install_fake_https(monkeypatch, response):
    _FakeHTTPSConnection.instances = []
    _FakeHTTPSConnection.response = response
    monkeypatch.setattr(deepseek_service_module, "HTTPSConnection", _FakeHTTPSConnection)


def _reply(text="safe reply") -> TransportResponse:
    body = json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": text}}]},
        separators=(",", ":"),
    ).encode("utf-8")
    return TransportResponse(200, body)


def _context(**overrides):
    values = {
        "health": lambda: {"status": "ok", "absolute_path": "C:/secret"},
        "business_date": lambda: {"as_of": "2024-01-02", "freshness": "fresh"},
        "mainboard": lambda: {
            "total": 10,
            "computable": 8,
            "tradable": 9,
            "as_of": "2024-01-02",
            "source": "csv",
            "symbol": "600519",
        },
        "opportunities": lambda: {
            "count": 2,
            "categories": {"screened": 2, "fresh": 1, "raw_log": "<script>"},
            "positions": ["600519"],
        },
        "factors": lambda: {
            "scheme_count": 3,
            "active_count": 2,
            "as_of": "2024-01-02",
            "freshness": "fresh",
            "source_token": "secret",
        },
    }
    values.update(overrides)
    return build_context(ContextProvider(**values))


def _make_service(
    monkeypatch,
    *,
    transport=None,
    executor=None,
    clock=None,
    provider=None,
    logger=None,
):
    monkeypatch.setenv(config.FEATURE_ENV, "1")
    monkeypatch.setenv(config.API_KEY_ENV, "test-only-key")
    clock = clock or _FakeClock()
    executor = executor or _ManualExecutor()
    service = DeepSeekChatService(
        transport=transport or _FakeTransport(response=_reply()),
        clock=clock,
        executor_factory=lambda: executor,
        context_provider=provider or (lambda: _context()),
        logger=logger,
        id_factory=_Ids(),
    )
    return service, clock, executor


def _run_request(service, executor):
    assert len(executor.tasks) == 1
    executor.run_next()


def _http_json(opener, port, path, method="GET", body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {} if data is None else {"Content-Type": "application/json"}
    request = Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with opener.open(request, timeout=3) as response:
            return (
                response.status,
                dict(response.headers),
                json.loads(response.read().decode("utf-8")),
            )
    except Exception as error:
        if hasattr(error, "read"):
            try:
                return error.code, dict(error.headers), json.loads(error.read().decode("utf-8"))
            finally:
                error.close()
        raise


def _loopback_json(port, path, *, method="GET", body=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return (
            response.status,
            dict(response.headers),
            json.loads(response.read().decode("utf-8")),
        )
    finally:
        connection.close()


def _start_api_server(monkeypatch, service):
    monkeypatch.setattr(server, "STATIC_DIR", ROOT / "static")
    monkeypatch.setattr(server, "DEEPSEEK_CHAT_SERVICE", service)
    monkeypatch.setattr(
        server,
        "SERVICE",
        SimpleNamespace(live=False, live_src=None, scan=lambda: []),
    )
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.APIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _post_with_transport():
    return UrllibTransport().post(
        url=config.DEEPSEEK_CHAT_URL,
        body=b'{"messages":[]}',
        api_key="test-only-key",
        connect_timeout=config.CONNECT_TIMEOUT_SECONDS,
        total_timeout=config.TOTAL_TIMEOUT_SECONDS,
        cancel_event=threading.Event(),
    )


def test_fixed_transport_is_verified_direct_https_without_proxy_or_redirect(monkeypatch):
    response = _FakeResponse([b'{"ok":', b"true}", b""], status=302)
    _install_fake_https(monkeypatch, response)
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")

    result = _post_with_transport()

    assert result == TransportResponse(302, b'{"ok":true}')
    assert len(_FakeHTTPSConnection.instances) == 1
    connection = _FakeHTTPSConnection.instances[0]
    assert connection.host == config.DEEPSEEK_CHAT_HOST
    assert connection.timeout == config.CONNECT_TIMEOUT_SECONDS
    assert connection.request_line == ("POST", config.DEEPSEEK_CHAT_PATH, True)
    assert dict(connection.headers)["Authorization"] == "Bearer test-only-key"
    assert connection.sent_body == b'{"messages":[]}'
    assert connection.context.check_hostname is True
    assert connection.context.verify_mode == ssl.CERT_REQUIRED
    assert connection.closed is True
    assert all(
        0 < value <= config.TOTAL_TIMEOUT_SECONDS
        for value in connection.transport_socket.timeouts
    )
    assert response.socket.timeouts
    assert response.closed is True


def test_fixed_transport_controls_stdlib_response_file_after_connection_close(monkeypatch):
    server_socket, client_socket = socket.socketpair()
    try:
        server_socket.sendall(
            b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
            b"Content-Length: 2\r\n\r\nok"
        )
        response = HTTPResponse(client_socket)
        response.begin()
        observed_timeouts = []
        original_read = response.read

        def read(amount):
            observed_timeouts.append(client_socket.gettimeout())
            return original_read(amount)

        response.read = read
        _install_fake_https(monkeypatch, response)

        result = _post_with_transport()

        assert result == TransportResponse(200, b"ok")
        assert observed_timeouts
        assert 0 < observed_timeouts[0] <= 0.25
        assert response.isclosed() is True
    finally:
        server_socket.close()
        client_socket.close()


def test_fixed_transport_rejects_non_fixed_url_without_connecting(monkeypatch):
    _install_fake_https(monkeypatch, _FakeResponse([b"{}"]))
    with pytest.raises(OSError):
        UrllibTransport().post(
            url="https://127.0.0.1/redirect",
            body=b"{}",
            api_key="test-only-key",
            connect_timeout=5,
            total_timeout=35,
            cancel_event=threading.Event(),
        )
    assert _FakeHTTPSConnection.instances == []


def test_fixed_transport_stops_slow_drip_body_at_hard_deadline(monkeypatch):
    class _AdvancingClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = _AdvancingClock()

    class _SlowResponse(_FakeResponse):
        def read(self, amount):
            self.read_sizes.append(amount)
            clock.value = config.TOTAL_TIMEOUT_SECONDS + 1
            return b"x"

    response = _SlowResponse([])
    _install_fake_https(monkeypatch, response)
    monkeypatch.setattr(deepseek_service_module, "_monotonic", clock)

    with pytest.raises(TimeoutError):
        _post_with_transport()
    assert _FakeHTTPSConnection.instances[0].closed is True
    assert response.socket.timeouts
    assert response.closed is True


def test_fixed_transport_stops_slow_drip_headers_at_hard_deadline(monkeypatch):
    class _AdvancingClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = _AdvancingClock()

    class _HeaderSlowConnection(_FakeHTTPSConnection):
        instances = []

        def getresponse(self):
            assert self.transport_socket.timeouts
            clock.value = config.TOTAL_TIMEOUT_SECONDS + 1
            raise socket.timeout("provider header detail")

    _HeaderSlowConnection.response = _FakeResponse([])
    monkeypatch.setattr(deepseek_service_module, "HTTPSConnection", _HeaderSlowConnection)
    monkeypatch.setattr(deepseek_service_module, "_monotonic", clock)

    with pytest.raises(TimeoutError):
        _post_with_transport()

    connection = _HeaderSlowConnection.instances[0]
    assert connection.closed is True
    assert all(
        0 < value <= config.TOTAL_TIMEOUT_SECONDS
        for value in connection.transport_socket.timeouts
    )


def test_fixed_transport_bounds_response_reads_to_16_kib(monkeypatch):
    response = _FakeResponse([b"x" * (config.MAX_RESPONSE_BYTES + 1)])
    _install_fake_https(monkeypatch, response)

    result = _post_with_transport()

    assert len(result.body) == config.MAX_RESPONSE_BYTES + 1
    assert len(response.read_sizes) == 1
    assert _FakeHTTPSConnection.instances[0].closed is True


@pytest.mark.parametrize("phase", ["connect", "read"])
def test_fixed_transport_socket_timeouts_map_to_bounded_timeout(monkeypatch, phase):
    class _TimeoutClock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = _TimeoutClock()

    class _TimeoutConnection(_FakeHTTPSConnection):
        def connect(self):
            if phase == "connect":
                raise socket.timeout("provider secret")
            super().connect()

    class _TimeoutResponse(_FakeResponse):
        def read(self, amount):
            self.read_sizes.append(amount)
            clock.value = config.TOTAL_TIMEOUT_SECONDS + 1
            raise socket.timeout("provider secret")

    response = _TimeoutResponse([])
    _TimeoutConnection.response = response
    _TimeoutConnection.instances = []
    monkeypatch.setattr(deepseek_service_module, "HTTPSConnection", _TimeoutConnection)
    monkeypatch.setattr(deepseek_service_module, "_monotonic", clock)
    with pytest.raises(TimeoutError):
        _post_with_transport()
    assert _TimeoutConnection.instances[0].closed is True


def test_disabled_status_reads_no_key_creates_no_executor_or_network(monkeypatch):
    monkeypatch.setenv(config.FEATURE_ENV, "0")
    monkeypatch.setenv(config.API_KEY_ENV, "test-only-key")
    called = []

    def forbidden_key_read():
        called.append(True)
        raise AssertionError("disabled status must not read the key")

    monkeypatch.setattr(config, "read_api_key", forbidden_key_read)
    executor_created = []
    transport = _FakeTransport(response=_reply())
    service = DeepSeekChatService(
        transport=transport,
        executor_factory=lambda: executor_created.append(True),
    )

    assert service.status()["state"] == "disabled"
    assert called == []
    assert executor_created == []
    assert transport.calls == []
    service.close()


def test_disabled_import_status_and_shutdown_leave_external_db_renameable(tmp_path, monkeypatch):
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    bars = cache / "bars.db"
    with closing(sqlite3.connect(bars)) as connection:
        connection.execute("CREATE TABLE marker(value INTEGER)")
        connection.commit()

    monkeypatch.setenv(config.FEATURE_ENV, "0")
    monkeypatch.setenv(config.API_KEY_ENV, "test-only-key")
    monkeypatch.setattr(server, "DEEPSEEK_CHAT_SERVICE", None)
    service = server.get_deepseek_chat_service()

    assert service.status()["state"] == "disabled"
    assert service._executor is None
    service.close()

    renamed = cache / "bars-renamed.db"
    bars.rename(renamed)
    renamed.unlink()


def test_mainboard_adapter_closes_read_only_connections_before_cache_cleanup(tmp_path):
    cache = tmp_path / "data" / "cache"
    cache.mkdir(parents=True)
    with closing(sqlite3.connect(cache / "stock_basic.db")) as connection:
        connection.execute(
            "CREATE TABLE stock_basic(code TEXT, name TEXT, out_date TEXT, status TEXT)"
        )
        connection.execute("INSERT INTO stock_basic VALUES ('000001.SZ', 'Synthetic', '', '1')")
        connection.commit()
    with closing(sqlite3.connect(cache / "bars.db")) as connection:
        connection.execute(
            "CREATE TABLE bar_meta(code TEXT, adjust TEXT, rows INTEGER, end_date TEXT)"
        )
        connection.execute(
            "CREATE TABLE daily_bar(code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adjust TEXT)"
        )
        connection.execute(
            "INSERT INTO bar_meta VALUES ('000001.SZ', 'qfq', 130, '2026-08-25')"
        )
        connection.commit()

    from qtrade_adapters.deepseek_harness.market_data import MainboardMarketDataAdapter

    adapter = MainboardMarketDataAdapter(tmp_path)
    assert adapter.universe_summary()["source"] == "external_sqlite"

    renamed = cache / "bars-renamed.db"
    (cache / "bars.db").rename(renamed)
    renamed.unlink()


def test_mainboard_connect_closes_after_query_only_setup_failure(monkeypatch, tmp_path):
    from qtrade_adapters.deepseek_harness.market_data import MainboardMarketDataAdapter

    class _Connection:
        row_factory = None
        closed = False

        def execute(self, statement):
            assert statement == "PRAGMA query_only=ON"
            raise sqlite3.OperationalError("synthetic setup failure")

        def close(self):
            self.closed = True

    connection = _Connection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError):
        MainboardMarketDataAdapter._connect(tmp_path / "bars.db")

    assert connection.closed is True


def test_unconfigured_status_is_local_and_does_not_start_work(monkeypatch):
    monkeypatch.setenv(config.FEATURE_ENV, "1")
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    transport = _FakeTransport(response=_reply())
    executor_created = []
    service = DeepSeekChatService(
        transport=transport,
        executor_factory=lambda: executor_created.append(True),
    )

    result = service.status()
    assert result["state"] == "unconfigured"
    assert result["session_id"] is None
    assert executor_created == []
    assert transport.calls == []
    service.close()


def test_send_is_accepted_then_replied_with_no_tool_fields(monkeypatch):
    transport = _FakeTransport(response=_reply("plain text"))
    service, clock, executor = _make_service(monkeypatch, transport=transport)

    ready = service.status()
    assert ready["state"] == "ready"
    session_id = ready["session_id"]
    accepted = service.send_payload({"session_id": session_id, "text": "show status"})
    assert accepted["state"] == "accepted"
    assert 250 <= accepted["poll_after_ms"] <= 1_000
    accepted_poll = service.poll(accepted["request_id"])
    assert accepted_poll["state"] == "accepted"
    assert accepted_poll["poll_after_ms"] == accepted["poll_after_ms"]
    assert transport.calls == []

    _run_request(service, executor)
    result = service.poll(accepted["request_id"], session_id)
    assert result["state"] == "replied"
    assert result["reply"] == "plain text"
    assert isinstance(result["reply"], str)
    assert service.status(session_id)["state"] == "ready"

    call = transport.calls[0]
    assert call["url"] == config.DEEPSEEK_CHAT_URL
    assert call["api_key"] == "test-only-key"
    payload = json.loads(call["body"].decode("utf-8"))
    assert set(payload) == {"model", "messages", "stream", "max_tokens", "temperature"}
    assert payload["model"] == config.DEEPSEEK_MODEL
    assert payload["stream"] is False
    assert payload["messages"][-1] == {"role": "user", "content": "show status"}
    assert "QTrade status context" in payload["messages"][1]["content"]
    assert all(
        field not in payload
        for field in (
            "tools",
            "tool_choice",
            "functions",
            "function_call",
            "parallel_tool_calls",
        )
    )
    body_text = call["body"].decode("utf-8")
    assert "600519" not in body_text
    assert "absolute_path" not in body_text
    assert "source_token" not in body_text
    history = service.history(session_id)
    assert "messages" not in history
    assert history["items"][-1] == {"role": "assistant", "text": "plain text"}
    clock.advance(config.MIN_SEND_INTERVAL_SECONDS + 0.1)
    service.close()


def test_context_is_lossy_and_recursively_exact():
    context = _context()
    encoded = serialize_context(context).decode("utf-8")
    assert set(context) == {
        "schema_version", "health", "business_date", "mainboard", "opportunities", "factors",
    }
    assert "absolute_path" not in encoded
    assert "symbol" not in encoded
    assert "positions" not in encoded
    assert "source_token" not in encoded
    assert "raw_log" not in encoded
    assert "<script>" not in encoded
    assert context["opportunities"]["public_summary"] == [
        {"category": "screened", "count": 2},
        {"category": "fresh", "count": 1},
    ]


def test_unknown_context_fields_can_never_be_serialized():
    context = _context()
    context["mainboard"]["unexpected"] = "secret"
    with pytest.raises(ValueError):
        serialize_context(context)
    context = _context()
    context["schema_version"] = True
    with pytest.raises(ValueError):
        serialize_context(context)


@pytest.mark.parametrize(
    ("status", "expected_state", "expected_code"),
    [
        (401, "failed", "invalid_credential"),
        (403, "failed", "invalid_credential"),
        (429, "failed", "upstream_rate_limited"),
        (500, "failed", "upstream_error"),
    ],
)
def test_provider_http_failures_are_stable_and_redacted(
    monkeypatch,
    status,
    expected_state,
    expected_code,
):
    logs = []
    transport = _FakeTransport(response=TransportResponse(status, b'{"secret":"provider"}'))
    service, _clock, executor = _make_service(monkeypatch, transport=transport, logger=logs.append)
    session_id = service.status()["session_id"]
    request = service.send(session_id=session_id, text="private prompt")
    _run_request(service, executor)

    result = service.poll(request["request_id"])
    assert result["state"] == expected_state
    assert result["ok"] is False
    assert result["error"]["code"] == expected_code
    assert "provider" not in json.dumps(result)
    assert "private prompt" not in json.dumps(logs)
    assert "test-only-key" not in json.dumps(logs)
    assert logs[-1]["http_status_class"] == status // 100
    service.close()


@pytest.mark.parametrize(
    ("error", "expected_state", "expected_code"),
    [
        (URLError("secret transport detail"), "service_unreachable", "upstream_unreachable"),
        (TimeoutError("secret timeout detail"), "timed_out", "upstream_timeout"),
    ],
)
def test_transport_failures_are_stable_and_redacted(
    monkeypatch,
    error,
    expected_state,
    expected_code,
):
    transport = _FakeTransport(error=error)
    service, _clock, executor = _make_service(monkeypatch, transport=transport)
    session_id = service.status()["session_id"]
    request = service.send(session_id=session_id, text="private prompt")
    _run_request(service, executor)
    result = service.poll(request["request_id"])
    assert result["state"] == expected_state
    assert result["error"]["code"] == expected_code
    assert "secret" not in json.dumps(result)
    service.close()


@pytest.mark.parametrize(
    "response",
    [
        TransportResponse(200, b"{}"),
        TransportResponse(200, b'{"choices":[]}'),
        TransportResponse(200, b'{"choices":[{"message":{"tool_calls":[]}}]}'),
        TransportResponse(200, b'{"choices":[{"message":{"content":""}}]}'),
        TransportResponse(200, b"x" * (config.MAX_RESPONSE_BYTES + 1)),
        TransportResponse("200", b'{"choices":[{"message":{"content":"reply"}}]}'),
    ],
)
def test_malformed_or_oversized_responses_are_not_returned(monkeypatch, response):
    transport = _FakeTransport(response=response)
    service, _clock, executor = _make_service(monkeypatch, transport=transport)
    session_id = service.status()["session_id"]
    request = service.send(session_id=session_id, text="status")
    _run_request(service, executor)
    result = service.poll(request["request_id"])
    assert result["state"] == "failed"
    assert result["error"]["code"] in {"invalid_response", "response_too_large"}
    assert "<script>" not in json.dumps(result)
    service.close()


def test_markup_reply_is_kept_as_plain_text_for_text_only_ui(monkeypatch):
    transport = _FakeTransport(response=_reply("<script>alert(1)</script>"))
    service, _clock, executor = _make_service(monkeypatch, transport=transport)
    session_id = service.status()["session_id"]
    request = service.send(session_id=session_id, text="return literal text")
    _run_request(service, executor)
    result = service.poll(request["request_id"])
    assert result["state"] == "replied"
    assert result["reply"] == "<script>alert(1)</script>"
    service.close()


def test_send_schema_limits_rate_and_session_authorization(monkeypatch):
    service, clock, executor = _make_service(monkeypatch)
    session_id = service.status()["session_id"]
    with pytest.raises(DeepSeekChatError) as unknown:
        service.send_payload({"session_id": session_id, "text": "x", "context": {}})
    assert unknown.value.code == "unknown_field"
    with pytest.raises(DeepSeekChatError) as too_large:
        service.send(session_id=session_id, text="x" * (config.MAX_PROMPT_CHARS + 1))
    assert too_large.value.code == "request_too_large"
    with pytest.raises(DeepSeekChatError) as invalid_session:
        service.send(session_id="not-issued", text="x")
    assert invalid_session.value.code == "invalid_session"

    accepted = service.send(session_id=session_id, text="x")
    with pytest.raises(DeepSeekChatError) as busy:
        service.send(session_id=session_id, text="y")
    assert busy.value.code == "busy"
    with pytest.raises(DeepSeekChatError) as wrong_session:
        service.poll(accepted["request_id"], "not-issued")
    assert wrong_session.value.code == "invalid_session"
    _run_request(service, executor)
    clock.advance(config.MIN_SEND_INTERVAL_SECONDS - 0.1)
    with pytest.raises(DeepSeekChatError) as rate_limited:
        service.send(session_id=session_id, text="z")
    assert rate_limited.value.code == "local_rate_limited"
    service.close()


def test_queued_cancel_is_idempotent_and_never_networks(monkeypatch):
    transport = _FakeTransport(response=_reply())
    service, _clock, executor = _make_service(monkeypatch, transport=transport)
    session_id = service.status()["session_id"]
    accepted = service.send(session_id=session_id, text="cancel me")

    cancelled = service.cancel(session_id=session_id, request_id=accepted["request_id"])
    assert cancelled["state"] == "failed"
    assert cancelled["error"]["code"] == "client_cancelled"
    assert cancelled["upstream_cancel_supported"] is False
    assert transport.calls == []
    assert service.cancel(
        session_id=session_id,
        request_id=accepted["request_id"],
    )["state"] == "failed"
    executor.run_next()
    assert service.poll(accepted["request_id"])["state"] == "failed"
    assert service.history(session_id)["items"][0]["role"] == "user"
    service.close()


def test_inflight_cancel_discards_late_reply(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class _BlockingTransport(_FakeTransport):
        def post(self, **kwargs):
            self.calls.append(kwargs)
            started.set()
            release.wait(timeout=3)
            return _reply("late reply")

    transport = _BlockingTransport()
    service, _clock, _executor = _make_service(monkeypatch, transport=transport)
    session_id = service.status()["session_id"]
    accepted = service.send(session_id=session_id, text="cancel in flight")
    request_id = accepted["request_id"]
    worker = threading.Thread(target=service._run_request, args=(request_id,))
    worker.start()
    assert started.wait(timeout=3)
    waiting = service.poll(request_id)
    assert waiting["state"] == "waiting"
    assert 250 <= waiting["poll_after_ms"] <= 1_000
    cancelled = service.cancel(session_id=session_id, request_id=request_id)
    assert cancelled["state"] == "failed"
    assert cancelled["error"]["code"] == "client_cancelled"
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    result = service.poll(request_id)
    assert result["state"] == "failed"
    assert result["error"]["code"] == "client_cancelled"
    assert "late reply" not in json.dumps(service.history(session_id))
    service.close()


def test_close_cancels_worker_and_clears_process_local_transcript(monkeypatch):
    started = threading.Event()
    finished = threading.Event()

    class _CooperativeTransport:
        def post(self, **kwargs):
            started.set()
            try:
                kwargs["cancel_event"].wait(timeout=10)
                return _reply("late reply")
            finally:
                finished.set()

    service = DeepSeekChatService(
        transport=_CooperativeTransport(),
        context_provider=lambda: _context(),
        id_factory=_Ids(),
    )
    monkeypatch.setenv(config.FEATURE_ENV, "1")
    monkeypatch.setenv(config.API_KEY_ENV, "test-only-key")
    session_id = service.status()["session_id"]
    service.send(session_id=session_id, text="close me")
    assert started.wait(timeout=3)

    started_at = time.monotonic()
    service.close()
    elapsed = time.monotonic() - started_at

    assert elapsed < config.CLOSE_WAIT_SECONDS
    assert finished.is_set()
    assert service._sessions == {}
    assert service._requests == {}
    assert service._inflight == set()
    assert service._executor is None
    service.close()


def test_close_uses_bounded_nonwaiting_executor_shutdown(monkeypatch):
    class _NeverFuture:
        def __init__(self):
            self.cancelled = False
            self.timeout = None

        def cancel(self):
            return False

        def result(self, timeout=None):
            self.timeout = timeout
            raise TimeoutError

    class _BoundedExecutor:
        def __init__(self):
            self.future = _NeverFuture()
            self.shutdown_args = None

        def submit(self, _function, *_args):
            return self.future

        def shutdown(self, *, wait=False, cancel_futures=False):
            if wait:
                raise AssertionError("service close must not wait in executor shutdown")
            self.shutdown_args = (wait, cancel_futures)

    executor = _BoundedExecutor()
    service, _clock, _manual = _make_service(monkeypatch, executor=executor)
    session_id = service.status()["session_id"]
    service.send(session_id=session_id, text="bounded close")

    service.close()

    assert executor.shutdown_args == (False, True)
    assert executor.future.timeout is not None
    assert service._sessions == {}
    assert service._requests == {}


def test_default_executor_worker_is_daemon_and_close_is_bounded(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking_context():
        started.set()
        try:
            release.wait(timeout=10)
            return _context()
        finally:
            finished.set()

    monkeypatch.setenv(config.FEATURE_ENV, "1")
    monkeypatch.setenv(config.API_KEY_ENV, "test-only-key")
    service = DeepSeekChatService(
        transport=_FakeTransport(response=_reply()),
        context_provider=blocking_context,
        id_factory=_Ids(),
    )
    session_id = service.status()["session_id"]
    service.send(session_id=session_id, text="daemon worker")
    assert started.wait(timeout=3)

    executor = service._executor
    assert executor is not None
    worker = executor._thread
    assert worker.daemon is True

    monkeypatch.setattr(config, "CLOSE_WAIT_SECONDS", 0.05)
    started_at = time.monotonic()
    service.close()
    elapsed = time.monotonic() - started_at
    assert elapsed < 1
    assert service._executor is None
    assert service._sessions == {}
    assert service._requests == {}

    release.set()
    assert finished.wait(timeout=3)
    worker.join(timeout=3)
    assert not worker.is_alive()


def test_default_executor_blocked_context_does_not_hold_process_exit():
    script = """
import os
import threading

from qtrade_adapters.deepseek_chat import config
from qtrade_adapters.deepseek_chat.service import DeepSeekChatService

os.environ[config.FEATURE_ENV] = "1"
os.environ[config.API_KEY_ENV] = "test-only-key"
config.CLOSE_WAIT_SECONDS = 0.05

started = threading.Event()

def blocked_context():
    started.set()
    threading.Event().wait(timeout=60)
    return {"health": "ok"}

class NoNetworkTransport:
    def post(self, **_kwargs):
        raise AssertionError("blocked context should prevent network")

service = DeepSeekChatService(
    transport=NoNetworkTransport(),
    context_provider=blocked_context,
)
session_id = service.status()["session_id"]
service.send(session_id=session_id, text="process boundary")
if not started.wait(timeout=2):
    raise AssertionError("worker did not start")
if not service._executor._thread.daemon:
    raise AssertionError("worker is not daemon")
service.close()
print("bounded-process-exit", flush=True)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 0, result.stderr
    assert "bounded-process-exit" in result.stdout


def test_history_is_bounded_to_ten_turns(monkeypatch):
    service, clock, executor = _make_service(monkeypatch)
    session_id = service.status()["session_id"]
    for index in range(11):
        accepted = service.send(session_id=session_id, text=f"message {index}")
        _run_request(service, executor)
        assert service.poll(accepted["request_id"])["state"] == "replied"
        clock.advance(config.MIN_SEND_INTERVAL_SECONDS + 0.01)
    history = service.history(session_id)
    assert len(history["items"]) <= config.MAX_HISTORY_MESSAGES
    assert (
        sum(len(item["text"].encode("utf-8")) for item in history["items"])
        <= config.MAX_HISTORY_BYTES
    )
    assert history["truncated"] is True
    service.close()


def test_terminal_request_records_and_sessions_are_bounded(monkeypatch):
    service, clock, executor = _make_service(monkeypatch)
    session_id = service.status()["session_id"]
    for index in range(config.MAX_REQUEST_RECORDS + 3):
        accepted = service.send(session_id=session_id, text=f"message {index}")
        _run_request(service, executor)
        assert service.poll(accepted["request_id"])["state"] == "replied"
        clock.advance(config.MIN_SEND_INTERVAL_SECONDS + 0.01)
    assert len(service._requests) == config.MAX_REQUEST_RECORDS
    with pytest.raises(DeepSeekChatError) as old_request:
        service.poll("req_test_2")
    assert old_request.value.code == "unknown_request"

    for _index in range(config.MAX_SESSIONS + 3):
        service.status()
    assert len(service._sessions) <= config.MAX_SESSIONS
    service.close()


def test_server_routes_are_before_external_bridge_and_do_not_inherit_cors(monkeypatch):
    service, _clock, executor = _make_service(monkeypatch)
    monkeypatch.setattr(
        server.qtrade_base_bridge,
        "try_serve",
        lambda *_args, **_kwargs: pytest.fail("chat routes must not reach external bridge"),
    )
    httpd, thread = _start_api_server(monkeypatch, service)
    opener = build_opener(ProxyHandler({}))
    try:
        port = httpd.server_address[1]
        status, headers, body = _http_json(opener, port, "/api/deepseek-chat/status")
        assert status == 200
        assert body["state"] == "ready"
        assert "Access-Control-Allow-Origin" not in headers

        accepted_status, accepted_headers, accepted = _http_json(
            opener,
            port,
            "/api/deepseek-chat/send",
            method="POST",
            body={"session_id": body["session_id"], "text": "hello"},
        )
        assert accepted_status == 202
        assert accepted["state"] == "accepted"
        assert "Access-Control-Allow-Origin" not in accepted_headers
        _run_request(service, executor)

        poll_status, _poll_headers, polled = _http_json(
            opener, port, f"/api/deepseek-chat/poll?request_id={accepted['request_id']}"
        )
        assert poll_status == 200
        assert polled["state"] == "replied"

        missing_status, _missing_headers, missing = _http_json(
            opener, port, "/api/deepseek-chat/unknown"
        )
        assert missing_status == 404
        assert missing["error"] == "not_found"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_server_backend_responses_match_frozen_ui_contract(monkeypatch):
    transport = _FakeTransport(response=_reply("plain text"))
    service, _clock, executor = _make_service(monkeypatch, transport=transport)
    httpd, thread = _start_api_server(monkeypatch, service)
    opener = build_opener(ProxyHandler({}))
    try:
        port = httpd.server_address[1]
        status, _headers, status_body = _http_json(
            opener, port, "/api/deepseek-chat/status"
        )
        assert status == 200
        assert status_body["state"] == "ready"
        session_id = status_body["session_id"]

        accepted_status, _headers, accepted = _http_json(
            opener,
            port,
            "/api/deepseek-chat/send",
            method="POST",
            body={"session_id": session_id, "text": "hello"},
        )
        assert accepted_status == 202
        assert set(accepted) == {
            "ok",
            "request_id",
            "session_id",
            "state",
            "poll_after_ms",
            "upstream_cancel_supported",
        }
        assert accepted["state"] == "accepted"
        assert 250 <= accepted["poll_after_ms"] <= 1_000

        _run_request(service, executor)
        poll_status, _headers, replied = _http_json(
            opener,
            port,
            f"/api/deepseek-chat/poll?request_id={accepted['request_id']}&session_id={session_id}",
        )
        assert poll_status == 200
        assert replied["state"] == "replied"
        assert replied["reply"] == "plain text"
        assert isinstance(replied["reply"], str)

        history_status, _headers, history = _http_json(
            opener,
            port,
            f"/api/deepseek-chat/history?session_id={session_id}&limit=20",
        )
        assert history_status == 200
        assert "messages" not in history
        assert history["items"] == [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "plain text"},
        ]
        assert all(set(item) == {"role", "text"} for item in history["items"])
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_server_reports_unconfigured_with_stable_error_and_503(monkeypatch):
    monkeypatch.setenv(config.FEATURE_ENV, "1")
    monkeypatch.delenv(config.API_KEY_ENV, raising=False)
    service = DeepSeekChatService(transport=_FakeTransport(response=_reply()))
    httpd, thread = _start_api_server(monkeypatch, service)
    opener = build_opener(ProxyHandler({}))
    try:
        status, _headers, body = _http_json(
            opener, httpd.server_address[1], "/api/deepseek-chat/status"
        )
        assert status == 503
        assert body["state"] == "unconfigured"
        assert body["error"]["code"] == "unconfigured"
        assert body["error"]["retryable"] is False
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_server_rejects_oversized_body_and_wrong_methods(monkeypatch):
    service, _clock, _executor = _make_service(monkeypatch)
    httpd, thread = _start_api_server(monkeypatch, service)
    try:
        port = httpd.server_address[1]
        oversized = json.dumps(
            {"session_id": "x", "text": "x" * config.MAX_REQUEST_BODY_BYTES},
            separators=(",", ":"),
        ).encode("utf-8")
        status, headers, body = _loopback_json(
            port,
            "/api/deepseek-chat/send",
            method="POST",
            body=oversized,
        )
        assert status == 413
        assert body["error"] == "request_too_large"
        assert headers["Connection"].lower() == "close"
        assert headers["Cache-Control"] == "no-store"
        assert "Access-Control-Allow-Origin" not in headers

        for method in ("POST", "PUT", "DELETE", "PATCH"):
            method_status, method_headers, method_body = _loopback_json(
                port,
                "/api/deepseek-chat/status",
                method=method,
                body=b"{}",
            )
            assert method_status == 405
            assert method_body["error"] == "method_not_allowed"
            assert method_headers["Connection"].lower() == "close"
            assert method_headers["Cache-Control"] == "no-store"
            assert "Access-Control-Allow-Origin" not in method_headers

        normal_status, _normal_headers, normal_body = _loopback_json(
            port,
            "/api/deepseek-chat/status",
        )
        assert normal_status == 200
        assert normal_body["state"] == "ready"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_rejected_http_error_is_readable_and_closed(monkeypatch):
    service, _clock, _executor = _make_service(monkeypatch)
    httpd, thread = _start_api_server(monkeypatch, service)
    opener = build_opener(ProxyHandler({}))
    try:
        oversized = json.dumps(
            {"session_id": "x", "text": "x" * config.MAX_REQUEST_BODY_BYTES},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/deepseek-chat/send",
            data=oversized,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as raised:
            opener.open(request, timeout=3)
        error = raised.value
        try:
            assert error.code == 413
            assert error.headers["Connection"].lower() == "close"
            assert json.loads(error.read().decode("utf-8")) == {
                "error": "request_too_large",
                "message": "request body is too large",
            }
        finally:
            error.close()

        status, _headers, body = _loopback_json(
            httpd.server_address[1],
            "/api/deepseek-chat/status",
        )
        assert status == 200
        assert body["state"] == "ready"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_slow_incomplete_rejection_closes_within_deadline(monkeypatch):
    service, _clock, _executor = _make_service(monkeypatch)
    httpd, thread = _start_api_server(monkeypatch, service)
    port = httpd.server_address[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=3)
    client.settimeout(3)
    try:
        request = (
            b"POST /api/deepseek-chat/send HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 65536\r\n\r\n"
            b"{"
        )
        started = time.monotonic()
        client.sendall(request)
        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
        elapsed = time.monotonic() - started
        assert elapsed < 3
        assert b" 413 " in response
        assert b'"error": "request_too_large"' in response
        status, _headers, body = _loopback_json(port, "/api/deepseek-chat/status")
        assert status == 200
        assert body["state"] == "ready"
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)
        service.close()


def test_server_context_provider_uses_only_qtrade_summary(monkeypatch):
    monkeypatch.setattr(
        server,
        "SERVICE",
        SimpleNamespace(
            universe_summary={
                "total": 10,
                "computable": 8,
                "tradable": 9,
                "candidate": 2,
                "symbol": "600519",
                "as_of": "2024-01-02",
                "source": "csv",
            },
            _candidate_symbols={"600519", "000001"},
        ),
    )
    monkeypatch.setattr(
        server,
        "read_update_status",
        lambda: {
            "trade_date": "2024-01-02",
            "state": "success",
            "freshness": {"portal": {"verified": True, "as_of": "2024-01-02"}},
        },
    )
    monkeypatch.setattr(
        server,
        "FACTOR_LIBRARY",
        SimpleNamespace(list_items=lambda: [{"as_of": "2024-01-02"}]),
    )
    context = server._build_deepseek_context()
    encoded = json.dumps(context, ensure_ascii=False)
    assert context["mainboard"] == {
        "total": 10,
        "computable": 8,
        "tradable": 9,
        "as_of": "2024-01-02",
        "source": "csv",
    }
    assert context["opportunities"]["count"] == 2
    assert context["factors"]["scheme_count"] == 1
    for forbidden in ("600519", "symbol", "candidate", "positions", "path", "environment"):
        assert forbidden not in encoded


def test_chat_source_has_no_process_or_dynamic_code_surface():
    source = SERVICE_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_imports = {"subprocess", "ctypes"}
    banned_calls = {"eval", "exec", "system", "popen"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] not in banned_imports for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_imports
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id.lower() not in banned_calls
    assert "third_party" not in source
    assert "quantapi" not in source
    assert "HTTPSConnection" in source
    assert "create_default_context" in source
    assert "ProxyHandler" not in source
    assert '"tools":' not in source
    assert '"functions":' not in source
    assert '"function_call":' not in source
    assert '"parallel_tool_calls":' not in source
    assert "_try_base_deck(path)" in Path(ROOT / "server.py").read_text(encoding="utf-8")


def test_server_chat_route_precedes_external_base_in_source():
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    assert source.index("if self._is_deepseek_chat_path(path):") < source.index(
        "if self._try_base_deck(path):"
    )
