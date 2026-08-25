@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
set "LOG_DIR=%ROOT_DIR%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

set "PYTHON_EXE="
set "PYTHON_ARGS="
if exist "%ROOT_DIR%\.venv\Scripts\python.exe" set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"

if not defined PYTHON_EXE (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_EXE set "PYTHON_EXE=%%P"
)
if not defined PYTHON_EXE (
  for /f "delims=" %%P in ('where py 2^>nul') do if not defined PYTHON_EXE (
    set "PYTHON_EXE=%%P"
    set "PYTHON_ARGS=-3"
  )
)
if not defined PYTHON_EXE if defined LocalAppData (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do if exist "%%~fD\python.exe" if not defined PYTHON_EXE set "PYTHON_EXE=%%~fD\python.exe"
)

if not defined PYTHON_EXE (
  echo [daily_update_1830] ERROR: Python interpreter not found. >&2
  echo [daily_update_1830] Install Python or add python/py to PATH. >&2
  endlocal & exit /b 9009
)

>>"%LOG_DIR%\daily_update_1830_bat.log" echo [daily_update_1830] Python=%PYTHON_EXE%
"%PYTHON_EXE%" %PYTHON_ARGS% -X utf8 "%SCRIPT_DIR%daily_update_1830.py" >> "%LOG_DIR%\daily_update_1830_bat.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
