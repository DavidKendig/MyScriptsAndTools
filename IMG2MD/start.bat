@echo off
setlocal
title Image to Markdown

echo ================================================
echo  Image to Markdown Converter
echo ================================================
echo.

REM -- Check Python is available --------------------
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python was not found.
    echo.
    echo Please install Python 3.10 or later from:
    echo   https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

REM -- Install / update requirements ----------------
echo Checking requirements...
echo.
pip install -r "%~dp0requirements.txt"
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip failed to install requirements.
    echo Try running this file as Administrator, or check your internet connection.
    echo.
    pause
    exit /b 1
)

echo.
echo Requirements are up to date.
echo.

REM -- Launch the GUI -------------------------------
echo Starting Image to Markdown...
echo.
python "%~dp0gui.py"

endlocal
