"""全市场 RSI 分层策略回测统计"""
import sys, time
sys.path.insert(0, r'C:\Users\ASUS\AppData\Roaming\reasonix\global-workspace\qtrade_desktop')
from server import DataService, find_data_dir

svc = DataService(find_data_dir(None), live=False)
symbols = svc.scan()
print(f"Total stocks: {len(symbols)}")

CAPITAL = 100000
COMMISSION = 0.0003
results = []
skipped = 0
start = time.time()

for i, sym in enumerate(symbols):
    if i % 500 == 0 and i > 0:
        elapsed = time.time() - start
        print(f"  ... {i}/{len(symbols)} ({elapsed:.0f}s elapsed)")
    try:
        r = svc.run_backtest(sym, "rsi_layered", CAPITAL, COMMISSION, 0.05, 0.15)
        if "error" in r:
            skipped += 1
            continue
        results.append({
            "symbol": sym,
            "total_return": r["total_return"],
            "annual": r["annual_return"],
            "max_dd": r["max_drawdown"],
            "win_rate": r["win_rate"],
            "trades": r["total_trades"],
            "final": r["final_value"],
        })
    except Exception:
        skipped += 1

elapsed = time.time() - start
print(f"\nDone in {elapsed:.0f}s: {len(results)} analyzed, {skipped} skipped")

if not results:
    print("NO RESULTS")
    sys.exit(1)

import statistics
rets = [r["total_return"] for r in results]
annuals = [r["annual"] for r in results]
dds = [r["max_dd"] for r in results]
wins = [r["win_rate"] for r in results]

n = len(rets)
positive = sum(1 for v in rets if v > 0)
negative = sum(1 for v in rets if v < 0)
flat = sum(1 for v in rets if v == 0)

print(f"\n{'='*60}")
print(f"RSI 分层策略 - 全市场回测统计 ({n} 只) 初始资金 {CAPITAL}")
print(f"{'='*60}")
print(f"总收益均值:     {statistics.mean(rets):+.2f}%")
print(f"总收益中位数:   {statistics.median(rets):+.2f}%")
print(f"年化均值:       {statistics.mean(annuals):+.2f}%")
print(f"年化中位数:     {statistics.median(annuals):+.2f}%")
print(f"最大回撤均值:   {statistics.mean(dds):.2f}%")
print(f"胜率均值:       {statistics.mean(wins):.1f}%")
print(f"平均交易次数:   {statistics.mean([r['trades'] for r in results]):.0f}")
print(f"\n盈亏分布: 盈利 {positive} ({positive/n*100:.1f}%) | "
      f"亏损 {negative} ({negative/n*100:.1f}%) | 持平 {flat}")
print(f"最好 5 只:")
for r in sorted(results, key=lambda x: -x["total_return"])[:5]:
    print(f"  {r['symbol']}: {r['total_return']:+.2f}% (年化 {r['annual']:+.2f}%, 回撤 {r['max_dd']:.2f}%)")
print(f"最差 5 只:")
for r in sorted(results, key=lambda x: x["total_return"])[:5]:
    print(f"  {r['symbol']}: {r['total_return']:+.2f}% (年化 {r['annual']:+.2f}%, 回撤 {r['max_dd']:.2f}%)")

# 保存结果
import json
with open(r'C:\Users\ASUS\AppData\Roaming\reasonix\global-workspace\qtrade_desktop\rsi_layered_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(f"\n结果已保存到 rsi_layered_results.json")
