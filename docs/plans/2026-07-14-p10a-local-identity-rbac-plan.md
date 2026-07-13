# P10A 本机账号、工作空间成员与 RBAC 实施计划

> **状态：** 由 Codex 于 2026-07-14 按既有阶段 5 路线冻结；先完成可审计的本机身份与工作空间成员底座，再分别开放财务、人力和投标人业务面。
> **给 Grok：** 每个任务只能修改其白名单内的文件；先写失败测试，完成后只发送 `review_request`，不得提交或推送。

**目标：** 在保持个人版可平滑运行的前提下，提供本机用户名/口令登录、服务端会话、工作空间成员关系、四种固定角色与统一的服务端工作空间校验，彻底消除受信任 `X-Workspace-Id` 请求头造成的越权入口。

**架构：** 采用本机 SQLite 身份表与不透明 HttpOnly 会话 Cookie，不使用 JWT、外部身份平台、邮箱、短信或第三方网络服务。`auth_mode=disabled` 保持已有个人版和测试的兼容行为；部署者显式切换为 `required` 并通过本地交互式管理员引导后，所有业务 API 必须从会话解析用户，再校验其工作空间成员关系。请求头仅作为已验证成员的工作空间选择器，不能授予访问权。

**技术栈：** FastAPI、SQLAlchemy/SQLite、Python 标准库 `hashlib.scrypt`/`secrets`、React、TypeScript、React Router、Playwright、pytest。

---

## 1. 只读审计与已冻结决策

### 1.1 现有风险

1. `backend/app/api/deps.py:get_workspace_id` 当前直接接受任意 `X-Workspace-Id`；它不验证身份或成员关系。
2. `Workspace.owner_user_id` 只是历史字符串，当前没有用户、成员、会话或审计数据域。
3. 几乎所有业务路由依赖 `get_workspace_id`，因此修复必须在公共依赖层收口，不能靠前端隐藏菜单。
4. `GET/PUT /api/settings` 会处理工作空间模型配置；多用户模式下只能由工作空间所有者访问，不能向普通成员回显 API Key。
5. 现有前端统一从 `shared/lib/api.ts` 发请求，路由已预留“全局 Provider（主题/鉴权）后续在此包裹”的接缝。

### 1.2 方案比较与选型

| 方案 | 结论 |
|---|---|
| 外部 OAuth/JWT/邮箱注册 | 不采用：需要外部网络、密钥、回调地址和账号恢复流程，超出本机自托管范围。 |
| 本地用户名口令 + 不透明 Cookie 会话 | **采用**：无新增依赖，服务端可立即撤销、过期和审计会话；前端不保存 Token。 |
| 继续信任 `X-Workspace-Id`，仅前端隐藏入口 | 不采用：请求可伪造，不能构成 RBAC。 |
| 强制升级所有现有个人版实例 | 不采用：先以 `auth_mode=disabled|required` 分阶段切换，避免未初始化账户锁死已有本机数据。 |

### 1.3 安全与数据不变量

- 角色固定为 `bid_writer`、`finance`、`hr`、`bidder`；工作空间所有者是成员标记，不新增第五个业务角色。
- 口令仅存 `scrypt` 派生值和随机盐；会话和 CSRF 值只向浏览器发送原始值，数据库仅存 SHA-256 摘要；日志、API、审计和错误消息均不得回显口令、Cookie、摘要或 Token。
- 启用 `required` 后，除健康检查、登录、退出和公开的初始化状态外，所有 `/api` 请求都要有有效会话；无会话为 401，非成员或无权限为固定 403，跨工作空间资源继续返回 404。
- Cookie 使用 `HttpOnly`、`SameSite=Strict`、`Path=/api`；生产部署可通过服务端布尔配置启用 `Secure`。变更请求携带仅存内存的 CSRF 头，服务端比对摘要。
- 不提供公开注册、密码找回、邮件、手机号、第三方登录、跨工作空间数据复制、角色自助提升或历史业务数据批量重写。
- `auth_mode=disabled` 仅是迁移兼容模式；进入 `required` 后不得再接受未校验工作空间头。管理员引导脚本必须交互读取口令，不接受口令命令行参数，也不得把口令写入 `.env`、文档或测试输出。

## 2. P10A 交付边界

### 已实现于本包的能力

1. 本机管理员引导、登录/登出/当前身份、会话失效和活动工作空间切换。
2. 用户、工作空间成员、会话和最小审计事件数据域；默认工作空间可被已有个人版安全接管。
3. 成员角色的创建、启停、角色变更与移除，仅工作空间所有者可执行；最后一个所有者不得被降级或移除。
4. 统一认证工作空间依赖，阻断伪造 `X-Workspace-Id`；设置 API 仅所有者可访问。
5. 登录页、会话恢复、退出和当前角色展示；未登录状态不渲染业务壳。

### 延后到独立包的能力

- 财务的报价、成本、利润和报表业务域；人力的团队推荐；投标人的脱敏预览、版本和合规看板。这些现有业务面尚无完整独立数据契约，不能只按路径猜测授权。
- 现有全部业务路由的细粒度角色矩阵；P10A 中除所有者/标书制作者外，财务、人力、投标人默认只可认证和查看自身成员信息，业务端点默认拒绝，避免越权开放。
- 多因素认证、密码找回、邮件通知、组织邀请链接、SAML/OIDC、审计导出、跨工作空间共享和移动端登录。

