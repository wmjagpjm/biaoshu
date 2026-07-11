<#
模块：多代理协作消息发送器
用途：向 Grok 与 Codex 的本地 JSONL 消息箱原子追加结构化消息。
对接：tools/agent-collaboration/Read-AgentMailbox.ps1；.agent-collaboration/messages。
二次开发：新增消息类型须同步更新协议文档；禁止在正文、主题或运行目录写入 API Key、令牌和密钥。
#>

[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [ValidateSet("grok", "codex")]
  [string]$From,

  [Parameter(Mandatory)]
  [ValidateSet("ready", "task", "plan", "status", "question", "review_request", "result", "error", "ack")]
  [string]$Kind,

  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$Subject,

  [Parameter(Mandatory)]
  [ValidateNotNullOrEmpty()]
  [string]$Body
)

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$messagesDir = Join-Path $repoRoot ".agent-collaboration\messages"
$fileName = if ($From -eq "grok") { "grok-to-codex.jsonl" } else { "codex-to-grok.jsonl" }
$targetPath = Join-Path $messagesDir $fileName

[void][System.IO.Directory]::CreateDirectory($messagesDir)
$record = [ordered]@{
  id = "msg_" + [Guid]::NewGuid().ToString("N")
  createdAt = [DateTime]::UtcNow.ToString("o")
  from = $From
  kind = $Kind
  subject = $Subject.Trim()
  body = $Body.Trim()
}
$line = $record | ConvertTo-Json -Compress -Depth 4
$mutex = [System.Threading.Mutex]::new($false, "Local\BiaoshuAgentMailbox")

try {
  if (-not $mutex.WaitOne([TimeSpan]::FromSeconds(5))) {
    throw "协作消息箱正忙，请稍后重试"
  }
  $encoding = [System.Text.UTF8Encoding]::new($false)
  $payload = $line + [System.Environment]::NewLine
  [System.IO.File]::AppendAllText($targetPath, $payload, $encoding)
  $record | ConvertTo-Json -Depth 4
} finally {
  if ($mutex) {
    try { $mutex.ReleaseMutex() } catch { }
    $mutex.Dispose()
  }
}
