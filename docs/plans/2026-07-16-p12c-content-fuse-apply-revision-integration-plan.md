<!--
模块：P12C-B-D1 content-fuse apply 修订账本接入实施计划
用途：冻结融合建议确认写入的双文件实现、失败先测与独立验收门。
对接：p12c-content-fuse-restore-revision-integration-contract.md；content_fuse_application_service.py；P12C-A 修订原语。
二次开发：只接 apply；consume 与 checkpoint restore 必须按各自事务另包冻结。
-->

# P12C-B-D1 content-fuse apply 修订账本接入实施计划

> **状态**：已完成 failure-first、Grok 受限实现、Codex 反假绿返修审查、独立验收、中文提交与推送。
> **前置**：C2 冻结=`52bbabf`、实现=`82cc82e`、闭环=`3f77559`；后端/前端串行全量基线 **721/263 passed**。
> **交付**：冻结=`e8ffaeb`、实现=`a6a28f6`；专项/扩大回归/后端全量 **11/285/732 passed**，前端沿用 **263 passed**。
> **顺序**：计划提交推送 → Grok 双文件失败先测/实现/自测 → Codex 受限审查与必要返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标

只把 `POST /api/projects/{id}/content-fuse-applications` 的真实 editor-state 迁移接入最近 10 条修订账本，内部来源固定 `content_fuse_apply`。一批一至五条建议无论改几章都只形成同一次 before→after transition；章节、恢复批次与 revision 共享原事务。

## 2. 精确文件白名单

- `backend/app/services/content_fuse_application_service.py`
- 新增 `backend/tests/test_p12c_content_fuse_apply_revisions.py`

禁止修改 API、共享 editor-state/revision service、consume 行为、checkpoint service、模型、Schema、既有测试、前端、依赖和文档。禁止 commit/push。

## 3. 固定实现

在 `apply_content_fuse_application` 中把现有锁原语返回值保存为 `state_row, before_state`。章节写入、batch flush 和裁剪完成后，用 `editor_state_service._state_from_row(project_id, state_row)` 从同一内存行构造 after；在原唯一 commit 前调用无提交修订原语，`source_kind` 必须是生产字面量 `content_fuse_apply`。响应 `state_version` 直接取 after。

删除 apply 成功路径现有 `get_editor_state` 重读；不得新增或移动 commit/rollback、锁、查询、refresh、upsert。不得改 consume 的 `get_editor_state`、零恢复版本或批次消费逻辑。请求/响应不得新增 revision 字段。

## 4. failure-first 与测试矩阵

生产修改前先新增专项并真实失败。专项必须覆盖：空账本 before+after；browser_put 后多建议批次精确 +1；最终版本与 GET/响应一致；任务元数据不能控制来源；各类 409/404/422/超限零增量；真实双并发一个成功一个 409 且只一条；recorder flush、trim 后异常、commit 失败全域回滚；其他项目隔离；consume/restore 未误接。

recorder/commit 注入必须走真实服务和 SQLite，500 使用 `raise_server_exceptions=False` 的真实公开路由并严格禁止敏感字段泄漏。不能用 `pytest.raises` 代替公开 500 契约，不能只查总数、使用 `>=`、空集合或 AST 冒充原子性。

## 5. 自测与独立验收

Grok 按契约串行运行新专项和受影响回归，再做双文件编译、diff 与白名单检查，完成后只发 `review_request`。Codex 独立审查生产调用位置、事务/失败域、来源隔离和测试反假绿，复跑专项、扩大回归与后端全量；全部通过才由 Codex 中文提交推送并文档闭环。

## 6. 非目标

不接 consume、checkpoint restore、历史 API/前端，不改任务建议、批次列表/配额、恢复漂移规则、权限、M3-D 前端队列或 E2E。下一包必须重新审计零恢复消费语义。

## 7. 实际交付记录

Grok failure-first 为 **9 failed / 2 passed**，实现后专项/契约回归为 **11/184 passed**。Codex 受限审查确认生产调用位置、同事务 flush/commit、固定来源、响应版本和失败全域回滚成立；同时发现 consume 隔离测试只锁来源计数的假绿缝隙，要求仅测试返修。返修后完整、部分、零 consume 均以修订身份序列前后全等证明 D1 未误接，断链补点也改为精确 `+2`。

Codex 最终独立通过 **11/285/732 passed**，双文件编译、diff、白名单与暂存检查通过；确认回执=`msg_50e3179cd2fc44929c835c259f1cc35d`，实现提交=`a6a28f6`。D2 仍必须单独冻结“仅 restored>0 记账、零恢复只消费”的条件，D3 继续等待 D2 闭环。
