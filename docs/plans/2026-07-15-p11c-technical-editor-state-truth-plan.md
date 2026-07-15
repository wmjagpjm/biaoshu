<!--
模块：P11C 技术标编辑态真实数据收口实施计划
用途：把技术标 editor-state 服务端权威、required 保存和生产演示入口清理拆成七文件纯前端实现包。
对接：docs/p11c-technical-editor-state-truth-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：Grok 只改白名单文件并自测；Codex 独立审查、验收、提交和推送；后续不得扩大本包边界。
-->

# P11C 技术标编辑态真实数据收口实施计划

> **状态**：已完成、独立验收并推送。计划/契约=`24b7ba8`，安全细化=`c5b3eec`，前端实现=`1441509`。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收结果**：后端未改，沿用串行全量 487 passed；前端 lint/build 通过，P11C 18 passed，Chromium headless 单 worker 串行全量 E2E 184 passed。
> **执行结果**：计划提交并推送 → Grok 实现/自测 → Codex 两轮退回返修 → 独立验收 → 中文文档闭环。

## 1. 精确前端文件白名单

仅允许修改或新增：

1. `frontend/package.json`
2. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
4. `frontend/src/features/technical-plan/components/FactsEditor.tsx`
5. `frontend/src/features/technical-plan/components/OutlineStepWorkspace.tsx`
6. `frontend/src/features/technical-plan/components/ChapterEditor.tsx`
7. `frontend/e2e/technical-editor-state-truth.spec.ts`（新建）

不得修改后端、共享 `api.ts`、`projectStore`、responseMatrix.ts/面板、ContentFuseDialog、useProjectPipeline、useProjectGuidance、mock.ts、路由/AppShell、CSS、Playwright 配置、依赖或其他 E2E。若现有公开 API 无法满足，必须先发 `question`，不得自行扩白名单。

## 2. Hook 实现顺序

1. 把 `defaultState/loadLocal/saveLocal/fromApi(..., fallback)` 收口为纯空 `createEmptyEditors` 与只消费远端的 `fromApi(data)`；移除 mock import、storageKey 和所有本地读写。
2. 新增固定常量 `TECHNICAL_EDITOR_LOAD_ERROR` / `TECHNICAL_EDITOR_SAVE_ERROR`，以及 `loading/loadError/saveError/apiReady`。切项目立即清空状态、错误、冲突/base/版本/选择与定时器。
3. 初始 effect 用当前项目会话执行唯一 GET；成功完整水合、设置 version/base/apiReady，失败保持空并设置固定 loadError。不得用 `hasRemote/updatedAt` 判定是否相信服务端。
4. 保留 `reloadFromApi(): Promise<boolean>` 兼容 M3-D，并允许页面普通任务选择 blocking 刷新；所有路径捕获 projectId/session。失败设置 loadError、禁止保存并返回 false，不抛原文；M3-D 对话框打开期间由页面暂不替换为失败卡，关闭后显示。
5. 保存 effect 只在 apiReady 且当前会话有效时运行。移除 localStorage；非 409 失败设固定 saveError，成功清错；409 保持现有 base/merge/conflict 行为。
6. 普通 PUT 与合并 PUT 复用 Hook 内最小同源请求函数：从 `getCsrfToken()` 读内存值，存在时加 `X-CSRF-Token`，始终 `credentials: same-origin`。不得修改共享客户端或记录 Token。
7. `reloadFromApi`、普通 PUT、409 解析、合并 PUT 的 success/catch/finally 都加当前会话校验；409 只读取类型收敛后的远端矩阵/version，冲突提示固定中文且不采用 `detail.message`；移除合并成功后的 `saveLocal` 和 `persistSource`。
8. 删除 `fillDemoAnalysis/extractDemoFacts` 及导出成员。其余大纲/章节/事实/矩阵编辑算法不改。

## 3. 页面与组件

1. 技术标页面始终以路由 `projectId` 初始化 Hook/管线/guidance；项目详情结果保存为 `{requestProjectId, project}`，仅匹配当前路由时可渲染，避免 A→B 首帧复用旧对象。
2. 渲染顺序：项目或 editor loading → 项目不存在跳回列表 → `loadError && !contentFuseOpen` 固定失败卡 → 真实工作区。失败卡含重试/返回列表，不挂编辑控件。
3. 普通 parse/analyze/outline/chapters/chapter 成功后执行 blocking `reloadFromApi`；返回 false 时不显示“已刷新成功”提示。ContentFuseDialog 继续接无参数 `reloadFromApi`，保证 M3-D 自己判断 boolean。
4. 页头移除「编辑：本地/后端」，真实工作区只标明服务端编辑态；保存失败显示固定中文并带稳定 testid。
5. 页面删除分析演示按钮；FactsEditor 删除演示抽取 prop/按钮；OutlineStepWorkspace 删除 DEMO_LOGS 和无处理器时的重置按钮，以真实状态替换；ChapterEditor 清理 mock 文案。

## 4. 新 E2E 设计

`technical-editor-state-truth.spec.ts` 使用受控路由桩与真实本机壳，必须至少包括：

