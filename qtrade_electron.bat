@echo off
setlocal
set "PROJECT_ROOT=%~dp0"

if not exist "%PROJECT_ROOT%electron\package.json" (
    echo [ERROR] Electron project was not found under this folder.
    endlocal & exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm was not found on PATH. Install Node.js 18+ first.
    endlocal & exit /b 1
)

echo Starting QTrade Electron development app...
call npm --prefix "%PROJECT_ROOT%electron" run start
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo [ERROR] Electron exited with code %EXIT_CODE%.
endlocal & exit /b %EXIT_CODE%
