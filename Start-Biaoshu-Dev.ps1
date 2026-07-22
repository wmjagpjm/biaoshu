# 模块：根启动入口薄委托（all）
# 用途：转发至 tools/v1-ops/Start-Biaoshu-Dev.ps1，无第二套算法
# 对接：V1-K 静默启动诚实诊断

param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]]$AllArgs
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$TrueSource = Join-Path $Root 'tools\v1-ops\Start-Biaoshu-Dev.ps1'
if (-not (Test-Path -LiteralPath $TrueSource -PathType Leaf)) {
  Write-Host 'status_write_failed'
  exit 1
}

# 以数组形式转发，兼容真源 ValueFromRemainingArguments 解析
$forward = New-Object System.Collections.Generic.List[string]
$forward.Add('-Component') | Out-Null
$forward.Add('all') | Out-Null
foreach ($a in @($AllArgs)) {
  if ($null -ne $a -and [string]$a -ne '') {
    $forward.Add([string]$a) | Out-Null
  }
}
$arr = $forward.ToArray()
& $TrueSource @arr
exit $LASTEXITCODE
