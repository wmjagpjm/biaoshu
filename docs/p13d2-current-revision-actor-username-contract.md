# P13-D2 当前已载入版本操作者用户名展示契约

> 状态：已冻结，待 Grok 实现与 Codex 独立验收
> 日期：2026-07-20
> 前置：P13-B 当前版本时间、P13-C 当前修订来源、P13-D1 修订操作者可信账本
> 后续：工作空间切换与成员可见性（须另行冻结）

## 1. 目标

`GET|PUT /api/projects/{projectId}/editor-state` 新增必出可空字段 `currentRevisionActorUsername`。服务端只查看当前项目最新一条修订；仅当该修订版本与响应 `stateVersion` 精确相等、actor 可可信解析为当前仍有效的同工作区成员时，返回其当前用户名，否则返回 `null`。

技术标和商务标标题区复用 P13-B/C 已有响应接受门，在来源行下展示 `当前版本操作者：<用户名>`；未知时展示 `当前版本操作者：操作者未知`。本包只解释“当前客户端已接受版本的可信操作者”，不承诺远端实时最新、在线状态或历史身份快照。

## 2. 已选择方案与取舍

采用“一次最新修订查询 + 同工作区成员/用户左联表”的保守方案：

1. 最新修订仍按 `created_at DESC,id DESC LIMIT 1` 唯一决定，不回扫旧同版本。
2. 同一次 SQL 只投影 `state_version/source_kind/username/user_is_active/member_is_active`；不得加载 `snapshot_json`、口令、摘要、会话、审计或内部 actor ID。
3. 来源与用户名从同一最新行独立校验：来源损坏只使来源为 `null`，不应在用户名仍可信时连带抹除；用户名不可解析也不影响合法来源。
4. 不采用“只按 user ID 查用户名”，因为这会在 actor 已不属于该工作区时继续披露身份。
5. 不在修订表保存用户名快照，因为那会新增迁移、扩大九类写链并改变 P13-D1 的不可见账本边界；历史名义语义后续须独立立项。

## 3. 用户停用、删除、改名与成员语义

`currentRevisionActorUsername` 非空必须同时满足：

- 最新修订的 `actor_user_id` 非空，并能关联到 `local_users`；
- 用户 `is_active` 原始值精确为真；
- 该用户在修订所属 `workspace_id` 有成员行，成员 `is_active` 原始值精确为真；
- 用户名通过第 4 节安全文本门。

否则统一 `null`。具体语义：

| 情况 | 展示 |
|---|---|
| P13-D1 required 模式真实 actor，用户与成员均启用 | 当前用户名 |
| disabled、旧修订、补账 before、actor 为空 | 未知 |
| 用户不存在、已删除或已停用 | 未知 |
| 同工作区成员不存在、已移除或已停用 | 未知 |
| 用户仍是活动成员但角色变更 | 当前用户名；角色不参与历史归因 |
| 用户名将来被管理面修改 | 新的当前用户名；本包不声称保存历史名称 |
| actor 仅属于其它工作区 | 未知，禁止跨工作区披露 |

当前系统没有用户名重命名入口；表格中的改名语义用于约束未来兼容和直接数据修复，不授权本包新增改名 API。

## 4. 响应与安全文本契约

### 4.1 后端字段

- `EditorStateOut.current_revision_actor_username: str | None`，序列化别名固定 `currentRevisionActorUsername`。
- GET/PUT 200 必须包含该键；无账本、版本不匹配或任一校验失败时值为 JSON `null`。
- 非空用户名必须为原生字符串、1..100 个 Unicode 码点、无首尾空白；拒绝 C0/C1/DEL、U+2028/U+2029 与双向控制字符 `U+061C/U+200E/U+200F/U+202A..U+202E/U+2066..U+2069`。
- 不 trim、不 NFKC 改写、不小写、不从当前登录会话补值；合法用户名原样返回。
- body、query、header 中的同名字段或 `actorUserId/actor_user_id` 均不得影响结果。

### 4.2 前端解析与展示

- `parseRevisionActorUsername` 独立重复相同安全文本门；缺失、`null`、非字符串、空白包裹、超长或控制字符均归一为 `null`。
- 技术标 testid 固定 `technical-editor-version-actor`；商务标固定 `business-editor-version-actor`。
- 用户名只作为 React 文本节点渲染，不进入 HTML、属性、title、URL、存储、Cookie、console、剪贴板、下载、外网或错误消息。
- 不显示 `actor_user_id`，不根据当前会话、来源标签、任务类型或页面角色猜用户名。

## 5. 接受门与状态机

