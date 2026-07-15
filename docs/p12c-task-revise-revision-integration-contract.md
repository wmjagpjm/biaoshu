<!--
模块：P12C-B-B 任务与 revise 修订账本接入契约
用途：记录只读调用审计，并把任务与商务 revise 按真实事务边界拆成 B1/B2。
对接：P12C-A 修订原语、P12C-B-A 浏览器 PUT、P12B-C 延迟写围栏。
二次开发：禁止给全部 upsert 调用统一补来源；无 editor-state 变化的成功分支不得产生修订。
-->

# P12C-B-B 任务与 revise 修订账本接入契约

> **状态**：只读审计与 P12C-B-B1 九类任务接入均已完成、独立验收并推送；B2 商务 revise 尚未实现。
> **前置**：P12C-B-A 冻结=`fbf93c0`、实现=`acf3139`、闭环=`a45b2da`，后端/前端全量基线 **680/263 passed**。
> **固定拆包**：B1 九类任务来源 `task`（冻结=`05864f6`、实现=`5a0d1c0`，已完成）→ B2 五类商务 revise 来源 `revise`。两包必须分别实现、验收、提交和闭环。

## 1. 只读审计结论

### 1.1 九类任务写入

`task_service.EDITOR_WRITER_TASK_TYPES` 固定包含：

- `parse`、`analyze`、`outline`、`chapter`、`chapters`；
- `biz_qualify`、`biz_toc`、`biz_quote`、`biz_commit`。

生产代码共有 9 个 `upsert_editor_state` 写点：`task_service.py` 5 个，`business_task_service.py` 4 个。任务创建时由服务端捕获权威 `stateVersion` 写入内部 payload，worker 在 LLM/解析完成后把该版本作为 `expected_state_version` 交给 upsert；客户端同名键会被覆盖，REST/SSE 不得泄露内部版本。

每次 upsert 自己取得项目写锁、校验锁后全状态版本并完成唯一 commit。随后的 `update_project` 和任务终态 `_set_task` 各自另行 commit，因此 P12C-B-B1 的原子域只定义为“本次 editor-state 迁移 + 对应 revision”，不得虚称项目步骤和任务终态也与之处于同一事务。若 revision 记录或 editor-state commit 失败，本次状态与 revision 必须双零写；worker 可以按既有规则另行把任务标记为 failed。

`chapters` 会逐章调用 upsert，并把上一章返回的新 `stateVersion` 作为下一章 expected。每次实际章节迁移必须各产生一条 `task` 修订；章间外部漂移导致后续冲突时，已经成功提交的前章及其修订保留，冲突章及之后不得新增状态或修订。这是既有逐章提交语义，不得改成单事务批量写。

`export`、`response_match`、`content_fuse` 不在九类 writer 中：export 不写 editor-state，response_match/content_fuse 只产生待确认建议。B1 不得给它们补 `task` 来源；content-fuse apply/consume 属于后续独立事务包。

### 1.2 商务 revise 写入

`revise_service.BUSINESS_WRITE_STAGES` 固定为 `business_parse` 加四类结构化商务阶段。生产代码只有 2 个 upsert 写点：一个覆盖四类结构化字段，一个覆盖 `parsed_markdown`。它们在 LLM 返回后使用请求中的 `expected_state_version` 锁后 CAS，成功 upsert 自己 commit。

结构化内容无法解析或没有可写 revised 正文时，代码会走 `lock_and_assert_expected_state_version`：版本匹配则只 commit 释放锁并 HTTP 200 返回当前版本，版本漂移则 409。该分支没有 editor-state 迁移，B2 不得生成 revision。普通技术 revise 不写 editor-state，也不得生成 `revise` 修订。

任务与 revise 的调用集合、失败表现和零变化分支不同，因此不得在同一个实现提交里修改。

## 2. P12C-B-B1 冻结范围

只允许 Grok 修改：

- `backend/app/services/task_service.py`；
- `backend/app/services/business_task_service.py`；
- 新增 `backend/tests/test_p12c_task_revisions.py`。

