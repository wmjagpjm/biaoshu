<!--
模块：P12C-B-D content-fuse 与 checkpoint restore 修订账本接入契约
用途：记录 apply/consume/restore 的只读事务审计，以及 D1/D2 的冻结、实现与独立验收闭环。
对接：P12C-A 修订原语；M3-D 融合应用/一次消费；P12B-D 检查点恢复。
二次开发：apply、consume、checkpoint restore 禁止合包；零恢复消费不得伪造 editor-state 修订。
-->

# P12C-B-D content-fuse 与 checkpoint restore 修订账本接入契约

> **状态**：D1 `content_fuse_apply`、D2 `content_fuse_consume` 与 D3 `checkpoint_restore` 三类写入均已实现、独立验收并推送。
> **前置**：P12C-B-C2 冻结=`52bbabf`、实现=`82cc82e`、闭环=`3f77559`；后端/前端串行全量基线 **721/263 passed**。
> **D1 提交**：冻结=`e8ffaeb`、实现=`a6a28f6`；Codex 独立后端基线 **11/285/732 passed**，前端沿用 **263 passed**。
> **D2 提交**：冻结=`6b83fc1`、实现=`f256f5b`；Codex 独立后端基线 **25/299/746 passed**，前端沿用 **263 passed**。
> **D3 提交**：冻结=`1d44484`、实现=`b91a7ff`；Codex 独立后端基线 **18/270/764 passed**，前端沿用 **263 passed**。
> **固定拆包**：D1 apply=`content_fuse_apply` → D2 consume=`content_fuse_consume` → D3 checkpoint restore=`checkpoint_restore`。三包分别失败先测、实现、验收、提交和闭环。

## 1. 只读事务审计

### 1.1 D1 content-fuse apply

`apply_content_fuse_application` 先取得项目写锁并完成全状态 CAS，再从成功 `content_fuse` 任务结果读取服务端权威建议，严格验证章节 base 后就地更新一至五章，写 active 恢复批次、裁剪到最近 20 批，并在原唯一 commit 前计算新 `stateVersion`。成功必然产生 editor-state 迁移；章节、批次和未来 revision 必须同成同败。

当前锁原语已返回同一行和权威当前状态，但函数丢弃 current 后又用 `get_editor_state` 重读。D1 固定复用锁后 `before_state/state_row`：完成章节、批次 flush 与裁剪后，用 `editor_state_service._state_from_row` 从同一内存行构造 after，固定 `content_fuse_apply` 调用无提交修订原语，并以该 after 的版本继续构造既有响应。这样不新增锁、查询或事务原语，并移除现有成功路径的重复项目/editor-state 读取。

### 1.2 D2 content-fuse consume

`consume_content_fuse_application` 在全状态 CAS 后执行漂移安全恢复。完整/部分恢复会迁移 editor-state；零恢复仍必须把批次标记为 consumed，但 13 键状态和版本保持不变。若在零恢复时机械调用空账本 recorder，可能写入一条没有迁移的 before 修订，因此 D2 必须独立冻结“仅 `restored > 0` 才记录、零恢复只消费”的条件，并证明 consume 失败/并发与批次一次性语义。

### 1.3 D3 checkpoint restore

`restore_editor_state_checkpoint` 在同一事务中完成锁后 CAS、目标快照严格重验、恢复前安全检查点插入、13 键写回、结果版本复核、保护裁剪和唯一 commit。它的失败域同时包含 editor-state、安全检查点和未来 revision；同内容目标、目标版本回退与恢复后 `updatedAt` 均须重新定义 revision 语义。D3 只能在 D1/D2 闭环后独立冻结。

## 2. P12C-B-D1 文件边界

Grok 只允许修改：

1. `backend/app/services/content_fuse_application_service.py`；
2. 新增 `backend/tests/test_p12c_content_fuse_apply_revisions.py`。

