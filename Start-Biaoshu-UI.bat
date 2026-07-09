@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0frontend"
set "URL=http://127.0.0.1:5173/create"

if not exist "%ROOT%\package.json" (
  echo [错误] 未找到前端目录：%ROOT%
  pause
  exit /b 1
)

cd /d "%ROOT%"

powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',5173); $c.Close(); exit 0 } catch { exit 1 }"
if %ERRORLEVEL%==0 (
  echo [提示] 开发服务已在运行，打开浏览器…
  start "" "%URL%"
  exit /b 0
)

echo [启动] 正在启动前端开发服务…
start "Biaoshu-Vite" cmd /k "cd /d \"%ROOT%\" && npm run dev"

powershell -NoProfile -Command ^
  "$u='%URL%'; $ok=$false; for($i=0;$i -lt 60;$i++){ try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',5173); $c.Close(); $ok=$true; break } catch { Start-Sleep -Seconds 1 } }; if($ok){ Start-Process $u } else { Write-Host '等待超时，请手动打开' $u }"

endlocal
