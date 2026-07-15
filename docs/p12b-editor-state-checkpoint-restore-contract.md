<!--
模块：P12B-D editor-state 检查点安全恢复契约
用途：冻结技术标/商务标检查点的恢复前安全检查点、全状态 CAS、同事务原子恢复和前端显式入口。
对接：P12A 手动检查点只读库；P12B-A/B/C 全状态版本、浏览器串行队列与延迟写入围栏。
二次开发：本包不是自动历史、任意时间线或多人协作；禁止客户端投稿快照、静默覆盖、自动重试和恢复后旧 UI 回写。
-->

# P12B-D editor-state 检查点安全恢复契约

> **状态**：已完成、独立验收并推送。冻结=`613818f`，D1 后端=`551caba`，D2 前端=`0f81dd6`。
> **工作分支**：`collab/grok-code-codex-review`。
> **执行拆包**：D1 后端原子恢复 → Codex 独立审查/验收 → D2 双工作区显式检查点面板 → Codex 独立审查/验收与文档闭环。
> **前置基线**：P12A 已提供服务端权威 13 键检查点、最近 20 条元数据列表和按需详情；P12B-A/B/C 已提供稳定 `stateVersion`、浏览器串行保存链以及所有既有延迟写入的锁后 CAS。

## 1. 审计结论与最小方案

P12A 的检查点创建函数会自行加锁、裁剪并提交，不能在恢复事务内直接调用。当前 editor-state 的 ORM 映射还包含 analysis/analysisOverview 双写、商务整包和响应矩阵收敛；若恢复服务自行复制字段映射，很容易出现“检查点摘要合法，但写回后版本变化”的第二套算法。

P12B-D 因此只增加一条显式恢复链：

1. 客户端必须提交当前内存中的合法 `expectedStateVersion`，服务端先取得 P12B 共用项目写锁并比较当前完整 13 键版本。
2. 服务端在同一锁、同一数据库事务内重新读取并严格验证用户选中的 P12A 检查点。
3. 覆盖当前 editor-state 前，服务端先把**当前权威状态**写成一条新的恢复前安全检查点；该检查点不是客户端正文，也不能省略。
4. 服务端通过 `editor_state_service` 的共享无提交映射原语，把目标检查点精确写回当前行，再独立重算结果版本；结果必须等于目标检查点的 `state_version`。
5. 安全检查点插入、13 键覆盖、最近 20 条裁剪和 commit 必须原子完成；任何一步失败全部 rollback。
6. 前端恢复必须进入各自现有保存链，执行时才读取最新 expected；成功后作废恢复前写入代次并只做一次 editor-state GET。409、网络不确定或非法成功响应不得自动重试。

本包允许用户明确恢复到与当前内容相同的检查点：仍创建一条恢复前安全检查点，成功版本可与恢复前版本相同。这样恢复动作的安全语义稳定，不新增含糊的“无变化”分支。

## 2. D1 后端 API

新增：

`POST /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/restore`

请求体只能是：

```json
{"expectedStateVersion":"esv_0123456789abcdef0123456789abcdef"}
```

规则：

- `expectedStateVersion` 必填，精确匹配 `^esv_[0-9a-f]{32}$`；缺失、空白、非法格式、`expected_state_version` 或额外字段固定 422。
- 不接受 snapshot、stateVersion、名称、备注、projectId、workspaceId、force、dryRun 或任何覆盖选项。
- required 模式继续由既有中间件校验 CSRF，工作空间权限继续只允许 strict `bid_writer`；owner 不绕过。disabled 个人模式保持可用。
- 技术标与商务标项目都允许恢复；检查点必须同时属于当前 workspace 和当前 project。

成功固定 200，响应只能是：

```json
{
  "restoredCheckpointId":"escp_...",
  "safetyCheckpointId":"escp_...",
  "stateVersion":"esv_...",
  "restoredAt":"2026-07-15T..."
}
```

其中 `stateVersion` 必须等于目标检查点中已验证的版本；`restoredAt` 与本轮 editor-state 内存行更新时间一致，响应必须在 commit 前构造，commit 后禁止 refresh、再次 GET 或重新计算。所有成功与固定业务错误都带 `Cache-Control: no-store`。

