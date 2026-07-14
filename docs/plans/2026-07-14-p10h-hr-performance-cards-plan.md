<!--
模块：P10H 人员业绩素材卡实施计划
用途：将人员业绩能力拆为可审查的后端与前端受限实现，并定义独立验收闭环。
对接：docs/p10h-hr-performance-cards-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：必须按本计划的文件白名单与非目标执行；未通过 Codex 审查不得提交实现。
-->

# P10H 人员业绩素材卡实施计划

> **状态**：规划已冻结，尚未派发实现。<br>
> **工作分支**：`collab/grok-code-codex-review`。<br>
> **前置基线**：后端串行全量 378 passed；前端全量 E2E 83 passed；P10D E2E 9 passed；P10F E2E 4 passed。所有 Playwright 命令共用 SQLite 重置库，必须串行运行。

## 1. 背景、目标与独立性

P10D 的 `performance` 只是资质类别，不足以记录人员参与项目的最小业绩信息；P10F 仅快照有效资质卡，不承载业绩。P10H 以独立 `hr_performance_cards` 数据域补足此能力，且只服务严格 HR 的手工素材维护。

本包目标是完成最小业绩卡 CRUD（无删除）、严格角色与工作空间隔离、摘要/详情投影分离、审计脱敏和单页前端。完整数据与权限契约以 [P10H 契约](../p10h-hr-performance-cards-contract.md) 为准。

## 2. 已完成只读审计

| 现有能力 | 结论 | P10H 决策 |
|---|---|---|
| `HrCredentialCardRow` 与 `/api/hr/credential-cards*` | 仅适合资质名称、等级和有效期；`performance` 为枚举类别 | 不扩展既有表或接口，避免混合资质与项目业绩语义 |
| `require_hr` | 已按精确角色、活动空间与非成员空间执行严格检查 | 直接复用，不修改 `deps.py`、认证中间件或会话协议 |
| P10F 团队推荐 | 只快照有效资质卡摘要，禁止人员业绩 | 不读取、写入或修改 P10F 表、服务、投影和技术标工作区 |
| HR 前端 | 已有 `RequireHr`、独立 HR 导航、内存 CSRF 与按需详情模式 | 新建独立 `hr-performance` feature，复用门禁但不改 P10D 页面/Hook/API |
| `Base.metadata.create_all` | 现有本地开发以导入实体注册建表 | 仅注册新实体；不新增 Alembic、迁移脚本或数据库文件 |

## 3. 冻结范围与非目标

**范围内**：当前空间严格 HR 手工创建、读取、编辑、启停业绩卡；最小响应投影；固定审计；独立 HR 页面与 E2E。

**明确不做**：

- P10D 资质卡字段、路由、页面、Hook、E2E 或 `HrCredentialCardRow` 的任何语义变更；
- P10F 团队推荐、技术标工作区、`/api/projects*`、编辑态、文件、响应矩阵、Word、财务、投标人、知识库与标讯；
- 附件、外链、证件号码/校验、照片、联系方式、简历全文、客户联系人、合同金额、报价、自动匹配、AI、审批、导出、批量导入、删除、跨空间搜索、历史版本；
- 认证/RBAC 依赖、中间件、CSRF 协议、数据库迁移、依赖、Playwright 配置或任何 PowerShell 脚本；
- 浏览器存储、URL 查询参数、外网请求与任何真实业务数据种子。

## 4. 数据、权限、缓存与审计矩阵

| 维度 | 冻结决策 |
|---|---|
| 数据来源 | 仅严格 HR 手工 JSON 输入；绝不从项目、文件、标讯、P10D/P10F 或外网读取/推演 |
| 数据表 | 新建 `hr_performance_cards`；服务端生成 `hpc_*`、工作空间、创建人与 UTC 时间 |
| 白名单字段 | `personName`、`projectName`、`projectRole`、`completedYear`、`performanceSummary`、`remark`、`isActive`；详情才返回摘要和备注 |
| 请求校验 | 手工读取 JSON 对象；`extra=forbid`；完成年份 `StrictInt` 1900–2100；启用状态 `StrictBool` |
| 权限 | 仅 required 模式精确 `hr`；`owner` 不隐式绕过；未登录 401，disabled/非 HR 403，非成员空间 403 |
| 资源隔离 | 跨空间、不存在、伪造 ID 统一 `404 hr_performance_not_found`，不回显 ID |
| 缓存与存储 | 全部成功响应 `Cache-Control: no-store`；仅 React 内存；禁止 URL 参数和浏览器存储 |
| 审计 | 创建/更新仅记录 `hr_performance_create` / `hr_performance_update` 与 `hpc_*` target；不记录业务字段、操作者或空间 |

