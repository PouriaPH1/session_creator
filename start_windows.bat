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

:: Auto Open Browser after 3 seconds in background
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"

:: Start Server
echo [INFO] Starting Master Server & Web Admin Panel on http://localhost:8000 ...
python run_server.py

pause
