@echo off
setlocal
cd /d "%~dp0"
title Biaoshu-API
echo Starting biaoshu backend on http://127.0.0.1:8000
echo Working dir: %cd%
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
) else (
  echo [ERROR] .venv not found. Install once:
  echo   python -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt
  echo.
  pause
  exit /b 1
)

echo.
echo Server exited.
pause
