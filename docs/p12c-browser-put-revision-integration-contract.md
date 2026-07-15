<!--
模块：P12C-B-A 浏览器 editor-state PUT 修订账本原子接入契约
用途：冻结首个生产写入者的来源、锁、事务、失败与文件边界。
对接：projects.put_editor_state；editor_state_service.upsert_editor_state；P12C-A record_editor_state_transition。
二次开发：本包只接 browser_put，不得顺带接任务、revise、callback、content-fuse、checkpoint restore、API 浏览或前端。
-->

# P12C-B-A 浏览器 PUT 修订账本原子接入契约

> **状态**：已冻结，尚未实现。
> **前置**：P12C-A 冻结=`daa8c43`、实现=`226e1c1`、闭环=`b025b20`，后端/前端全量基线 **666/263 passed**。
> **单一目标**：只让公开 `PUT /api/projects/{project_id}/editor-state` 的成功状态迁移以内部固定来源 `browser_put` 写入独立修订账本，并与 editor-state 同锁、同事务、同成同败。

## 1. 只读审计结论

公开浏览器 PUT 由 `backend/app/api/projects.py::put_editor_state` 调用 `backend/app/services/editor_state_service.py::upsert_editor_state`。后者同时被任务、revise、商务任务和测试直接调用，不能给服务函数设置默认 `browser_put`，否则尚未审计的内部写入会被错误归因并提前接入。

`upsert_editor_state` 当前有三条入口：全状态 expected CAS、仅矩阵版本锁、无版本兼容写。前两条已取得项目级写锁并构造锁后 `current_state`；无版本兼容写只校验项目后读取 editor-state。若公开 PUT 直接在现状上记账，无版本浏览器保存就不能证明 before/after 来自同一写锁。本包必须把“携带内部修订来源”纳入现有 `needs_version_lock`，并在该锁后从同一 ORM 行构造 before。

P12C-A 服务在模块顶层导入 `editor_state_service` 以复用 13 键算法，因此 `editor_state_service` 禁止顶层反向导入它。本包只能在需要记录时局部导入 `editor_state_revision_service`，不得复制规范快照、版本、插入或裁剪算法。

`EditorStateUpdate` 当前对额外字段采用 Pydantic 默认忽略，路由又显式构造 kwargs。客户端即使提交 `sourceKind`、`revisionSourceKind` 或 snake_case 同名键，也不得影响内部来源；路由必须只传服务端字面量 `browser_put`，不新增 Schema 字段。

## 2. 精确实现契约

### 2.1 服务签名与调用隔离

`upsert_editor_state` 新增仅限内部调用的关键字参数：

```python
revision_source_kind: str | None = None
```

默认值必须为 `None`。所有既有任务、revise、商务任务和直接服务调用保持不传，因此本包不得为它们写 revision。公开 PUT 路由唯一显式传入 `revision_source_kind="browser_put"`。

不得从 HTTP body、query、header、Cookie、项目字段或用户输入派生该值；不得把它加入请求/响应 Schema、日志、审计或返回体。

### 2.2 锁后 before 与提交前 after

带 `revision_source_kind` 的调用必须进入现有项目级写锁路径：

1. `needs_version_lock` 同时考虑 expected、矩阵版本写和 `revision_source_kind is not None`。
2. expected CAS 分支复用 `lock_and_assert_expected_state_version` 返回的同一 `row/current_state`。
3. 无 expected 但需要锁的分支只调用一次 `_lock_for_versioned_write`，随后只用该同一 row 构造 `current_state`；只有 `versioned_matrix_write` 为真时才执行矩阵版本比较，禁止因 `client_matrix_version is None` 产生假冲突。
4. 不带来源且不带版本的既有内部兼容调用保持原行为，不得被本包自动记账或错误加来源。
5. 完成现有内存写入与矩阵收敛后，继续在 commit 前用同一 row 构造 `response`；该对象同时是 after 和成功响应来源。

