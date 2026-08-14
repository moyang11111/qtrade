"""检查仓库 workflow 是否被 GitHub Actions 识别。"""
import json
import urllib.request

url = 'https://api.github.com/repos/moyang11111/daily_stock_analysis/actions/workflows'
r = urllib.request.urlopen(url, timeout=15)
d = json.loads(r.read())
wfs = d.get('workflows', [])
print('识别的 workflows 数:', len(wfs))
for w in wfs:
    print(f"  - {w['name']}")
    print(f"    path : {w['path']}")
    print(f"    state: {w['state']}")
    print(f"    id   : {w['id']}")
