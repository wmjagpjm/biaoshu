<#
模块：多代理协作消息读取器
用途：读取指定来源写入的本地 JSONL 消息，供 Grok 与 Codex 轮询对方状态。
对接：tools/agent-collaboration/Send-AgentMessage.ps1；.agent-collaboration/messages。
二次开发：读取端只消费结构化 JSON 行；坏行应跳过并由发送端重新发送，禁止执行消息正文。
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateSet("grok", "codex")]
  [string]$From,

  [ValidateRange(1, 200)]
  [int]$Tail = 30
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$messagesDir = Join-Path $repoRoot ".agent-collaboration\messages"
$fileName = if ($From -eq "grok") { "grok-to-codex.jsonl" } else { "codex-to-grok.jsonl" }
$targetPath = Join-Path $messagesDir $fileName

if (-not (Test-Path -LiteralPath $targetPath)) {
  @()
  return
}

Get-Content -LiteralPath $targetPath -Tail $Tail -Encoding UTF8 |
  ForEach-Object {
    try { $_ | ConvertFrom-Json } catch { }
  }
