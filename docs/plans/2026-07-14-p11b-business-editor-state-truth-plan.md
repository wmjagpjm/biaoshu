<!--
模块：P11B 商务标编辑态真实数据收口实施计划
用途：把商务标 workspace 服务端权威收口拆成四文件纯前端实现包。
对接：docs/p11b-business-editor-state-truth-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：Grok 只改白名单文件并自测；Codex 独立审查、验收、提交和推送。
-->

# P11B 商务标编辑态真实数据收口实施计划

> **状态**：计划已冻结，等待前端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 487 passed；前端 lint/build 通过、单 worker 串行全量 E2E 155 passed。
> **执行顺序**：计划提交并推送 → Grok 前端实现/自测 → Codex 审查/返修/独立验收 → 中文文档闭环。

## 1. 方案与不变量

P11B 复用现有项目详情与 editor-state API，不新增后端。工作区状态从 `createEmptyWorkspace(projectId)` 开始，但只有当前项目 GET 成功后才可显示和编辑；GET 空字段就是权威空态。旧 workspace localStorage 完全忽略且保持原值，AI 反馈 history 键继续独立保留。

不变量：服务端 editor-state 是商务内容唯一权威；API 失败不得由本地/演示内容恢复；只有成功水合的当前项目可 PUT；A 的迟到异步结果不得污染 B；错误固定中文且不泄露原文。

## 2. 精确前端文件白名单

仅允许修改或新增：

1. `frontend/package.json`
2. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
3. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
4. `frontend/e2e/business-editor-state-truth.spec.ts`（新建）

不得修改后端、共享 `api.ts`、P11A `projectStore`/页面/E2E、技术标 hooks、`business-bid/mock.ts`、解析策略、任务管线、认证/RBAC、Playwright 配置、依赖、CSS 或文档之外的其他代码。

## 3. 实现要求

1. Hook 删除 workspace `storageKey`、`isDemoProjectId`、`loadLocalWorkspace`、workspace `localStorage.setItem` 及 `createDemoWorkspace` import；初始与切项目仅用 `createEmptyWorkspace(projectId)`，不得读取/删除旧键。
2. 保留 `feedbackKey/loadHistory` 和 history 写入，文件顶注释明确它不是 editor-state 权威；不得扩大 history 数据范围或迁移。
3. 增加固定 `loadError`，`refreshFromApi` 返回 `Promise<boolean>`：成功水合真实字段、清错误、允许保存并返回 true；失败重置为空、禁止保存、设置固定加载错误并返回 false。不得抛后端原文。
4. 为项目会话增加代次与 active project 校验。首次 GET、显式重试、任务/修订后刷新、600 ms PUT 的成功/失败都必须在更新状态前确认仍属于当前项目；切项目清计时器、清错误、立即禁止旧会话保存。
5. PUT 失败只设置固定保存错误；成功清除。不得将错误原文写页面、console、history 或存储。刷新失败不得把业务任务改成失败，但必须阻止旧工作区继续作为最新内容显示。
6. 页面在项目存在时按 `wsLoading → loadError → 工作区` 渲染；固定失败卡含重试与返回列表。项目不存在优先保持「未找到项目」。保存错误固定显示，不拼接异常原文。
7. `package.json` 新增 `test:e2e:business-editor-state-truth`。新 E2E 必须使用受控路由与真实本机壳，禁止宽泛 `/api/projects` 前缀放行、吞异常、条件跳过、固定等待或只断言非空。

## 4. Codex 审查重点

- workspace 旧键是否仅改名、删除或迁移而非完全忽略并保值；
- `createDemoWorkspace`、`bb_*` 演示分支是否仍在生产 hook；
- GET 失败是否仍把空/旧 workspace 当成功挂载；
- 切项目是否只取消 GET，却遗漏 PUT/计时器/错误迟到；
- 任务成功后刷新失败是否谎报任务失败或继续显示旧内容；
- saveError 是否仍使用异常 `message`；
- E2E 是否精确区分允许的 feedback 键与禁止的 workspace 键族，是否主动证明未知项目 API/外网被阻断。

## 5. 独立验收

Grok 完成后只发送 `review_request`，报告原任务 ID、精确四文件、失败先测、真实/空/失败/重试/保存/A→B、网络/存储/console 证据、各测试和风险；不得 git add、commit 或 push。

Codex 审查通过后依次串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:business-editor-state-truth
npm run test:e2e:core-project-data-truth
npm run test:e2e:parse-strategy
npm run test:e2e:export-image-warnings
npm run test:e2e
git diff --check
```

所有 Playwright 命令必须 Chromium headless、单 worker、逐条串行。所有 PowerShell 与 Grok 子进程后台静默运行，不弹终端、浏览器或前台应用。
