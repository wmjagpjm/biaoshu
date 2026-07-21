<!--
模块：V1-H1 章节生成空白正文质量实施计划
用途：按冻结、failure-first、生产实现、独立验收和文档闭环拆分 V1-H1。
对接：V1-H1 契约、Grok A/B 消息箱、独立 worktree、后端串行 pytest。
二次开发：严格两文件；Grok 不做 Git 写入；疑似问题双确认后才返修。
-->

# V1-H1 章节生成空白正文质量实施计划

> **执行代理要求：** 必须使用 `executing-plans`，逐项执行并在每个审查点核对真实证据。

**目标：** 模型返回空白章节正文时，单章和多章任务必须失败且不得写入空白 `needs_review` 章节，同时保留合法短章与既有逐章 CAS 语义。

**架构：** 在单章/多章共用的 `_generate_one_chapter_body()` 出口增加单一空白质量门；用独立 pytest 文件从任务终态、编辑态快照和调用序列三侧证明行为。导出完整性提醒留给 V1-H2。

**技术栈：** FastAPI 服务层、SQLAlchemy 测试数据库、pytest、monkeypatch、现有同步任务执行夹具。

---

### 任务 1：冻结契约与执行基线

**文件：**

- 新建：`docs/v1h1-writer-empty-body-quality-contract.md`
- 新建：`docs/plans/2026-07-22-v1h1-writer-empty-body-quality-plan.md`

**步骤：**

1. 核对主仓为 `collab/grok-code-codex-review@775875d`，上游一致且工作区干净。
2. 记录 Grok A/B 双路只读审计和 Codex 的 H1/H2 拆包裁定。
3. 运行 `git diff --check`，只暂存上述两份文档。
4. 中文提交 `文档：冻结V1H1章节空白正文质量门`，只推送协作分支。
5. 核对本地、上游和 GitHub 实际分支一致。

### 任务 2：创建独立实现 worktree

**路径：**

- worktree：`C:\Users\Administrator\biaoshu-v1h1-writer-empty-impl`
- 分支：`collab/v1h1-writer-empty-impl`
- 数据：仅 pytest 临时数据库

**步骤：**

1. 从任务 1 的冻结提交创建全新分支和 worktree，不复用 V1-F/V1-G。
2. 核对 worktree 分支、HEAD、工作区和空暂存区。
3. 向 Grok B 下发 test-only task；Grok A 只读等待，禁止并发 pytest。

### 任务 3：Grok B 编写 failure-first

**文件：**

- 新建：`backend/tests/test_v1h1_writer_empty_body_quality.py`

**步骤：**

1. 复用现有测试 app、用户、工作区、项目和同步任务夹具；所有正文、标题和 ID 使用合成锚点。
2. 实现契约 §5 的六个测试，精确断言任务终态、固定错误、editor-state 快照/版本和生成调用序列。
3. 先运行单一专项，预期约 `4 failed / 2 passed`；如实记录实际结果和首红。
4. 运行 `git diff --check`，核对仅一个新测试文件、空暂存区并计算 SHA-256。
5. 发送 `review_request`，不得修改生产、Git add/commit/push、安装依赖、联网或读取真实数据。

### 任务 4：Codex 独立审查红测

**步骤：**

1. 核对唯一测试文件和反假绿禁令，逐行审查夹具未复制生产逻辑。
2. 确认空串与混合空白独立覆盖，且合法短章和原样保留是正对照。
3. 确认多章第二章空白时精确证明只提交第一章、第二章不写、第三章不调用。
4. 独立串行运行专项，确认红点来自现有生产缺口。
5. 疑似测试缺口必须先发 question，收到 Grok B 明确确认后才授权 test-only 返修。
6. 冻结测试哈希，向 Grok A 下发 production-only task。

### 任务 5：Grok A 最小生产实现

**文件：**

- 修改：`backend/app/services/task_service.py`
- 只读：`backend/tests/test_v1h1_writer_empty_body_quality.py`

**步骤：**

1. 在 `_generate_one_chapter_body()` 取得 `result.content` 后，以 `str(content or "").strip()` 做唯一判空。
2. 空白时抛出固定 `ValueError("模型未返回有效章节正文，请重试")`；有效时返回原始字符串与既有引用。
3. 不改 `_run_chapter/_run_chapters` 的 CAS、逐章提交、进度、取消或成功 result。
4. 先运行新专项至全绿，再串行运行契约指定三组定向回归。
5. 发送生产文件哈希、冻结测试哈希和真实结果的 `review_request`；不得 Git 写入或扩围。

### 任务 6：Codex 独立验收

**步骤：**

1. 核对严格两文件、测试哈希未变、暂存区为空。
2. 静态追踪单章、首章空白、多章中途空白、合法短章和带首尾空白正文。
3. 串行运行新专项及定向回归，禁止并发和全量 pytest。
4. 运行 `git diff --check`；核对无数据库、uploads、依赖、配置或其它文件变化。
5. 发现生产问题时走 `Codex question → Grok 只读确认 → Codex task → Grok review_request`。

### 任务 7：提交、推送与文档闭环

**步骤：**

1. Codex 只暂存严格两文件，中文提交 `修复：拒绝章节生成的空白模型输出`。
2. 将实现提交快进到 `collab/grok-code-codex-review`，只推送该分支。
3. 更新契约/计划、`HANDOFF-next.md`、路线图和联调清单，记录红绿数字、消息链、哈希和未运行项。
4. 中文提交 `文档：闭环V1H1章节空白正文质量门` 并推送。
5. 核对主仓与实现 worktree 干净，本地/上游/GitHub HEAD 一致。

### 任务 8：继续 V1-H2

H1 闭环后立即冻结技术标导出 `contentWarnings`：历史空章、手工空章或部分生成残留仍可导出审阅草稿，但浏览器必须显示固定、有限、脱敏的正文完整性提醒；禁止复用 `imageWarnings` 或按字数硬拦。
