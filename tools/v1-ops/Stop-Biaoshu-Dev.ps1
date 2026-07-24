# 模块：标书开发服务受控停机（PowerShell）
# 用途：仅终止本仓库 backend/.venv 与 frontend 归属的 8000/5173 监听进程树
# 安全：先全量归属判定，失败则零终止；禁止按端口盲杀

[CmdletBinding()]
param(
  [switch]$WhatIf,
  [string]$ListenerSnapshotJson
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\.."))
$BackendVenv = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "backend\.venv"))
$FrontendDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "frontend"))
$TargetPorts = @(8000, 5173)
$ReleaseWaitSeconds = 15

function Write-Fail([string]$Message) {
  Write-Host $Message -ForegroundColor Red
}

function Write-Ok([string]$Message) {
  Write-Host $Message -ForegroundColor Green
}

function Test-PathUnder([string]$Candidate, [string]$Root) {
  if ([string]::IsNullOrWhiteSpace($Candidate) -or [string]::IsNullOrWhiteSpace($Root)) {
    return $false
  }
  try {
    $c = [System.IO.Path]::GetFullPath($Candidate)
    $r = [System.IO.Path]::GetFullPath($Root)
  } catch {
    return $false
  }
  if ($c.Equals($r, [System.StringComparison]::OrdinalIgnoreCase)) {
    return $true
  }
  $prefix = if ($r.EndsWith("\")) { $r } else { $r + "\" }
  return $c.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-IsNoConnectionError([string]$Message) {
  if ([string]::IsNullOrWhiteSpace($Message)) { return $false }
  # 无匹配监听对象：视为该查询条件下确认无监听（幂等）
  if ($Message -match 'No MSFT_NetTCPConnection|No matching MSFT_NetTCPConnection|ObjectNotFound|找不到.*NetTCPConnection') {
    return $true
  }
  return $false
}

function Test-IsStrictJsonInt($Value) {
  # 仅接受 ConvertFrom-Json 的整数数值类型；拒绝 string/bool/float
  if ($null -eq $Value) { return $false }
  if ($Value -is [bool]) { return $false }
  if ($Value -is [string]) { return $false }
  if ($Value -is [double] -or $Value -is [float] -or $Value -is [single] -or $Value -is [decimal]) {
    return $false
  }
  if ($Value -is [byte] -or $Value -is [sbyte] -or
      $Value -is [int16] -or $Value -is [uint16] -or
      $Value -is [int32] -or $Value -is [uint32] -or
      $Value -is [int64] -or $Value -is [uint64] -or
      $Value -is [int] -or $Value -is [long]) {
    return $true
  }
  return $false
}

function Test-IsNormalizedWindowsAbsolutePath([string]$PathText) {
  if ([string]::IsNullOrEmpty($PathText)) { return $true }
  if ($PathText.Length -gt 512) { return $false }
  if ($PathText -match '[\r\n\x00<>"|?*]') { return $false }
  # 拒绝相对路径与非法形态
  if ($PathText.StartsWith(".\") -or $PathText.StartsWith("./") -or
      $PathText.StartsWith("..\") -or $PathText.StartsWith("../") -or
      $PathText -eq "." -or $PathText -eq "..") {
    return $false
  }
  # 盘符绝对路径或 UNC
  $isDrive = $PathText -match '^[A-Za-z]:[\\/]'
  $isUnc = $PathText -match '^\\\\[^\\/]+[\\/][^\\/]+'
  if (-not $isDrive -and -not $isUnc) { return $false }
  try {
    if (-not [System.IO.Path]::IsPathRooted($PathText)) { return $false }
  } catch {
    return $false
  }
  # 拒绝路径段中的 ..（非规范/可疑）
  $segments = $PathText -split '[\\/]+'
  foreach ($seg in $segments) {
    if ($seg -eq "..") { return $false }
  }
  return $true
}

function Get-LiveListenerRecords {
  # Get-NetTCPConnection 不可用或枚举失败时固定失败，绝不能落入“无监听”
  if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    throw "LISTENER_ENUM_FAILED"
  }

  $records = New-Object System.Collections.Generic.List[object]
  foreach ($port in $TargetPorts) {
    # V1-Q/A8：每个端口始终执行 exact(127.0.0.1) 与 full(无 LocalAddress，保留全部 Listen)。
    # full 必须覆盖显式 RFC1918/LAN，禁止再按回环/通配集合过滤而丢弃 LAN 监听。
    # 不得因 exact 非空而短路 full（IPv4 127 与 IPv6/LAN 可同端口并存 foreign）。
    # 任一次非“无匹配”真异常固定失败；No MSFT/空数组视为该次确认空。
    # 将 exact+full 记录按 port/pid 去重合并后进入全量归属判定；任一 foreign 则整次拒绝。
    $exactConns = $null
    $exactOk = $false
    try {
      $exactConns = @(Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $port -State Listen -ErrorAction Stop)
      $exactOk = $true
    } catch {
      $detail = "$($_.Exception.Message) $($_.FullyQualifiedErrorId)"
      if (Test-IsNoConnectionError $detail) {
        $exactConns = @()
        $exactOk = $true
      }
    }
    if (-not $exactOk) {
      throw "LISTENER_ENUM_FAILED"
    }

    $fullConns = $null
    $fullOk = $false
    try {
      # 目标端口全部 Listen（含 0.0.0.0/::/回环/显式 RFC1918），不做地址白名单丢弃
      $fullConns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
      $fullOk = $true
    } catch {
      $detail = "$($_.Exception.Message) $($_.FullyQualifiedErrorId)"
      if (Test-IsNoConnectionError $detail) {
        $fullConns = @()
        $fullOk = $true
      }
    }
    if (-not $fullOk) {
      throw "LISTENER_ENUM_FAILED"
    }

    $seenPid = @{}
    foreach ($conn in @(@($exactConns) + @($fullConns))) {
      if (-not $conn) { continue }

      $owning = $null
      try {
        $owning = $conn.OwningProcess
      } catch {
        throw "LISTENER_PID_UNTRUSTED"
      }
      if ($null -eq $owning) {
        throw "LISTENER_PID_UNTRUSTED"
      }
      if ($owning -is [bool] -or $owning -is [string] -or
          $owning -is [double] -or $owning -is [float] -or
          $owning -is [single] -or $owning -is [decimal]) {
        throw "LISTENER_PID_UNTRUSTED"
      }
      try {
        $pidVal = [int]$owning
      } catch {
        throw "LISTENER_PID_UNTRUSTED"
      }
      if ($pidVal -le 0) {
        throw "LISTENER_PID_UNTRUSTED"
      }
      # 同端口同 PID 去重（exact 与 full 可能重复命中同一监听）
      $dedupeKey = "{0}|{1}" -f [int]$port, $pidVal
      if ($seenPid.ContainsKey($dedupeKey)) { continue }
      $seenPid[$dedupeKey] = $true

      $exe = $null
      $cmd = $null
      try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidVal" -ErrorAction SilentlyContinue
        if ($proc) {
          $exe = $proc.ExecutablePath
          $cmd = $proc.CommandLine
        }
      } catch {
        $exe = $null
        $cmd = $null
      }
      $records.Add([pscustomobject]@{
          port            = [int]$port
          pid             = $pidVal
          executablePath  = $(if ($exe) { [string]$exe } else { "" })
          commandLine     = $(if ($cmd) { [string]$cmd } else { "" })
        }) | Out-Null
    }
  }
  return $records
}

function Read-SnapshotRecords([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) {
    throw "SNAPSHOT_INVALID"
  }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "SNAPSHOT_INVALID"
  }
  try {
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $data = $raw | ConvertFrom-Json
  } catch {
    throw "SNAPSHOT_INVALID"
  }
  if ($null -eq $data) {
    throw "SNAPSHOT_INVALID"
  }

  $trimmedRaw = $raw.Trim()
  if (-not $trimmedRaw.StartsWith("[")) {
    throw "SNAPSHOT_INVALID"
  }

  $list = @($data)
  $records = New-Object System.Collections.Generic.List[object]
  $seenPids = @{}
  $allowedKeys = @("port", "pid", "executablePath", "commandLine")

  foreach ($item in $list) {
    if ($null -eq $item) { throw "SNAPSHOT_INVALID" }
    $props = @($item.PSObject.Properties.Name)
    if ($props.Count -ne 4) { throw "SNAPSHOT_INVALID" }
    foreach ($k in $allowedKeys) {
      if ($props -notcontains $k) { throw "SNAPSHOT_INVALID" }
    }
    foreach ($p in $props) {
      if ($allowedKeys -notcontains $p) { throw "SNAPSHOT_INVALID" }
    }

    $port = $item.port
    $pidVal = $item.pid
    $exe = $item.executablePath
    $cmd = $item.commandLine

    if ($null -eq $port -or $null -eq $pidVal) { throw "SNAPSHOT_INVALID" }
    # 仅接受 JSON 整数数值类型；拒绝 "8000"、true、8000.0
    if (-not (Test-IsStrictJsonInt $port)) { throw "SNAPSHOT_INVALID" }
    if (-not (Test-IsStrictJsonInt $pidVal)) { throw "SNAPSHOT_INVALID" }

    try {
      $portInt = [int]$port
      $pidInt = [int]$pidVal
    } catch {
      throw "SNAPSHOT_INVALID"
    }
    # 越界：端口必须属于目标集合；PID 必须为正
    if ($portInt -lt 1 -or $portInt -gt 65535) { throw "SNAPSHOT_INVALID" }
    if ($TargetPorts -notcontains $portInt) { throw "SNAPSHOT_INVALID" }
    if ($pidInt -le 0) { throw "SNAPSHOT_INVALID" }
    if ($seenPids.ContainsKey($pidInt)) { throw "SNAPSHOT_INVALID" }
    $seenPids[$pidInt] = $true

    if ($null -eq $exe) { $exe = "" }
    if ($null -eq $cmd) { $cmd = "" }
    if ($exe -isnot [string] -or $cmd -isnot [string]) { throw "SNAPSHOT_INVALID" }
    $exeStr = [string]$exe
    $cmdStr = [string]$cmd
    if ($cmdStr -match '[\r\n\x00]') { throw "SNAPSHOT_INVALID" }
    if ($cmdStr.Length -gt 8192) { throw "SNAPSHOT_INVALID" }
    # executablePath 非空必须是规范 Windows 绝对路径
    if (-not (Test-IsNormalizedWindowsAbsolutePath $exeStr)) { throw "SNAPSHOT_INVALID" }

    $records.Add([pscustomobject]@{
        port           = $portInt
        pid            = $pidInt
        executablePath = $exeStr
        commandLine    = $cmdStr
      }) | Out-Null
  }
  return $records
}

function Test-BackendOwnership([string]$ExecutablePath, [string]$CommandLine) {
  if ([string]::IsNullOrWhiteSpace($ExecutablePath)) { return $false }
  if (-not (Test-PathUnder $ExecutablePath $BackendVenv)) { return $false }
  $leaf = [System.IO.Path]::GetFileName($ExecutablePath)
  if ($leaf -notmatch '^(?i)(python\.exe|pythonw\.exe)$') { return $false }
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
  $cl = $CommandLine.ToLowerInvariant()
  $repoHint = $RepoRoot.ToLowerInvariant().Replace("\", "/")
  $backendHint = ($RepoRoot + "\backend").ToLowerInvariant().Replace("\", "/")
  $venvHint = $BackendVenv.ToLowerInvariant().Replace("\", "/")
  $clNorm = $cl.Replace("\", "/")
  $hasUvicorn = $clNorm -match "uvicorn"
  $hasRepo = ($clNorm.Contains($repoHint) -or $clNorm.Contains($backendHint) -or $clNorm.Contains($venvHint))
  if (-not $hasUvicorn) { return $false }
  if (-not $hasRepo) { return $false }
  return $true
}

function Test-FrontendOwnership([string]$ExecutablePath, [string]$CommandLine) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
  $cl = $CommandLine
  $clNorm = $cl.ToLowerInvariant().Replace("\", "/")
  $feNorm = $FrontendDir.ToLowerInvariant().Replace("\", "/")
  if (-not $clNorm.Contains($feNorm)) { return $false }
  $isNode = $false
  if (-not [string]::IsNullOrWhiteSpace($ExecutablePath)) {
    $leaf = [System.IO.Path]::GetFileName($ExecutablePath)
    if ($leaf -match '^(?i)(node\.exe)$') { $isNode = $true }
  }
  if (-not $isNode) {
    if ($clNorm -match "(^|[\\/ ])node(\.exe)?[\s`"]" -or $clNorm -match "vite" -or $clNorm -match "npm") {
      $isNode = $true
    }
  }
  if (-not $isNode) { return $false }
  return $true
}

function Test-RecordOwnership($Record) {
  $port = [int]$Record.port
  if ($port -eq 8000) {
    return Test-BackendOwnership $Record.executablePath $Record.commandLine
  }
  if ($port -eq 5173) {
    return Test-FrontendOwnership $Record.executablePath $Record.commandLine
  }
  return $false
}

function Test-PortListening([int]$Port) {
  # V1-Q：观察目标端口全部 Listen（含显式 LAN）；仅明确无 MSFT_NetTCPConnection 视为空闲。
  # 其它枚举异常一律 busy fail-closed；禁止任何回环 TCP 客户端异步连接回退探测。
  try {
    if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
      # 复查阶段无法枚举时按仍占用处理，避免误报成功
      return $true
    }
    try {
      $c = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop)
      return ($c.Count -gt 0)
    } catch {
      $detail = "$($_.Exception.Message) $($_.FullyQualifiedErrorId)"
      if (Test-IsNoConnectionError $detail) {
        return $false
      }
      # 非“明确无监听对象”的枚举异常：直接 busy，零连接回退
      return $true
    }
  } catch {
    return $true
  }
}

function Stop-ProcessTree([int]$ProcessId) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "taskkill.exe"
  $psi.Arguments = "/PID $ProcessId /T /F"
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $p = [System.Diagnostics.Process]::Start($psi)
  $p.WaitForExit(10000) | Out-Null
  return $p.ExitCode
}

# --- 主流程 ---

if ($ListenerSnapshotJson -and -not $WhatIf) {
  Write-Fail "ListenerSnapshotJson 只能与 -WhatIf 同时使用，已中止且未终止任何进程"
  exit 2
}

$records = $null
try {
  if ($ListenerSnapshotJson) {
    $records = Read-SnapshotRecords -Path $ListenerSnapshotJson
  } else {
    $records = Get-LiveListenerRecords
  }
} catch {
  $errMsg = "$($_.Exception.Message)"
  if ($errMsg -eq "SNAPSHOT_INVALID") {
    Write-Fail "监听快照格式无效，已中止且未终止任何进程"
    exit 2
  }
  Write-Fail "无法收集端口监听信息，已中止且未终止任何进程"
  exit 1
}

# 无监听：幂等成功（仅在已确认无监听时）
if (-not $records -or @($records).Count -eq 0) {
  if ($WhatIf) {
    Write-Ok "WhatIf：未发现 8000/5173 监听，无需终止"
  } else {
    Write-Ok "未发现 8000/5173 监听，视为已停止"
  }
  exit 0
}

# 全量归属判定（先验证后终止）
$owned = New-Object System.Collections.Generic.List[object]
foreach ($rec in @($records)) {
  if (-not (Test-RecordOwnership $rec)) {
    Write-Fail "无法确认端口监听进程归属，已中止且未终止任何进程"
    exit 3
  }
  $owned.Add($rec) | Out-Null
}

$uniquePids = @($owned | Select-Object -ExpandProperty pid -Unique)

if ($WhatIf) {
  Write-Ok ("WhatIf：将终止本仓库归属进程数={0}（端口 8000/5173）" -f $uniquePids.Count)
  exit 0
}

foreach ($pidVal in $uniquePids) {
  try {
    Stop-ProcessTree -ProcessId ([int]$pidVal) | Out-Null
  } catch {
    Write-Fail "终止进程失败，请手动检查后重试"
    exit 4
  }
}

$deadline = [DateTime]::UtcNow.AddSeconds($ReleaseWaitSeconds)
$stillBusy = @()
do {
  $stillBusy = @()
  foreach ($port in $TargetPorts) {
    if (Test-PortListening -Port $port) {
      $stillBusy += $port
    }
  }
  if ($stillBusy.Count -eq 0) { break }
  Start-Sleep -Milliseconds 500
} while ([DateTime]::UtcNow -lt $deadline)

if ($stillBusy.Count -gt 0) {
  Write-Fail "进程终止后端口仍未释放，请手动检查"
  exit 5
}

Write-Ok "已受控停止本仓库开发服务（8000/5173）"
exit 0