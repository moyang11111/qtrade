"""CSV 模式后端测试：DataService(live=False) 全功能。"""
import sys
sys.path.insert(0, '.')

from server import DataService, macd, rsi, bollinger, sma

ds = DataService('C:/Users/ASUS/qtrade/data/cache', live=False)

# 1. 扫描
symbols = ds.scan()
print(f'[1] 扫描: {len(symbols)} symbols')
assert len(symbols) > 1000

# 2. K线
kline = ds.get_kline('000001', 10)
print(f'[2] K线: {len(kline)} rows, last={kline[-1]["close"]}')
assert len(kline) == 10

# 3. 行情
info = ds.get_info('000001')
print(f'[3] 行情: latest={info["latest"]}, change={info["change_pct"]}%')
assert info['symbol'] == '000001'

# 4. 指标
ind = ds.get_indicators('000001')
print(f'[4] 指标: MA keys={list(ind["mas"].keys())}, MACD={len(ind["macd"])}, RSI={len(ind["rsi"])}, BOLL={len(ind["boll"])}')
assert all(k in ind['mas'] for k in ['ma5', 'ma10', 'ma20', 'ma60'])
assert len(ind['macd']) == len(ind['rsi']) == len(ind['boll'])
ma0 = ind['mas']['ma5'][0]
assert 'time' in ma0 and 'value' in ma0, f'MA 必须带 time/value: {ma0}'
assert ind['mas']['ma5'][0]['time'] == ind['macd'][0]['time'], 'MA 与 MACD 时间轴起点必须一致'
assert ds.get_indicators('000001') is ind

# 5. 指标函数正确性
df = ds._resolve_df('000001')
close = df['close']
m, s, h = macd(close)
r = rsi(close)
u, mid, l = bollinger(close)
assert not m.isna().all() and not r.isna().all()
assert (r.dropna().between(0, 100)).all()
print(f'[5] 指标函数: MACD={m.iloc[-1]:.4f}, RSI={r.iloc[-1]:.1f}')

# 6. 回测
for strat in ['dual_ma', 'trend_5d', 'bollinger', 'bb_rsi', 'pullback_20d', 'pullback_deep', 'breakout']:
    bt = ds.run_backtest('000001', strat, 100000, 0.0003, 0.05, 0.15)
    assert 'error' not in bt, f'{strat}: {bt}'
    assert 'equity' in bt and len(bt['equity']) == len(df)
    print(f'[6] 回测 {strat}: return={bt["total_return"]}%, trades={bt["total_trades"]}')

# 7. 错误处理
missing = ds.run_backtest('999999', 'dual_ma', 100000, 0.0003, 0.05, 0.15)
assert 'error' in missing
print(f'[7] 错误处理: {missing}')

print('\n=== CSV 模式全部测试通过 ===')
