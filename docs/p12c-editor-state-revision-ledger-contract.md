<!--
模块：P12C editor-state 有限自动修订历史契约
用途：冻结独立于手动/安全检查点的最近自动修订账本、事务原语和后续接入边界。
对接：P12A 手动检查点；P12B-A/B/C/D 全状态 CAS、延迟写围栏与安全恢复。
二次开发：本文件不是公开历史 API 或任意版本库；不得把基础账本或单一浏览器写入者冒充完整自动历史。
-->

# P12C editor-state 有限自动修订历史契约

> **状态**：P12C-A 账本、P12C-B 八类原子写入来源、P12C-C1 只读列表/详情与 P12C-C2 受限恢复均已实现并独立验收；P12C-C3 双工作区前端已冻结。
> **拆包**：P12C-A（`daa8c43`/`226e1c1`）→ B-A 浏览器 PUT（`fbf93c0`/`acf3139`）→ B-B1 九类任务（`05864f6`/`5a0d1c0`）→ B-B2 商务 revise（`3a30c03`/`5149385`）→ B-C1 个人 callback（`76834f5`/`1d0ce0e`）→ B-C2 P8C 票据 callback（`52bbabf`/`82cc82e`）→ B-D1 content-fuse apply（`e8ffaeb`/`a6a28f6`）→ B-D2 consume（`6b83fc1`/`f256f5b`）→ B-D3 checkpoint restore（`1d44484`/`b91a7ff`）→ P12C-C1 只读列表/详情（`26b504e`/`7023ecd`）→ C2 受限恢复（`54af600`/`2276366`/`0803250`）→ C3 双工作区前端（已冻结）。

## 1. 只读审计结论

P12A/P12B-D 的 `editor_state_checkpoints` 同时承载用户手动检查点和恢复前安全检查点，固定每项目最近 20 条。若把高频 autosave/任务写入直接追加到该表，手动检查点和安全检查点会在短时间内被淘汰，破坏已验收恢复语义。自动修订必须使用独立表、独立配额和独立服务，禁止复用现有 20 条裁剪域。

当前写入者不只 `upsert_editor_state`：任务/revise、个人 callback、P8C 票据、M3-D apply/consume 和 checkpoint restore 均有各自事务边界。一次把新表和全部写入者合包会重新打开 P12B-C/D 已关闭的锁、CAS、回滚和迟到写风险。因此 A 包只交付内部账本原语，不修改任何生产写入路径；B 包必须在 A 独立验收后另立精确白名单。

## 2. P12C-A 数据模型

新增 `EditorStateRevisionRow`，表名 `editor_state_revisions`，只含：

| 字段 | 契约 |
|---|---|
| `id` | 服务端生成 `esr_` + 32 位小写 hex；主键 |
| `workspace_id` | 当前工作空间外键，级联删除 |
| `project_id` | 当前项目外键，级联删除 |
| `snapshot_json` | `editor_state_service` 权威 13 键规范 JSON；禁止客户端投稿 |
| `state_version` | 从同一规范 JSON 计算的 `esv_` 版本 |
| `snapshot_bytes` | UTF-8 字节数，数据库约束 1～2 MiB |
| `source_kind` | 固定内部来源枚举，见下节；不得存任意字符串 |
| `created_at` | 服务端 UTC 时间；排序与 `id` 共同稳定 |

索引固定覆盖 `(workspace_id, project_id, created_at, id)`。表中不得新增用户/操作者、任务/检查点/票据/批次 ID、项目名称、路径、请求体、异常、备注、标签、IP、Cookie、CSRF、API Key 或其他来源详情。

## 3. 固定来源与配额

`source_kind` 只允许以下内部枚举，为 B 包预留，A 包测试可以使用但不得接入生产调用：

- `browser_put`
- `task`
- `revise`
- `callback`
- `local_parser`
- `content_fuse_apply`
- `content_fuse_consume`
- `checkpoint_restore`

每项目只保留最近 **10** 条自动修订，按 `created_at DESC, id DESC` 稳定裁剪。每条仍受 2 MiB 上限，因此单项目自动修订账本理论上限为 20 MiB；这与 P12A/P12B-D 的 20 条检查点独立。裁剪只能 SELECT 待保留/删除的 `id/state_version`，不得为淘汰加载 `snapshot_json`；DELETE 必须同时限定 workspace/project/id，绝不跨域。

## 4. 无提交事务原语

新增内部函数 `record_editor_state_transition(...)`，只接受调用方已在同一项目写锁/事务内取得的 `before_state`、`after_state` 和固定 `source_kind`。它必须：

