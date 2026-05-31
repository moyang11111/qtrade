"""
Experiment: Split backtest into bear market (2022-01 ~ 2024-09-23)
and bull market (2024-09-24 ~ 2025-12) periods.
Analyze strategy performance by regime.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pandas as pd
import numpy as np
from qtrade import DataFetcher, BacktestEngine
from qtrade.strategy import (
    DualMASignal, BollingerSignal, BreakoutSignal,
    RegimeFilterSignal, EventDrivenSignal,
    RegimeFilterV2Signal, EventDrivenV2Signal, AdaptiveSignal,
)

# ── Config ──────────────────────────────────────────────────
SYMBOLS = ["600519", "000858", "000001"]

# Split at 2024-09-24 (policy bull market start)
BEAR_START = "20220101"
BEAR_END = "20240923"
BULL_START = "20240924"
BULL_END = "20251231"

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
    "regime_v2": {
        "class": RegimeFilterV2Signal,
        "params": {
            "fast_ma": 10,
            "slow_ma": 30,
            "bull_threshold": 0.6,
            "bear_threshold": 0.4,
        },
    },
    "event_v2": {
        "class": EventDrivenV2Signal,
        "params": {
            "vol_surge_period": 15,
            "vol_surge_threshold": 1.8,
            "momentum_period": 10,
            "min_gap_pct": 0.02,
        },
    },
    "adaptive": {
        "class": AdaptiveSignal,
        "params": {
            "vol_window": 20,
            "vol_threshold_high": 1.5,
            "vol_threshold_low": 0.7,
            "trend_window": 30,
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
        print(f"  {symbol}")
        print(f"{'='*70}")

        # Fetch full data
        try:
            df_full = fetcher.fetch(symbol=symbol, start=BEAR_START, end=BULL_END)
            print(f"  Full: {len(df_full)} bars")
        except Exception as e:
            print(f"  [SKIP] {e}")
            continue

        # Split into bear and bull periods
        bear_cutoff = pd.Timestamp("2024-09-24")

        df_bear = df_full[df_full.index < bear_cutoff]
        df_bull = df_full[df_full.index >= bear_cutoff]

        print(f"  Bear: {len(df_bear)} bars ({df_bear.index[0].date()} ~ {df_bear.index[-1].date()})")
        print(f"  Bull: {len(df_bull)} bars ({df_bull.index[0].date()} ~ {df_bull.index[-1].date()})")

        # Buy-and-hold benchmark
        bear_bh = (df_bear["close"].iloc[-1] / df_bear["close"].iloc[0] - 1) * 100 if len(df_bear) > 1 else 0
        bull_bh = (df_bull["close"].iloc[-1] / df_bull["close"].iloc[0] - 1) * 100 if len(df_bull) > 1 else 0
        full_bh = (df_full["close"].iloc[-1] / df_full["close"].iloc[0] - 1) * 100 if len(df_full) > 1 else 0
        print(f"  Buy & Hold: bear={bear_bh:+.2f}%  bull={bull_bh:+.2f}%  full={full_bh:+.2f}%")

        for strat_name, strat_info in STRATEGIES.items():
            params = strat_info["params"].copy()
            params["name"] = strat_name

            for period_name, df_period in [("bear", df_bear), ("bull", df_bull), ("full", df_full)]:
                metrics = run_single(strat_info["class"], params, df_period)

                row = {
                    "symbol": symbol,
                    "strategy": strat_name,
                    "period": period_name,
                }
                row.update(metrics)
                all_results.append(row)

                ret = metrics.get("total_return", "ERR")
                trades = metrics.get("total_trades", 0)
                if isinstance(ret, (int, float)):
                    print(f"    [{period_name:>4}] {strat_name:<16}  ret={ret:+7.2f}%  trades={trades}")
                else:
                    print(f"    [{period_name:>4}] {strat_name:<16}  ERROR: {metrics.get('error', 'unknown')}")

    # ── Results DataFrame ───────────────────────────────────
    results_df = pd.DataFrame(all_results)
    out_dir = "results"
    os.makedirs(out_dir, exist_ok=True)
    results_df.to_csv(f"{out_dir}/regime_split_results.csv", index=False)

    # ── Analysis ────────────────────────────────────────────
    numeric = results_df.copy()
    for col in ["total_return", "sharpe_ratio", "max_drawdown", "total_trades", "win_rate"]:
        numeric[col] = pd.to_numeric(numeric[col], errors="coerce")

    valid = numeric.dropna(subset=["total_return"])

    # ── Bear vs Bull comparison ─────────────────────────────
    print(f"\n{'='*70}")
    print("  BEAR MARKET (2022-01 ~ 2024-09-23)")
    print(f"{'='*70}")
    bear = valid[valid["period"] == "bear"]
    if not bear.empty:
        bear_summary = bear.groupby("strategy").agg({
            "total_return": "mean",
            "sharpe_ratio": "mean",
            "max_drawdown": "mean",
            "win_rate": "mean",
            "total_trades": "mean",
        }).round(2)
        print(bear_summary.to_string())

    print(f"\n{'='*70}")
    print("  BULL MARKET (2024-09-24 ~ 2025-12)")
    print(f"{'='*70}")
    bull = valid[valid["period"] == "bull"]
    if not bull.empty:
        bull_summary = bull.groupby("strategy").agg({
            "total_return": "mean",
            "sharpe_ratio": "mean",
            "max_drawdown": "mean",
            "win_rate": "mean",
            "total_trades": "mean",
        }).round(2)
        print(bull_summary.to_string())

    print(f"\n{'='*70}")
    print("  FULL PERIOD (2022-01 ~ 2025-12)")
    print(f"{'='*70}")
    full = valid[valid["period"] == "full"]
    if not full.empty:
        full_summary = full.groupby("strategy").agg({
            "total_return": "mean",
            "sharpe_ratio": "mean",
            "max_drawdown": "mean",
            "win_rate": "mean",
            "total_trades": "mean",
        }).round(2)
        print(full_summary.to_string())

    # ── Best strategies by regime ───────────────────────────
    print(f"\n{'='*70}")
    print("  Best strategy by regime")
    print(f"{'='*70}")
    for period in ["bear", "bull", "full"]:
        subset = valid[valid["period"] == period]
        if subset.empty:
            continue
        best = subset.sort_values("total_return", ascending=False).head(1)
        row = best.iloc[0]
        print(f"\n  {period.upper()}: {row['strategy']}")
        print(f"    ret={row['total_return']:+.2f}%  "
              f"sharpe={row.get('sharpe_ratio', 'N/A')}  "
              f"dd={row['max_drawdown']:.2f}%  "
              f"trades={row['total_trades']:.0f}")


if __name__ == "__main__":
    main()
