# P10A 刷新后 CSRF 会话续发修订

> **状态：** Codex 于 2026-07-14 在 P10A 任务 3 独立审查中补充。该修订解决浏览器刷新后 React 内存 CSRF 丢失、而 `/api/auth/me` 按既有脱敏契约不重复下发 CSRF 所造成的写操作不可用问题。

## 问题

登录响应可提供原始 CSRF 值，前端只放入内存；这是正确的。浏览器刷新后，HttpOnly 会话 Cookie 仍存在，但 React 内存被清空。`GET /api/auth/me` 只恢复脱敏身份、不重复返回 CSRF，因此已恢复的用户无法通过后续 POST、PUT、PATCH、DELETE 的 CSRF 校验，甚至不能正常退出。

## 冻结决策

新增受有效会话保护的只读入口：`GET /api/auth/csrf`。

1. 它不在公开白名单中；认证中间件先验证 HttpOnly 会话。由于是 GET，不要求旧 CSRF，专门用于硬刷新后的恢复。
2. 服务端为当前会话轮换一个新的随机 CSRF 原始值，仅返回一次 `csrfToken`；数据库只更新其 SHA-256 摘要，响应、审计和日志不得记录原始值或摘要。
3. 响应必须 `Cache-Control: no-store`；前端仅写入模块/React 内存，禁止 localStorage、sessionStorage、Cookie 或 URL。
4. 前端仅在 `authRequired=true`、`/auth/me` 已成功恢复会话且本轮没有登录响应 CSRF 时调用它；失败则清空内存并进入受控未登录状态，不能渲染可写业务壳。
5. 此端点不改变角色、工作空间或公开路径；所有业务授权仍在服务端依赖层执行。

## 任务 3 白名单补充

除主计划任务 3 与前一份握手修订列出的文件外，Grok 可为本修订**仅修改**：

- `backend/app/services/auth_service.py`
- `backend/app/api/schemas.py`
- `backend/app/api/auth.py`
- `backend/tests/test_auth_rbac.py`
- `frontend/src/features/auth/types.ts`
- `frontend/src/features/auth/hooks/useAuthSession.ts`
- `frontend/e2e/auth-rbac.spec.ts`

不得修改认证中间件、实体、数据库、Cookie 属性、公开路径白名单、依赖或任何业务授权规则。

## 验收补充

1. 有效会话可获取一次新的 CSRF；无会话得到固定 401，响应不含原始口令、Cookie 或摘要。
2. 新值可通过一次变更请求；刷新恢复后的前端将它仅存内存并正常完成受 CSRF 保护的退出/写操作。
3. CSRF 续发响应带 `Cache-Control: no-store`，旧 CSRF 轮换后失效。
