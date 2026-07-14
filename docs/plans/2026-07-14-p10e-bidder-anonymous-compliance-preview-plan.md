# P10E 投标人匿名合规预览实施计划

> **执行约束**：Grok 仅按本计划的单一白名单任务实现和自测，不提交、不推送；Codex 负责差异审查、独立验收、文档闭环、提交和推送。

**目标：** 为严格 `bidder` 成员交付一个只读、工作空间级、无项目和个人信息泄漏的响应矩阵合规汇总预览。

**架构：** 后端新增独立 `bidder` 路由和服务，复用既有编辑态的响应矩阵收敛结果，只输出固定匿名统计投影。前端新增隔离特性页和严格角色门禁；不接入既有项目、编辑态、财务、人力、文件或设置 API。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、React、TypeScript、Vite、Playwright、pytest。

---

## 范围冻结

- 契约：`docs/p10e-bidder-anonymous-compliance-preview-contract.md`。
- 唯一业务接口：`GET /api/bidder/compliance-preview`。
- 唯一数据源：当前空间技术标的收敛 `responseMatrix`。
- 预览只给聚合计数与整数基点；不输出项目、条目或原文。
- 不建表、不迁移、不安装依赖、不触网、不调用 LLM、不创建任务。
- 禁止修改既有 `finance`、`hr`、`get_workspace_id`、项目 API、编辑态写入语义、认证中间件或数据库结构。

## 任务 1：后端严格角色与匿名聚合（Grok）

**白名单文件：**

- 修改：`backend/app/api/deps.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/main.py`
- 新建：`backend/app/api/bidder.py`
- 新建：`backend/app/services/bidder_compliance_preview_service.py`
- 新建：`backend/tests/test_bidder_compliance_preview.py`

**步骤：**

1. 先在 `test_bidder_compliance_preview.py` 写失败测试，覆盖严格 `bidder`、其他角色/所有者/disabled/未登录/跨空间、固定字段投影、空态、`no-store`、失效引用收敛、基点计算与审计脱敏。
2. 运行 `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/test_bidder_compliance_preview.py`，记录实现前失败原因；不得为通过测试而放宽断言。
3. 在 `deps.py` 新增 `require_bidder`。实现与 `require_hr` 对称：disabled 和无会话均为 `403 role_forbidden`（全局中间件先拦截时保持 `401 auth_required`），非成员空间保留 `403 workspace_forbidden`，仅精确 `bidder` 通过。
4. 在新服务中查询当前空间技术标，逐个取得既有收敛编辑态矩阵；只聚合状态，不返回行。覆盖率按契约计算；成功读后写固定、无内容的审计目标。
5. 在新路由中只注册 GET，设置 `Cache-Control: no-store`，并以 Pydantic 响应模型锁定字段白名单。所有新模块与公开函数必须先写中文「模块 / 用途 / 对接 / 二次开发」四字段注释。
6. 重跑定向测试和以下回归：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_bidder_compliance_preview.py tests/test_auth_rbac.py tests/test_finance_role.py tests/test_hr_credential_cards.py
git diff --check
```

7. 通过消息箱发送 `review_request`，报告失败先测、精确文件列表、最终命令与结果、风险和未做项；不得 `commit` 或 `push`。

## 任务 2：前端受限页与本地 E2E（Grok）

**前置条件：** Codex 已审查并提交任务 1；Grok 以该提交为基线再开始。

**白名单文件：**

- 修改：`frontend/src/features/auth/hooks/useAuthSession.ts`
- 修改：`frontend/src/app/router.tsx`
- 修改：`frontend/src/app/layout/AppShell.tsx`
- 修改：`frontend/package.json`
- 新建：`frontend/src/features/bidder/types.ts`
- 新建：`frontend/src/features/bidder/lib/bidderComplianceApi.ts`
- 新建：`frontend/src/features/bidder/hooks/useBidderCompliancePreview.ts`
- 新建：`frontend/src/features/bidder/pages/BidderCompliancePreviewPage.tsx`
- 新建：`frontend/src/features/bidder/pages/BidderCompliancePreviewPage.css`
- 新建：`frontend/e2e/bidder-compliance-preview.spec.ts`

**步骤：**

1. 先写 E2E 失败用例：投标人访问、匿名字段渲染、空态、受限角色不请求预览、固定错误文案、网络白名单和浏览器存储为空。
2. 运行 `npm run test:e2e:bidder-compliance-preview`，记录实现前失败。
3. 仅在会话 hook 中派生 `canAccessBidder`；路由新增 `RequireBidder` 和 `/bidder`，导航新增独立「投标人 / 合规预览」分组。禁止改变其他角色入口。
4. 新特性只能用 `apiFetch` 调用预览 GET；请求结果仅在 React 内存保存，不能请求项目、编辑态、文件、设置、财务或人力接口。
5. 页面只显示契约字段和固定中文说明；API 失败不回显服务端详情。所有新模块与公开函数都写中文四字段注释。
6. 重跑：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:bidder-compliance-preview
npm run test:e2e:auth-rbac
npm run test:e2e:finance-role
npm run test:e2e:hr-credential-cards
git diff --check
```

7. 经消息箱发送 `review_request`，并等待 Codex 独立审查；不得提交或推送。

## 任务 3：Codex 审查、验收与文档闭环

1. 对每次 Grok 的 `review_request` 检查 `git diff --check`、变更白名单、四字段注释和契约逐条一致性；越界、泄漏或错误收口必须通过消息箱退回。
2. 在后端任务通过后，由 Codex 使用中文提交信息提交并推送协作分支，再向 Grok 下发前端单一任务。
3. 在前端任务通过后，Codex 独立运行后端全量、前端 `lint`/`build`、P10E E2E 及相关角色 E2E；按风险决定是否运行全量 E2E。
4. 更新 `docs/integration-checklist.md`、路线图、`docs/HANDOFF-next.md` 的完成状态、真实 HEAD、验收结果、未做项和注释齐备表；每次 HANDOFF 更新保留四字段注释专章。
5. 用独立中文提交提交文档闭环，推送 `collab/grok-code-codex-review`，再次核对本地 HEAD、远端 HEAD、工作区和 `git diff --check`。

## 完成判定

P10E 只有在后端角色边界、匿名投影、审计与前端网络/存储边界均通过独立测试，三段提交（计划、实现、文档）均已推送协作分支，且交接与联调文档记录真实结果后，才可标记完成。
