@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v1-ops\Stop-Biaoshu-Dev.ps1" %*
exit /b %ERRORLEVEL%
