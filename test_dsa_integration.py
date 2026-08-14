"""测试 DSA 集成：信号读取 + AI 模拟盘。"""
import io
import sys
sys.path.insert(0, '.')
import server  # 触发 server.py 的 stdout 编码包装

from server import DsaSignalReader, AiPaperTrader, DataService

# 1. 信号读取
reader = DsaSignalReader()
print('[1] DSA 数据库可用:', reader.available())
assert reader.available(), '应找到 DSA 数据库'
views = reader.get_views(limit=10)
print(f'[2] 活跃信号: {len(views)} 条')
for v in views[:4]:
    print(f"    {v['symbol']} {v['name']} | {v['action_label']} score={v['score']} conf={v['confidence']} | {v['reason'][:40]}")

# 2. 单股查询
for sym in ['600588', '002261']:
    v = reader.latest_for(sym)
    print(f'[3] {sym} 最新观点: {v["action_label"] if v else "无"} score={v["score"] if v else "-"}')

# 3. AI 模拟盘
trader = AiPaperTrader('test_ai_paper.json')
price_fn = lambda s: {'600588': 11.85, '002261': 8.2}.get(s, 10.0)
status = trader.sync(views, price_fn)
print(f'[4] 模拟盘: 现金 {status["cash"]:.0f} 总资产 {status["total"]:.0f} 持仓 {len(status["positions"])} 只')
for p in status['positions']:
    print(f"    {p['symbol']} qty={p['qty']} 成本 {p['avg_cost']} 现价 {p['last_price']}")
print(f'[5] 交易记录: {len(status["trades"])} 笔')
for t in status['trades'][:3]:
    print(f"    {t['side']} {t['symbol']} @{t['price']} qty={t['qty']} {t.get('reason','')}")

import os
os.remove('test_ai_paper.json') if os.path.exists('test_ai_paper.json') else None
print('\n=== DSA 集成测试通过 ===')
