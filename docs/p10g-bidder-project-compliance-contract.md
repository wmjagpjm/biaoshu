# P10G 投标人项目级合规统计预览契约

## 1. 目标与边界

P10G 在不改变 P10E 工作空间级匿名合规汇总的前提下，为当前工作空间的严格 `bidder` 提供一条独立的「项目合规」只读路径。投标人先取得技术标项目的最小选择器，再按用户动作读取单个项目的响应矩阵**统计投影**，用于判断该项目的合规准备度。

本包只显示项目选择器的 `id`、`name`，以及单项目的 `dataState` 和五项统计：`totalItems`、`coveredItems`、`uncoveredItems`、`waivedItems`、`coverageBasisPoints`。统计口径与 P10E 完全一致：`covered`、`uncovered`、`waived` 逐项计数；未知状态按未覆盖计入；覆盖率为 `covered/(covered+uncovered)*10000` 的半入整数基点，豁免不入分母；无条目时为 `empty` 且覆盖率为 `null`。

明确不做项目详情、行业/状态/步骤/更新时间、矩阵原文、`sourceKey`、章节/大纲、备注、解析正文、文件、人员、团队推荐、资质、报价、成本、写入、CSRF 写接口、导出、评审结论、废标判定、版本/结果跟踪、跨空间搜索、批量汇总、浏览器持久化或外网请求。P10E 的匿名工作空间汇总语义和接口保持不变。

## 2. 权限与项目白名单

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色精确为 `bidder` | 可调用 `/api/bidder/project-compliance*`，仅当前工作空间 |
| required 未登录 | 认证中间件固定 `401 auth_required` |
| disabled、仅 `is_owner`、`bid_writer`、`finance`、`hr` | 统一 `403 role_forbidden` |
| 指定非成员 `X-Workspace-Id` | `403 workspace_forbidden` |
| 当前空间的 `kind=technical` 项目 | 可出现在选择器，并可读取单项目统计 |
| 不存在、跨空间或 `kind=business` 的项目 ID | 统一 `404 bidder_project_compliance_not_found` |

不得复用或开放既有 `/api/projects*`、`/editor-state`、`/hr/*`、`/finance/*`。`is_owner` 永远不能替代当前成员的 `bidder` 角色；成员角色本身精确为 `bidder` 时，所有者仍按该角色通过。

## 3. 数据来源、接口与响应投影

服务端只从当前工作空间 `kind=technical` 的 `Project` 白名单列读取选择器，并经既有编辑态服务取得该项目已收敛的 `responseMatrix` 后在服务端计数。不得向客户端传递 `ProjectOut`、完整编辑态或任何矩阵行。不得新建表、缓存、任务、外部依赖或写入业务数据。

| 方法 | 路径 | 成功 | 响应范围 |
|---|---|---|---|
| GET | `/api/bidder/project-compliance/projects` | 200 | `items[]`，每项仅 `id`、`name`；仅当前空间技术标 |
| GET | `/api/bidder/project-compliance/{projectId}` | 200 | `dataState`、`summary`（固定五项统计）；空矩阵也返回 `200 empty` |

`projects` 静态路径必须在 `{projectId}` 参数路径之前注册。两条接口均固定 `Cache-Control: no-store`，无请求体、筛选参数、分页、排序参数和写操作。响应不得附带项目 ID/名称以外的项目字段；详情响应也不得回显路径参数。

详情成功示例：

```json
{
  "dataState": "ready",
  "summary": {
    "totalItems": 12,
    "coveredItems": 9,
    "uncoveredItems": 2,
    "waivedItems": 1,
    "coverageBasisPoints": 8182
  }
}
```

## 4. 审计、前端与验收边界

读取项目选择器不写审计。单项目成功读取只记录 `bidder_project_compliance_read`，`result=success`，target 固定为 `project_compliance`；审计不得记录项目 ID/名称、统计数字、矩阵内容、人员、财务、请求路径或原始输入。

前端新增严格投标人可见的独立路由 `/bidder/project-compliance` 和「项目合规」入口，复用既有 `RequireBidder`。页面初始只请求项目选择器；未选择项目不得请求详情或 P10E 聚合。选择项目后才请求单项目统计，错误只显示固定中文脱敏文案。项目切换时必须先清空旧统计，并使延迟响应失效，不能短暂展示上一个项目的数据。所有结果仅存在 React 内存，禁止写入 `localStorage`、`sessionStorage` 或 URL 查询参数。

独立验收至少覆盖：required 未登录、disabled、严格角色/所有者、非成员工作空间、选择器仅 `id/name` 与技术标隔离、跨空间/商务标/伪造 ID 的统一 404、ready/empty/未知状态计数、最小响应字段、`no-store`、固定审计脱敏、前端初始网络白名单、按需详情请求、项目切换无旧数据、角色受限不请求、浏览器存储为零，以及 P10E 既有接口和匿名投影不回归。

**验收记录（2026-07-14）**：计划=`26b43ea`、后端=`c3cf8b4`、前端=`d5656cc` 已推送至协作分支。Codex 独立通过 P10G 后端定向 14 项、后端全量 378 项、前端 `lint` / `build`、P10G 定向 E2E 10 项、P10E E2E 8 项、认证 E2E 11 项及前端全量 E2E 83 项；仅保留既有 Starlette/httpx 弃用警告与 Vite 大包体积提示。一次并行启动相关 E2E 曾触发共享 SQLite 重置竞争，已停止并改为串行复跑，最终通过基线以本记录为准。
