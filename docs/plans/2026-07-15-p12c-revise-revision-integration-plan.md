<!--
模块：P12C-B-B2 商务 revise 修订账本接入实施计划
用途：限定 Grok 两文件失败先测与实现、Codex 审查验收及提交顺序。
对接：P12C-B-B 审计契约、P12B-C1 revise 延迟写围栏、P12C-B-A 原子接入模式。
二次开发：本计划只接五类商务 revise；技术 revise、callback、content-fuse、restore 均不接入。
-->

# P12C-B-B2 商务 revise 修订账本接入实施计划

> **状态**：已完成、独立验收并推送；冻结=`3a30c03`、实现=`5149385`。
> **顺序**：冻结提交推送 → Grok 两文件 failure-first/实现/自测 → Codex 受限审查与返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标与范围

让 `business_parse` 与四类结构化商务 revise 的真实成功 editor-state 迁移，以内部固定来源 `revise` 写入 P12C 独立最近 10 条修订账本。复用 upsert 已有项目写锁、锁后 before、写后 after、唯一 commit 与统一 rollback，不新增事务机制。

唯一生产改动是给 `revise_service.py` 的两个真实 upsert 调用传服务端字面量 `revision_source_kind="revise"`。无字段变化的版本校验 200 不调用 upsert；普通技术 revise 不在 `BUSINESS_WRITE_STAGES`，均保持零修订。

## 2. Grok 实施步骤

1. 新建 `test_p12c_revise_revisions.py`，先覆盖真实成功、零变化、stale/并发漂移、recorder/commit 失败并运行得到红灯；准确记录失败数和首要原因。
2. 只修改两个生产调用；来源不得来自请求、stage、LLM 输出、数据库或任意动态参数。
3. 运行专项与 P12B-C/revise/P12C-A/B-A/B-B1 受影响回归；报告命令、通过数、警告和文件清单。
4. 不提交、不推送；等待 Codex 审查并仅在两文件白名单内返修。

## 3. 反假绿与安全重点

- 成功、失败和零变化必须查询真实 editor-state/revision 表；AST 只补充调用集合。
- 并发漂移必须由 LLM 阻塞期间独立 Session/HTTP 写入，不得顺序调用冒充并发。
- 外部并发浏览器修订按来源和精确版本排除，不能用总 revision 数误判 revise 零增量。
- recorder 失败用 `TestClient(app, raise_server_exceptions=False)` 验证真实脱敏 500；commit 注入需证明 recorder 已 flush 后才失败。
- 按 `stateVersion` 精确定位 after；并列时间戳只按契约排序，不假定随机 ID 表示插入顺序。
- 不修改或放宽 P12B-C 既有 required expected、409 无正文、合法 stateVersion 和技术 revise 兼容断言。

## 4. Grok 自测

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revise_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_settings_and_revise.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py
```

随后运行 `py_compile` 两文件、`git diff --check` 与精确两文件白名单；后端全量留给 Codex 独立执行。

## 5. 完成条件

专项、扩展受影响回归、后端串行全量、审查、编译、白名单与暂存区检查全部通过后，Codex 才能中文提交并推送实现。随后更新本契约/计划、P12C 总契约、HANDOFF、路线图和联调清单；明确 B2 只覆盖商务 revise，callback/content-fuse/restore 与历史浏览仍未实现。

## 6. 实际交付与独立验收

Grok 在生产修改前运行新专项，得到 **6 failed / 5 passed**；最终专项 **11 passed**、受影响回归 **122 passed**。实现只在 `revise_service.py` 的两个真实 upsert 写点传入固定 `revision_source_kind="revise"`，未新增包装器，也未改动无字段变化、技术 revise、Schema、API 或前端。

Codex 独立通过专项 **11 passed**、扩展受影响回归 **147 passed**、后端串行全量 **701 passed**；只有 1 条既有 Starlette/httpx 弃用警告。`py_compile`、精确双文件白名单、工作树与暂存区 diff 检查均通过。实现已以 `5149385` 中文提交并推送；Grok 最终回执=`msg_1f7714f97a9b4255985e55d3789ab5fd`，Codex 确认=`msg_10bf04a7dcde47428782ac75faac7389`。

本包没有交付个人 callback、P8C 一次性本地解析 callback、content-fuse apply/consume、checkpoint restore、历史列表/详情/恢复或前端入口；后续必须先只读审计各自锁、CAS、票据消费与 commit 边界。
