<!--
模块：P12C-B-D content-fuse 与 checkpoint restore 修订账本接入契约
用途：记录 apply/consume/restore 的只读事务审计，并冻结 D1 content_fuse_apply 最小包。
对接：P12C-A 修订原语；M3-D 融合应用/一次消费；P12B-D 检查点恢复。
二次开发：apply、consume、checkpoint restore 禁止合包；零恢复消费不得伪造 editor-state 修订。
-->

# P12C-B-D content-fuse 与 checkpoint restore 修订账本接入契约

> **状态**：三类写入只读审计完成；D1 `content_fuse_apply` 已冻结，D2 consume 与 D3 checkpoint restore 待后续独立冻结。
> **前置**：P12C-B-C2 冻结=`52bbabf`、实现=`82cc82e`、闭环=`3f77559`；后端/前端串行全量基线 **721/263 passed**。
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

## 5. 非目标

D1 不接 `content_fuse_consume` 或 `checkpoint_restore`，不修改 M3-D 前端/队列/响应，不新增历史列表/详情/恢复/删除/diff/搜索，不改变批次 20 条配额、一次消费、章节漂移规则、任务建议协议或权限。D2/D3 仍须重新冻结，禁止从 D1 实现推断已自动接入。
