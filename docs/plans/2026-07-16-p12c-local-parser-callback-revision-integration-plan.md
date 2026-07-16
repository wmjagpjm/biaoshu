<!--
模块：P12C-B-C2 P8C 一次性票据 callback 修订账本接入实施计划
用途：冻结 local_parser 来源的双文件实现、分叉事务、失败先测与独立验收门。
对接：p12c-callback-revision-integration-contract.md；local_parser_ticket_service.py；P12C-A 修订原语。
二次开发：stale/null 必须只提交票据消费；其他失败必须完整回滚并允许票据重用；不得与后续来源合包。
-->

# P12C-B-C2 P8C 一次性票据 callback 修订账本接入实施计划

> **状态**：已冻结，待 Grok failure-first、实现与自测。
> **前置**：C1 冻结=`76834f5`、实现=`1d0ce0e`、闭环=`03ef17c`；后端/前端串行全量基线 **711/263 passed**。
> **顺序**：计划提交推送 → Grok 双文件失败先测/实现/自测 → Codex 受限审查与必要返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标与事务分叉

只把 P8C 公开一次性票据 callback 的真实 editor-state 迁移接入最近 10 条修订账本，内部来源固定为 `local_parser`。不改变 P8C 已交付的授权、请求体、响应、错误码和票据消费语义。

三类结果必须严格分叉：

1. fresh 版本匹配：票据消费、parsed Markdown、成功任务、项目步骤、成功审计和 revision 在现有唯一 commit 中同成同败；
2. stale 或旧空版本：只提交票据消费并返回固定 409，其他业务与 revision 全部零写，再放同票固定 401；
3. 非版本异常：完整 rollback，票据回到未消费，可用同一票据重试。

## 2. 精确文件白名单

Grok 只允许修改：

- `backend/app/services/local_parser_ticket_service.py`
- 新增 `backend/tests/test_p12c_local_parser_callback_revisions.py`

禁止修改 `parse_callback.py`、`editor_state_service.py`、`editor_state_revision_service.py`、模型、数据库迁移、Schema、认证中间件、既有测试、前端、依赖和文档。禁止 commit/push。

## 3. 固定实现方式

`apply_one_time_callback` 在 fresh 分支保存现有锁原语返回的 `locked_state_row` 与权威 `before_state`，并传给 `_finalize_success_writes`。helper 不再重新取得 editor-state 行：复用锁后行，只有原本为空时才按既有行为创建内存行。

helper 完成既有 parsed Markdown、成功任务、项目三字段和固定成功审计暂存后，用 `editor_state_service._state_from_row(ticket.project_id, state_row)` 构造同一内存 after；随后调用无提交修订原语，`source_kind` 必须是生产代码字面量 `local_parser`。不新增或移动 commit/rollback，不增加锁、查询、refresh 或 upsert，不把客户端 `source` 传作 revision 来源。

公开成功响应仍只有 `ok/chars/taskId`；既有 400/401/409/413/500 的状态码、固定中文、`no-store` 和脱敏边界全部保持。不得返回 stateVersion、currentStateVersion、revision ID、snapshot、sourceKind 或任何内部历史字段。

## 4. 失败先测与反假绿矩阵

新专项必须先在未改生产代码时运行并真实失败；至少因 fresh 成功没有 `local_parser` 修订、recorder 未被调用或 pending revision 数量为零而失败。不得通过篡改全局夹具、替换数据库、修改既有测试或放宽断言制造失败/通过。

测试均走真实 SQLite；公开行为必须走真实 HTTP 路由：

1. 空账本 fresh 成功：精确形成 before/after 两个 `local_parser` 时间点，after 与最终 GET 版本/规范状态一致；
2. 已有 `browser_put` 基线：仅精确增加一个 `local_parser` after，既有来源不变，其他项目零增量；
3. `mineru/docling` 均只能成为解析元数据，revision 来源固定；非法 source/正文、超限、无效/过期/重放票据零修订；
4. stale 与 null expected：固定 409、消费落库、重放 401，正文/任务/项目/成功审计/本次 revision 精确零写；外部 `browser_put` 行按来源和版本排除；
5. recorder 包装器先调用真实原语并确认 flush，再抛带 marker 异常：公开 JSON 必须精确为固定 500，完整回滚后同票可重试；
6. commit 包装器须在同一 Session 观察 revision 已 pending/flush 后再抛：固定 500、完整回滚、同票可重试，最终只留一次成功 transition；
7. fresh 成功审计与 revision 同事务；recorder/commit/stale/null 失败路径不落成功审计；
8. 个人 callback 真实路由保持 `callback` 且零 `local_parser`；
9. AST 只补充检查精确生产文件、固定字面量与单次调用，不能替代数据库与 HTTP 断言。

通用 500 helper 若在 JSON detail 缺失时仍放行，或只断言 `status_code >= 400`，视为假绿。revision 增量若使用宽松 `>=`、空集合、随机 ID 顺序、只查总数不查来源/版本，视为假绿。

## 5. Grok 自测与 Codex 验收

Grok 至少串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_local_parser_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_local_parser_callback_tickets.py tests\test_p12c_personal_callback_revisions.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py
```

再执行生产/新测试双文件 `py_compile`、`git diff --check`、暂存区 diff 检查与精确双文件白名单。完成后只通过消息箱发送 `review_request`，报告失败先测、最终测试、文件列表、风险与明确未做项。

Codex 独立审查调用位置、事务分叉、错误脱敏、测试反假绿和越界差异；随后独立运行专项、扩大受影响回归与后端串行全量。全部通过后由 Codex 中文提交并推送实现，再更新 callback/P12C 总契约、计划、路线图、联调清单和 HANDOFF。

## 6. 明确非目标

本包不接 content-fuse apply/consume、checkpoint restore，不新增历史列表/详情/恢复/删除/diff/搜索或前端入口，不改变 P8C 助手、票据签发、TTL、来源枚举、认证/CSRF/中间件，不实现任意版本库、多人协作、解析器安装或真实模型验收。下一来源仍须重新审计并独立冻结。