不得在记录前后调用 `get_editor_state`、二次 `db.get`、`refresh`、新建 Session、取得第二把锁或重算另一套 before/after。

### 2.3 同事务记录与失败双零写

仅当 `revision_source_kind is not None` 时，在 `response = _state_from_row(...)` 之后、唯一 `db.commit()` 之前局部导入并调用：

```python
record_editor_state_transition(
    db,
    workspace_id,
    project_id,
    before_state=current_state,
    after_state=response,
    source_kind=revision_source_kind,
)
```

必须复用现有 try/except 事务边界：

- expected 或矩阵版本陈旧：固定 409，editor-state 与 revision 均零写；
- 项目不存在或跨工作空间：固定 404，均零写；
- revision 校验、插入、flush 或裁剪失败：请求失败，现有 editor-state 修改与本轮 revision 全部 rollback；
- commit 失败：两者全部 rollback；
- 成功：只有一次 commit，返回 `stateVersion` 精确等于本轮 after revision 的版本；
- before 与 after 同版本时，遵守 P12C-A 相邻去重，不得伪造新版本；空账本可留下唯一 before 时间点。

不得吞掉 revision 异常后仍提交正文，不得先 commit editor-state 再补历史，也不得由 recorder commit/rollback。

## 3. 文件白名单

Grok 只允许修改：

1. `backend/app/services/editor_state_service.py`
2. `backend/app/api/projects.py`
3. `backend/tests/test_p12c_browser_put_revisions.py`（新增）

禁止修改 P12C-A 表/服务/测试、Schema、其他写入者、前端、配置、依赖、锁文件、迁移、文档和 Git 历史。Grok 不得 `git add/commit/push`。

## 4. 反假绿验收

新增专项必须使用真实 FastAPI + SQLite，并至少覆盖：

1. 首次实际浏览器 PUT 在空账本写入 before+after 两条，来源均为 `browser_put`，after 版本等于响应版本。
2. 第二次连续 PUT 只追加新 after；重复同一规范状态不追加相邻重复版本。
3. 带 expected、无 expected 兼容写、仅矩阵版本写均记录正确锁后 before/after；无 expected 浏览器调用真实进入现有写锁路径。
4. 陈旧 expected 与陈旧矩阵版本均固定 409，editor-state 和 revision 数量/内容不变。
5. 跨工作空间或不存在项目固定 404，不产生旁路 revision。
6. 在 recorder 已 flush 一条后注入失败，证明 HTTP/服务失败且 editor-state 与本轮 revision 精确双零写；错误响应不得泄漏正文、版本、项目、SQL 或异常原文。
7. 直接调用 `upsert_editor_state` 且不传来源，证明业务状态可按既有语义成功但 revision 为零，防止任务/revise 被误接入。
8. 客户端伪造 `sourceKind/revisionSourceKind/revision_source_kind` 不改变行来源，响应也不出现来源或 revision 正文/ID。
9. 记录过程无二次 editor-state 读取、无第二把锁、无 refresh、无多次 commit；失败后可由新 Session 证明数据库状态。
10. P12C-A 最近 10 条、跨空间/项目隔离和检查点域完全不受影响。

禁止只测 mock、只断言状态码、宽泛断言 revision 数量大于零、用顺序调用冒充并发/回滚、吞掉服务器异常、放宽来源集合或把本包冒充用户可浏览的自动历史。

## 5. 非目标与后续闸门

本包不接任务、revise、个人 callback、P8C 本机票据 callback、content-fuse apply/consume 或 checkpoint restore；不新增 revision 列表/详情/恢复/删除/diff/搜索/分页、前端、下载、导出、审计正文、多人协作或任意版本能力。

P12C-B-A 独立验收并闭环后，才可重新审计并冻结 P12C-B-B 的任务/revise 接入；不得因为服务已有可选来源参数就让其他调用方自行传值。
