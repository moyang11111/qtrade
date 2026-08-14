"""简易 JS 语法检查：括号/引号/模板字符串配平。"""
import sys
import io
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

def check_js(path):
    src = Path(path).read_text(encoding='utf-8')
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    i = 0
    n = len(src)
    line = 1
    issues = []

    # 跳过注释和字符串的简单状态机
    state = 'code'  # code | line_comment | block_comment | str_s | str_d | template

    while i < n:
        c = src[i]
        if c == '\n':
            line += 1

        if state == 'code':
            if c == '/' and i + 1 < n and src[i+1] == '/':
                state = 'line_comment'; i += 2; continue
            if c == '/' and i + 1 < n and src[i+1] == '*':
                state = 'block_comment'; i += 2; continue
            if c == "'":
                state = 'str_s'; i += 1; continue
            if c == '"':
                state = 'str_d'; i += 1; continue
            if c == '`':
                state = 'template'; i += 1; continue
            if c in '([{':
                stack.append((c, line))
            elif c in ')]}':
                if not stack:
                    issues.append(f'L{line}: 多余的 {c}')
                else:
                    top, tl = stack.pop()
                    if top != pairs[c]:
                        issues.append(f'L{line}: {top}(L{tl}) 与 {c} 不匹配')
            i += 1

        elif state == 'line_comment':
            if c == '\n':
                state = 'code'
            i += 1

        elif state == 'block_comment':
            if c == '*' and i + 1 < n and src[i+1] == '/':
                state = 'code'; i += 2
            else:
                i += 1

        elif state == 'str_s':
            if c == '\\':
                i += 2
            elif c == "'":
                state = 'code'; i += 1
            else:
                i += 1

        elif state == 'str_d':
            if c == '\\':
                i += 2
            elif c == '"':
                state = 'code'; i += 1
            else:
                i += 1

        elif state == 'template':
            if c == '\\':
                i += 2
            elif c == '`':
                state = 'code'; i += 1
            else:
                i += 1

    if stack:
        for sym, ln in stack:
            issues.append(f'L{ln}: 未闭合的 {sym}')
    if state in ('str_s', 'str_d', 'template', 'block_comment'):
        issues.append(f'未闭合的 {state}')

    return issues

if __name__ == '__main__':
    files = sys.argv[1:] or ['static/js/api.js', 'static/js/chart.js', 'static/js/app.js']
    ok = True
    for f in files:
        issues = check_js(f)
        if issues:
            ok = False
            print(f'✗ {f}:')
            for msg in issues:
                print(f'    {msg}')
        else:
            print(f'✓ {f}: 括号/引号配平 OK')
    sys.exit(0 if ok else 1)