禁止修改 API 路由、`editor_state_service.py`、`editor_state_revision_service.py`、consume 函数行为、checkpoint service、模型、Schema、认证中间件、既有测试、前端、依赖或文档；禁止新增 commit/rollback/refresh/锁/查询/upsert、API 字段、历史 API 或日志。Grok 不得 commit/push。

## 3. D1 必须证明的行为

1. 历史账本为空的既有技术标状态成功 apply：before/after 均来自服务端权威 13 键状态，来源固定 `content_fuse_apply`，after 版本与响应及最终 GET 精确一致；
2. 已有 `browser_put` 基线：一至五条建议同一批 apply 只精确增加一条 after 修订，不按章节数量多记；浏览器行来源不变，其他项目零增量；
3. 请求没有 revision 来源字段，task 建议中的 source/action/正文也不能控制内部来源；成功响应字段集合、`no-store` 和版本格式保持不变；
4. 缺/坏 expected、全状态陈旧、任务/项目/建议/章节/base 冲突、零变化、超限快照及双并发失败请求均不新增本次 `content_fuse_apply` 修订；并发双 apply 恰好一个成功、一个 409，胜者只留一次；
5. recorder 真实 flush 后抛错、裁剪后抛错和最终 commit 抛错均通过真实 HTTP/SQLite 证明章节、批次、revision 全域回滚；500 不反射正文、任务/项目/批次 ID、版本、SQL、路径、表名、异常类型或内部来源；
6. commit 失败注入必须在同一 Session 观察 revision 已 flush 且来源/版本精确，再抛错；禁止仅断言异常发生；
7. D1 不给 consume 或 checkpoint restore 记任何来源；成功 apply 后执行零/部分/完整 consume 时，本包只保留 apply 修订，consume 行数精确为零。

## 4. 反假绿与验收门

failure-first 必须在生产修改前运行新专项，至少一项因缺少 apply revision 真实失败，并报告精确失败数与原因。数据库断言必须按来源和精确 `stateVersion` 计算增量；多建议不得用修订数 `>=1`，空集合不得通过，随机 ID 不得推断顺序。AST 只能补充证明 apply 内唯一 recorder、固定字面来源、consume/restore 未调用，不能替代真实 HTTP、SQLite、并发和 rollback 证据。