1. 只用 `editor_state_service.extract_canonical_snapshot`、`canonical_snapshot_json` 和共享版本算法处理两个状态，禁止复制 13 键、序列化或哈希。
2. 校验两个输入携带的合法 `stateVersion` 与重新计算结果精确一致；非法、缺失、非规范、非有限值、超 2 MiB 或来源非法均抛固定内部错误。
3. 只读取当前项目最新一条 `id/state_version`，不得读取历史正文。
4. 若账本为空或最新版本不等于 before，先追加 before；随后仅在最新版本不等于 after 时追加 after。相邻同版本不得重复；恢复到更早版本时因其与最新不同，仍须形成新时间点。
5. 插入和裁剪只 `flush`，绝不 `commit`、`rollback`、`refresh`、查询项目、取得第二把锁或记录审计；事务成败完全由未来 B 包调用方控制。
6. 返回值只能是本轮新增数量和最终版本等内部最小结果，不返回 snapshot、项目/空间或行 ID。

A 包不允许任何生产代码调用该函数。B 包接入时，历史记录失败必须与对应 editor-state 业务写同事务回滚，禁止“正文成功但历史失败”或反向部分成功；具体调用点、旧无 expected 写入锁化和每类来源映射必须另行冻结。

## 5. 安全与隐私

自动修订包含完整标书编辑态，属于服务端敏感正文。A 包禁止新增 API、Schema、路由、前端、浏览器存储、URL、console、下载、导出、搜索、日志或审计正文；测试不得使用真实人员、项目、招标文件或密钥。内部错误使用固定 code，不拼接 snapshot、版本、项目、SQL 或异常原文。

项目删除依赖数据库外键级联清理。A 包不提供单条/批量 DELETE，不提供保留期设置，不接受客户端 source，不修改手动检查点列表、恢复裁剪或 D2 面板。

## 6. P12C-A 文件边界

只允许：

1. `backend/app/models/entities.py`
2. `backend/app/services/editor_state_revision_service.py`（新增）
3. `backend/tests/test_editor_state_revisions.py`（新增）

禁止修改 API/Schema、数据库启动补列、既有 editor-state/checkpoint 服务、任务/revise/callback/P8C/M3-D、前端、配置、依赖、锁文件和其他测试。Grok 不得 `git add/commit/push`。

## 7. 反假绿验收

专项必须真实覆盖：新表列/约束/外键/索引；精确来源枚举；首个 transition 写 before+after、同版本只写一条；连续 transition 只追加新 after；账本与 before 不连续时补 before；恢复到旧版本形成新行；相邻同版本去重；第 11 条后稳定只留最近 10 条；并列时间戳按 id 稳定；其他项目/空间零误删；项目删除级联。

还必须覆盖：13 键/版本委托共享算法；缺失/非法/带空白/不匹配版本、非规范值、NaN/Infinity、超限和非法 source 固定失败；任一步异常不 commit，调用方 rollback 后新行精确为零；原语不查询项目、不取得锁、不 refresh；最新读取与裁剪 SQL 投影不含 `snapshot_json`；返回值无 snapshot/ID/项目/空间；P12A/P12B-D 现有检查点数量和内容完全不变。

禁止只测纯函数、mock 掉真实 SQLite、以宽泛异常/状态集合放行、固定 sleep、`or True`、从客户端构造 snapshot、顺序调用冒充并发，或因 A 包存在就声称生产写入已自动留史。

## 8. 非目标与后续闸门

P12C-A 不产生用户可见功能，不接任何生产写入者，不新增历史列表/详情/恢复/删除/diff/搜索/分页，不提供任意版本、分支、合并、命名、标签、发布、审批、跨项目浏览或多人实时协作。

P12C-B 已证明浏览器 PUT、九类任务、五类商务 revise、个人 callback、P8C 票据 callback、content-fuse apply/consume 与 checkpoint restore 八类来源均按各自事务边界原子留史。P12C-C1 已交付最小元数据列表与按需详情；权限、正文出域和 SQL 投影均按独立契约验收。是否复用 P12B-D restore 留给 C2，禁止从只读 API 推断恢复已可用。

## 9. P12C-A 实现与验收记录

实现提交 `226e1c1` 新增独立 `editor_state_revisions` 表、固定 8 类内部来源、每项目最近 10 条裁剪和无提交 `record_editor_state_transition`。A 包没有生产调用、API、Schema 或前端入口，不能据此声称自动历史已可用。

Grok 首轮全量为 636 passed / 1 failed，失败来自并列时间戳测试在统一时间后继续 transition，随机 ID 稳定排序与插入顺序不等价；测试改为先完成 transition、再统一时间并只验证 `created_at DESC, id DESC`。Codex 随后发现缺任一权威键时 `extract_canonical_snapshot(...get...)` 会补 `None`，攻击者可携带匹配版本让假全状态入账；失败先测得到 28 failed / 1 passed，返修后要求 13 个权威键全部存在，同时允许 `projectId/updatedAt/responseMatrixVersion` 等服务端派生额外键。跨工作空间裁剪隔离、DELETE 行 ID 精确条件和合法 32 位夹具 ID 亦已补齐。

