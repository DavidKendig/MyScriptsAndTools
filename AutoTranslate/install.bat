@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title AutoTranslate - Installer

echo ============================================================
echo   AutoTranslate installer for Windows
echo ============================================================
echo.

REM PY holds the executable, PYARGS any launcher switch. Keeping them apart
REM lets the executable be quoted, which matters when the user profile path
REM contains spaces.
set "PY="
set "PYARGS="

REM ---------------------------------------------------------------
REM 1. Locate Python 3
REM ---------------------------------------------------------------
py -3 --version >nul 2>&1 && set "PY=py" && set "PYARGS=-3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)

if not defined PY (
    echo Python 3 was not found. Installing it with winget...
    where winget >nul 2>&1
    if errorlevel 1 (
        echo.
        echo winget is not available on this machine.
        echo Please install Python 3.12 manually from https://www.python.org/downloads/
        echo IMPORTANT: tick "Add python.exe to PATH" and keep the "tcl/tk and IDLE" option.
        echo Then run this installer again.
        echo.
        pause
        exit /b 1
    )
    winget install --id Python.Python.3.12 --exact --source winget ^
        --accept-package-agreements --accept-source-agreements
    echo.
    echo Re-checking for Python...
    py -3 --version >nul 2>&1 && set "PY=py" && set "PYARGS=-3"
    if not defined PY (
        if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
            set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        )
    )
    if not defined PY (
        echo.
        echo Python was installed but this window still has the old PATH.
        echo Close this window, open a new one, and run install.bat again.
        echo.
        pause
        exit /b 1
    )
)

for /f "delims=" %%v in ('"%PY%" %PYARGS% --version 2^>^&1') do set "PYVER=%%v"
echo Found !PYVER!
echo.

REM ---------------------------------------------------------------
REM 2. Verify Tkinter is present
REM ---------------------------------------------------------------
"%PY%" %PYARGS% -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo WARNING: this Python has no Tkinter, so the graphical interface will not start.
    echo Repair your Python installation and tick "tcl/tk and IDLE".
    echo The headless mode ^(start.bat --cli^) will still work.
    echo.
)

REM ---------------------------------------------------------------
REM 3. Virtual environment
REM ---------------------------------------------------------------
if exist ".venv\Scripts\python.exe" (
    echo Reusing the existing virtual environment.
) else (
    echo Creating the virtual environment in .venv ...
    "%PY%" %PYARGS% -m venv .venv
    if errorlevel 1 (
        echo.
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

REM ---------------------------------------------------------------
REM 4. Dependencies
REM ---------------------------------------------------------------
echo.
echo Installing dependencies...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Pillow could not be installed. AutoTranslate still runs without it,
    echo but images will be uploaded at full resolution.
)

echo.
echo ============================================================
echo   Done. Launch the app with start.bat
echo ============================================================
echo.
echo You also need a model server running:
echo   Ollama    - https://ollama.com/download  then:  ollama pull llama3.2-vision
echo   LM Studio - https://lmstudio.ai  then load a vision model and start the server
echo.
pause
