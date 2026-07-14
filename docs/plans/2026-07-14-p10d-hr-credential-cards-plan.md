# P10D：人力人员资质素材卡实施计划

## 1. 决策与目标

P10D 启用 P10A 已有但默认拒绝的严格 `hr` 角色，提供当前工作空间内的最小人员资质素材卡登记与查看能力。它解决团队在投标前需要受控沉淀人员资质、证书和可用状态的缺口，但不把个人档案、简历系统、团队推荐或文件库提前做进来。

本包完成后，严格 `hr` 可在独立 `/hr` 页面创建、查看、修改、停用自己的工作空间人员资质卡；其他角色仍不得访问。卡片仅供人力登记，尚不向标书制作者或财务侧投放，不自动推荐到项目。

## 2. 范围与非目标

### 2.1 本包范围

- 仅 `AUTH_MODE=required`、当前工作空间成员角色严格为 `hr`；
- 新表 `hr_credential_cards`，工作空间隔离；
- 受控 CRUD（列表、单卡、创建、更新、停用/启用），最小审计；
- `/hr` 独立前端门禁、中文列表/表单、空态和固定错误；
- 服务端和浏览器均不回退到通用项目、设置、文件或外网接口。

### 2.2 明确不做

- 不收集身份证件号码、手机号、住址、银行信息、照片、人脸、简历全文、证书扫描件、附件、外链或第三方身份核验；
- 不做项目团队推荐、人员匹配、评分、AI 推理、导出、审批、共享链接、批量导入、删除历史或跨工作空间搜索；
- 不让 `owner`、`bid_writer`、`finance`、`bidder` 越过 strict `hr` 读取；
- 不修改 P10A 会话/CSRF/中间件、P10B/P10C 财务端点或既有业务授权。

## 3. 权限、数据与接口契约

### 3.1 权限矩阵

| 场景 | HR 素材卡 |
|---|---|
| required + strict `hr` | 允许当前空间受控读写 |
| `owner` / `bid_writer` / `finance` / `bidder` | `403 role_forbidden`，所有者不绕过 |
| required 未登录 | 全局中间件 `401 auth_required` |
| disabled | `403 role_forbidden` |
| 跨空间、不存在卡 | `404 hr_credential_not_found` |

所有成功读取响应为 `Cache-Control: no-store`；所有变更继续使用 P10A CSRF。

### 3.2 最小字段

表名：`hr_credential_cards`。

| 字段 | 规则 |
|---|---|
| `id`、`workspace_id` | 服务端 ID；当前空间写入；客户端不得指定 |
| `person_name` | 1–80 字符，只作工作协作显示名 |
| `category` | `professional`、`safety`、`performance`、`other` |
| `credential_name` | 1–120 字符，不存证件号码 |
| `level` | 0–80 字符，可空 |
| `valid_until` | 可空 ISO 日期；不做提醒或自动判定 |
| `remark` | 最多 500 字符；不得放联系方式/证件号 |
| `is_active` | 服务端布尔启停；不做物理删除 |
| `created_by_user_id`、时间戳 | 仅已验证主体与服务端 UTC |

服务端以字段白名单输出；列表不返回 `remark`，单卡/写入响应可返回 `remark`。服务端校验枚举、长度、日期和禁用敏感字段名/结构；客户端输入仅作 UX 预检，服务端为权威。

### 3.3 专用接口

| 方法 | 路径 | 成功 |
|---|---|---|
| GET | `/api/hr/credential-cards` | `200` 当前空间摘要列表 |
| GET | `/api/hr/credential-cards/{cardId}` | `200` 单卡详情 |
| POST | `/api/hr/credential-cards` | `201` 新卡 |
| PATCH | `/api/hr/credential-cards/{cardId}` | `200` 更新或启停 |

不提供删除、项目关联、上传、导出或跨空间查询。创建/更新的审计 action 固定为 `hr_credential_create`、`hr_credential_update`，target 只含卡片 ID，不记录姓名、证书名、备注或原始请求。

## 4. 实施任务

### 任务 1：后端严格 HR 素材卡域（Grok）

初版允许文件：

- `backend/app/models/entities.py`、`backend/app/models/__init__.py`；
- `backend/app/api/schemas.py`、`backend/app/api/hr.py`（新建）；
- `backend/app/services/hr_credential_service.py`（新建）；
- `backend/app/main.py`（仅实体/路由注册）；
- `backend/tests/test_hr_credential_cards.py`（新建）。

若需扩展 `deps.py`，必须先由 Codex 审查 `require_hr` 的最小设计，禁止自行放宽任何现有角色。

后端验收：字段白名单、严格 HR、disabled/未登录/非 HR、跨空间 404、CSRF、禁用敏感字段、审计脱敏、无物理删除和 P10A/P10B/P10C 回归。

### 任务 2：前端 HR 素材卡（Grok）

在任务 1 验收后另行冻结白名单。预期只涉及认证能力派生、路由/导航最小门禁、`features/hr/**`、`frontend/e2e/hr-credential-cards.spec.ts` 与必要脚本。

体验约束：严格 HR 才显示入口；列表只取摘要，点选后取详情；提交后重读服务端；不把个人数据写浏览器存储；错误不回显后端 detail；网络只能访问 `/api/auth/*`、`/api/health`、`/api/hr/credential-cards*`。

### 任务 3：Codex 验收与文档

独立审查白名单、个人数据最小化、角色隔离、审计、no-store、跨空间和敏感存储；运行后端串行全量、前端 lint/build、相关 E2E；新增 P10D 契约，更新联调清单、路线图、交接和注释齐备表；以中文提交并推送协作分支。

## 5. 风险与停止条件

人员资质属于敏感协作资料。本包只保存最小显示名与资质描述；任何需要证件号、联系方式、附件、项目推荐、审批或跨团队共享的需求都必须停止并新立数据保护与授权计划。若无法在不修改 P10A 身份/会话或不引入通用项目访问的前提下完成，Grok 必须通过消息箱报告而非扩大范围。
