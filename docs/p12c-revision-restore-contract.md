<!--
模块：P12C-C2 editor-state 修订受限恢复契约
用途：冻结 revision restore 的准确来源、旧 SQLite 迁移、CAS、安全检查点和原子回滚边界。
对接：P12C-C1 修订列表/详情；P12B-D 检查点恢复；P12C-A 无提交修订原语。
二次开发：本包仅后端恢复，不实现前端、删除、diff、搜索、跨项目历史或多人协作。
-->

# P12C-C2 editor-state 修订受限恢复契约

> **状态**：已冻结，待 Grok failure-first、受限实现与 Codex 独立验收。
> **前置**：C1 冻结=`26b504e`、实现=`7023ecd`、闭环=`5be234c`；后端/前端串行全量基线 **777/263 passed**。

## 1. 目标与关键决策

C2 只新增当前项目单条历史修订的后端受限恢复。它必须把目标规范 13 键写回当前 editor-state，在覆盖前原子保存当前状态为安全检查点，并在内容实际变化时形成新的修订时间点。

修订恢复的准确内部来源固定为 `revision_restore`。禁止复用 `checkpoint_restore`、目标原来源或 `browser_put` 冒充本次动作；C1 已公开 `sourceKind`，错误来源会成为长期可见的错误历史。现有 SQLite 表 CHECK 只有八类来源，因此 C2 必须同时提供旧库幂等迁移，不能只改 Python 枚举或依赖 `create_all`。

前端恢复入口另行冻结。C2 不因已有只读详情而默认授权正文展示、自动恢复或无确认恢复。

## 2. 固定 API

唯一新增端点：

`POST /api/projects/{projectId}/editor-state-revisions/{revisionId}/restore`

请求体精确为：

```json
{"expectedStateVersion":"esv_..."}
```

仅接受 camelCase；缺失、snake_case、额外 `force/source/snapshot/checkpointId`、空白、大小写或非法版本均固定 422，且不得进入恢复服务。

成功响应顶层精确为：

```json
{"safetyCheckpointId":"escp_...","stateVersion":"esv_...","restoredAt":"..."}
```

不回显请求 revision ID、新 revision 行 ID、正文、快照、项目/空间、原来源、当前旧版本、检查点正文或内部路径。`stateVersion` 必须等于目标修订重验版本，`restoredAt` 必须等于本次写回后 editor-state 的 `updatedAt`。成功和全部业务错误固定 `Cache-Control: no-store`。

## 3. 权限、作用域与错误

路由继续复用 `get_workspace_id`：disabled 保持个人版兼容；required 只允许当前空间精确 `bid_writer`，其他角色、仅 owner、无会话与非成员空间不得放宽。required 模式 POST 继续经过既有 Cookie/CSRF 门。

- 陈旧 expected 固定 `409 editor_state_version_conflict`，仅返回固定消息和当前 `currentStateVersion`；CAS 必须先于目标读取和任何写入；
- 项目不存在或跨空间固定 `404 project_not_found`；
- revision 不存在、跨项目或跨空间固定 `404 editor_state_revision_not_found`；
- 目标元数据/正文损坏固定 `500 editor_state_revision_corrupt`；
- 恢复前安全快照超过 2 MiB 沿用固定 413 安全检查点错误；
- 其他恢复内部失败固定 `500 editor_state_revision_restore_failed / 修订恢复失败，未修改编辑内容`。

错误不得反射路径 ID、目标/当前版本、来源、正文、SQL、表名、临时表、文件名、异常类型或内部路径。409 的 `currentStateVersion` 是唯一允许返回的当前版本字段。

## 4. 单事务顺序与共享原语

新增 `editor_state_revision_restore_service.py` 作为路由恢复编排；`editor_state_checkpoint_service.py` 抽取一个“已锁定、已验证规范目标”的无提交共享原语，既有 checkpoint restore 与新 revision restore 都必须调用，禁止复制第二套安全检查点/13 键映射/裁剪事务。

固定顺序：

1. 取得项目 editor-state 写锁并校验 `expectedStateVersion`；陈旧时立即 409；
2. 锁后调用 C1 history 权威读取，以 revision/workspace/project 三重 SQL 获取并重验目标；禁止全局 `db.get` 或客户端投稿 snapshot/source；
3. 从同一锁后 `current_state` 构造规范安全快照，验证字节/版本后插入新安全检查点；
4. 通过 `apply_canonical_snapshot_to_locked_row` 写回精确 13 键，再从同一内存行重算结果版本并等于目标版本；
5. 若目标版本不同于锁前当前版本，调用现有无提交 transition 原语，来源字面量固定 `revision_restore`；正常连续链新增精确一条 after，遗留断链沿用原语补 before+after；
6. 若目标版本与当前版本相同，仍创建安全检查点并更新 `updatedAt`，但不得调用 recorder，完整 revision 身份序列精确不变；
7. recorder 内修订裁剪到 10 条；随后保护新安全检查点地裁剪检查点到 20 条；
8. commit 前构造响应，唯一 commit 后禁止 refresh、GET 或二次提交。

共享原语不得自行查询目标、加第二把锁或接受任意来源；仅允许 `checkpoint_restore|revision_restore`。它不 commit/rollback，两个公开恢复编排各自保留现有固定错误映射和完整 rollback 域。

