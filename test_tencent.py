"""测试腾讯日K接口对不同市场代码的支持。"""
import json
import urllib.request


def market_prefix(code: str) -> str:
    if code.startswith(('6', '9')):
        return 'sh' + code
    if code.startswith(('0', '3')):
        return 'sz' + code
    if code.startswith(('4', '8')):
        return 'bj' + code
    return code


for code in ['000001', '600519', '300750', '688981', '832000']:
    sym = market_prefix(code)
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,5,qfq'
    try:
        r = urllib.request.urlopen(url, timeout=8)
        d = json.loads(r.read().decode('utf-8'))
        data = d.get('data', {}).get(sym, {})
        klines = data.get('qfqday') or data.get('day') or []
        last = klines[-1] if klines else []
        print(f'{code} -> {sym}: {len(klines)} bars, last_date={last[0] if last else "N/A"}')
    except Exception as e:
        print(f'{code} -> {sym}: FAIL {str(e)[:80]}')
