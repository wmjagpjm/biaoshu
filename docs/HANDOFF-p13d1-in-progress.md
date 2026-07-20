# P13-D1 历史在途交接：修订操作者可信账本

> 交接日期：2026-07-20
> 状态：**已由 Codex 最终验收、提交并推送；本文保留为历史审查记录**
> 实现提交：`a8982e3`（`功能：完成P13D1修订操作者可信账本`）
> 冻结基线：`31326840f27a58dcd0e029b0c098eb60b19939d1`（`文档：冻结P13D1修订操作者账本`）
> 工作分支：`collab/grok-code-codex-review`，严禁操作 `main`
> 契约：`docs/p13d1-editor-state-revision-actor-ledger-contract.md`
> 实施计划：`docs/plans/2026-07-20-p13d1-editor-state-revision-actor-ledger-plan.md`

本文记录 P13-D1 提交前的操作级交接，正文中的“未提交/下一步”均为历史现场，不再代表当前状态。最终真值以 `docs/HANDOFF-next.md`、本文件顶部完成状态与实际 `git status` 为准。

最终独立证据：专项+精确 schema **18 passed**、PRAGMA 顺序回归 **2 passed**、五条代表性真实事务路径 **5 passed**，py_compile/diff-check/19 个生产哈希与实现白名单通过。交接原先把 PRAGMA 污染归因给前序测试；复现后确认真实根因是 P13-C 测试跨池连接恢复，已改为同一显式连接闭环。

---

## 1. 新会话复制即用

```text
继续 biaoshu P13-D1 修订操作者可信账本的独立审查与验收。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止切换、提交或推送 main。
先完整阅读 docs/HANDOFF-next.md、docs/HANDOFF-p13d1-in-progress.md、docs/p13d1-editor-state-revision-actor-ledger-contract.md、docs/plans/2026-07-20-p13d1-editor-state-revision-actor-ledger-plan.md 和 docs/integration-checklist.md。
当前工作区包含 Grok 未提交的 P13-D1 实现：19 个生产文件、1 个既有测试修改和 1 个新测试；不要 pull、reset、checkout、stash 或覆盖这些差异。先核对 git status -sb、HEAD、远端分支和暂存区。
Grok 已完成 test-only 返修，review_request=msg_de747706fcb64a188eef50d77e29d451；专项 17 passed、精确 schema 1 passed。两文件顺序联跑唯一失败已定位为既有 test_no_commit_rollback_refresh_project_lock 未恢复 SQLite PRAGMA，单独运行 P13-C 用例通过。不要把它误判为 P13-D1 生产缺陷，也不要直接修改 P13-C。
下一步先审查 test-only 返修和 19 个生产文件冻结哈希，再决定是否用最小 test-only 修复恢复 PRAGMA；串行运行 P13-D1 专项、schema、任务真实 worker、迁移回滚和各写链定点回归。未通过 Codex 验收前不得提交 P13-D1；通过后由 Codex 以中文提交并推送，Grok 不得执行任何 Git 写操作。
所有 PowerShell/测试后台静默运行，不弹窗、不打开浏览器、不抢前台；pytest 禁止 xdist/并发分组，Playwright 必须 --workers=1 --retries=0。本包无前端改动，不需要默认运行 Playwright、后端全量或整仓 E2E。
对话、注释和 Commit Message 一律简体中文；新写或大改文件遵守“模块/用途/对接/二次开发”注释四字段。
```

---

## 2. 当前 Git 真值