Codex 独立验收结果：P12C-A 专项 **67 passed**，P12A/P12B-D 受影响回归 **77 passed**，后端串行全量 **666 passed**；均只有 1 条既有 Starlette/httpx 弃用告警。`py_compile`、精确三文件白名单、工作树与暂存区 diff 检查全部通过。

## 10. P12C-B-A 浏览器 PUT 接入记录

冻结提交 `fbf93c0`、实现提交 `acf3139`。公开 `PUT /api/projects/{project_id}/editor-state` 唯一传服务端字面量 `browser_put`；服务内部来源默认 `None`，来源存在时复用现有项目写锁、锁后 before、提交前 after 与唯一 commit。当前仅浏览器 PUT 自动记账，未新增任何公开历史 API、Schema 或前端。

Grok failure-first 为 11 failed / 1 passed，首版专项/受影响/全量为 12/107/678 passed。Codex 两轮返修后独立通过 **14 / 107 / 680 passed**，关闭并列时间戳、跨空间 404、flush 后脱敏 500 与 commit 前已 flush/失败双零写证据；编译、白名单和 diff 检查通过。

## 11. P12C-B-B1 九类任务接入记录

冻结提交 `05864f6`、实现提交 `5a0d1c0`。`task_service.py` 5 个技术 writer 与 `business_task_service.py` 4 个商务 writer 均经私有包装器固定传 `task`；每次 upsert 的 editor-state/revision 共用原子事务，批量章节保持逐章提交与版本自推进。该 B1 实现提交未接入 export、response_match、content-fuse、商务 revise 和其他写入者；商务 revise 已由后续 B2 独立接入。

Grok failure-first **8 failed / 2 passed**，首版专项/受影响回归 10/109 passed。Codex 一次返修关闭内部 upsert 异常经任务 REST/SSE 泄露、章间漂移逻辑优先级假绿、宽松增量与空集合来源断言；独立通过专项 **10**、扩展受影响回归 **126**、后端串行全量 **690 passed**。编译、精确三文件白名单和 diff 检查通过。

## 12. P12C-B-B2 商务 revise 接入记录

冻结提交 `3a30c03`、实现提交 `5149385`。`revise_service.py` 的两个真实 upsert 写点固定传 `revise`，覆盖 `business_parse` 与四类结构化商务阶段；结构化解析失败、空 revised、普通技术 revise、陈旧 expected 和 LLM 期间漂移不产生本次 `revise` 修订。同步 HTTP 失败继续由既有全局 500 脱敏，未新增包装器。

Grok failure-first **6 failed / 5 passed**，最终专项/受影响回归 11/122 passed。Codex 独立通过专项 **11**、扩展受影响回归 **147**、后端串行全量 **701 passed**；编译、精确双文件白名单和 diff 检查通过。recorder flush 与 commit 失败均已证明 editor-state/revision 双零，外部 `browser_put` 漂移按来源和精确版本排除。B2 交付时个人 callback、P8C 一次性本地解析 callback、content-fuse 与 checkpoint restore 均未接入，随后个人 callback 已由 C1 完成。

## 13. P12C-B-C callback 审计与拆包

只读审计确认个人 callback 与 P8C 票据 callback 不能合包：前者在现有项目锁下把 editor-state、成功任务与项目步骤用唯一 commit 原子提交，任何失败统一 rollback；后者在版本陈旧或旧空版本票据时必须单独 commit 票据消费并返回 409，只有非版本中途异常才回滚并允许票据重用。

因此 B-C1 只改 `parse_callback.py` 与独立新测试，在个人 callback 唯一 commit 前用锁后 before、内存 after 和固定 `callback` 调用无提交原语。冻结=`76834f5`、实现=`1d0ce0e`；Codex 独立通过 **10/224/711 passed**。B-C2 随后以 `52bbabf`/`82cc82e` 独立证明 fresh `local_parser` 原子留史、stale/null 仅消费无修订和失败票据可重用；Codex 独立通过 **20/272/721 passed**。完整边界见 `docs/p12c-callback-revision-integration-contract.md`。

## 14. P12C-B-D1 content-fuse apply 接入记录

冻结=`e8ffaeb`、实现=`a6a28f6`。融合 apply 复用同一次锁后 before/行，在章节、恢复批次和裁剪全部暂存后从同一内存行构造 after，以固定 `content_fuse_apply` 在唯一 commit 前记账；一至五条建议同批只形成一次迁移，空账本形成 before+after。consume 与 checkpoint restore 未被接入。

