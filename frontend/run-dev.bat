@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where npm >nul 2>&1
if errorlevel 1 exit /b 1

netstat -ano 2>nul | findstr /i /c:":5173 " | findstr /i "LISTENING" >nul
if not errorlevel 1 exit /b 0

powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath $env:ComSpec -ArgumentList @('/c','npm run dev') -WorkingDirectory (Get-Location).Path -WindowStyle Hidden" >nul 2>&1
exit /b %ERRORLEVEL%
