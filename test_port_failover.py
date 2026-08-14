"""端口自动顺延完整测试：占用 8888 → server.py --port 8888 应自动改用 8889。"""
import io
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

# 1. 占用 8888
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('127.0.0.1', 8888))
sock.listen(1)
print('[1] 已占用 8888 端口')

# 2. 启动 server.py（输出到日志文件）
log = Path('failover_v4.log')
with open(log, 'w', encoding='utf-8') as f:
    proc = subprocess.Popen(
        [sys.executable, '-u', 'server.py',
         '--data-dir', 'C:/Users/ASUS/qtrade/data/cache',
         '--port', '8888', '--no-browser'],
        stdout=f, stderr=subprocess.STDOUT,
    )
    time.sleep(4)

    # 3. 轮询 8889（最多 10 秒）
    ok = False
    for _ in range(20):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8889/api/health', timeout=2) as r:
                data = r.read().decode('utf-8')
                assert '"status": "ok"' in data
                print(f'[2] 顺延成功: 8888 被占 → 8889 可访问 ({data})')
                ok = True
                break
        except Exception:
            time.sleep(0.5)

    proc.terminate()
    proc.wait(timeout=5)

sock.close()

# 4. 打印服务器输出
print('[server.py 输出]')
content = log.read_text(encoding='utf-8', errors='replace')
print(content if content.strip() else '（无输出）')
log.unlink(missing_ok=True)

print('=== 端口自动顺延:', '通过' if ok else '失败', '===')
sys.exit(0 if ok else 1)
