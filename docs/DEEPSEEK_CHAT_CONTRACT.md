# QTrade optional read-only DeepSeek chat

This document defines the QTrade-owned chat boundary.  It is separate from
DeepSeek HARNESS and from the existing `/quantapi/chat2` proxy.

## Product and safety boundary

The feature is experimental and disabled by default.  It is enabled only when
the application is started with `QTRADE_DEEPSEEK_CHAT=1`.  The user must also
provide `QTRADE_DEEPSEEK_API_KEY` in the user/application environment.  The
key is read only by the QTrade Python process while checking local readiness or
dispatching a user message.  It is never returned by an API, placed in the
context, or written to logs.

The interface is text-only and read-only.  It cannot execute a command, start a
process, access a tool, change configuration, write application data, update
market data, place an order, or call the existing HARNESS chat proxy.  The
user's text and the small QTrade status summary are sent to the fixed DeepSeek
official chat-completions endpoint and may incur provider charges.  No network
request is made by status, poll, history, or cancel.

Only the fixed model and HTTPS endpoint in
`qtrade_adapters/deepseek_chat/config.py` are used.  The browser cannot provide
an endpoint, model, key, system prompt, context, tool, function, or metadata.
HTTP redirects are disabled and TLS verification remains enabled.

After changing the user-level environment, restart the QTrade application.
Never place a real key in source, tests, screenshots, issue reports, or
documentation.  The local tests use fake transports only.

On Windows, set `QTRADE_DEEPSEEK_CHAT=1` and
`QTRADE_DEEPSEEK_API_KEY` as user-level environment variables in the user
Environment Variables dialog, then restart QTrade.  To disable the feature,
remove the flag or set it to any value other than `1`, then restart.  Do not
paste a real key into a terminal transcript or this document.

PR22 supplies this backend contract only.  It does not change the native
console UI; a later UI change must keep the chat collapsed/disabled by default
and render replies as text, never as HTML.

## API

All routes are same-origin QTrade routes under `/api/deepseek-chat` and are
handled before the external base/proxy routes.

### `GET /api/deepseek-chat/status`

This is a local readiness check.  It does not contact DeepSeek.  The response
contains only `ok`, `feature`, `experimental`, `read_only`, `state`, opaque
`session_id`/`request_id` values, `upstream_cancel_supported: false`, a stable
`last_error` object or null, and safe numeric `limits`.  It never contains a
key, environment value, path, provider response, or transcript.

`ready` means that the feature flag, dedicated key, local session and local
capacity checks pass.  It does not mean that the provider has been contacted.

### `POST /api/deepseek-chat/send`

The exact JSON object is:

```json
{"session_id":"<server-issued opaque id>","text":"<plain text>"}
```

Unknown fields are rejected.  Text is limited to 2,000 Unicode characters and
8 KiB UTF-8.  The response state is `accepted`, which means only that the
bounded local job was queued.  The client must poll for `replied`.

The provider request contains a fixed system instruction, a server-generated
allowlisted QTrade context, the user's text, a fixed model, `stream: false`,
and bounded generation settings.  It deliberately contains none of
`tools`, `tool_choice`, `functions`, `function_call`, or
`parallel_tool_calls`.

### `GET /api/deepseek-chat/poll?request_id=...`

Returns the current request state.  A successful terminal response contains a
plain-text `reply`; an error terminal response contains only a stable
`{code,retryable,message}` object.  Provider body, headers, stack traces and
tool/function payloads are never returned.

### `GET /api/deepseek-chat/history?session_id=...&limit=...`

Returns only the bounded in-memory user/assistant text history.  The initial
implementation keeps at most 20 messages (10 turns) and 32 KiB per session;
it does not persist history.

### `POST /api/deepseek-chat/cancel`

The exact JSON object is:

```json
{"session_id":"<server-issued opaque id>","request_id":"<server-issued opaque id>"}
```

Queued work is removed locally where possible.  In-flight work is marked
cancelled locally and any late response is discarded.  The API always reports
`upstream_cancel_supported: false`; it never claims that the provider stopped.
Completed requests are idempotent.

## State machine

The public states are:

`disabled`, `idle`, `unconfigured`, `ready`, `accepted`, `waiting`,
`replied`, `failed`, `timed_out`, and `service_unreachable`.

Only `ready` may transition to `accepted`.  A request then moves to `waiting`
and may finish as `replied`, `failed`, `timed_out`, or
`service_unreachable`.  `accepted` is never rendered as `replied`.

## Context allowlist

The server constructs a versioned typed object containing only:

- health enum;
- business date and freshness enum;
- mainboard total/computable/tradable counts, date and source enum;
- opportunity count and fixed approved category counts;
- factor scheme/active counts, date and freshness enum.

It excludes symbols, positions, accounts, orders, decisions, paths, working
directories, environment variables, keys, commands, raw logs, exceptions,
arbitrary files, source tokens and client-supplied JSON.  The context builder
selects and validates every nested field before deterministic serialization.

## Limits and errors

The local service allows one active request, enforces a five-second per-session
send interval and a bounded process-wide rate window.  It keeps only a bounded
number of process-local session/request records.  It uses a five-second
connection timeout, a 35-second total deadline, a 16 KiB provider response
bound and bounded plain-text reply/history sizes.

Provider and transport failures map to stable codes such as
`invalid_credential`, `upstream_rate_limited`, `upstream_error`,
`upstream_unreachable`, `upstream_timeout`, `invalid_response`, and
`response_too_large`.  Logs contain only opaque request ID, state/error code,
elapsed time, byte counts and HTTP status class.

## Verification

Tests inject a fake transport, clock and executor.  They must never call a
real provider, use a real key, start HARNESS, or perform a trade.  Static
contracts reject subprocess/process APIs, arbitrary endpoints, tool/function
fields, unsafe context fields, and unredacted diagnostics.  Electron packaging
includes the new Python package through the existing `qtrade_adapters` resource
rule while excluding credentials, third-party runtime files and Python cache
files.
