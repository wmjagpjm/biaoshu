<!--
模块：P12C-B-C callback 修订账本接入契约
用途：记录个人 callback 与 P8C 一次性本地解析 callback 的事务审计，并分别冻结 C1/C2 最小实现包。
对接：P12C-A 修订原语；P12B-C2 callback 版本围栏；P8C 一次性回传票据。
二次开发：个人 callback 与 P8C 票据 callback 禁止合包；陈旧票据消费例外不得被普通回滚语义覆盖。
-->

# P12C-B-C callback 修订账本接入契约

> **状态**：两类 callback 均已实现、独立验收并推送；C1=`76834f5`/`1d0ce0e`，C2=`52bbabf`/`82cc82e`。
> **前置**：P12C-B-B2 冻结=`3a30c03`、实现=`5149385`、闭环=`33ef13e`；后端/前端全量基线 **701/263 passed**。
> **固定拆包**：C1 个人兼容 callback 来源 `callback`（冻结=`76834f5`、实现=`1d0ce0e`）→ C2 P8C 一次性票据 callback 来源 `local_parser`。两包必须分别失败先测、实现、验收、提交和闭环。

## 1. 只读调用与事务审计

### 1.1 C1 个人 callback

`POST /api/projects/{project_id}/parse-callback` 位于 `backend/app/api/parse_callback.py`。它先完成可选 `X-Local-Token` 与项目作用域校验，再调用 `lock_and_assert_expected_state_version` 取得项目写锁、权威当前 editor-state 与可选现有行。成功路径在同一个 Session/事务内：

1. 写入 `parsed_markdown` 与 `updated_at`；
2. 新增成功 `parse` 任务；
3. 更新项目 `status=analyzing`、`technical_plan_step=1` 与时间；
4. 从内存 editor-state 行构造新全状态版本；
5. 唯一 `db.commit()`，然后返回合法 `stateVersion`。

版本冲突在任何业务写前发生并统一 rollback；中途异常固定返回脱敏 `parse_callback_failed` 500 并 rollback。该路径没有票据消费例外，适合在现有锁和唯一事务内直接调用无提交 revision 原语。C1 必须把锁后返回的权威当前状态保存为 before，以提交前内存行构造 after，再以服务端字面量 `callback` 记录 transition；不得调用会自行提交的 `upsert_editor_state`。

请求体 `source` 仍是既有解析结果元数据，不能作为 revision 来源。无论客户端投稿何值，内部 `source_kind` 都必须固定为 `callback`，且不得进入请求/响应 Schema。

### 1.2 C2 P8C 一次性票据 callback

`POST /api/local-parser/callback` 由 `local_parser_ticket_service.apply_one_time_callback` 执行。它先条件 UPDATE 原子消费票据，再读取票据签发时服务端捕获的 `expected_state_version`：

- 版本匹配：票据消费、editor-state、成功任务、项目步骤、固定审计在同一事务提交；未来修订来源应为 `local_parser`。
- 版本陈旧或旧票据版本为空：正文/任务/项目/成功审计必须零写，但票据消费必须单独 commit 后返回 409；再次使用同票固定 401。
- 非版本中途异常：完整 rollback，票据保持可重用。

因此 C2 不能复用 C1 的普通“任何异常全部 rollback”设计，也不能只给 `_finalize_success_writes` 机械补记录。它必须独立证明 fresh 成功原子留史、stale/null 仅消费无修订、recorder/commit 失败票据可重用，以及公开响应不泄露当前版本。C2 另行冻结，不属于 C1 文件白名单。

## 2. P12C-B-C1 文件边界

只允许 Grok 修改：

1. `backend/app/api/parse_callback.py`；
2. 新增 `backend/tests/test_p12c_personal_callback_revisions.py`。

生产改动只允许：保存 `lock_and_assert_expected_state_version` 返回的 before；在现有 `new_state/new_sv` 已构造、唯一 commit 之前调用 `record_editor_state_transition`，固定 `source_kind="callback"`。允许增加所需的 revision 服务导入。

禁止修改 `editor_state_service.py`、`editor_state_revision_service.py`、`local_parser_ticket_service.py`、模型、Schema、认证中间件、既有测试、前端、依赖或文档；禁止新增锁、额外 commit/rollback、upsert、API 字段、历史 API 或日志。Grok 不得 commit/push。

## 3. C1 必须证明的行为

