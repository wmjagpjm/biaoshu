@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not exist "frontend\run-dev.bat" exit /b 1
call "frontend\run-dev.bat"
exit /b %ERRORLEVEL%
