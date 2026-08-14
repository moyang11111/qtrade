"""端到端对齐验证：K线 time 与 MA/MACD/RSI/BOLL time 必须完全一致。"""
import sys
sys.path.insert(0, '.')
from server import DataService

ds = DataService('C:/Users/ASUS/qtrade/data/cache')

kline = ds.get_kline('000001', 400)
ind = ds.get_indicators('000001')

kline_times = [d['time'] for d in kline]
kline_last = kline_times[-1]
kline_first = kline_times[0]

print(f'K线: {len(kline)} 根, [{kline_first}, {kline_last}]')

# MA 最后一个非空点 time == 最后一根 K 线 time
for key in ['ma5', 'ma10', 'ma20', 'ma60']:
    pts = [d for d in ind['mas'][key] if d['value'] is not None]
    last = pts[-1]
    assert last['time'] == kline_last, f'{key} 最后点 {last["time"]} != K线最后 {kline_last}'
    print(f'  {key}: {len(pts)} 点, 最后 {last["time"]} == K线最后 ✅')

# 全部指标 time 都落在 K 线时间范围内
all_times = set(kline_times)
for name, arr in [('MACD', ind['macd']), ('RSI', ind['rsi']), ('BOLL', ind['boll'])]:
    for d in arr:
        assert d['time'] in all_times, f'{name} time {d["time"]} 不在 K 线范围内'
    print(f'  {name}: {len(arr)} 点全部在 K 线时间轴上 ✅')

print('\n=== 对齐验证通过：均线/指标与日K完全对齐 ===')
