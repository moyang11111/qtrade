"""检查 DSA 数据库决策信号。"""
import io
import os
import sqlite3
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

db = r"C:\Users\ASUS\AppData\Roaming\daily-stock-analysis-desktop\data\stock_analysis.db"
print("数据库存在:", os.path.exists(db))
if os.path.exists(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = conn.cursor()
    tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print("相关表:", [t for t in tables if "signal" in t.lower() or "analysis" in t.lower() or "history" in t.lower()][:10])
    try:
        n = cur.execute("SELECT COUNT(*) FROM decision_signals").fetchone()[0]
        print("decision_signals 行数:", n)
        if n > 0:
            for row in cur.execute(
                "SELECT stock_code, action, score, confidence, horizon, status, created_at "
                "FROM decision_signals ORDER BY created_at DESC LIMIT 5"
            ):
                print("  ", row)
    except Exception as e:
        print("查询失败:", e)
    conn.close()
