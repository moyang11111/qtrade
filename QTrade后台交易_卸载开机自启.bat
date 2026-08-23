@echo off
chcp 65001 >nul
title QTrade Auto-Trading - Disable autostart

set "TARGET=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\QTrade后台交易_静默启动.vbs"

if exist "%TARGET%" (
    del /f "%TARGET%"
    echo Autostart disabled.
) else (
    echo Autostart item not found. Nothing to do.
)
pause
