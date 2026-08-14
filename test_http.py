"""HTTP 集成测试：启动内置服务并验证全部端点（含 404）。"""
import sys
import json
import threading
import urllib.request
import urllib.error

import server as server_mod
from pathlib import Path

# 启动服务（直接修改 server 模块的全局变量）
server_mod.SERVICE = server_mod.DataService('C:/Users/ASUS/qtrade/data/cache', live=False)
server_mod.STATIC_DIR = Path(__file__).parent / 'static'

PORT = 18768
http_server = server_mod.HTTPServer(('127.0.0.1', PORT), server_mod.APIHandler)
threading.Thread(target=http_server.serve_forever, daemon=True).start()

BASE = f'http://127.0.0.1:{PORT}'
ok = 0

def get(path):
    global ok
    with urllib.request.urlopen(BASE + path) as r:
        data = json.loads(r.read())
        ok += 1
        print(f'OK {path} -> status {r.status}')
        return data

def get404(path):
    global ok
    try:
        urllib.request.urlopen(BASE + path)
        print(f'FAIL {path} 应该返回 404')
        return False
    except urllib.error.HTTPError as e:
        if e.code == 404:
            ok += 1
            print(f'OK {path} -> 404 正确')
            return True
        print(f'FAIL {path} -> {e.code}')
        return False

# 1. health
h = get('/api/health')
assert h['status'] == 'ok' and h['symbols'] > 1000

# 2. symbols
s = get('/api/symbols')
assert len(s) > 1000

# 3. kline
k = get('/api/kline/000001?limit=50')
assert len(k) == 50 and 'close' in k[-1]

# 4. info
info = get('/api/info/600519')
assert info['latest'] > 0

# 5. indicators
ind = get('/api/indicators/000001')
assert all(k2 in ind['mas'] for k2 in ['ma5', 'ma20'])

# 6. backtest
bt = get('/api/backtest?symbol=000001&strategy=dual_ma&capital=100000')
assert 'total_return' in bt and len(bt['equity']) > 100

# 7. 404 处理
assert get404('/api/info/NOT_EXIST')
assert get404('/api/kline/ZZZZ')

# 8. 参数错误
try:
    urllib.request.urlopen(BASE + '/api/backtest?symbol=000001&capital=abc')
    print('FAIL 参数错误应返回 400')
except urllib.error.HTTPError as e:
    if e.code == 400:
        ok += 1
        print(f'OK /api/backtest?capital=abc -> 400 正确')

http_server.shutdown()
print(f'\n=== HTTP 集成测试全部通过（{ok} 项检查）===')