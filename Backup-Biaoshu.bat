@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if "%~1"=="" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v1-ops\Backup-Biaoshu.ps1"
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\v1-ops\Backup-Biaoshu.ps1" -DestinationRoot "%~1"
)
exit /b %ERRORLEVEL%
