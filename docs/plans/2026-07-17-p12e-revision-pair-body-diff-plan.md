# P12E-B 双修订正文差异后端实施计划

> **状态：已完成（2026-07-17）**。Grok 按本计划在四文件白名单内实现；Codex 已独立审查、验收、中文文档闭环和提交推送。实现提交=`5a5b08a`。

**目标：** 为同一工作空间、同一项目的两条历史修订提供只读、有界、脱敏的章节正文差异 API。
**架构：** 复用 P12C-C1 的双修订快照读取与完整性校验，复用 P12E-A 的章节配对、完整值判等、difflib 前截断和展示预算；路由只负责作用域/错误映射与精确 schema 投影，不读取当前 editor-state。
**技术栈：** FastAPI、Pydantic、SQLAlchemy、Python 标准库 `difflib`、pytest/SQLite。

---

## 1. 开工与白名单核验

文件：无代码变更。
步骤：

1. 核对分支 `collab/grok-code-codex-review`、HEAD/远端一致、工作区干净；读取 P12E-B 契约和 P12E-A 实现。
2. 只允许四文件：`backend/app/api/schemas.py`、`backend/app/api/editor_state_revisions.py`、`backend/app/services/editor_state_revision_body_diff_service.py`、`backend/tests/test_p12e_revision_pair_body_diff.py`。
3. 不得修改 P12E-A 现有路由响应，不得新增前端或数据库文件。

## 2. 先写真实红测

文件：创建 `backend/tests/test_p12e_revision_pair_body_diff.py`。
步骤：

1. 使用真实 SQLite、项目和两条有效修订 fixture，写 changed、added、removed 与同修订一致的 HTTP 断言；精确验证六键/五键/二键、前后章节计数、`sameBody` 与计数一致性。
2. 写跨项目、跨工作空间、第一条不存在、第二条不存在、第一条损坏、第二条损坏的固定状态/错误码/`no-store` 断言；响应不得泄漏 ID、路径、正文或异常原文。
3. 写无 query/body 的路由探针与五域零写断言；写 101 个差异章 difflib 调用上限和“前 100 章相同、后续才不同”完整值反假绿断言。
4. 只运行新增测试，记录真实失败原因；不得因为生产路由尚不存在而把收集失败冒充业务红测。

## 3. 扩展服务层为双快照比较

文件：修改 `backend/app/services/editor_state_revision_body_diff_service.py`。
步骤：

1. 抽取一个内部纯比较入口，参数为 `before_snapshot`、`after_snapshot`，返回 `before_chapter_count`、`after_chapter_count` 与 P12E-A 相同的有界 items/flags；保持 P12E-A `compare_revision_body_with_current` 外部行为不变。
2. 新增双修订服务入口，分别调用 `get_editor_state_revision(db, workspace_id, project_id, before_id)` 和 `...after_id`；不读取当前 editor-state，不打开写事务、不加锁、不发 HTTP。
3. 两个快照都必须是已校验 dict；只取 `chapters`，复用唯一 ID/序号配对、完整正文判等、100 章 difflib cap、20,000/240/80/2,000/120,000 预算。
4. 任一历史服务层 404/corrupt 原样上抛；其他异常统一 `editor_state_revision_body_diff_failed`，不得反射内部细节。
5. 运行新增后端专项，确认红测转绿；再运行 P12E-A 专项确认旧 current 路由无回归。

## 4. 增加精确响应模型与路由

文件：修改 `backend/app/api/schemas.py`、`backend/app/api/editor_state_revisions.py`。
步骤：

1. 增加 pair body-diff 顶层模型，固定 `sameBody/changedChapterCount/beforeChapterCount/afterChapterCount/truncated/items` 六键；复用或独立声明严格的 item/hunk 模型，`extra="forbid"`。
2. 增加唯一 GET 路由 `/projects/{project_id}/editor-state-revisions/{before_revision_id}/body-diff/{after_revision_id}`，声明 response model 并固定 `no-store`。
3. 路由只调用服务层、映射既有历史错误和固定正文差异错误、按 schema 投影；不得把任一 ID/版本/原始快照放入成功或错误响应。
4. 运行新增 HTTP 专项与 `py_compile`，确认 P12E-A 原路由仍保持原六键 `currentChapterCount/targetChapterCount`。

## 5. 受限审查与回归

步骤：

1. 逐行检查双修订作用域、快照完整性、纯比较入口、章节配对、完整值判等、difflib 前 cap、展示预算、异常脱敏和五域零写。
2. 运行 `backend/.venv/Scripts/python.exe -m pytest -q tests/test_p12e_revision_pair_body_diff.py tests/test_p12e_revision_body_diff.py`。
3. 串行运行 P12D/P12C 受影响回归与后端全量 `backend/.venv/Scripts/python.exe -m pytest -q`；保留真实计数和唯一既有弃用告警。
4. 运行两生产文件/服务 `py_compile`、`git diff --check`、精确四文件白名单、暂存区为空和工作区产物检查。
5. Grok 只发 `review_request`，不得提交或推送；Codex 发现越界只通过消息箱退回同一任务定点修复。

## 6. Codex 文档闭环与提交

全部独立验收已通过：P12E-B/P12E-A/P12D-P12C **13/23/50 passed**，合并专项 **86 passed**，后端全量 **867 passed**，仅 1 条既有 Starlette/httpx 弃用告警；`py_compile`、`git diff --check`、四文件白名单和空暂存区通过。Codex 已更新 `docs/HANDOFF-next.md`、路线图、`docs/integration-checklist.md`、本契约和本计划，并以中文提交信息提交推送。实现边界继续明确：本包没有前端入口、分页、搜索、恢复、删除、导出、分享或多人协作。
