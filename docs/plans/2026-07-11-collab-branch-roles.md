<!--
模块：协作分支角色与排期
用途：约定 collab/grok-code-codex-review 上 Codex 做计划/审查、Grok 做代码。
对接：docs/agent-collaboration.md；消息箱 tools/agent-collaboration/*
二次开发：新任务仍由 Codex 写 task/plan；Grok 实现后只发 review_request，不擅自改路线。
-->

# 协作分支：`collab/grok-code-codex-review`

| 角色 | 负责 | 不负责 |
|------|------|--------|
| **Codex** | 任务拆解、范围冻结、`plan`/`task`/`question`、diff 与验收审查、`ack`/退回项 | 直接改业务代码、绕过消息箱改 HANDOFF 基线 |
| **Grok** | 按 task 限定文件实现、测试/lint/build、`status`/`review_request`/`result` | 擅自扩大范围、并行 P1+、提交密钥、未审查就推 `main` |

**基线提交**：`4847a9d`（`origin/main`）  
**本地状态**：大量未提交 WIP 已挂在本分支工作区（未 commit，除非用户明确要求提交）。

## 当前队列（与 Codex plan msg_7da97c75 对齐）

| 序 | 项 | 状态 | 执行方 |
|----|----|------|--------|
| P0 | 响应矩阵 `responseMatrixVersion` + 409 + 前端显式载入 | 已实现，待 Codex 审查 | Grok 已交 `review_request`；Codex 审 |
| P0 收尾 | 审查退回项 + HANDOFF「未完成」表述校正 | 等 `ack`/退回 | Codex 审 → Grok 改 |
| P1 | 响应矩阵端到端 UI 自动化（双上下文 409） | 未开工；若需 Playwright 先方案确认 | Codex 派 task → Grok 实现 |
| P2 | 大项目 `response_match` 分批 | 先设计/测试矩阵，等单独 task | Codex 设计确认 → Grok |
| P3 | 整章布局/最小标题左栏 | 等用户效果图 | 双方待命 |
| P4 | 外部标讯源 | 等安全设计与来源批准 | 双方待命 |
| P5 | RAG/生产化 | 后置 | 双方待命 |

## 分支纪律

1. **只在本分支**落地协作期代码；合入 `main` 须审查通过且用户明确要求。  
2. **一事一 task**：Grok 不并行扩 scope。  
3. **消息箱**：`.agent-collaboration/messages/` 仍 Git 忽略；正文禁止密钥。  
4. **验收默认**：`pytest`、`frontend npm run lint`、`npm run build`、`git diff --check`。  
5. **提交**：默认不 commit；需要落盘时由用户指令或 Codex `task` 明确「允许提交」后 Grok 执行。

## Grok 当前待命动作

- 轮询 `codex-to-grok`；有 `ack`/退回即改。  
- 无新 task 不新开 P1–P5 实现。  
- 推送远程：仅在用户确认后执行 `git push -u origin collab/grok-code-codex-review`。