生产实现只做一件事：给上述 9 个真实任务 upsert 调用传服务端字面量 `revision_source_kind="task"`。不得修改 `editor_state_service.py`、修订原语、模型、Schema、API、任务 payload/REST/SSE 结构、既有 commit 次序或错误文案；不得改 revise、callback、content-fuse、checkpoint restore。

## 3. B1 必须证明的行为

1. `analyze`、单章/批量章节和至少一类商务任务的真实成功路径，revision 增量、来源 `task`、最终 `stateVersion` 与权威状态精确一致。
2. 九类生产写点都只传服务端字面量 `task`；三个非 writer 任务没有误接入。
3. 创建后 editor-state 漂移的 stale 任务：LLM/解析结果不得覆盖，revision 增量为 0，既有固定脱敏 failed 终态与版本不外泄保持不变。
4. 批量章节两次成功迁移产生两条连续修订；章间外部漂移时只保留已成功前缀，不得给冲突章记修订。
5. recorder flush 失败与 upsert commit 失败：本次 editor-state/revision 双零写；任务按既有规则失败，HTTP/任务错误不得回显正文、版本、来源内部参数或异常类型细节。
6. 跨工作空间、项目隔离和最近 10 条裁剪继续成立；不得依赖随机 ID 表示插入顺序。
7. P12C-B-A 浏览器 PUT 仍记 `browser_put`，不得被改成 `task`；其他直接 upsert 调用默认 `None` 仍不记账。

## 4. B1 反假绿要求

- failure-first 必须在任何生产修改前运行并保存结果；至少一项因缺少 `task` 来源而真实失败。
- 不允许只检查函数签名、源码字符串或 mock 断言冒充数据库行为；调用集合可用 AST/spy 作为补充，但成功、冲突和失败原子性必须查询真实 editor-state 与 revision 表。
- revision 断言按精确 `stateVersion`、来源与集合比较；并列时间戳时只接受契约排序，不假定随机 ID 等于插入序。
- commit 失败注入必须证明 revision 已在同一 Session 的 commit 前 flush，然后验证回滚后数据库双零写。
- 不得放宽 P12B-C 既有 expected、409、任务脱敏和章间漂移断言迎合实现。

## 5. B1 验收门

Grok 至少运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_task_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py
```

Codex 已独立执行专项、任务/商务/revise/修订受影响回归、后端串行全量、`py_compile`、精确三文件白名单和暂存区检查，并以 `5a0d1c0` 中文提交推送。

## 6. B1 实现与验收记录

Grok failure-first 为 **8 failed / 2 passed**；首版实现后专项 10、受影响回归 109 passed。Codex 审查发现 recorder/commit 内部异常原文会经任务 `error=str(exc)` 泄露，并发现章间漂移 `A and B or A` 只要存在任意浏览器行即可假绿；同时要求把单写/双写增量改为精确等式、来源匹配拒绝空集合。

第一次受限返修在两个生产服务各自新增私有任务 upsert 包装器：固定来源 `task`，`EditorStateVersionConflict` 原样上抛，其他 upsert 异常仅返回固定中文“编辑内容写入失败，请重试”。九类 writer 全部经包装器，非 writer 不接入；逐章提交、任务 payload/REST/SSE 和既有 commit 顺序不变。

Codex 独立验收：专项 **10 passed**、扩展受影响回归 **126 passed**、后端串行全量 **690 passed**；只有 1 条既有 Starlette/httpx 弃用警告。`py_compile`、精确三文件白名单、工作树与暂存区 diff 检查全部通过。Grok 最终回执=`msg_ae20abfe8407452fa80e3af03f534791`，Codex 确认=`msg_7dac7f967d6e408597e4b4e0a73819d7`。

## 7. 后续 B2 闸门

B1 闭环后才冻结 B2。B2 只能修改 `revise_service.py` 与独立新测试：两个真实写点传固定 `revise`，五类商务成功写回产生修订；结构解析失败/空正文的“只校验版本”200、普通技术 revise 和陈旧 409 均为零修订。B2 不得借机修改 LLM 提示词、返回 Schema、历史 API 或前端。
