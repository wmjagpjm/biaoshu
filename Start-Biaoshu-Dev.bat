@echo off
setlocal EnableExtensions
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v1-ops\Start-Biaoshu-Dev.ps1" -Component all %*
exit /b %ERRORLEVEL%
