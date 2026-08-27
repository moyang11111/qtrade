"""QTrade-owned, text-only DeepSeek service with a bounded local state machine."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import secrets
import socket
import threading
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import config
from .context import ContextError, ContextProvider, build_context, serialize_context


class Clock(Protocol):
    """Clock seam used by deterministic tests."""

    def monotonic(self) -> float: ...

    def time(self) -> float: ...


class DeepSeekTransport(Protocol):
    """Small transport seam; implementations must not follow redirects."""

    def post(
        self,
        *,
        url: str,
        body: bytes,
        api_key: str,
        connect_timeout: float,
        total_timeout: float,
        cancel_event: threading.Event,
    ) -> "TransportResponse": ...


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class UrllibTransport:
    """Fixed-host HTTPS transport with a response-size bound."""

    def post(
        self,
        *,
        url: str,
        body: bytes,
        api_key: str,
        connect_timeout: float,
        total_timeout: float,
        cancel_event: threading.Event,
    ) -> TransportResponse:
        if url != config.DEEPSEEK_CHAT_URL:
            raise OSError("unsupported endpoint")
        if cancel_event.is_set():
            raise _LocalTransportCancelled

        request = Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        started = time.monotonic()
        # Do not inherit HTTP(S)_PROXY from the process environment.  The
        # provider host is fixed, and a user-controlled proxy would otherwise
        # become an alternate destination for the credential.
        opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
        timeout = min(float(connect_timeout), float(total_timeout))
        try:
            with opener.open(request, timeout=timeout) as response:
                response_body = response.read(config.MAX_RESPONSE_BYTES + 1)
                if cancel_event.is_set():
                    raise _LocalTransportCancelled
                if time.monotonic() - started > total_timeout:
                    raise TimeoutError
                return TransportResponse(int(response.status), response_body)
        except HTTPError as error:
            try:
                response_body = error.read(config.MAX_RESPONSE_BYTES + 1)
            except OSError:
                response_body = b""
            if cancel_event.is_set():
                raise _LocalTransportCancelled
            return TransportResponse(int(error.code), response_body)


@dataclass(frozen=True)
class _ErrorDefinition:
    status_code: int
    retryable: bool
    message: str


_ERRORS = {
    "feature_disabled": _ErrorDefinition(409, False, "DeepSeek chat is disabled"),
    "unconfigured": _ErrorDefinition(503, False, "DeepSeek chat is not configured"),
    "invalid_request": _ErrorDefinition(400, False, "The request is invalid"),
    "unknown_field": _ErrorDefinition(400, False, "The request contains an unknown field"),
    "request_too_large": _ErrorDefinition(413, False, "The request is too large"),
    "invalid_session": _ErrorDefinition(404, False, "The chat session is not available"),
    "unknown_request": _ErrorDefinition(404, False, "The chat request is not available"),
    "local_rate_limited": _ErrorDefinition(429, True, "Please wait before sending again"),
    "busy": _ErrorDefinition(409, True, "Another chat request is in progress"),
    "service_closed": _ErrorDefinition(503, True, "The chat service is closed"),
    "context_unavailable": _ErrorDefinition(503, True, "QTrade context is temporarily unavailable"),
    "context_too_large": _ErrorDefinition(413, False, "QTrade context is too large"),
    "upstream_unreachable": _ErrorDefinition(502, True, "The DeepSeek service is unreachable"),
    "upstream_timeout": _ErrorDefinition(504, True, "The DeepSeek service timed out"),
    "invalid_credential": _ErrorDefinition(502, False, "The DeepSeek credential was rejected"),
    "upstream_rate_limited": _ErrorDefinition(429, True, "The DeepSeek service is rate limited"),
    "upstream_rejected": _ErrorDefinition(502, False, "The DeepSeek service rejected the request"),
    "upstream_error": _ErrorDefinition(502, True, "The DeepSeek service returned an error"),
    "invalid_response": _ErrorDefinition(502, True, "The DeepSeek response was invalid"),
    "response_too_large": _ErrorDefinition(502, True, "The DeepSeek response was too large"),
    "client_cancelled": _ErrorDefinition(499, False, "Local waiting was cancelled"),
    "timed_out": _ErrorDefinition(504, True, "The chat request timed out"),
    "internal_error": _ErrorDefinition(503, True, "The chat service is temporarily unavailable"),
}


class DeepSeekChatError(Exception):
    """Stable public error; provider details are intentionally discarded."""

    def __init__(self, code: str):
        definition = _ERRORS.get(code, _ERRORS["internal_error"])
        self.code = code if code in _ERRORS else "internal_error"
        self.status_code = definition.status_code
        self.retryable = definition.retryable
        self.public_message = definition.message
        super().__init__(self.public_message)


class _LocalTransportCancelled(Exception):
    pass


@dataclass
class _Message:
    message_id: str
    role: str
    text: str
    created_at: str


@dataclass
class _Request:
    request_id: str
    session_id: str
    text: str
    state: str
    created_at: str
    accepted_at: str
    started_at: str | None = None
    finished_at: str | None = None
    reply_text: str | None = None
    reply_message_id: str | None = None
    error: DeepSeekChatError | None = None
    future: Future | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    cancel_requested: bool = False
    upstream_started: bool = False
    discarded: bool = False
    request_bytes: int = 0
    response_bytes: int = 0
    http_status: int | None = None
    started_monotonic: float | None = None


@dataclass
class _Session:
    session_id: str
    messages: list[_Message] = field(default_factory=list)
    active_request_id: str | None = None
    last_send_monotonic: float | None = None
    history_truncated: bool = False


class _SystemClock:
    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def time() -> float:
        return time.time()


def _opaque_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _timestamp(clock: Clock) -> str:
    return (
        datetime.fromtimestamp(clock.time(), timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


class DeepSeekChatService:
    """Bounded, process-local service for the optional text-only chat."""

    def __init__(
        self,
        *,
        transport: DeepSeekTransport | None = None,
        clock: Clock | None = None,
        executor_factory: Callable[[], Executor] | None = None,
        context_provider: Callable[[], Mapping[str, object]] | None = None,
        logger: Callable[[Mapping[str, object]], object] | None = None,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self._transport = transport or UrllibTransport()
        self._clock = clock or _SystemClock()
        self._executor_factory = executor_factory or (
            lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtrade-deepseek")
        )
        self._context_provider = context_provider or (
            lambda: build_context(ContextProvider())
        )
        self._logger = logger or (lambda _event: None)
        self._id_factory = id_factory or _opaque_id
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._requests: dict[str, _Request] = {}
        self._inflight: set[str] = set()
        self._recent_sends: deque[float] = deque()
        self._executor: Executor | None = None
        self._closed = False

    def close(self) -> None:
        """Stop accepting work and cancel queued futures without waiting forever."""

        with self._lock:
            self._closed = True
            for request in self._requests.values():
                if request.state in {"accepted", "waiting"}:
                    request.cancel_requested = True
                    request.discarded = True
                    request.cancel_event.set()
                    if request.state == "accepted" and request.future is not None:
                        if request.future.cancel():
                            self._inflight.discard(request.request_id)
                    self._finish_locked(
                        request,
                        "failed",
                        DeepSeekChatError("client_cancelled"),
                    )
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    def status(self, session_id: str | None = None) -> dict[str, object]:
        """Return local readiness only; this method never contacts the provider."""

        if not config.feature_enabled():
            return self._status_payload("disabled", None, None, None)
        api_key = config.read_api_key()
        if api_key is None:
            return self._status_payload("unconfigured", None, None, None)

        with self._lock:
            if self._closed:
                return self._status_payload(
                    "failed", None, None, DeepSeekChatError("service_closed")
                )
            session = self._get_session_locked(session_id)
            request = self._active_request_locked(session)
            if request is None:
                return self._status_payload("ready", session.session_id, None, None)
            return self._status_payload(
                request.state,
                session.session_id,
                request.request_id,
                request.error,
            )

    def send_payload(self, payload: object) -> dict[str, object]:
        """Validate the exact HTTP payload before entering the state machine."""

        if not isinstance(payload, Mapping):
            raise DeepSeekChatError("invalid_request")
        unknown = set(payload) - {"session_id", "text"}
        if unknown:
            raise DeepSeekChatError("unknown_field")
        if set(payload) != {"session_id", "text"}:
            raise DeepSeekChatError("invalid_request")
        return self.send(session_id=payload["session_id"], text=payload["text"])

    def send(self, *, session_id: object, text: object) -> dict[str, object]:
        """Accept exactly one bounded request and schedule it locally."""

        if not config.feature_enabled():
            raise DeepSeekChatError("feature_disabled")
        if config.read_api_key() is None:
            raise DeepSeekChatError("unconfigured")
        self._validate_session_id(session_id)
        clean_text = self._validate_text(text)
        now = self._clock.monotonic()

        with self._lock:
            if self._closed:
                raise DeepSeekChatError("service_closed")
            session = self._sessions.get(session_id)
            if session is None:
                raise DeepSeekChatError("invalid_session")
            self._prune_rate_window_locked(now)
            if len(self._inflight) >= config.MAX_ACTIVE_REQUESTS:
                raise DeepSeekChatError("busy")
            if len(self._recent_sends) >= config.MAX_REQUESTS_PER_MINUTE:
                raise DeepSeekChatError("local_rate_limited")
            if (
                session.last_send_monotonic is not None
                and now - session.last_send_monotonic < config.MIN_SEND_INTERVAL_SECONDS
            ):
                raise DeepSeekChatError("local_rate_limited")

            self._prune_request_records_locked()
            timestamp = _timestamp(self._clock)
            request = _Request(
                request_id=self._id_factory("req"),
                session_id=session_id,
                text=clean_text,
                state="accepted",
                created_at=timestamp,
                accepted_at=timestamp,
            )
            self._requests[request.request_id] = request
            self._inflight.add(request.request_id)
            session.active_request_id = request.request_id
            session.last_send_monotonic = now
            self._recent_sends.append(now)
            self._append_message_locked(session, "user", clean_text)
            try:
                executor = self._get_executor_locked()
                request.future = executor.submit(self._run_request, request.request_id)
            except Exception:
                self._inflight.discard(request.request_id)
                session.active_request_id = None
                self._requests.pop(request.request_id, None)
                raise DeepSeekChatError("service_closed") from None

            return {
                "ok": True,
                "request_id": request.request_id,
                "session_id": request.session_id,
                "state": "accepted",
                "poll_after_ms": config.POLL_AFTER_MS,
                "upstream_cancel_supported": False,
            }

    def poll(self, request_id: object, session_id: object | None = None) -> dict[str, object]:
        """Return a request snapshot without reading configuration or networking."""

        self._validate_request_id(request_id)
        if session_id is not None:
            self._validate_session_id(session_id)
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise DeepSeekChatError("unknown_request")
            if session_id is not None and request.session_id != session_id:
                raise DeepSeekChatError("invalid_session")
            return self._request_payload_locked(request)

    def history(self, session_id: object, limit: object | None = None) -> dict[str, object]:
        """Return bounded in-memory plain-text history only."""

        self._validate_session_id(session_id)
        if limit is None:
            count = config.MAX_HISTORY_MESSAGES
        else:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise DeepSeekChatError("invalid_request")
            count = max(1, min(limit, config.MAX_HISTORY_MESSAGES))
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise DeepSeekChatError("invalid_session")
            messages = session.messages[-count:]
            return {
                "ok": True,
                "session_id": session.session_id,
                "messages": [
                    {
                        "message_id": message.message_id,
                        "role": message.role,
                        "text": message.text,
                        "created_at": message.created_at,
                    }
                    for message in messages
                ],
                "truncated": session.history_truncated,
            }

    def cancel(
        self,
        *,
        session_id: object,
        request_id: object,
    ) -> dict[str, object]:
        """Cancel local waiting and discard late results; never cancel upstream."""

        self._validate_session_id(session_id)
        self._validate_request_id(request_id)
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                raise DeepSeekChatError("unknown_request")
            if request.session_id != session_id:
                raise DeepSeekChatError("invalid_session")
            if request.state not in {"accepted", "waiting"}:
                return self._request_payload_locked(request)
            request.cancel_requested = True
            request.discarded = True
            request.cancel_event.set()
            if request.state == "accepted" and request.future is not None:
                if request.future.cancel():
                    self._inflight.discard(request.request_id)
            self._finish_locked(request, "failed", DeepSeekChatError("client_cancelled"))
            return self._request_payload_locked(request)

    def _get_executor_locked(self) -> Executor:
        if self._executor is None:
            self._executor = self._executor_factory()
        return self._executor

    def _get_session_locked(self, session_id: str | None) -> _Session:
        if session_id is not None:
            self._validate_session_id(session_id)
            session = self._sessions.get(session_id)
            if session is None:
                raise DeepSeekChatError("invalid_session")
            return session
        if len(self._sessions) >= config.MAX_SESSIONS:
            for existing_id, existing in self._sessions.items():
                if existing.active_request_id is None:
                    del self._sessions[existing_id]
                    break
            else:
                raise DeepSeekChatError("busy")
        new_session_id = self._id_factory("session")
        session = _Session(new_session_id)
        self._sessions[new_session_id] = session
        return session

    def _prune_request_records_locked(self) -> None:
        while len(self._requests) >= config.MAX_REQUEST_RECORDS:
            oldest_id = next(
                (
                    request_id
                    for request_id, request in self._requests.items()
                    if request.state not in {"accepted", "waiting"}
                ),
                None,
            )
            if oldest_id is None:
                raise DeepSeekChatError("busy")
            self._requests.pop(oldest_id, None)

    def _active_request_locked(self, session: _Session) -> _Request | None:
        if session.active_request_id is None:
            return None
        request = self._requests.get(session.active_request_id)
        if request is None or request.state not in {"accepted", "waiting"}:
            session.active_request_id = None
            return None
        return request

    def _run_request(self, request_id: str) -> None:
        request: _Request | None
        try:
            with self._lock:
                request = self._requests.get(request_id)
                if request is None:
                    return
                if request.cancel_requested or request.discarded:
                    self._finish_locked(
                        request,
                        "failed",
                        DeepSeekChatError("client_cancelled"),
                    )
                    return
                request.state = "waiting"
                request.started_at = _timestamp(self._clock)
                request.started_monotonic = self._clock.monotonic()

            api_key = config.read_api_key()
            if api_key is None:
                self._finish_by_id(request_id, "failed", DeepSeekChatError("unconfigured"))
                return

            try:
                context = self._context_provider()
                context_body = serialize_context(context)
            except ContextError as error:
                self._finish_by_id(request_id, "failed", DeepSeekChatError(error.code))
                return
            except Exception:
                self._finish_by_id(request_id, "failed", DeepSeekChatError("context_unavailable"))
                return

            if len(context_body) > config.MAX_CONTEXT_BYTES:
                self._finish_by_id(request_id, "failed", DeepSeekChatError("context_too_large"))
                return
            context_text = context_body.decode("utf-8")
            with self._lock:
                request = self._requests.get(request_id)
                if request is None:
                    return
                if request.cancel_requested or request.discarded:
                    self._finish_locked(
                        request,
                        "failed",
                        DeepSeekChatError("client_cancelled"),
                    )
                    return
                body = self._build_request_body(request.text, context_text)
                if len(body) > config.MAX_WIRE_REQUEST_BYTES:
                    self._finish_locked(
                        request,
                        "failed",
                        DeepSeekChatError("request_too_large"),
                    )
                    return
                request.request_bytes = len(body)
                request.upstream_started = True

            try:
                response = self._transport.post(
                    url=config.DEEPSEEK_CHAT_URL,
                    body=body,
                    api_key=api_key,
                    connect_timeout=config.CONNECT_TIMEOUT_SECONDS,
                    total_timeout=config.TOTAL_TIMEOUT_SECONDS,
                    cancel_event=request.cancel_event,
                )
            except _LocalTransportCancelled:
                self._finish_by_id(request_id, "failed", DeepSeekChatError("client_cancelled"))
                return
            except (TimeoutError, socket.timeout):
                self._finish_by_id(request_id, "timed_out", DeepSeekChatError("upstream_timeout"))
                return
            except (HTTPError, URLError, OSError):
                self._finish_by_id(
                    request_id,
                    "service_unreachable",
                    DeepSeekChatError("upstream_unreachable"),
                )
                return
            except Exception:
                self._finish_by_id(request_id, "failed", DeepSeekChatError("upstream_error"))
                return

            with self._lock:
                request = self._requests.get(request_id)
                if request is None:
                    return
                if request.cancel_requested or request.discarded:
                    self._finish_locked(
                        request,
                        "failed",
                        DeepSeekChatError("client_cancelled"),
                    )
                    return
                if (
                    request.started_monotonic is not None
                    and self._clock.monotonic() - request.started_monotonic
                    > config.TOTAL_TIMEOUT_SECONDS
                ):
                    self._finish_locked(
                        request,
                        "timed_out",
                        DeepSeekChatError("upstream_timeout"),
                    )
                    return

            self._handle_response(request_id, response)
        except Exception:
            self._finish_by_id(request_id, "failed", DeepSeekChatError("internal_error"))
        finally:
            with self._lock:
                self._inflight.discard(request_id)

    def _handle_response(self, request_id: str, response: object) -> None:
        if (
            not isinstance(response, TransportResponse)
            or not isinstance(response.status, int)
            or isinstance(response.status, bool)
            or not 100 <= response.status <= 599
            or not isinstance(response.body, bytes)
        ):
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return
        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return
            request.response_bytes = len(response.body)
            request.http_status = response.status
            if request.cancel_requested or request.discarded:
                self._finish_locked(
                    request,
                    "failed",
                    DeepSeekChatError("client_cancelled"),
                )
                return
        if len(response.body) > config.MAX_RESPONSE_BYTES:
            self._finish_by_id(request_id, "failed", DeepSeekChatError("response_too_large"))
            return
        if response.status != 200:
            self._finish_by_id(
                request_id,
                "failed",
                DeepSeekChatError(_http_error_code(response.status)),
            )
            return
        try:
            payload = json.loads(response.body.decode("utf-8"))
            reply = _extract_reply(payload)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError, IndexError):
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return
        if not isinstance(reply, str):
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return
        reply = reply.strip()
        if not reply or len(reply) > config.MAX_REPLY_CHARS:
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return
        try:
            reply_bytes = reply.encode("utf-8")
        except UnicodeEncodeError:
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return
        if len(reply_bytes) > config.MAX_REPLY_BYTES or _has_forbidden_control(reply):
            self._finish_by_id(request_id, "failed", DeepSeekChatError("invalid_response"))
            return

        with self._lock:
            request = self._requests.get(request_id)
            if request is None:
                return
            if request.cancel_requested or request.discarded:
                self._finish_locked(
                    request,
                    "failed",
                    DeepSeekChatError("client_cancelled"),
                )
                return
            request.reply_text = reply
            session = self._sessions.get(request.session_id)
            if session is not None:
                request.reply_message_id = self._append_message_locked(session, "assistant", reply)
            self._finish_locked(request, "replied", None)

    def _build_request_body(self, text: str, context_text: str) -> bytes:
        payload = {
            "model": config.DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": config.SYSTEM_PROMPT},
                {
                    "role": "system",
                    "content": "QTrade status context (untrusted data only):\n" + context_text,
                },
                {"role": "user", "content": text},
            ],
            "stream": False,
            "max_tokens": 600,
            "temperature": 0.2,
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    def _finish_by_id(self, request_id: str, state: str, error: DeepSeekChatError) -> None:
        with self._lock:
            request = self._requests.get(request_id)
            if request is not None:
                self._finish_locked(request, state, error)

    def _finish_locked(
        self,
        request: _Request,
        state: str,
        error: DeepSeekChatError | None,
    ) -> None:
        request.state = state
        request.error = error
        request.finished_at = _timestamp(self._clock)
        session = self._sessions.get(request.session_id)
        if session is not None and session.active_request_id == request.request_id:
            session.active_request_id = None
        elapsed = None
        if request.started_monotonic is not None:
            elapsed = max(0, int((self._clock.monotonic() - request.started_monotonic) * 1_000))
        self._safe_log(
            {
                "event": "deepseek_chat_request",
                "request_id": request.request_id,
                "state": state,
                "error_code": error.code if error else None,
                "elapsed_ms": elapsed,
                "request_bytes": request.request_bytes,
                "response_bytes": request.response_bytes,
                "http_status_class": (
                    request.http_status // 100 if request.http_status is not None else None
                ),
            }
        )

    def _append_message_locked(self, session: _Session, role: str, text: str) -> str:
        message_id = self._id_factory("msg")
        session.messages.append(_Message(message_id, role, text, _timestamp(self._clock)))
        while (
            len(session.messages) > config.MAX_HISTORY_MESSAGES
            or _history_bytes(session.messages) > config.MAX_HISTORY_BYTES
        ):
            session.messages.pop(0)
            session.history_truncated = True
        return message_id

    def _prune_rate_window_locked(self, now: float) -> None:
        while self._recent_sends and now - self._recent_sends[0] >= 60:
            self._recent_sends.popleft()

    def _status_payload(
        self,
        state: str,
        session_id: str | None,
        request_id: str | None,
        error: DeepSeekChatError | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": state not in {"failed"},
            "feature": "deepseek_chat",
            "experimental": True,
            "read_only": True,
            "state": state,
            "session_id": session_id,
            "request_id": request_id,
            "upstream_cancel_supported": False,
            "last_error": _error_payload(error),
            "limits": config.public_limits(),
        }
        return payload

    def _request_payload_locked(self, request: _Request) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": request.error is None,
            "request_id": request.request_id,
            "session_id": request.session_id,
            "state": request.state,
            "upstream_cancel_supported": False,
        }
        if request.state == "replied" and request.reply_text is not None:
            payload["reply"] = {
                "message_id": request.reply_message_id,
                "text": request.reply_text,
                "created_at": request.finished_at,
            }
        elif request.error is not None:
            payload["error"] = _error_payload(request.error)
        return payload

    @staticmethod
    def _validate_session_id(value: object) -> None:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise DeepSeekChatError("invalid_session")

    @staticmethod
    def _validate_request_id(value: object) -> None:
        if not isinstance(value, str) or not value or len(value) > 128:
            raise DeepSeekChatError("unknown_request")

    @staticmethod
    def _validate_text(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DeepSeekChatError("invalid_request")
        if len(value) > config.MAX_PROMPT_CHARS:
            raise DeepSeekChatError("request_too_large")
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise DeepSeekChatError("invalid_request") from None
        if len(encoded) > config.MAX_PROMPT_BYTES:
            raise DeepSeekChatError("request_too_large")
        if _has_forbidden_control(value):
            raise DeepSeekChatError("invalid_request")
        return value

    def _safe_log(self, event: Mapping[str, object]) -> None:
        try:
            self._logger(event)
        except Exception:
            pass


def _history_bytes(messages: list[_Message]) -> int:
    return sum(len(message.text.encode("utf-8")) for message in messages)


def _has_forbidden_control(value: str) -> bool:
    return any(ord(char) < 32 and char not in "\r\n\t" for char in value)


def _extract_reply(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("response is not an object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("response choices are unsupported")
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise ValueError("response choice is unsupported")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ValueError("response message is unsupported")
    if "tool_calls" in message or "function_call" in message:
        raise ValueError("tool response is unsupported")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("response content is unsupported")
    return content


def _http_error_code(status: int) -> str:
    if status in (401, 403):
        return "invalid_credential"
    if status == 429:
        return "upstream_rate_limited"
    if 400 <= status < 500:
        return "upstream_rejected"
    if 500 <= status < 600:
        return "upstream_error"
    return "upstream_rejected"


def _error_payload(error: DeepSeekChatError | None) -> dict[str, object] | None:
    if error is None:
        return None
    return {
        "code": error.code,
        "retryable": error.retryable,
        "message": error.public_message,
    }


__all__ = [
    "DeepSeekChatError",
    "DeepSeekChatService",
    "DeepSeekTransport",
    "TransportResponse",
    "UrllibTransport",
]
