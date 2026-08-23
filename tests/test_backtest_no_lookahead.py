# -*- coding: utf-8 -*-
"""隔离未来函数的回归测试。

验证回测模拟器：
1. 信号在 T 日收盘确认 → 只能在 T+1 日开盘成交（不得用当根收盘价自买自卖）。
2. T+1 开盘碰到一字涨停/跌停时，对应买卖应被跳过。
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402


def _make_df(n=30):
    idx = pd.date_range("2026-01-05", periods=n, freq="B")
    opens = [10.0 + i * 0.01 for i in range(n)]
    return pd.DataFrame({
        "open": opens,
        "high": [o + 0.2 for o in opens],
        "low": [o - 0.2 for o in opens],
        "close": opens,          # 收盘 = 开盘，便于断言
        "volume": [1_000_000] * n,
    }, index=idx)


def test_buy_executes_next_open():
    df = _make_df()
    signals = pd.Series(0, index=df.index)
    signals.iloc[10] = 1                       # T=10 收盘出买入信号

    res = server.DataService._simulate(df, signals, 100000, 0.0003, 0.05, 0.15, "600000")

    buys = [t for t in res["trades"] if t["type"] == "buy"]
    assert buys, "应产生一笔买入"

    first_buy = buys[0]
    # 关键断言：成交日 = T+1 = index[11]
    assert first_buy["date"] == str(df.index[11])[:10], (
        f"成交日 {first_buy['date']} 应为信号次日 {str(df.index[11])[:10]}"
    )
    # 关键断言：成交价 = T+1 开盘价，而不是 T 日收盘价
    assert abs(first_buy["price"] - round(float(df["open"].iloc[11]), 2)) < 1e-6, (
        f"成交价 {first_buy['price']} 应为次日开盘 {df['open'].iloc[11]}"
    )
    # 信号当天（T=10）不应成交
    dates = {t["date"] for t in res["trades"]}
    assert str(df.index[10])[:10] not in dates, "信号当天不应成交（未来函数泄漏）"


def test_limit_up_blocks_buy():
    df = _make_df()
    # 制造 T+1=index[11] 开盘即对标一字涨停
    prev_close = float(df["close"].iloc[10])
    limit_up = round(prev_close * 1.10, 2)
    df.loc[df.index[11], "open"] = limit_up
    df.loc[df.index[11], "close"] = limit_up

    signals = pd.Series(0, index=df.index)
    signals.iloc[10] = 1
    signals.iloc[11] = 1            # 让 T+2 也有可执行的买入信号

    res = server.DataService._simulate(df, signals, 100000, 0.0003, 0.05, 0.15, "600000")

    # T+1 一字涨停买不进：不应在 index[11] 成交
    trades_at = [t for t in res["trades"] if t["date"] == str(df.index[11])[:10]]
    assert not trades_at, "一字涨停日不应买到股票"

    # 后面 T+2（index[12]，开盘非涨停）应能正常买入
    later_buy = [t for t in res["trades"] if t["type"] == "buy" and t["date"] == str(df.index[12])[:10]]
    assert later_buy, "涨停日之后应能买入"


def test_fee_model_and_audit_and_grade():
    df = _make_df()
    signals = pd.Series(0, index=df.index)
    signals.iloc[5] = 1

    res = server.DataService._simulate(df, signals, 100000, 0.0003, 0.05, 0.15, "600000")

    # 结论分级
    assert "grade" in res and "score" in res and res["grade"] in ("A", "B", "C", "D")

    # 数据审计（PIT/覆盖率/时效）
    audit = server.DataService._audit_data(df, "600000")
    assert audit["available"] is True
    assert audit["rows"] == len(df)
    assert audit["columns_ok"] is True
    assert audit["bars_per_year"] > 0
