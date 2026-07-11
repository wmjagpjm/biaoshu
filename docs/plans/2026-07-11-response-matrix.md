<!--
模块：响应矩阵实施计划
用途：记录技术要求/评分点到大纲和章节的可追溯映射 v1 范围。
对接：editor-state responseMatrix、技术标分析步、HANDOFF-next.md
二次开发：后续多端冲突处理和端到端用例必须延续 sourceKey、生成快照与有效引用语义
-->

# 响应矩阵 v1 实施计划

> **协作约定：** Codex 负责实现与验证；Grok 负责只读审查和反驳。本计划不创建 Git 提交，除非用户明确要求。

**目标：** 在技术标分析步建立“技术要求/评分点 → 大纲节点/章节正文”的可追溯响应矩阵，让用户能人工确认每条要求是否已覆盖，并在删除大纲或章节后自动识别失效引用。

**架构：** 响应矩阵作为 editor-state 的 `responseMatrix` 字段持久化。后端负责字段存储、旧 SQLite 补列、输入规范化、失效引用收敛和 `response_match` 待确认建议任务；前端负责从分析结果生成稳定来源键、合并用户已有映射、展示覆盖状态、编辑入口及逐条应用建议。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、React、TypeScript、pytest、Vite。

---

## 已冻结的 v1 契约

- 矩阵来源只取技术标分析结果中的技术要求和评分点，不新增独立采集来源。
- `sourceKey` 是稳定合并键，不能依赖展示排序；分析结果重排时不得错绑用户已勾选的大纲或章节。
- `status` 仅允许 `uncovered`、`partial`、`covered`、`waived`；非豁免项没有有效链接时自动降级为 `uncovered`。
- `responseMatrix: null` 表示不更新该字段，显式 `[]` 才清空矩阵。
- 前后端都要过滤已删除的大纲节点和章节 ID，覆盖统计只认有效引用。
- `response_match` 只把模型建议写入任务结果；前端必须人工勾选应用，且仅合并自建议生成后未被人工改动的行。`waived`、备注和已保存的非 `uncovered` 状态不被建议覆盖。
- 本轮不做多端冲突合并和端到端 UI 自动化。

## 任务拆分

1. 后端扩展 editor-state：新增 `response_matrix_json` 字段、schema 映射、旧库补列、规范化函数和失效引用收敛。
2. 前端扩展技术标编辑态：新增响应矩阵类型、`responseMatrix` 工具函数、分析结果合并、持久化写回和删除大纲/章节后的重算。
3. 增加 `ResponseMatrixPanel`：展示技术要求/评分点、覆盖状态、章节/大纲勾选、备注和失效引用提示。
4. 覆盖测试：空 GET、往返、部分更新隔离、`null` 不清空、`[]` 清空、坏 JSON 不 500、旧库补列、死引用清理和降级。
5. 交给 Grok 做差异复审，优先检查防抖写回清空、来源重排错绑、死引用假覆盖和后端坏数据容错。
6. Word 导出联动：技术标导出时再次收敛失效关联，输出中文关联位置的响应矩阵表；商务标保持隔离。
7. 智能建议：新增 `response_match` 异步任务，限制模型候选范围并规范化结果；前端逐条勾选后以快照校验和关联合并写回。

## 执行记录（2026-07-11）

- 已完成后端 `responseMatrix` 全链路：模型字段、schema、GET/PUT 映射、旧 SQLite 补列、坏 JSON 容错、死引用过滤和状态降级。
- 已完成前端响应矩阵：稳定 `sourceKey`、分析结果合并、有效引用统计、面板编辑和 editor-state 防抖持久化。
- 已完成技术标 Word 导出联动：导出前再次收敛失效关联，按模板表格输出类型、来源、权重、状态、关联位置和备注；商务标不输出该章节，内部 ID 不出现在文档。
- 已完成智能建议 v1：`response_match` 仅返回待确认建议；后端过滤未知来源/非法 ID、每个 `sourceKey` 只保留最佳建议且禁止 `waived`；前端逐条应用并以生成快照跳过人工修改过的行，最后再次收敛失效引用。
- 已补 `backend/tests/test_response_matrix.py`，专项 9 项通过；后端全量最终为 **123 passed**。
- 前端 `npm run build` 通过，仅保留既有单包体积警告；`python -m compileall app scripts` 与 `git diff --check` 通过。
- `npm run lint` 仍有 6 个既有 Hooks 规则错误（`projectStore.ts` 4 处、`useWorkspaceSettings.ts` 2 处），不由本轮响应矩阵改动引入。
- Grok 对智能建议首轮指出“应用后空关联状态”风险；经确认应用末尾与手动编辑/API 回读共用收敛函数后，复审结论为无 P0/P1。剩余风险为多端 last-write-wins、输入候选截断、分析刷新导致批量失效需人工确认、缺少端到端用例。
