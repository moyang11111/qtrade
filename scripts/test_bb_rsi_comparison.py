"""布林带+RSI策略全面对比测试"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
from qtrade.backtest.engine import BacktestEngine
from qtrade.data.fetcher import DataFetcher
from qtrade.strategy import (
    DualMASignal,
    BollingerSignal,
    BreakoutSignal,
    RegimeFilterSignal,
    EventDrivenSignal,
    RegimeFilterV2Signal,
    EventDrivenV2Signal,
    AdaptiveSignal,
    AdaptiveHybridSignal,
    BBRsiSignal,
)

# 测试股票池
SYMBOLS = [
    "300750",  # 宁德时代
    "600519",  # 贵州茅台
    "000001",  # 平安银行
    "000858",  # 五粮液
    "300033",  # 同花顺
    "002049",  # 紫光国微
    "300394",  # 天孚通信
    "002709",  # 天赐材料
]

# 策略配置
STRATEGIES = {
    "dual_ma": {
        "class": DualMASignal,
        "params": {"fast_period": 5, "slow_period": 20},
    },
    "bollinger": {
        "class": BollingerSignal,
        "params": {"period": 20, "std_mult": 2.0},
    },
    "breakout": {
        "class": BreakoutSignal,
        "params": {"entry_period": 20, "exit_period": 10},
    },
    "regime_filter": {
        "class": RegimeFilterSignal,
        "params": {
            "ma_short": 20, "ma_long": 60, "ma_very_long": 120,
            "bull_boost": 1.2, "bear_reduce": 0.5, "sideways_reduce": 0.7,
        },
    },
    "event_driven": {
        "class": EventDrivenSignal,
        "params": {
            "vol_surge_period": 20, "vol_surge_threshold": 2.0,
            "gap_threshold": 0.03, "fund_flow_confirm": True,
            "vol_ratio_confirm": True, "lookback_days": 5,
        },
    },
    "regime_v2": {
        "class": RegimeFilterV2Signal,
        "params": {"fast_ma": 10, "slow_ma": 30, "bull_threshold": 0.6, "bear_threshold": 0.4},
    },
    "event_v2": {
        "class": EventDrivenV2Signal,
        "params": {"vol_window": 20, "vol_threshold": 1.8, "min_gap": 0.02},
    },
    "adaptive": {
        "class": AdaptiveSignal,
        "params": {"vol_window": 20, "vol_threshold_high": 1.5, "vol_threshold_low": 0.7, "trend_window": 30},
    },
    "hybrid": {
        "class": AdaptiveHybridSignal,
        "params": {
            "ma_fast": 5, "ma_mid": 20, "ma_long": 60, "persistence_bars": 3,
            "breakout_entry": 20, "breakout_exit": 10, "vol_factor": 1.3,
            "dual_fast": 5, "dual_slow": 20, "trend_window": 20,
        },
    },
    "bb_rsi": {
        "class": BBRsiSignal,
        "params": {
            "bb_period": 20, "bb_std": 2.0,
            "rsi_period": 14, "rsi_buy": 20, "rsi_sell": 65,
        },
    },
}

# 回测配置
BACKTEST_CONFIG = {
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


def run_single_backtest(df, strategy_class, params):
    """运行单个策略回测"""
    strategy = strategy_class(params)
    df_signals = strategy.generate_signals(df)
    engine = BacktestEngine(BACKTEST_CONFIG)
    result = engine.run(df_signals)
    return result.metrics


def main():
    print("=" * 80)
    print("布林带+RSI策略全面对比测试")
    print("=" * 80)
    print()

    # 获取数据
    fetcher = DataFetcher({"data": {"source": "pytdx"}})

    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"测试股票: {symbol}")
        print(f"{'='*60}")

        try:
            df = fetcher.fetch(symbol, "2022-01-01", "2024-12-31")
            if df is None or len(df) == 0:
                print(f"  跳过: 无数据")
                continue

            print(f"  数据量: {len(df)} 条K线")

            # 测试所有策略
            for strat_name, strat_config in STRATEGIES.items():
                try:
                    metrics = run_single_backtest(
                        df,
                        strat_config["class"],
                        strat_config["params"]
                    )

                    total_return = metrics.get("total_return", 0)
                    trades = metrics.get("total_trades", 0)
                    win_rate = metrics.get("win_rate", 0)
                    max_dd = metrics.get("max_drawdown", 0)
                    sharpe = metrics.get("sharpe_ratio", 0)

                    all_results.append({
                        "symbol": symbol,
                        "strategy": strat_name,
                        "total_return": total_return,
                        "total_trades": trades,
                        "win_rate": win_rate,
                        "max_drawdown": max_dd,
                        "sharpe_ratio": sharpe,
                    })

                    print(f"  {strat_name:20s}: return={total_return:+7.2f}%  "
                          f"trades={trades:3d}  win={win_rate:5.1f}%  "
                          f"dd={max_dd:5.1f}%  sharpe={sharpe:+.2f}")

                except Exception as e:
                    print(f"  {strat_name:20s}: ERROR - {str(e)[:50]}")

        except Exception as e:
            print(f"  跳过: {str(e)[:50]}")

    # 汇总分析
    if all_results:
        print(f"\n\n{'='*80}")
        print("汇总分析")
        print(f"{'='*80}")

        df_results = pd.DataFrame(all_results)

        # 按策略汇总
        strategy_summary = df_results.groupby("strategy").agg({
            "total_return": ["mean", "std", "count"],
            "total_trades": "mean",
            "win_rate": "mean",
            "max_drawdown": "mean",
            "sharpe_ratio": "mean",
        }).round(2)

        strategy_summary.columns = [
            "avg_return", "std_return", "count",
            "avg_trades", "avg_win_rate", "avg_dd", "avg_sharpe"
        ]

        # 按平均收益排序
        strategy_summary = strategy_summary.sort_values("avg_return", ascending=False)

        print("\n策略排名 (按平均收益):")
        print("-" * 100)
        print(f"{'策略':<20} {'平均收益':>10} {'标准差':>10} {'股票数':>8} "
              f"{'平均交易':>10} {'平均胜率':>10} {'平均回撤':>10} {'夏普比率':>10}")
        print("-" * 100)

        for strat_name, row in strategy_summary.iterrows():
            marker = " <-- BB_RSI" if strat_name == "bb_rsi" else ""
            print(f"{strat_name:<20} {row['avg_return']:>+9.2f}% {row['std_return']:>9.2f} "
                  f"{int(row['count']):>8d} {row['avg_trades']:>10.1f} "
                  f"{row['avg_win_rate']:>9.1f}% {row['avg_dd']:>9.1f}% "
                  f"{row['avg_sharpe']:>+9.2f}{marker}")

        # 保存详细结果
        results_file = os.path.join(os.path.dirname(__file__), "..", "results", "bb_rsi_comparison.csv")
        df_results.to_csv(results_file, index=False)
        print(f"\n详细结果已保存: {results_file}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
