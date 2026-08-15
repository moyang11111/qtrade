@echo off
cd /d "%~dp0electron"

if not exist ".\node_modules\electron\dist\electron.exe" (
    echo [提示] 未找到 Electron，请先在 electron 目录执行: npm install
    echo 浏览器模式: 双击项目根目录的 run.bat 即可。
    pause
    exit /b 1
)

start "" ".\node_modules\electron\dist\electron.exe" main.js
