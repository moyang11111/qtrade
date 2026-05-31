"""回测布林带+RSI策略"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy.rule.bb_rsi import BBRsiSignal
from qtrade.data.fetcher import DataFetcher
import pandas as pd


def backtest_bb_rsi(symbol: str, start_date: str, end_date: str):
    """回测布林带+RSI策略"""

    print(f"回测布林带+RSI策略")
    print(f"股票: {symbol}")
    print(f"日期范围: {start_date} ~ {end_date}")
    print("=" * 60)

    # 获取数据
    config = {
        "data": {
            "source": "pytdx",
            "fallback": ["akshare"],
            "cache": {
                "type": "csv",
                "dir": "data/cache",
                "enabled": True
            }
        }
    }
    fetcher = DataFetcher(config)
    df = fetcher.fetch(symbol, start_date, end_date)

    if df.empty:
        print(f"错误: 无法获取 {symbol} 的数据")
        return None

    print(f"数据量: {len(df)} 条K线")
    print(f"日期范围: {df.index[0].date()} ~ {df.index[-1].date()}")
    print()

    # 创建策略实例
    strategy_config = {
        "bb_period": 20,
        "bb_std": 2.0,
        "rsi_period": 14,
        "rsi_buy": 20,
        "rsi_sell": 65
    }
    strategy = BBRsiSignal(strategy_config)

    # 生成信号
    df_signals = strategy.generate_signals(df)

    # 统计信号
    buy_signals = df_signals[df_signals['signal_action'] == 1]
    sell_signals = df_signals[df_signals['signal_action'] == -1]

    print(f"买入信号数量: {len(buy_signals)}")
    print(f"卖出信号数量: {len(sell_signals)}")
    print()

    if len(buy_signals) > 0:
        print("前5个买入信号:")
        cols = [c for c in ['close', 'signal_strength', 'bb_lower', 'rsi'] if c in buy_signals.columns]
        print(buy_signals[cols].head())
        print()

    # 回测
    backtest_config = {
        "backtest": {
            "initial_capital": 1000000,
            "commission": 0.0003,
            "slippage": 0.001,
            "stop_loss_pct": 0.08,
            "trail_stop_pct": 0.10,
            "lot_size": 100,
        },
        "position_sizing": {
            "method": "fixed",
            "fixed_pct": 0.95,
        }
    }
    engine = BacktestEngine(backtest_config)

    result = engine.run(df_signals)

    # 打印结果
    m = result.metrics
    print("=" * 60)
    print("回测结果:")
    print("=" * 60)
    print(f"初始资金: {m.get('initial_capital', 1000000):,.0f}")
    print(f"最终资金: {m.get('final_capital', 0):,.0f}")
    print(f"总收益率: {m.get('total_return', 0):.2f}%")
    print(f"年化收益率: {m.get('annual_return', 0):.2f}%")
    print(f"最大回撤: {m.get('max_drawdown', 0):.2f}%")
    print(f"夏普比率: {m.get('sharpe_ratio', 0):.2f}")
    print(f"胜率: {m.get('win_rate', 0):.2f}%")
    print(f"盈亏比: {m.get('profit_loss_ratio', 0):.2f}")
    print(f"交易次数: {m.get('total_trades', 0)}")
    print()

    # 保存详细结果
    import json
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)

    result_file = results_dir / f"bb_rsi_{symbol}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "symbol": symbol,
            "metrics": {k: v for k, v in m.items() if not isinstance(v, (dict, list))},
            "trade_count": len(result.trade_log),
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"详细结果已保存: {result_file}")

    return result


if __name__ == "__main__":
    # 回测宁德时代
    backtest_bb_rsi(
        symbol="300750",
        start_date="2022-01-01",
        end_date="2024-12-31"
    )

    print("\n" + "=" * 60)
    print("\n")

    # 回测贵州茅台
    backtest_bb_rsi(
        symbol="600519",
        start_date="2022-01-01",
        end_date="2024-12-31"
    )
