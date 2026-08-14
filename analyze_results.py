#!/usr/bin/env python
"""分析全市场因子回测结果，找出有用因子。"""
import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

import pandas as pd

pd.set_option('display.width', 220)
pd.set_option('display.max_columns', 20)

res = pd.read_csv(sys.argv[1] if len(sys.argv) > 1 else 'factor_backtest_results.csv', dtype={'symbol': str})
print(f'总记录: {len(res)} 行, {res["symbol"].nunique()} 只股票, {res["strategy"].nunique()} 个策略\n')

# ===== 汇总 =====
summary = []
for name, sub in res.groupby('strategy'):
    trades_total = int(sub['trades'].sum())
    summary.append({
        '策略': name,
        '股票数': len(sub),
        '平均收益%': round(sub['total_return'].mean(), 2),
        '中位收益%': round(sub['total_return'].median(), 2),
        '正收益占比%': round((sub['total_return'] > 0).mean() * 100, 1),
        '平均胜率%': round(sub['win_rate'].mean(), 1),
        '平均次数': round(sub['trades'].mean(), 1),
        '总次数': trades_total,
        '平均回撤%': round(sub['max_drawdown'].mean(), 2),
        '平均年化%': round(sub['annual'].mean(), 2),
    })
summ = pd.DataFrame(summary).sort_values('平均收益%', ascending=False)

print('================ 全市场因子回测汇总（按平均收益排序） ================')
print(summ.to_string(index=False))

# ===== 有用因子判定 =====
# 条件：平均收益 > 0、正收益占比 > 50%、平均次数 >= 5、平均回撤 < 60%
useful = summ[
    (summ['平均收益%'] > 0) &
    (summ['正收益占比%'] > 50) &
    (summ['平均次数'] >= 5) &
    (summ['平均回撤%'] < 60)
].sort_values('平均收益%', ascending=False)

print('\n================ 判定为"有用因子"（收益>0 且 正收益占比>50% 且 次数>=5 且 回撤<60%） ================')
if useful.empty:
    print('（无满足全部条件的因子，展示宽松筛选）')
    useful = summ[(summ['平均收益%'] > 0) & (summ['正收益占比%'] >= 50)].sort_values('平均收益%', ascending=False)
print(useful.to_string(index=False))

# ===== 最佳因子详表（每只股票的明细抽样） =====
if not useful.empty:
    best = useful.iloc[0]['策略']
    print(f'\n===== 最佳因子「{best}」逐股明细（前 15 只） =====')
    best_df = res[res['strategy'] == best].sort_values('total_return', ascending=False)
    print(best_df[['symbol', 'total_return', 'annual', 'win_rate', 'trades', 'max_drawdown']]
          .head(15).to_string(index=False))
    print(f'\n「{best}」交易最活跃股票 top5:')
    print(best_df.nlargest(5, 'trades')[['symbol', 'trades', 'win_rate', 'total_return']].to_string(index=False))

# ===== 与基准对比 =====
print('\n================ 基准对比（买入持有） ================')
bh = res[res['strategy'] == 'ma60_trend']  # 占位
buyhold = []
for sym in res['symbol'].unique():
    df = pd.read_csv(f'C:/Users/ASUS/qtrade/data/cache/{sym}.csv', index_col=0, parse_dates=True)
    if len(df) < 2:
        continue
    ret = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
    buyhold.append({'symbol': sym, 'bh_return': ret})
bh_df = pd.DataFrame(buyhold)
print(f'买入持有平均收益: {bh_df["bh_return"].mean():.2f}%  中位: {bh_df["bh_return"].median():.2f}%')
print(f'正收益占比: {(bh_df["bh_return"] > 0).mean()*100:.1f}%')
if not useful.empty:
    best = useful.iloc[0]['策略']
    b = res[res['strategy'] == best]
    print(f'对比: 最佳因子「{best}」平均收益 {b["total_return"].mean():.2f}% vs 买入持有 {bh_df["bh_return"].mean():.2f}%')
