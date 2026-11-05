@echo off
setlocal enabledelayedexpansion

:: 1. Standardize execution context to repository root
cd /d "%~dp0"

echo ===================================================
echo   Livestock Analytics Platform - Local Dev Launcher
echo ===================================================

:: 2. Python Environment Detection Pipeline
set "PYTHON_EXEC="

:: Check local virtual environment in root or backend
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXEC=.venv\Scripts\python.exe"
    echo [INFO] Detected active virtual environment: .venv
) else if exist "backend\.venv\Scripts\python.exe" (
    set "PYTHON_EXEC=backend\.venv\Scripts\python.exe"
    echo [INFO] Detected active virtual environment: backend\.venv
)

:: Probe system 'python' command
if not defined PYTHON_EXEC (
    python --version >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXEC=python"
        echo [INFO] Detected system Python executable: python
    )
)

:: Probe Windows Python Launcher 'py -3' fallback
if not defined PYTHON_EXEC (
    py -3 --version >nul 2>&1
    if !errorlevel! equ 0 (
        set "PYTHON_EXEC=py -3"
        echo [INFO] Detected Windows Python Launcher: py -3
    )
)

:: Error Trap: Missing Python runtime
if not defined PYTHON_EXEC (
    echo.
    echo [ERROR] Python 3.12+ was not detected on this system.
    echo.
    echo Remediation Steps:
    echo   1. Download Python 3.12+ from: https://www.python.org/downloads/
    echo   2. Run the installer and CHECK the box: "Add python.exe to PATH"
    echo   3. Restart your terminal or command prompt and run run_dev.bat again.
    echo.
    pause
    exit /b 1
)

:: 3. Preflight Dependency & Data Checks
echo [INFO] Verifying core dependencies...
%PYTHON_EXEC% -c "import fastapi, uvicorn, duckdb, pandas, openpyxl, PIL" >nul 2>&1
if !errorlevel! neq 0 (
    echo [WARN] Required packages are missing. Installing via requirements.txt...
    %PYTHON_EXEC% -m pip install -r requirements.txt
    if !errorlevel! neq 0 (
        echo [ERROR] Failed to install dependencies. Please run 'pip install -r requirements.txt' manually.
        pause
        exit /b 1
    )
)

:: Check local DuckDB development database
if not exist "backend\data\dev.duckdb" (
    echo [WARN] Local database backend\data\dev.duckdb not found.
    echo [INFO] Initializing DuckDB database with default seed data...
    %PYTHON_EXEC% scripts\init_duckdb.py --force
)

:: 4. Start Uvicorn Server via Python Module Invocation
echo.
echo [INFO] Launching backend API and static frontend...
echo [INFO] Access platform at: http://localhost:8000/login.html
echo.

cd backend
%PYTHON_EXEC% -m uvicorn app.main:app --reload --port 8000

