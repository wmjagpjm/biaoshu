<!--
模块：P10I 人员资质到期提示契约
用途：冻结严格人力角色读取当前空间资质有效期风险摘要的数据、权限、审计与前端边界。
对接：docs/plans/2026-07-14-p10i-hr-credential-expiry-plan.md；P10D hr_credential_cards；后续 /api/hr/credential-expiry。
二次开发：本包只做服务端日期提示，不得扩为证件真伪校验、附件识别、外网核验或自动审批。
-->

# P10I 人员资质到期提示契约

## 1. 审计结论、目标与边界

P10D 已保存可空 `validUntil`，但其冻结契约明确不做过期提醒或自动判断。真正的证件真伪校验需要证件号码、扫描件、权威数据源与更严格的数据保护，目前既无合法数据源也无授权，不能实现或伪造。

P10I 因此只新增“人员资质到期提示”：严格 `hr` 可读取当前工作空间启用中 P10D 卡片的服务端日期分类、固定计数和最小关注列表。它不修改 P10D 卡片、不新增数据表、不读取备注、不访问外网，也不向标书制作者或其他角色投影。

页面和 API 必须明确声明：结果仅依据人工录入的有效期日期生成，不验证证书真实性、持证状态、适用范围或监管结论。

## 2. 权限、来源与时间语义

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色严格为 `hr` | 允许读取当前工作空间 P10I 摘要与页面 |
| required 未登录 | 全局中间件返回 `401 auth_required` |
| disabled、仅所有者、`bid_writer`、`finance`、`bidder` | `403 role_forbidden` |
| 非成员 `X-Workspace-Id` | 保持 `403 workspace_forbidden` |

唯一数据源为当前工作空间 `hr_credential_cards`。只读取 `is_active=true` 卡片进行分类；停用卡不出现在关注列表，仅计入 `inactiveExcludedCount`。不得读取 P10H、P10F、项目、文件、编辑态、财务、投标人、知识库、标讯或外部网站。

服务端以 UTC 自然日生成 `asOfDate`，固定提示窗口为 **90 天**，客户端不能传日期或窗口参数：

- `expired`：`validUntil < asOfDate`；`daysRemaining` 为负数；
- `expiring_soon`：`asOfDate <= validUntil <= asOfDate + 90 天`；当天到期为 `0`；
- `valid`：`validUntil > asOfDate + 90 天`；只计数，不进入关注列表；
- `missing_expiry`：`validUntil=null`；进入关注列表，`daysRemaining=null`。

## 3. 固定响应投影

唯一接口：`GET /api/hr/credential-expiry`，成功 `200` 且 `Cache-Control: no-store`。

顶层只返回：

- `asOfDate`：服务端 UTC 日期；
- `windowDays`：固定整数 `90`；
- `activeTotalCount`、`expiredCount`、`expiringSoonCount`、`validCount`、`missingExpiryCount`、`inactiveExcludedCount`：非负整数；
- `attentionItems`：只含 `expired`、`expiring_soon`、`missing_expiry`。

每个关注项只返回：`cardId`、`personName`、`category`、`credentialName`、`level`、`validUntil`、`state`、`daysRemaining`。不得返回 `remark`、工作空间、创建人、时间戳、审计、证件号码、附件、路径、外链或任意未列字段。

排序固定为：`expired` 在前并按最早有效期升序，随后 `expiring_soon` 按最近有效期升序，最后 `missing_expiry`；同组以 `cardId` 稳定排序。空数据仍返回完整计数与空数组，不返回 404。

## 4. 审计、错误与前端

每次成功读取记录 `hr_credential_expiry_read`，target 固定为 `credential_expiry`，result 固定为 `success`。既有审计基础设施可从已验证会话记录操作者与工作空间标识，但 action、target、result 与扩展字段不得记录卡片 ID、姓名、资质名、日期、状态、计数或响应体。

除认证/RBAC/工作空间固定错误外，不新增客户端可控参数或业务错误。服务端异常不得回显数据库内容、路径、人员信息或原始异常。不存在 POST、PATCH、DELETE、上传、导出、批量、刷新写入或“自动修复有效期”接口。

前端新增严格 HR 可见的 `/hr/credential-expiry` 与「到期提示」入口。页面初始只能请求本包唯一 GET；不得请求 P10D 列表后在浏览器自行判定，不得读取 P10D 详情或备注。数据只保存在 React 内存，错误使用固定中文，禁止 `localStorage`、`sessionStorage`、URL 参数、外网和乐观伪造。

## 5. 验收底线

后端至少覆盖精确角色/所有者/disabled/非成员矩阵、UTC 日期边界、90 天两端、当天到期、负数剩余天数、无有效期、停用排除、字段白名单、空态、`no-store` 与固定审计脱敏。前端至少覆盖严格 HR 入口、计数与关注列表、风险排序、免责声明、空态、固定错误脱敏、网络白名单、浏览器存储为零、非 HR/所有者/disabled 直达零 P10I API，以及 P10D/P10F/P10H 不回归。
