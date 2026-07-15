<!--
模块：P12B-A editor-state 全状态版本与可选 CAS 实施计划
用途：把共享版本算法、GET 输出、可选条件 PUT 和独立验收拆成一个五文件后端受限任务。
对接：docs/p12b-editor-state-version-foundation-contract.md；P12A；Grok-Codex 消息箱。
二次开发：Grok 只实现与自测；Codex 负责范围冻结、两类版本冲突审查、独立验收、中文提交和文档闭环。
-->

# P12B-A editor-state 全状态版本与可选 CAS 实施计划

> **状态**：已实现、两轮返修、独立验收并推送。
> **前置提交**：P12A 后端=`9f53d92`、闭环=`6fd4c76`。
> **本包提交**：计划/契约=`0b55c30`、实现=`780cc82`。

## 1. 实施顺序

1. 先在新专项测试中独立实现规范 13 键、紧凑排序 UTF-8 JSON 与 SHA-256 前 32 位重算，证明当前 GET 缺 `stateVersion`、当前 Schema 忽略 expected 或无法 CAS 的真实失败。
2. 在 `editor_state_service` 建立共享键集、规范快照和版本函数；重构当前状态组装，使 GET、锁后 CAS 与 P12A 检查点使用同一权威算法，避免循环依赖和双实现漂移。
3. Schema 新增成功响应 `stateVersion` 与请求 `expectedStateVersion` 严格格式；projects 路由只转发可选 expected 并映射固定全状态 409。
4. 泛化既有矩阵写锁为一次项目锁：带 expected 时锁后重算全状态版本，先比全状态、后比矩阵；冲突零写并 rollback。
5. P12A checkpoint service 委托共享算法，同时保留测试或内部调用需要的兼容函数名；不得改 P12A API/表/快照键集。
6. 完成后只发送 `review_request`，不得提交或推送。

## 2. Codex 审查重点

1. 是否真的从锁后服务端行重算版本，而不是信任客户端、缓存、`updatedAt` 或 P12A 存量记录。
2. 13 键与 P12A 是否逐字一致；`projectId/updatedAt/responseMatrixVersion` 和敏感字段是否排除；是否 `allow_nan=False`。
3. expected 与矩阵版本是否只锁一次、全状态冲突优先、任一冲突整包零写；是否显式 rollback。
4. 缺 expected 是否如实保留兼容，文档和测试不得冒充最终安全门。
5. 并发测试是否真用独立 Session/线程与 barrier；409 是否精确、最小、脱敏。
6. 是否偷改模型、旧测试、后台任务、callback、M3-D、前端或新增 restore/history。

## 3. 独立验收

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state_full_version.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state_checkpoints.py tests/test_editor_state.py tests/test_response_matrix.py tests/test_content_fuse_applications.py tests/test_local_parser_callback_tickets.py tests/test_bid_templates.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

仓库根继续运行 `git diff --check` 与暂存后 `git diff --cached --check`。所有进程后台静默；本包无 Playwright，不启动浏览器。

## 4. 提交与后续

计划/契约已由 Codex 中文提交并推送；Grok 实现经两轮定点返修和 Codex 独立验收后，由 Codex 单独提交后端。P12B-A 结束后先做 P12B-B 前端 CAS，不得跳到恢复；长期目标保持 active。

## 5. 实际审查与验收

1. 初版专项 13 项通过，但 Codex 发现 CAS 锁后又调用 `get_editor_state` 并二次 `db.get`，同时提交成功后仍 `refresh`/重读；第一次返修新增 SQL 事件捕获、提交后抛错探针与提交异常回滚，专项增至 16 项。
2. Codex 首次串行全量得到 **522 passed / 12 failed**：内容融合三项暴露提交前 aware UTC 与 SQLite 重读 naive 的 `updatedAt` 字符串漂移；财务九项暴露既有报价 `NaN/Infinity` 与严格规范哈希的兼容断裂。
3. 第二次返修只改服务与专项测试：统一 `updatedAt` 输出；仅在 editor-state 持久 JSON 读写边界把非有限 float 收敛为 `null`，规范哈希和 P12A 检查点仍严格 `allow_nan=False`。既有失败测试未修改。
4. Codex 最终独立结果：专项 **19 passed**；内容融合三项加财务整文件 **12 passed**；原回归 **104 passed**；后端串行全量 **537 passed**。只有 1 条既有 Starlette/httpx 弃用警告，`py_compile`、`git diff --check`、五文件白名单与 HEAD/远端核对均通过。
