@echo off
chcp 65001 >nul
title QTrade Auto-Trading - Stop

echo Stopping QTrade background trading service...

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'server\.py' -and $_.CommandLine -match 'single-instance' } | ForEach-Object { Write-Host ('Stopped PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }"

echo.
echo Done. If no PID shown above, the service was not running.
pause
