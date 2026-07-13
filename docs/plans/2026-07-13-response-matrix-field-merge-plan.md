<!--
模块：响应矩阵字段级三方合并方案
用途：锁定 base 快照、可编辑字段原子三方比较、仅矩阵 PUT、冲突显式选择与测试矩阵。
对接：frontend responseMatrix.ts / useTechnicalPlanEditors / ResponseMatrixPanel；editor-state 409。
二次开发：禁止 deep-merge/并集/静默覆盖；智能建议语义不变；包 8/9 不在本方案。
-->

# 响应矩阵字段级三方合并（阶段 4 功能包 7 MVP）

> **状态（2026-07-13）**：P1 返修已完成 / 待 Codex 复审（未授权 commit/push）。  
> **基线**：`1289c92`（实现响应矩阵源分页调用；包 6 已完成并推送）。  
> **分支**：`collab/grok-code-codex-review`。  
> **P1 返修依据**：`msg_808492b626f94bdc915bb67d669fce01`。

## 1. 问题

多端同时编辑响应矩阵时，仅靠 `responseMatrixVersion` 409 只能「整表载入远端」或卡住本地，无法在不同字段各自合法修改时安全合并，也不能在同字段冲突时让用户显式选择。

## 2. 冻结契约

| 项 | 规则 |
|----|------|
| 对齐键 | 仅 `sourceKey` |
| 不参与合并的元数据 | `id` / `sourceIndex` / `kind` / `sourceText` / `weight`（分析派生） |
| 可编辑字段 | 仅 `notes`、`status`、`chapterIds`、`outlineNodeIds` |
| 比较 | 四字段均为**原子**比较；数组去重排序后比；`notes` 全字符串、**不 trim** |
| 冲突 | 同字段双端相对 base 改成不同值 = 冲突；禁止并集 / deep-merge / 静默覆盖 |
| 合并后 | 必须跑既有 `reconcileResponseMatrixLinks`；非 waived 无有效链接 → `uncovered`；有链接不自动升 `covered`；waived 不因空链接降级 |
| 行集 | base 有行且一端删除、另一端修改 = 行冲突；仅一端新增或未改按三方规则；面板不直接删行 |
| 409 | **禁止**自动重试/自动保存；生成合并预览 |
| 无冲突预览 | 展示「可安全合并」；用户点「应用合并」才写 |
| 有冲突预览 | 逐字段展示 base/local/remote；须全部显式「采用本地」或「采用远端」后应用可用；**不得预选** |
| 放弃本地 | 保留「重新载入远端矩阵」 |
| 应用 PUT | 请求体**只能**含 `responseMatrix` + `responseMatrixVersion`（409 的 remoteVersion） |
| 合并成功后 | setState 后**跳过一次**普通防抖全量 PUT；本地 localStorage 仍更新；禁止把 analysis/outline/chapters/facts 回写远端 |
| 再次 409 | **清空** `mergePreview` 与选择；禁止用旧预览+新 version 再写；提示「重新载入远端矩阵」后重进合并；不自动循环 |
| 其它失败 | 网络/非 409 HTTP 失败：可保留预览与中文可恢复提示 |
| base 快照 | 深拷贝仅在成功 GET、成功带矩阵 PUT、显式载入远端时更新；随 projectId 切换/卸载清空 |
| base 不匹配 | `baseVersion !== 本次请求版本` 或请求后本地又改 → **不**生成三方预览，退回旧冲突条 |
| 智能建议 | 语义不变；生成中/待确认不自动应用；合并后旧 `base` 门闩仍决定是否跳过 |

## 3. 实现边界

| 文件 | 职责 |
|------|------|
| `lib/responseMatrix.ts` | 无副作用 `threeWayMergeResponseMatrix` / `resolveResponseMatrixThreeWayChoices` / 比较与克隆 |
| `hooks/useTechnicalPlanEditors.ts` | `matrixBaseRef` + `baseVersion`；409 条件生成预览；`applyResponseMatrixMerge` 仅矩阵 PUT |
| `components/ResponseMatrixPanel.tsx` | 受控：冲突条 + 预览 + 字段选择 + 应用禁用规则 |
| `pages/TechnicalPlanWorkspace.tsx` | 只传新增 props |
| `e2e/response-matrix-field-merge.spec.ts` | 双上下文验收 |
| 文档 | 本计划 + HANDOFF + 路线图 + 联调清单 |

**禁止**：改 backend/API/DB、全局 Playwright、依赖、其它 src；真实模型/Key/外网；未 ack 的 commit/push。

## 4. 核心流程

1. 成功同步矩阵 → 深拷贝写入 `matrixBaseRef`，记录 `baseVersion`。  
2. 防抖保存带矩阵 PUT；记录 `matrixAtRequest` 与 `versionAtRequest`。  
3. 409：若 `baseVersion === versionAtRequest` 且本地相对请求矩阵未再改 → 三方合并预览；否则仅旧冲突提示。  
4. 用户「应用合并」→ 解析选择 → reconcile → PUT 仅矩阵+远端版本。  
5. 成功：更新本地矩阵、version、base，解除 block；失败：保留预览与中文错误。

## 5. 测试矩阵

### E2E（`response-matrix-field-merge.spec.ts`）

| 场景 | 期望 |
|------|------|
| A 改 notes，B 旧版改 chapterIds | B 见「可安全合并」；应用后 GET 同时保留 A notes 与 B 链接；合并相关 PUT 无 analysis/outline/chapters/facts；超过 800ms 防抖窗仍无全量回写 |
| A/B 同 notes 不同值 | 冲突对照；未选时应用禁用；选远端后最终值=远端 |
| 应用 PUT 被 mock 409 | 错误提示；PUT 仅 1 次；预览与应用按钮消失；须重新载入；库未写 B 的章节 |

### 回归

- `npm run test:e2e:matrix`（原 4 + field-merge）
- `npm run lint` / `npm run build`
- `backend .venv\Scripts\python.exe -m pytest -q`
- `git diff --check`

## 6. 明确不做

- 后端三方合并算法或 API 新路由
- 自动合并并静默保存
- 字段并集 / deep-merge
- 智能建议字段级自动合并
- 包 8 可插拔解析、包 9 交付增强
