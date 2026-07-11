<#
模块：Codex 消息箱 10 秒轮询
用途：检测 codex-to-grok 新消息并 stdout 输出，供 Grok 持续领取任务。
对接：Read-AgentMailbox.ps1；.agent-collaboration/messages/.grok-poll-state.txt
二次开发：只读消息，不执行正文；禁止打印密钥。仅在 NEW_MSG 时输出，避免心跳刷屏。
#>
$ErrorActionPreference = "Continue"
$toolDir = $PSScriptRoot
$read = Join-Path $toolDir "Read-AgentMailbox.ps1"
$repoRoot = Split-Path -Parent (Split-Path -Parent $toolDir)
$stateFile = Join-Path $repoRoot ".agent-collaboration\messages\.grok-poll-state.txt"
[void][System.IO.Directory]::CreateDirectory((Split-Path $stateFile))

$lastSeen = ""
if (Test-Path -LiteralPath $stateFile) {
  $raw = Get-Content -LiteralPath $stateFile -Raw -ErrorAction SilentlyContinue
  if ($null -ne $raw) {
    $lastSeen = $raw.Trim()
  }
}

Write-Output ("POLL start last={0} at={1}" -f $lastSeen, (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))

while ($true) {
  try {
    $msgs = @( & $read -From codex -Tail 30 )
    $latest = $null
    foreach ($m in $msgs) {
      if (($null -ne $m) -and ($null -ne $m.id) -and ($m.id -ne "")) {
        $latest = $m
      }
    }

    if (($null -ne $latest) -and ($latest.id -ne $lastSeen)) {
      $lastSeen = [string]$latest.id
      $utf8 = New-Object System.Text.UTF8Encoding $false
      [System.IO.File]::WriteAllText($stateFile, $lastSeen, $utf8)

      $bodyText = ""
      if ($null -ne $latest.body) {
        $bodyText = ([string]$latest.body) -replace "\s+", " "
        $bodyText = $bodyText.Trim()
        if ($bodyText.Length -gt 500) {
          $bodyText = $bodyText.Substring(0, 500)
        }
      }

      $line = "NEW_MSG id={0} kind={1} subject={2} body={3}" -f @(
        $latest.id
        $latest.kind
        $latest.subject
        $bodyText
      )
      Write-Output $line
    }
  }
  catch {
    Write-Output ("POLL error: {0}" -f $_.Exception.Message)
  }

  Start-Sleep -Seconds 10
}
