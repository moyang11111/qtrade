from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from qtrade_adapters.deepseek_harness.portal_refresh_provider import (
    PortalPlanError,
    _akshare_network_guard,
    build_trusted_plan,
)


TARGET = "2026-08-28"
SYMBOLS = ("600001", "600002", "600003", "000001", "002001")


class FakeAdapter:
    def __init__(self, **_kwargs):
        self.records = {
            code: {
                "code": code,
                "name": f"Stock {code}",
                "exchange": "SH" if code.startswith("6") else "SZ",
                "listed": True,
                "suspended": False,
                "risk_warning": None,
                "history_rows": 200,
                "latest_trade_date": TARGET,
                "computable": True,
                "tradable": True,
                "eligible_reason": None,
            }
            for code in SYMBOLS
        }

    def scan(self):
        return list(self.records)

    def metadata(self, symbol):
        return self.records.get(symbol)


def _plan(**kwargs):
    return build_trusted_plan(
        base_dir=kwargs.pop("base_dir", "C:/does-not-read"),
        target_date=kwargs.pop("target_date", TARGET),
        calendar_dates=kwargs.pop("calendar_dates", [TARGET]),
        adapter_factory=kwargs.pop("adapter_factory", FakeAdapter),
        **kwargs,
    )


def test_build_plan_uses_only_server_owned_calendar_and_mainboard_metadata():
    plan, provider = _plan()

    assert plan.symbols == SYMBOLS
    assert plan.target_date == TARGET
    assert plan.calendar_verified is True
    assert len(plan.universe_token) == 64
    assert tuple(provider.metadata) == SYMBOLS
    assert all(set(item) <= {
        "code", "name", "exchange", "risk_warning", "suspended", "listed",
        "tradable", "history_rows", "latest_trade_date", "computable", "eligible_reason",
    } for item in provider.metadata.values())


@pytest.mark.parametrize("target", [datetime.date(2026, 8, 29), "2026-08-30"])
def test_weekend_is_skipped_before_provider_or_calendar_access(target):
    def no_calendar():
        raise AssertionError("weekend must not query the calendar")

    with pytest.raises(PortalPlanError, match="weekend"):
        build_trusted_plan(
            base_dir="C:/does-not-read",
            target_date=target,
            calendar_loader=no_calendar,
            adapter_factory=lambda **_: (_ for _ in ()).throw(AssertionError("no adapter")),
        )


def test_calendar_closed_is_distinct_from_calendar_unavailable():
    with pytest.raises(PortalPlanError, match="calendar_closed"):
        build_trusted_plan(
            base_dir="C:/does-not-read",
            target_date=TARGET,
            calendar_dates=["2026-08-27"],
            adapter_factory=lambda **_: (_ for _ in ()).throw(AssertionError("no adapter")),
        )

    with pytest.raises(PortalPlanError, match="calendar_unavailable"):
        build_trusted_plan(
            base_dir="C:/does-not-read",
            target_date=TARGET,
            calendar_dates=[],
            adapter_factory=lambda **_: (_ for _ in ()).throw(AssertionError("no adapter")),
        )


def test_plan_rejects_unbounded_or_duplicate_universe():
    class TooSmall(FakeAdapter):
        def scan(self):
            return list(SYMBOLS[:4])

    class Duplicate(FakeAdapter):
        def scan(self):
            return [*SYMBOLS, SYMBOLS[0]]

    for factory in (TooSmall, Duplicate):
        with pytest.raises(PortalPlanError, match="universe_"):
            _plan(adapter_factory=factory)


def test_akshare_provider_uses_fixed_qfq_daily_call(monkeypatch):
    calls = []

    class Frame:
        empty = False

        def to_dict(self, orient):
            assert orient == "records"
            return [{
                "date": TARGET,
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
            }]

    def fixed_daily(**kwargs):
        calls.append(kwargs)
        return Frame()

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(stock_zh_a_daily=fixed_daily),
    )
    _, provider = _plan()
    result = provider.fetch("600001", TARGET)

    assert result["rows"][0]["code"] == "600001"
    assert calls == [{
        "symbol": "sh600001",
        "start_date": "20260828",
        "end_date": "20260828",
        "adjust": "qfq",
    }]


def test_akshare_provider_history_is_target_anchored_and_bounded(monkeypatch):
    calls = []

    class Frame:
        empty = False

        def to_dict(self, orient):
            assert orient == "records"
            target = datetime.date.fromisoformat(TARGET)
            return [{
                "date": target - datetime.timedelta(days=319 - offset),
                "open": 10 + offset,
                "high": 11 + offset,
                "low": 9 + offset,
                "close": 10.5 + offset,
                "volume": 1000 + offset,
            } for offset in range(320)]

    def fixed_daily(**kwargs):
        calls.append(kwargs)
        return Frame()

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(stock_zh_a_daily=fixed_daily),
    )
    _, provider = _plan()
    result = provider.fetch_history("600001", TARGET)

    assert len(result["rows"]) == 320
    assert result["rows"][-1]["date"] == TARGET
    assert calls == [{
        "symbol": "sh600001",
        "start_date": "20250105",
        "end_date": "20260828",
        "adjust": "qfq",
    }]


def test_akshare_transport_forces_no_proxy_redirect_and_bounded_body(monkeypatch):
    import requests

    observed = {}

    class Response:
        status_code = 200
        closed = False

        def iter_content(self, chunk_size):
            observed["chunk_size"] = chunk_size
            return [b"safe response"]

        def close(self):
            self.closed = True

    def fake_send(session, request, **kwargs):
        observed.update({
            "method": request.method,
            "url": request.url,
            "kwargs": kwargs,
            "trust_env": session.trust_env,
        })
        return Response()

    monkeypatch.setenv("HTTPS_PROXY", "https://invalid.example/proxy")
    monkeypatch.setattr(requests.sessions.Session, "send", fake_send)
    with _akshare_network_guard():
        response = requests.get("https://data.example/fixed")

    assert response._content == b"safe response"
    assert observed["trust_env"] is False
    assert observed["kwargs"]["proxies"] == {}
    assert observed["kwargs"]["allow_redirects"] is False
    assert observed["kwargs"]["timeout"] == (10.0, 20.0)
    assert observed["kwargs"]["stream"] is True
    assert observed["chunk_size"] == 64 * 1024
    assert response.closed is True


def test_akshare_transport_rejects_redirect_and_overlarge_or_slow_body(monkeypatch):
    import requests

    class Response:
        def __init__(self, chunks, status=200):
            self.status_code = status
            self.chunks = chunks
            self.closed = False

        def iter_content(self, chunk_size):
            if self.chunks == "slow":
                raise RuntimeError("slow read")
            return iter(self.chunks)

        def close(self):
            self.closed = True

    response_list = [
        Response([b"redirect"], status=302),
        Response([b"x" * (8 * 1024 * 1024 + 1)]),
        Response("slow"),
    ]
    responses = iter(response_list)

    def fake_send(_session, _request, **_kwargs):
        return next(responses)

    monkeypatch.setattr(requests.sessions.Session, "send", fake_send)
    with _akshare_network_guard():
        session = requests.Session()
        for expected in ("redirect", "too large", "slow read"):
            with pytest.raises(RuntimeError, match=expected):
                session.send(requests.Request("GET", "https://data.example/fixed").prepare())
    assert all(response.closed for response in response_list)
