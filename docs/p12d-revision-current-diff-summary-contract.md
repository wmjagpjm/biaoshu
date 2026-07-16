<!--
模块：P12D-A 修订与当前状态差异摘要契约
用途：冻结恢复前只读差异摘要的字段、权限、脱敏、事务和非目标边界。
对接：P12C 修订历史、editor-state 权威状态、editor_state_revisions 路由与 P12D-A 实施计划。
二次开发：禁止返回正文、ID、版本、路径或任意字段值；禁止写数据库；前端入口须另立后续包。
-->

# P12D-A 修订与当前状态差异摘要契约

> **状态**：已完成并推送；冻结=`2cc6ee3`、实现=`9445fcc`，前端入口留给 P12D-B。
> **基线**：P12C-C3=`5e4f9f6`、P9C-R1 实现=`b53dcce`/闭环=`6c6b560`；后端/前端串行全量 **831/284 passed**。

## 1. 选包结论

剩余候选中，真实 MinerU/Docling 部署依赖外部 CLI 与模型，合法外部标讯源依赖来源授权，Word `structure`/整章布局缺少跨页与 WPS 视觉语义，均不能在当前本机条件下形成可靠 failure-first 闭环。P12C 已有最近 10 条规范修订、按需详情和受限恢复，但用户恢复前只能看到单侧数量摘要，无法知道目标修订相对服务端当前状态改了哪些数据域。

本包只增加一个后端只读差异摘要 API：比较服务端当前权威 13 键与所选已校验修订，返回变更字段名和两侧六项有界计数摘要。它不返回正文或字段值，不执行恢复，不改变最近 10 条配额，前端按钮与 E2E 留给独立后续包。

## 2. API 与响应契约

新增：

```text
GET /api/projects/{projectId}/editor-state-revisions/{revisionId}/comparison
```

- 继续复用 `get_workspace_id`：disabled 模式沿用默认工作空间，required 模式仅 `bid_writer`；跨工作空间、跨项目和不存在修订统一 404。
- 请求无 body、无查询参数语义；未知查询参数不得改变比较目标、作用域、字段全集或读取范围。
- 成功和所有业务错误固定 `Cache-Control: no-store`。
- 成功体精确四键：

```json
{
  "sameState": false,
  "changedFields": ["chapters", "parsedMarkdown"],
  "currentSummary": {
    "outlineNodeCount": 3,
    "chapterCount": 2,
    "factCount": 1,
    "responseMatrixRowCount": 4,
    "businessEntryTotal": 0,
    "hasParsedMarkdown": true
  },
  "targetSummary": {
    "outlineNodeCount": 3,
    "chapterCount": 2,
    "factCount": 1,
    "responseMatrixRowCount": 4,
    "businessEntryTotal": 0,
    "hasParsedMarkdown": false
  }
}
```

`changedFields` 只允许以下 13 个服务端固定键，并严格按该顺序输出：`outline`、`chapters`、`facts`、`mode`、`analysis`、`responseMatrix`、`guidance`、`parsedMarkdown`、`businessQualify`、`businessToc`、`businessQuote`、`businessCommit`、`analysisOverview`。`sameState` 当且仅当数组为空；不得只比较数量，内容变化但计数相同仍必须列出对应字段。

摘要算法与 C3 展示语义一致：大纲递归节点数、章节数、事实数、矩阵行数、资格/目录/报价行/承诺合计，以及解析正文是否为非空字符串。遍历最多 10,000 个节点、深度最多 32；非法或超界结构固定失败，不得截断后伪造成功。

## 3. 权威数据与比较算法

1. 当前侧必须调用 `editor_state_service.get_editor_state`，再调用 `extract_canonical_snapshot`；不得直接信任客户端状态、版本、摘要或自行拼第 14 键。
2. 目标侧必须调用 `editor_state_revision_history_service.get_editor_state_revision`，复用三重作用域、元数据、规范 JSON、精确 13 键、字节、来源与版本重验；禁止另写宽松历史读取。
3. 每个字段都用共享 `canonical_snapshot_json({key: value})` 得到规范 JSON 后逐字节比较，避免 Python `True == 1`、对象键顺序或空值语义造成假相等；禁止只比对象 identity、长度、摘要或 stateVersion 字符串。
4. 只允许读取当前项目和目标修订；响应不得包含 `projectId`、`revisionId`、`stateVersion`、`snapshotBytes`、来源、时间、`updatedAt`、`responseMatrixVersion`、正文、字段原值、SQL、路径或异常原文。