操作者用户名与 P13-B 时间、P13-C 来源共用同一合法 `stateVersion` 接受时点：

1. 初始 GET、显式刷新、成功 PUT、矩阵 PUT/合并 PUT、恢复后 GET，以及版本化外部写后的唯一 GET，均只从已通过既有 session/write epoch 与合法版本门的同一响应接受三项元数据。
2. 项目切换或会话重置立即清空为 `null`；旧项目 success/catch/finally 不得污染或解锁新项目。
3. 409、网络失败、HTTP 失败、非法/缺失 `stateVersion` 不得单独覆盖已接受的时间、来源或操作者；沿用既有保值/阻断语义。
4. 合法版本响应内用户名缺失或非法时，该次已接受版本的操作者精确更新为未知，不保留上一版本用户名。
5. 外部 POST/SSE/任务事件不得直接投稿用户名；只有随既有 editor-state GET 返回且通过门后才能更新。
6. 禁止新增 GET、轮询、重试、定时器、订阅或浏览器持久化。

## 6. 后端查询与事务边界

- 将 P13-C 单字段解析扩展为当前修订元数据解析，生产 `_editor_out` 每次只调用一次。
- 查询必须限定 `workspace_id + project_id`，按最新一条排序并 `LIMIT 1`；成员联接还必须限定同一 `workspace_id`。
- 用户和成员启用位应以原始整数/等价严格投影校验，非法布尔值不得被 truthy 宽判为启用。
- GET 继续零写；解析器不得 add/delete/flush/commit/rollback/refresh/裁剪，也不得查询 Project 或当前会话。
- PUT 沿用原业务与修订事务；响应解析发生在既有成功提交之后。若提交后已有并发漂移，最新修订版本不匹配则两项元数据均保守 `null`。
- 不改变 13 键快照、`stateVersion`、`updatedAt`、CAS、修订生成、actor 写入、裁剪、固定、搜索、恢复和历史 API。

## 7. 允许修改范围

生产文件严格九个：

- `backend/app/api/schemas.py`
- `backend/app/api/projects.py`
- `backend/app/services/editor_state_revision_service.py`
- `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
- `frontend/src/features/editor-state-collaboration/EditorStateVersionFreshness.tsx`
- `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

测试文件：

- 新增 `backend/tests/test_p13d2_current_revision_actor_username.py`
- 受限同步 `backend/tests/test_p13c_current_revision_source.py` 的精确投影/零写合同
- 扩展 `frontend/e2e/editor-state-version-freshness.spec.ts`

若旧 P12C 测试只因响应新增合法只读键而失败，须先提交证据并由 Codex 授权 test-only 机械同步；不得预防性修改。禁止修改模型、迁移、身份 API、成员 API、revision 历史 API、样式、配置、依赖或其它业务。

## 8. failure-first 与验收门

1. 生产文件哈希冻结后先写专项红测，必须在生产改动前记录真实失败与首个业务失败；不得用 import/signature/source-text 存在性冒充行为失败。
2. 后端覆盖 GET/PUT 非空、无账本/actor null、最新版本不匹配不回扫、用户/成员缺失或停用、跨工作区、角色变化、当前名语义、用户名坏值、来源与用户名独立校验、客户端注入、actor ID/敏感列不泄漏、精确一条 SQL 与零写。
3. 前端覆盖技术/商务有效中文名与未知、严格坏值、GET/PUT 同门更新、项目切换立即清空、A→B 迟到隔离、409/失败保值、非法版本阻断、外部写唯一 GET、零新增请求/定时器/存储与 ID 泄漏。
4. Grok 只运行 P13-D2 专项、P13-B/C 直接受影响 E2E、lint/build、py_compile 和 diff-check；pytest 串行，Playwright 固定 `--workers=1 --retries=0`。
5. Codex 独立审查查询投影、同工作区联接、安全文本、接受门和测试反假绿；按证据选择 P13-C/P12C 定点回归，不机械重复后端全量或整仓 318 E2E。
6. 最终须通过生产九文件白名单、测试授权白名单、`git diff --check`、公开 actor ID/敏感字段泄漏门、空暂存区和中文文档闭环。

## 9. 明确不做

- 不展示历史列表/详情 actor，不按 actor 搜索、筛选、分页或统计。
- 不做用户名快照、重命名入口、成员管理 UI、活动工作空间切换 UI或跨工作区身份目录。
- 不做 presence、心跳、在线状态、实时协同、光标、章节锁/租约、评论、审批、通知。
- 不扩 SSE/WebSocket、事件广播、游标重放、多任务总线或断线恢复。
- 不增加数据库列、索引、外键、迁移、缓存、请求、轮询或依赖。
