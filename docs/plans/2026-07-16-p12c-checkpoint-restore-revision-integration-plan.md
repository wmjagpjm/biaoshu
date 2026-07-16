<!--
模块：P12C-B-D3 checkpoint restore 修订账本接入实施计划
用途：冻结检查点恢复的双文件实现、同内容零修订、复合事务和失败先测门。
对接：p12c-content-fuse-restore-revision-integration-contract.md；P12B-D 安全恢复；P12C-A 修订原语。
二次开发：仅不同规范版本恢复记 checkpoint_restore；不得把 updatedAt 变化冒充 13 键迁移。
-->

# P12C-B-D3 checkpoint restore 修订账本接入实施计划

> **状态**：已完成、独立验收并推送；冻结=`1d44484`、实现=`b91a7ff`。
> **前置**：D2 冻结=`6b83fc1`、实现=`f256f5b`、闭环=`e72427a`；后端/前端串行全量基线 **746/263 passed**。
> **顺序**：计划提交推送 → Grok 双文件失败先测/实现/自测 → Codex 受限审查与必要返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标

只把 P12B-D 现有 `POST /api/projects/{id}/editor-state-checkpoints/{checkpointId}/restore` 中真实发生的规范 13 键迁移接入最近 10 条修订账本，内部来源固定 `checkpoint_restore`。恢复到不同版本形成一次 transition；恢复到相同版本仍创建安全检查点并成功返回，但不得伪造修订。

## 2. 精确双文件白名单

- `backend/app/services/editor_state_checkpoint_service.py`
- 新增 `backend/tests/test_p12c_checkpoint_restore_revisions.py`

禁止修改 API、Schema、共享 editor-state/revision service、模型、既有测试、前端、依赖、配置和文档。禁止新增或移动 commit/rollback、锁、状态/目标读取、检查点写入/裁剪、refresh 或成功后重读。Grok 不得 commit/push。

## 3. 固定实现

复用锁后 `current_state` 为 before，复用共享写回后的 `result_state` 为 after。在目标版本复核成功后、检查点保护裁剪与原唯一 commit 之前，仅当 `result_version != current_state["stateVersion"]` 时固定调用无提交 recorder；同版本禁止调用。recorder 与两个独立裁剪域继续由现有 try/rollback 覆盖，响应字段和提交后行为不变。

## 4. failure-first 与测试矩阵

先新增专项，在不改生产时运行并报告精确红绿数。覆盖遗留空账本 before+after、已有来源连续基线 +1、技术/商务恢复、同内容空账本零修订、回到旧版本的新时间点、响应/GET/after/目标版本一致、来源隔离、422/409/404/跨项目/真实跨空间/损坏/超限/语义漂移零增量、不同版本真实双并发精确错误码、recorder flush/revision 裁剪/检查点裁剪/commit 失败三域回滚与可重试、公开 500 脱敏及提交前同 Session pending 证据。

既有 `test_editor_state_checkpoint_restore.py` 不得改写或删除；它继续证明 P12B-D 的权限、CSRF、13 键、安全检查点、20 条保护裁剪和响应时间语义。新测试必须以精确身份序列、行数、来源和版本补充 revision 证据，不得复制既有测试后削弱断言。

## 5. 自测与独立验收

Grok 串行运行 D3 新专项、既有恢复专项、P12C 修订原语与 D1/D2 content-fuse 专项，再做双文件 `py_compile`、diff、暂存区与白名单检查，只发送 `review_request`。Codex 独立审查条件调用位置、同内容、回退时间点、三域事务、跨空间、并发与反假绿，复跑专项、扩大回归和后端全量；全部通过后才由 Codex 中文提交推送并文档闭环。

## 6. 非目标

不新增历史 API/前端，不改变检查点 20 条或修订 10 条配额，不改目标验证、13 键映射、CAS、权限、安全检查点、响应与 `updatedAt` 语义，不实现删除、diff、搜索、任意修订恢复、定时器或多人协作。P12C-C 留给 D3 闭环后的下一轮规划。

## 7. 实施与验收记录

Grok 在冻结提交 `1d44484` 后严格按双文件白名单完成 failure-first **11 failed / 7 passed**，随后在目标版本复核成功后、检查点裁剪与原唯一 commit 前接入条件 recorder：不同版本固定 `checkpoint_restore`，同版本零 recorder；生产实现没有新增锁、查询、提交、刷新或公开字段。

Codex 首轮审查发现来源隔离用例对筛选后的 restore 行否定 apply/consume，无法拦截额外来源误写，返修任务=`msg_7526501fec8744d58be85c18b5bde998`；第二轮继续收紧同内容 `updatedAt` 真值、所有失败路径完整 editor-state/修订身份全等，以及两个裁剪失败后的原目标可重试，任务=`msg_42b459153c9e4c5191048b00df4fc1b8`。两轮均只修改 D3 新测试，生产文件 SHA256 保持不变。

Codex 独立通过 D3 专项 **18 passed**、扩大恢复/editor-state/全部既有来源回归 **270 passed**、后端串行全量 **764 passed**；均只有 1 条既有 Starlette/httpx 弃用警告。双文件 `py_compile`、`git diff --check`、精确白名单、暂存区、分支与远端检查均通过。确认消息=`msg_1b0bff219b7940eabf665626c1214a2b`，实现提交 `b91a7ff` 已推送 `collab/grok-code-codex-review`；前端无改动，沿用单 worker、零重试 **263 passed** 基线。
