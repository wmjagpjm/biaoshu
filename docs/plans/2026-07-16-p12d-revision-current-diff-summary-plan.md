<!--
模块：P12D-A 修订与当前状态差异摘要实施计划
用途：把只读比较服务、严格响应、失败语义与真实 SQLite 零写验证拆成 Grok 可执行步骤。
对接：docs/p12d-revision-current-diff-summary-contract.md、P12C 历史服务、editor-state 权威服务与后端测试。
二次开发：Grok 只实现四文件白名单并发送 review_request；不得提交、推送或扩到前端。
-->

# P12D-A 修订与当前状态差异摘要实施计划

> **给 Grok：** REQUIRED SUB-SKILL：按 `executing-plans` 逐项执行；先 failure-first，再最小实现。不得提交或推送。
> **完成状态**：冻结=`2cc6ee3`、实现=`9445fcc`，后端全量 **831 passed**；本计划已闭环，后续前端工作另立 P12D-B。

**目标：** 新增一个只读 API，精确比较所选修订与服务端当前 13 键状态，只返回变更字段名和两侧有界摘要。

**架构：** 新比较服务组合既有权威当前状态读取与 P12C-C1 目标修订重验，不修改两者；逐字段使用共享规范 JSON 比较，再生成固定六项摘要。既有修订路由只挂 GET 和响应模型，零数据模型、零事务写、零前端变化。

**技术栈：** Python 3.13、FastAPI、Pydantic、SQLAlchemy、SQLite、pytest。

---

## 1. 文件与总规则

只允许：

- 修改 `backend/app/api/schemas.py`
- 修改 `backend/app/api/editor_state_revisions.py`
- 新建 `backend/app/services/editor_state_revision_comparison_service.py`
- 新建 `backend/tests/test_p12d_revision_current_comparison.py`

新文件顶部必须有中文“模块 / 用途 / 对接 / 二次开发”四字段。生产代码不得导入测试；测试不得修改既有服务或通过 monkeypatch 绕开真实 SQLite 作用域/零写验证。禁止前端、依赖、配置、实体、迁移、文档、Git 暂存/提交/推送。

## 2. 任务 1：先写 API failure-first 专项

### 步骤 1：建立真实状态与修订 fixture

在新测试文件复用项目 API/服务创建真实技术与商务 editor-state，并通过既有 `record_editor_state_transition` 或生产写路径生成规范修订。夹具保存五域前快照：`project_editor_states` 行、`editor_state_revisions` 全部列、`editor_state_checkpoints`、项目关键列和认证审计行。

### 步骤 2：先写最小红测

先只写：同状态 comparison 应 200、精确三键、`sameState=true`、`changedFields=[]`、两侧摘要相同、`Cache-Control=no-store`。在生产未改时运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12d_revision_current_comparison.py --tb=line
```

预期：路由不存在而精确 404 或缺响应实现；记录 failed/passed 数与首个业务失败。不得先改生产，不得用 ImportError、SyntaxError、fixture 崩溃或缺依赖充当红测。

### 步骤 3：补齐反假绿用例

按契约 §6 补齐 13 键顺序、相同计数不同内容、JSON `true` 对数字 `1`、边界/越界、损坏/作用域/角色/no-store/固定 500/五域零写/AST 禁写。每个断言使用精确状态码、键集、数组和值；禁止 `>=400`、宽泛 2xx、`truthy`、空集合冒充来源隔离或只比计数。

## 3. 任务 2：实现严格 Schema

### 步骤 1：增加固定摘要模型

在 `schemas.py` 增加 comparison summary：仅 `outlineNodeCount/chapterCount/factCount/responseMatrixRowCount/businessEntryTotal/hasParsedMarkdown` 六键；所有计数为非负整数，禁止额外键。

### 步骤 2：增加固定比较响应

顶层仅 `sameState/changedFields/currentSummary/targetSummary`；`changedFields` 元素使用 13 键 `Literal`，顺序由服务保证；禁止 ID、版本、来源、时间或 snapshot 字段。运行专项，预期仍因服务/路由未实现失败，但 Schema 单测通过。

## 4. 任务 3：实现只读比较服务

### 步骤 1：读取两侧权威快照

- 当前侧：`editor_state_service.get_editor_state` → `extract_canonical_snapshot`。
- 目标侧：`editor_state_revision_history_service.get_editor_state_revision(...)["snapshot"]`。
- 不直接查询修订表、不复制 13 键字面量；字段序列只引用 `editor_state_service.CANONICAL_STATE_KEYS`。

### 步骤 2：逐字段规范比较

对每个固定键分别调用 `editor_state_service.canonical_snapshot_json({key: value})`，比较字符串；按权威键顺序收集差异。不得只比 Python `==`、版本、计数或长度。

### 步骤 3：有界生成六项摘要

实现迭代或有深度门的统计：节点预算 10,000、深度 32；数组和报价 rows 计数，解析正文只转布尔。超界、序列化或当前读取异常统一固定 comparison failed；历史服务业务错误原样保留。

### 步骤 4：证明服务零写

服务文件不得出现 `add/delete/flush/commit/rollback/refresh`、锁、审计、HTTP、检查点或 revision recorder 调用。运行纯服务与真实数据库专项。

## 5. 任务 4：挂载 GET 路由与固定错误

### 步骤 1：新增 comparison GET

在既有修订路由增加 `/{revision_id}/comparison`，复用当前依赖与 `_no_store`。调用新服务，构造严格响应模型；不要改 list/detail/restore 行为。

### 步骤 2：错误映射

P12C 历史 `EditorStateRevisionHistoryError` 继续走既有 `_raise_history_error`；新比较错误固定 500 code/message + no-store。不得捕获后返回空差异或部分摘要。

### 步骤 3：运行专项至全绿

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12d_revision_current_comparison.py --tb=line
```

