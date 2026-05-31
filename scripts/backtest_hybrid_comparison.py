"""
Backtest comparison: Hybrid strategy vs existing strategies
Tests all strategies on 20 stocks across 3 market regimes (bear/bull/full)
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy import (
    DualMASignal,
    BollingerSignal,
    BreakoutSignal,
    RegimeFilterSignal,
    RegimeFilterV2Signal,
    EventDrivenSignal,
    EventDrivenV2Signal,
    AdaptiveSignal,
    AdaptiveHybridSignal,
)

# 20 stocks across multiple sectors (excluding alcohol/consumer)
SYMBOLS = [
    # Tech
    "300033",  # 同花顺
    "002049",  # 紫光国微
    "688012",  # 中微公司
    "300394",  # 天孚通信
    "002371",  # 北方华创
    # New Energy
    "300750",  # 宁德时代
    "601012",  # 隆基绿能
    "300274",  # 阳光电源
    "002709",  # 天赐材料
    "300014",  # 亿纬锂能
    # Pharma/Biotech
    "300760",  # 迈瑞医疗
    "300347",  # 泰格医药
    "300122",  # 智飞生物
    "002821",  # 凯莱英
    "300529",  # 健帆生物
    # Finance
    "601688",  # 华泰证券
    "600030",  # 中信证券
    "000776",  # 广发证券
    "601318",  # 中国平安
    "601628",  # 中国人寿
]

# Market regime periods
BEAR_START = "2022-01-01"
BEAR_END = "2024-09-23"
BULL_START = "2024-09-24"
BULL_END = "2025-12-31"

# Strategies to test
STRATEGIES = {
    "dual_ma": DualMASignal,
    "bollinger": BollingerSignal,
    "breakout": BreakoutSignal,
    "regime_filter": RegimeFilterSignal,
    "regime_v2": RegimeFilterV2Signal,
    "event_driven": EventDrivenSignal,
    "event_v2": EventDrivenV2Signal,
    "adaptive": AdaptiveSignal,
    "hybrid": AdaptiveHybridSignal,
}


def load_stock_data(symbol):
    """Load stock data from CSV"""
    cache_dir = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
    csv_path = os.path.join(cache_dir, f"{symbol}.csv")

    if not os.path.exists(csv_path):
        return None

    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    return df


def run_backtest(df, strategy_class, config=None):
    """Run backtest with given strategy"""
    if config is None:
        config = {
            "backtest": {
                "initial_capital": 1000000,
                "commission": 0.0003,
                "slippage": 0.001,
                "stop_loss_pct": 0.08,
                "trail_stop_pct": 0.15,
                "lot_size": 100,
            },
            "position_sizing": {
                "method": "fixed",
                "fixed_pct": 0.95,
            },
        }

    # Create strategy instance and generate signals
    strategy = strategy_class({})
    df_with_signals = strategy.generate_signals(df)

    # Run backtest
    engine = BacktestEngine(config)
    result = engine.run(df_with_signals)
    return result


def main():
    """Run comprehensive backtest comparison"""

    print("=" * 80)
    print("HYBRID STRATEGY BACKTEST COMPARISON")
    print("=" * 80)

    # Collect all results
    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"Testing {symbol}")
        print(f"{'='*60}")

        # Load full data
        df_full = load_stock_data(symbol)
        if df_full is None:
            print(f"  [SKIP] No data available")
            continue

        # Split into regimes
        df_bear = df_full[(df_full.index >= BEAR_START) & (df_full.index <= BEAR_END)]
        df_bull = df_full[(df_full.index >= BULL_START) & (df_full.index <= BULL_END)]

        print(f"  Data range: {df_full.index[0].date()} ~ {df_full.index[-1].date()}")
        print(f"  Bear period: {len(df_bear)} bars")
        print(f"  Bull period: {len(df_bull)} bars")

        # Test each strategy on each regime
        for strat_name, strat_class in STRATEGIES.items():
            print(f"\n  [{strat_name}]")

            # Full period
            if len(df_full) > 100:
                try:
                    result = run_backtest(df_full, strat_class)
                    metrics = result.metrics
                    print(f"    FULL:  return={metrics['total_return']:>8.2f}%  "
                          f"trades={metrics['total_trades']:>3}  "
                          f"win_rate={metrics['win_rate']:>6.1f}%  "
                          f"max_dd={metrics['max_drawdown']:>6.1f}%")

                    all_results.append({
                        "symbol": symbol,
                        "strategy": strat_name,
                        "period": "full",
                        "total_return": metrics["total_return"],
                        "total_trades": metrics["total_trades"],
                        "win_rate": metrics["win_rate"],
                        "max_drawdown": metrics["max_drawdown"],
                        "sharpe_ratio": metrics.get("sharpe_ratio", 0),
                    })
                except Exception as e:
                    print(f"    FULL:  ERROR - {e}")

            # Bear period
            if len(df_bear) > 50:
                try:
                    result = run_backtest(df_bear, strat_class)
                    metrics = result.metrics
                    print(f"    BEAR:  return={metrics['total_return']:>8.2f}%  "
                          f"trades={metrics['total_trades']:>3}  "
                          f"win_rate={metrics['win_rate']:>6.1f}%  "
                          f"max_dd={metrics['max_drawdown']:>6.1f}%")

                    all_results.append({
                        "symbol": symbol,
                        "strategy": strat_name,
                        "period": "bear",
                        "total_return": metrics["total_return"],
                        "total_trades": metrics["total_trades"],
                        "win_rate": metrics["win_rate"],
                        "max_drawdown": metrics["max_drawdown"],
                        "sharpe_ratio": metrics.get("sharpe_ratio", 0),
                    })
                except Exception as e:
                    print(f"    BEAR:  ERROR - {e}")

            # Bull period
            if len(df_bull) > 50:
                try:
                    result = run_backtest(df_bull, strat_class)
                    metrics = result.metrics
                    print(f"    BULL:  return={metrics['total_return']:>8.2f}%  "
                          f"trades={metrics['total_trades']:>3}  "
                          f"win_rate={metrics['win_rate']:>6.1f}%  "
                          f"max_dd={metrics['max_drawdown']:>6.1f}%")

                    all_results.append({
                        "symbol": symbol,
                        "strategy": strat_name,
                        "period": "bull",
                        "total_return": metrics["total_return"],
                        "total_trades": metrics["total_trades"],
                        "win_rate": metrics["win_rate"],
                        "max_drawdown": metrics["max_drawdown"],
                        "sharpe_ratio": metrics.get("sharpe_ratio", 0),
                    })
                except Exception as e:
                    print(f"    BULL:  ERROR - {e}")

    # Analyze results
    print("\n" + "=" * 80)
    print("AGGREGATE RESULTS ANALYSIS")
    print("=" * 80)

    results_df = pd.DataFrame(all_results)

    for period in ["full", "bear", "bull"]:
        print(f"\n{'='*60}")
        print(f"{period.upper()} PERIOD - STRATEGY RANKING")
        print(f"{'='*60}")

        period_df = results_df[results_df["period"] == period]
        if len(period_df) == 0:
            print("  No data")
            continue

        # Group by strategy
        strategy_stats = period_df.groupby("strategy").agg({
            "total_return": ["mean", "std", "count"],
            "win_rate": "mean",
            "max_drawdown": "mean",
            "sharpe_ratio": "mean",
        }).round(2)

        strategy_stats.columns = ["avg_return", "std_return", "count",
                                  "avg_win_rate", "avg_max_dd", "avg_sharpe"]
        strategy_stats = strategy_stats.sort_values("avg_return", ascending=False)

        print("\n  Strategy            Avg Return    Std    Win Rate    Max DD    Sharpe    Count")
        print("  " + "-" * 76)

        for strat_name, row in strategy_stats.iterrows():
            marker = " <-- HYBRID" if strat_name == "hybrid" else ""
            print(f"  {strat_name:<20} {row['avg_return']:>8.2f}%  "
                  f"{row['std_return']:>6.2f}  "
                  f"{row['avg_win_rate']:>8.1f}%  "
                  f"{row['avg_max_dd']:>7.1f}%  "
                  f"{row['avg_sharpe']:>8.2f}  "
                  f"{int(row['count']):>5}{marker}")

        # Best performer
        best = strategy_stats.index[0]
        best_return = strategy_stats.loc[best, "avg_return"]
        print(f"\n  Best performer: {best} ({best_return:.2f}%)")

        # Hybrid rank
        if "hybrid" in strategy_stats.index:
            hybrid_rank = list(strategy_stats.index).index("hybrid") + 1
            hybrid_return = strategy_stats.loc["hybrid", "avg_return"]
            print(f"  Hybrid rank: #{hybrid_rank} ({hybrid_return:.2f}%)")

    # Save results
    output_dir = os.path.join(os.path.dirname(__file__), "..", "results")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, "hybrid_backtest_results.csv")
    results_df.to_csv(output_path, index=False)
    print(f"\n{'='*60}")
    print(f"Results saved to: {output_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