交接文档提交后，`HEAD` 会前移到新的文档提交；P13-D1 代码差异仍应保持未暂存。接手者必须以命令重新确认，不能只相信本文的静态 SHA：

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
git diff --cached --name-only
git diff --name-only
git ls-files --others --exclude-standard
```

交接时的实现基线和脏态：

- P13-D1 冻结基线本地与远端均为 `31326840f27a58dcd0e029b0c098eb60b19939d1`；本交接文档提交推送后，两端会共同前移。
- P13-D1 实现没有任何 `git add`、commit 或 push。
- 暂存区为空。
- 脏态应为 19 个生产文件、1 个既有测试修改、1 个未跟踪专项测试。
- 不得执行 `git pull`、`git reset`、`git checkout --`、`git stash`、rebase 或任何清理命令。

### 2.1 生产文件（19 个）

1. `backend/app/models/entities.py`
2. `backend/app/core/database.py`
3. `backend/app/api/deps.py`
4. `backend/app/api/projects.py`
5. `backend/app/api/tasks.py`
6. `backend/app/api/revise.py`
7. `backend/app/api/parse_callback.py`
8. `backend/app/api/content_fuse_applications.py`
9. `backend/app/api/editor_state_checkpoints.py`
10. `backend/app/api/editor_state_revisions.py`
11. `backend/app/services/editor_state_revision_service.py`
12. `backend/app/services/editor_state_service.py`
13. `backend/app/services/task_service.py`
14. `backend/app/services/revise_service.py`
15. `backend/app/services/local_parser_ticket_service.py`
16. `backend/app/services/content_fuse_application_service.py`
17. `backend/app/services/editor_state_checkpoint_service.py`
18. `backend/app/services/editor_state_revision_restore_service.py`
19. `backend/app/services/business_task_service.py`

第 19 个文件不在最初计划白名单中，但 Codex 首轮审查已接受这一必要扩围：四类商务任务同样以 `source_kind=task` 写修订；若不传 actor，required 模式会把真实操作者错误记成未知。不得为了恢复“18 文件”形式而回退它。

### 2.2 测试文件（2 个）

- `backend/tests/test_editor_state_revisions.py`：仅在精确列集合中机械加入 `actor_user_id`。
- `backend/tests/test_p13d1_revision_actor_ledger.py`：新增专项，当前未跟踪，不能遗漏提交。

### 2.3 生产冻结哈希

Grok test-only 返修期间，19 个生产文件的 SHA-256 保持首轮 review_request 报告值。接手者在允许任何生产返修前应重新计算并比较；完整哈希可在 `msg_1a838890b3384c4cbbd6b238e37d5ede` 中读取。关键值：

- `entities.py`：`2D989B1F2210063CB76CB25F57E0EEC13B4457D8E6C3846F707C567E16697DDF`
- `database.py`：`969C61198E6E25ED722A19C4135D37CC94AA2F28DBB26FA78D6629016BB46D15`
- `editor_state_revision_service.py`：`D78571129DAA18C9D2867CB1A45B409C892922B9DD57EA9648D07F3D664F3678`
- `task_service.py`：`3084579BD99BA6F705622FF200D08B465441D1E2E5CBF30C9A5336548B4E35C4`
- `business_task_service.py`：`4402DEEFE96BE9E3A901BCA29AA862267CA321552311E2A0C60926E77FC375CE`
- 当前专项测试：`6D5A790844C880A86D4CC9F5D837009B77581DBF09EE580CB90EFE62D7A27BE8`
- 当前 schema 测试：`72D85782F690A74A8F3B71270EE84316BB2DD0A0E53D17A50C5CB03B005A0998`

---

## 3. P13-D1 要解决什么

P13-B 已显示客户端当前接受版本的 UTC 时间，P13-C 已显示该版本来自浏览器保存、任务、解析、融合或恢复中的哪一种流程。P13-D1 为这些真实修订建立服务端可信、可空的操作者账本；P13-D2 才会把当前 actor 解析成用户名并展示。

### 3.1 数据模型

- `editor_state_revisions.actor_user_id VARCHAR(64) NULL`。
- `project_tasks.actor_user_id VARCHAR(64) NULL`。
- 两列均不建 FK、不建索引；旧行保持 `NULL`，不猜测、不回填。
- 旧 SQLite 由 `ensure_schema_columns` 内两个幂等 `ALTER TABLE` 迁移补列，迁移函数不自行 commit。
- 如果第二个 ALTER 失败，外层事务必须让两张表都不留下 actor 列。

### 3.2 身份真值

- required 模式只读 `request.state.auth_db_user_id`。
- disabled、缺失、空白、超长或非字符串均固定 `None`。
- 不从请求 body/query/header、用户名、Cookie 原文、工作空间头或客户端任务 payload 猜身份。
- 本地解析票据回调没有登录 Request，只能用已消费票据的 `issued_by_user_id`。

### 3.3 九类写链

| 来源 | actor 捕获点 |
|---|---|
| `browser_put` | editor-state PUT 的认证 request state |
| `task` | 创建任务时落入任务行，后台 worker 重新加载任务行 |
| `revise` | revise 请求的认证 request state |
| `callback` | 个人解析回调 request state |
| `local_parser` | 一次性票据 `issued_by_user_id` |
| `content_fuse_apply` | 融合 apply 请求 |
| `content_fuse_consume` | 融合 consume 请求，只有 `restored > 0` 才留修订 |
| `checkpoint_restore` | 检查点恢复请求 |
| `revision_restore` | 修订恢复请求 |

### 3.4 最重要的真实性规则

- 空账本或账本断层时补入的 `before` 行 actor 永远是 `NULL`，因为它不是当前请求创造的版本。
- 只有与最新版本不同的真实 `after` 行才记录本次 actor。
- `before == after`、stale、零恢复、同版本恢复不得伪造操作者修订。
- actor 与 editor-state、任务、解析、融合或恢复原业务写入必须共享原事务；任一步失败全域回滚。
- actor 不进入 13 键快照、公开响应、修订列表/详情、任务 REST/SSE、浏览器存储或错误信息。

---

## 4. Grok 已完成的实现与审查状态

### 4.1 首轮实现

- Codex 下发任务：`msg_a0c6083215454410b9a95c3c19c54c02`。
- Grok 首轮 review_request：`msg_1a838890b3384c4cbbd6b238e37d5ede`。
- failure-first：`16 failed / 0 passed`。
- 首轮绿测：专项 `16 passed`，py_compile 和 `git diff --check` 通过。
- 首轮最小回归：`2 failed / 89 passed`。其中一条是合法新增列导致旧精确列集合过期；另一条是 P13-C PRAGMA 顺序污染。

Codex 首轮生产审查结论：

- 模型、迁移、request actor helper、recorder、九路参数传播和任务 actor 持久化方向成立。
- `business_task_service.py` 的扩围必要并被接受。
- 未发现公开 response schema、前端或历史列表/详情被扩展。
- 19 个生产文件暂时冻结，先退回 test-only 补强；这不等于最终验收通过。

### 4.2 首轮测试退回原因

原专项存在以下反假绿缺口：

1. 浏览器响应泄漏检查含恒真 `or True`。
2. “后台 worker”测试只是直接调用包装器，没有证明 Request/Session 结束后独立重载任务行。
3. content-fuse 与恢复链只检查函数签名，没有证明 route→service→recorder 完整传播。
4. 缺空账本 `before == after` 时唯一补账行 actor 为 null 的证据。
5. 迁移失败测试没有真实证明第二个 ALTER 失败后两列同时回滚。

Codex 下发严格 test-only 返修：`msg_6cf099e801f544e69efbe51e6eab6c44`，只允许修改两个测试文件，19 个生产文件冻结。

### 4.3 test-only 返修结果

- Grok review_request：`msg_de747706fcb64a188eef50d77e29d451`。
- P13-D1 专项：`17 passed`，10.92 秒。
- 精确 schema 用例：`1 passed`，0.70 秒。
- 两测试文件 py_compile：通过。
- `git diff --check`：通过，仅有 Windows CRLF 提示，无 whitespace error。

返修已关闭：

- 删除恒真断言，以递归响应键检查证明没有 actor 泄漏。
- 真正创建带 actor 的 analyze 任务，关闭创建 Session，再由 `_bg_worker` 的独立 Session 重载任务并走真实 `_run_analyze`/upsert，最终修订 actor 精确匹配。
- 用 AST 证明 content-fuse、两类 restore 和 local parser 从入口到 recorder 的完整命名参数链。
- 空账本同状态只补一条 actor null 的 before。
- 真实注入第二个 ALTER 失败，重新连接后两张表都没有残留 actor 列。
- 既有精确 schema 集合机械增加 `actor_user_id`，既有 FK/index/约束断言不放宽。

### 4.4 仍未完成的 Codex 验收

当前还没有：

- Codex 对返修后 40 KiB 专项测试的逐段独立审查结论。
- Codex 对 19 个生产文件最终差异与冻结哈希的重新确认。
- Codex 选取九路中的真实 HTTP/事务定点回归结果。
- 对已定位 PRAGMA 顺序污染的最小修复与回归。
- 实现提交、远端推送和文档闭环提交。

因此禁止把 P13-D1 写成“已完成”。

---

## 5. 唯一已知失败：既有 SQLite PRAGMA 顺序污染

Grok 顺序联跑：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_p13c_current_revision_source.py --tb=line
```

