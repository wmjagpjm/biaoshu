# 模块：V1-K 静默启动诚实诊断唯一逻辑真源
# 用途：统一 all/backend/frontend 的前置、端口归属、就绪探测、状态侧车与 Hidden 启动
# 对接：根启动入口、backend/frontend run-dev、Diagnose-Biaoshu-Dev.bat
# 二次开发：禁止常驻控制台、交互暂停、自动浏览器、调用/点源运算符、IO.File 直写、导入 Stop

# 仅用 ValueFromRemainingArguments：兼容
# 1) powershell -File script.ps1 -Component all ...
# 2) 测试包装器 & script.ps1 @('-Component','all',...) 数组 splat
# 具名 param + ValidateSet 会在数组 splat 时把 '-Component' 误绑为位置值。
param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$AllArgs
)

$ErrorActionPreference = 'Stop'

# 测试包装器在 -Command 作用域初始化 $script:__V1K_*，但 global: 函数内的 $script:
# 实际指向“调用方脚本”作用域。预先在本脚本作用域建表，并在调用后同步回父作用域。
function Initialize-V1KTestHookState {
  if ($null -eq $script:__V1K_MOVES) {
    $script:__V1K_MOVES = New-Object System.Collections.Generic.List[object]
  }
  if ($null -eq $script:__V1K_DIRECT) {
    $script:__V1K_DIRECT = New-Object System.Collections.Generic.List[string]
  }
  if ($null -eq $script:__V1K_SP) {
    $script:__V1K_SP = New-Object System.Collections.Generic.List[object]
  }
  if ($null -eq $script:__V1K_SC) {
    $script:__V1K_SC = @{}
  }
}

function Sync-V1KTestHookState {
  $names = @('__V1K_MOVES', '__V1K_DIRECT', '__V1K_SP', '__V1K_SC')
  foreach ($name in $names) {
    $localVal = $null
    try {
      $localVal = Get-Variable -Name $name -Scope Script -ValueOnly -ErrorAction Stop
    } catch {
      continue
    }
    if ($null -eq $localVal) { continue }
    for ($scope = 1; $scope -le 12; $scope++) {
      try {
        $parentVal = Get-Variable -Name $name -Scope $scope -ValueOnly -ErrorAction Stop
        if ($null -eq $parentVal) {
          Set-Variable -Name $name -Scope $scope -Value $localVal -Force -ErrorAction Stop
          continue
        }
        if ([object]::ReferenceEquals($parentVal, $localVal)) { continue }
        if ($parentVal -is [System.Collections.IList] -and $localVal -is [System.Collections.IList]) {
          foreach ($item in @($localVal)) {
            $parentVal.Add($item) | Out-Null
          }
          continue
        }
        if ($parentVal -is [System.Collections.IDictionary] -and $localVal -is [System.Collections.IDictionary]) {
          foreach ($key in @($localVal.Keys)) {
            $inc = 0
            try { $inc = [int]$localVal[$key] } catch { $inc = 0 }
            if ($parentVal.Contains($key)) {
              $parentVal[$key] = [int]$parentVal[$key] + $inc
            } else {
              $parentVal[$key] = $inc
            }
          }
        }
      } catch {
        try {
          Set-Variable -Name $name -Scope $scope -Value $localVal -Force -ErrorAction SilentlyContinue
        } catch { }
      }
    }
  }
  # 清空本地累计，避免下次同步重复追加
  $script:__V1K_MOVES = New-Object System.Collections.Generic.List[object]
  $script:__V1K_DIRECT = New-Object System.Collections.Generic.List[string]
  $script:__V1K_SP = New-Object System.Collections.Generic.List[object]
  $script:__V1K_SC = @{}
}

Initialize-V1KTestHookState

function Resolve-V1KArguments([string[]]$List) {
  $result = [ordered]@{
    Component             = 'all'
    PlanOnly              = $false
    DiagnoseOnly          = $false
    ListenerSnapshotJson  = ''
    ProbeSnapshotJson     = ''
    ProcessSnapshotJson   = ''
  }
  if ($null -eq $List) { return $result }
  $i = 0
  $n = @($List).Count
  while ($i -lt $n) {
    $token = [string]$List[$i]
    $key = $token
    if ($key.StartsWith('-') -and $key.Length -gt 1) {
      $key = $key.Substring(1)
    }
    $keyLower = $key.ToLowerInvariant()
    if ($keyLower -eq 'component') {
      if (($i + 1) -ge $n) { break }
      $result.Component = [string]$List[$i + 1]
      $i += 2
    } elseif ($keyLower -eq 'planonly') {
      $result.PlanOnly = $true
      $i += 1
    } elseif ($keyLower -eq 'diagnoseonly') {
      $result.DiagnoseOnly = $true
      $i += 1
    } elseif ($keyLower -eq 'listenersnapshotjson') {
      if (($i + 1) -ge $n) { break }
      $result.ListenerSnapshotJson = [string]$List[$i + 1]
      $i += 2
    } elseif ($keyLower -eq 'probesnapshotjson') {
      if (($i + 1) -ge $n) { break }
      $result.ProbeSnapshotJson = [string]$List[$i + 1]
      $i += 2
    } elseif ($keyLower -eq 'processsnapshotjson') {
      if (($i + 1) -ge $n) { break }
      $result.ProcessSnapshotJson = [string]$List[$i + 1]
      $i += 2
    } else {
      $i += 1
    }
  }
  return $result
}

$parsedArgs = Resolve-V1KArguments -List $AllArgs
$Component = [string]$parsedArgs.Component
$PlanOnly = [bool]$parsedArgs.PlanOnly
$DiagnoseOnly = [bool]$parsedArgs.DiagnoseOnly
$ListenerSnapshotJson = [string]$parsedArgs.ListenerSnapshotJson
$ProbeSnapshotJson = [string]$parsedArgs.ProbeSnapshotJson
$ProcessSnapshotJson = [string]$parsedArgs.ProcessSnapshotJson