## 3. 锁、校验与事务顺序

D1 服务固定按以下顺序执行，全部位于一个显式 try/rollback 域：

1. 调用 `editor_state_service.lock_and_assert_expected_state_version()`，取得同项目写锁、唯一当前 ORM 行和规范当前状态；全状态 CAS 是第一业务闸门。
2. 用 `checkpoint_id + workspace_id + project_id` 三重 SQL 条件读取目标检查点；不存在或跨项目/空间统一 404，禁止先全局主键读取正文再用 Python 过滤。
3. 调用 P12A 既有严格快照校验：规范 JSON、精确 13 键、UTF-8 字节、摘要、outline/chapter 计数任一不一致固定损坏错误。
4. 从锁后当前状态抽取规范 13 键，生成恢复前安全快照；按 UTF-8 计算 1～2 MiB、版本和计数。当前状态超过检查点上限时固定 413，目标状态与当前状态均不改。
5. 插入一条新的安全检查点。安全检查点的版本必须等于锁后当前 `stateVersion`；ID 与正文只由服务端生成。
6. 调用新增的共享无提交原语 `editor_state_service.apply_canonical_snapshot_to_locked_row()` 写回全部 13 键。该原语只能操作已经持锁的行，不得自行锁、查询项目、commit 或 refresh。
7. 用写回后的规范状态重新计算 `stateVersion`；若不等于目标检查点版本，按损坏检查点处理并 rollback。该二次验证必须捕获 analysis 双写、mode 收敛、商务结构或矩阵重建造成的语义漂移。
8. 裁剪为每项目最多 20 条。新安全检查点必须被保护、绝不能因时间戳并列或随机 ID 排序被本轮裁剪；若目标检查点本来是最旧记录，可以按“最近 20 条”规则自然淘汰。
9. 在 commit 前构造响应，然后一次 commit。插入、写回、裁剪或 commit 任一异常都必须回滚 editor-state 与安全检查点。

禁止复用会自行 commit 的 `create_editor_state_checkpoint()` 或 `upsert_editor_state()`；禁止在检查点服务复制 13 键哈希、ORM 列映射或响应矩阵收敛算法。

## 4. 固定错误语义

| 场景 | HTTP / detail | 写入结果 |
|---|---|---|
| 当前全状态版本陈旧 | 409；精确 `code/message/currentStateVersion`，复用 `editor_state_version_conflict` | editor-state 不变，零安全检查点 |
| 项目不存在/跨空间 | 404 `project_not_found` | 零写入 |
| 检查点不存在/跨项目/跨空间 | 404 `editor_state_checkpoint_not_found` | 零写入 |
| 目标检查点存储损坏或写回后版本漂移 | 500 `editor_state_checkpoint_corrupt` | 零恢复，零安全检查点 |
| 当前安全快照超过 2 MiB | 413 `editor_state_checkpoint_too_large` | 零恢复，零安全检查点 |
| Schema 非法 | 422 | 路由不进入服务 |
| 数据库/序列化/commit 异常 | 既有脱敏 500 | 显式 rollback，零部分成功 |

错误 detail 不得包含目标/安全检查点 ID、快照、正文、标题、矩阵、商务字段、项目路径、SQL、异常原文、Cookie、Token 或用户信息。409 的 `currentStateVersion` 仅用于既有冲突协议，前端不得写入日志、URL、浏览器存储或界面。

## 5. D2 前端显式入口

D2 在技术标和商务标工作区页头后复用一个 `EditorStateCheckpointPanel`，交付以下最小能力：

