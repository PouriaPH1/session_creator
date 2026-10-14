@echo off
chcp 65001 >nul
title Telegram Session Creator & Admin Panel

echo ====================================================================
echo 🚀 Telegram Session Creator & Manager (Windows Runner)
echo ====================================================================

:: Check Python installation
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    exit /b
)

:: Create Virtual Environment if not exists
if not exist "venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv venv
)

:: Activate Virtual Environment
call venv\Scripts\activate.bat

:: Install Requirements
echo [INFO] Checking and installing dependencies...
pip install -r requirements.txt -q

:: Check .env
if not exist ".env" (
    echo [INFO] Creating .env from .env.example...
    copy .env.example .env >nul
)

:: Start Server with automatic free port detection and browser launcher
echo [INFO] Starting Master Server with dynamic free port detection...
python run_server.py --open-browser

pause
