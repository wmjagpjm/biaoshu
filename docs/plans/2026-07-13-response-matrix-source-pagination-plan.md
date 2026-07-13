<!--
模块：响应矩阵智能建议来源分页方案
用途：锁定 sourceBatchIndex 与候选分批的契约、成功/失败/取消规则、测试矩阵与明确不做项。
对接：backend task_service._run_response_match；frontend TechnicalPlanWorkspace；docs/HANDOFF-next.md。
二次开发：字段级合并、取消中断 E2E、真实 Key/外网不在本方案；改契约须同步单测与 E2E。
-->

# 响应矩阵智能建议：来源分页（阶段 4 功能包 6）

> **状态（2026-07-13）**：实现中 / 待 Codex 审查（未授权 commit/push）。  
> **基线**：`460097a`（阶段 4 包 5 已完成并推送）。  
> **分支**：`collab/grok-code-codex-review`。

## 1. 问题

`response_match` 原先固定 `sources[:80]`。超过 80 条非 waived 来源时，第 81 条及以后永不进入模型 prompt，智能建议静默截断。

## 2. 分页契约

| 字段 | 含义 |
|------|------|
| `payload.sourceBatchIndex` | 0-based 来源页；仅接受非负 int（排除 bool）；缺失/null/bool/float/字符串/负值 → 0 |
| `payload.candidateBatchIndex` | 既有候选批；规则不变 |
| 页大小 | `_RESPONSE_MATCH_SOURCE_LIMIT = 80` |
| `sourceBatchCount` | `ceil(非 waived 来源数 / 80)`，至少 1 |
| 越界 | `sourceBatchIndex >= sourceBatchCount` → 任务 **failed**，模型 0 次，不写 editor-state |
| `prompt_sources` | `sources[i*80:(i+1)*80]` |
| 候选切片 | 仍只由 `candidateBatchIndex` 决定；ID 白名单不放宽 |
| result | 增加 `sourceBatchIndex` / `sourceBatchCount` / `isLastSourceBatch`；保留 `sourceCount`（本页实际条数）、`totalSourceCount` 与候选元数据 |
| 写入边界 | 只写 `ProjectTask.result_json`，禁止写 editor-state |

单次模型调用来源条数 ≤ 80，防止上下文顶满。

## 3. 前端串行规则

- **外层**来源页 × **内层**候选批 await 串行。
- 每个请求 payload **同时**带 `sourceBatchIndex` 与 `candidateBatchIndex`。
- **停止条件**：当前候选末批 **且** 当前来源末页；不得在来源页 0 的候选走完后提前结束。
- 合并仍用 `mergeResponseMatrixSuggestions`（整条择优，禁止字段级合并）。
- `matchSessionRef`：取消 / 换项目 / 迟到结果不污染。
- 失败或取消：停止剩余页/批，保留已累计建议。
- 进度展示：来源页 + 候选批 + 累计条数；禁止 fixed sleep。
- 旧后端缺 source 元数据时：兼容为单页。

## 4. 成功 / 失败 / 取消

| 场景 | 行为 |
|------|------|
| 成功末页末批 | 展示累计建议；应用前 editor-state 不变 |
| 来源页越界 | failed；中文错误含「来源批次越界」 |
| 候选批越界 | failed；既有语义 |
| 中途失败 | 停后续；保留已成功批建议 |
| 取消 | 停后续；保留已成功批建议 |

## 5. 测试矩阵

### 后端（`test_response_matrix.py`）

- 81 来源：页 0=80、页 1=1；`sourceBatchCount=2`；prompt 不混页。
- 来源页 × 候选批嵌套；本页候选 ID 校验不放宽。
- 来源页越界：failed、模型 0 次、state 不变。
- 非法 `sourceBatchIndex` 钳制为 0；候选非法回归。
- ≤80 来源且不传 source 批号：旧路径回归。

### 前端 E2E（`response-matrix-source-pagination.spec.ts`）

- 种子 81 非 waived + 本机 mock LLM。
- 「智能建议」后第 2 页唯一来源出现在待确认卡。
- 进度含「来源页」；应用前 GET editor-state 与基线一致。
- `expect.poll` / UI 断言；禁止 fixed sleep / 真实 Key。

### 回归

- `backend .venv\Scripts\python.exe -m pytest -q`
- `frontend npm run test:e2e:matrix`（原 3 + 本 spec）
- `npm run lint` / `npm run build`
- `git diff --check`

## 6. 明确不做

- 字段级智能合并（包 7）
- 取消中断 / 409 与建议交叉 E2E
- 改全局 Playwright 配置、DB 结构、API 路由
- 真实模型 / Key / 外网
- 未获 Codex ack 前的 commit / push

## 7. 允许改动文件

- `backend/app/services/task_service.py`
- `backend/tests/test_response_matrix.py`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/e2e/response-matrix-source-pagination.spec.ts`（新）
- `frontend/package.json`（仅 matrix 脚本追加）
- `docs/plans/2026-07-13-response-matrix-source-pagination-plan.md`（本文件）
- `docs/plans/2026-07-12-bid-writer-roadmap.md`
- `docs/HANDOFF-next.md`
- `docs/integration-checklist.md`
