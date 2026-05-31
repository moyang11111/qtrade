"""五日趋势策略 V3 参数微调扫描"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
from itertools import product
from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy.rule.trend_5d import Trend5DSignal
from qtrade.data.fetcher import DataFetcher

SYMBOLS = [
    "300750", "600519", "000001", "000858",
    "300033", "002049", "300394", "002709",
]

# 参数扫描范围
PARAM_GRID = {
    "vol_multiplier": [1.2, 1.3, 1.5, 1.8, 2.0],
    "trailing_stop_pct": [0.03, 0.05, 0.07],
    "time_stop_days": [7, 10, 15],
}

BT_CONFIG = {
    "backtest": {
        "initial_capital": 1000000,
        "commission": 0.0003,
        "slippage": 0.001,
        "stop_loss_pct": 0.0,
        "trail_stop_pct": 0.0,
        "take_profit_pct": 0.0,
        "lot_size": 100,
    },
    "position_sizing": {
        "method": "fixed",
        "fixed_pct": 0.95,
    }
}


def main():
    fetcher = DataFetcher({"data": {"source": "pytdx"}})

    # 预加载所有数据
    print("加载数据...")
    stock_data = {}
    for symbol in SYMBOLS:
        try:
            df = fetcher.fetch(symbol, "2022-01-01", "2024-12-31")
            if df is not None and len(df) > 0:
                stock_data[symbol] = df
        except Exception:
            pass
    print(f"已加载 {len(stock_data)} 只股票")

    # 生成参数组合
    combos = list(product(
        PARAM_GRID["vol_multiplier"],
        PARAM_GRID["trailing_stop_pct"],
        PARAM_GRID["time_stop_days"],
    ))
    print(f"参数组合: {len(combos)} 种")
    print(f"回测总数: {len(combos) * len(stock_data)}")
    print("=" * 100)

    all_results = []

    for i, (vol_mult, trail_pct, time_days) in enumerate(combos):
        params = {
            "ma_period": 5,
            "ma10_period": 10,
            "ma20_period": 20,
            "slope_period": 3,
            "add_thresh": 0.01,
            "vol_ma_period": 20,
            "vol_multiplier": vol_mult,
            "atr_period": 14,
            "atr_median_period": 60,
            "trailing_stop_pct": trail_pct,
            "time_stop_days": time_days,
        }

        returns = []
        trades_list = []
        win_rates = []
        drawdowns = []
        sharpes = []

        for symbol, df in stock_data.items():
            try:
                strategy = Trend5DSignal(params)
                df_signals = strategy.generate_signals(df)
                engine = BacktestEngine(BT_CONFIG)
                result = engine.run(df_signals)
                m = result.metrics
                returns.append(m.get("total_return", 0))
                trades_list.append(m.get("total_trades", 0))
                win_rates.append(m.get("win_rate", 0))
                drawdowns.append(m.get("max_drawdown", 0))
                sharpes.append(m.get("sharpe_ratio", 0))
            except Exception:
                pass

        if returns:
            all_results.append({
                "vol_mult": vol_mult,
                "trail_pct": trail_pct,
                "time_days": time_days,
                "avg_return": sum(returns) / len(returns),
                "avg_trades": sum(trades_list) / len(trades_list),
                "avg_win_rate": sum(win_rates) / len(win_rates),
                "avg_drawdown": sum(drawdowns) / len(drawdowns),
                "avg_sharpe": sum(sharpes) / len(sharpes),
                "n_positive": sum(1 for r in returns if r > 0),
                "n_stocks": len(returns),
            })

        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(combos)}")

    # 汇总分析
    df_results = pd.DataFrame(all_results)
    df_results = df_results.sort_values("avg_return", ascending=False)

    print(f"\n{'='*100}")
    print("参数扫描结果 (按平均收益排序)")
    print(f"{'='*100}")
    print(f"{'vol_mult':>10} {'trail%':>8} {'days':>6} "
          f"{'avg_ret':>10} {'avg_trades':>10} {'avg_win%':>10} {'avg_dd%':>10} {'avg_sharpe':>10} {'pos/n':>6}")
    print("-" * 100)

    for _, row in df_results.head(20).iterrows():
        print(f"{row['vol_mult']:>10.1f} {row['trail_pct']:>7.0%} {row['time_days']:>6.0f} "
              f"{row['avg_return']:>+9.2f}% {row['avg_trades']:>10.1f} "
              f"{row['avg_win_rate']:>9.1f}% {row['avg_drawdown']:>9.1f}% "
              f"{row['avg_sharpe']:>+9.2f} {row['n_positive']:>3.0f}/{row['n_stocks']:.0f}")

    # 当前参数
    print("-" * 100)
    current = df_results[
        (df_results["vol_mult"] == 1.5) &
        (df_results["trail_pct"] == 0.05) &
        (df_results["time_days"] == 10)
    ]
    if not current.empty:
        row = current.iloc[0]
        print(f"{'[当前]':>10} {row['trail_pct']:>7.0%} {row['time_days']:>6.0f} "
              f"{row['avg_return']:>+9.2f}% {row['avg_trades']:>10.1f} "
              f"{row['avg_win_rate']:>9.1f}% {row['avg_drawdown']:>9.1f}% "
              f"{row['avg_sharpe']:>+9.2f} {row['n_positive']:>3.0f}/{row['n_stocks']:.0f}")

    print(f"\n{'='*100}")
    best = df_results.iloc[0]
    print(f"最佳参数: vol_mult={best['vol_mult']}, trail={best['trail_pct']:.0%}, days={best['time_days']:.0f}")
    print(f"  avg_return={best['avg_return']:+.2f}%, avg_trades={best['avg_trades']:.1f}, "
          f"win_rate={best['avg_win_rate']:.1f}%, dd={best['avg_drawdown']:.1f}%")

    # 保存结果
    os.makedirs("results", exist_ok=True)
    df_results.to_csv("results/trend_5d_param_sweep.csv", index=False)
    print(f"\n详细结果已保存: results/trend_5d_param_sweep.csv")


if __name__ == "__main__":
    main()