## 3. HTTP 与角色契约

| 方法 | 路径 | 访问规则 |
|---|---|---|
| GET | `/api/auth/bootstrap-status` | 公开；仅返回是否已完成管理员引导，不含用户信息。 |
| POST | `/api/auth/login` | 公开；用户名/口令校验成功后设置 Cookie，并只返回脱敏用户、成员和 CSRF 值。失败固定 401。 |
| POST | `/api/auth/logout` | 当前会话；撤销会话并清除 Cookie。 |
| GET | `/api/auth/me` | 当前会话；返回脱敏身份、可访问工作空间、当前角色和 CSRF 值。 |
| PUT | `/api/auth/active-workspace` | 当前会话且目标为成员；只切换会话中的工作空间，不创建工作空间。 |
| GET/POST/PATCH/DELETE | `/api/auth/members*` | 仅当前工作空间所有者；不能删除或降级最后一个所有者。 |

| 角色 | P10A 已开放 | P10A 明确禁止 |
|---|---|---|
| 所有者 + `bid_writer` | 所有既有业务功能、成员管理、设置、会话管理。 | 角色自助提升、删除最后所有者。 |
| `finance` | 登录、退出、身份/成员自身信息。 | 任何既有业务数据、设置、成员管理和 API Key。 |
| `hr` | 登录、退出、身份/成员自身信息。 | 任何既有业务数据、设置、成员管理和 API Key。 |
| `bidder` | 登录、退出、身份/成员自身信息。 | 任何既有业务数据、设置、成员管理和 API Key。 |

所有业务路由在 `required` 模式先经过统一会话/成员校验；非 `bid_writer` 默认收到固定 `role_forbidden`，直到后续独立权限矩阵包逐项开放。前端隐藏导航只是体验优化，绝不替代服务端校验。

## 4. 任务 1：后端身份数据域、会话与安全工作空间依赖

**允许文件：**

- 修改：`backend/app/core/config.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/api/deps.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/main.py`
- 新建：`backend/app/services/auth_service.py`
- 新建：`backend/app/api/auth.py`
- 新建：`backend/app/api/auth_middleware.py`
- 新建：`backend/scripts/bootstrap_local_admin.py`
- 新建：`backend/tests/test_auth_rbac.py`

**禁止文件：** 前端、现有业务 service/router、requirements、`.env`、数据库文件、用户上传文件、任意网络/邮件/外部身份 SDK。

### 步骤 1：先写失败测试

在 `test_auth_rbac.py` 只使用独立 SQLite 与 `auth_mode=required` 夹具，覆盖：

1. 未初始化时业务 API 返回固定 503；伪造 `X-Workspace-Id` 不可绕过。
2. 管理员初始化后，错误用户名和错误口令得到相同 401；响应、日志断言和审计记录不含口令、Cookie 或摘要。
3. 正确登录得到 HttpOnly/SameSite Cookie 和 CSRF 值；`/auth/me` 仅返回脱敏身份。
4. 无 Cookie 的业务请求为 401；有效 Cookie 可访问默认工作空间；非成员工作空间头为 403；资源跨空间仍为 404。
5. 退出、过期或已撤销会话后为 401；变更请求缺失/错误 CSRF 为 403。
6. `auth_mode=disabled` 维持既有测试所需的默认工作空间和 `X-Workspace-Id` 隔离行为。

