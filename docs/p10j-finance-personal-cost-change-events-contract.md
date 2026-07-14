<!--
模块：P10J 财务个人成本变更记录契约
用途：冻结严格财务角色读取本人在当前工作空间成功成本变更记录的数据、权限、审计与前端边界。
对接：docs/plans/2026-07-14-p10j-finance-personal-cost-change-events-plan.md；P10C finance_cost_* 审计事件；/api/finance/cost-change-events。
二次开发：本包不是完整财务审计；不得推断项目、金额、名称、备注、变更前后值、失败尝试或其他用户活动。
-->

# P10J 财务个人成本变更记录契约

> **状态**：已完成、独立验收并推送。计划=`701c946`，后端=`4e662d6`，前端=`fce6cb6`。
> **工作分支**：`collab/grok-code-codex-review`。

## 1. 审计结论与方案选择

P10C 已在成功新增、修改、删除成本条目时写入 `auth_audit_events`。可靠字段只有操作者用户 ID、工作空间 ID、固定 action、固定 `success`、成本条目不透明 ID 和服务端时间；事件不保存项目 ID、成本名称、金额、备注、变更前后值，也不记录校验失败或权限拒绝。

因此当前数据只能安全支撑“我的成本变更记录”，不能实现项目审计、全空间成员审计、变更详情、失败操作追踪或完整财务审计。P10J 只让严格 `finance` 查看本人在当前活动工作空间最近 50 条成功成本条目变更，并在页面显著说明这些限制。

拒绝本轮实现的其他候选：持久化融合历史需要新的版本模型和恢复冲突契约；真实解析器部署依赖外部二进制、路径和 Token 治理；人力附件/证件核验缺少合法数据源和隐私授权；投标人矩阵明细会扩大原文与项目数据出域。它们均不得搭车进入 P10J。

## 2. 权限与唯一数据源

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色严格为 `finance` | 允许读取本人、当前工作空间 P10J 记录 |
| required 未登录 | 全局中间件返回 `401 auth_required` |
| disabled、仅所有者、`bid_writer`、`hr`、`bidder` | `403 role_forbidden` |
| 非成员 `X-Workspace-Id` | 保持 `403 workspace_forbidden` |

唯一数据源为既有 `auth_audit_events`。查询必须同时满足：

- `workspace_id` 精确等于 `require_finance` 解析出的当前活动工作空间；
- `actor_user_id` 精确等于已验证会话操作者，不接受客户端用户 ID；
- `result='success'`；
- `action` 仅为 `finance_cost_create`、`finance_cost_update`、`finance_cost_delete`；
- `target` 为非空 `fce_` 成本条目不透明 ID。

查询只能投影 `action`、`target`、`created_at` 三列，按 `created_at DESC`、审计事件 `id DESC` 稳定排序并在 SQL 固定 `LIMIT 50`。不得整实体加载，不得读取用户名、其他 action、其他工作空间或其他财务成员记录。

## 3. 固定 API 投影

唯一接口：`GET /api/finance/cost-change-events`。无路径参数、查询参数、请求体、分页游标或客户端 limit；任意未知查询参数不得改变固定 50 条上限。成功响应 `200` 且 `Cache-Control: no-store`。

响应顶层只返回 `items`。每项仅含：

- `action`：固定为 `create`、`update`、`delete` 之一，由服务端映射内部 action；
- `entryId`：既有 `fce_*` 不透明成本条目 ID；
- `occurredAt`：既有服务端审计时间。

不得返回审计事件 ID、内部 action、result、actor/user、workspace、项目 ID/名称、成本类别/名称/金额/备注、创建/更新时间、报价、毛利、变更前后值、失败原因或任何额外字段。非法或不完整的历史行直接排除，不向客户端回显。

每次成功读取另写固定审计：action=`finance_cost_change_events_read`、result=`success`、target=`self_recent_50`；不得记录返回数量、条目 ID、时间范围、用户输入或响应体。该读取事件不属于响应的三类 action，不会自增长进入列表。

## 4. 前端边界

新增严格财务可见 `/finance/cost-changes` 页面和「我的成本记录」导航，继续复用 `RequireFinance`。`/finance` 的「财务报价」必须精确激活，不能在子页同时高亮。

页面只请求本包唯一 GET，展示服务端返回的动作、条目编号和发生时间；动作使用固定中文映射。页面必须显示：只记录当前账户在当前工作空间成功的成本条目新增、修改、删除；不是完整财务审计，不能还原项目、金额、内容、变更前后值或失败尝试。

首次挂载在 React Strict Mode 下严格只发 1 次 GET；手动刷新后累计严格 2 次。允许组件实例级在途 Promise 复用，禁止模块全局缓存、浏览器存储、URL 参数、计时器轮询或后台刷新。错误只显示固定中文，不回显后端 detail、路径或原始异常。

前端不得请求报价、成本草案、项目、编辑态、设置、文件、人力、投标人、知识库、标讯、未知 API 或外网；不得新增写接口、CSRF 操作、筛选、搜索、导出、删除、详情跳转或成员切换。

## 5. 明确非目标

- 不新增或修改数据表、迁移、索引、依赖、认证、角色或 CSRF；
- 不补录 P10J 上线前缺失的失败记录，不把“没有记录”解释为“没有发生”；
- 不做工作空间全员审计、管理员审计、按项目审计、审批流、财务报表或合规结论；
- 不连接已删除条目的业务正文，不反查项目或当前成本条目；
- 不修改 P10B 报价、P10C 成本草案、其他角色页面或标书制作者生产路径。

## 6. 验收底线

后端至少覆盖本人/同空间其他财务成员/同用户其他空间/其他 action/result/非法 target 的隔离，固定 50 条与稳定倒序，三字段 SQL 投影，响应字段白名单、空态、`no-store`、读取审计脱敏，以及 required/disabled/所有者/各角色/非成员矩阵。前端至少覆盖严格财务入口、首次/刷新请求次数、动作映射、限制声明、空态、固定错误、网络白名单、零浏览器存储，以及非财务/所有者/disabled 直达零 P10J 请求。

## 7. 交付与独立验收记录

- 后端按四文件白名单交付。Codex 两轮返修先后修复 SQL `LIKE` 下划线通配导致非法 target 占用上限，以及空后缀、首尾空白在 `LIMIT 50` 后才过滤的问题；最终字面 `fce_` 前缀、非空后缀和无首尾空白均在 SQL 上限前过滤，并保留 Python 防御校验。
- 后端独立验收：P10J 定向 **16 passed**；P10B/P10C/认证受影响回归 **63 passed**；串行全量 **422 passed**，仅 1 条既有 Starlette/httpx 弃用警告。
- 前端按九文件白名单交付。Codex 返修 E2E 的外网边界：Google 字体本地空响应收口，其他非本机请求先记录再中止，并用不真实出网的探针证明外链可观测。
- 前端独立验收：P10J E2E **12 passed**、P10I 定向复测 **10 passed**、lint 零错误零警告、build 通过；第二轮单 worker 串行全量 E2E **122 passed**。首轮唯一失败是既有 P10I 页面整页纯白启动抖动，P10J 12 项在该轮也全部通过，定向复测和完整重跑均已排除功能回归。
- 协作消息：后端最终回执=`msg_345732f9d78543ebb2ebb2ee5c77119f`；前端最终回执=`msg_1120ac97e76346e7bc2b2fb6266e50be`。Grok 未提交或推送，Git 始终由 Codex 负责。
