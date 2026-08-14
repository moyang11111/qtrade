"""调试：找出回撤异常的原因。"""
import sys
sys.path.insert(0, '.')
import pandas as pd
import numpy as np
from research_backtest import STRATEGIES, simulate, load_data

data = load_data('C:/Users/ASUS/qtrade/data/cache')
# 用全数据找 RSI 超卖回归回撤最大的
worst = None
for code, df in list(data.items())[:400]:
    sig = STRATEGIES['rsi_reversion'][1](df)
    if (sig == 1).sum() == 0:
        continue
    ret, trades, wr, mdd, ann = simulate(df, sig)
    if mdd > 200:
        worst = (code, mdd, ret, trades)
        print('异常:', worst)
        # 检查该股票价格范围
        print('  价格 min/max:', df['close'].min(), df['close'].max())
        print('  行数:', len(df))
        break

if worst is None:
    print('前400只无异常，扩大搜索...')
