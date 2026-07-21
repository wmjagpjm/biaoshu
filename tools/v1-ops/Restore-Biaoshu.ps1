# 模块：标书离线恢复 PowerShell 包装
# 用途：显式备份目录、中文确认后调用 Python 标准库核心；成功仅输出恢复前备份路径与固定摘要
# 对接：Restore-Biaoshu.bat；tools/v1-ops/biaoshu_restore.py
# 二次开发：不自动停机；不暴露 skip/force/注入参数；须保持 UTF-8 BOM 与 Windows PowerShell 5.1 可解析

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$BackupDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\.."))
$PyCore = Join-Path $ScriptDir "biaoshu_restore.py"
$VenvPython = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"

function Write-Fail([string]$Message) {
  Write-Host $Message -ForegroundColor Red
}

function Write-Ok([string]$Message) {
  Write-Host $Message -ForegroundColor Green
}

function ConvertTo-WindowsArgument([string]$Argument) {
  # 可靠 Windows argv quoting（兼容 PowerShell 5.1 / CommandLineToArgvW）
  if ($null -eq $Argument) { $Argument = "" }
  if ($Argument -match '[\r\n\x00"]') {
    throw "ARGUMENT_INVALID"
  }
  if ($Argument -notmatch '\s') {
    return $Argument
  }
  $trailing = [regex]::Match($Argument, '(\\+)$')
  if ($trailing.Success) {
    $Argument = $Argument + $trailing.Groups[1].Value
  }
  return '"' + $Argument + '"'
}

function Invoke-PythonCore {
  param(
    [Parameter(Mandatory)][string]$PythonExe,
    [Parameter(Mandatory)][string[]]$ArgumentList
  )
  $quoted = New-Object System.Collections.Generic.List[string]
  foreach ($a in $ArgumentList) {
    $quoted.Add((ConvertTo-WindowsArgument -Argument $a)) | Out-Null
  }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $PythonExe
  $psi.Arguments = [string]::Join(" ", $quoted.ToArray())
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.CreateNoWindow = $true
  $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
  $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
  $psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8"
  $psi.EnvironmentVariables["PYTHONUTF8"] = "1"

  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  try {
    if (-not $proc.Start()) {
      throw "PROCESS_START_FAILED"
    }
    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()
    $proc.WaitForExit() | Out-Null
    $stdoutText = [string]$stdoutTask.Result
    $stderrText = [string]$stderrTask.Result
    return [pscustomobject]@{
      ExitCode = [int]$proc.ExitCode
      StdOut   = $stdoutText
      StdErr   = $stderrText
    }
  } finally {
    if ($proc) { $proc.Dispose() }
  }
}

if (-not (Test-Path -LiteralPath $PyCore -PathType Leaf)) {
  Write-Fail "恢复核心模块不存在"
  exit 1
}

if ([string]::IsNullOrWhiteSpace($BackupDir)) {
  Write-Fail "必须显式指定备份目录"
  exit 1
}

try {
  $BackupDir = [System.IO.Path]::GetFullPath($BackupDir)
} catch {
  Write-Fail "备份目录路径无效"
  exit 1
}

if (-not (Test-Path -LiteralPath $BackupDir -PathType Container)) {
  Write-Fail "备份目录不存在"
  exit 1
}

# 显式中文确认：必须精确输入「恢复」；其它输入取消且零写入
Write-Host "即将用指定备份覆盖本机日用数据。请先确认已受控停机。"
Write-Host "请输入「恢复」继续，其它任意输入将取消："
$confirm = Read-Host
if ($confirm -cne "恢复") {
  Write-Fail "已取消恢复（未修改任何业务数据）"
  exit 1
}

$python = $null
if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
  $python = $VenvPython
} else {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) {
    $python = $cmd.Source
  }
}

if (-not $python) {
  Write-Fail "未找到可用的 Python 解释器"
  exit 1
}

$argList = @(
  $PyCore,
  "--repo-root", $RepoRoot,
  "--backup-dir", $BackupDir,
  "--apply"
)

$stdoutText = ""
$stderrText = ""
$exitCode = 1
try {
  $result = Invoke-PythonCore -PythonExe $python -ArgumentList $argList
  $exitCode = [int]$result.ExitCode
  $stdoutText = [string]$result.StdOut
  $stderrText = [string]$result.StdErr
} catch {
  $err = "$($_.Exception.Message)"
  if ($err -eq "ARGUMENT_INVALID") {
    Write-Fail "恢复参数无效"
    exit 1
  }
  Write-Fail "无法启动恢复核心"
  exit 1
}

if ($exitCode -ne 0) {
  $msg = $stderrText.Trim()
  if ([string]::IsNullOrWhiteSpace($msg)) {
    $msg = "离线恢复失败"
  }
  $firstLine = ($msg -split "(`r`n|`n)")[0]
  Write-Fail $firstLine
  exit $exitCode
}

$lines = @($stdoutText -split "(`r`n|`n)" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($lines.Count -lt 1) {
  Write-Fail "恢复核心未返回结果"
  exit 1
}

Write-Ok $lines[0]
if ($lines.Count -ge 2) {
  Write-Host $lines[1]
}
Write-Host "恢复前备份含敏感业务数据，请勿提交到 Git、日志、消息箱或公开同步目录。"
exit 0