1. 折叠入口“版本检查点”；展开后才 GET 最近 20 条元数据，不请求详情 snapshot。
2. “保存服务器当前版本”必须先把当前 UI 最新状态通过既有保存链执行一次受控即时 PUT，再 POST P12A 的精确空对象 `{}`。POST 返回版本必须等于前一步已接受的服务端版本；不一致说明期间出现远端变更，进入全状态阻断并要求显式重载。
3. 列表只展示创建时间、outline 节点数、章节数和格式化大小；checkpointId/stateVersion 只保留在内存中作 key/请求参数，不直接展示。
4. 每条提供“恢复”按钮，第一次点击只进入内联确认态；用户再次点击“确认恢复”才 POST restore。确认文案必须说明“当前服务器内容会先自动保存为安全检查点，恢复会替换全部技术标和商务标编辑态”。
5. restore 进入各自既有串行保存链，真正执行时读取最新合法 expected。它必须等待之前普通 PUT；POST 成功响应缺失/非法版本、409、网络 abort 或其它不确定失败均保留本地 UI、停止自动保存、零自动重试。
6. POST 合法成功后先接受新版本并阻断旧 UI，递增写入 epoch、清未发送防抖 PUT，只做唯一一次 editor-state GET。GET 成功才解除阻断并水合恢复内容；GET 失败显示“恢复已完成，但刷新失败，请重新载入远端内容”，业务不得重试。
7. 恢复成功后可另做一次检查点**列表** GET，用于显示新安全检查点；该请求不计入唯一 editor-state GET，但不得请求详情正文。
8. 项目切换、折叠/卸载和重复点击必须用项目会话代次隔离迟到 list/create/restore；旧项目结果不得改新项目正文、提示、列表、阻断状态或发额外请求。

创建检查点和恢复在全状态阻断、初始加载失败或版本未知时禁用。界面只用固定中文，不拼接后端异常；不得使用 localStorage/sessionStorage/IndexedDB、URL、Cookie、剪贴板、下载、console、轮询、自动定时检查点或外网。

## 6. 精确文件白名单

### D1 后端原子恢复

- `backend/app/api/schemas.py`
- `backend/app/api/editor_state_checkpoints.py`
- `backend/app/services/editor_state_checkpoint_service.py`
- `backend/app/services/editor_state_service.py`
- `backend/tests/test_editor_state_checkpoint_restore.py`（新增）

D1 禁止修改实体、main、认证/CSRF/数据库基础设施、项目路由、任务/revise/callback/M3-D、前端、依赖、锁文件、文档或既有测试；不得 commit/push。

### D2 双工作区显式入口