1. 服务端真实 analysis/outline/chapters/facts/parsedMarkdown，旧 editor 键预置但忽略保值。
2. 无行 canonical 空响应：分析/大纲/事实/章节/解析均为空，不补 mock，不新增 editor 键。
3. GET 500、401、404：固定失败卡、零工作区、零 PUT；每次显式重试精确 +1 GET，成功后挂载。
4. 加载延迟期间不渲染旧项目、mock 或编辑控件。
5. 编辑分析/大纲/事实/章节后 800 ms 防抖 PUT 的次数、路径和 body 精确；parsedMarkdown 不被普通编辑误写。
6. required 登录场景：登录响应内存 CSRF 精确出现在普通 PUT 与合并 PUT 请求头；会话 Cookie 由同源浏览器携带，Token/正文不落存储。
7. PUT 500/401/403：固定保存失败、SECRET/detail/code/路径/ID 不泄漏；再次编辑新增一次 PUT，成功清错。
8. 409 仍出现固定中文矩阵冲突且不出现通用保存错误，服务端 SECRET/detail.message 不展示；应用合并仍仅两个键，无自动循环。
9. 普通任务成功而 editor GET 失败：任务只执行一次，旧内容被失败卡遮蔽，重试恢复。
10. M3-D reload 失败：对话框既有业务完成提示仍可见、业务 POST/consume 不重复；关闭后出现 P11C 失败卡。
11. SPA A→B：A 项目对象、初始化 GET、任务后 reload、普通 PUT 成功/失败/409 均不得污染 B。
12. 「填入演示数据」「从招标/知识库抽取」「恢复示例目录」、mock 片段和固定伪日志均不存在。
13. 精确 method+路径白名单；未知 API 与外网主动探针可观测阻断；local/session/IndexedDB/Cookie/clipboard/console 精确收敛。

禁止 `test.skip`、条件跳过、`or True`、宽泛 `startsWith('/api/projects')` 放行、吞异常、固定 `waitForTimeout` 作为完成证据或只断言非空。存储枚举必须用 `key(i) ?? ""`，探针安装失败必须使测试失败。

## 5. Codex 审查重点

- 是否只把本地键改名、迁移或延后写，而不是完全忽略并保值；
- 服务端全空是否仍经 `length` 或 `|| fallback` 补 mock；
- loading 是否在 effect 前存在 A→B 首帧闪旧项目；
- reloadFromApi 是否只保护初始 GET，却遗漏任务迟到和 finally；
- required PUT 是否真的带内存 CSRF，合并 PUT 是否也覆盖；
- 409 是否被通用 saveError 吞掉，或为修 P11C 破坏 base/三方合并/二次 409；
- M3-D 对话框是否因 blocking loading/loadError 提前卸载，导致业务成功提示和防二次提交丢失；
- 演示按钮是否仅改文案、仍调用假数据；固定大纲日志是否仍冒充真实任务；
- E2E 是否主动证明旧键原值、空态、网络白名单、存储/console/CSRF 与会话迟到，而非只做可见性冒烟。

## 6. 独立验收

Grok 只发送 `review_request`，报告原任务 ID、精确七文件、失败先测、真实/空/失败/重试/required/409/M3-D/A→B/演示入口/网络存储证据与风险；不得 git add、commit 或 push。

Codex 审查通过后依次串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:technical-editor-state-truth -- --workers=1
npm run test:e2e:business-editor-state-truth -- --workers=1
npm run test:e2e:core-project-data-truth -- --workers=1
npm run test:e2e:auth-rbac -- --workers=1
npm run test:e2e:parse-strategy -- --workers=1
npm run test:e2e:matrix -- --workers=1
npm run test:e2e:fuse-apply -- --workers=1
npm run test:e2e:fuse-persistent-recovery -- --workers=1
npm run test:e2e:templates -- --workers=1
npm run test:e2e -- --workers=1
git diff --check
```

所有 PowerShell 与 Grok 子进程必须后台静默，Playwright 只用 Chromium headless、单 worker、逐条串行，不弹终端、浏览器或前台应用。后端未改，不重复把前端回归冒充后端新验收；沿用 487 passed 基线。

## 7. 实施、审查与验收闭环

1. Grok 按七文件白名单完成首版后，Codex 退回缺失的 required 真实登录 Cookie/CSRF、M3-D、合并 PUT、401/403、A→B、存储/剪贴板/IndexedDB 与精确网络白名单证据。
2. 第二轮审查确认登录场景仍伪造 `/auth/me`，且跨项目共用保存 Promise 链会让 A 的挂起 PUT 阻塞 B；再次退回后，Grok 改为登录页真实 `POST /api/auth/login` + HttpOnly Cookie + 内存 CSRF，并在项目切换时重置保存链。
3. 最终实现严格保持七文件边界：技术标编辑态只认服务端 GET/PUT；合法空态不补 mock；旧键忽略保值；加载/保存失败固定脱敏；普通/合并 PUT 均使用同源凭据与内存 CSRF；409、响应矩阵与 M3-D 兼容；A 的迟到和挂起保存不污染或阻塞 B；生产演示入口已移除。
4. Codex 独立验收：lint 通过；build 通过（仅既有大 chunk 提示）；P11C 18、P11B 11、P11A 10、认证/RBAC 11、解析策略 6、响应矩阵 8、M3-D 确认 6、M3-D 持久恢复 5、模板 1，全部 passed；单 worker 串行全量 E2E 184 passed；`git diff --check` 通过。
5. 解析策略第一次组合回归出现一次 5/6 的刷新时序波动，随后立即隔离重跑 6/6，Grok 自测同为 6/6，全量 184/184 再次覆盖通过；记录为一次性测试时序波动，不隐去也不把首轮失败冒充通过。
6. 前端实现提交 `1441509` 已推送到 `origin/collab/grok-code-codex-review`。本包不包含后端、通用版本历史、多人协作、guidance 历史服务端化或真实 MinerU/Docling 部署。
