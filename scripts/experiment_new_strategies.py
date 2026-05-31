"""Experiment: Test new strategies (regime filter, event-driven) against baselines."""

import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

from qtrade.data.fetcher import DataFetcher
from qtrade.backtest.engine import BacktestEngine
from qtrade.strategy import (
    DualMASignal,
    BollingerSignal,
    BreakoutSignal,
    RegimeFilterSignal,
    EventDrivenSignal,
)
from qtrade.backtest.performance import PerformanceAnalyzer


def run_strategy_backtest(strategy_name: str, strategy, df: pd.DataFrame,
                           initial_capital: float = 1000000) -> dict:
    """Run backtest for a single strategy."""
    print(f"\n{'='*60}")
    print(f"Testing: {strategy_name}")
    print(f"{'='*60}")

    try:
        # Generate signals
        signals_df = strategy.generate_signals(df)

        # Create backtest engine
        engine = BacktestEngine(
            initial_capital=initial_capital,
            commission_rate=0.0003,  # 万三
            slippage=0.001,  # 0.1%
        )

        # Run backtest
        result = engine.run_backtest(signals_df)

        # Analyze performance
        analyzer = PerformanceAnalyzer(
            initial_capital=initial_capital,
            risk_free_rate=0.03,
        )

        metrics = analyzer.compute_all_metrics(
            equity_curve=result.equity_curve,
            trades=result.trades,
        )

        print(f"✓ Backtest completed")
        print(f"  Total Return: {metrics['total_return']:.2%}")
        print(f"  Sharpe Ratio: {metrics['sharpe_ratio']:.3f}")
        print(f"  Max Drawdown: {metrics['max_drawdown']:.2%}")
        print(f"  Win Rate: {metrics['win_rate']:.2%}")
        print(f"  Total Trades: {metrics['total_trades']}")

        return {
            "strategy": strategy_name,
            "metrics": metrics,
            "result": result,
        }

    except Exception as e:
        print(f"✗ Backtest failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Run comprehensive strategy experiments."""
    print("="*60)
    print("Strategy Experiment Suite")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)

    # Configuration
    symbol = "000001"  # 平安银行
    start_date = "20200101"
    end_date = "20231231"
    initial_capital = 1000000

    # Fetch data
    print(f"\nFetching data for {symbol} ({start_date} to {end_date})...")
    fetcher = DataFetcher()

    try:
        df = fetcher.fetch_history(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )
        print(f"✓ Loaded {len(df)} bars")
        print(f"  Date range: {df.index[0]} to {df.index[-1]}")
    except Exception as e:
        print(f"✗ Failed to fetch data: {e}")
        return

    # Define strategies to test
    strategies = {
        "Baseline: Dual MA": DualMASignal({
            "fast_period": 5,
            "slow_period": 20,
        }),
        "Baseline: Bollinger": BollingerSignal({
            "period": 20,
            "std_dev": 2.0,
        }),
        "Baseline: Breakout": BreakoutSignal({
            "entry_period": 20,
            "exit_period": 10,
        }),
        "NEW: Regime Filter": RegimeFilterSignal({
            "ma_short": 20,
            "ma_long": 60,
            "ma_very_long": 120,
            "bull_boost": 1.2,
            "bear_reduce": 0.5,
        }),
        "NEW: Event-Driven": EventDrivenSignal({
            "vol_surge_period": 20,
            "vol_surge_threshold": 2.0,
            "gap_threshold": 0.03,
            "fund_flow_confirm": True,
            "vol_ratio_confirm": True,
        }),
    }

    # Run all strategies
    results = []
    for name, strategy in strategies.items():
        result = run_strategy_backtest(name, strategy, df, initial_capital)
        if result:
            results.append(result)

    # Compare results
    if results:
        print("\n" + "="*60)
        print("Strategy Comparison")
        print("="*60)

        comparison_data = []
        for r in results:
            m = r["metrics"]
            comparison_data.append({
                "Strategy": r["strategy"],
                "Total Return": f"{m['total_return']:.2%}",
                "Sharpe": f"{m['sharpe_ratio']:.3f}",
                "Max DD": f"{m['max_drawdown']:.2%}",
                "Win Rate": f"{m['win_rate']:.2%}",
                "Trades": m["total_trades"],
            })

        comparison_df = pd.DataFrame(comparison_data)
        print("\n" + comparison_df.to_string(index=False))

        # Save results
        output_dir = Path("results")
        output_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"strategy_comparison_{timestamp}.csv"
        comparison_df.to_csv(output_file, index=False)
        print(f"\n✓ Results saved to: {output_file}")

        # Analyze best strategy
        best_idx = np.argmax([r["metrics"]["sharpe_ratio"] for r in results])
        best_strategy = results[best_idx]
        print(f"\n🏆 Best Strategy (by Sharpe): {best_strategy['strategy']}")
        print(f"   Sharpe Ratio: {best_strategy['metrics']['sharpe_ratio']:.3f}")
        print(f"   Total Return: {best_strategy['metrics']['total_return']:.2%}")

    print("\n" + "="*60)
    print(f"Experiment completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*60)


if __name__ == "__main__":
    main()