1. 空账本首次成功 callback：before/after 均来自服务端权威 13 键状态，after 版本与响应及最终 editor-state 精确一致，来源固定 `callback`；不得用随机 ID 推断插入先后。
2. 已有 `browser_put` 基线后成功 callback：精确新增一条 after 修订，来源为 `callback`；既有浏览器行保持 `browser_put`，其他项目零变化。
3. 缺失/非法 expected 的 422、可选 Token 缺失/错误的 401、陈旧 expected 的 409，均不新增 callback 修订；陈旧响应保持既有最小 `currentStateVersion`，不得回显 Markdown、文件名、客户端 source 或内部 revision 参数。
4. recorder 已真实 flush 后注入失败：固定脱敏 500，editor-state、成功任务、项目步骤和 revision 全部回滚；注入 marker、正文、版本、SQL、路径、表名、异常类型与内部来源键不得进入响应或库。
5. commit 失败：必须在同一 Session 证明 callback after 修订已于 commit 前 flush；随后固定脱敏 500，editor-state/任务/项目/revision 全部回滚。
6. 客户端 `source` 不能控制 revision 来源；响应不得新增 `revisionSourceKind`、snapshot、revision ID 或其他历史字段。
7. P8C 公开 callback 仍不产生 `callback` 修订；C1 不得把 `local_parser` 提前接入，也不得改变票据 stale/null 消费语义。

## 4. 反假绿要求

- failure-first 必须在生产修改前运行新专项，至少一项因缺少 callback 修订而真实失败，并报告失败数与首要原因。
- 成功、冲突与失败原子性必须查询真实 SQLite 的 editor-state、revision、task 与 project；AST 只能补充证明函数内唯一记录调用和固定字符串来源。
- revision 增量必须按来源与精确 `stateVersion` 计算；外部 `browser_put` 行不得计入 callback 增量，空集合不得通过。
- recorder 失败必须先调用真实原语完成 flush 再抛错；commit 失败必须从同一 Session 查询到精确预期 pending 行数，不能只断言异常发生。
- 禁止放宽 P12B-C2 既有 422/401/409、原子零写、成功版本、Token 或 P8C 票据断言；禁止顺序调用冒充并发、`>=` 宽松增量、`or True`、固定 sleep 或仅源码字符串检查。

## 5. C1 验收门

