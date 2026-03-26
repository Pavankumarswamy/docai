@echo off
:: ============================================================
::  DOCAI — Interactive Terminal CLI Launcher
::  Double-click this file (or run from CMD/PowerShell) to start
:: ============================================================

title DOCAI — Document Intelligence System

:: Locate the virtual environment relative to this script
set "SCRIPT_DIR=%~dp0"
set "VENV_PYTHON=%SCRIPT_DIR%backend\.venv\Scripts\python.exe"
set "CLI_SCRIPT=%SCRIPT_DIR%backend\cli.py"

:: ── Verify venv exists ────────────────────────────────────────
if not exist "%VENV_PYTHON%" (
    echo.
    echo  [ERROR] Virtual environment not found at:
    echo          %VENV_PYTHON%
    echo.
    echo  Please set up the environment first:
    echo    cd backend
    echo    python -m venv .venv
    echo    .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

:: ── Verify CLI script exists ──────────────────────────────────
if not exist "%CLI_SCRIPT%" (
    echo.
    echo  [ERROR] CLI script not found: %CLI_SCRIPT%
    echo.
    pause
    exit /b 1
)

:: ── Enable UTF-8 output for rich / Unicode symbols ───────────
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

:: ── Launch the interactive CLI ────────────────────────────────
"%VENV_PYTHON%" "%CLI_SCRIPT%" %*

:: Keep window open only on error
if %errorlevel% neq 0 (
    echo.
    echo  [!] DOCAI exited with error code %errorlevel%
    echo  Check backend\logs\run.log for details.
    echo.
    pause
)
