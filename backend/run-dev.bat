@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" exit /b 1

netstat -ano 2>nul | findstr /i /c:":8000 " | findstr /i "LISTENING" >nul
if not errorlevel 1 exit /b 0

powershell -NoProfile -WindowStyle Hidden -Command "$python = Join-Path (Get-Location) '.venv\Scripts\python.exe'; Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--reload','--host','127.0.0.1','--port','8000') -WorkingDirectory (Get-Location).Path -WindowStyle Hidden" >nul 2>&1
exit /b %ERRORLEVEL%
