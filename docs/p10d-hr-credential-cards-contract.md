# P10D 人员资质素材卡契约

## 1. 目标与边界

P10D 只为当前工作空间的严格 `hr` 成员提供最小人员资质素材卡：登记、摘要列表、按需查看详情、编辑和启停。它用于沉淀协作显示名与资质描述，不是人事档案、证件库、附件库、团队推荐或项目配置功能。

明确不做身份证件号码、手机号、住址、银行信息、照片、人脸、简历全文、证书扫描件、附件、外链、导出、批量导入、物理删除、项目关联、自动推荐、审批、共享链接和跨工作空间搜索。客户端不得传入这些额外字段；服务端以 `extra=forbid` 与响应白名单拒绝越界字段和结构。

## 2. 权限与隔离

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色严格为 `hr` | 允许访问当前工作空间的 P10D 接口和 `/hr` 页面 |
| required 未登录 | 全局认证中间件返回 `401 auth_required` |
| disabled、`owner` 的隐式绕过、`bid_writer`、`finance`、`bidder` 或非成员空间 | `403 role_forbidden`；非成员 `X-Workspace-Id` 保持 `403 workspace_forbidden` |
| 伪造、跨空间或不存在的卡片 ID | `404 hr_credential_not_found`，不区分真实存在性 |

`require_hr` 与 P10B `require_finance` 对称，只依据当前活动成员的精确角色，不因 `is_owner` 自动放行。前端的 `canAccessHr` 与 `/hr` 门禁仅作体验分流，不能替代服务端检查。

## 3. 数据最小化

表：`hr_credential_cards`。ID 由服务端生成 `hcc_*`，包含当前 `workspace_id`、创建人 ID 和服务端 UTC 时间戳；客户端不能指定这些字段。

| 字段 | 规则 |
|---|---|
| `personName` | 1–80 字符；仅协作显示名 |
| `category` | `professional`、`safety`、`performance`、`other` |
| `credentialName` | 1–120 字符；仅证书或资质名称 |
| `level` | 0–80 字符，可空 |
| `validUntil` | 可空 ISO 日期；不做过期提醒或自动判断 |
| `remark` | 最多 500 字符；仅详情与写入响应可见 |
| `isActive` | 仅 JSON `true` / `false`；服务端 `StrictBool` 拒绝字符串、数字和布尔强制转换 |

列表摘要不含 `remark`、创建人、工作空间或任何敏感扩展字段；详情和成功写入响应只增加 `remark`。所有成功读取响应设置 `Cache-Control: no-store`，写入响应同样不缓存。浏览器不写 `localStorage` 或 `sessionStorage`。

## 4. 接口

| 方法 | 路径 | 成功 | 说明 |
|---|---|---|---|
| GET | `/api/hr/credential-cards` | 200 | 当前空间摘要列表；不含 `remark` |
| GET | `/api/hr/credential-cards/{cardId}` | 200 | 单卡详情；含 `remark` |
| POST | `/api/hr/credential-cards` | 201 | 创建；既有内存 CSRF 必须通过 |
| PATCH | `/api/hr/credential-cards/{cardId}` | 200 | 修改或启停；既有内存 CSRF 必须通过 |

不存在 DELETE、上传、导出或通用项目回退接口。写入请求由路由手工读取 JSON；无效 JSON、额外键、长度/日期/枚举/严格布尔不合法时统一 `422 invalid_hr_credential`，detail 固定中文且不回显原始输入。前端只调用上述路径，ID 使用 `encodeURIComponent`；新建、编辑、启停成功后均重新读取列表和当前详情，禁止乐观伪造成功。

## 5. 审计与验收

成功创建与更新分别记录 `hr_credential_create`、`hr_credential_update`，审计 target 仅为卡片 ID；不得写入姓名、资质名、备注、请求体或敏感值。

P10D 已完成独立验收并推送：后端 `d8f7cbd`，前端 `71f065a`。验收基线为后端串行全量 `326 passed`（仅既有 Starlette/httpx 弃用警告）、P10D HR E2E `9 passed`、前端全量 E2E `55 passed`、`npm run lint` / `npm run build` 通过（仅既有大包体积提示）和 `git diff --check` 通过。

后续若需要人员业绩、团队推荐、证件校验、附件、联系方式、项目组装、审批、共享或删除历史，必须新建数据保护、授权、审计和保留期限的独立计划；不得扩大本契约。
