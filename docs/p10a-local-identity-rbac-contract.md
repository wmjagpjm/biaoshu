# P10A 本机身份、会话与 RBAC 契约

> **状态：** 2026-07-14 完成实现与独立验收。P10A 仅提供本机身份和默认拒绝的权限底座；财务、人力、投标人业务授权属于后续 P10B，未在本包开放。

## 1. 启用顺序

1. 默认 `AUTH_MODE=disabled`：保持个人版兼容，前端直接显示既有业务壳，`X-Workspace-Id` 保持历史测试隔离语义。
2. 切换为 `AUTH_MODE=required` 前，在受控本机终端执行 `backend/scripts/bootstrap_local_admin.py`，按交互提示输入用户名和口令；脚本只使用 `getpass`，不接受口令命令行参数。
3. 启动后，前端先读取 `GET /api/auth/bootstrap-status`。`authRequired=true` 时仅有效登录会话可进入业务壳；未初始化和未登录均不会显示业务壳。
4. 登录后浏览器仅持有 HttpOnly、SameSite=Strict、`Path=/api` 的不透明会话 Cookie；口令、Cookie、CSRF、会话摘要、API Key 和数据库文件均不得写入浏览器存储或文档。

## 2. 固定安全契约

- 角色仅为 `bid_writer`、`finance`、`hr`、`bidder`；工作空间所有者是成员标记，不是第五种角色。
- 口令使用 `scrypt` 与随机盐；会话和 CSRF 仅持 SHA-256 摘要。未知 `AUTH_MODE` 在加载配置时拒绝，绝不静默退化。
- `required` 模式下，除健康检查和明确认证公开入口外，所有 `/api` 由会话中间件保护。`X-Workspace-Id` 只能从当前会话已加入的工作空间中选择，不能授予访问权。
- 成员管理与设置仅当前工作空间所有者可用；最后一个活跃所有者不能被降级、停用或移除。停用或移除成员会撤销其会话。
- `bid_writer` 可使用既有业务；`finance`、`hr`、`bidder` 在 P10A 对既有业务和设置一律收到 `role_forbidden`，且不能读取 API Key。

## 3. 会话与 CSRF 恢复

登录响应只在本次返回原始 CSRF。页面硬刷新后，前端先用 Cookie 调用 `/api/auth/me` 恢复脱敏身份，再调用受会话保护的 `GET /api/auth/csrf` 轮换并取得新的 CSRF：

- 该入口不在公开路径中；无会话固定 401。
- 旧 CSRF 轮换后立即失效；响应只包含 `csrfToken`，且带 `Cache-Control: no-store`。
- 前端只保存到 React/模块内存；续发失败进入受控未登录态，不渲染可写业务壳。

## 4. 前端体验边界

- `authRequired=false` 才进入个人版业务壳；认证模式握手失败保持非业务错误页，不能猜测为个人版。
- 未登录只显示中文登录页；非 `bid_writer` 直达业务路由会重定向到受限说明页，并隐藏业务导航；所有者才显示设置入口。
- 前端导航只是体验分流，服务端鉴权、角色和工作空间校验始终是唯一权限边界。

## 5. 验收与非目标

验收包括后端全量 pytest、认证定向测试、前端 lint/build、认证 E2E、P9C 语义索引 E2E、知识卡片 E2E 和 `git diff --check`。当前不包含公开注册、口令找回、邮件/短信、OAuth/JWT、MFA、审计导出、跨空间共享或财务/人力/投标人业务权限；这些必须另立 P10B 及后续计划。
