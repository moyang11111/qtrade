@echo off
chcp 65001 >nul
title QTrade Desktop — A股量化交易终端
cd /d "%~dp0"

echo ============================================
echo   QTrade Desktop — A股量化交易终端
echo ============================================
echo.

REM ---- 1. 检查 Python ----
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [错误] 未找到 python，请先安装 Python 3.10+
    pause
    exit /b 1
)
echo [OK] Python: 
python --version

REM ---- 2. 确定数据目录 ----
set DATA_DIR=C:\Users\ASUS\qtrade\data\cache
if not exist "%DATA_DIR%" (
    echo [警告] 数据目录不存在: %DATA_DIR%
    set DATA_DIR=data\cache
)

REM ---- 3. 检查 8765 端口是否被占用 ----
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>nul
if %errorlevel% equ 0 (
    echo [警告] 端口 8765 已被占用！尝试换用 9000 端口...
    echo 访问地址: http://127.0.0.1:9000
    echo.
    python server.py --data-dir "%DATA_DIR%" --port 9000
) else (
    echo [OK] 端口 8765 可用，访问地址: http://127.0.0.1:8765
    echo.
    echo 提示: 如果浏览器未自动打开，请手动访问 http://127.0.0.1:8765
    echo 按 Ctrl+C 停止服务
    echo.
    python server.py --data-dir "%DATA_DIR%" --port 8765
)

echo.
echo [信息] 服务已停止。
pause
