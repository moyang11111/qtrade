@echo off
chcp 65001 >nul
title QTrade Auto-Trading Service (console)
cd /d "%~dp0"

echo ============================================
echo   QTrade Auto Paper Trading - background service
echo ============================================
echo Keep this console for logs. Auto-trading continues
echo after you close the QTrade window.
echo To stop auto-trading, run the STOP script.
echo.

python server.py --no-browser --port 8765 --single-instance
pause
