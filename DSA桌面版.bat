@echo off
title DSA 股票分析桌面版
chcp 65001 >nul
cd /d C:\Users\ASUS\AppData\Roaming\reasonix\global-workspace\daily_stock_analysis\apps\dsa-desktop
echo ============================================
echo   DSA 股票智能分析系统 - 桌面版
echo ============================================
echo 正在启动后端服务（首次约需 30-40 秒）...
echo 启动后会自动弹出桌面窗口，此窗口可最小化。
echo 关闭此窗口 = 关闭 DSA 桌面版
echo.
npm run dev
echo.
echo 服务已停止。
pause