Grok 至少串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_apply_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_content_fuse_applications.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12c_local_parser_callback_revisions.py
```

再执行双文件 `py_compile`、`git diff --check`、暂存区 diff 检查和精确双文件白名单。Codex 独立审查并扩大回归、运行后端串行全量；前端无改动，沿用 **263 passed** 串行基线。

## 5. D1 实现、返修与独立验收记录

Grok 在精确双文件边界内先取得 **9 failed / 2 passed** 的 failure-first 证据，再完成生产接入和专项 **11 passed**。实现保存同一次锁后 `state_row/before_state`，在章节、恢复批次 flush 与最近 20 批裁剪后，从同一内存行构造 after；原唯一 commit 前以服务端字面量 `content_fuse_apply` 调用无提交 recorder，响应版本直接取 after，并移除 apply 成功路径的 `get_editor_state` 重读。Grok 未提交或推送。

Codex 审查发现 consume 隔离用例只检查 apply/consume 来源计数，若误写为其他来源仍可能假绿，遂下发仅测试返修 `msg_1c1610e5c0114550a3001e426625be4a`。Grok 回执 `msg_e51deb777dcc46eda74ea0adf2a38d1c` 将完整、部分、零 consume 前后的 `(revision_id, stateVersion, source)` 身份序列收紧为完全相等，并把断链后的 apply 补点改为精确增量；生产文件在返修阶段保持不变。初版回执=`msg_7f737db9851a4ae1ae761ae6f19f53df`，Codex 最终确认=`msg_50e3179cd2fc44929c835c259f1cc35d`。

Codex 独立通过专项 **11 passed**、扩大融合/editor-state/检查点/全部既有来源回归 **285 passed**、后端串行全量 **732 passed**；均只有 1 条既有 Starlette/httpx 弃用警告。双文件 `py_compile`、`git diff --check`、精确白名单、暂存区和分支/远端检查全部通过。实现提交 `a6a28f6` 已推送 `collab/grok-code-codex-review`。

## 6. 非目标

D1 不接 `content_fuse_consume` 或 `checkpoint_restore`，不修改 M3-D 前端/队列/响应，不新增历史列表/详情/恢复/删除/diff/搜索，不改变批次 20 条配额、一次消费、章节漂移规则、任务建议协议或权限。D2/D3 仍须重新冻结，禁止从 D1 实现推断已自动接入。

## 7. P12C-B-D2 consume 冻结边界

### 7.1 精确文件白名单

Grok 只允许修改：

1. `backend/app/services/content_fuse_application_service.py`；
2. `backend/tests/test_p12c_content_fuse_apply_revisions.py`，只把 D1 的“consume 尚未接入”阶段守卫机械更新为 D2 后真值，不得削弱 D1 apply 证据；
3. 新增 `backend/tests/test_p12c_content_fuse_consume_revisions.py`。

禁止修改 API 路由、共享 editor-state/revision service、模型、Schema、认证中间件、其他既有测试、前端、依赖、配置或文档；禁止新增/移动 commit、rollback、refresh、锁、查询、upsert、API 字段、历史 API 或日志。Grok 不得 commit/push。

### 7.2 固定实现

`consume_content_fuse_application` 复用现有锁原语返回的 `state_row/current_state`，其中 current 是服务端权威 before。现有章节漂移筛选、批次 `consumed` 标记和 `consumed_at` 不变：

- `restored > 0`：写回同一 `state_row` 后用 `editor_state_service._state_from_row(project_id, state_row)` 构造 after，在原唯一 commit 前固定字面量 `content_fuse_consume` 调用无提交 recorder；响应版本直接取 after；
- `restored == 0`：批次仍在原事务内消费，但不得调用 recorder、不得改 13 键状态或 `updatedAt`，响应版本继续精确等于操作前版本；
- 删除 restored>0 成功路径的 `get_editor_state` 重读；不得改变完整/部分/零恢复数量、跳过规则、一次消费错误码或公开响应字段。

### 7.3 必须证明的行为

1. D1 apply 后完整恢复：无论恢复一至五章，同批只精确新增一条 `content_fuse_consume` after，响应/最终 GET/after 版本精确一致，不按章节数多记；
2. 外部 `browser_put` 造成部分漂移后，部分恢复只精确新增一条 consume after；浏览器和 apply 行来源/身份不变，其他项目零增量；
3. D1 以前遗留的空账本 active 批次完整恢复时，before+after 均以 `content_fuse_consume` 留史；空账本零恢复只消费且修订仍为零；
4. 零恢复时批次必须 consumed、版本和 13 键完全不变、revision 身份序列完全不变；禁止仅断言 `content_fuse_consume == 0` 而放过其他来源误写；
5. 缺/坏 expected、陈旧 CAS、项目/批次不存在、已消费、跨项目/跨空间以及失败请求均不得新增本次 consume 修订，不得改变 active 批次或章节；
6. 完整恢复双并发恰好一胜一 409，胜者只留一条 consume after；零恢复双并发恰好一胜一“已消费”409，状态版本不变且 consume 修订为零；
7. recorder 真实 flush 后抛错与最终 commit 抛错均走真实公开 HTTP/SQLite，证明章节、批次、revision 全域回滚且批次仍可重试；commit 注入必须在同一 Session 观察 consume after、来源、版本和 pending batch/chapter 已暂存；
8. 公开 500 不泄漏正文、项目/任务/批次/建议 ID、版本、SQL、路径、表名、异常类型或内部来源；响应 shape、`no-store` 和版本格式不变；
9. D1 apply 仍只记录 `content_fuse_apply`，D2 consume 只记录 `content_fuse_consume`，checkpoint restore 仍零 recorder；AST 只能补充固定调用数和字面来源，不能替代 HTTP、SQLite、并发与回滚证据。

### 7.4 failure-first 与验收

生产修改前必须先写/更新两份测试并真实运行，至少一项因 consume 未调用 recorder 失败，报告精确失败/通过数。专项禁止 `>=`、`>0`、空集合、随机 ID 顺序、宽泛状态集合、固定 sleep、顺序调用冒充并发或 mock 掉 SQLite。

Grok 至少串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_consume_revisions.py tests\test_p12c_content_fuse_apply_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_content_fuse_applications.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12c_local_parser_callback_revisions.py
```