结果：`1 failed / 90 passed`。唯一失败：

```text
tests/test_p13c_current_revision_source.py::test_corrupt_latest_source_returns_null_no_500
结束 PRAGMA ignore_check_constraints 期望 0，实际为 1
```

二分后精确污染前序：

```text
tests/test_editor_state_revisions.py::test_no_commit_rollback_refresh_project_lock
```

证据：

- 只跑 P13-C 该用例：`1 passed`。
- 先跑污染前序、再跑 P13-C：`1 failed / 1 passed`。
- P13-C 精确用例曾由 Codex 独立运行通过。
- 这表明问题是既有测试没有在 `finally` 中恢复连接级 PRAGMA，不是当前 actor 生产语义已经失败。

下一位接手者应先审查 `test_no_commit_rollback_refresh_project_lock` 如何设置 `ignore_check_constraints`，用最小 test-only `try/finally` 恢复原值，并补“用例结束后 PRAGMA=0”断言。禁止直接删除 P13-C 的结束守卫，禁止在生产代码里掩盖测试污染。

---

## 6. 精确续跑顺序

### 6.1 第一步：状态与消息箱

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
git diff --cached --name-only
.\tools\agent-collaboration\Read-AgentMailbox.ps1 -From grok -Tail 8
```

必须看到暂存区为空，并能读到 `msg_de747706fcb64a188eef50d77e29d451`。当前这轮 Grok 单次进程已经结束，不要重复启动同一任务。

### 6.2 第二步：审查 test-only 返修

- 全文搜索并确认不存在 `or True`、宽泛 `>= 1`、`toBeTruthy` 式替代证据。
- 真 worker 测试只能窄 patch LLM，不能 mock 掉任务重载或 editor-state upsert。
- 迁移失败必须真实经过 `ensure_schema_columns`，并用新连接检查两列均不存在。
- AST 只能作为低成本传播门，不能替代至少几条真实写路径。
- 既有 schema 测试只能多一个 actor 列，不得放宽 FK/index/约束。

### 6.3 第三步：最小修复 PRAGMA 测试污染

若代码审查确认污染来自前序测试自身，授权 Grok 或由 Codex做**仅测试文件的最小修复**；不得修改 P13-C 或生产代码。先得到污染复现，再修复后验证：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_editor_state_revisions.py::test_no_commit_rollback_refresh_project_lock `
  tests\test_p13c_current_revision_source.py::test_corrupt_latest_source_returns_null_no_500 `
  --tb=short
```

