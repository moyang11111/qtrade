import os, sys, time, pandas as pd, numpy as np, urllib.request
from pathlib import Path

os.chdir(Path(__file__).parent)
sys.path.insert(0, 'src')
cache = Path('data/cache')

print("正在加载行情数据...")
main_codes = sorted([f.stem for f in cache.glob("*.csv") if f.stem.startswith(("60", "00"))])
print(f"主板股票: {len(main_codes)} 只")

print("正在获取实时价格...")
prices = {}
done = 0
for i in range(0, len(main_codes), 50):
    batch = main_codes[i:i+50]
    pf = ['sh' + c if c.startswith(('6', '9')) else 'sz' + c for c in batch]
    try:
        req = urllib.request.Request('https://qt.gtimg.cn/q=' + ','.join(pf), headers={'User-Agent': 'Mozilla/5.0'})
        for line in urllib.request.urlopen(req, timeout=8).read().decode('gbk').split(';'):
            if '=' in line and '"' in line:
                c2 = line.split('=')[0].split('_')[-1][2:]
                v2 = line.split('"')[1].split('~')
                if len(v2) > 33 and float(v2[3]) > 0:
                    prices[c2] = float(v2[3])
        done += len(batch)
        if done % 500 == 0:
            print(f"  {done}/{len(main_codes)}")
    except:
        pass
    time.sleep(0.05)

print(f"获取到 {len(prices)} 只股票价格")

print("正在计算指标并筛选...")
sigs = []
checked = 0
for code in main_codes:
    checked += 1
    if checked % 500 == 0:
        print(f"  {checked}/{len(main_codes)}")
    
    f = cache / f"{code}.csv"
    try:
        df = pd.read_csv(f, parse_dates=['date'], index_col='date')
        df.columns = [c.lower() for c in df.columns]
        d = df['2026-01-01':'2026-06-11']
        if len(d) < 80:
            continue
        c = d['close'].values
        v = d['volume'].values
        n = len(c)
        i = n - 1
        
        ma60 = float(pd.Series(c).rolling(60).mean().values[i])
        peak60 = float(pd.Series(c).rolling(60).max().values[i])
        v5_mean = v[max(0, i-4):i+1].mean()
        v20_mean = v[max(0, i-19):i+1].mean()
        v5 = v5_mean / v20_mean if v20_mean > 0 else 1
        
        if code not in prices:
            continue
        
        p = prices[code]
        drop = (peak60 - p) / peak60 * 100
        
        if p > ma60 and 15 <= drop <= 40 and v5 < 0.7:
            sigs.append(('FULL', code, p, ma60, peak60, drop, v5))
        elif p > ma60 and 10 <= drop <= 45 and v5 < 0.8:
            sigs.append(('NEAR', code, p, ma60, peak60, drop, v5))
    except:
        pass

sigs.sort(key=lambda x: (0 if x[0]=='FULL' else 1, -x[4]))

full = [s for s in sigs if s[0]=='FULL']
near = [s for s in sigs if s[0]=='NEAR']

print(f"\n{'='*55}")
print(f"  完全符合: {len(full)} 只 | 接近: {len(near)} 只")
print(f"{'='*55}")

if full:
    print("\n  === 完全符合 ===")
    for s in full[:15]:
        print(f"  {s[1]}  现价{s[2]:.2f}  回撤{s[5]:.1f}%  量比{s[6]:.2f}")

if near:
    print(f"\n  === 接近（回调10-15%或量比0.7-0.8）===")
    for s in near[:15]:
        issues = []
        if not (15 <= s[5] <= 40): issues.append(f"回撤{s[5]:.1f}%")
        if s[6] >= 0.7: issues.append(f"量比{s[6]:.2f}")
        print(f"  {s[1]}  现价{s[2]:.2f}  回撤{s[5]:.1f}%  量比{s[6]:.2f}  [{', '.join(issues)}]")
print()
print("完成。按回车退出...")
input()
