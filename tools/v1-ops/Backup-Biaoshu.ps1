# 模块：标书离线备份 PowerShell 包装
# 用途：参数规范化后调用 Python 标准库核心；成功仅输出最终目录与固定敏感提示
# 安全：不暴露跳过端口/哈希/完整性的绕过开关

[CmdletBinding()]
param(
  [string]$DestinationRoot,
  [switch]$IncludeSemanticModels
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $ScriptDir "..\.."))
$PyCore = Join-Path $ScriptDir "biaoshu_backup.py"
$VenvPython = Join-Path $RepoRoot "backend\.venv\Scripts\python.exe"

function Write-Fail([string]$Message) {
  Write-Host $Message -ForegroundColor Red
}

function Write-Ok([string]$Message) {
  Write-Host $Message -ForegroundColor Green
}

function ConvertTo-WindowsArgument([string]$Argument) {
  # 可靠 Windows argv quoting（兼容 PowerShell 5.1 / CommandLineToArgvW）
  # 拒绝双引号、NUL、换行；有空白才加双引号；闭合引号前连续反斜杠双写
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
  # A7：ProcessStartInfo 原始双流；禁用仅 Core 才有的参数列表 API
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
    # 先异步读两流再 WaitForExit，避免任一管道填满死锁
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
  Write-Fail "备份核心模块不存在"
  exit 1
}

if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
  $parent = Split-Path -Parent $RepoRoot
  $DestinationRoot = Join-Path $parent "biaoshu-backups"
} else {
  $DestinationRoot = [System.IO.Path]::GetFullPath($DestinationRoot)
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
  "--destination-root", $DestinationRoot
)
if ($IncludeSemanticModels) {
  $argList += "--include-semantic-models"
}

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
    Write-Fail "备份参数无效"
    exit 1
  }
  Write-Fail "无法启动备份核心"
  exit 1
}

if ($exitCode -ne 0) {
  # 失败只输出 stderr 首行固定中文，保留 exit code；不拼 python 前缀/traceback
  $msg = $stderrText.Trim()
  if ([string]::IsNullOrWhiteSpace($msg)) {
    $msg = "离线备份失败"
  }
  $firstLine = ($msg -split "(`r`n|`n)")[0]
  Write-Fail $firstLine
  exit $exitCode
}

$finalDir = $stdoutText.Trim()
if ([string]::IsNullOrWhiteSpace($finalDir)) {
  Write-Fail "备份核心未返回最终目录"
  exit 1
}

Write-Ok $finalDir
Write-Host "备份目录含敏感业务数据，请勿提交到 Git、日志、消息箱或公开同步目录。"
exit 0