### 6.4 第四步：Codex 独立最小验收

所有 pytest 逐条串行执行，禁止 xdist 或并行分组：

```powershell
cd C:\Users\Administrator\biaoshu\backend

.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_p13d1_revision_actor_ledger.py `
  tests\test_editor_state_revisions.py::test_table_columns_constraints_indexes_and_fk_cascade `
  --tb=short

.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_editor_state_revisions.py::test_no_commit_rollback_refresh_project_lock `
  tests\test_p13c_current_revision_source.py::test_corrupt_latest_source_returns_null_no_500 `
  --tb=short
```

随后根据专项中的真实用例名称，用 `-k` 选择以下风险各至少一条，不机械跑全量：

- required/disabled request actor helper；
- browser PUT 真响应无泄漏；
- 后台 task 独立 Session 重载 actor；
- 个人 callback 与本地票据 callback；
- content-fuse apply/consume；
- checkpoint/revision restore；
- 第二 ALTER 失败回滚；
- before null、after actor、no-op/stale/零恢复无伪归因。

只有这些证据指向共享事务或迁移的广泛回归，才扩大到相应既有文件。默认不跑后端全量；本包没有前端生产改动，默认不跑 Playwright、lint、build 或整仓 318 E2E。

### 6.5 第五步：静态门

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m py_compile `
  app\models\entities.py app\core\database.py app\api\deps.py `
  app\api\projects.py app\api\tasks.py app\api\revise.py `
  app\api\parse_callback.py app\api\content_fuse_applications.py `
  app\api\editor_state_checkpoints.py app\api\editor_state_revisions.py `
  app\services\editor_state_revision_service.py app\services\editor_state_service.py `
  app\services\task_service.py app\services\business_task_service.py `
  app\services\revise_service.py app\services\local_parser_ticket_service.py `
  app\services\content_fuse_application_service.py `
  app\services\editor_state_checkpoint_service.py `
  app\services\editor_state_revision_restore_service.py `
  tests\test_p13d1_revision_actor_ledger.py tests\test_editor_state_revisions.py
cd ..
git diff --check
git diff --cached --name-only
```

