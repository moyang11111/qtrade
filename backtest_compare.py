# -*- coding: utf-8 -*-
"""Quan market strategy comparison backtest"""
import sys, numpy as np
sys.path.insert(0, r'C:\Users\ASUS\AppData\Roaming\reasonix\global-workspace\qtrade_desktop')
from server import DataService, find_data_dir
from pathlib import Path

data_dir = find_data_dir(None)
svc = DataService(data_dir, live=False)
symbols = svc.scan()

strategies = ["rsi_layered", "reversal_combo", "reversal_20"]
results = {s: [] for s in strategies}

valid = []
for s in symbols:
    p = Path(data_dir) / f"{s}.csv"
    if p.exists():
        try:
            import pandas as pd
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            if len(df) >= 400:
                valid.append(s)
        except Exception:
            pass
print(f"Testing {len(valid)} stocks with 400+ bars")

for i, s in enumerate(valid):
    if i % 500 == 0 and i > 0:
        print(f"  ... {i}/{len(valid)}")
    for st in strategies:
        try:
            r = svc.run_backtest(s, st, 100000, 0.0003, 0.05, 0.15)
            if "error" not in r and "max_drawdown" in r:
                results[st].append(r)
        except Exception:
            pass

print("\n" + "=" * 70)
print("Full market strategy comparison (3547 stocks)")
print("=" * 70)

for st in strategies:
    rs = results[st]
    if not rs:
        print(f"\n[{st}]  NO RESULTS")
        continue
    rets = [r["total_return"] for r in rs]
    dds = [r["max_drawdown"] for r in rs]
    n = len(rets)
    pos = sum(1 for v in rets if v > 0)
    print(f"\n[{st}]  {n} stocks")
    print(f"  avg total return: {np.mean(rets):+.2f}% | median: {np.median(rets):+.2f}%")
    print(f"  avg max drawdown: {np.mean(dds):.2f}%")
    print(f"  profitable: {pos/n*100:.1f}%")
    print(f"  avg annual: {np.mean([r['annual_return'] for r in rs]):+.2f}%")
    print(f"  avg trades: {np.mean([r['total_trades'] for r in rs]):.0f}")

print("\n" + "=" * 70)
print("Quantile comparison (total return %)")
print("=" * 70)
print(f"{'strategy':<20}{'10%':>8}{'25%':>8}{'50%':>8}{'75%':>8}{'90%':>8}")
for st in strategies:
    rets = sorted([r["total_return"] for r in results[st]])
    if not rets:
        continue
    q = [np.percentile(rets, p) for p in [10, 25, 50, 75, 90]]
    print(f"{st:<20}{q[0]:>+8.1f}{q[1]:>+8.1f}{q[2]:>+8.1f}{q[3]:>+8.1f}{q[4]:>+8.1f}")

# Save
import json
out = {}
for st in strategies:
    out[st] = results[st]
with open(r'C:\Users\ASUS\AppData\Roaming\reasonix\global-workspace\qtrade_desktop\strategy_compare.json', 'w', encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, default=str)
print("\nSaved to strategy_compare.json")
