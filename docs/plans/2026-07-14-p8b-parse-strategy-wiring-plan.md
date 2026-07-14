# P8B 工作空间解析策略接线实施计划

> **执行约束**：Grok 只按本计划分任务实现并自测，不提交、不推送；Codex 负责范围审查、独立验收、文档闭环、中文提交和推送。实施前后均以 `collab/grok-code-codex-review` 当前 HEAD 为基线。

**目标：** 让工作空间的 `light`、`local`、`ask` 解析策略同时驱动技术标和商务标入口，而不引入服务端 MinerU 或 Docling。

**架构：** 后端在已有设置路由下新增一个仅返回策略枚举的脱敏读取接口，且复用既有 `get_workspace_id` 标书制作者权限。前端以一个共享 Hook 和选择组件在点击时重新读取策略：轻量路径沿用既有解析任务，本地路径只导航到既有回传页，询问路径仅决定本次动作。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、React、TypeScript、Vite、Playwright、pytest。

---

## 范围冻结

- 契约：`docs/p8b-parse-strategy-wiring-contract.md`。
- 唯一新接口：`GET /api/settings/parse-strategy`，严格只返回 `{parseStrategy}` 并设置 `Cache-Control: no-store`。
- 不改 `GET|PUT /api/settings` 的所有者限制；新接口使用既有 `get_workspace_id`，因此认证模式 required 下只接受 `bid_writer`。
- 不改 `parse_engines.py`、`task_service.py`、`parse-callback`、数据库模型、依赖、认证中间件、财务/人力/投标人代码。
- 轻量任务固定携带 `{ engine: "lightweight" }`；本地与询问策略绝不形成解析任务 payload。
- 前端禁止对策略使用浏览器持久化存储，点击解析时必须刷新读取。

## 任务 1：策略脱敏读取与后端先失败测试（Grok）

**白名单文件：**

- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/api/settings.py`
- 修改：`backend/app/services/settings_service.py`
- 新建：`backend/tests/test_parse_strategy_read.py`

**步骤：**

1. 在 `test_parse_strategy_read.py` 先写失败测试：无设置行时返回 `{parseStrategy:"light"}` 且数据库不新增设置行；三种保存值逐一返回；响应没有完整设置字段且 `Cache-Control=no-store`；required 下精确 `bid_writer`、未登录、finance/hr/bidder/owner 和非成员工作空间；disabled 兼容路径。
2. 运行 `cd backend; .\.venv\Scripts\python.exe -m pytest -q tests/test_parse_strategy_read.py`，记录失败原因。
3. 在 `settings_service.py` 写最小只读函数：已存在行返回合法值；无行只返回 `DEFAULT_PARSE`，不得调用 `get_or_create_settings`、不得 commit。公开函数写中文四字段注释。
4. 在 `schemas.py` 新增只含 `parse_strategy`（序列化别名 `parseStrategy`）的响应模型；不得复用会序列化 Key 的完整设置模型。
5. 在 `settings.py` 新增 `GET /parse-strategy`，依赖 `get_workspace_id`，响应只使用新模型并加 `Cache-Control: no-store`。更新模块与公开路由的中文四字段注释；原有 owner 路由与序列化函数语义不变。
6. 重跑 `backend` 中的 `test_parse_strategy_read.py`、`test_settings_and_revise.py`、`test_auth_rbac.py`、`test_parse_engines.py`、`test_parse_export.py`、`test_async_and_callback.py`，并运行 `git diff --check`。
7. 通过消息箱发送 `review_request`，包含失败先测证据、精确文件列表、最终命令和结果、未做项。不得提交、推送或改动白名单外文件。

## 任务 2：共享前端策略决策与本地预填（Grok）

**前置条件：** Codex 已审查任务 1 并使用中文提交将其固定到协作分支；Grok 必须从该提交开始。

**白名单文件：**

- 新建：`frontend/src/features/parse-strategy/lib/parseStrategyApi.ts`
- 新建：`frontend/src/features/parse-strategy/hooks/useWorkspaceParseStrategy.ts`
- 新建：`frontend/src/features/parse-strategy/components/ParseStrategyChoiceDialog.tsx`
- 新建：`frontend/src/features/parse-strategy/components/ParseStrategyChoiceDialog.css`
- 修改：`frontend/src/features/local-parser/pages/LocalParserPage.tsx`
- 修改：`frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- 修改：`frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
- 修改：`frontend/package.json`
- 新建：`frontend/e2e/parse-strategy-wiring.spec.ts`

**步骤：**

1. 先写 `parse-strategy-wiring.spec.ts` 的失败用例。所有用例必须使用真实 E2E 后端和前端，不得 route stub、真实 Key、固定 sleep 或外网。覆盖契约 §6 所列技术标、商务标、本地跳转、询问框取消/两种选择、失败收口、请求与存储边界。
2. 运行 `cd frontend; npm run test:e2e:parse-strategy`，记录失败原因。
3. 新建 API 客户端和 Hook。客户端只能 `apiFetch("/settings/parse-strategy")`；Hook 的 `refresh()` 返回本次合法策略或固定中文失败，不读写 `localStorage`/`sessionStorage`。每个新模块与公开函数写中文四字段注释。
4. 新建可访问的通用选择框，提供“在线轻量解析”“本地 MinerU 回传”“取消”三个明确按钮。组件仅发出当前一次选择事件，不能读写设置、文件、编辑态或任务。
5. 改造本地解析页，使用查询参数 `projectId` 预填表单项目 ID；URL 参数缺失或空白时维持原手输体验。不得自动提交回调。
6. 改造技术标文档解析入口：点击时 `refresh()`；`light` 才调用既有 `pipeline.runTask("parse", {engine:"lightweight"})` 并保留刷新编辑态/成功提示；`local` 导航到带编码项目 ID 的本地页；`ask` 打开选择框。策略读取或取消时不得创建任务。
7. 改造商务标 `onPickFile`、“整段重解析”和解析反馈的 `onRegenerate`，全部经同一个本地 `handleParse` 决策函数。上传成功后仅 `light` 自动创建轻量任务；`local`/`ask` 不创建任务。不得改其他 `biz_*` 或导出任务。
8. 在 `package.json` 新增 `test:e2e:parse-strategy`，只执行新 spec。执行 `npm run lint`、`npm run build`、`npm run test:e2e:parse-strategy`、`npm run test:e2e:auth-rbac`、`npm run test:e2e:bid-template-reuse` 和 `git diff --check`。
9. 通过消息箱发送 `review_request`，报告失败先测、白名单、命令结果、网络/存储检查、未做项。不得提交或推送。

## 任务 3：Codex 独立审查、验收与文档闭环

1. 每次 Grok 回传后，先检查消息箱内容、`git status`、`git diff --check`、白名单、四字段注释和契约逐条一致性。出现策略降级、完整设置泄漏、MinerU/Docling/外部进程、持久化存储、越权路由或白名单外改动时，必须通过消息箱退回。
2. 任务 1 通过后，Codex 独立运行其定向测试与 auth/parse 回归，确认新 GET 在未设置行时无写入后，以中文提交并推送；再下发任务 2。
3. 任务 2 通过后，Codex 独立运行后端全量、前端 lint/build、P8B E2E、解析后端回归、技术标/商务标相关 E2E；必要时运行前端 E2E 全量。验证轻量任务 `result.engine=lightweight`，并在本地/取消/读取失败路径中确认没有创建任务。
4. 更新 `docs/integration-checklist.md`、`docs/plans/2026-07-12-bid-writer-roadmap.md`、`docs/HANDOFF-next.md`：写入真实提交、命令结果、P8B 完成状态、未做的生产级 MinerU/Docling 与四字段注释表。交接文档必须保留注释规范专章。
5. 以独立中文提交完成文档闭环，推送 `collab/grok-code-codex-review`；最后确认 `git rev-parse HEAD` 等于 `origin/collab/grok-code-codex-review`、工作区干净且 `git diff --check` 通过。

## 完成判定与未完成项

P8B 只有在策略读取不泄漏完整设置、三种策略在两个工作台均按契约决策、后端与浏览器边界均经独立验证、计划/实现/交接文档均已中文提交并推送后才完成。

真实 MinerU/Docling 的生产部署、外部可执行路径白名单、callback Token 默认策略、解析器安装、更多格式支持、回传后自动分析与策略版本历史都不在本包范围；若要实施，必须另建安全与部署契约。