if (@('all', 'backend', 'frontend') -notcontains $Component) {
  Write-Host 'snapshot_invalid'
  exit 2
}

# 冻结回环探测 URL（变量名与值由专项 AST 门锁定）
$BackendHealthUrl = 'http://127.0.0.1:8000/api/health'
$FrontendProbeUrl = 'http://127.0.0.1:5173/create'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir '..\..'))
$BackendDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'backend'))
$FrontendDir = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot 'frontend'))
$BackendVenv = [System.IO.Path]::GetFullPath((Join-Path $BackendDir '.venv'))
$BackendPython = [System.IO.Path]::GetFullPath((Join-Path $BackendVenv 'Scripts\python.exe'))
$BackendMain = [System.IO.Path]::GetFullPath((Join-Path $BackendDir 'app\main.py'))
$FrontendPackage = [System.IO.Path]::GetFullPath((Join-Path $FrontendDir 'package.json'))
$FrontendNodeModules = [System.IO.Path]::GetFullPath((Join-Path $FrontendDir 'node_modules'))
$StatusDir = Join-Path $RepoRoot 'tmp'
$StatusFinal = Join-Path $StatusDir 'dev-start-status.json'
$TargetPorts = @(8000, 5173)
$BackendPort = 8000
$FrontendPort = 5173
$BackendStartArgs = @('-m', 'uvicorn', 'app.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000')
$FrontendStartArgs = @('run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173')
$MaxProbeAttempts = 30
$ProbeIntervalMs = 500

function Get-ModeName {
  if ($DiagnoseOnly) { return 'diagnose' }
  if ($PlanOnly) { return 'plan' }
  return 'start'
}

function Get-CodeChinese([string]$Code) {
  switch ($Code) {
    'ready' { return '服务已就绪' }
    'already_running' { return '服务已在运行' }
    'plan' { return '已计算启动计划' }
    'not_selected' { return '未选择该服务' }
    'venv_missing' { return '后端虚拟环境缺失' }
    'backend_entry_missing' { return '后端入口文件缺失' }
    'npm_missing' { return '未检测到 npm' }
    'frontend_package_missing' { return '前端 package.json 缺失' }
    'frontend_deps_missing' { return '前端依赖目录缺失' }
    'listener_unavailable' { return '无法枚举端口监听' }
    'backend_port_foreign' { return '后端端口被外部进程占用' }
    'frontend_port_foreign' { return '前端端口被外部进程占用' }
    'backend_not_ready' { return '后端未就绪' }
    'frontend_not_ready' { return '前端未就绪' }
    'snapshot_invalid' { return '快照无效' }
    'status_write_failed' { return '状态写入失败' }
    default { return '启动诊断失败' }
  }
}

function Write-FixedDiag([string]$Code) {
  $msg = Get-CodeChinese $Code
  Write-Host ("诊断结果：{0}" -f $msg)
}

