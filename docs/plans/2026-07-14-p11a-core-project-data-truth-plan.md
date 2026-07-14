<!--
模块：P11A 核心项目真实数据收口实施计划
用途：把项目服务端权威收口拆成一个纯前端、可受限审查和独立 E2E 验收的实现包。
对接：docs/p11a-core-project-data-truth-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：Grok 只改白名单文件并自测；Codex 独立审查、验收、提交和推送。
-->

# P11A 核心项目真实数据收口实施计划

> **状态**：已完成、独立验收并推送。计划=`70a2dc7`，前端=`b0a86e4`。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 487 passed；前端 lint/build 通过、单 worker 串行全量 E2E 145 passed。
> **执行顺序**：计划提交并推送 → Grok 前端实现/自测 → Codex 审查/返修/独立验收 → 中文文档闭环。

## 1. 方案与不变量

P11A 选择“核心项目 API fail-closed”，不新增后端聚合接口，也不把演示数据改名为缓存。项目列表、详情和创建只认既有 `/api/projects*`；真实空数组保持空，任何失败都显式呈现且零本地项目写入。

不变量：服务端项目 ID 是后续文件、任务、editor-state 与权限的唯一锚点；创建失败绝不导航；列表失败绝不混入旧数据；不存在项目绝不由 mock 复活；旧 localStorage 项目键既不读取也不修改；错误固定中文且不泄露服务端原文。

## 2. 精确前端文件白名单

仅允许修改或新增：

1. `frontend/package.json`
2. `frontend/src/features/technical-plan/lib/projectStore.ts`
3. `frontend/src/features/technical-plan/pages/TechnicalPlanListPage.tsx`
4. `frontend/src/features/technical-plan/pages/TechnicalPlanNewPage.tsx`
5. `frontend/src/features/create/pages/CreatePage.tsx`
6. `frontend/src/features/business-bid/pages/BusinessBidPage.tsx`
7. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
8. `frontend/src/features/duplicate-check/pages/DuplicateCheckPage.tsx`
9. `frontend/src/features/rejection-check/pages/RejectionCheckPage.tsx`
10. `frontend/e2e/core-project-data-truth.spec.ts`（新建）

不得修改后端、共享 `api.ts`、认证/RBAC、router/AppShell、技术标/商务标 editor-state hooks、mock fixture 文件、知识库、Playwright 配置、依赖、CSS 或其他角色页面。

## 3. 实现要求

1. `projectStore` 移除生产路径的 `currentWorkspace/mockProjects`、`biaoshu.projects.v1`、同步本地 CRUD、mock 合并和 `VITE_USE_API_PROJECTS/VITE_MERGE_MOCK_PROJECTS` 分支。保留的公开异步函数必须中文注释并只走真实 API。
2. `listProjectsAsync` 对真实 `200 []` 返回空；失败不得返回旧项目。可使用明确结果态或固定内部错误，但所有调用方不得出现未处理 Promise；技术/商务列表、查重、废标均显示固定中文并保留空集合。
3. `getProjectAsync` 不得本地回退。技术标既有“不存在则回列表”语义可保持；商务标必须删除 `mockBusinessProjects` 复活分支。错误不得显示服务端 detail。
4. `createProjectAsync` 只 POST 一次并透传成功项目；失败抛给页面处理，不生成本地 ID。技术标新建页、创建方案页、商务标页各自增加在途禁用与固定错误；失败 URL、表单和项目数保持不变，允许再次显式重试。
5. 创建成功后才可按既有规则写 `biaoshu.pendingProjectFiles`，且 projectId 必须等于 POST 响应；失败前后该键精确不变。
6. UI 不显示「本地/演示兜底」「演示 mock」或后端原始异常；真实 API 来源可保留。不得因为 API 空数组展示 fixture。
7. 新 E2E 使用受控路由与真实本机壳，主动预置假 localStorage 项目并证明不渲染、不改值；主动探测未知 `/api` 和外网，禁止宽泛 `/api/projects` 前缀放行、吞异常、条件跳过、固定等待或只断言非空。

## 4. Codex 审查重点

- 是否仍有生产入口 import `mockProjects/mockTasks/mockBusinessProjects`；
- 是否仅把 fallback 改名却仍读取 localStorage；
- API 空态是否被当作错误或补演示数据；
- 创建失败是否仍通过 deprecated 本地函数或 catch 导航；
- 查重/废标是否产生未处理拒绝；
- 错误是否回显 detail/code/路径/ID；
- 旧项目存储键是否被清空、迁移或上传；
- 路由桩是否宽放未知项目 API，存储与 console 断言是否反假绿。

## 5. 独立验收

Grok 完成后只发送 `review_request`，报告原任务 ID、精确十文件、失败先测、各页面真值/空态/失败态、三条创建路径、直达演示 ID、项目选择器、网络/存储/console 证据、lint/build/E2E/diff-check；不得 git add、commit 或 push。

Codex 审查通过后依次串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:core-project-data-truth
npm run test:e2e:auth-rbac
npm run test:e2e:parse-strategy
npm run test:e2e:templates
npm run test:e2e
git diff --check
```

所有 Playwright 命令必须 Chromium headless、单 worker、逐条串行。所有 PowerShell 与 Grok 子进程后台静默运行，不弹终端、浏览器或前台应用。

## 6. 实施、审查与验收记录

1. 计划提交 `70a2dc7` 冻结契约、十文件白名单、错误/存储/网络边界和串行命令后推送协作分支。
2. Grok 通过任务 `msg_485bd9ca1a7d4e4e9b1ffaa94423145b` 完成十文件首版，不执行 git add/commit/push。生产实现移除 mock/localStorage 项目 CRUD 与环境开关语义，页面收敛真实列表/空态/失败态和三条创建失败路径。
3. Codex 首轮审查确认生产实现方向正确，但否决“只断言 v1 单键值”的存储测试假绿；通过任务 `msg_993109293d344f0caec8e227d9792bbd` 仅允许修改 `core-project-data-truth.spec.ts`，补齐项目元数据键族、完整失败快照、成功 pending 精确键值及 editor-state 精确允许项。
4. 返修回执 `msg_945df663cd8341cc86d74c17cb516fb0` 经独立复核通过；Codex 运行 lint/build、P11A 10、认证 11、解析策略 6、模板 1 以及全量 155，均为 Chromium headless、1 worker、串行；`git diff --check` 通过。
5. 前端实现由 Codex 提交为 `b0a86e4` 并推送；验收确认消息为 `msg_442349b0341a4dfeacc169c2a0df18e2`。本包未改后端、共享 API、editor-state hooks、mock fixture、知识库、角色权限、依赖或 Playwright 配置。
