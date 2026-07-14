<!--
模块：P10J 财务个人成本变更记录实施计划
用途：把本人成功成本变更的固定投影与严格财务页面拆为后端、前端两个可审查受限任务。
对接：docs/p10j-finance-personal-cost-change-events-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：必须遵守文件白名单；Grok 只实现和自测，Codex 独立审查、验收、提交与推送。
-->

# P10J 财务个人成本变更记录实施计划

> **状态**：计划已冻结，等待后端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 406 passed；前端 lint/build 通过、单 worker 串行全量 E2E 110 passed。Playwright 共用 SQLite 重置库，必须串行。

## 1. 决策

现有 P10C 审计数据不含项目、金额、业务内容或前后快照，只能证明某个已验证用户在某工作空间成功新增、修改或删除过一个不透明成本条目。P10J 据此只交付“我的成本变更记录”，不冒充完整财务审计。

选择该能力是因为它完全复用既有审计表、strict `finance` 门禁和脱敏 action/target，不需要外部数据、业务迁移或扩大标书内容权限。其他候选继续保留为未实现主线，禁止搭车。

## 2. 冻结数据与行为

1. 后端仅按当前工作空间、当前会话 user、固定三 action、`success` 与合法 `fce_*` target 查询审计表。
2. SQL 只投影 action/target/created_at，固定最近 50 条，按时间和事件 ID 倒序；服务端映射为 `create|update|delete`。
3. API 只返回 action/entryId/occurredAt，固定 `no-store`；成功读取写固定脱敏读取审计。
4. 前端使用独立严格财务路由，服务端结果直出，不查询项目或成本详情；首次严格单 GET，刷新再发一次。
5. 无表、迁移、依赖、写接口、筛选、分页、导出、存储、URL 参数、轮询、外网或跨角色投影。

## 3. 任务 1：后端受限实现

仅允许修改或新增：

- `backend/app/api/schemas.py`
- `backend/app/api/finance.py`
- `backend/app/services/finance_cost_change_event_service.py`（新建）
- `backend/tests/test_finance_cost_change_events.py`（新建）

不得修改实体、数据库初始化/迁移、`main.py`、`deps.py`、认证/CSRF、P10B/P10C 服务、其他角色服务、依赖、脚本或既有测试。

实现要求：

- 先写失败测试；服务查询用 SQLAlchemy `select` 明确三列，不得加载 `AuthAuditEventRow` 实体；
- 过滤当前 workspace/current actor/三 action/success/合法 target，固定 LIMIT 50 和稳定倒序；
- 路由从已验证 `request.state` 获取 actor，客户端不能注入 user/workspace/limit；复用 `require_finance`；
- Pydantic 响应使用固定枚举和别名，成功固定 `no-store`；读取审计 action/target 固定且不含返回值；
- 文件顶和公开 API 补齐中文“模块 / 用途 / 对接 / 二次开发”注释；
- 完成后只发送 `review_request`，报告精确文件、失败先测、定向/受影响回归、`git diff --check`、风险和未做项，不 commit/push。

Codex 审查重点：是否误用通用 `list_recent_audit_events` 后在 Python 过滤；是否泄露其他 actor/workspace/action；SQL 是否整实体；未知 query 是否改变上限；读取审计是否进入返回列表；是否新增表或把本能力写成完整审计。

## 4. 任务 2：前端受限实现

后端验收提交后才派发。仅允许修改或新增：

- `frontend/package.json`
- `frontend/src/app/router.tsx`
- `frontend/src/app/layout/AppShell.tsx`
- `frontend/src/features/finance-cost-change-events/types.ts`（新建）
- `frontend/src/features/finance-cost-change-events/lib/financeCostChangeEventsApi.ts`（新建）
- `frontend/src/features/finance-cost-change-events/hooks/useFinanceCostChangeEvents.ts`（新建）
- `frontend/src/features/finance-cost-change-events/pages/FinanceCostChangeEventsPage.tsx`（新建）
- `frontend/src/features/finance-cost-change-events/pages/FinanceCostChangeEventsPage.css`（新建）
- `frontend/e2e/finance-cost-change-events.spec.ts`（新建）

不得修改 `useAuthSession`、共享 API/认证层、P10B/P10C feature、Playwright 配置、依赖、遗留 Sidebar 或后端文件。

实现要求：

- 复用 `RequireFinance`，新增 `/finance/cost-changes` 与「我的成本记录」；收紧 `/finance` 导航精确激活；
- 页面只请求 `GET /finance/cost-change-events`，不请求报价/成本草案或其他业务端点；
- Strict Mode 首次严格单次 GET，手动刷新累计严格两次；只能组件实例级复用在途 Promise，不得模块全局缓存；
- 只显示固定动作中文、entryId、时间和契约限制声明；空态与错误固定中文；
- E2E 阻断项目、编辑态、设置、文件、报价/成本、人力、投标人、未知 API、外网，并验证 storage 为零；
- 完成后只发送 `review_request`，报告定向 E2E、lint/build、网络/存储断言和 `git diff --check`，不 commit/push。

## 5. 独立验收与提交顺序

Codex 依次完成：

1. 审查后端白名单、SQL 投影、本人/空间隔离、字段与读取审计，运行 P10J 定向、P10B/P10C/认证回归和后端串行全量；形成独立中文后端提交并推送。
2. 派发前端单一任务，审查白名单、门禁、请求计数、网络与存储边界，运行 lint、build、P10J 定向及单 worker 全量 E2E；形成独立中文前端提交并推送。
3. 更新本计划、契约、路线图、联调清单和 HANDOFF，形成独立中文文档闭环提交并推送。

建议验证命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_finance_cost_change_events.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_finance_role.py tests/test_finance_cost_draft.py tests/test_auth_rbac.py
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:finance-cost-change-events
npm run test:e2e
```

所有 Playwright 命令必须等待前一个完成，禁止并行。所有 PowerShell 与 Grok 子进程后台静默运行，不启动可见窗口、浏览器或前台应用。
