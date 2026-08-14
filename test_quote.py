"""解析腾讯实时快照字段。"""
import urllib.request

url = 'https://qt.gtimg.cn/q=sh600519'
r = urllib.request.urlopen(url, timeout=8)
txt = r.read().decode('gbk', errors='replace')
fields = txt.split('~')
print(f'总字段数: {len(fields)}')
interesting = {
    0: '市场', 1: '名称', 2: '代码', 3: '最新价', 4: '昨收', 5: '今开',
    6: '成交量(手)', 7: '外盘', 8: '内盘',
    30: '时间', 31: '涨跌额', 32: '涨跌幅', 33: '最高', 34: '最低',
    35: '价格/成交量/成交额', 36: '成交量(手)', 37: '成交额(万)', 38: '换手率',
    39: '市盈率', 43: '振幅', 44: '流通市值', 45: '总市值',
}
for i, name in interesting.items():
    if i < len(fields):
        print(f'[{i}] {name}: {fields[i]}')
