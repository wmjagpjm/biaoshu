# P10F：人力项目团队推荐快照实施计划

## 背景与目标

P10D 已将人员资质限制为严格 HR 当前空间的最小卡片库，刻意不含项目关联或团队推荐。P10F 复用这些有效卡的摘要字段，让 HR 为技术标项目人工组装一份可复查的团队推荐快照；标书制作者仅在自己当前项目中按需查看最小展示投影。

本计划的固定行为以 `docs/p10f-hr-team-recommendation-contract.md` 为准。任何未写进该契约的项目内容、人员数据、数据导出、自动匹配或角色放宽均不在范围内。

## 只读审计结论

1. `HrCredentialCardRow` 已具备工作空间、有效状态和最小资质摘要，但其 `remark` 只能在 HR 卡片详情返回；团队快照不得复制它。
2. 既有 `/api/projects*` 依赖 `get_workspace_id` 且返回完整 `ProjectOut`，HR 不可复用；必须新建只含技术标 `id/name` 的 HR 选择器。
3. 严格 `require_hr` 在 required 下精确匹配角色，并拒绝 disabled 与所有者隐式绕过；HR 写接口继续使用该依赖。标书制作者展示投影需要新增同样拒绝 disabled/所有者隐式绕过的严格读取依赖，不能把既有个人版 `get_workspace_id` 语义误用为跨角色授权，也不能反向开放 `/api/hr/*`。
4. 资质卡不物理删除但可停用。推荐需存成员快照并在保存时只接受有效卡，避免后续卡片编辑/停用无审计地改变已交付人员名单。

## 任务 1：后端受限实现（交给 Grok）

允许改动：

- `backend/app/models/entities.py`、`backend/app/models/__init__.py`、`backend/app/main.py`；
- `backend/app/api/deps.py`（仅新增 P10F 严格标书制作者读取依赖，不改既有依赖语义）；
- `backend/app/api/schemas.py`；
- 新建 `backend/app/services/hr_team_recommendation_service.py`；
- 新建或最小扩展 HR/项目专用 API 路由及 `backend/app/main.py` 注册；
- 新建 `backend/tests/test_hr_team_recommendations.py`，以及为真实隔离矩阵所必需的既有测试文件。

实现要求：

1. 建立推荐主表与成员快照表，并以数据库唯一约束保证每个工作空间技术标项目最多一个记录；启动导入模型确保 SQLite 现有 `create_all` 路径可建表。
2. HR 路由严格复用 `require_hr`，且不可调用或放宽 `/projects*`。项目选择器只能查询本空间 `kind=technical` 的 `id/name`。
3. `PUT` 必须手工安全读取 JSON；只接收 `memberCardIds`，顺序保留，0–30、去重、有效且同空间卡片校验原子完成。首次创建与后续替换须在一笔事务内完成，成员快照不含 `remark`。
4. 仅为 required 模式精确 `bid_writer` 新增单项目只读展示投影，确认项目归属且 `kind=technical` 后再查询；`empty` 不能以 404 表示；响应不得泄露 HR 内部 ID 或人员扩展字段。
5. 所有响应 `no-store`，所有写操作走既有 CSRF；固定错误、不回显输入；审计 action/target 按契约脱敏。
6. 禁止改认证中间件、`get_workspace_id`、`require_hr`、既有 P10D 卡片字段/接口、完整项目响应、财务/投标人路径，以及任何外部网络/依赖。

Grok 先写失败测试，再实现至通过；不得提交、推送或修改文档。Codex 审查时将独立检查 schema 响应字段、SQL 作用域、事务、快照不可变性、CSRF、审计和授权矩阵。

## 任务 2：前端受限实现（须在任务 1 验收后再下发）

预期白名单：认证能力派生、`router.tsx`、`AppShell.tsx`、新增 `frontend/src/features/hr-team-recommendation/**`、技术标工作区的按需只读展示组件、对应 CSS 和新增 P10F E2E。不得改动共享项目存储、编辑器状态、财务/投标人模块或浏览器存储策略。

体验约束：

1. 只有严格 HR 显示团队推荐入口；初始只取 HR 项目选择器和资质卡摘要，点选项目后才取团队详情。不可请求资质卡备注。
2. 保存仅发送有序 `memberCardIds`；成功后重新取摘要与详情；服务端错误固定中文脱敏，不回显 detail。
3. 技术标工作区仅在用户主动点击查看时请求 `/projects/{projectId}/team-recommendation`；ready 显示顺序、协作显示名和资质摘要，empty 明确显示未推荐。不可请求 `/hr/*`、项目完整对象、编辑态、文件、财务或外网作为回退。
4. 页面与测试不得向 `localStorage`/`sessionStorage` 写人员或推荐数据。

## 独立验收与提交顺序

1. Codex 对 Grok 后端差异执行白名单审查、`git diff --check`、P10F 定向测试及后端全量 `pytest -q`；通过后才以中文提交并推送后端实现。
2. Codex 对前端差异执行白名单和网络边界审查，独立运行 `npm run lint`、`npm run build`、P10F E2E、既有 HR/认证/技术标相关 E2E 与全量 E2E。
3. 仅在完整独立验收通过后，由 Codex 更新交接、路线图和联调清单，做中文文档闭环提交并推送 `collab/grok-code-codex-review`。

## 明确非目标

不做真实人员档案、个人业绩、AI 团队推荐、项目角色自由填写、审批/发布/撤回流、附件/证件、导出、Word 自动写入、项目内容共享、跨项目/跨空间汇总、多人并发合并、历史版本或删除。后续若要引入任一项，必须另行冻结数据保留、访问主体、审计和展示投影契约。
