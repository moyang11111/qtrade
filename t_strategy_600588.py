#!/usr/bin/env python
"""600588 用友网络 —— 做 T 策略回测（日线近似）。

说明：
- 真实做 T 需分钟数据判断日内高低点；这里用日线做近似分析。
- 三种"可实现"T 策略（信号日收盘判断，次日可执行）+ 一种"理论上限"对照。
- 成本：佣金 0.025%*2 + 卖出印花税 0.05% + 滑点 0.1%*2 ≈ 每笔完整 T 约 0.35%。
"""
import sys
import io
import glob
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

import pandas as pd
import numpy as np
import urllib.request
import json

COST = 0.0035  # 完整 T（一买一卖）成本

def market_prefix(code):
    return ('sh' if code.startswith(('6', '9')) else 'sz') + code

def fetch_latest(code):
    """腾讯实时日K，补齐最新数据。"""
    sym = market_prefix(code)
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,10,qfq'
    try:
        r = urllib.request.urlopen(url, timeout=8)
        d = json.loads(r.read().decode('utf-8'))
        node = d.get('data', {}).get(sym, {})
        kl = node.get('qfqday') or node.get('day') or []
        return pd.DataFrame([row[:6] for row in kl], columns=['date','open','close','high','low','volume'])
    except Exception:
        return None

# ---------- 数据 ----------
df = pd.read_csv('C:/Users/ASUS/qtrade/data/cache/600588.csv', index_col=0, parse_dates=True)
df.columns = [c.lower() for c in df.columns]
# 合并腾讯最新
new = fetch_latest('600588')
if new is not None:
    new['date'] = pd.to_datetime(new['date'])
    new = new.set_index('date')
    df = pd.concat([df, new[~new.index.isin(df.index)]])
df = df.sort_index()
for col in ['open', 'high', 'low', 'close', 'volume']:
    df[col] = pd.to_numeric(df[col], errors='coerce')
df = df.dropna(subset=['close'])
print(f"600588 数据: {len(df)} 行, {df.index[0].date()} -> {df.index[-1].date()}, 最新收盘 {df['close'].iloc[-1]:.2f}")

c = df['close']; o = df['open']; h = df['high']; l = df['low']; v = df['volume']
r = c.pct_change()

def rsi(close, n=6):
    d = close.diff()
    g = d.clip(lower=0); lo = (-d).clip(lower=0)
    ag = g.ewm(alpha=1/n, adjust=False).mean()
    al = lo.ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100/(1 + ag/al.replace(0, np.nan))

# ============ 通用模拟：信号(1买/-1卖) + 底仓模式 ============
def sim_t(sig, df, max_hold=5, label=''):
    """做T模拟：信号日收盘买入，次日开始持有，max_hold日内若未触发卖出信号则第max_hold日收盘卖。
    返回 (次数, 胜率%, 累计收益%, 平均每T%, 最长持有)"""
    close = df['close'].values
    n = len(df)
    trades = 0; wins = 0; cum = 1.0; per = []
    i = 0
    while i < n - 1:
        if sig.iloc[i] == 1:  # 买入
            buy = close[i]
            sell_idx = None
            for j in range(i + 1, min(i + 1 + max_hold, n)):
                if sig.iloc[j] == -1:
                    sell_idx = j; break
            if sell_idx is None:
                sell_idx = min(i + max_hold, n - 1)
            sell = close[sell_idx]
            ret = sell / buy - 1 - COST
            cum *= (1 + ret)
            trades += 1
            if ret > 0: wins += 1
            per.append(ret)
            i = sell_idx + 1
        else:
            i += 1
    if trades == 0:
        return 0, 0, 0, 0
    return trades, wins/trades*100, (cum-1)*100, np.mean(per)*100

results = {}

# ---- 策略1: RSI(6) 正T 低吸高抛 ----
r6 = rsi(c, 6)
sig1 = pd.Series(0, index=df.index)
sig1[r6 < 25] = 1
sig1[r6 > 75] = -1
results['RSI6 正T(25买/75卖)'] = sim_t(sig1, df)

# ---- 策略2: 回踩MA20 缩量低吸，反弹MA5卖 ----
ma5 = c.rolling(5).mean(); ma20 = c.rolling(20).mean()
v5 = v.rolling(5).mean()
sig2 = pd.Series(0, index=df.index)
sig2[(c < ma20) & (v < v5*0.8)] = 1   # 缩量回踩
sig2[c > ma5] = -1                     # 反弹上MA5
results['回踩MA20正T'] = sim_t(sig2, df)

# ---- 策略3: 反T 冲高卖、回落买回 ----
h20 = h.rolling(20).max()
sig3 = pd.Series(0, index=df.index)
sig3[c >= h20.shift(1)] = -1           # 创20日新高卖底仓
sig3[c < ma20] = 1                     # 回落MA20买回
results['反T(新高卖/回MA20买)'] = sim_t(sig3, df, max_hold=10)

# ---- 策略4: 振幅大时做T（正T） ----
amp = (h - l) / o
sig4 = pd.Series(0, index=df.index)
sig4[amp > 0.05] = 1                    # 大振幅日买
sig4[amp > 0.06] = -1                   # 更大振幅卖（冲高）
results['大振幅日正T(>5%振幅)'] = sim_t(sig4, df, max_hold=3)

# ---- 策略5: 理论上限（日内低点买/高点卖，不可实现，参考） ----
def sim_theoretical(df, amp_th=0.04):
    sel = df[(df['high']-df['low'])/df['open'] > amp_th]
    per = []
    for _, row in sel.iterrows():
        ret = row['high']/row['low'] - 1 - COST
        per.append(ret)
    if not per:
        return 0, 0, 0, 0
    return len(per), sum(1 for x in per if x>0)/len(per)*100, (np.prod([1+x for x in per])-1)*100, np.mean(per)*100
results['理论上限(>4%振幅日低买高卖)'] = sim_theoretical(df)

print(f"\n{'策略':<28}{'次数':>6}{'胜率%':>8}{'累计收益%':>10}{'每T均%':>9}")
print('-'*66)
for k, (tn, wr, cumr, per) in results.items():
    print(f"{k:<28}{tn:>6}{wr:>8.1f}{cumr:>10.2f}{per:>9.2f}")

# ---- 额外：统计适合做T的日子 ----
amp = (h - l) / o
print(f"\n做T空间分析（近1年）:")
a = amp.tail(250)
print(f"  振幅>3% 的天数占比: {(a>0.03).mean()*100:.0f}%  (T空间约 {a[a>0.03].median()*100:.1f}%)")
print(f"  振幅>5% 的天数占比: {(a>0.05).mean()*100:.0f}%")
print(f"  振幅>7% 的天数占比: {(a>0.07).mean()*100:.0f}%")
print(f"\n结论: 日振幅中位 {amp.median()*100:.1f}% > 双边成本 0.35%，理论上有做T空间")
