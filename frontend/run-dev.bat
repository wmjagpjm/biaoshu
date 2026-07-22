@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\tools\v1-ops\Start-Biaoshu-Dev.ps1" -Component frontend %*
exit /b %ERRORLEVEL%
