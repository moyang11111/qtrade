"""更新测试文件：DataService 显式 live=False（CSV 模式）。"""
import io
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", write_through=True)

OLD = "DataService('C:/Users/ASUS/qtrade/data/cache')"
NEW = "DataService('C:/Users/ASUS/qtrade/data/cache', live=False)"

for p in ['test_backend.py', 'test_http.py']:
    s = open(p, encoding='utf-8').read()
    n = s.count(OLD)
    s = s.replace(OLD, NEW)
    open(p, 'w', encoding='utf-8').write(s)
    print(f'{p}: 替换 {n} 处')
