<!--
模块：P12C-B-D2 content-fuse consume 修订账本接入实施计划
用途：冻结融合恢复消费的三文件实现、条件记账、失败先测与独立验收门。
对接：p12c-content-fuse-restore-revision-integration-contract.md；content_fuse_application_service.py；P12C-B-D1。
二次开发：restored>0 才记录 consume；零恢复只消费；checkpoint restore 必须另包。
-->

# P12C-B-D2 content-fuse consume 修订账本接入实施计划

> **状态**：已冻结，待 Grok failure-first、实现与自测。
> **前置**：D1 冻结=`e8ffaeb`、实现=`a6a28f6`、闭环=`366c36b`；后端/前端串行全量基线 **732/263 passed**。
> **顺序**：计划提交推送 → Grok 三文件失败先测/实现/自测 → Codex 受限审查与必要返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标

只把 `POST /api/projects/{id}/content-fuse-applications/{batchId}/consume` 中真实发生的 editor-state 迁移接入最近 10 条修订账本，内部来源固定 `content_fuse_consume`。完整或部分恢复无论改几章都只形成同一次 transition；零恢复仍消费批次，但不伪造没有发生的 editor-state 修订。

## 2. 精确三文件白名单

- `backend/app/services/content_fuse_application_service.py`
- `backend/tests/test_p12c_content_fuse_apply_revisions.py`，只更新 D1 阶段守卫
- 新增 `backend/tests/test_p12c_content_fuse_consume_revisions.py`

禁止修改 API、共享 editor-state/revision service、checkpoint service、模型、Schema、其他既有测试、前端、依赖、配置和文档。禁止新增或移动 commit/rollback、锁、查询、refresh、upsert；禁止改公开字段、批次/快照/漂移/一次消费规则。Grok 不得 commit/push。

## 3. 固定实现

复用锁后 `state_row/current_state` 作为同事务行与 before。恢复循环和 batch consumed 写入完成后：若 `restored > 0`，从同一内存行构造 after，固定 `source_kind="content_fuse_consume"` 调无提交 recorder，并以 after 版本响应；若 `restored == 0`，不构造虚假迁移、不调用 recorder，版本继续取 before。删除 restored>0 的 `get_editor_state` 重读，保留原唯一 commit/refresh。

## 4. failure-first 与测试矩阵

先更新 D1 阶段守卫并新增 D2 专项，再在未改生产时真实运行。专项覆盖：遗留空账本；D1 apply 后完整恢复；browser_put 漂移后的部分恢复；零恢复只消费零修订；一至五章精确单条；响应/GET/after 一致；来源隔离；404/409/422/跨作用域零增量；完整与零恢复两类真实双并发；recorder flush 和 commit 失败的章节/批次/revision 全域回滚、可重试与公开 500 脱敏。

D1 既有测试不得删除 apply 的空账本、1–5 建议、失败原子性或并发证据。只允许把“consume 尚未接入”改为 D2 真值，并继续精确证明 consume 不新增额外 apply 或 checkpoint 来源。

## 5. 自测与独立验收

Grok 按契约串行运行 D1+D2 专项和受影响回归，再做三文件编译、diff 与白名单检查，完成后只发 `review_request`。Codex 独立审查条件调用位置、零恢复、事务/失败域、并发一次性和测试反假绿，复跑专项、扩大回归与后端全量；全部通过后才由 Codex 中文提交推送并文档闭环。

## 6. 非目标

不接 checkpoint restore、历史 API/前端，不改批次配额、恢复快照、漂移判断、权限、M3-D 队列或 E2E。D3 必须等待 D2 闭环后重新审计安全检查点与 restore 的复合事务。
