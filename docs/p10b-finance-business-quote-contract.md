# P10B：财务商务标报价只读契约

## 1. 目的与适用条件

P10B 为已启用本机身份模式的团队提供最小化财务查看能力：财务成员可以读取当前工作空间内商务标项目的已落库报价分项与合计。

该能力仅在 `AUTH_MODE=required` 生效，且当前活动工作空间成员角色必须严格为 `finance`。它不是通用项目浏览器，不授予编辑、导出、审批、成本或利润计算能力。

## 2. 授权与访问边界

| 主体或模式 | 财务报价接口与 `/finance` 页面 | 既有生产业务接口 |
| --- | --- | --- |
| `finance` | 允许只读当前工作空间商务标报价 | 继续拒绝 |
| `bid_writer`（含所有者） | 拒绝 | 保持 P10A 既有权限 |
| `hr`、`bidder` | 拒绝 | 继续拒绝 |
| 未登录 required | 全局会话中间件先返回 `401 auth_required` | 保持既有行为 |
| `AUTH_MODE=disabled` | 拒绝，不显示入口 | 保持个人版既有业务兼容 |

所有者不是独立的财务绕过条件；`isOwner=true` 不会放开本接口。带有非成员 `X-Workspace-Id` 的请求仍按 P10A 工作空间选择器返回 `403 workspace_forbidden`，不会泄露资源内容。

## 3. API 契约

### 3.1 列表

`GET /api/finance/business-bids`

- 成功：`200`，响应带 `Cache-Control: no-store`；
- 数据：仅当前工作空间 `kind=business` 项目，按既有项目更新时间排序；
- 每项严格为 `projectId`、`name`、`industry`、`status`、`updatedAt`、`quoteRowCount`、`quoteTotal`；
- `quoteTotal` 只累加有限的数值型 `amount`，`NaN`、无穷大、布尔、字符串和对象都不进入总额。

### 3.2 明细

`GET /api/finance/business-bids/{projectId}`

- 成功：`200`，响应带 `Cache-Control: no-store`；
- 在列表字段上仅追加 `quoteRows` 与 `quoteNotes`；
- 单行仅可含 `id`、`name`、`unit`、`quantity`、`unitPrice`、`amount`、`remark`；无效金额为 `null`；
- 不存在、其他工作空间或非商务标项目一律为 `404 project_not_found`，不区分存在性。

该资源没有 `POST`、`PUT`、`PATCH` 或 `DELETE` 方法；框架应返回 `405`，且不得产生写入。

## 4. 数据最小化与禁止返回项

服务层从现有项目和编辑器状态读取后逐字段重建响应，绝不透传 `business_json` 或完整 editor-state。响应、前端状态和错误页面均不得出现：

- `businessQualify`、`businessToc`、`businessCommit`、资格、目录、承诺；
- 技术标大纲、章节、解析文本、响应矩阵；
- 文件、任务、资源、知识库、设置、模型配置或 API Key；
- 成本、利润、税率、毛利率等没有独立数据契约的推算值；
- 会话 Cookie、CSRF、口令、摘要或工作空间外项目数据。

P10B 的报价列表/明细子区域只请求上述两个专用 `GET` 接口。P10C 已在同一严格财务页面追加独立成本草案端点，但该扩展受 `docs/p10c-finance-cost-draft-contract.md` 约束，不能改变本节报价投影或将其作为通用业务接口。页面不以 `/projects`、`/editor-state`、`/settings` 或 `/files` 作为降级路径，也不把业务数据或认证信息写入 localStorage、sessionStorage 或 URL。

## 5. 前端体验契约

- `/finance` 只对严格财务角色显示“财务报价”导航；其他角色与 disabled 模式直达该路径均显示受限说明；
- 页面展示项目摘要、分项编号/名称/单位/数量/单价/金额/备注及报价备注；
- 金额仅格式化展示，`null` 或非有限值显示“—”，不在浏览器计算成本或利润；
- 项目状态以中文显示；加载、空列表、空分项、请求失败和 404 都有中文可读状态；
- 导航隐藏只是体验分流，服务端 `require_finance` 才是最终授权边界。

## 6. 验收证据（2026-07-14）

- 后端 `tests/test_finance_role.py`：9 passed，覆盖字段白名单、金额归一、跨空间/技术标 404、角色拒绝、只读方法和通用业务继续拒绝；
- 后端全量按串行分组：299 passed，1 条既有 Starlette/httpx 弃用警告；
- `npm run lint`、`npm run build`：通过（构建仅保留既有大 chunk 提示）；
- `npm run test:e2e:finance-role`：7 passed，覆盖财务入口、两专用请求、分项编号、中文状态、错误/空态、角色门禁和敏感存储；
- `npm run test:e2e:auth-rbac`：11 passed；`test:e2e:semantic-index`：9 passed；`test:e2e:cards`：1 passed；
- `git diff --check`：通过。

## 7. 后续边界

P10C 已独立冻结并完成成本草案与毛利快照；税率、审批、导出、预算、回款、版本和审计查看仍须另立数据来源、精度、写入人、工作空间隔离与权限矩阵。人力团队推荐和投标人匿名预览/版本/合规数据域同样需要各自的契约；不得因为已经存在角色名称或前端路径而推断授权。
