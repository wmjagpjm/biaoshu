@echo off
setlocal EnableExtensions
title Biaoshu Dev Launcher
cd /d "%~dp0"

set "ROOT=%cd%"
set "BACKEND=%ROOT%\backend"
set "FRONTEND=%ROOT%\frontend"
set "URL=http://127.0.0.1:5173/create"

echo.
echo === Biaoshu FE+BE start ===
echo ROOT=%ROOT%
echo.

if not exist "%BACKEND%\run-dev.bat" (
  echo [ERROR] missing %BACKEND%\run-dev.bat
  goto :fail
)
if not exist "%FRONTEND%\run-dev.bat" (
  echo [ERROR] missing %FRONTEND%\run-dev.bat
  goto :fail
)

REM ---- Backend ----
call :listening 8000
if %ERRORLEVEL%==0 (
  echo [OK] port 8000 already up
) else (
  echo [START] open window: Biaoshu-API
  start "Biaoshu-API" "%BACKEND%\run-dev.bat"
)

REM ---- Frontend ----
call :listening 5173
if %ERRORLEVEL%==0 (
  echo [OK] port 5173 already up
) else (
  echo [START] open window: Biaoshu-Vite
  start "Biaoshu-Vite" "%FRONTEND%\run-dev.bat"
)

echo.
echo Waiting for http://127.0.0.1:8000/api/health and :5173 ...
set /a _i=0
:wait_loop
set /a _i+=1
call :http_ok "http://127.0.0.1:8000/api/health"
set "_be=%ERRORLEVEL%"
call :listening 5173
set "_fe=%ERRORLEVEL%"
if "%_be%"=="0" if "%_fe%"=="0" goto :ready
if %_i% GEQ 90 goto :timeout
ping -n 2 127.0.0.1 >nul
goto :wait_loop

:ready
echo [OK] services ready
echo Opening %URL%
start "" "%URL%"
echo.
echo Backend:  http://127.0.0.1:8000/api/health
echo Frontend: %URL%
echo.
echo Keep API/Vite windows open. This window can stay open too.
echo.
pause
exit /b 0

:timeout
echo [WARN] timeout.
echo Look at Biaoshu-API / Biaoshu-Vite windows for red errors.
echo You can also double-click:
echo   backend\run-dev.bat
echo   frontend\run-dev.bat
echo.
pause
exit /b 1

:fail
echo.
pause
exit /b 1

:listening
netstat -ano 2>nul | findstr "LISTENING" | findstr ":%~1 " >nul 2>&1
if errorlevel 1 (
  netstat -ano 2>nul | findstr "LISTENING" | findstr ":%~1$" >nul 2>&1
)
exit /b %ERRORLEVEL%

:http_ok
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -Uri '%~1' -TimeoutSec 2; if($r.StatusCode -ge 200 -and $r.StatusCode -lt 500){ exit 0 } else { exit 1 } } catch { exit 1 }"
exit /b %ERRORLEVEL%
