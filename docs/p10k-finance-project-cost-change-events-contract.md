<!--
模块：P10K 财务项目成本变更记录契约
用途：冻结严格财务角色按当前空间商务标项目读取上线后成功成本变更记录的数据、权限、事务与前端边界。
对接：docs/plans/2026-07-14-p10k-finance-project-cost-change-events-plan.md；P10C finance_cost_service；/api/finance/business-bids/{projectId}/cost-change-events。
二次开发：本包不是完整财务审计；不得从旧审计日志反推项目，不得返回金额、业务正文、成员身份、失败尝试或变更前后值。
-->

# P10K 财务项目成本变更记录契约

> **状态**：方案已冻结，等待 Grok 先实现后端受限包；前端须在后端独立验收提交后另行派发。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 432 passed；前端 lint/build 通过、单 worker 串行全量 E2E 131 passed。

## 1. 审计结论与方案选择

P10J 的唯一数据源 `auth_audit_events` 只有 workspace、actor、action、result、`fce_*` target 和时间，没有项目 ID。现存成本条目可关联项目，但删除事件发生后条目已不存在；用当前条目 JOIN 会永久漏掉删除历史，也会把项目级记录错误描述为完整历史。因此 P10K 禁止回填、猜测或从 target 解析项目，改为从本包上线后在 P10C 成功新增、修改、删除的同一数据库事务内写一条最小不可变项目事件。

P10K 只交付“选定商务标项目最近 50 条已记录的成功成本变更”。严格财务用户可以区分事件是本人还是同空间其他财务成员完成，但看不到其他成员 ID、用户名或显示名。页面必须明确：旧历史不会补录，没有记录不等于没有发生；本能力不含金额、内容、失败尝试或完整审计。

拒绝本轮实现的其他候选：真实 MinerU/Docling 依赖外部运行时；Word `structure` 需要视觉产品决策；外部标讯需要合法来源授权；人力附件/真实核验与投标矩阵明细会扩大敏感数据边界；税务、审批、预算、回款、版本及失败尝试审计均需独立模型。它们不得搭车。

## 2. 最小不可变事件表

新增 `finance_project_cost_change_events`，字段只允许：

| 字段 | 规则 |
|---|---|
| `id` | 服务端生成 `fpce_` 不透明主键，客户端不可指定 |
| `workspace_id` | 当前已验证工作空间；外键级联删除，索引 |
| `project_id` | 当前已验证商务标项目；外键级联删除，索引 |
| `entry_id` | 当次 `fce_*` 条目 ID；故意不设外键，删除事件后仍保留 |
| `action` | 仅 `create`、`update`、`delete`，数据库 CHECK |
| `actor_user_id` | 当前已验证会话用户 ID，仅用于服务端映射本人/其他；不对外返回，索引 |
| `created_at` | 服务端 UTC 时间，索引 |

表不得保存成本类别、名称、金额、备注、报价、毛利、变更前后快照、请求体、失败原因、用户名或工作空间名称。建议复合索引覆盖 `(workspace_id, project_id, created_at)`；不得新增 update/delete API、客户端写入口或通用审计查询。

P10C 三种成功写操作必须在原事务内同时完成业务变更、P10A 脱敏审计和 P10K 事件：任一步抛错则全部回滚。删除事件必须先捕获项目、条目和 actor，再删除业务行；不得因条目删除丢失事件。P10J 原接口与历史语义保持不变。

## 3. 权限、项目隔离与固定 API

唯一新接口：`GET /api/finance/business-bids/{projectId}/cost-change-events`。复用 strict `require_finance`，无请求体、查询参数、分页游标或客户端 limit。

| 场景 | 结果 |
|---|---|
| required 且当前活动成员角色精确为 `finance`，项目为当前空间商务标 | `200` |
| required 未登录 | 全局中间件 `401 auth_required` |
| disabled、仅所有者、`bid_writer`、`hr`、`bidder` | `403 role_forbidden` |
| 非成员工作空间 | `403 workspace_forbidden` |
| 跨空间、技术标、不存在或伪造项目 | 统一 `404 project_not_found`，不反射路径 ID |

项目合法性必须在读取事件前以 SQL 最小投影校验当前 workspace、精确 project ID 和 `kind='business'`；禁止为此加载 editor-state、报价正文或成本条目。事件查询只能投影 `action`、`entry_id`、`actor_user_id`、`created_at` 四列，按 `created_at DESC, id DESC` 稳定排序，固定 SQL `LIMIT 50`。

成功响应固定 `Cache-Control: no-store`，顶层只含 `items`；每项只含：

- `action`：`create|update|delete`；
- `entryId`：原始合法 `fce_*` 不透明 ID；
- `actorScope`：仅 `self|other`，由服务端比较事件 actor 与当前会话 user；
- `occurredAt`：服务端事件时间。

不得返回 `projectId/projectName`、事件 ID、workspace、actor/user ID 或名称、内部审计 action/result、金额、名称、类别、备注、报价、毛利、前后值、失败原因或额外字段。非法历史行防御性排除，但合法性过滤必须在 SQL LIMIT 前完成，避免异常行占满最近 50 条。

每次成功读取写固定脱敏审计：action=`finance_project_cost_change_events_read`、result=`success`、target=`current_project_recent_50`；不得记录项目/条目/用户 ID、数量、时间范围或响应体。

## 4. 前端边界

只在既有 `/finance` 当前项目成本草案下增加“项目成本记录”折叠区，不新增路由或导航。选中项目后不自动读取；用户显式点击“查看项目记录”才调用唯一新 GET，手动刷新严格再调用一次。

面板只显示固定中文动作、条目编号、`本人/其他财务成员` 和时间，以及“仅记录 P10K 上线后的成功操作，不含金额、内容、成员身份、失败尝试或旧历史”的限制说明。空态和错误使用固定中文，不回显后端 detail、路径、项目 ID 或原始异常。

项目切换必须立即关闭并清空旧事件；迟到响应不得覆盖新项目。状态只在当前组件实例内存中，禁止 URL 参数、local/session storage、IndexedDB、Cookie、剪贴板、console、轮询、计时器、下载或外网。不得因 P10K 自动增加 P10B/P10C 首屏请求；不得请求 P10J `/finance/cost-change-events`、通用 projects/editor-state/settings/files、其他角色或未知 API。

## 5. 明确非目标

- 不回填 P10K 上线前事件，不从 P10J 审计或现存条目猜项目；
- 不返回其他成员身份，不做全工作空间、跨项目、管理员或失败尝试审计；
- 不保存或展示金额、名称、备注、类别、报价、毛利、前后快照；
- 不做筛选、搜索、分页、导出、审批、税务、预算、回款、版本、撤销或通知；
- 不修改认证/角色/CSRF，不改变 P10B 报价、P10C 成本语义、P10J 本人记录或其他角色页面；
- 不引入 Alembic、依赖、后台任务或生产部署能力。

## 6. 验收底线

后端至少覆盖三种写操作各写一条事件、删除后事件保留、业务/审计/事件同事务回滚、旧 P10J 行零回填、同项目其他 actor 映射 `other`、其他项目/空间隔离、技术标/伪造项目统一 404、固定 50 条和稳定倒序、非法 action/entry/actor 在 SQL 上限前排除、四列 SQL 投影、精确响应字段、`no-store`、读取审计脱敏，以及 required/disabled/所有者/各角色矩阵。前端至少覆盖零自动 P10K 请求、显式读取/刷新次数、项目切换清空与迟到隔离、动作/actor 映射、限制声明、空态、固定错误、P10J/其他业务/外网阻断和零浏览器存储；P10B/P10C 既有 E2E 必须回归。