任一目标读取、规范验证、安全插入、写回、结果复核、recorder、修订裁剪、检查点裁剪或 commit 失败，editor-state、revision、checkpoint 三域必须完整回滚；原目标若仍在 10 条配额内，恢复真实依赖后同一 expected/target 可重试成功。

## 5. `revision_restore` 旧库迁移

新鲜表的模型 CHECK 与 `REVISION_SOURCE_KINDS` 精确增加第九类 `revision_restore`。SQLite 旧表升级必须由 `ensure_schema_columns` 调用专用迁移函数，并满足：

1. 从 `sqlite_master` 精确检查现表 DDL；已含 `revision_restore` 立即 no-op；非 SQLite no-op；
2. 在独立单事务内创建固定临时表、显式八列复制、核对复制前后行数，再删除旧表、重命名并重建全部索引；
3. 新表保留主键、两个 `ON DELETE CASCADE` 外键、字节 CHECK、九来源 CHECK、单列索引和原复合索引；所有既有八来源行、ID、正文、版本、字节和时间精确保留；
4. 连续运行两次幂等；禁止 `writable_schema`、生产 `ignore_check_constraints`、无核对 DROP、吞异常后继续启动或把迁移失败当作“列已存在”；
5. 迁移失败必须回滚并阻止应用启动，不能留下半表、空表或失去索引/FK 的可运行状态。

迁移不修改修订 10 条配额，不重写任何业务行，不生成 `revision_restore` 事件。

## 6. 精确文件白名单

Grok 只允许修改以下 8 个文件：

1. `backend/app/models/entities.py`；
2. `backend/app/core/database.py`；
3. `backend/app/services/editor_state_revision_service.py`；
4. `backend/app/services/editor_state_checkpoint_service.py`；
5. 新增 `backend/app/services/editor_state_revision_restore_service.py`；
6. `backend/app/api/editor_state_revisions.py`；
7. `backend/app/api/schemas.py`；
8. 新增 `backend/tests/test_p12c_revision_restore.py`。

禁止修改 C1 history read service、其他模型/路由/既有测试、认证、前端、依赖、配置或文档。`main.py` 已挂载 revision router，不得改动。Grok 不得 `git add/commit/push`。

## 7. failure-first 与反假绿矩阵

生产修改前先新增真实 HTTP+SQLite 专项并运行，至少因路由不存在、`revision_restore` 被旧 CHECK 拒绝或共享原语不存在真实失败，报告精确 failed/passed。最终专项必须覆盖：

1. 严格 body、camelCase、Cookie/CSRF、disabled 与 required `bid_writer`，finance/hr/bidder/仅 owner 拒绝；
2. 正常旧版本恢复：响应精确三字段；GET editor-state 精确等于目标 13 键；安全检查点精确等于恢复前完整状态；最新新增行唯一来源 `revision_restore`，版本/正文等于目标；
3. 同内容恢复：安全检查点 +1、`updatedAt` 真变化、版本/13 键不变、revision 完整身份序列零变化；
4. 正常连续链精确 +1；人工制造合法遗留断链时精确补 before+after；回到旧版本形成新顶部时间点而非移动旧行；
5. 10 条 revision 与 20 条 checkpoint 边界裁剪，最新恢复行和新安全检查点保留，两个配额互不串扰；
6. 陈旧 expected 在目标存在/不存在时均先 409 且三域零写；缺失项目/revision、跨项目与真实跨空间固定 404，不泄漏 ID；
7. 目标坏 ID/版本/字节/来源/时间/JSON/键集/非规范/版本漂移均固定脱敏 500，三域完整零写；不得仅直测私有函数代替 HTTP；
8. 安全快照超限、写回漂移、recorder、revision trim、checkpoint trim、commit 失败均固定错误、三域完整回滚，并在恢复真实依赖后证明同目标可重试；
9. 两个同 expected 并发恢复精确一胜一 409，只有胜者的一份安全检查点和恢复 transition，最终状态等于胜者目标；禁止顺序调用冒充并发或接受任意 409；
10. 请求伪造 source/snapshot/force 固定 422；`revision_restore` 计数精确增加，`checkpoint_restore` 和目标原来源计数精确不变；
11. 既有 checkpoint restore 仍保持 `checkpoint_restore`、同内容零修订和原响应；共享原语无任意来源、无查询/锁/commit/rollback；
12. 旧 SQLite 八来源表真实迁移两次，所有行/DDL/FK/索引/级联精确保留，新来源可写、伪造来源仍被 CHECK 拒绝；故障注入证明失败不丢旧表数据且不被吞掉。

禁止宽泛 2xx/4xx/5xx、`>=1`、只比计数、空集合假绿、mock SQLite、跨项目冒充跨空间、客户端 source 控制、固定 sleep、随机 ID 推断顺序、修改既有测试迎合实现或用 checkpoint restore 冒充 revision restore。

## 8. 非目标

C2 不实现前端列表/详情/恢复入口、二次确认 UI、迟到隔离、删除、diff、搜索、分页、下载、导出、命名、标签、审批、跨项目历史、保留期设置、自动定时恢复/历史、分支合并或多人实时协作。前端必须在 C2 后另包冻结，不能因后端 POST 存在就自动调用。
