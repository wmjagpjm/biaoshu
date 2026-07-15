<!--
模块：P12C-A editor-state 有限自动修订账本实施计划
用途：把独立表、规范 transition 记录器和反假绿测试限定为一个后端基础包。
对接：docs/p12c-editor-state-revision-ledger-contract.md；P12A/P12B-D 检查点服务；editor_state_service。
二次开发：A 包验收前禁止接生产写入者；完成后也不得声称已有公开历史或自动恢复。
-->

# P12C-A editor-state 有限自动修订账本实施计划

> **状态**：已冻结，尚未实现。
> **顺序**：冻结提交推送 → Grok 三文件实现/自测 → Codex 安全审查与必要返修 → 后端独立验收 → 中文提交推送 → 文档闭环。

## 1. 实施目标

建立与 `editor_state_checkpoints` 完全独立的 `editor_state_revisions` 表和无提交 `record_editor_state_transition` 原语。A 包只证明数据模型、共享规范算法、相邻去重、断链补点、10 条裁剪、事务回滚和敏感正文不出域，不改变任何线上行为。

## 2. Grok 精确任务

白名单仅三文件：

1. `backend/app/models/entities.py`
2. `backend/app/services/editor_state_revision_service.py`（新增）
3. `backend/tests/test_editor_state_revisions.py`（新增）

实施顺序：

1. 先写失败测试并记录未建表/未有服务的真实失败；不得先写实现再补假 failure-first。
2. 在 entities 新增独立行模型、数据库 CheckConstraint、级联外键和四列稳定索引；不得修改旧检查点表。
3. 新服务只委托 `editor_state_service` 共享 13 键、规范 JSON 与版本算法；定义固定来源集合、固定内部错误、ID 生成、插入、最新最小投影和裁剪最小投影。
4. 实现 transition：按最新版本决定补 before/追加 after，相邻去重，恢复到旧版本仍追加；插入/裁剪只 flush，无 commit/rollback/锁/项目查询。
5. 用真实 SQLite 覆盖模型、算法、配额、隔离、回滚、SQL 投影和返回最小化；确认现有检查点表零变化。
6. 运行专项、P12A/P12B-D 受影响回归、后端全量、`py_compile` 和仓库 diff 检查；完成后只发 `review_request`。

## 3. Codex 审查重点

1. 是否误复用/修改 `editor_state_checkpoints`，或让自动记录进入现有 20 条裁剪域。
2. 是否复制 13 键/JSON/哈希，是否相信调用方自报版本而不重新计算。
3. 最新/裁剪 SQL 是否加载 snapshot，DELETE 是否缺 workspace/project 约束。
4. 首次、连续、断链、回退和相邻同版本语义是否稳定；并列时间戳是否用 id 打破。
5. 是否隐藏 commit/rollback/锁/项目查询/审计，导致未来无法与业务写保持同事务。
6. 错误、返回、日志和测试输出是否泄漏正文、版本、行/项目/空间或异常原文。
7. 是否出现任何生产调用、API/Schema/前端/配置/依赖越界，或把基础原语冒充已交付自动历史。

## 4. 独立验收命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_full_version.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\services\editor_state_revision_service.py
```

另在仓库根运行 `git diff --check`、精确白名单核对和暂存后 `git diff --cached --check`。全部 PowerShell 后台静默执行，不打开浏览器或可见终端。

## 5. 完成与后续

P12C-A 只有在专项、受影响回归、后端全量和安全审查全部通过后，才由 Codex 中文提交并推送。随后先更新契约/计划/HANDOFF/路线图/联调清单，写明“账本原语存在但生产写入尚未接入”。P12C-B 必须重新枚举每个写入事务并拆包，禁止直接跳到前端历史浏览或恢复。
