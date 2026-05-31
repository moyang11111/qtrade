"""回测：Pullback20D 纯信号驱动策略。

直接模拟：找到信号 → 下一日买入 → 20日后卖出。
与 SignalFollower 不同，不做仓位管理、不设止盈止损。
"""

import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import pandas as pd
import numpy as np


def run_direct_backtest(data_dir="data/cache", start="2026-01-01", end="2026-05-27"):
    """手动模拟：找到信号 → 全仓买入 → 20日后卖出 → 统计结果。"""
    cache = Path(data_dir)
    all_trades = []

    for csv_file in sorted(cache.glob("*.csv")):
        df = pd.read_csv(csv_file, parse_dates=["date"], index_col="date")
        df.columns = [c.lower() for c in df.columns]
        d = df[start:end].copy()
        if len(d) < 80:
            continue
        sym = csv_file.stem

        close = d["close"].values
        volume = d["volume"].values
        n = len(close)

        # 计算指标
        ma60 = pd.Series(close).rolling(60).mean().values
        peak = pd.Series(close).rolling(60).max().values
        vol_ratio = np.full(n, np.nan)
        for i in range(20, n):
            v5 = np.mean(volume[max(0, i-4):i+1])
            v20 = np.mean(volume[max(0, i-19):i+1])
            vol_ratio[i] = v5 / v20 if v20 > 0 else 1

        # 找买入信号
        for i in range(60, n):
            drop_pct = (peak[i] - close[i]) / peak[i]
            if not (0.15 <= drop_pct <= 0.40):
                continue
            if close[i] <= ma60[i]:
                continue
            if vol_ratio[i] >= 0.7:
                continue

            entry_idx = i + 1
            if entry_idx >= n:
                continue
            entry_price = close[entry_idx]

            exit_idx = entry_idx + 20
            if exit_idx >= n:
                exit_idx = n - 1
            exit_price = close[exit_idx]

            ret = (exit_price / entry_price - 1) * 100
            actual_hold = exit_idx - entry_idx

            all_trades.append({
                "sym": sym,
                "entry_date": str(d.index[entry_idx].date()),
                "exit_date": str(d.index[exit_idx].date()),
                "entry_price": round(entry_price, 2),
                "exit_price": round(exit_price, 2),
                "drop_pct": round(drop_pct * 100, 1),
                "vol_ratio": round(vol_ratio[i], 2),
                "hold_days": actual_hold,
                "return": round(ret, 2),
            })

    return pd.DataFrame(all_trades)


if __name__ == "__main__":
    df = run_direct_backtest()

    if df.empty:
        print("No trades found!")
        sys.exit(0)

    print(f"{'='*65}")
    print(f"  Pullback 20-Day Hold Strategy (2026 Jan-May)")
    print(f"  Entry: 15-40% drop from 60d high + volume shrink < 0.7")
    print(f"  Exit:  Hold 20 trading days, no SL/TP")
    print(f"{'='*65}")
    print(f"  Total trades: {len(df)}")
    print(f"  Winners: {(df['return'] > 0).sum()} ({(df['return'] > 0).sum()/len(df)*100:.0f}%)")
    print(f"  Average return: {df['return'].mean():+.1f}%")
    print(f"  Median return: {df['return'].median():+.1f}%")
    print(f"  Max win: {df['return'].max():+.1f}%")
    print(f"  Max loss: {df['return'].min():+.1f}%")
    print(f"  Unique stocks: {df['sym'].nunique()}")
    print(f"  Avg hold days: {df['hold_days'].mean():.0f}")
    print()

    print(f"  {'Sym':<10} {'Trades':>6} {'AvgRet':>8} {'Win%':>6}")
    print(f"  {'-'*10} {'-'*6} {'-'*8} {'-'*6}")
    for sym, grp in df.groupby("sym"):
        w = (grp["return"] > 0).sum()
        print(f"  {sym:<10} {len(grp):>6} {grp['return'].mean():>+7.1f}% {w/len(grp)*100:>5.0f}%")

    print()
    print("=== Recent trades ===")
    for _, t in df.sort_values("entry_date", ascending=False).head(15).iterrows():
        tag = "+" if t["return"] > 0 else "-"
        print(f"  {tag} {t['sym']}  {t['entry_date']} B @{t['entry_price']:.2f} -> {t['exit_date']} S @{t['exit_price']:.2f}  "
              f"Ret={t['return']:+.1f}%  Hold={t['hold_days']}d  Drop={t['drop_pct']:.0f}%  VR={t['vol_ratio']:.2f}")