### 6.6 第六步：实现提交与推送

仅当 Codex 明确验收通过：

1. 通过消息箱向 Grok 发送 ack；Grok 仍不得 Git 写操作。
2. 精确暂存 19 个生产文件、专项测试、schema 测试，以及获授权的 PRAGMA test-only 修复。
3. `git diff --cached --check`，核对暂存文件白名单。
4. 中文提交：`功能：完成P13D1修订操作者可信账本`。
5. 推送到协作分支：

```powershell
git -c http.proxy=http://127.0.0.1:7890 `
    -c https.proxy=http://127.0.0.1:7890 `
    push origin collab/grok-code-codex-review
```

6. 再独立更新契约、计划、路线图、主交接和联调清单，记录真实验收数字，以中文文档提交闭环并再次推送。
7. 确认本地 HEAD 与远端一致、工作区干净后，才冻结 P13-D2。

---

## 7. Grok 协作方式

### 7.1 固定职责

- Grok：按 Codex 的单任务、文件级白名单实现与自测；只通过本地消息箱返回 review_request；不得 `git add`、commit、push、stash、reset、checkout 或改文档口径。
- Codex：独立规划、冻结契约、审查差异、决定受限返修、独立验收、中文文档闭环、唯一负责 Git 提交与推送。
- 用户无需在两者之间人工复制消息；只有认证确实报 401/402/登录失败时才请用户重新登录。2026-07-20 本轮认证已成功，不能因进程退出就误判“额度又没了”。

### 7.2 消息箱命令

发送任务：

```powershell
cd C:\Users\Administrator\biaoshu
.\tools\agent-collaboration\Send-AgentMessage.ps1 `
  -From codex -Kind task -Subject '任务标题' -Body '目标、白名单、禁止项、验收命令、不得提交推送'
```

读取 Grok 回执：

```powershell
.\tools\agent-collaboration\Read-AgentMailbox.ps1 -From grok -Tail 20
```

当前 P13-D1 追溯 ID：

- 首轮任务：`msg_a0c6083215454410b9a95c3c19c54c02`
- 首轮 review：`msg_1a838890b3384c4cbbd6b238e37d5ede`
- test-only 返修任务：`msg_6cf099e801f544e69efbe51e6eab6c44`
- test-only 最终 review：`msg_de747706fcb64a188eef50d77e29d451`

### 7.3 后台静默启动 Grok

所有 PowerShell 命令必须后台静默，不弹终端、不拉起浏览器、不抢占桌面：

```powershell
cd C:\Users\Administrator\biaoshu
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:ALL_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
$stdout = '.agent-collaboration\grok.stdout.log'
$stderr = '.agent-collaboration\grok.stderr.log'
$arguments = '--cwd "C:\Users\Administrator\biaoshu" --single "读取 .agent-collaboration/messages/codex-to-grok.jsonl 中最新一条 Codex 任务，严格按任务执行；完成后仅通过消息箱向 Codex 发送 review_request，不要提交或推送。" --always-approve --disable-web-search --no-subagents --output-format json'
Start-Process -FilePath 'C:\Users\Administrator\.grok\bin\grok.exe' `
  -ArgumentList $arguments `
  -WorkingDirectory 'C:\Users\Administrator\biaoshu' `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru
