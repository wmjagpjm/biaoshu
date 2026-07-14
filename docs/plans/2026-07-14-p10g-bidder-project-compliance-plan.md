# P10G：投标人项目级合规统计预览实施计划

## 背景与只读审计结论

P10E 已安全交付当前工作空间技术标响应矩阵的匿名总量，但其契约刻意不暴露项目 ID 或名称，不能支持投标人在一个已知项目范围内查看单项目准备度。现有 `require_bidder` 已精确拒绝 disabled、所有者隐式绕过及其他角色；`bidder_compliance_preview_service` 已验证服务端矩阵统计与基点口径；前端已有独立 `/bidder` 路由、导航分组和 `RequireBidder` 门禁。

人员业绩不作为本包：P10D 的 `performance` 只是资质类别，没有稳定人员主体或业绩证据关联，直接扩展会造成错误关联和更大的隐私边界。P10G 因此仅从现有、已收敛的技术标响应矩阵派生五项统计，不新增人员或财务数据。

固定行为以 `docs/p10g-bidder-project-compliance-contract.md` 为准。未写入契约的项目详情、矩阵原文、版本、结果跟踪、写操作或权限放宽均不在范围内。

## 任务 1：后端受限实现（交给 Grok）

允许改动：

- `backend/app/api/schemas.py`；
- `backend/app/api/bidder.py`；
- 新建 `backend/app/services/bidder_project_compliance_service.py`；
- 新建 `backend/tests/test_bidder_project_compliance.py`；
- 仅在确有必要时最小扩展既有 P10E 测试，禁止改变 P10E 既有响应语义。

实现要求：

1. 严格复用 `require_bidder`，不得修改认证中间件、`get_workspace_id`、`require_bidder` 或已有 P10E/P10F 服务语义。
2. 选择器直接按当前工作空间和 `kind=technical` 查询，并且只投影 `id/name`；不得调用或暴露 `/api/projects*` 的完整响应。
3. 单项目先校验当前空间技术标归属，再读取已收敛矩阵并复用 P10E 的计数口径；`empty` 为 `200`，跨空间/不存在/商务标为同一固定 404。
4. 新 schema 必须字段白名单；详情禁止返回项目字段、矩阵行、来源、章节、大纲、人员、团队、文件、财务或任意未列字段。两条成功响应均 `no-store`。
5. 选择器不审计；单项目成功读仅写固定 action/target，且审计记录中不得出现项目 ID/名称、计数或矩阵内容。
6. 不新增表、迁移、任务、缓存、外部网络、写接口或 CSRF 分支；每个新增或大改代码文件必须具备「模块/用途/对接/二次开发」中文四字段注释。

Grok 先写失败测试，再实现至通过；不得提交、推送或修改文档。Codex 将独立审查 SQL 作用域、响应投影、路由顺序、角色矩阵、计数口径、审计和 P10E 非回归。

## 任务 2：前端受限实现（须在任务 1 验收后再下发）

预期白名单：`router.tsx`、`AppShell.tsx`、新增 `frontend/src/features/bidder-project-compliance/**`、新增 P10G E2E 与 `package.json` 目标命令。允许复用认证能力与 `RequireBidder`，不得改共享项目存储、编辑器状态、P10E 匿名页的数据请求语义、财务/人力模块或浏览器存储策略。

体验约束：

1. `/bidder/project-compliance` 仅 strict `bidder` 可挂载；初始只请求项目选择器，未选择项目不得请求详情或 `/bidder/compliance-preview`。
2. 下拉项仅展示项目名称；选择后才请求单项目统计，ready/empty 均不显示项目详情、矩阵原文或推导结论。
3. 项目切换时清空旧数据并拒绝过时响应；错误固定中文脱敏，不回显后端 detail、路径参数或项目 ID。
4. 现有 `/bidder` 导航高亮必须收紧为精确匹配，新增「项目合规」只匹配 `/bidder/project-compliance`；不得因为 `matchPrefix=/bidder` 让 P10E 匿名页错误高亮或挂载。
5. 不得请求 `/projects*`、`/editor-state`、`/hr/*`、`/finance/*`、文件接口或外网；不得写入浏览器存储或 URL 参数。

## 独立验收与提交顺序

1. Codex 对后端差异执行白名单审查、`git diff --check`、P10G 定向测试、P10E 回归和后端全量 `pytest -q`；通过后中文提交并推送后端实现。
2. Codex 对前端差异执行白名单与网络边界审查，独立运行 `npm run lint`、`npm run build`、P10G E2E、P10E/认证/技术标相关 E2E 与前端全量 E2E。
3. 仅在完整独立验收后，由 Codex 更新交接、路线图和联调清单，中文提交并推送 `collab/grok-code-codex-review`。

## 明确非目标

不做项目详情、项目状态/步骤、矩阵行/来源/章节/大纲、项目或工作空间级写入、审核/审批、评审结论、废标判定、导出、文件、人员或团队信息、财务、版本、结果跟踪、历史快照、跨空间/跨项目搜索、自动刷新、浏览器存储或外网。后续若要引入任一项，必须另行冻结数据来源、访问主体、审计、保留与展示投影契约。
