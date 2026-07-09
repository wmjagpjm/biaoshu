@echo off
setlocal
cd /d "%~dp0"
title Biaoshu-Vite
echo Starting biaoshu frontend (Vite)
echo Working dir: %cd%
echo.

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm not in PATH
  pause
  exit /b 1
)

call npm run dev
echo.
echo Dev server exited.
pause
