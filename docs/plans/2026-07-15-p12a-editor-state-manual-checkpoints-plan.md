<!--
模块：P12A editor-state 手动检查点只读库实施计划
用途：把服务端检查点表、创建/列表/详情 API 和独立验收拆成一个后端受限任务。
对接：docs/p12a-editor-state-manual-checkpoints-contract.md；P11B/P11C；Grok-Codex 消息箱。
二次开发：Grok 只实现与自测，不得提交推送；Codex 负责计划、审查、独立验收、中文提交和文档闭环。
-->

# P12A editor-state 手动检查点只读库实施计划

> **状态**：已完成受限实现、两轮返修、Codex 独立验收、实现提交与推送；实现=`9f53d92`。
> **执行顺序**：计划提交推送 → Grok 后端实现/自测 → Codex 独立审查/返修/验收/提交 → 中文文档闭环。

## 1. 只读审计结论

现有 editor-state 写入分散在普通 PUT、异步任务、两类 callback、模板新建和 M3-D 原子事务。P12A 不拦截这些路径，也不做恢复；只在用户显式请求时，从服务端当前权威状态创建有限只读检查点。这样可以先建立可信历史载体和浏览 API，同时避免破坏 P8C/M3-D 已验收事务。

Word `heading_border.structure` 审计同时确认只有未接线的“上下/左右结构”枚举，没有标题/正文容器与跨页产品语义，因此不作为本包，不由实现代理擅自决定视觉规则。

## 2. Grok 后端任务

精确七文件白名单：

1. `backend/app/models/entities.py`
2. `backend/app/models/__init__.py`
3. `backend/app/main.py`
4. `backend/app/api/schemas.py`
5. `backend/app/api/editor_state_checkpoints.py`（新增）
6. `backend/app/services/editor_state_checkpoint_service.py`（新增）
7. `backend/tests/test_editor_state_checkpoints.py`（新增）

实现顺序：

1. 先写实体/API/权限/快照/20 条裁剪/损坏数据/事务/SQL 投影失败测试并实际运行，保留真实 failure-first 摘要。
2. 新增精确实体、CHECK、外键和复合索引；在 models/main 注册，但不改旧表和 `ensure_schema_columns()`。
3. 新服务锁项目后调用既有 `editor_state_service.get_editor_state()`，只抽取契约 13 键，紧凑排序序列化，计算 UTF-8 字节、计数和 `esv_` 哈希。
4. 创建、裁剪同事务；列表显式 select 元数据列，详情单条解析并重新校验；固定业务错误不得回显快照、ID、SQL 或异常。
5. 新路由只提供 POST 创建、GET 列表、GET 详情；复用 `get_workspace_id`，所有成功响应 `no-store`，空对象 Schema `extra=forbid`。
6. 运行专项、editor-state/M3-D/P8C/技术商务标受影响回归与 `git diff --check`；只发送 `review_request`，不得 `git add/commit/push`。

禁止修改任何白名单外文件；禁止新增恢复、删除、下载、前端、依赖、迁移、自动快照钩子或客户端快照字段。

## 3. Codex 审查重点

1. 客户端是否仍能投稿 snapshot、版本、计数或名称；服务端是否漏存 projectId/updatedAt/responseMatrixVersion/用户/任务等字段。
2. 创建是否锁后重读；插入、20 条裁剪是否同一事务；并发是否能超过上限或误删其他项目。
3. 列表 SQL 是否真的不选 `snapshot_json`；详情是否严格验证 JSON、键集、字节和版本，而不是坏数据静默空态。
4. 2 MiB 是否按 UTF-8 字节而非字符；树计数是否迭代有界；错误是否固定脱敏。
5. 新表约束/索引/FK 是否由数据库真实证明；required/disabled/strict role/CSRF/跨空间是否复用现有语义。
6. 是否偷偷实现恢复、改变 editor-state PUT/M3-D/P8C 事务或把 `stateVersion` 冒充全状态并发锁。

## 4. 独立验收命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state.py tests\test_response_matrix.py tests\test_content_fuse_applications.py tests\test_local_parser_callback_tickets.py tests\test_business_bid_mvp.py
```

按审查风险决定是否跑后端串行全量。P12A 不改前端；仍需确认仓库根 `git diff --check` 和暂存后 `git diff --cached --check`。所有 PowerShell 与 Grok 进程后台静默，不启动浏览器或可见窗口。

## 5. 提交与闭环

通过后由 Codex 单独中文提交后端并推送协作分支，再更新本契约/计划、路线图、联调清单和 HANDOFF。文档必须写明“手动、最多 20、只读、无恢复、非完整历史”，并保留 P12B 并发恢复闸门；长期目标继续 active。

## 6. 实际审查与验收记录

Grok 原实现任务为 `msg_b1d4a03f493e4edc909eea632b60133a`。Codex 首轮审查发现淘汰路径加载全部 `snapshot_json`、提交后 `refresh` 可制造已写入却报错、详情允许同步篡改后的非规范 JSON、损坏元数据可能泄漏类型异常、跨项目详情先按全局主键加载正文，遂下发 `msg_2248b407df6a4747aca0b0860e93bcf0`。首轮真实红测为 4 failed，返修后专项升至 24 passed。

第二轮审查发现创建函数仅保护插入后的步骤，项目锁、权威读取和序列化失败仍依赖 Session 关闭时被动回滚，且默认 `json.dumps` 接受 `NaN/Infinity`。返修任务 `msg_38b36fcf84284344b59407d28b153aa4` 先得到 5 项真实失败，再把完整创建链收进统一回滚域并启用 `allow_nan=False`。Codex 最终回执为 `msg_2d76b0ced0c749fca11edbccdf4dc20c`。

独立验收结果：专项 **29 passed**；editor-state、认证、项目、M3-D、模板受影响回归 **97 passed**；P8C/异步 callback **15 passed**；后端串行全量 **518 passed**，只有 1 条既有 Starlette/httpx 弃用警告。实现严格为七文件，提交 `9f53d92` 已推送 `origin/collab/grok-code-codex-review`。下一步只能先审计 P12B 的全状态并发版本、恢复前安全检查点、原子恢复和迟到 autosave 防护，不得直接增加恢复端点。
