"""右侧趋势 + 热点题材 筛选器"""
import os, sys, time, pandas as pd, numpy as np, urllib.request, requests
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, 'src')
cache = Path('data/cache')

# 1. 获取热点
print("获取同花顺热点题材...")
reasons = {}
try:
    r = requests.get('http://zx.10jqka.com.cn/event/api/getharden/date/2026-06-11/orderby/date/orderway/desc/charset/GBK/',
                     headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    for item in r.json().get('data', [])[:200]:
        code = item.get('code', '')
        reason = item.get('reason', '')
        if code and reason:
            reasons[code] = f"{item.get('name','')}: {reason[:30]}"
    print(f"  热点股: {len(reasons)} 只")
except:
    print("  热点获取失败，仅用趋势")

# 2. 扫描右侧趋势
print("扫描右侧趋势(MA5>MA20>MA60 + 放量)...")
main_codes = sorted([f.stem for f in cache.glob("*.csv") if f.stem.startswith(("60", "00"))])
print(f"  主板: {len(main_codes)} 只")

results = []
for idx, code in enumerate(main_codes):
    if idx % 500 == 0:
        print(f"  {idx}/{len(main_codes)}")
    try:
        f = cache / f"{code}.csv"
        df = pd.read_csv(f, parse_dates=['date'], index_col='date')
        df.columns = [c.lower() for c in df.columns]
        d = df['2026-01-01':'2026-06-11']
        if len(d) < 80: continue
        c = d['close'].values; v = d['volume'].values; n = len(c); i = n - 1
        if i < 20: continue

        ma5 = float(pd.Series(c).rolling(5).mean().values[i])
        ma20 = float(pd.Series(c).rolling(20).mean().values[i])
        ma60_ = float(pd.Series(c).rolling(60).mean().values[i])
        ret5 = (c[i] / c[i - 5] - 1) * 100 if i >= 5 else 0
        ret20 = (c[i] / c[i - 20] - 1) * 100 if i >= 20 else 0
        vr = v[max(0, i - 4):i + 1].mean() / v[max(0, i - 19):i + 1].mean()

        # 宽条件: MA20>MA60 + 20日涨 + 放量
        if ma20 > ma60_ and ret20 > 3 and vr > 0.7:
            score = ret20 + vr * 5
            results.append((code, c[i], ma5, ma20, ma60_, ret5, ret20, vr, score))
            results.append((code, c[i], ret5, ret20, round(vr, 2)))
    except:
        pass

results.sort(key=lambda x: -x[-1])  # sort by score

hot = [r for r in results if r[0] in reasons]
trend = [r for r in results if r[0] not in reasons]

print(f"\n{'='*60}")
print(f"  右侧趋势 + 热点题材: {len(hot)} 只")
print(f"{'='*60}")
for r in hot:
    print(f"  {r[0]}  现价{r[1]:.2f}  5日{r[5]:+.1f}%  20日{r[6]:+.1f}%  量比{r[7]:.2f}")
    print(f"        题材: {reasons.get(r[0], '')}")

print(f"\n{'='*60}")
print(f"  纯右侧趋势: {len(trend)} 只 (前15)")
print(f"{'='*60}")
for r in trend[:15]:
    print(f"  {r[0]}  现价{r[1]:.2f}  5日{r[5]:+.1f}%  20日{r[6]:+.1f}%  量比{r[7]:.2f}")

print("\n完成。按回车退出...")
input()
