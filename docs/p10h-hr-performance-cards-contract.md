<!--
模块：P10H 人员业绩素材卡契约
用途：冻结严格人力角色维护最小人员业绩卡的数据、权限、审计与前端边界。
对接：docs/plans/2026-07-14-p10h-hr-performance-cards-plan.md；后续 /api/hr/performance-cards*。
二次开发：业绩、附件、证件校验与项目团队组装必须分包演进，不得借本契约扩权。
-->

# P10H 人员业绩素材卡契约

## 1. 目标与边界

P10H 仅为当前工作空间的严格 `hr` 成员提供最小人员业绩素材卡：登记、摘要列表、按需查看详情、编辑和启停。它补足 P10D 中「资质类别为业绩」无法表达具体项目经历的缺口，但不改变 P10D 资质卡、P10F 团队推荐快照或任何标书制作者路径。

业绩卡仅保存协作显示名、人工录入的项目名称、项目角色、可选完成年份、业绩摘要、仅详情可见备注与启用状态。数据来源固定为 HR 手工录入；不得读取、关联或复制本系统技术标、商务标、标讯、编辑态、响应矩阵、文件、知识卡片或外部网站数据。

明确不做身份证件号码、手机号、住址、照片、人脸、简历全文、客户联系方式、合同金额、报价、附件、扫描件、外链、导出、批量导入、物理删除、项目关联、自动匹配、团队推荐写入、Word 写入、审批、共享链接、跨工作空间搜索、版本历史或证件校验。客户端不得传入这些额外字段，服务端必须拒绝。

## 2. 权限与隔离

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色严格为 `hr` | 允许访问当前工作空间的 P10H 接口和 `/hr/performance-cards` 页面 |
| required 未登录 | 全局认证中间件返回 `401 auth_required` |
| disabled、`owner` 的隐式绕过、`bid_writer`、`finance`、`bidder` | `403 role_forbidden` |
| 非成员 `X-Workspace-Id` | 保持 `403 workspace_forbidden` |
| 伪造、跨工作空间或不存在的业绩卡 ID | 统一 `404 hr_performance_not_found`，不区分真实存在性 |

复用现有 `require_hr`，只依据当前活动成员的精确角色；所有者身份不自动放行。前端 `canAccessHr` 与路由门禁仅作体验分流，不能替代服务端检查。

## 3. 数据最小化与响应投影

新增表 `hr_performance_cards`。ID 由服务端生成 `hpc_*`；服务端写入当前 `workspace_id`、创建人 ID 与 UTC 时间戳，客户端不能指定或修改这些字段。

| 字段 | 规则 | 摘要列表 | 详情与成功写入 |
|---|---|---|---|
| `personName` | 1–80 字符；仅协作显示名 | 返回 | 返回 |
| `projectName` | 1–120 字符；人工录入项目名称 | 返回 | 返回 |
| `projectRole` | 0–80 字符；可空 | 返回 | 返回 |
| `completedYear` | 可空整数，范围 1900–2100 | 返回 | 返回 |
| `performanceSummary` | 1–1000 字符；人工概述 | 不返回 | 返回 |
| `remark` | 0–500 字符 | 不返回 | 返回 |
| `isActive` | 仅 JSON `true` / `false` | 返回 | 返回 |

摘要与详情可返回 `id`、上述白名单字段及服务端 `createdAt`、`updatedAt`；不得返回 `workspaceId`、`createdBy`、请求体、审计信息或任意未列字段。`completedYear` 使用严格 JSON 整数，拒绝字符串、浮点和布尔强制转换；`isActive` 使用 `StrictBool`。所有成功响应均设置 `Cache-Control: no-store`，浏览器不得写入 `localStorage`、`sessionStorage` 或 URL 查询参数。

## 4. 接口与错误语义

| 方法 | 路径 | 成功 | 说明 |
|---|---|---|---|
| GET | `/api/hr/performance-cards` | 200 | 当前空间摘要列表，不含 `performanceSummary`、`remark` |
| GET | `/api/hr/performance-cards/{cardId}` | 200 | 单卡详情，按需返回摘要与备注 |
| POST | `/api/hr/performance-cards` | 201 | 创建；既有内存 CSRF 必须通过 |
| PATCH | `/api/hr/performance-cards/{cardId}` | 200 | 编辑或启停；既有内存 CSRF 必须通过 |

不存在 DELETE、上传、导出、项目选择器、批量接口或回退到既有 `/api/projects*` 的接口。写路由必须手工读取 JSON 对象，再以 `extra=forbid` 模型校验；非法 JSON、非对象、额外键、空补丁、长度/年份/严格布尔或严格整数不合法时，统一返回 `422 invalid_hr_performance` 与固定中文 detail，绝不回显原始输入、路径 ID 或数据库异常。

前端只能调用上述四条路径，卡片 ID 必须经 `encodeURIComponent`。初始页面只请求摘要；用户点选后才请求详情。创建、编辑或启停成功后，必须重新读取摘要与当前详情，禁止乐观伪造成功。

## 5. 审计、前端与验收边界

成功创建与更新分别记录 `hr_performance_create`、`hr_performance_update`，审计 target 仅为 `hpc_*` 卡片 ID；不得记录姓名、项目名、角色、年份、业绩摘要、备注、请求体、工作空间或操作者。

前端新增严格 HR 可见的独立路由 `/hr/performance-cards` 与「人员业绩」入口，复用既有 `RequireHr`；页面只在 React 内存保存列表与详情，所有错误为固定中文脱敏文案。非 HR、disabled 或仅所有者直达该路由时仅显示受限页且不得发出 P10H 接口请求。

独立验收至少覆盖：required 未登录、disabled、严格角色/所有者、非成员工作空间、跨空间/伪造 ID 的统一 404、摘要与详情字段隔离、严格年份和布尔、额外键/非对象/空补丁拒绝、`no-store`、CSRF、审计脱敏、创建/编辑/启停后的强制重读、初始网络白名单、详情按需读取、浏览器存储为零，以及 P10D/P10F 不回归。