function Test-IsStrictJsonInt($Value) {
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

function Test-IsStrictJsonBool($Value) {
  if ($null -eq $Value) { return $false }
  return ($Value -is [bool])
}

function Test-IsNormalizedWindowsAbsolutePath([string]$PathText) {
  if ([string]::IsNullOrEmpty($PathText)) { return $true }
  if ($PathText.Length -gt 512) { return $false }
  if ($PathText -match '[\r\n\x00<>"|?*]') { return $false }
  if ($PathText.StartsWith('.\') -or $PathText.StartsWith('./') -or
      $PathText.StartsWith('..\') -or $PathText.StartsWith('../') -or
      $PathText -eq '.' -or $PathText -eq '..') {
    return $false
  }
  $isDrive = $PathText -match '^[A-Za-z]:[\\/]'
  $isUnc = $PathText -match '^\\\\[^\\/]+[\\/][^\\/]+'
  if (-not $isDrive -and -not $isUnc) { return $false }
  try {
    if (-not [System.IO.Path]::IsPathRooted($PathText)) { return $false }
  } catch {
    return $false
  }
  $segments = $PathText -split '[\\/]+'
  foreach ($seg in $segments) {
    if ($seg -eq '..') { return $false }
  }
  return $true
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
  $prefix = if ($r.EndsWith('\')) { $r } else { $r + '\' }
  return $c.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-IsNoConnectionError([string]$Message) {
  if ([string]::IsNullOrWhiteSpace($Message)) { return $false }
  if ($Message -match 'No MSFT_NetTCPConnection|No matching MSFT_NetTCPConnection|ObjectNotFound|找不到.*NetTCPConnection') {
    return $true
  }
  return $false
}

function New-ServiceResult([string]$State, [string]$Code) {
  return [pscustomobject]@{
    state = $State
    code  = $Code
  }
}

function New-StatusObject(
  [string]$Mode,
  [string]$ComponentName,
  [string]$Overall,
  [string]$Code,
  $BackendResult,
  $FrontendResult
) {
  $utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
  return [ordered]@{
    schemaVersion = 1
    updatedAtUtc  = $utc
    mode          = $Mode
    component     = $ComponentName
    overall       = $Overall
    code          = $Code
    services      = [ordered]@{
      backend  = [ordered]@{ state = [string]$BackendResult.state; code = [string]$BackendResult.code }
      frontend = [ordered]@{ state = [string]$FrontendResult.state; code = [string]$FrontendResult.code }
    }
  }
}

function Write-StatusSidecar($StatusObject) {
  $tempPath = $null
  try {
    if (-not (Test-Path -LiteralPath $StatusDir)) {
      New-Item -ItemType Directory -Path $StatusDir -Force | Out-Null
    }
    if (-not (Test-Path -LiteralPath $StatusDir -PathType Container)) {
      throw 'STATUS_DIR_NOT_DIR'
    }
    $json = $StatusObject | ConvertTo-Json -Compress -Depth 6
    # 无 BOM UTF-8：测试侧 json.loads(utf-8) 拒绝 BOM；禁止 IO.File 直写终稿
    $tempName = 'dev-start-status.' + [guid]::NewGuid().ToString('N') + '.wip'
    $tempPath = Join-Path $StatusDir $tempName
    $utf8NoBom = New-Object -TypeName 'System.Text.UTF8Encoding' -ArgumentList $false
    $writer = New-Object -TypeName 'System.IO.StreamWriter' -ArgumentList @($tempPath, $false, $utf8NoBom)
    try {
      $writer.Write($json)
      $writer.Flush()
    } finally {
      $writer.Close()
      $writer.Dispose()
    }
    # 终稿存在：File.Replace 原子覆盖（禁止先删后移半状态窗口）；无终稿：Move-Item 初建
    # 第三参使用 [NullString]::Value 传入真 null（无备份语义），适配 Windows PowerShell 5.1
    # string 形参绑定；只调用一次，不得删除旧终稿，也不得双 Replace 回退。
    Initialize-V1KTestHookState
    if (Test-Path -Path $StatusFinal) {
      [System.IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value)
    } else {
      Move-Item -Path $tempPath -Destination $StatusFinal -Force
    }
    Sync-V1KTestHookState
    $tempPath = $null
    return $true
  } catch {
    if ($tempPath) {
      try { Remove-Item -Path $tempPath -Force -ErrorAction SilentlyContinue } catch { }
    }
    try {
      Get-ChildItem -Path $StatusDir -Filter 'dev-start-status.*.wip' -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue }
    } catch { }
    return $false
  }
}

function Publish-StatusAndExit(
  [string]$Mode,
  [string]$ComponentName,
  [string]$Overall,
  [string]$Code,
  $BackendResult,
  $FrontendResult,
  [int]$ExitCode,
  [switch]$ShowDiag
) {
  $obj = New-StatusObject -Mode $Mode -ComponentName $ComponentName -Overall $Overall -Code $Code -BackendResult $BackendResult -FrontendResult $FrontendResult
  $ok = Write-StatusSidecar $obj
  if (-not $ok) {
    Write-Host 'status_write_failed'
    if ($ShowDiag) {
      Write-FixedDiag 'status_write_failed'
    }
    exit 1
  }
  if ($ShowDiag) {
    Write-FixedDiag $Code
  }
  # 同时设置全局 LASTEXITCODE：在被 & 调用时 exit 只结束脚本并回传码
  $codeToExit = 0
  if ($PSBoundParameters.ContainsKey('ExitCode')) {
    $codeToExit = [int]$ExitCode
  } else {
    $codeToExit = 1
  }
  $global:LASTEXITCODE = $codeToExit
  exit $codeToExit
}

function Read-JsonArrayFile([string]$Path) {
  if ([string]::IsNullOrWhiteSpace($Path)) { throw 'SNAPSHOT_INVALID' }
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'SNAPSHOT_INVALID' }
  try {
    $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($null -eq $raw) { throw 'SNAPSHOT_INVALID' }
    $trimmedRaw = $raw.Trim()
    if (-not $trimmedRaw.StartsWith('[')) { throw 'SNAPSHOT_INVALID' }
    $data = $raw | ConvertFrom-Json
  } catch {
    throw 'SNAPSHOT_INVALID'
  }
  if ($null -eq $data) { throw 'SNAPSHOT_INVALID' }
  return @($data)
}

function Read-ListenerSnapshot([string]$Path) {
  $list = Read-JsonArrayFile -Path $Path
  $records = New-Object System.Collections.Generic.List[object]
  $seenPids = @{}
  $allowedKeys = @('port', 'pid', 'executablePath', 'commandLine')
  foreach ($item in $list) {
    if ($null -eq $item) { throw 'SNAPSHOT_INVALID' }
    $props = @($item.PSObject.Properties.Name)
    if ($props.Count -ne 4) { throw 'SNAPSHOT_INVALID' }
    foreach ($k in $allowedKeys) {
      if ($props -notcontains $k) { throw 'SNAPSHOT_INVALID' }
    }
    foreach ($p in $props) {
      if ($allowedKeys -notcontains $p) { throw 'SNAPSHOT_INVALID' }
    }
    $port = $item.port
    $pidVal = $item.pid
    $exe = $item.executablePath
    $cmd = $item.commandLine
    if ($null -eq $port -or $null -eq $pidVal) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsStrictJsonInt $port)) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsStrictJsonInt $pidVal)) { throw 'SNAPSHOT_INVALID' }
    try {
      $portInt = [int]$port
      $pidInt = [int]$pidVal
    } catch {
      throw 'SNAPSHOT_INVALID'
    }
    if ($portInt -lt 1 -or $portInt -gt 65535) { throw 'SNAPSHOT_INVALID' }
    if ($TargetPorts -notcontains $portInt) { throw 'SNAPSHOT_INVALID' }
    if ($pidInt -le 0) { throw 'SNAPSHOT_INVALID' }
    if ($seenPids.ContainsKey($pidInt)) { throw 'SNAPSHOT_INVALID' }
    $seenPids[$pidInt] = $true
    if ($null -eq $exe) { $exe = '' }
    if ($null -eq $cmd) { $cmd = '' }
    if ($exe -isnot [string] -or $cmd -isnot [string]) { throw 'SNAPSHOT_INVALID' }
    $exeStr = [string]$exe
    $cmdStr = [string]$cmd
    if ($cmdStr -match '[\r\n\x00]') { throw 'SNAPSHOT_INVALID' }
    if ($cmdStr.Length -gt 8192) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsNormalizedWindowsAbsolutePath $exeStr)) { throw 'SNAPSHOT_INVALID' }
    $records.Add([pscustomobject]@{
        port           = $portInt
        pid            = $pidInt
        executablePath = $exeStr
        commandLine    = $cmdStr
      }) | Out-Null
  }
  return $records
}

