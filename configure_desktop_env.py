"""向桌面版 .env 追加 DSA 关键配置（不覆盖已有项）。"""
import io
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

path = Path.home() / "AppData" / "Roaming" / "daily-stock-analysis-desktop" / ".env"
content = path.read_text(encoding="utf-8")

# 需要确保的配置（key -> value）
# 密钥从环境变量读取，避免硬编码进仓库；未设置则不写入
import os

CONFIG = {}
if os.environ.get("DEEPSEEK_API_KEY"):
    CONFIG["DEEPSEEK_API_KEY"] = os.environ["DEEPSEEK_API_KEY"]
if os.environ.get("PUSHPLUS_TOKEN"):
    CONFIG["PUSHPLUS_TOKEN"] = os.environ["PUSHPLUS_TOKEN"]
CONFIG["NOTIFICATION_REPORT_CHANNELS"] = "pushplus"
CONFIG["GENERATION_BACKEND"] = "litellm"

import re

added, updated, skipped = [], [], []
for key, val in CONFIG.items():
    pat = re.compile(rf"^{re.escape(key)}=(.*)$", re.MULTILINE)
    m = pat.search(content)
    if m:
        cur = m.group(1).strip()
        if cur and not cur.startswith("#"):
            skipped.append(f"{key}（已存在={cur}）")
        else:
            content = pat.sub(f"{key}={val}", content)
            updated.append(key)
    else:
        content += f"\n{key}={val}"
        added.append(key)

path.write_text(content, encoding="utf-8")
print(f"新增: {added}")
print(f"更新: {updated}")
print(f"跳过(已有值): {skipped}")

# 校验
check = path.read_text(encoding="utf-8")
for key in CONFIG:
    m = re.search(rf"^{re.escape(key)}=(.+)$", check, re.MULTILINE)
    print(f"  校验 {key} = {'OK' if m and m.group(1).strip() else 'MISSING'}")
