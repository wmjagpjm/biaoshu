<!--
模块：P12C-C1 editor-state 修订历史只读接口契约
用途：冻结最近 10 条修订元数据列表与单条按需详情的后端边界、安全投影和验收门。
对接：P12C-A 修订账本；P12C-B 八类写入接入；P12B-D 检查点只读接口设计。
二次开发：本包只读，不实现恢复、删除、diff、搜索、分页、前端或多人协作。
-->

# P12C-C1 editor-state 修订历史只读接口契约

> **状态**：已冻结，待 Grok failure-first、受限实现与 Codex 独立验收。
> **前置**：P12C-B-D3 冻结=`1d44484`、实现=`b91a7ff`、闭环=`d07012b`；后端/前端串行全量基线 **764/263 passed**。

## 1. 目标与拆包

C1 只向现有 editor-state 标书制作者开放两个只读端点：

- `GET /api/projects/{projectId}/editor-state-revisions`：返回当前项目最近 10 条修订元数据，绝不加载或返回 `snapshot_json`；
- `GET /api/projects/{projectId}/editor-state-revisions/{revisionId}`：只在用户按 ID 请求时读取并严格重验单条规范快照。

列表和详情同属一个只读安全边界，可在一包完成；恢复会写 editor-state、安全检查点与新 revision，必须留给 C2 重新冻结。C1 不新增前端，避免把后端正文详情在没有交互、二次确认和迟到隔离前直接暴露到页面。

## 2. 固定 API 与响应

列表响应顶层精确为：

```json
{"items":[{"revisionId":"esr_...","stateVersion":"esv_...","snapshotBytes":123,"sourceKind":"browser_put","createdAt":"..."}]}
```

详情响应精确为同五个元数据字段加 `snapshot`；`snapshot` 必须是服务端重验后的规范 13 键对象。两个成功响应和全部业务错误均固定 `Cache-Control: no-store`。不返回项目/空间、用户、任务、检查点、票据、批次、路径、IP、正文摘要、标签或内部 SQL。

列表固定 `created_at DESC, id DESC`，最多 10 条；C1 不定义 `limit/offset/cursor/source/search` 等查询参数，即使请求携带这些未知参数，也必须忽略且不能改变固定排序、10 条上限、来源全集或正文不可搜索边界。现有项目无修订时返回 `200 {"items":[]}`。

## 3. 权限、作用域与错误

复用 `get_workspace_id`：disabled 保持个人版兼容；required 只允许当前空间 `bid_writer`。其他角色、无会话与非成员空间继续沿用既有固定鉴权错误，不能因新路由放宽。

项目不存在或跨空间固定 `404 project_not_found / 项目不存在或不可访问`。修订不存在、跨项目或跨空间固定 `404 editor_state_revision_not_found / 修订记录不存在或不可访问`。损坏元数据或详情正文固定 `500 editor_state_revision_corrupt / 修订记录数据损坏，无法读取`。错误不得反射路径 ID、版本、来源、正文、SQL、表名、文件名、异常类型或内部路径。

## 4. 服务与 SQL 边界

新增独立只读服务 `editor_state_revision_history_service.py`，禁止把公开读取、项目查询和 HTTP 错误混入现有无提交 writer 原语。服务必须：

1. 项目校验只投影 `Project.id`，同时限定 `workspace_id/project_id`；
2. 列表 SELECT 只投影 `id/state_version/snapshot_bytes/source_kind/created_at`，SQL 投影段绝不出现 `snapshot_json`，固定排序与 `LIMIT 10`；
3. 详情 SELECT 显式投影上述五列加 `snapshot_json`，WHERE 同时限定 `revision id/workspace/project`，禁止全局 `db.get` 后 Python 过滤；
4. 列表元数据严格验证 ID、版本、字节范围、固定来源和时间；详情再验证 UTF-8 精确字节数、JSON 对象、精确 13 键、紧凑排序规范 JSON、共享版本算法与来源；任一异常收敛为固定 corrupt；
5. 全程只读，不调用 `commit/rollback/flush/refresh`，不取得锁，不写审计，不修改 10 条配额，不读取当前 editor-state，也不触发检查点服务。

13 键、规范 JSON、版本格式和固定来源必须委托 `editor_state_service` 与 `editor_state_revision_service` 的现有权威常量/算法；禁止复制第二套哈希或来源枚举。

## 5. 精确文件白名单

Grok 只允许修改以下 5 个文件：

1. 新增 `backend/app/services/editor_state_revision_history_service.py`；
2. 新增 `backend/app/api/editor_state_revisions.py`；
3. `backend/app/api/schemas.py`；
4. `backend/app/main.py`；
5. 新增 `backend/tests/test_p12c_revision_history_read.py`。

禁止修改模型、现有 writer/revision/checkpoint/editor-state 服务、数据库启动补列、认证中间件、其他路由、既有测试、前端、依赖、配置或文档。Grok 不得 `git add/commit/push`。

## 6. failure-first 与反假绿验收

生产修改前必须先写新专项并运行，至少因路由不存在真实失败，报告精确 failed/passed。专项使用真实 HTTP + SQLite，覆盖：

1. 现有空项目列表精确 200 空数组；多来源真实写入后列表 shape、来源、版本、字节、顺序和最多 10 条精确；
2. SQL 捕获证明列表项目校验最小投影、revision SELECT 不含 `snapshot_json`，禁止用响应不含 snapshot 代替 SQL 证据；
3. 详情精确六字段、13 键快照、规范重算版本/字节一致，响应不附带项目/空间/内部关联；
4. 项目/修订不存在、跨项目、真实跨空间均精确 404，两个空间的完整列表/详情身份不受影响；
5. required 模式 `bid_writer` 可读，finance/hr/bidder/仅 owner 均拒绝，disabled 兼容；所有成功/业务错误 `no-store`；
6. 伪造 ID、损坏 ID/版本/字节/来源/时间/JSON/键集/非规范 JSON/版本漂移均固定脱敏 500，不泄漏正文、ID、版本、来源、SQL、表名、路径或异常；
7. GET 前后 revision、checkpoint、editor-state 和项目状态精确全等；服务源码/AST 补充证明无 commit/rollback/flush/refresh/锁/写调用，但不得替代数据库证据；
8. C1 路由无 POST/PATCH/DELETE/restore；未知查询参数不改变响应，不能扩大条数、筛选来源或搜索正文。

禁止宽泛 2xx/4xx/5xx、`>=1`、空集合假绿、只测 service、mock SQLite、跨项目冒充跨空间、ORM 整实体列表加载、随机 ID 推断时间顺序或修改既有测试迎合实现。

## 7. 非目标

C1 不实现修订恢复、恢复前安全检查点、expectedStateVersion、删除、diff、搜索、分页、下载、导出、命名、标签、审批、跨项目历史、保留期设置、自动定时历史、前端面板或多人实时协作。C2 恢复与后续前端必须在 C1 独立闭环后重新规划，不能直接复用检查点 restore 或把 `revisionId` 当 `checkpointId`。
