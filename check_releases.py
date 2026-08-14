"""查询 DSA 官方 Releases。"""
import json
import urllib.request

url = 'https://api.github.com/repos/moyang11111/daily_stock_analysis/releases?per_page=5'
r = urllib.request.urlopen(url, timeout=15)
rels = json.loads(r.read())
if not rels:
    print('暂无 Release 发布（桌面版需自己打包）')
for rel in rels:
    assets = [a['name'] for a in rel.get('assets', [])]
    print(f"tag: {rel['tag_name']} | 发布于: {rel['published_at']} | 资产数: {len(assets)}")
    for a in assets[:10]:
        print('   ', a)
    print()
