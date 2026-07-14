<!--
模块：M3-C 融合写入单批撤销实施计划
用途：把会话内最近批次快照、漂移校验、状态恢复与浏览器验收拆成一个纯前端受限任务。
对接：docs/m3c-content-fuse-undo-contract.md；ContentFuseDialog；useTechnicalPlanEditors；content-fuse-apply E2E。
二次开发：Grok 只实现和自测，Codex 独立审查、验收、提交与推送；不得修改后端或扩展为历史系统。
-->

# M3-C 融合写入单批撤销实施计划

> **状态**：纯前端实现、Codex 独立审查与全量验收均已完成，等待本文档闭环提交。<br>
> **工作分支**：`collab/grok-code-codex-review`。<br>
> **提交链**：计划=`c63310f`；实现=`b8ff605`。<br>
> **验收基线**：M3-B/M3-C E2E 6 passed、M3-A E2E 1 passed、P10H E2E 10 passed；前端 lint/build 通过，单 worker 串行全量 E2E 106 passed。Playwright 共用 SQLite 重置库，必须串行。

## 1. 目标与架构

在既有 M3-B 融合对话框内增加最近一次成功确认批次的单次撤销。对话框保存最小内存快照；撤销时以当前章节标题、正文和状态对照写入后快照，只有未漂移章节才恢复写入前正文与状态。恢复继续调用既有编辑器替换函数和串行防抖 PUT，不新增 API、表、依赖或持久化。

## 2. 单一实现任务与文件白名单

仅允许修改：

- `frontend/src/features/technical-plan/components/ContentFuseDialog.tsx`
- `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- `frontend/e2e/content-fuse-apply.spec.ts`

不得修改 `TechnicalPlanWorkspace.tsx`、`contentFuse.ts`、`types.ts`、CSS、`package.json`、共享 API、认证、其他 feature、Playwright 配置、后端、依赖、数据库、迁移、脚本或文档。若白名单不足，Grok 必须发 `question`，不得自行扩围。

## 3. TDD 实施步骤

### 任务 1：先补失败 E2E

1. 在既有 `content-fuse-apply.spec.ts` 复用本机 mock LLM、种子与条件轮询，新增“多章写入后撤销并刷新保持”的用例；种子应让至少一章写入前状态为 `pending`，验证正文、`status` 均恢复。
2. 新增“写入后手工改一章，撤销仅恢复未漂移章”的用例；断言固定汇总、漂移章内容保留、快照消费后按钮消失，刷新后仍正确。
3. 新增或合并验证关闭对话框再打开没有撤销入口；不得使用固定 sleep，不得访问外网，不得绕开真实编辑器状态 PUT。
4. 先运行 `npm run test:e2e:fuse-apply`，确认新增断言因缺少撤销功能失败，并在 `review_request` 报告失败点。

### 任务 2：恢复正文时同步恢复原状态

1. 把 `replaceChapterBody` 扩为向后兼容的可选第三参数，只允许传入明确的原 `ChapterContent.status`；未传时保持现有行为。
2. 有显式状态时恢复该状态，同时始终由正文重新派生 `preview` 和 `wordCount`；不得允许调用方写入标题、ID 或其他字段。
3. 现有修订预览与 M3-B 两参数调用不得改变行为。

### 任务 3：实现最近批次一次性撤销

1. 在对话框内定义最小快照类型；确认写入前记录每个目标章的原正文/状态/标题，按章节去重，并记录本批最终正文/状态和建议 ID。
2. 只有至少一条实际写入时才建立快照；新建议生成、对话框新会话或项目变化清空；下一成功批次覆盖上一批。
3. 增加“撤销本次写入”按钮。点击时逐章精确校验存在性、标题、正文、状态；匹配才以显式原状态调用替换函数，不匹配跳过。
4. 撤销后无条件消费快照，显示“已撤销 N 章，跳过 M 章”；只移除已恢复章关联的已写入建议 ID。不得新增网络函数、浏览器存储、计时器、全局变量或模块级缓存。
5. 更新本轮触达文件顶中文四字段与公开回调注释；删除“无专用撤销”的过时文案。

### 任务 4：自测与交接

依次串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run test:e2e:fuse-apply
npm run test:e2e:fuse
npm run lint
npm run build
```

完成后运行 `git diff --check`，只通过消息箱发送 `review_request`：报告精确文件、失败先测证据、最终结果、撤销漂移规则、网络/存储边界、风险和未做项。Grok 不得 `git add`、commit 或 push。

## 4. Codex 独立验收与提交

Codex 核对白名单与契约，重点审查：是否恢复原状态而非只恢复正文；是否在撤销前重新读取当前章节；手工漂移是否绝不覆盖；快照是否只在实例内且一次消费；多建议同章是否保留最初 before 与最终 after；原 M3-B 是否不回归。

随后 Codex 独立串行运行定向 M3-B/M3-C E2E、lint、build 和单 worker 全量 E2E。通过后形成独立中文实现提交并推送，再更新本计划、路线图、联调清单和 HANDOFF，形成独立中文文档闭环提交。

## 5. 实施审查与独立验收记录

1. Grok 严格只修改 3 个白名单文件。失败先测在确认写入后定位不到“撤销本次写入”按钮；最小实现后，最近批次快照仅保存在对话框实例 React state，按章节保存最早 before、最终 after 与建议 ID。
2. Codex 审查确认：撤销点击时重新读取当前章节，并同时校验存在性、标题、正文和状态；漂移章跳过且不覆盖。成功恢复正文与原状态，`preview`/`wordCount` 继续由编辑器派生；仅恢复章移除“已写入”标记，快照无条件一次消费。
3. 安全审查确认没有新增路由、鉴权、API、网络函数、浏览器持久化、模块级缓存、计时器、依赖、后端或敏感日志；React 仍以文本渲染，不新增 XSS 面。撤销只经既有串行防抖 editor-state PUT 保存。
4. Codex 独立运行 M3-B/M3-C E2E 6 passed、M3-A E2E 1 passed、P10H E2E 10 passed、lint/build 通过。首轮全量为 103/106，3 项失败截图均为纯白页，失败点分散在刷新/首次加载应用壳，相关定向均通过；随后完整单 worker 重跑 106/106 通过，以该完整结果覆盖首轮环境性失败。
5. Grok 未提交或推送；Codex 完成审查、独立验收、中文实现提交与协作分支推送。M3-C 仍不提供跨关闭、跨刷新、跨项目、跨设备的持久化历史或任意编辑撤销。