## 6. 任务 5：Grok 串行自测与交接

从仓库根依次运行：

```powershell
backend\.venv\Scripts\python.exe -m py_compile backend\app\api\schemas.py backend\app\api\editor_state_revisions.py backend\app\services\editor_state_revision_comparison_service.py backend\tests\test_p12d_revision_current_comparison.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_p12d_revision_current_comparison.py --tb=line
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_p12c_revision_history_read.py backend\tests\test_p12c_revision_restore.py backend\tests\test_editor_state_revisions.py backend\tests\test_editor_state_checkpoints.py --tb=line
git diff --check
```

再核对暂存区为空，`git status --short` 恰好四文件白名单。不得运行全量（留给 Codex），不得改文档、安装依赖、联网、提交或推送。

最终只通过消息箱发送 `review_request`，必须包含：taskId、failure-first 精确结果与首个业务失败、四文件清单、逐条最终命令及计数、13 键/规范比较/摘要预算/错误脱敏/五域零写证据、风险与未做项。

## 7. Codex 独立验收

Codex 先逐行审查服务组合、异常边界、Schema 与测试反假绿，再独立运行 Grok 命令和后端全量：

```powershell
backend\.venv\Scripts\python.exe -m pytest -q backend\tests --basetemp=C:\Temp\bf-p12d-a
```

全量预期为当前 817 加新增专项数，精确计数以实际为准。最后执行 `py_compile`、`git diff --check`、四文件白名单、暂存区为空、工作区无数据库/日志/缓存产物。全部通过后才由 Codex 中文提交、推送并更新主交接/路线图/联调清单；前端比较入口另立 P12D-B。

## 8. 后续边界

P12D-A 只交付后端只读基础。P12D-B 才可设计技术/商务共用“与当前版本对比”入口、严格 response parser、迟到隔离和串行 E2E；任意历史两两比较、正文 diff、搜索、分页、删除、导出与多人协作继续不做。

## 9. 实施闭环

1. 计划冻结提交为 `2cc6ee3`；Grok 任务=`msg_0458b3b3de3c4c088e9bdeead15f1f16`，审查请求=`msg_49322ccb10bb44beb9e70d054d5f9f96`，未执行 Git 暂存、提交或推送。
2. 首次红测被 `MultipleResultsFound` 暴露为测试 fixture 无效失败；在生产代码仍未修改时修正 fixture 后，有效 failure-first 为 **14 failed**，精确落在路由 404、缺比较服务或响应上。
3. Grok 自测为专项 **14 passed**、四组受影响回归 **132 passed**，并通过 `py_compile`、diff 与四文件白名单。
4. Codex 逐行审查 Schema、路由、服务和完整专项测试，确认 13 键共享来源、逐字段规范 JSON、摘要预算、固定脱敏错误和五域零写；直接反假绿验证 `True` 与 `1` 得到 `changedFields=["guidance"]`。
5. Codex 独立结果为专项 **14 passed**、受影响回归 **132 passed**、后端串行全量 **831 passed**（1 条既有弃用告警，1026.84 秒，标准错误为空）；`py_compile`、`git diff --check`、暂存区为空及精确四文件白名单通过。
6. 实现以中文提交 `9445fcc` 推送；验收确认=`msg_33dd27a988b542a3a808604d27b643ae`。P12D-A 到此完成，未偷带前端、恢复、任意历史比较、正文 diff、删除、搜索或分页。