## 5. 允许改动文件

### 任务 1：后端最小业绩卡域

仅允许：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/main.py`
- `backend/app/api/schemas.py`
- `backend/app/api/hr.py`
- `backend/app/services/hr_performance_service.py`（新建）
- `backend/tests/test_hr_performance_cards.py`（新建）

不得修改 `backend/app/api/deps.py`、认证/会话/CSRF 中间件、P10D/P10F 服务、项目/编辑态/文件/财务/投标人路由、依赖、迁移、脚本或现有测试。

### 任务 2：前端严格 HR 页面

仅允许：

- `frontend/package.json`
- `frontend/src/app/router.tsx`
- `frontend/src/app/layout/AppShell.tsx`
- `frontend/src/features/hr-performance/types.ts`（新建）
- `frontend/src/features/hr-performance/lib/hrPerformanceApi.ts`（新建）
- `frontend/src/features/hr-performance/hooks/useHrPerformanceCards.ts`（新建）
- `frontend/src/features/hr-performance/pages/HrPerformanceCardsPage.tsx`（新建）
- `frontend/src/features/hr-performance/pages/HrPerformanceCardsPage.css`（新建）
- `frontend/e2e/hr-performance-cards.spec.ts`（新建）

不得修改 `useAuthSession`、共享 API/认证层、P10D/P10F feature、技术标工作区、Sidebar、Playwright 配置、依赖或任何后端文件。路由复用既有 `RequireHr`，导航只增加一项严格 HR 可见的「人员业绩」。

## 6. 任务拆分与完成条件

### 任务 1：后端

1. 先新增定向失败测试，锁定角色/空间矩阵、字段投影、严格输入、审计与 `no-store`。
2. 新建 ORM 行、服务、Schema 与 HR 路由；文件顶及公开 API 必须补齐中文「模块 / 用途 / 对接 / 二次开发」注释。
3. 列表只返回摘要；详情、创建与更新才返回 `performanceSummary`、`remark`；写入通过既有 CSRF。
4. 运行定向测试及 P10D/P10F/认证回归；请求 Codex 审查，保持未提交。

**Grok 应报告**：精确文件清单、失败先测结果、定向测试命令与结果、`git diff --check`、未做项与风险。

### 任务 2：前端

1. 先新增专用 E2E，锁定初始只读摘要、点选才读详情、严格门禁、网络白名单、错误脱敏与无浏览器存储。
2. 以独立 feature 实现类型、API、Hook、页面和样式；所有新增模块及导出函数补齐中文四字段注释。
3. 创建、编辑与启停成功后强制重读列表和当前详情；不使用乐观更新。
4. 运行定向 E2E 和 P10D/P10F/认证回归；请求 Codex 审查，保持未提交。

**Grok 应报告**：精确文件清单、定向 E2E 结果、网络/存储断言、`lint`、`build`、`git diff --check` 与未做项。

## 7. Codex 审查与独立验收

1. 审查 Grok 每个任务的 diff 是否严格在白名单内，重点检查 P10D/P10F 未被扩权、响应不泄露摘要/备注、ID 不回显、审计不含业务数据。
2. 后端实现通过审查后，才允许 Grok 形成单一中文实现提交；前端同理，禁止合包。
3. 前端完成后，Codex 串行执行下列验证，并核对工作区与远端分支：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:hr-performance-cards
npm run test:e2e:hr-credential-cards
npm run test:e2e:hr-team-recommendations
npm run test:e2e:auth-rbac

cd ..
git diff --check
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
```

Playwright 命令必须逐条等待完成后再启动下一条，禁止并行。若契约范围、白名单、验收或文档未满足，Codex 通过消息箱退回 Grok；通过后由 Codex 完成中文验收/交接文档、中文提交及推送。

## 8. 文档闭环

实现验收通过后，Codex 更新：本计划的验收记录、P10H 契约、`docs/plans/2026-07-12-bid-writer-roadmap.md`、`docs/integration-checklist.md` 与 `docs/HANDOFF-next.md`。HANDOFF 必须保留代码注释规范与注释齐备表；新路径须更新表中状态。计划、后端、前端和文档闭环维持独立中文提交并推送到协作分支。