Grok failure-first **9 failed / 2 passed**，首版专项/受影响回归 **11/184 passed**。Codex 一次仅测试返修关闭完整/部分 consume 可能以其他来源误写仍通过的假绿点，以逐行身份序列前后全等收紧隔离；最终独立通过专项 **11**、扩大回归 **285**、后端串行全量 **732 passed**，双文件编译、diff 与白名单检查通过。D1 当时要求 D2 重新冻结 restored>0 与零恢复只消费语义；该动作随后已完成，D3 继续独立分包。

## 15. P12C-B-D2 content-fuse consume 接入记录

冻结=`6b83fc1`、实现=`f256f5b`。融合 consume 复用锁后 before/同一状态行：完整或部分恢复只在原唯一 commit 前固定记录一次 `content_fuse_consume`，不按恢复章节数多记；零恢复仍原子消费批次，但 13 键、`updatedAt`、版本及全部修订身份序列精确不变。成功路径不再用 `get_editor_state` 重读，checkpoint restore 未被接入。

Grok failure-first **11 failed / 13 passed**。Codex 两轮仅测试返修依次关闭部分恢复宽松集合、跨项目恒真自比较、缺失真实跨空间公开 HTTP、并发任意 409、零恢复部分字段比较、500 固定表名/路径泄漏门，以及外空间 editor-state 只比三个字段。最终独立通过 D1+D2 专项 **25**、扩大受影响回归 **299**、后端串行全量 **746 passed**，三文件编译、diff、白名单和分支/远端检查通过。D2 交付时生产写入已覆盖浏览器 PUT、九类任务、五类商务 revise、个人 callback、P8C callback、content-fuse apply 与 consume；随后 D3 已独立完成，P12C-C 当时未提前开始。

## 16. P12C-B-D3 checkpoint restore 接入记录

冻结=`1d44484`、实现=`b91a7ff`、闭环=`d07012b`。不同规范版本 restore 在目标复核后、检查点裁剪与唯一 commit 前固定记录 `checkpoint_restore`；同内容仍创建安全检查点并更新 `updatedAt`，但零修订；回到历史版本形成新时间点。Codex 两轮 test-only 返修后独立通过 **18/270/764 passed**，八类内部来源接入至此闭环。

## 17. P12C-C1 只读历史接口冻结

C1 冻结=`26b504e`、实现=`7023ecd`。只新增当前项目最近 10 条元数据列表与单条详情 GET；列表固定五列投影且不加载 `snapshot_json`，详情以 revision/workspace/project 三重作用域读取六列并严格重验规范快照。Codex 对物化阶段坏时间做一次受限返修后，独立通过 **13/201/777 passed**；真实越界字节、非法来源、坏时间和正文损坏均固定脱敏 500/no-store，完整 GET 零写。恢复、删除、diff、搜索、分页、前端与多人协作均不在 C1，下一步只能独立审计并冻结 C2。

## 18. P12C-C2 受限 revision restore 完成

C2 冻结=`54af600`、范围修订=`2276366`、实现=`0803250`。固定新增 `POST .../editor-state-revisions/{revisionId}/restore` 与准确来源 `revision_restore`，不得用 `checkpoint_restore` 冒充。恢复锁后 CAS、三重作用域读取并重验目标、原子保存安全检查点、共享 13 键写回、不同内容记新时间点、同内容零修订，最后在同一事务内完成修订 10 条与检查点 20 条裁剪。

Codex 用真实 DROP 前故障注入发现旧 SQLite 迁移首版在失败后残留临时表；Grok 受限返修为 CREATE 前零行 DML 触发物理事务后，失败路径完整保留旧 DDL/八列/索引/FK/CHECK 且不留临时表。独立通过专项 **23**、四文件 **121**、后端串行全量 **800 passed**；前端无改动沿用 **263 passed**。精确 API、11 文件白名单和完整反假绿记录见 `docs/p12c-revision-restore-contract.md` 与 `docs/plans/2026-07-16-p12c-revision-restore-plan.md`。前端、删除、diff、搜索和多人协作不在 C2；下一包只能先审计并冻结 C3 前端边界。

## 19. P12C-C3 双工作区前端冻结

C3 只新增独立 revision API/共用折叠面板，并在技术/商务 hook 与页面复用 P12B-D2 已验收的版本化外部写队列。默认折叠零请求，列表最多 10 条，详情只在点击后读取并在 API 层压缩成有界计数摘要；原始 13 键快照不进入组件，revision ID 和版本只保留在内存，不进入可见 DOM、URL、存储或日志。恢复二次确认后使用执行时最新 expected，成功唯一 editor-state GET；两个面板共用操作令牌，检查点与修订恢复不能并发写。

精确七文件白名单、失败矩阵和单 worker、零重试 E2E 命令见 `docs/p12c-revision-history-frontend-contract.md` 与 `docs/plans/2026-07-16-p12c-revision-history-frontend-plan.md`。C3 不修改后端或检查点模块，不实现删除、diff、搜索、分页、跨项目历史、自动历史或多人协作。
