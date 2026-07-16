<!--
模块：P12C-B-C1 个人 callback 修订账本接入实施计划
用途：限定 Grok 双文件 failure-first/实现、自测与 Codex 独立审查验收顺序。
对接：P12C callback 接入契约；P12B-C2 个人 callback 版本围栏；P12C-A 无提交原语。
二次开发：本计划只接个人兼容 callback；P8C 票据 callback、content-fuse、restore 均不接入。
-->

# P12C-B-C1 个人 callback 修订账本接入实施计划

> **状态**：已完成、独立验收并推送；冻结=`76834f5`、实现=`1d0ce0e`。
> **顺序**：冻结提交推送 → Grok 双文件 failure-first/实现/自测 → Codex 受限审查与返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标

让个人兼容 `POST /api/projects/{projectId}/parse-callback` 的真实 editor-state 迁移，以服务端固定来源 `callback` 写入 P12C 独立最近 10 条修订账本。修订与 parsed Markdown、成功任务和项目步骤继续共享现有项目锁、Session 与唯一 commit，任何失败整体 rollback。

## 2. Grok 实施步骤

1. 只新增 `test_p12c_personal_callback_revisions.py`，覆盖真实成功、401/422/409 零修订、recorder/commit 失败全域回滚、来源/项目/P8C 隔离；先运行得到红灯并通过消息箱报告失败数和首要原因。
2. 只修改 `parse_callback.py`：保留锁后 before，提交前用内存行构造 after，调用无提交 revision 原语并传字面量 `callback`。
3. 不改变既有 Token、项目 404、expected 422、stale 409、成功响应、任务/项目写入、commit/rollback 或错误文案；不调用 upsert，不修改 P8C service。
4. 运行专项、指定受影响回归、两文件编译、diff 和白名单；报告命令、通过数、警告与精确文件清单。
5. 不提交、不推送，只发送 `review_request`；Codex 审查后如需返修，仍不得越过双文件白名单。

## 3. Codex 审查重点

- before 必须直接来自同一次锁后 CAS，after 必须是 commit 前同一内存 editor-state 行；不得提交后重读、再加锁或复制状态算法。
- revision 调用必须位于任务/项目写入之后、唯一 commit 之前；recorder/commit 失败要由既有固定 500 捕获并完整 rollback。
- 客户端 `source`、filename、Markdown、请求额外键都不能决定内部来源；不得扩展响应。
- 空账本 before+after、连续账本只追加 after、stale 外部浏览器行排除及 10 条裁剪不得用随机 ID 或宽松数量假绿。
- 新测试不得替换或放宽 P12B-C2、P8C、P12C-A/B-A/B-B1/B-B2 既有测试。

## 4. 验收命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_personal_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_async_and_callback.py tests\test_local_parser_callback_tickets.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py
.\.venv\Scripts\python.exe -m py_compile app\api\parse_callback.py tests\test_p12c_personal_callback_revisions.py
```

Codex 另行运行扩大受影响回归和 `pytest -q` 后端串行全量。所有命令后台静默；本包不启动浏览器或前端 E2E。

## 5. 完成条件

专项、扩展回归、后端全量、编译、精确双文件白名单、工作树/暂存区 diff 与安全审查全部通过后，Codex 才能中文提交并推送实现。随后更新 P12C 总契约、callback 契约/计划、HANDOFF、路线图和联调清单；明确 C1 只覆盖个人 callback，P8C `local_parser` 仍未实现。

## 6. 实际交付与独立验收

Grok 生产修改前专项 **6 failed / 4 passed**，失败均由缺少 `callback` 修订触发；实现后专项/受影响回归 **10/150 passed**。生产差异只保存锁后 before，并在唯一 commit 前以同一内存 after 和固定 `callback` 调用无提交原语。

Codex 第一次审查要求仅返修新测试：500 必须精确验证固定 JSON 错误，P8C 隔离必须走真实公开 HTTP 路由。Grok 返修后通过专项/相关回归 **10/48 passed**；Codex 独立通过专项 **10**、扩大受影响回归 **224**、后端串行全量 **711 passed**，只有 1 条既有弃用警告。编译、精确双文件白名单和 diff 检查均通过，实现已以 `1d0ce0e` 中文提交并推送。

本包没有接入 P8C `local_parser`、content-fuse、checkpoint restore、历史 API 或前端。Grok 最终回执=`msg_23f84b7c2b924ab2878267a2aaeaef96`，Codex 确认=`msg_8fa02eb1bca24a81a18f8b34b9443f96`。