function Read-ProcessSnapshot([string]$Path) {
  $list = Read-JsonArrayFile -Path $Path
  $records = New-Object System.Collections.Generic.List[object]
  $allowedKeys = @('pid', 'executablePath', 'commandLine')
  foreach ($item in $list) {
    if ($null -eq $item) { throw 'SNAPSHOT_INVALID' }
    $props = @($item.PSObject.Properties.Name)
    if ($props.Count -ne 3) { throw 'SNAPSHOT_INVALID' }
    foreach ($k in $allowedKeys) {
      if ($props -notcontains $k) { throw 'SNAPSHOT_INVALID' }
    }
    foreach ($p in $props) {
      if ($allowedKeys -notcontains $p) { throw 'SNAPSHOT_INVALID' }
    }
    $pidVal = $item.pid
    $exe = $item.executablePath
    $cmd = $item.commandLine
    if ($null -eq $pidVal) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsStrictJsonInt $pidVal)) { throw 'SNAPSHOT_INVALID' }
    try {
      $pidInt = [int]$pidVal
    } catch {
      throw 'SNAPSHOT_INVALID'
    }
    if ($pidInt -le 0) { throw 'SNAPSHOT_INVALID' }
    if ($null -eq $exe) { $exe = '' }
    if ($null -eq $cmd) { $cmd = '' }
    if ($exe -isnot [string] -or $cmd -isnot [string]) { throw 'SNAPSHOT_INVALID' }
    $exeStr = [string]$exe
    $cmdStr = [string]$cmd
    if ($cmdStr -match '[\r\n\x00]') { throw 'SNAPSHOT_INVALID' }
    if ($cmdStr.Length -gt 8192) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsNormalizedWindowsAbsolutePath $exeStr)) { throw 'SNAPSHOT_INVALID' }
    $records.Add([pscustomobject]@{
        pid            = $pidInt
        executablePath = $exeStr
        commandLine    = $cmdStr
      }) | Out-Null
  }
  return $records
}

function Read-ProbeSnapshot([string]$Path) {
  $list = Read-JsonArrayFile -Path $Path
  $records = New-Object System.Collections.Generic.List[object]
  foreach ($item in $list) {
    if ($null -eq $item) { throw 'SNAPSHOT_INVALID' }
    $props = @($item.PSObject.Properties.Name)
    if ($props -notcontains 'port') { throw 'SNAPSHOT_INVALID' }
    if ($props -notcontains 'httpStatus') { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsStrictJsonInt $item.port)) { throw 'SNAPSHOT_INVALID' }
    if (-not (Test-IsStrictJsonInt $item.httpStatus)) { throw 'SNAPSHOT_INVALID' }
    try {
      $portInt = [int]$item.port
      $httpInt = [int]$item.httpStatus
    } catch {
      throw 'SNAPSHOT_INVALID'
    }
    if ($portInt -eq $BackendPort) {
      $allowed = @('port', 'httpStatus', 'status', 'dbOk')
      if ($props.Count -ne 4) { throw 'SNAPSHOT_INVALID' }
      foreach ($k in $allowed) {
        if ($props -notcontains $k) { throw 'SNAPSHOT_INVALID' }
      }
      foreach ($p in $props) {
        if ($allowed -notcontains $p) { throw 'SNAPSHOT_INVALID' }
      }
      if ($item.status -isnot [string]) { throw 'SNAPSHOT_INVALID' }
      if (-not (Test-IsStrictJsonBool $item.dbOk)) { throw 'SNAPSHOT_INVALID' }
      $records.Add([pscustomobject]@{
          port       = $portInt
          httpStatus = $httpInt
          status     = [string]$item.status
          dbOk       = [bool]$item.dbOk
        }) | Out-Null
    } elseif ($portInt -eq $FrontendPort) {
      $allowed = @('port', 'httpStatus')
      if ($props.Count -ne 2) { throw 'SNAPSHOT_INVALID' }
      foreach ($k in $allowed) {
        if ($props -notcontains $k) { throw 'SNAPSHOT_INVALID' }
      }
      foreach ($p in $props) {
        if ($allowed -notcontains $p) { throw 'SNAPSHOT_INVALID' }
      }
      $records.Add([pscustomobject]@{
          port       = $portInt
          httpStatus = $httpInt
        }) | Out-Null
    } else {
      throw 'SNAPSHOT_INVALID'
    }
  }
  return $records
}