Grok 至少运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_personal_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_async_and_callback.py tests\test_local_parser_callback_tickets.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py
```

随后运行两文件 `py_compile`、`git diff --check` 与精确双文件白名单。Codex 负责独立审查、扩大受影响回归、后端串行全量、中文实现提交与推送；前端无改动，沿用串行 E2E **263 passed** 基线。

## 6. 非目标与后续闸门

C1 不接入 P8C `local_parser`、content-fuse apply/consume 或 checkpoint restore，不新增历史列表/详情/恢复/删除/diff/搜索、前端入口、版本投稿或多人协作。C1 独立闭环后，已基于票据消费例外重新冻结 C2 白名单和失败原子性，禁止直接复制 C1 实现。

## 7. C1 实现与验收记录

冻结提交 `76834f5`、实现提交 `1d0ce0e`。个人 callback 保存同一次锁后 CAS 返回的权威 before，在 parsed Markdown、成功任务与项目步骤均写入后，以同一内存行构造 after，并在唯一 commit 前用服务端字面量 `callback` 调用无提交修订原语。未调用 upsert，未新增锁、查询、commit/rollback、API 字段或前端改动。

Grok failure-first 为 **6 failed / 4 passed**；实现后专项/受影响回归 **10/150 passed**。Codex 审查发现通用 500 也可通过脱敏 helper、P8C 隔离用例直调 service 冒充公开路由，遂限定仅返修新测试：固定要求 JSON `parse_callback_failed/回传处理失败`，并改用真实 `POST /api/local-parser/callback`。返修后 Grok 通过 **10/48 passed**。

Codex 独立通过专项 **10 passed**、扩大受影响回归 **224 passed**、后端串行全量 **711 passed**；只有 1 条既有 Starlette/httpx 弃用警告。`py_compile`、精确双文件白名单、工作树与暂存区 diff 检查全部通过。Grok 最终回执=`msg_23f84b7c2b924ab2878267a2aaeaef96`，Codex 确认=`msg_8fa02eb1bca24a81a18f8b34b9443f96`。

C1 只覆盖个人兼容 callback。C2 已独立冻结 `local_parser_ticket_service.py` 与新测试，尤其不能破坏 stale/null 票据“只提交消费、零修订”和非版本失败“完整 rollback、票据可重用”的分叉事务语义。

## 8. P12C-B-C2 冻结边界

C2 只允许 Grok 修改：

1. `backend/app/services/local_parser_ticket_service.py`；
2. 新增 `backend/tests/test_p12c_local_parser_callback_revisions.py`。

生产实现固定为复用现有锁后行，不新增锁、查询或事务原语：`apply_one_time_callback` 保存 `lock_and_assert_expected_state_version` 返回的 `locked_state_row/before_state`，仅在 fresh 版本匹配分支把两者传给 `_finalize_success_writes`。该私有 helper 复用传入的锁后行；行为空才按既有语义创建，在 parsed Markdown、成功任务、项目步骤和成功审计均已暂存后，用 `editor_state_service._state_from_row` 从同一内存行构造 after，并以服务端字面量 `source_kind="local_parser"` 调用 `record_editor_state_transition`。recorder 只 flush，最终仍由现有 fresh 分支唯一 `db.commit()` 提交。

禁止修改公开路由、`editor_state_service.py`、`editor_state_revision_service.py`、模型、Schema、认证中间件、既有测试、前端、依赖或文档；禁止接受客户端 revision 来源、调用 upsert、新增 commit/rollback/refresh/锁/查询、改变票据 TTL/摘要/公开授权或响应字段。Grok 不得 commit/push。

C2 必须通过真实公开 `POST /api/local-parser/callback` 证明：

1. fresh 空账本成功时 before/after 原子写入且来源均为 `local_parser`；已有 `browser_put` 基线时只精确增加 after 一条，版本与最终 GET 一致；
2. 客户端 `source=mineru|docling` 只影响既有解析元数据，均不能控制 revision 来源；非法 source/正文、超限、无效/过期/重放票据均零 `local_parser` 修订；
3. stale 与旧空 `expected_state_version` 均固定 409、票据已消费且重放 401，正文/任务/项目/成功审计/`local_parser` 修订零写；并发形成的外部 `browser_put` 修订不得计入本次增量；
4. recorder 真实 flush 后抛错与最终 commit 抛错均固定返回 `{"detail":{"code":"local_parser_callback_failed","message":"回传处理失败"}}` 500，票据消费、正文、任务、项目、成功审计和 revision 全部 rollback；移除注入后同一票据可重试且只成功留史一次；
5. 公开成功响应仍精确只有 `ok/chars/taskId`，409 不含 `currentStateVersion`，所有失败均不反射票据、正文、文件名、客户端 source、SQL、路径、表名、异常类型或内部 revision 字段；
6. 个人 callback 仍只产生 `callback`，不得被误记为 `local_parser`；AST 只能补充证明生产函数内固定字面来源、单次 recorder 调用和文件边界，不能替代真实 SQLite 原子性断言。

failure-first 必须在生产修改前运行 C2 新专项，报告真实失败数与原因。Grok 随后至少运行新专项，以及 `test_local_parser_callback_tickets.py`、`test_p12c_personal_callback_revisions.py`、`test_p12b_delayed_writer_fences.py`、`test_editor_state_revisions.py` 和既有 P12C 来源专项；最后执行双文件 `py_compile`、`git diff --check` 与精确双文件白名单。Codex 独立扩大回归并运行后端串行全量，前端沿用 **263 passed** 串行基线。

## 9. C2 实现与验收记录

冻结提交 `52bbabf`、实现提交 `82cc82e`。fresh 分支保存同一次锁原语返回的锁后行和权威 before，`_finalize_success_writes` 复用该行，在既有正文、任务、项目和成功审计暂存后以同一内存行构造 after，并在原唯一 commit 前用固定 `local_parser` 调用无提交修订原语。stale/null 分支没有进入 helper，仍只提交票据消费；其他异常仍完整 rollback 并允许同票重用。未新增锁、查询、commit/rollback、upsert、API 字段或前端改动。

Grok failure-first 为 **7 failed / 3 passed**，实现后专项 **10 passed**；受影响回归的唯一失败是 C1 阶段“P8C 尚未接 local_parser”的过时守卫。Codex 要求只返修测试：旧守卫改为 P8C 精确一条 `local_parser` 且零 `callback`，C2 recorder `added_count` 精确为 1，并把无效/缺失/过期/重放 401 收紧为固定 JSON。返修后 Grok 通过 **20/147 passed**。

Codex 独立通过专项 **20 passed**、扩大受影响回归 **272 passed**、后端串行全量 **721 passed**；只有 1 条既有 Starlette/httpx 弃用警告。三文件 `py_compile`、精确三文件白名单、工作树/暂存区 diff 与安全审查均通过。Grok failure-first=`msg_03552ae3506d477591ecedede8c34261`、初版=`msg_b4761646c5f247679acd6c45b36cfbb9`、返修=`msg_e7215fc505304d5b977c7419a36eebd9`，Codex 确认=`msg_445f10c957ac45b1890bff677abcf845`。

两类 callback 均已闭环。下一来源必须重新只读审计 content-fuse apply/consume 的双事务和 checkpoint restore 的恢复事务，禁止把三者合包或直接跳到历史浏览/恢复 UI。
