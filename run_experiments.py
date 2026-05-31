"""
Strategy parameter sweep experiment.

Runs all strategies across parameter grids, collects metrics,
and identifies best parameter/factor combinations.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from itertools import product
import pandas as pd
from qtrade import DataFetcher, BacktestEngine
from qtrade.strategy import (
    DualMASignal, BollingerSignal, BreakoutSignal,
    list_strategies, get_signal_generator,
)


# ── Config ──────────────────────────────────────────────────
SYMBOLS = ["600519", "000858", "000001"]   # 茅台, 五粮液, 平安银行
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

# ── Parameter grids ─────────────────────────────────────────
PARAM_GRIDS = {
    "dual_ma": {
        "class": DualMASignal,
        "grid": {
            "fast_period": [3, 5, 10, 15],
            "slow_period": [20, 30, 50, 60],
        },
    },
    "bollinger": {
        "class": BollingerSignal,
        "grid": {
            "period": [10, 20, 30, 50],
            "std_mult": [1.5, 2.0, 2.5, 3.0],
        },
    },
    "breakout": {
        "class": BreakoutSignal,
        "grid": {
            "entry_period": [10, 20, 30, 50],
            "exit_period": [5, 10, 15, 20],
        },
    },
}


# ── Helpers ─────────────────────────────────────────────────
def grid_combos(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    vals = list(grid.values())
    return [dict(zip(keys, combo)) for combo in product(*vals)]


def run_single(strategy_cls, params: dict, df: pd.DataFrame) -> dict:
    """Run one strategy with one parameter set, return metrics."""
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


# ── Main ────────────────────────────────────────────────────
def main():
    fetcher = DataFetcher({"data": {}})
    all_results = []

    for symbol in SYMBOLS:
        print(f"\n{'='*60}")
        print(f"  Fetching {symbol}")
        print(f"{'='*60}")
        try:
            df = fetcher.fetch(symbol=symbol, start=START, end=END)
            print(f"  Got {len(df)} bars: {df.index[0].date()} ~ {df.index[-1].date()}")
        except Exception as e:
            print(f"  [SKIP] {e}")
            continue

        for strat_name, strat_info in PARAM_GRIDS.items():
            combos = grid_combos(strat_info["grid"])
            print(f"\n  {strat_name}: {len(combos)} param combos")

            for params in combos:
                params["name"] = strat_name
                metrics = run_single(strat_info["class"], params, df)

                row = {"symbol": symbol, "strategy": strat_name}
                row.update(params)
                row.update(metrics)
                all_results.append(row)

                ret = metrics.get("total_return", "ERR")
                trades = metrics.get("total_trades", 0)
                if isinstance(ret, (int, float)):
                    print(f"    {params}  ->  ret={ret:+.2f}%  trades={trades}")
                else:
                    print(f"    {params}  ->  ERROR: {metrics.get('error', 'unknown')}")

    # ── Results DataFrame ───────────────────────────────────
    results_df = pd.DataFrame(all_results)
    out_dir = "results"
    os.makedirs(out_dir, exist_ok=True)
    results_df.to_csv(f"{out_dir}/experiment_results.csv", index=False)
    print(f"\n{'='*60}")
    print(f"  Results saved to {out_dir}/experiment_results.csv")
    print(f"  Total experiments: {len(results_df)}")
    print(f"{'='*60}")

    # ── Analysis ────────────────────────────────────────────
    numeric = results_df.copy()
    numeric["total_return"] = pd.to_numeric(numeric["total_return"], errors="coerce")
    numeric["sharpe_ratio"] = pd.to_numeric(numeric["sharpe_ratio"], errors="coerce")
    numeric["max_drawdown"] = pd.to_numeric(numeric["max_drawdown"], errors="coerce")
    numeric["total_trades"] = pd.to_numeric(numeric["total_trades"], errors="coerce")
    numeric["win_rate"] = pd.to_numeric(numeric["win_rate"], errors="coerce")

    valid = numeric.dropna(subset=["total_return"])
    valid = valid[valid["total_trades"] > 0]  # skip no-trade runs

    print(f"\n  Valid runs (with trades): {len(valid)}")

    if valid.empty:
        print("  No valid results to analyze.")
        return

    # Best per strategy
    print(f"\n{'='*60}")
    print("  Best parameter sets per strategy (by Sharpe)")
    print(f"{'='*60}")
    for strat_name in PARAM_GRIDS:
        subset = valid[valid["strategy"] == strat_name]
        if subset.empty:
            print(f"\n  {strat_name}: no valid runs")
            continue
        best = subset.sort_values("sharpe_ratio", ascending=False).head(3)
        print(f"\n  {strat_name} — Top 3:")
        for _, row in best.iterrows():
            param_cols = [k for k in PARAM_GRIDS[strat_name]["grid"].keys()]
            params_str = ", ".join(f"{k}={row[k]}" for k in param_cols)
            print(f"    {params_str}")
            print(f"      ret={row['total_return']:+.2f}%  "
                  f"sharpe={row.get('sharpe_ratio', 'N/A')}  "
                  f"dd={row['max_drawdown']:.2f}%  "
                  f"trades={row['total_trades']:.0f}  "
                  f"winrate={row['win_rate']:.1f}%")

    # Strategy comparison (avg across symbols)
    print(f"\n{'='*60}")
    print("  Strategy comparison (avg across all symbols)")
    print(f"{'='*60}")
    strat_summary = valid.groupby("strategy").agg({
        "total_return": ["mean", "median", "std"],
        "sharpe_ratio": "mean",
        "max_drawdown": "mean",
        "win_rate": "mean",
        "total_trades": "mean",
    }).round(2)
    print(strat_summary.to_string())

    # Parameter sensitivity: which param values produce best Sharpe on average
    print(f"\n{'='*60}")
    print("  Parameter sensitivity (avg Sharpe per param value)")
    print(f"{'='*60}")
    for strat_name, strat_info in PARAM_GRIDS.items():
        subset = valid[valid["strategy"] == strat_name]
        if subset.empty:
            continue
        print(f"\n  {strat_name}:")
        for param_name in strat_info["grid"]:
            sens = subset.groupby(param_name)["sharpe_ratio"].mean().round(3)
            print(f"    {param_name}:")
            for val, avg_sharpe in sens.items():
                print(f"      {val:>5}  ->  avg Sharpe = {avg_sharpe:+.3f}")


if __name__ == "__main__":
    main()
