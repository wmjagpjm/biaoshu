# 模块：标书前后端联调启动（PowerShell）
# 用途：双击 bat 无效时，可右键「使用 PowerShell 运行」本脚本
# 对接：backend uvicorn :8000 + frontend vite :5173

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$Url = "http://127.0.0.1:5173/create"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"

Write-Host "=== Biaoshu FE+BE start ===" -ForegroundColor Cyan
Write-Host "ROOT=$Root"

if (-not (Test-Path (Join-Path $Backend "app\main.py"))) {
  Write-Host "[ERROR] backend missing: $Backend" -ForegroundColor Red
  Read-Host "Press Enter to exit"
  exit 1
}
if (-not (Test-Path (Join-Path $Frontend "package.json"))) {
  Write-Host "[ERROR] frontend missing: $Frontend" -ForegroundColor Red
  Read-Host "Press Enter to exit"
  exit 1
}

function Test-Port([int]$Port) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $Port)
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

if (Test-Port 8000) {
  Write-Host "[OK] port 8000 already up"
} else {
  Write-Host "[START] backend :8000"
  if (Test-Path $Py) {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "`"$Py`" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000" -WorkingDirectory $Backend -WindowStyle Normal
  } else {
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000" -WorkingDirectory $Backend -WindowStyle Normal
  }
}

if (Test-Port 5173) {
  Write-Host "[OK] port 5173 already up"
} else {
  Write-Host "[START] frontend :5173"
  Start-Process -FilePath "cmd.exe" -ArgumentList "/k", "npm run dev" -WorkingDirectory $Frontend -WindowStyle Normal
}

Write-Host "Waiting for services..."
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  if ((Test-Port 8000) -and (Test-Port 5173)) { $ok = $true; break }
  Start-Sleep -Seconds 1
}

if ($ok) {
  Write-Host "[OK] opening $Url" -ForegroundColor Green
  Start-Process $Url
} else {
  Write-Host "[WARN] timeout. Open manually: $Url" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Backend:  http://127.0.0.1:8000/api/health"
Write-Host "Frontend: $Url"
Write-Host "Keep Biaoshu-API / Biaoshu-Vite windows open."
Read-Host "Press Enter to close this launcher"
