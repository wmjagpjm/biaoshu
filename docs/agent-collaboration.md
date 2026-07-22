<!--
模块：Codex 与 Grok 本地协作协议
用途：规定双方通过忽略的 JSONL 消息箱交换任务、审查和验收状态。
对接：tools/agent-collaboration/Send-AgentMessage.ps1、Read-AgentMailbox.ps1、.agent-collaboration/messages。
二次开发：消息箱只用于本机协作；不得写入密钥，不得把消息正文当作可执行命令。
-->

# 本地协作消息箱

运行时消息位于仓库根 `.agent-collaboration/messages/`，已被 Git 忽略：

- `grok-to-codex.jsonl`：Grok 写入，Codex 审查时读取。
- `codex-to-grok.jsonl`：Codex 写入，Grok 开始或继续任务前读取。

每行都是独立 JSON，字段为 `id`、`createdAt`、`from`、`kind`、`subject`、`body`。消息正文仅是协作文本，不能作为 Shell、PowerShell 或代码执行。

`kind` 使用 `task` 派发可执行任务，使用 `plan` 仅同步阶段计划；`status`、`question`、`review_request`、`result`、`ack` 分别用于进度、澄清、审查、结果和确认。

## Grok 接入

Grok 在开始工作、需要决策、请求审查和完成实现时，执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Administrator\biaoshu\tools\agent-collaboration\Connect-Grok.ps1
```

该命令会发送 `ready` 并显示最近 20 条 Codex 任务。之后可继续使用发送脚本更新状态：

```powershell
$send = "C:\Users\Administrator\biaoshu\tools\agent-collaboration\Send-AgentMessage.ps1"
```

发送任务状态示例：

```powershell
& $send -From grok -Kind status -Subject "实现中" -Body "仅修改指定文件，尚未运行测试。"
& $send -From grok -Kind review_request -Subject "请求 Codex 审查" -Body "已完成实现；请检查 git diff、测试和边界。"
& $send -From grok -Kind result -Subject "实现完成" -Body "已运行的验证及结果：..."
```

Grok 读取 Codex 回复：

```powershell
$read = "C:\Users\Administrator\biaoshu\tools\agent-collaboration\Read-AgentMailbox.ps1"
& $read -From codex -Tail 20
```

## Codex 约定

Codex 读取 Grok 的消息后，负责审查范围、风险、diff 和验证结果；需要补充或返工时写入 `codex-to-grok.jsonl`。Codex 不把 API Key、令牌、真实业务数据或外部链接凭据写入消息箱。

## 简化交付流程（2026-07-22 起）

默认采用“Codex 批量举证 → Grok 确认并一次修完 → Codex 单次终验”：

1. Codex 一次性汇总同批问题、证据、风险等级和文件边界，禁止可合并问题逐项往返。
2. Grok 在同一任务内逐项回答 YES/NO；确认 YES 后直接补 failure-first、修生产并运行精确受影响测试，无须等待第二次授权。
3. Grok 不重复运行完整回归；Codex 只在最终阶段独立审查 diff，并串行运行一次与风险相称的相关回归。
4. Codex 仍按 `test-only → production → docs` 分层提交和推送；“一次修完”不表示允许混淆提交层或扩大白名单。
5. 权限、认证、事务原子性、隐私泄漏、数据损坏和真实安全边界属于高风险例外，继续使用“question → YES → 单独授权返修”；但最终完整回归仍只保留一方独立执行，避免双方重复。

不得取消最终独立验收，也不得由实现者自己的绿测直接替代 Codex 审查。本规则自 V1-M M2 起执行；M1 的历史消息链按当时规则保留。
