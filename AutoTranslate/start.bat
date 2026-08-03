@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title AutoTranslate

REM PY holds the executable, PYARGS any launcher switch. Both stay separate so
REM the executable can be quoted (paths with spaces) while the switch is not.
set "PY="
set "PYARGS="

if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not defined PY (
    py -3 --version >nul 2>&1 && set "PY=py" && set "PYARGS=-3"
)
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo Python was not found. Run install.bat first.
    pause
    exit /b 1
)

REM Arguments are passed through, e.g.
REM   start.bat --cli --folder "C:\pages" --model llava:7b
"%PY%" %PYARGS% "%~dp0main.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo AutoTranslate exited with code %RC%.
    pause
)
exit /b %RC%
