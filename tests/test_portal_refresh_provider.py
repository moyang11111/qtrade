from __future__ import annotations

import datetime
import sqlite3
from types import SimpleNamespace

import pytest

from qtrade_adapters.deepseek_harness.portal_refresh_provider import (
    AksharePortalProvider,
    PortalHistoryError,
    PortalPlanError,
    _akshare_network_guard,
    _load_trade_dates,
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


def test_trusted_plan_excludes_nontradable_symbols_and_counts_reasons():
    class MixedAdapter(FakeAdapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.records["600002"]["risk_warning"] = "ST"
            self.records["600003"]["suspended"] = True
            self.records["000001"]["listed"] = False
            self.records["000002"] = {
                **self.records["600001"], "code": "000002", "exchange": "SZ",
            }
            self.records["000003"] = {
                **self.records["600001"], "code": "000003", "exchange": "SZ",
            }
            self.records["000004"] = {
                **self.records["600001"], "code": "000004", "exchange": "SZ",
            }

    plan, provider = _plan(adapter_factory=MixedAdapter)
    assert plan.symbols == ("600001", "002001", "000002", "000003", "000004")
    assert dict(plan.excluded_by_reason) == {
        "not_tradable": 1, "risk_warning": 1, "suspended": 1,
    }
    assert tuple(provider.metadata) == plan.symbols


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


def test_provider_uses_eastmoney_qfq_fallback_and_converts_lots(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "get", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("offline")))

    class Frame:
        empty = False

        def to_dict(self, orient):
            assert orient == "records"
            return [{
                "日期": TARGET,
                "开盘": 10,
                "最高": 11,
                "最低": 9,
                "收盘": 10.5,
                "成交量": 10,
            }]

    def fixed_daily(**kwargs):
        calls.append(kwargs)
        return Frame()

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=fixed_daily,
                        stock_zh_a_hist_tx=lambda **_: (_ for _ in ()).throw(ConnectionError("offline"))),
    )
    _, provider = _plan()
    result = provider.fetch("600001", TARGET)

    assert result["rows"][0]["code"] == "600001"
    assert result["rows"][0]["volume"] == 1000
    assert calls == [{
        "symbol": "600001",
        "period": "daily",
        "start_date": "20260828",
        "end_date": "20260828",
        "adjust": "qfq",
        "timeout": 20,
    }]


def test_provider_history_is_target_anchored_and_bounded(monkeypatch):
    import requests
    calls = []
    monkeypatch.setattr(requests, "get", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("offline")))

    class Frame:
        empty = False

        def to_dict(self, orient):
            assert orient == "records"
            target = datetime.date.fromisoformat(TARGET)
            return [{
                "日期": target - datetime.timedelta(days=319 - offset),
                "开盘": 10 + offset,
                "最高": 11 + offset,
                "最低": 9 + offset,
                "收盘": 10.5 + offset,
                "成交量": 10 + offset,
            } for offset in range(320)]

    def fixed_daily(**kwargs):
        calls.append(kwargs)
        return Frame()

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=fixed_daily,
                        stock_zh_a_hist_tx=lambda **_: (_ for _ in ()).throw(ConnectionError("offline"))),
    )
    _, provider = _plan()
    result = provider.fetch_history("600001", TARGET)

    assert len(result["rows"]) == 320
    assert result["rows"][-1]["date"] == TARGET
    assert calls == [{
        "symbol": "600001",
        "period": "daily",
        "start_date": "20250105",
        "end_date": "20260828",
        "adjust": "qfq",
        "timeout": 20,
    }]


def test_history_uses_cache_and_fetches_only_recent_gap(tmp_path, monkeypatch):
    db = tmp_path / "bars.db"
    target = datetime.date(2026, 8, 28)
    with sqlite3.connect(db) as connection:
        connection.execute("CREATE TABLE daily_bar (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, adjust TEXT)")
        connection.executemany(
            "INSERT INTO daily_bar VALUES (?,?,?,?,?,?,?,?)",
            [("600001.SH", (target - datetime.timedelta(days=320 - index)).isoformat(), 10, 11, 9, 10.5, 1000, "qfq") for index in range(320)],
        )
        connection.execute(
            "INSERT INTO daily_bar VALUES (?,?,?,?,?,?,?,?)",
            ("600001.SH", target.isoformat(), 10, 11, 9, 10.5, None, "qfq"),
        )
    provider = AksharePortalProvider({"600001": {"suspended": False}}, history_db=db)
    calls = []

    def fresh(code, trade_date, start_date, minimum, *, all_rows=False):
        calls.append((code, trade_date, start_date, minimum, all_rows))
        return [
            {"code": code, "date": (target - datetime.timedelta(days=offset)).isoformat(),
             "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000,
             "adjust": "qfq"}
            for offset in (1, 0)
        ]

    monkeypatch.setattr(provider, "_fetch_rows", fresh)
    result = provider.fetch_history("600001", target.isoformat())
    assert len(result["rows"]) == 320
    assert result["rows"][-1]["date"] == target.isoformat()
    assert calls == [("600001", target.isoformat(), "20260820", 1, True)]

    def inconsistent(*args, **kwargs):
        if args[3] == 320:
            calls.append(args + (kwargs.get("all_rows", False),))
            return [
                {"code": "600001", "date": (target - datetime.timedelta(days=319 - index)).isoformat(),
                 "open": 9, "high": 12, "low": 8, "close": 11, "volume": 1000,
                 "adjust": "qfq"}
                for index in range(320)
            ]
        rows = fresh(*args, **kwargs)
        rows[0]["close"] = 12
        return rows

    monkeypatch.setattr(provider, "_fetch_rows", inconsistent)
    rebased = provider.fetch_history("600001", target.isoformat())
    assert len(rebased["rows"]) == 320
    assert all(row["close"] == 11 for row in rebased["rows"])
    assert calls[-1] == ("600001", target.isoformat(), "20250105", 320, False)


