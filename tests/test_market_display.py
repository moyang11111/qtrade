from __future__ import annotations

import json
from types import SimpleNamespace
import threading

import pandas as pd
import pytest

import server
from paper_trading.market_data import MarketDataProvider


def _frame(last_day: str, close: float) -> pd.DataFrame:
    dates = pd.date_range(end=last_day, periods=80, freq="D")
    return pd.DataFrame(
        {"open": close - 1, "high": close + 1, "low": close - 2,
         "close": close, "volume": 1000},
        index=dates,
    )


def _service(live_frame: pd.DataFrame | None, quote: dict | None = None):
    service = server.DataService.__new__(server.DataService)
    service._portal_reload_lock = threading.RLock()
    service._df_cache = {}
    service._ind_cache = {}
    service.portal_mirror_active = True
    service.live_src = None
    service.market_src = SimpleNamespace(
        fetch_kline=lambda symbol, count: live_frame if live_frame is not None else (_ for _ in ()).throw(RuntimeError("offline")),
        fetch_quote=lambda symbol: quote if quote is not None else (_ for _ in ()).throw(RuntimeError("offline")),
    )
    service._resolve_df = lambda symbol, count=320: _frame("2026-09-11", 10)
    return service


def test_market_display_uses_live_qfq_without_changing_research_history():
    quote = {"name": "测试", "price": 20.5, "open": 20, "high": 21,
             "low": 19, "change": 0.5, "change_pct": 2.5, "volume": 10,
             "turnover": 1, "pe": 10, "time": "20260921150000"}
    service = _service(_frame("2026-09-21", 20), quote)

    assert service.get_kline("000001", 1)[-1]["close"] == 10
    assert service.get_info("000001")["latest"] == 10
    assert service.get_market_kline("000001", 1)[-1]["close"] == 20
    info = service.get_market_info("000001")
    assert info["latest"] == 20.5
    assert info["quote_source"] == "tencent"
    assert info["kline_source"] == "tencent_qfq"
    assert info["kline_date"] == "2026-09-21"
    assert service.get_market_indicators("000001")["mas"]["ma5"][-1]["value"] == 20
    assert service.get_indicators("000001")["mas"]["ma5"][-1]["value"] == 10
    assert MarketDataProvider(service).get_quote("000001").price == 10


def test_market_display_falls_back_to_published_snapshot_and_reports_date():
    service = _service(None)

    assert service.get_market_kline("000001", 1)[-1]["close"] == 10
    info = service.get_market_info("000001")
    assert info["quote_source"] == "published_snapshot"
    assert info["kline_source"] == "published_snapshot"
    assert info["kline_date"] == "2026-09-11"


def test_market_display_rejects_older_live_bar():
    service = _service(_frame("2026-09-10", 20))

    assert service.get_market_kline("000001", 1)[-1]["close"] == 10
    assert service.get_market_info("000001")["kline_source"] == "published_snapshot"


def test_market_display_labels_live_kline_close_when_quote_is_unavailable():
    service = _service(_frame("2026-09-21", 20))

    info = service.get_market_info("000001")
    assert info["latest"] == 20
    assert info["quote_source"] == "tencent_qfq_close"


def test_tencent_source_never_falls_back_to_raw_day(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def read(self):
            return json.dumps({"data": {"sz000001": {"day": [["2026-09-21", "10", "10", "11", "9", "10"]]}}}).encode()

    monkeypatch.setattr(server.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    source = server.TencentLiveSource()

    with pytest.raises(RuntimeError, match="前复权"):
        source.fetch_kline("000001")
