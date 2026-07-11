<#
模块：Grok 协作消息箱接入器
用途：让 Grok 主动登记到本地消息箱，并读取 Codex 已分配的待办任务。
对接：Send-AgentMessage.ps1、Read-AgentMailbox.ps1、docs/agent-collaboration.md。
二次开发：接入只登记状态和读取任务，不执行消息正文；执行范围仍以 Grok 收到的明确任务约束为准。
#>

[CmdletBinding()]
param(
  [string]$Body = "Grok 已接入协作消息箱，等待 Codex 审查任务。"
)

$toolDir = $PSScriptRoot
$send = Join-Path $toolDir "Send-AgentMessage.ps1"
$read = Join-Path $toolDir "Read-AgentMailbox.ps1"

& $send -From grok -Kind ready -Subject "Grok 已接入" -Body $Body
& $read -From codex -Tail 20
