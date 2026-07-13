# P10A 前端认证模式握手修订

> **状态：** Codex 于 2026-07-14 在 P10A 任务 3 实施前补充。该修订仅解决前端无法区分个人版兼容模式与强制认证未初始化状态的问题，不改变既有身份、会话或角色边界。

## 问题

原有公开接口 `GET /api/auth/bootstrap-status` 只返回 `bootstrapped`。当本机处于下列两种状态时，响应完全相同：

- `auth_mode=disabled` 且尚未创建本机管理员：个人版应继续进入业务壳；
- `auth_mode=required` 且尚未创建本机管理员：前端必须显示登录/引导入口，业务 API 已由服务端固定拒绝。

因此，前端不能从 `bootstrapped` 或 `GET /api/auth/me` 的 401 安全地区分两种状态；以“401 就进入业务壳”或“未初始化就进入业务壳”都会破坏其中一条契约。

## 冻结决策

将公开、只读的 `GET /api/auth/bootstrap-status` 扩展为：

```json
{
  "bootstrapped": false,
  "authRequired": true
}
```

- `authRequired` 严格等于已校验配置 `auth_mode == "required"`；只暴露一个部署模式布尔值，不含用户名、口令、Cookie、CSRF、会话、模型密钥或工作空间数据。
- 前端以该字段决定流程：`false` 时保持个人版业务壳；`true` 时先请求 `/auth/me`，无有效会话则只显示登录页，已初始化状态再允许登录。
- 服务端中间件仍是唯一权限边界。该字段仅解决体验分流，不能授予任何业务 API 权限。

## 任务 3 白名单补充

除主计划任务 3 的前端白名单外，Grok 可为该只读握手**仅修改**：

- `backend/app/api/schemas.py`
- `backend/app/api/auth.py`
- `backend/tests/test_auth_rbac.py`

不得改动认证中间件、配置语义、数据库、依赖、公开路径白名单或任何业务授权规则。

## 验收补充

1. `AUTH_MODE=disabled` 时公开端点返回 `authRequired=false`，前端保持业务壳。
2. `AUTH_MODE=required` 时公开端点返回 `authRequired=true`；无会话只显示登录页，未初始化业务 API 继续是 503。
3. 该响应和前端内存状态均不含口令、Cookie、CSRF、会话摘要、API Key 或用户业务数据。