- `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts`（新增）
- `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx`（新增）
- `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
- `frontend/e2e/editor-state-checkpoint-restore.spec.ts`（新增）
- `frontend/e2e/technical-editor-state-truth.spec.ts`（仅允许受新请求影响的路由桩/断言机械对齐）
- `frontend/e2e/business-editor-state-truth.spec.ts`（仅允许受新请求影响的路由桩/断言机械对齐）

D2 不新增 CSS 文件或依赖，不改后端、路由总表、M3-D 对话框、认证、共享 API 基础设施、配置或文档；不得 commit/push。

## 7. 反假绿验收

D1 至少证明：

- 技术标与商务标分别恢复完整 13 键；结果版本用独立规范 JSON 算法重算并精确等于目标版本；恢复前安全检查点详情精确等于恢复前状态。
- 空项目、同内容恢复均成功且创建安全检查点；同内容恢复版本不变。
- 陈旧 expected 固定 409 且目标状态/安全记录零写；两个同 expected 并发恢复只能一成一败。
- 目标不存在、跨项目/空间、损坏 JSON/键集/字节/摘要/计数/非规范 JSON/语义不一致全部固定失败且零部分写。
- 当前安全快照超 2 MiB、插入/写回/裁剪/commit 故障全部 rollback；提交成功后不 refresh/重读。
- 原有 20 条时恢复后仍精确 20，安全检查点必保留，其他项目/空间不误删。
- required strict role、owner 不绕过、CSRF、disabled、`no-store`、请求精确 Schema 和错误脱敏真实覆盖。

D2 至少逐个技术标/商务标模式证明：

- 立即编辑后点击创建：即时 PUT 先完成，POST `{}` 后发，检查点版本等于 PUT 成功版本；PUT 挂起时 POST 数量为 0。
- 普通 PUT 挂起时 restore POST 为 0；PUT 成功后 restore 的 expected 精确等于 PUT 响应版本。
- 恢复成功只发一个 editor-state GET，水合目标全部相关字段；新安全检查点出现在元数据列表。
- 409、网络 abort、200 缺失/非法/带空白版本逐轮保留本地正文、零自动重试、两个防抖窗口零 PUT、零 `pageerror`/unhandled rejection。
- POST 成功但唯一 editor-state GET 失败时显示业务已完成提示并保持阻断；用户显式重载后才恢复。
- A→B、折叠/卸载、迟到 list/create/restore 不污染；按钮双击只有一个业务请求；确认前 restore 为 0。
- 不请求 checkpoint 详情正文，不展示 ID/version，不写浏览器存储/URL/console，不启动外网。

禁止 `or True`、宽泛状态码、固定 sleep 代替请求门、顺序调用冒充并发、只断言提示不检查请求体/次数/时序、route fallback 伪造成功或把网络不确定当确定失败继续自动保存。

## 8. 非目标

- 不做自动检查点、每次 autosave 历史、命名/备注/标签、删除、下载、导出、diff、搜索、分页或跨项目浏览。
- 不做任意版本时间线、分支、合并、发布、审批、多人实时协作或后台自动恢复。
- 不恢复项目元数据、文件、任务、模板、M3-D 批次、反馈历史、知识库、财务/人力/投标人数据；只恢复当前项目 editor-state 的精确 13 键。
- 不修改 P12A 检查点创建请求的空对象契约，不让客户端上传快照或指定安全检查点内容。
- 不自动使用 409 的 `currentStateVersion` 重发，不提供 force overwrite，不让恢复前旧 UI 以新版本自动 PUT。

## 9. 完成闸门

D1 与 D2 必须分别经历 Grok 实现、自测、Codex 受限 diff/安全审查、必要返修和独立验收，再由 Codex 单独中文提交并推送。后端全量、前端单 worker 串行全量、lint、build、`git diff --check` 和暂存区检查全部通过后，才能把 P12B-D 标为完成并更新 HANDOFF、路线图与联调清单。

## 10. 交付闭环（2026-07-15）

D1 后端按五文件边界交付恢复请求 Schema、同一锁/事务内的当前版本 CAS、安全检查点插入、目标快照严格重验、13 键共享写回、写回版本复核、最近 20 条裁剪和固定 `no-store` 路由。Grok 首版后，Codex 拒绝了跨项目目标检查点查询先取整行、损坏元数据异常边界不完整和过宽异常断言；定点返修后独立通过恢复专项 **58 passed**、受影响回归 **81 passed**、后端串行全量 **599 passed**，实现提交 `551caba`。

D2 前端按七文件实际边界交付共用折叠面板、最小元数据 API、技术/商务即时保存后创建和版本化安全恢复。四轮受限审查依次关闭：forced-create 不确定失败未阻断、商务恢复后误 PUT、宽松响应 shape 与固定 sleep；跨项目共享布尔守卫和未真正入闸的迟到测试；禁用按钮 `force:true` 假令牌证据、create 成功体非法版本未阻断和不完整水合；最后关闭 HTTP `ApiError.code` 冒充内部版本错误身份。Codex 独立通过 D2 专项 **51 passed**、受影响回归 **63 passed**、lint/build/diff；前端全量首跑在第 240 项出现纯白页，精确失败用例 **1 passed** 后完整单 worker、零重试重跑 **263 passed**，实现提交 `0f81dd6`。

Grok D1 审查链为 `msg_f4f03a8c9e7b44f89bef02cf706ee975` → `msg_c8daf29394314fbb8c9d96e8c32cf902` → `msg_6690fc4c8b2f4cb8a3c7c3516ef936fd` → `msg_3f70df5de44545eabc53d46715a6910a`；D2 实现与四轮返修最终回执依次为 `msg_1072d2d077c14b0cbb269f2f9a161fc5`、`msg_5799855ef6be41798063dde5a0f1410e`、`msg_bd4138fa5ea44ae3bb927f548a5c9808`、`msg_9e0f4926f1fa41e0b6cd07d555d888f5`、`msg_a37557e7e11543df93d0599bf580ac83`；Codex 最终确认=`msg_94a365e64f9f424f93d46ffdd2e344d7`。

本包仍不包含自动检查点、每次 autosave 历史、任意版本时间线/浏览/回滚、命名、删除、下载、diff、搜索、跨项目历史或多人协作。任何后续版本库能力必须重新只读审计并冻结独立数据保留、授权、并发和隐私契约。