再执行三文件 `py_compile`、`git diff --check`、暂存区检查和精确三文件白名单。Codex 独立扩大回归并运行后端串行全量；前端无改动，沿用 **263 passed** 串行基线。

## 8. D2 非目标

D2 不接 `checkpoint_restore`，不修改 M3-D 前端/队列/响应，不新增历史列表/详情/恢复/删除/diff/搜索，不改变 20 批配额、快照结构、章节漂移规则、零恢复一次消费、权限或审计。D3 必须在 D2 实现与文档闭环后重新冻结。

## 9. D2 实现、返修与独立验收记录

Grok 在冻结提交 `6b83fc1` 后按精确三文件白名单完成 failure-first **11 failed / 13 passed**，随后接入生产逻辑：复用锁后 `state_row/before_state`，仅在 `restored > 0` 时从同一内存行构造 after，并在原唯一 commit 前以固定 `content_fuse_consume` 调用无提交 recorder；`restored == 0` 继续只消费批次，13 键、`updatedAt`、响应版本与修订身份序列均不变。实现没有新增或移动 commit、rollback、refresh、锁、查询或 upsert，也没有接入 checkpoint restore。

初版回执=`msg_75568b0572a445c18c5fa659137bdf29`。Codex 首轮受限审查拒绝部分恢复的 `>=1`/子集断言、跨项目自比较、缺失真实跨空间公开 HTTP、并发只看 409、零恢复只比部分字段以及 500 脱敏缺少固定表名/路径，返修任务=`msg_f34f653ca76243afa3785e27a9813b15`；Grok 回执=`msg_12fe29174bb64c47947bc4558dac1a31`。Codex 再把外空间 editor-state 从三字段比较收紧为完整字典全等，任务=`msg_22c714d6ed9e445f9a177ee47d88360b`，最终回执=`msg_a9410ee18ff64338b36b652e6dc7401b`。两轮返修均只改 D2 新测试，生产文件和 D1 阶段守卫保持不变。

Codex 独立确认完整/部分恢复精确 +1、遗留空账本 before+after、零恢复只消费、跨项目/跨空间零副作用、两类真实双并发精确错误码、recorder flush/commit 失败全域回滚及公开 500 脱敏。专项 **25 passed**、扩大受影响回归 **299 passed**、后端串行全量 **746 passed**；均只有 1 条既有 Starlette/httpx 弃用警告。三文件 `py_compile`、`git diff --check`、精确白名单、暂存区与分支/远端检查通过。Codex 确认=`msg_2e23e5e7f9414b52b83569b526592426`，实现提交 `f256f5b` 已推送 `collab/grok-code-codex-review`。

D2 闭环后仍未实现 `checkpoint_restore` 修订接入、修订历史 API/前端、删除、diff、搜索或多人协作。下一包只能先只读审计 P12B-D 安全检查点恢复的复合事务，再冻结 P12C-B-D3；不得把 D2 的条件记账机械复制到 restore。

## 10. P12C-B-D3 checkpoint restore 冻结边界

### 10.1 只读审计结论

`restore_editor_state_checkpoint` 已在一次项目写锁和一个显式回滚域内完成：锁后全状态 CAS、目标检查点三重作用域读取与严格重验、恢复前安全检查点插入、共享 13 键写回、目标版本复核、保护安全检查点地裁剪、提交前响应构造和唯一 commit。D3 不得重做 P12B-D，也不得嵌套调用自行提交的检查点创建或 editor-state upsert。