先运行：

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_auth_rbac.py
```

预期：因模块和路由不存在而失败。

### 步骤 2：最小实现

1. 增加 `auth_mode`、会话时长、Cookie 安全标记和引导状态所需的服务端配置；默认 `disabled`，生产切换仅接受 `required`。
2. 增加 `LocalUserRow`、`WorkspaceMemberRow`、`AuthSessionRow`、`AuthAuditEventRow`。用户名使用规范化唯一键；成员 `(workspace_id,user_id)` 唯一；会话只存 token/CSRF 摘要、过期和撤销时间；审计只存 actor、workspace、固定动作/结果/目标和时间。
3. 通过 `create_all` 与幂等索引补齐表；不删除旧表，不批量修改历史 `workspace.owner_user_id`。交互式引导脚本以 `getpass` 创建首个用户、默认空间所有者成员并在必要时安全更新默认空间 owner。
4. `auth_service` 使用 `hashlib.scrypt`、随机盐和 `secrets.token_urlsafe`；实现登录、会话读取/撤销、CSRF 校验、会话过期、脱敏序列化与固定中文错误码。
5. `auth_middleware.py` 在 `required` 模式统一拦截 `/api`：仅 `/api/health` 与明确列出的 `/api/auth/*` 公共端点可匿名；其余 API 必须有有效会话，并把脱敏主体写入 `request.state`。`deps.py` 再从该主体解析成员；`X-Workspace-Id` 只在成员列表内选择空间。`disabled` 模式保持现有兼容语义。为后续路由提供 `require_owner`、`require_bid_writer` 依赖。
6. 新增 `/api/auth/bootstrap-status`、`login`、`logout`、`me`、`active-workspace`；`main.py` 注册路由与中间件。非 `bid_writer` 的既有业务路由由公共工作空间依赖统一返回固定 `role_forbidden`，设置路由由 `require_owner` 收口。

### 步骤 3：运行定向与回归

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_auth_rbac.py
$env:PYTHONHASHSEED='0'; .\.venv\Scripts\python.exe -m pytest -q tests/test_health_and_projects.py tests/test_knowledge_rag.py tests/test_settings_and_revise.py
git diff --check
```

预期：全部通过；没有依赖安装、网络、真实口令或数据库文件进入差异。

## 5. 任务 2：成员管理与角色默认拒绝

**允许文件：**

- 修改：`backend/app/services/auth_service.py`
- 修改：`backend/app/api/auth.py`
- 修改：`backend/app/api/deps.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/api/settings.py`
- 修改：`backend/tests/test_auth_rbac.py`

**测试先行：** 所有者可创建/启停/改角色/移除成员；最后一个所有者无法移除或降级；非所有者为 403；`finance`、`hr`、`bidder` 在 P10A 请求业务 API 与设置 API 均为固定 `role_forbidden`，不回显 API Key；`bid_writer` 保持既有业务访问。

**最小实现：** 增加成员列表与管理 API；先完成所有者检查和最后所有者保护，再将业务公共依赖接入 `require_bid_writer`，设置路由接入 `require_owner`。不可在此任务按路径猜测财务/人力/投标人权限，不得改变已有业务数据的工作空间过滤。

**验证：** 定向 `test_auth_rbac.py`、settings/项目/知识库回归、`git diff --check`。Grok 仅回报审查请求。

## 6. 任务 3：登录页、会话恢复与受限导航

**允许文件：**

- 修改：`frontend/src/shared/lib/api.ts`
- 修改：`frontend/src/App.tsx`
- 修改：`frontend/src/app/router.tsx`
- 修改：`frontend/src/app/layout/AppShell.tsx`
- 修改：`frontend/src/app/layout/Sidebar.tsx`
- 修改：`frontend/src/app/layout/TopBar.tsx`
- 新建：`frontend/src/features/auth/types.ts`
- 新建：`frontend/src/features/auth/hooks/useAuthSession.ts`
- 新建：`frontend/src/features/auth/pages/LoginPage.tsx`
- 新建：`frontend/src/features/auth/pages/LoginPage.css`
- 新建：`frontend/e2e/auth-rbac.spec.ts`
- 修改：`frontend/package.json`

**测试先行：** 路由拦截模拟未登录时只显示登录页；登录成功后恢复业务壳；退出后清空内存会话并回登录页；非 `bid_writer` 不显示业务导航且直接访问业务路由会被重定向；浏览器不把口令、Cookie 或 CSRF 写入 localStorage/sessionStorage，不访问外部主机。

**最小实现：** `apiFetch` 使用同源 Cookie（不读取 Cookie 值），从内存会话附带 CSRF 头；`AuthProvider` 只保存脱敏 `me` 响应与当前工作空间；路由在 `auth_mode=disabled` 的后端兼容返回下保留个人版业务壳。所有权限仍由后端决定。

**验证：** `npm run lint`、`npm run build`、新增 `npm run test:e2e:auth-rbac`、既有 `test:e2e:semantic-index` 与 `test:e2e:cards`。

## 7. 任务 4：验收、运行说明与后续权限包

Codex 在每个 Grok 任务后独立审查白名单、Cookie 属性、密码/会话摘要、`X-Workspace-Id` 成员校验、所有者保护、错误脱敏和前端存储；重跑后端全量、前端 lint/build、认证 E2E、P9C/卡片 E2E 与 `git diff --check`。

文档闭环必须新建本机身份与 RBAC 契约，更新路线图、交接和联调清单，明确：

- 启用认证的管理员交互式引导顺序；
- `disabled` 与 `required` 模式的迁移差异；
- P10A 只建立安全身份底座，财务、人力、投标人业务权限仍需 P10B 及后续独立计划；
- 不记录真实口令、Cookie、CSRF、会话摘要、数据库文件或用户业务内容。

## 8. 总验收矩阵

| 维度 | 通过标准 |
|---|---|
| 身份安全 | 口令与会话原始值不落库/不回显；错误登录无用户枚举；Cookie/CSRF/过期/登出均受控。 |
| 工作空间 | `required` 模式不能以伪造头进入非成员空间；旧资源跨空间继续 404。 |
| 角色 | 所有者与 `bid_writer` 可维持生产链；其他三个角色在 P10A 默认拒绝业务端点，不能读设置密钥。 |
| 迁移 | `disabled` 个人版回归不受破坏；`required` 未引导时不开放业务 API；不修改历史业务行。 |
| 前端 | 无 Token/口令/CSRF 持久化；未登录无业务壳；导航隐藏不替代后端校验。 |
| 回归 | 后端全量 pytest、前端 lint/build、认证/P9C/卡片 E2E、`git diff --check` 全部通过。 |