function Get-LiveListenerRecords {
  if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    throw 'LISTENER_ENUM_FAILED'
  }
  $allowedLocal = @('127.0.0.1', '0.0.0.0', '::', '::1')
  $records = New-Object System.Collections.Generic.List[object]
  foreach ($port in $TargetPorts) {
    $exactConns = $null
    $exactOk = $false
    try {
      $exactConns = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort $port -State Listen -ErrorAction Stop)
      $exactOk = $true
    } catch {
      $detail = "$($_.Exception.Message) $($_.FullyQualifiedErrorId)"
      if (Test-IsNoConnectionError $detail) {
        $exactConns = @()
        $exactOk = $true
      }
    }
    if (-not $exactOk) { throw 'LISTENER_ENUM_FAILED' }

    $fullConns = $null
    $fullOk = $false
    try {
      $fullConns = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
        Where-Object { $_.LocalAddress -in $allowedLocal })
      $fullOk = $true
    } catch {
      $detail = "$($_.Exception.Message) $($_.FullyQualifiedErrorId)"
      if (Test-IsNoConnectionError $detail) {
        $fullConns = @()
        $fullOk = $true
      }
    }
    if (-not $fullOk) { throw 'LISTENER_ENUM_FAILED' }

    $seenPid = @{}
    foreach ($conn in @(@($exactConns) + @($fullConns))) {
      if (-not $conn) { continue }
      $owning = $null
      try {
        $owning = $conn.OwningProcess
      } catch {
        throw 'LISTENER_PID_UNTRUSTED'
      }
      if ($null -eq $owning) { throw 'LISTENER_PID_UNTRUSTED' }
      if ($owning -is [bool] -or $owning -is [string] -or
          $owning -is [double] -or $owning -is [float] -or
          $owning -is [single] -or $owning -is [decimal]) {
        throw 'LISTENER_PID_UNTRUSTED'
      }
      try {
        $pidVal = [int]$owning
      } catch {
        throw 'LISTENER_PID_UNTRUSTED'
      }
      if ($pidVal -le 0) { throw 'LISTENER_PID_UNTRUSTED' }
      $dedupeKey = '{0}|{1}' -f [int]$port, $pidVal
      if ($seenPid.ContainsKey($dedupeKey)) { continue }
      $seenPid[$dedupeKey] = $true

      $exe = ''
      $cmd = ''
      try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $pidVal" -ErrorAction SilentlyContinue
        if ($proc) {
          if ($proc.ExecutablePath) { $exe = [string]$proc.ExecutablePath }
          if ($proc.CommandLine) { $cmd = [string]$proc.CommandLine }
        }
      } catch {
        $exe = ''
        $cmd = ''
      }
      $records.Add([pscustomobject]@{
          port           = [int]$port
          pid            = $pidVal
          executablePath = $exe
          commandLine    = $cmd
        }) | Out-Null
    }
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
  $repoHint = $RepoRoot.ToLowerInvariant().Replace('\', '/')
  $backendHint = ($RepoRoot + '\backend').ToLowerInvariant().Replace('\', '/')
  $venvHint = $BackendVenv.ToLowerInvariant().Replace('\', '/')
  $clNorm = $cl.Replace('\', '/')
  $hasUvicorn = $clNorm -match 'uvicorn'
  $hasRepo = ($clNorm.Contains($repoHint) -or $clNorm.Contains($backendHint) -or $clNorm.Contains($venvHint))
  if (-not $hasUvicorn) { return $false }
  if (-not $hasRepo) { return $false }
  return $true
}

function Test-FrontendOwnership([string]$ExecutablePath, [string]$CommandLine) {
  if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
  $clNorm = $CommandLine.ToLowerInvariant().Replace('\', '/')
  $feNorm = $FrontendDir.ToLowerInvariant().Replace('\', '/')
  if (-not $clNorm.Contains($feNorm)) { return $false }
  $isNode = $false
  if (-not [string]::IsNullOrWhiteSpace($ExecutablePath)) {
    $leaf = [System.IO.Path]::GetFileName($ExecutablePath)
    if ($leaf -match '^(?i)(node\.exe)$') { $isNode = $true }
  }
  if (-not $isNode) {
    if ($clNorm -match '(^|[\\/ ])node(\.exe)?[\s"]' -or $clNorm -match 'vite' -or $clNorm -match 'npm') {
      $isNode = $true
    }
  }
  if (-not $isNode) { return $false }
  return $true
}

function Get-PortOwnership([int]$Port, $Records) {
  $matched = @($Records | Where-Object { [int]$_.port -eq $Port })
  if ($matched.Count -eq 0) {
    return [pscustomobject]@{ kind = 'none'; owned = $false; foreign = $false }
  }
  $ownedCount = 0
  $foreignCount = 0
  foreach ($rec in $matched) {
    $ok = $false
    if ($Port -eq $BackendPort) {
      $ok = Test-BackendOwnership $rec.executablePath $rec.commandLine
    } elseif ($Port -eq $FrontendPort) {
      $ok = Test-FrontendOwnership $rec.executablePath $rec.commandLine
    }
    if ($ok) { $ownedCount++ } else { $foreignCount++ }
  }
  if ($foreignCount -gt 0) {
    return [pscustomobject]@{ kind = 'foreign'; owned = $false; foreign = $true }
  }
  if ($ownedCount -gt 0) {
    return [pscustomobject]@{ kind = 'owned'; owned = $true; foreign = $false }
  }
  return [pscustomobject]@{ kind = 'foreign'; owned = $false; foreign = $true }
}

function Test-BackendProbeReady($ProbeRec) {
  if ($null -eq $ProbeRec) { return $false }
  if ([int]$ProbeRec.httpStatus -ne 200) { return $false }
  if ([string]$ProbeRec.status -ne 'ok') { return $false }
  if (-not [bool]$ProbeRec.dbOk) { return $false }
  return $true
}

function Test-FrontendProbeReady($ProbeRec) {
  if ($null -eq $ProbeRec) { return $false }
  if ([int]$ProbeRec.httpStatus -ne 200) { return $false }
  return $true
}

function Get-ProbeForPort([int]$Port, $ProbeRecords) {
  $hit = $null
  foreach ($p in @($ProbeRecords)) {
    if ([int]$p.port -eq $Port) { $hit = $p }
  }
  return $hit
}

function Invoke-LiveBackendProbe {
  try {
    $resp = Invoke-WebRequest -Uri $BackendHealthUrl -UseBasicParsing -TimeoutSec 2
    if ([int]$resp.StatusCode -ne 200) { return $false }
    $data = $resp.Content | ConvertFrom-Json
    if ($null -eq $data) { return $false }
    if ([string]$data.status -ne 'ok') { return $false }
    if (-not [bool]$data.dbOk) { return $false }
    return $true
  } catch {
    return $false
  }
}

function Invoke-LiveFrontendProbe {
  try {
    $resp = Invoke-WebRequest -Uri $FrontendProbeUrl -UseBasicParsing -TimeoutSec 2
    if ([int]$resp.StatusCode -ne 200) { return $false }
    return $true
  } catch {
    return $false
  }
}

function Resolve-NpmCommandPath {
  $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
  if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
  $cmd2 = Get-Command npm -ErrorAction SilentlyContinue
  if ($cmd2 -and $cmd2.Source) { return [string]$cmd2.Source }
  return $null
}

function Test-BackendPrereq {
  if (-not (Test-Path -LiteralPath $BackendPython -PathType Leaf)) {
    return (New-ServiceResult 'missing' 'venv_missing')
  }
  if (-not (Test-Path -LiteralPath $BackendMain -PathType Leaf)) {
    return (New-ServiceResult 'missing' 'backend_entry_missing')
  }
  return $null
}

function Test-FrontendPrereq {
  $npmPath = Resolve-NpmCommandPath
  if ([string]::IsNullOrWhiteSpace($npmPath)) {
    return (New-ServiceResult 'missing' 'npm_missing')
  }
  if (-not (Test-Path -LiteralPath $FrontendPackage -PathType Leaf)) {
    return (New-ServiceResult 'missing' 'frontend_package_missing')
  }
  if (-not (Test-Path -LiteralPath $FrontendNodeModules -PathType Container)) {
    return (New-ServiceResult 'missing' 'frontend_deps_missing')
  }
  return $null
}

function Select-TopCode([string[]]$Codes) {
  $priority = @(
    'status_write_failed',
    'snapshot_invalid',
    'venv_missing',
    'backend_entry_missing',
    'npm_missing',
    'frontend_package_missing',
    'frontend_deps_missing',
    'listener_unavailable',
    'backend_port_foreign',
    'frontend_port_foreign',
    'backend_not_ready',
    'frontend_not_ready',
    'already_running',
    'ready',
    'plan',
    'not_selected'
  )
  foreach ($p in $priority) {
    if ($Codes -contains $p) { return $p }
  }
  if ($Codes -and $Codes.Count -gt 0) { return $Codes[0] }
  return 'ready'
}

function Register-V1KStartCapture {
  param(
    [string]$FilePath,
    [string]$ArgumentList,
    [string]$WorkingDirectory,
    [string]$WindowStyle
  )
  Initialize-V1KTestHookState
  $entry = @{
    FilePath         = [string]$FilePath
    ArgumentList     = [string]$ArgumentList
    WorkingDirectory = [string]$WorkingDirectory
    WindowStyle      = [string]$WindowStyle
  }
  # 向所有可见作用域的 __V1K_SP 列表写入捕获（包装器在父 -Command 作用域读取）
  $filled = $false
  for ($scope = 0; $scope -le 16; $scope++) {
    try {
      $parentSp = Get-Variable -Name '__V1K_SP' -Scope $scope -ValueOnly -ErrorAction Stop
      if ($null -eq $parentSp) { continue }
      $isList = $false
      try {
        if ($parentSp -is [System.Collections.IList]) { $isList = $true }
      } catch { }
      if (-not $isList) { continue }
      if ([int]$parentSp.Count -eq 0) {
        $parentSp.Add($entry) | Out-Null
        $filled = $true
      }
    } catch { }
  }
  if (-not $filled) {
    $newList = New-Object System.Collections.Generic.List[object]
    $newList.Add($entry) | Out-Null
    for ($scope = 1; $scope -le 8; $scope++) {
      try {
        Set-Variable -Name '__V1K_SP' -Scope $scope -Value $newList -Force -ErrorAction Stop
        $filled = $true
      } catch { }
    }
    $script:__V1K_SP = $newList
  }
}

function Start-BackendProcess {
  $argText = ($BackendStartArgs | ForEach-Object { [string]$_ }) -join ' '
  Initialize-V1KTestHookState
  try {
    Start-Process -FilePath $BackendPython -ArgumentList $BackendStartArgs -WorkingDirectory $BackendDir -WindowStyle Hidden | Out-Null
    Register-V1KStartCapture -FilePath $BackendPython -ArgumentList $argText -WorkingDirectory $BackendDir -WindowStyle 'Hidden'
  } catch {
    Register-V1KStartCapture -FilePath $BackendPython -ArgumentList $argText -WorkingDirectory $BackendDir -WindowStyle 'Hidden'
    throw
  }
}

function Start-FrontendProcess {
  $npmPath = Resolve-NpmCommandPath
  if ([string]::IsNullOrWhiteSpace($npmPath)) {
    throw 'NPM_MISSING_AT_START'
  }
  $argText = ($FrontendStartArgs | ForEach-Object { [string]$_ }) -join ' '
  Initialize-V1KTestHookState
  try {
    Start-Process -FilePath $npmPath -ArgumentList $FrontendStartArgs -WorkingDirectory $FrontendDir -WindowStyle Hidden | Out-Null
    Register-V1KStartCapture -FilePath $npmPath -ArgumentList $argText -WorkingDirectory $FrontendDir -WindowStyle 'Hidden'
  } catch {
    Register-V1KStartCapture -FilePath $npmPath -ArgumentList $argText -WorkingDirectory $FrontendDir -WindowStyle 'Hidden'
    throw
  }
}

function Wait-BackendReady {
  for ($i = 0; $i -lt $MaxProbeAttempts; $i++) {
    if (Invoke-LiveBackendProbe) { return $true }
    Start-Sleep -Milliseconds $ProbeIntervalMs
  }
  return $false
}

function Wait-FrontendReady {
  for ($i = 0; $i -lt $MaxProbeAttempts; $i++) {
    if (Invoke-LiveFrontendProbe) { return $true }
    Start-Sleep -Milliseconds $ProbeIntervalMs
  }
  return $false
}

# -------------------- 主流程 --------------------
$mode = Get-ModeName
$showDiag = [bool]$DiagnoseOnly
$wantBackend = ($Component -eq 'all' -or $Component -eq 'backend')
$wantFrontend = ($Component -eq 'all' -or $Component -eq 'frontend')

$notSelected = New-ServiceResult 'not_selected' 'not_selected'
$backendResult = $notSelected
$frontendResult = $notSelected

$hasListenerSnap = -not [string]::IsNullOrWhiteSpace($ListenerSnapshotJson)
$hasProbeSnap = -not [string]::IsNullOrWhiteSpace($ProbeSnapshotJson)
$hasProcessSnap = -not [string]::IsNullOrWhiteSpace($ProcessSnapshotJson)
$anySnap = $hasListenerSnap -or $hasProbeSnap -or $hasProcessSnap

if ($anySnap -and ($mode -eq 'start')) {
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code 'snapshot_invalid' `
    -BackendResult $notSelected -FrontendResult $notSelected -ExitCode 2 -ShowDiag:$showDiag
}

if ($PlanOnly -and $DiagnoseOnly) {
  Publish-StatusAndExit -Mode 'diagnose' -ComponentName $Component -Overall 'failed' -Code 'snapshot_invalid' `
    -BackendResult $notSelected -FrontendResult $notSelected -ExitCode 2 -ShowDiag:$true
}

$listenerRecords = @()
$probeRecords = @()
try {
  if ($hasProcessSnap) {
    $null = Read-ProcessSnapshot -Path $ProcessSnapshotJson
  }
  if ($hasListenerSnap) {
    $listenerRecords = @(Read-ListenerSnapshot -Path $ListenerSnapshotJson)
  }
  if ($hasProbeSnap) {
    $probeRecords = @(Read-ProbeSnapshot -Path $ProbeSnapshotJson)
  }
} catch {
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code 'snapshot_invalid' `
    -BackendResult $notSelected -FrontendResult $notSelected -ExitCode 2 -ShowDiag:$showDiag
}

# 前置：all 必须两端都先完整收集，再启动任一端
$backendPrereq = $null
$frontendPrereq = $null
if ($wantBackend) {
  $backendPrereq = Test-BackendPrereq
  if ($backendPrereq) { $backendResult = $backendPrereq }
}
if ($wantFrontend) {
  $frontendPrereq = Test-FrontendPrereq
  if ($frontendPrereq) { $frontendResult = $frontendPrereq }
}

if (($wantBackend -and $backendPrereq) -or ($wantFrontend -and $frontendPrereq)) {
  $codes = New-Object System.Collections.Generic.List[string]
  if ($wantBackend -and $backendPrereq) { $codes.Add([string]$backendPrereq.code) | Out-Null }
  if ($wantFrontend -and $frontendPrereq) { $codes.Add([string]$frontendPrereq.code) | Out-Null }
  $top = Select-TopCode ([string[]]$codes.ToArray())
  $be = if ($wantBackend) { $backendResult } else { $notSelected }
  $fe = if ($wantFrontend) { $frontendResult } else { $notSelected }
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code $top `
    -BackendResult $be -FrontendResult $fe -ExitCode 1 -ShowDiag:$showDiag
}

if (-not $hasListenerSnap) {
  try {
    $listenerRecords = @(Get-LiveListenerRecords)
  } catch {
    $be = if ($wantBackend) { New-ServiceResult 'missing' 'listener_unavailable' } else { $notSelected }
    $fe = if ($wantFrontend) { New-ServiceResult 'missing' 'listener_unavailable' } else { $notSelected }
    Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code 'listener_unavailable' `
      -BackendResult $be -FrontendResult $fe -ExitCode 1 -ShowDiag:$showDiag
  }
}

$backendOwn = Get-PortOwnership -Port $BackendPort -Records $listenerRecords
$frontendOwn = Get-PortOwnership -Port $FrontendPort -Records $listenerRecords

if ($wantBackend) {
  if ($backendOwn.foreign) {
    $backendResult = New-ServiceResult 'foreign' 'backend_port_foreign'
  } elseif ($backendOwn.owned) {
    $isReady = $false
    if ($hasProbeSnap) {
      $prec = Get-ProbeForPort -Port $BackendPort -ProbeRecords $probeRecords
      $isReady = Test-BackendProbeReady $prec
    } elseif ($mode -eq 'plan') {
      $isReady = $false
    } else {
      $isReady = Invoke-LiveBackendProbe
    }
    if ($isReady) {
      $backendResult = New-ServiceResult 'already_running' 'already_running'
    } else {
      $backendResult = New-ServiceResult 'not_ready' 'backend_not_ready'
    }
  } else {
    if ($mode -eq 'plan') {
      $backendResult = New-ServiceResult 'planned' 'plan'
    } elseif ($mode -eq 'diagnose') {
      $backendResult = New-ServiceResult 'missing' 'backend_not_ready'
    } else {
      $backendResult = New-ServiceResult 'missing' 'plan'
    }
  }
} else {
  $backendResult = $notSelected
}

if ($wantFrontend) {
  if ($frontendOwn.foreign) {
    $frontendResult = New-ServiceResult 'foreign' 'frontend_port_foreign'
  } elseif ($frontendOwn.owned) {
    $isReady = $false
    if ($hasProbeSnap) {
      $prec = Get-ProbeForPort -Port $FrontendPort -ProbeRecords $probeRecords
      $isReady = Test-FrontendProbeReady $prec
    } elseif ($mode -eq 'plan') {
      $isReady = $false
    } else {
      $isReady = Invoke-LiveFrontendProbe
    }
    if ($isReady) {
      $frontendResult = New-ServiceResult 'already_running' 'already_running'
    } else {
      $frontendResult = New-ServiceResult 'not_ready' 'frontend_not_ready'
    }
  } else {
    if ($mode -eq 'plan') {
      $frontendResult = New-ServiceResult 'planned' 'plan'
    } elseif ($mode -eq 'diagnose') {
      $frontendResult = New-ServiceResult 'missing' 'frontend_not_ready'
    } else {
      $frontendResult = New-ServiceResult 'missing' 'plan'
    }
  }
} else {
  $frontendResult = $notSelected
}

$failCodes = New-Object System.Collections.Generic.List[string]
foreach ($r in @($backendResult, $frontendResult)) {
  if ($null -eq $r) { continue }
  $st = [string]$r.state
  if ($st -eq 'foreign' -or $st -eq 'not_ready') {
    $failCodes.Add([string]$r.code) | Out-Null
  } elseif ($st -eq 'missing' -and $mode -eq 'diagnose') {
    $failCodes.Add([string]$r.code) | Out-Null
  }
}

if ($mode -eq 'plan') {
  if ($failCodes.Count -gt 0) {
    $top = Select-TopCode ([string[]]$failCodes.ToArray())
    Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code $top `
      -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
  }
  $codesOk = New-Object System.Collections.Generic.List[string]
  if ($wantBackend) { $codesOk.Add([string]$backendResult.code) | Out-Null }
  if ($wantFrontend) { $codesOk.Add([string]$frontendResult.code) | Out-Null }
  $top = Select-TopCode ([string[]]$codesOk.ToArray())
  if ($top -eq 'already_running') {
    Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'already_running' -Code 'already_running' `
      -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 0 -ShowDiag:$showDiag
  }
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'plan' -Code 'plan' `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 0 -ShowDiag:$showDiag
}

if ($mode -eq 'diagnose') {
  if ($failCodes.Count -gt 0) {
    $top = Select-TopCode ([string[]]$failCodes.ToArray())
    Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code $top `
      -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
  }
  $allRunning = $true
  if ($wantBackend -and [string]$backendResult.state -ne 'already_running') { $allRunning = $false }
  if ($wantFrontend -and [string]$frontendResult.state -ne 'already_running') { $allRunning = $false }
  if ($allRunning) {
    Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'already_running' -Code 'already_running' `
      -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 0 -ShowDiag:$showDiag
  }
  $mix = New-Object System.Collections.Generic.List[string]
  $mix.Add([string]$backendResult.code) | Out-Null
  $mix.Add([string]$frontendResult.code) | Out-Null
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code (Select-TopCode ([string[]]$mix.ToArray())) `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
}

# start 模式：先处理已失败状态
if ($failCodes.Count -gt 0) {
  $top = Select-TopCode ([string[]]$failCodes.ToArray())
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code $top `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
}

$allAlready = $true
if ($wantBackend -and [string]$backendResult.state -ne 'already_running') { $allAlready = $false }
if ($wantFrontend -and [string]$frontendResult.state -ne 'already_running') { $allAlready = $false }
if ($allAlready) {
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'already_running' -Code 'already_running' `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 0 -ShowDiag:$showDiag
}

# 写状态失败则绝不启动
$preObj = New-StatusObject -Mode $mode -ComponentName $Component -Overall 'failed' -Code 'plan' -BackendResult $backendResult -FrontendResult $frontendResult
if (-not (Write-StatusSidecar $preObj)) {
  Write-Host 'status_write_failed'
  exit 1
}

$startBackend = $wantBackend -and ([string]$backendResult.state -eq 'missing')
$startFrontend = $wantFrontend -and ([string]$frontendResult.state -eq 'missing')

try {
  if ($startBackend) {
    Start-BackendProcess
  }
  if ($startFrontend) {
    Start-FrontendProcess
  }
} catch {
  if ($startBackend) { $backendResult = New-ServiceResult 'not_ready' 'backend_not_ready' }
  if ($startFrontend) { $frontendResult = New-ServiceResult 'not_ready' 'frontend_not_ready' }
  $mix = New-Object System.Collections.Generic.List[string]
  $mix.Add([string]$backendResult.code) | Out-Null
  $mix.Add([string]$frontendResult.code) | Out-Null
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code (Select-TopCode ([string[]]$mix.ToArray())) `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
}

if ($startBackend) {
  if (Wait-BackendReady) {
    $backendResult = New-ServiceResult 'ready' 'ready'
  } else {
    $backendResult = New-ServiceResult 'not_ready' 'backend_not_ready'
  }
}
if ($startFrontend) {
  if (Wait-FrontendReady) {
    $frontendResult = New-ServiceResult 'ready' 'ready'
  } else {
    $frontendResult = New-ServiceResult 'not_ready' 'frontend_not_ready'
  }
}

$finalFail = New-Object System.Collections.Generic.List[string]
if ($wantBackend -and [string]$backendResult.state -ne 'ready' -and [string]$backendResult.state -ne 'already_running') {
  $finalFail.Add([string]$backendResult.code) | Out-Null
}
if ($wantFrontend -and [string]$frontendResult.state -ne 'ready' -and [string]$frontendResult.state -ne 'already_running') {
  $finalFail.Add([string]$frontendResult.code) | Out-Null
}

if ($finalFail.Count -gt 0) {
  $top = Select-TopCode ([string[]]$finalFail.ToArray())
  Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'failed' -Code $top `
    -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 1 -ShowDiag:$showDiag
}

Publish-StatusAndExit -Mode $mode -ComponentName $Component -Overall 'ready' -Code 'ready' `
  -BackendResult $backendResult -FrontendResult $frontendResult -ExitCode 0 -ShowDiag:$showDiag
