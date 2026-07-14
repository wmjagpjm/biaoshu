# P10C：财务成本草案与毛利快照契约

## 1. 目的与边界

P10C 在 P10B 的商务标报价只读投影上，提供严格财务成员维护的项目成本草案与基于当前报价的毛利快照。它用于内部成本讨论，不是会计核算、审批结论、最终利润、含税口径或财务报表。

本包只包含：人工成本条目、人民币分精度汇总、毛利快照、最小审计和受控前端录入。以下均不在范围内：税率、发票、币种、预算、回款、审批、导出、锁账、版本历史、会计凭证、人力数据或投标人预览。

## 2. 权限与隔离

| 场景 | 行为 |
|---|---|
| `AUTH_MODE=required` 且当前成员严格为 `finance` | 可读取、创建、修改、删除当前工作空间商务标成本草案 |
| `owner`、`bid_writer`、`hr`、`bidder` | 已登录时固定 `403 role_forbidden`；所有者不构成绕过 |
| required 未登录 | 全局会话中间件固定 `401 auth_required` |
| `AUTH_MODE=disabled` | 固定 `403 role_forbidden`，不开放财务能力 |
| 技术标、跨工作空间、不存在项目或非本项目条目 | 统一 `404 project_not_found`，不泄露存在性 |

全部响应使用 `Cache-Control: no-store`。浏览器不把成本、毛利、备注、Cookie 或 CSRF 写入 `localStorage`、`sessionStorage` 或 URL。

## 3. 数据与精度

表：`finance_cost_entries`。

| 字段 | 约束 |
|---|---|
| `id` | 服务端随机不透明 ID，客户端不可指定 |
| `workspace_id`、`project_id` | 由当前已校验商务标写入，带外键 |
| `category` | 仅 `labor`、`material`、`service`、`other` |
| `name` | 1–120 字符 |
| `amount_fen` | 人民币正整数分，`1..999999999999`；ORM 与服务层双重校验 |
| `remark` | 最多 500 字符，可为空 |
| `created_by_user_id`、时间戳 | 只从已验证服务端主体和 UTC 时钟取得 |

报价总额仅从 P10B 的有限数值 `quoteTotal` 以 `Decimal(str(value))` 量化为分；不解析字符串、对象、布尔或非有限数值。汇总公式为：`grossProfitFen = quoteTotalFen - costTotalFen`。报价合计小于等于零时，`grossMarginBasisPoints=null`；其他情况按整数/Decimal 最近基点计算。响应不输出浮点成本或毛利金额。

## 4. API 白名单

| 方法 | 路径 | 成功响应 |
|---|---|---|
| GET | `/api/finance/business-bids/{projectId}/cost-draft` | `200`，项目名、报价/成本/毛利分、毛利基点、条目白名单 |
| POST | `/api/finance/business-bids/{projectId}/cost-entries` | `201`，新条目 |
| PATCH | `/api/finance/business-bids/{projectId}/cost-entries/{entryId}` | `200`，更新条目 |
| DELETE | `/api/finance/business-bids/{projectId}/cost-entries/{entryId}` | `204` |

条目响应仅含 `id`、`category`、`name`、`amountFen`、`remark`、`createdAt`、`updatedAt`。草案响应仅含 `projectId`、`projectName`、`quoteTotalFen`、`costTotalFen`、`grossProfitFen`、`grossMarginBasisPoints`、`costEntries`。禁止返回报价行、`business_json`、创建人、工作空间、审计细节、设置和认证字段。

所有写入使用既有 P10A CSRF 校验；服务端请求模型以 `StrictInt` 接受 `amountFen`，拒绝布尔、字符串和包括 `1.0` 在内的浮点输入。

## 5. 审计与前端约束

每次成功创建、修改、删除分别写入 `finance_cost_create`、`finance_cost_update`、`finance_cost_delete`。审计 `target` 只保存条目 ID，绝不包含金额、名称、备注或原始请求。

`/finance` 仍受严格财务门禁。页面只可请求 P10B 两个报价 GET 与本契约四个成本端点；元金额由纯文本按字符串拆分为整数分，禁止浮点乘法。成功写入后必须重新读取草案，不做乐观累计。项目切换时，只有报价明细 ID 与当前选中项目一致才挂载成本面板，避免旧项目明细与新项目成本错配。

## 6. 验收证据（2026-07-14）

- 后端串行全量：**314 passed**，仅 1 条既有 Starlette/httpx 弃用警告；其中 P10C 定向、P10B 财务和 P10A 鉴权分组 **63 passed**。
- 前端：`npm run lint`、`npm run build` 通过；构建仅保留既有大 chunk 提示。
- E2E：`test:e2e:finance-cost-draft` **4 passed**，覆盖元转分、CRUD 后刷新、零报价、负毛利、错误脱敏、网络白名单、无敏感存储和项目切换一致性；`finance-role` **7 passed**、`auth-rbac` **11 passed**、`semantic-index` **9 passed**、`cards` **1 passed**。
- `git diff --check` 通过；实现提交为后端 `6f30084`、前端 `737c7db`。

## 7. 后续边界

P10D 如需税务、审批、导出、预算、回款、成本版本或审计查看，必须另立数据契约、权限矩阵、数据来源和验收计划；不得借本接口或前端页面直接扩展。
