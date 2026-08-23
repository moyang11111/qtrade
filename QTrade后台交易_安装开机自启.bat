@echo off
chcp 65001 >nul
title QTrade Auto-Trading - Enable autostart

set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SRC=%~dp0QTrade后台交易_静默启动.vbs"

if not exist "%SRC%" (
    echo [ERROR] File not found: %SRC%
    pause
    exit /b 1
)

copy /y "%SRC%" "%STARTUP%" >nul
if %errorlevel% neq 0 (
    echo [ERROR] Copy failed. Try running as administrator.
    pause
    exit /b 1
)

echo ============================================
echo   Autostart enabled.
echo ============================================
echo The trading service will start silently on boot.
echo Location: %STARTUP%\QTrade后台交易_静默启动.vbs
echo To disable, run the uninstall script.
pause
