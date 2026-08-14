"""实时模式测试：腾讯接口拉取 + 内存缓存 + CSV 回退。"""
import sys
import time
sys.path.insert(0, '.')

from server import DataService, TencentLiveSource, market_prefix

# 1. 数据源可用性
print('[1] 腾讯接口可用:', TencentLiveSource.available())

# 2. 实时日 K
ds = DataService('C:/Users/ASUS/qtrade/data/cache', live=True)
df = ds._resolve_df('000001')
assert df is not None and len(df) > 200, f'000001 实时K线异常: {len(df) if df is not None else "None"}'
print(f'[2] 实时K线 000001: {len(df)} 根, 最新 {df.index[-1].date()} 收 {df["close"].iloc[-1]:.2f}')

# 3. 内存缓存验证：第二次拉取不产生新请求
t0 = time.time()
df2 = ds._resolve_df('000001')
dt1 = time.time() - t0
t0 = time.time()
ds.live_src.fetch_kline('000001')
dt2 = time.time() - t0
print(f'[3] 缓存: 二次拉取 {dt1*1000:.0f}ms / 强制拉取 {dt2*1000:.0f}ms')
assert dt1 < 0.05, '应在缓存命中（<50ms）'

# 4. 实时快照
q = ds.live_src.fetch_quote('000001')
print(f'[4] 快照 000001: {q["name"]} 最新 {q["price"]} 涨跌 {q["change_pct"]}% 换手 {q["turnover"]}%')

# 5. get_info 合并
info = ds.get_info('000001')
print(f'[5] get_info: name={info.get("name")}, latest={info.get("latest")}, high60={info.get("high_60d")}')

# 6. 指标
ind = ds.get_indicators('000001')
assert len(ind['mas']['ma5']) > 200
print(f'[6] 指标: MA5 {len(ind["mas"]["ma5"])} 点, RSI {len(ind["rsi"])} 点')

# 7. K线对齐
k = ds.get_kline('000001', 400)
assert k[-1]['time'] == ind['mas']['ma5'][-1]['time']
print(f'[7] 对齐: K线最后 time == MA5 最后 time ✅')

# 8. CSV 回退（北交所，腾讯不支持）
df_bj = ds._resolve_df('832000')
print(f'[8] 北交所 832000 回退: {"CSV加载" if df_bj is not None else "无数据"}')

# 9. 回测（实时数据）
bt = ds.run_backtest('000001', 'dual_ma', 100000, 0.0003, 0.05, 0.15)
assert 'error' not in bt
print(f'[9] 实时数据回测: return={bt["total_return"]}%, trades={bt["total_trades"]}')

# 10. 股票池
symbols = ds.scan()
print(f'[10] 股票池: {len(symbols)} 只 (含内置 {len([s for s in symbols if s in __import__("server").COMMON_STOCKS])} 只常用)')

# 11. 内存缓存确认（不落盘）
import os
assert not hasattr(ds, 'storage'), '不应有磁盘存储'
print('[11] 无磁盘存储，仅内存缓存 ✅')

print('\n=== 实时模式全部测试通过 ===')
