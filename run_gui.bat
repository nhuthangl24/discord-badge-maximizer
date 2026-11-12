@echo off
title Discord Badge Maximizer GUI Server Manager
echo ======================================================
echo   Starting Discord Badge Maximizer GUI Control Panel...
echo ======================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH!
    echo Please download and install Python from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

:: Install required dependencies if missing
echo Checking dependencies...
pip install -r requirements.txt >nul 2>&1

:: Launch GUI with pythonw (Zero terminal / No console window)
echo Launching GUI...
start "" pythonw gui.py
exit /b 0
