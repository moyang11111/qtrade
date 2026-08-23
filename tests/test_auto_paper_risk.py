# -*- coding: utf-8 -*-
"""自动模拟盘风控门禁 + 远期验证池 回归测试。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


class _FakeSvc:
    live = True

    def scan(self):
        return []

    def _resolve_df(self, symbol, count=320):
        idx = pd.date_range("2025-01-02", periods=140, freq="B")
        return pd.DataFrame({"open": [10.0] * 140, "high": [10.2] * 140,
                             "low": [9.8] * 140, "close": [10.0] * 140,
                             "volume": [1_000_000] * 140}, index=idx)

    def _load_csv(self, symbol):
        return self._resolve_df(symbol)

    def get_info(self, symbol):
        return {"name": "t", "latest": 10.0, "open": 10.0, "high": 10.1,
                "low": 9.9, "change": 0.0, "change_pct": 0.0,
                "volume": 1_000_000, "time": "2026-08-19 10:00:00", "prev_close": 10.0}


@pytest.fixture
def trader(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # 隔离 auto_paper_meta.json / .db，不影响其它测试
    server.SERVICE = _FakeSvc()
    return server.EngineAutoPaperTrader()


def test_risk_gate(trader):
    ok, reason = trader._risk_gate({"net_asset": 100000})
    assert ok is True
    ok2, reason2 = trader._risk_gate({"net_asset": 80000})
    assert ok2 is False
    assert "回撤" in reason2


def test_forward_pool_record(trader):
    trader._record_forward("000001", {"buy_date": "2026-08-01", "buy_price": 10.0},
                           10.0, 11.2, "2026-08-19")
    pool = trader.state["forward_pool"]
    assert len(pool) == 1
    assert pool[0]["pnl_pct"] == 12.0
    assert pool[0]["hold_days"] == 18
    assert 5 in pool[0]["horizons"] and 20 not in pool[0]["horizons"]


def test_status_includes_risk_and_forward(trader):
    st = trader.status(server.SERVICE)
    assert "risk" in st and "forward_pool" in st
    assert st["risk"]["max_new_per_cycle"] > 0
    assert st["risk"]["l0_gate"] is True
    assert st["risk"]["l0_breadth_min"] == trader.L0_BREADTH_MIN
    assert st["risk"]["max_family_positions"] == trader.MAX_FAMILY_POSITIONS


def test_factor_family_classification():
    assert server.EngineAutoPaperTrader._factor_family("Sequoia:海龟突破20日高+阳线放量") == "breakout"
    assert server.EngineAutoPaperTrader._factor_family("MA5上穿MA20+1.5倍量") == "trend"
    assert server.EngineAutoPaperTrader._factor_family("反弹：RSI超卖") == "reversal"
    assert server.EngineAutoPaperTrader._factor_family("涨停洗盘回踩确认") == "limitup"
    assert server.EngineAutoPaperTrader._factor_family("随便什么") == "other"