修订账本的状态版本只覆盖规范 13 键，不含 `updatedAt`。因此必须精确区分：

- 当前版本与目标版本不同：发生真实规范状态迁移，固定 `checkpoint_restore` 记录一次 before→after transition；空账本允许 recorder 写 before+after，已有连续基线精确新增一条 after；
- 当前版本与目标版本相同：恢复仍按 P12B-D 语义创建恢复前安全检查点、更新 `updatedAt` 并成功返回，但规范 13 键及 `stateVersion` 未迁移，必须零修订；不得因账本为空而伪造一条 before；
- 恢复回到历史上已出现过的版本：只要它与当前最新版本不同，就必须作为新的时间点再次追加，不能按“全表已有该版本”去重。

### 10.2 精确文件白名单

Grok 只允许修改：

1. `backend/app/services/editor_state_checkpoint_service.py`；
2. 新增 `backend/tests/test_p12c_checkpoint_restore_revisions.py`。

禁止修改 API 路由、Schema、`editor_state_service.py`、`editor_state_revision_service.py`、模型、认证中间件、既有测试、前端、依赖、配置或文档；禁止新增或移动 commit、rollback、refresh、项目锁、目标/状态读取、检查点插入/裁剪或公开字段。除调用无提交 recorder 的内部最新版本查询与裁剪外，不得新增查询。Grok 不得 git add、commit 或 push。

### 10.3 固定实现位置与事务顺序

在既有锁后 `current_state`、目标严格重验、安全检查点插入、共享写回和 `result_version == target_version` 复核全部成功后，且在 `_trim_checkpoints` 与原唯一 commit 之前：

- 若 `result_version != current_state["stateVersion"]`，以 `current_state` 为 before、`result_state` 为 after、固定字面量 `source_kind="checkpoint_restore"` 调用 `record_editor_state_transition`；
- 若两版本相同，禁止调用 recorder；
- recorder、revision 裁剪、检查点保护裁剪和 commit 必须留在现有同一 try/rollback 域；任何一步失败都要让 editor-state、安全检查点与 revision 三域同时回滚；
- 成功响应继续使用既有目标版本、检查点 ID 与 `restoredAt`，提交后仍禁止 refresh 或 `get_editor_state` 重读。

### 10.4 必须证明的行为

1. 遗留空账本从 B 恢复到不同目标 A：精确形成 before(B)+after(A) 两条 `checkpoint_restore`，after 版本与响应、最终 GET、目标检查点一致；安全检查点快照精确等于 B；
2. 已有 `browser_put`/其他来源连续基线：恢复到不同目标只精确 +1 checkpoint after，旧行 ID/版本/来源完全保留；技术标和商务标均不得改变 P12B-D 的 13 键恢复语义；
3. 同内容恢复：安全检查点仍精确 +1，响应成功且 `restoredAt` 对齐最终 `updatedAt`；规范 13 键、版本和 revision 身份序列精确不变。必须包含遗留空账本，防止无条件 recorder 在空账本伪造 before；
4. 回退到已出现过的历史目标版本时，每次从不同当前版本恢复都精确新增一个新的 checkpoint after 行；禁止用版本集合或随机 ID 顺序假设冒充新时间点；
5. 缺/坏 expected、陈旧 409、项目/检查点不存在、跨项目、真实跨空间、损坏/超限目标以及写回后版本漂移，均不得新增 revision、安全检查点或 editor-state 写入；公开错误不泄漏 ID、正文、版本、SQL、表名、路径、异常类型或内部来源；
6. 不同目标版本的真实双并发必须精确一胜一 `editor_state_version_conflict`，胜者只形成一个安全检查点和一次 checkpoint transition；不得只断言任意 409。相同内容不承诺一次性冲突，禁止把版本未变化的重复成功误写为单胜契约；
7. recorder 真实 flush 后抛错、recorder/revision 裁剪失败、后续检查点裁剪失败和最终 commit 失败，均证明 editor-state、安全检查点、revision 三域全回滚且目标检查点仍可重试；commit 注入必须在同一 Session 观察 after revision、安全检查点和写回状态均已 pending；
8. D1 apply 只保留 `content_fuse_apply`，D2 consume 只保留 `content_fuse_consume`，D3 restore 只允许 `checkpoint_restore`；请求体、检查点正文/ID 或客户端字段不能控制来源；
9. AST 只能补充 restore 内唯一 recorder、固定字面来源、同版本分支不调用及无提交后重读，不能替代真实 HTTP、SQLite、跨空间、并发和回滚证据。