```

CLI 调用端超时不代表子进程停止。先用带 `--cwd`/`--single` 的命令行过滤任务进程，再看消息箱；不得重复下发同一任务。单次任务发出 review_request 后退出是正常行为。

---

## 8. P13-D1 之后的路线图

### 8.1 立即下一包：P13-D2 当前操作者用户名展示

必须等 P13-D1 提交、推送且工作区干净后单独冻结。建议边界：

- editor-state 响应只在“最新修订版本与当前 `stateVersion` 精确匹配”时解析 actor。
- 公开字段只返回当前操作者用户名或 `null`，绝不返回内部用户 ID。
- 用户不存在、停用或历史改名的语义须先冻结；建议保守返回 null，不把当前会话用户冒充历史 actor。
- 技术标/商务标复用 P13-B/C 的同一接受门和标题区，不新增轮询或额外前端请求。
- 无 actor 的旧数据、disabled 模式、补账 before、坏 actor、版本不匹配均显示未知。
- 不顺手加入历史列表 actor、按 actor 搜索、presence、在线状态或多人光标。

### 8.2 多人协作主线仍未实现

现有系统有账号、workspace、RBAC、CAS、409 冲突、任务 SSE 工作空间鉴权和当前版本元数据，但这不等于真正多人实时协作。仍缺：

- 前端活动工作空间切换 UI 与多成员可见性；
- 在线成员/presence、心跳与离线过期；
- 协同光标、选区、字段/章节锁或租约；
- editor-state 事件广播、SSE 游标/重放或 WebSocket；
- 项目级多任务事件总线、断线恢复与跨客户端状态一致；
- 评论、审批、通知和完整身份审计。

建议先做不依赖实时协议的最小工作空间切换和成员可见性，再单独设计 presence/事件协议；不要把上述能力塞进 P13-D2。

### 8.3 其它未实现产品主线

- 解析交付：真实 MinerU/Docling CLI、模型制品安装/打包、自动部署和真实用户文档验收；当前只有外置助手与安全回传链。
- Word：`heading_border.structure`、整章/节级页框、跨页标题与整体版式决策。
- 外部标讯：P9B 目前只有国能 e 招单站受控追踪；其它合法网站/API/RSS、定时同步和附件链未做。
- 语义检索：真实用户语料评测与调优、更多召回/排序策略；固定 BGE 运行时门已完成，但不等于业务效果已调优。
- 版本治理：跨项目历史、完整时间线、按 actor 搜索/筛选、审计报表；当前项目内的命名、搜索、分页、删除、固定、比较和恢复已做，不能重复列为未实现。
- 修订搜索增强：片段、高亮、评分、自动搜索、FTS/缓存、多源聚合。
- 财务：税务、预算、审批、导出、回款/应收、旧历史、失败尝试和完整财务审计。
- 人力：附件和真实证件核验；现有到期提示只是人工日期提示。
- 投标人：响应矩阵明细、版本与结果跟踪；P10G 只有项目合规统计。
- AI 反馈：商务 AI feedback history 仍是浏览器本地语义，尚未服务端化。
- 生产化：Alembic、PostgreSQL、HTTPS、Key 加密、Docker、备份恢复、监控运维和生产 worker/Celery 治理。

外部模型、用户语料、外部数据源授权和 Word 视觉规则都依赖用户制品或决策；在这些前置未提供时，优先推进不依赖外部资产的 P13-D2 与多人协作基础。

---

## 9. 禁止事项

- 禁止把 P13-D1 当前脏代码写成已完成、已提交或已推送。
- 禁止清理、覆盖或丢弃当前 21 个路径的差异。
- 禁止让 Grok执行 Git 写操作，禁止推送 `main` 或 force push。
- 禁止为一次顺序污染默认跑后端全量；先修复精确前序测试的 PRAGMA 恢复。
- 禁止并发 pytest；Playwright 必须 `--workers=1 --retries=0`，但本包默认无需运行。
- 禁止在 P13-D1 公开 actor、用户名或新增前端展示。
- 禁止从客户端投稿 actor，禁止给 actor 列加 FK/索引，禁止回填旧行。
- 禁止把补账 before、no-op、stale、零恢复或同版本恢复归给当前操作者。
- 禁止把真实 Key、Cookie、CSRF、数据库、上传文件、模型缓存或 `.agent-collaboration` 消息提交到 Git。
- 禁止前台启动 PowerShell、Grok、测试或浏览器；所有长任务后台隐藏、日志重定向。
