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
| **Grok** | 按 task 限定文件实现、测试/lint/build、`status`/`review_request`/`result` | 擅自扩大范围、并行多项、提交密钥、未审查就推 `main` |

**远程分支基线**：`0e4a42c` — 修正响应矩阵版本写锁与同页串行保存（已推送 `origin/collab/grok-code-codex-review`）
**相对 `origin/main`**：含 WIP 汇总 `18b592d` + 上述 P0 修正。
**本地工作区**：P1 双浏览器 E2E 实现与文档校正**待最终 ack 后**再 commit/push；禁止直接合入 `main`。

## 当前队列

| 序 | 项 | 状态 | 执行方 |
|----|----|------|--------|
| P0 | 响应矩阵 `responseMatrixVersion` + 409 + 前端显式载入 | **已审查 / 已提交**（`0e4a42c`） | 完成 |
| P0 收尾 | DB 写锁 + 同页串行 + 并发测试 | **已审查 / 已提交**（同上） | 完成 |
| P1 | 响应矩阵双浏览器 E2E（409 主路径） | **已实现，待本轮最终 ack/commit** | Grok 待 Codex 最终 ack |
| P2 | 大项目 `response_match` 分批 | 先设计/测试矩阵，等单独 task | Codex 设计确认 → Grok |
| P3 | 整章布局/最小标题左栏 | 等用户效果图 | 双方待命 |
| P4 | 外部标讯源 | 等安全设计与来源批准 | 双方待命 |
| P5 | RAG/生产化 | 后置 | 双方待命 |

## 分支纪律

1. **只在本分支**落地协作期代码；合入 `main` 须审查通过且用户明确要求。  
2. **一事一 task**：Grok 不并行扩 scope。  
3. **消息箱**：`.agent-collaboration/messages/` 仍 Git 忽略；正文禁止密钥。  
4. **验收默认**：`pytest`（**127 passed**）、`frontend npm run lint`、`npm run build`、`npm run test:e2e:matrix`、`git diff --check`。
5. **提交**：须 Codex 对对应 `review_request` 明确 `ack`（或用户书面指令）后再 commit/push。

## Grok 当前待命动作

- 文档与 E2E 已实现，待最终 `ack` 后再 commit/push。
- 无新 task 不新开 P2–P5。
- 推送远程：仅在最终 ack 或用户确认后执行 `git push origin collab/grok-code-codex-review`。