### 10.5 failure-first 与验收门

生产修改前必须先新增 D3 专项并真实运行，至少一项因 restore 尚未调用 recorder 而失败，同时同内容零修订用例应在旧生产上通过；必须报告精确失败/通过数。测试禁止 `>=`/`>0` 修订增量、空集合、宽泛 2xx/409 集合、固定 sleep、顺序调用冒充并发、跨项目冒充跨空间或 mock 掉 SQLite。

Grok 至少串行运行新 D3 专项、既有 `test_editor_state_checkpoint_restore.py`、修订账本及 D1/D2 content-fuse 专项，再执行双文件 `py_compile`、`git diff --check`、暂存区与精确白名单检查。Codex 独立复跑专项、扩大恢复/editor-state/全部既有来源回归和后端串行全量；前端无改动，沿用单 worker、零重试 **263 passed** 基线。

### 10.6 D3 非目标

D3 不新增或修改检查点/修订历史 API、Schema 或前端，不改变手动/安全检查点最近 20 条与修订最近 10 条的独立裁剪域，不实现删除、diff、搜索、跨项目历史、任意修订恢复、自动定时历史或多人协作。P12C-C 必须等待 D3 实现与文档闭环后另行冻结。

## 11. D3 实现、返修与独立验收记录

Grok 在冻结提交 `1d44484` 后先取得 **11 failed / 7 passed** 的 failure-first 证据，再按精确双文件边界完成接入：复用锁后 `current_state` 和写回后 `result_state`，仅在两者规范版本不同时以固定 `checkpoint_restore` 调用无提交 recorder；同版本仍创建安全检查点并更新 `updatedAt`，但修订身份序列保持不变。recorder、两个独立裁剪域与唯一 commit 继续位于原回滚域内，Grok 未提交或推送。

首轮回执=`msg_4430593ed0004731b5c155393eba699e`。Codex 首轮拒绝来源隔离的同义反复断言，返修任务=`msg_7526501fec8744d58be85c18b5bde998`、回执=`msg_df4a4ca99337452098d92e80c4dbfe8d`；第二轮把同内容 `updatedAt` 更新、失败完整状态零写和 revision/checkpoint 裁剪失败后的原目标可重试收紧为精确证据，任务=`msg_42b459153c9e4c5191048b00df4fc1b8`、回执=`msg_69322b31400844f4aa72bbaed660eb98`。两轮返修均只改新测试，生产文件哈希不变。

Codex 独立确认空账本 before+after、已有基线精确 +1、同内容零修订、回到历史版本形成新时间点、跨项目/跨空间隔离、不同版本真实双并发精确一胜一冲突、四类失败三域回滚与公开 500 脱敏。专项 **18 passed**、扩大回归 **270 passed**、后端串行全量 **764 passed**；均只有 1 条既有 Starlette/httpx 弃用警告。双文件 `py_compile`、`git diff --check`、精确白名单、暂存区与分支/远端检查通过。Codex 确认=`msg_1b0bff219b7940eabf665626c1214a2b`，实现提交 `b91a7ff` 已推送。

D3 闭环后，八类内部来源的既有生产写入接入已覆盖到 checkpoint restore。仍未实现的是修订历史 API/前端、受限历史恢复、删除、diff、搜索、跨项目历史、自动定时历史与多人协作；下一包必须先重新审计并冻结 P12C-C，不能从内部账本直接推断公开历史能力已交付。
