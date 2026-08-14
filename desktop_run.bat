@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ================================
echo   QTrade Desktop - PySide6 版
echo ================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

REM 检查 PySide6
python -c "import PySide6" >nul 2>&1
if %errorlevel% neq 0 (
    echo [提示] PySide6 未安装，正在安装...
    pip install PySide6 mplfinance -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    if %errorlevel% neq 0 (
        echo [错误] PySide6 安装失败
        pause
        exit /b 1
    )
)

echo 启动 QTrade Desktop...
python desktop_app.py %*
pause
