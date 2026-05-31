"""
Test new strategies: Regime Filter and Event-Driven.
Compare against baseline strategies.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from itertools import product
import pandas as pd
from qtrade import DataFetcher, BacktestEngine
from qtrade.strategy import (
    DualMASignal, BollingerSignal, BreakoutSignal,
    RegimeFilterSignal, EventDrivenSignal,
)


# ── Config ──────────────────────────────────────────────────
SYMBOLS = ["600519", "000858", "000001"]
START = "20220101"
END = "20251231"

BT_CONFIG = {
    "backtest": {
        "initial_capital": 1000000,
        "commission": 0.0003,
        "min_commission": 5.0,
        "slippage": 0.001,
        "lot_size": 100,
        "stop_loss_pct": 0.10,
        "trail_stop_pct": 0.08,
    }
}

# ── Strategies to test ──────────────────────────────────────
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
            "ma_short": 20,
            "ma_long": 60,
            "ma_very_long": 120,
            "bull_boost": 1.2,
            "bear_reduce": 0.5,
            "sideways_reduce": 0.7,
        },
    },
    "event_driven": {
        "class": EventDrivenSignal,
        "params": {
            "vol_surge_period": 20,
            "vol_surge_threshold": 2.0,
            "gap_threshold": 0.03,
            "fund_flow_confirm": True,
            "vol_ratio_confirm": True,
            "lookback_days": 5,
        },
    },
}


def run_single(strategy_cls, params: dict, df: pd.DataFrame) -> dict:
    """Run one strategy, return metrics."""
    cfg = {"name": params.get("name", strategy_cls.__name__)}
    cfg.update(params)
    strategy = strategy_cls(cfg)

    try:
        df_sig = strategy.generate_signals(df)
        engine = BacktestEngine(BT_CONFIG)
        result = engine.run(df_sig)
        return result.metrics
    except Exception as e:
        return {"error": str(e)}


def main():
    fetcher = DataFetcher({"data": {}})
    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*70}")
        print(f"  Fetching {symbol}")
        print(f"{'='*70}")
        try:
            df = fetcher.fetch(symbol=symbol, start=START, end=END)
            print(f"  Got {len(df)} bars: {df.index[0].date()} ~ {df.index[-1].date()}")
        except Exception as e:
            print(f"  [SKIP] {e}")
            continue

        for strat_name, strat_info in STRATEGIES.items():
            params = strat_info["params"].copy()
            params["name"] = strat_name

            print(f"\n  Testing {strat_name}...")
            metrics = run_single(strat_info["class"], params, df)

            row = {"symbol": symbol, "strategy": strat_name}
            row.update(params)
            row.update(metrics)
            all_results.append(row)

            ret = metrics.get("total_return", "ERR")
            trades = metrics.get("total_trades", 0)
            if isinstance(ret, (int, float)):
                print(f"    ret={ret:+.2f}%  trades={trades}")
            else:
                print(f"    ERROR: {metrics.get('error', 'unknown')}")

    # ── Results DataFrame ───────────────────────────────────
    results_df = pd.DataFrame(all_results)
    out_dir = "results"
    os.makedirs(out_dir, exist_ok=True)
    results_df.to_csv(f"{out_dir}/new_strategies_results.csv", index=False)
    print(f"\n{'='*70}")
    print(f"  Results saved to {out_dir}/new_strategies_results.csv")
    print(f"  Total experiments: {len(results_df)}")
    print(f"{'='*70}")

    # ── Analysis ────────────────────────────────────────────
    numeric = results_df.copy()
    numeric["total_return"] = pd.to_numeric(numeric["total_return"], errors="coerce")
    numeric["sharpe_ratio"] = pd.to_numeric(numeric["sharpe_ratio"], errors="coerce")
    numeric["max_drawdown"] = pd.to_numeric(numeric["max_drawdown"], errors="coerce")
    numeric["total_trades"] = pd.to_numeric(numeric["total_trades"], errors="coerce")
    numeric["win_rate"] = pd.to_numeric(numeric["win_rate"], errors="coerce")

    valid = numeric.dropna(subset=["total_return"])
    valid = valid[valid["total_trades"] > 0]

    print(f"\n  Valid runs (with trades): {len(valid)}")

    if valid.empty:
        print("  No valid results to analyze.")
        return

    # Best per strategy
    print(f"\n{'='*70}")
    print("  Best parameter sets per strategy (by Sharpe)")
    print(f"{'='*70}")
    for strat_name in STRATEGIES:
        subset = valid[valid["strategy"] == strat_name]
        if subset.empty:
            print(f"\n  {strat_name}: no valid runs")
            continue
        best = subset.sort_values("sharpe_ratio", ascending=False).head(3)
        print(f"\n  {strat_name} — Top 3:")
        for _, row in best.iterrows():
            print(f"    {row['symbol']}")
            print(f"      ret={row['total_return']:+.2f}%  "
                  f"sharpe={row.get('sharpe_ratio', 'N/A')}  "
                  f"dd={row['max_drawdown']:.2f}%  "
                  f"trades={row['total_trades']:.0f}  "
                  f"winrate={row['win_rate']:.1f}%")

    # Strategy comparison (avg across symbols)
    print(f"\n{'='*70}")
    print("  Strategy comparison (avg across all symbols)")
    print(f"{'='*70}")
    strat_summary = valid.groupby("strategy").agg({
        "total_return": ["mean", "median", "std"],
        "sharpe_ratio": "mean",
        "max_drawdown": "mean",
        "win_rate": "mean",
        "total_trades": "mean",
    }).round(2)
    print(strat_summary.to_string())


if __name__ == "__main__":
    main()
