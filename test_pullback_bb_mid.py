"""测试 pullback_bb_mid 策略：强势回调布林中轨买入。
用法: python test_pullback_bb_mid.py

SignalFollower 推荐配置（在 config 中设置）:
  take_profit_pct=0.10   # 10% 止盈
  stop_loss_pct=0.05     # 5% 止损
  trail_stop_pct=0.0     # 不设移动止损
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd
from qtrade.strategy.registry import list_strategies, get_signal_generator
from qtrade.backtest import BacktestEngine

# 1. 确认注册
assert "pullback_bb_mid" in list_strategies(), "策略未注册！"
print(f"已注册策略: {list_strategies()}")

# 2. 测试多股票
cache = os.path.join(os.path.dirname(__file__), "data", "cache")
stocks = {"600519": "茅台", "000858": "五粮液", "300750": "宁德时代",
          "002371": "北方华创", "300274": "阳光电源"}

config = {
    "backtest": {
        "initial_capital": 1000000, "commission": 0.0003,
        "slippage": 0.001, "lot_size": 100,
        "sizing_method": "fixed", "base_position_pct": 0.95,
        "stop_loss_pct": 0.05, "take_profit_pct": 0.10,
        "trail_stop_pct": 0.0,
    }
}

for code, name in stocks.items():
    f = os.path.join(cache, f"{code}.csv")
    if not os.path.exists(f):
        continue
    df = pd.read_csv(f, index_col=0, parse_dates=True)

    strategy = get_signal_generator("pullback_bb_mid")({})
    signals = strategy.generate_signals(df)

    buys = int((signals["signal_action"] == 1).sum())
    if buys == 0:
        print(f"\n{code} {name}: 无信号（不在上升趋势中）")
        continue

    engine = BacktestEngine(config)
    bt = engine.run(signals)
    m = bt.metrics

    print(f"\n{code} {name} ({buys}个信号, {m.get('total_trades',0)}笔交易):")
    print(f"  收益={m.get('total_return',0):+.1f}%  夏普={m.get('sharpe_ratio','N/A')}  "
          f"胜率={m.get('win_rate',0):.0f}%  回撤={m.get('max_drawdown',0):.1f}%")
    for t in bt.trade_log:
        print(f"  {t.get('entry_date','')}→{t.get('exit_date','')}  "
              f"入场={t.get('entry_price',0):.2f} "
              f"盈亏={t.get('pnl_pct',0):+.2f}%")