@pytest.mark.parametrize(
    ("code", "symbol", "expected_volume"),
    [("600001", "sh600001", 123400), ("000001", "sz000001", 123400)],
)
def test_tencent_qfq_primary_normalizes_share_units_and_does_not_call_sina(monkeypatch, code, symbol, expected_volume):
    import requests
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {symbol: {"qfqday": [[TARGET, "10", "10.5", "11", "9", "1234"]]}}}

    def eastmoney(**kwargs):
        pytest.fail("Eastmoney should not be called after Tencent succeeds")

    def tencent(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(requests, "get", tencent)
    monkeypatch.setitem(__import__("sys").modules, "akshare", SimpleNamespace(
        stock_zh_a_hist=eastmoney,
        stock_zh_a_daily=lambda **_: pytest.fail("Sina must not be called"),
    ))
    _, provider = _plan()
    result = provider.fetch(code, TARGET)
    assert result["rows"][0]["volume"] == expected_volume
    assert calls == [("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                      {"params": {"param": f"{symbol},day,,,20,qfq"}})]


def test_tencent_incremental_request_is_bounded_by_recent_gap(monkeypatch):
    import requests

    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"sh600001": {"qfqday": [
                [TARGET, "10", "10.5", "11", "9", "1234"]
            ]}}}

    monkeypatch.setattr(requests, "get", lambda _url, **kwargs: (calls.append(kwargs), Response())[1])
    provider = AksharePortalProvider({"600001": {"suspended": False}})
    provider._source_rows("tencent", "600001", TARGET, "20260820")
    provider._source_rows("tencent", "600001", TARGET, "20250105")
    assert [call["params"]["param"] for call in calls] == [
        "sh600001,day,,,26,qfq", "sh600001,day,,,400,qfq",
    ]


def test_tencent_raw_day_is_not_accepted_as_qfq(monkeypatch):
    import requests

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"sh600001": {
                "day": [[TARGET, "10", "10.5", "11", "9", "1000"]],
            }}}

    monkeypatch.setattr(requests, "get", lambda *_, **__: Response())
    _, provider = _plan()
    with pytest.raises(RuntimeError, match="qfq"):
        provider._source_rows("tencent", "600001", TARGET, "20260828")


def test_tdx_cannot_turn_raw_bar_into_qfq_snapshot(monkeypatch):
    import requests
    class Empty:
        empty = True

    monkeypatch.setattr(requests, "get", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("offline")))
    monkeypatch.setitem(__import__("sys").modules, "akshare", SimpleNamespace(
        stock_zh_a_hist=lambda **_: Empty(), stock_zh_a_hist_tx=lambda **_: Empty(),
    ))
    _, provider = _plan()
    monkeypatch.setattr(provider, "_tdx_target_present", lambda *_: True)
    with pytest.raises(RuntimeError, match="provider_failed") as error:
        provider.fetch_history("600001", TARGET)
    assert error.value.transient is True


def test_calendar_uses_tencent_then_eastmoney_without_sina(monkeypatch):
    calls = []

    class Frame:
        def __getitem__(self, key):
            assert key == "date"
            return SimpleNamespace(tolist=lambda: ["2026-08-27", TARGET])

    def tencent(**kwargs):
        calls.append("tencent")
        raise ConnectionError("offline")

    def eastmoney(**kwargs):
        calls.append("eastmoney")
        return Frame()

    monkeypatch.setitem(__import__("sys").modules, "akshare", SimpleNamespace(
        stock_zh_index_daily_em=eastmoney, stock_zh_index_daily_tx=tencent,
        tool_trade_date_hist_sina=lambda: pytest.fail("Sina must not be called"),
    ))
    assert _load_trade_dates() == ["2026-08-27", TARGET]
    assert calls == ["tencent", "eastmoney"]


@pytest.mark.parametrize(
    ("rows", "suspended", "reason"),
    [
        ([TARGET], False, "insufficient_history"),
        (["2026-08-27"] * 320, False, "target_date_missing"),
        ([TARGET], True, "suspended"),
    ],
)
def test_history_validation_exposes_only_stable_quality_classification(monkeypatch, rows, suspended, reason):
    import requests
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": {"sh600001": {"qfqday": [
                [day, "10", "10.5", "11", "9", "1000"] for day in rows
            ]}}}

    monkeypatch.setattr(requests, "get", lambda *_, **__: Response())
    class Frame:
        empty = not rows

        def to_dict(self, orient):
            assert orient == "records"
            return [{
                "日期": value,
                "开盘": 10,
                "最高": 11,
                "最低": 9,
                "收盘": 10.5,
                "成交量": 10,
            } for value in rows]

    class TencentFrame(Frame):
        def to_dict(self, orient):
            return [{
                "date": value, "open": 10, "high": 11, "low": 9,
                "close": 10.5, "volume": 1000,
            } for value in rows]

    monkeypatch.setitem(
        __import__("sys").modules,
        "akshare",
        SimpleNamespace(stock_zh_a_hist=lambda **_: Frame(),
                        stock_zh_a_hist_tx=lambda **_: TencentFrame()),
    )
    _, provider = _plan()
    monkeypatch.setattr(provider, "_tdx_target_present", lambda *_: None)
    provider.metadata["600001"]["suspended"] = suspended
    with pytest.raises(PortalHistoryError, match=reason):
        provider.fetch_history("600001", TARGET)


def test_akshare_transport_honors_system_proxy_and_bounds_body(monkeypatch):
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
    assert observed["trust_env"] is True
    assert observed["kwargs"]["proxies"]["https"] == "https://invalid.example/proxy"
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