## 4. 错误、只读与安全边界

- 项目/修订不存在和跨空间继续复用 P12C-C1 固定 404；目标修订损坏继续复用 `editor_state_revision_corrupt` 固定 500。
- 当前状态读取、规范比较或摘要遍历的其他异常统一 `editor_state_revision_comparison_failed` / `修订差异摘要生成失败`，不得反射异常类、服务文件名、表名、字段值、ID、版本、路径或 SQL。
- 新服务和 GET 路由禁止 `add/delete/execute` 写语句、`flush/commit/rollback/refresh`、锁、审计、检查点、修订新增/裁剪与任何 HTTP 请求。测试必须用真实 SQLite 证明 editor-state、修订、检查点、项目和审计计数及内容逐值不变。
- 不新增依赖、配置、环境变量、表、列、迁移、启动补列、缓存、日志、后台任务或下载。

## 5. 严格实现白名单

Grok 只允许修改以下 4 个文件：

1. `backend/app/api/schemas.py`
2. `backend/app/api/editor_state_revisions.py`
3. `backend/app/services/editor_state_revision_comparison_service.py`（新建）
4. `backend/tests/test_p12d_revision_current_comparison.py`（新建）

禁止修改实体/迁移、P12C 历史/恢复/账本服务、`editor_state_service.py`、其他 API、前端、E2E、依赖、配置、文档或用户数据。Grok 不得 `git add`、commit 或 push；文档、提交和推送由 Codex 负责。

## 6. 验收门

- failure-first 必须在生产未改时由 comparison 路由 404 或缺少响应模型/服务失败；不得用语法错误、坏 fixture 或缺依赖冒充。
- 专项至少覆盖：精确四键/两侧六键；完整 13 键顺序；同状态；单字段；多字段；相同计数但内容不同；`True` 与 `1`；空行默认态；技术/商务混合；10,000/32 边界与越界固定失败；损坏修订；不存在/跨项目/跨空间；required 角色；`no-store`；固定 500 脱敏；真实 SQLite 五域零写；AST 禁止写原语。
- Codex 独立运行新专项、P12C C1/C2/账本/检查点受影响回归、`py_compile`、后端串行全量、`git diff --check` 和四文件白名单。任一门失败不得进入前端包。

## 7. 非目标

本包不实现前端按钮/面板/E2E，不返回文本 diff、正文片段、字段值、ID/版本，不比较两个任意历史修订，不做分页、搜索、导出、删除、保留策略、恢复、自动刷新、轮询、多人协作、审计事件或新角色。`sameState=false` 只表示 13 键至少一项不同，不解释业务优劣，也不保证本地尚未保存编辑已进入服务端。

## 8. 交付与独立验收记录

- Grok 任务=`msg_0458b3b3de3c4c088e9bdeead15f1f16`，审查请求=`msg_49322ccb10bb44beb9e70d054d5f9f96`，Codex 验收确认=`msg_33dd27a988b542a3a808604d27b643ae`。Grok 未提交或推送。
- 首次红测因测试 fixture 对既有行使用单值查询而触发 `MultipleResultsFound`，属于无效测试前置失败；Grok 在生产代码仍未修改时先修正 fixture，随后得到有效红测 **14 failed**，失败原因均为 comparison 路由 404、服务或响应尚不存在。
- 最终专项 **14 passed**；P12C C1/C2/账本/检查点受影响回归 **132 passed**；Codex 后端全量 **831 passed**、1 条既有 Starlette/httpx 弃用告警、耗时 1026.84 秒，标准错误为空。
- `py_compile`、`git diff --check`、暂存区为空和精确四文件白名单均通过；Codex 另以直接调用证明 JSON `true` 与数字 `1` 会把 `guidance` 列入差异。
- 独立审查确认当前侧复用权威 editor-state、目标侧复用 C1 详情重验，比较只遍历共享 13 键并逐字段规范 JSON；成功响应严格四个顶层字段和两侧六项摘要，不回显正文、字段值、ID 或版本。新服务无写入、锁、审计或网络原语，真实 SQLite 五域零写测试通过。
- 实现提交 `9445fcc` 已推送至 `origin/collab/grok-code-codex-review`。技术标/商务标共用前端“与当前版本对比”入口、严格解析、迟到隔离和串行 E2E 必须在 P12D-B 独立冻结，不能回填本包。
