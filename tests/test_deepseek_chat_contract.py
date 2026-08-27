"""Offline contracts for the QTrade-owned read-only DeepSeek chat service."""

from __future__ import annotations

import ast
from concurrent.futures import Future
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.error import URLError
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
)


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
            return error.code, dict(error.headers), json.loads(error.read().decode("utf-8"))
        raise


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
    assert service.poll(accepted["request_id"])["state"] == "accepted"
    assert transport.calls == []

    _run_request(service, executor)
    result = service.poll(accepted["request_id"], session_id)
    assert result["state"] == "replied"
    assert result["reply"]["text"] == "plain text"
    assert result["reply"]["message_id"]
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
    assert service.history(session_id)["messages"][-1]["text"] == "plain text"
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
    assert result["reply"]["text"] == "<script>alert(1)</script>"
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
    assert service.history(session_id)["messages"][0]["role"] == "user"
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


def test_history_is_bounded_to_ten_turns(monkeypatch):
    service, clock, executor = _make_service(monkeypatch)
    session_id = service.status()["session_id"]
    for index in range(11):
        accepted = service.send(session_id=session_id, text=f"message {index}")
        _run_request(service, executor)
        assert service.poll(accepted["request_id"])["state"] == "replied"
        clock.advance(config.MIN_SEND_INTERVAL_SECONDS + 0.01)
    history = service.history(session_id)
    assert len(history["messages"]) <= config.MAX_HISTORY_MESSAGES
    assert (
        sum(len(item["text"].encode("utf-8")) for item in history["messages"])
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


def test_server_rejects_oversized_body_and_wrong_methods(monkeypatch):
    service, _clock, _executor = _make_service(monkeypatch)
    httpd, thread = _start_api_server(monkeypatch, service)
    opener = build_opener(ProxyHandler({}))
    try:
        port = httpd.server_address[1]
        status, _headers, body = _http_json(
            opener,
            port,
            "/api/deepseek-chat/send",
            method="POST",
            body={"session_id": "x", "text": "x" * config.MAX_REQUEST_BODY_BYTES},
        )
        assert status == 413
        assert body["error"] == "request_too_large"
        method_status, _method_headers, method_body = _http_json(
            opener,
            port,
            "/api/deepseek-chat/status",
            method="POST",
            body={},
        )
        assert method_status == 405
        assert method_body["error"] == "method_not_allowed"
        for method in ("PUT", "DELETE", "PATCH"):
            unsupported_status, unsupported_headers, unsupported_body = _http_json(
                opener,
                port,
                "/api/deepseek-chat/status",
                method=method,
                body={},
            )
            assert unsupported_status == 405
            assert unsupported_body["error"] == "method_not_allowed"
            assert "Access-Control-Allow-Origin" not in unsupported_headers
    finally:
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
    assert "ProxyHandler({})" in source
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
