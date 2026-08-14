@echo off
cd /d "%~dp0electron"
start "" ".\node_modules\electron\dist\electron.exe" main.js
