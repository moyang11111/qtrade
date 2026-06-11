@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo =======================================
echo   QTrade Paper Trading
echo   策略: PullbackDeepSignal
echo   止盈10%% / 止损5%%
echo =======================================
echo.
python scripts\paper_trading.py --all %*
pause
