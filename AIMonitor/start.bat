@echo off
setlocal EnableDelayedExpansion
title AI System Monitor

echo ================================================
echo  AI System Monitor
echo ================================================
echo.

REM -- Locate a usable Python interpreter ----------
REM Prefer the official 'py' launcher; fall back to 'python'.
set "PYEXE="
where py >nul 2>&1
if %errorlevel% equ 0 (
    py -3 -c "import sys; print(sys.version)" >nul 2>&1
    if !errorlevel! equ 0 set "PYEXE=py -3"
)
if not defined PYEXE (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        python -c "import sys; print(sys.version)" >nul 2>&1
        if !errorlevel! equ 0 set "PYEXE=python"
    )
)
if not defined PYEXE (
    echo ERROR: No working Python interpreter was found.
    echo.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and make sure "Add Python to PATH" is ticked.
    echo Also disable the Windows Store python alias:
    echo   Settings ^> Apps ^> Advanced app settings ^> App execution aliases
    echo.
    pause
    exit /b 1
)

echo Using interpreter: %PYEXE%
%PYEXE% --version
echo.

REM -- Install / update requirements ----------------
echo Checking requirements...
echo.
%PYEXE% -m pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo WARNING: pip failed to install some requirements.
    echo The monitor will still try to start.
    echo.
    pause
)

echo.
echo Requirements are ready.
echo.

REM -- Launch the GUI -------------------------------
echo Starting AI System Monitor...
echo.
%PYEXE% "%~dp0ai_monitor.py"
set "RC=%errorlevel%"

echo.
echo ================================================
echo  AI System Monitor exited (code %RC%).
echo ================================================
echo.
pause
endlocal
