<!--
模块：V1-G 任务成功后编辑态刷新围栏契约
用途：阻止旧项目任务迟到成功后触发编辑态重载、粘住新项目 loading 或污染提示与步进。
对接：技术标任务入口、商务标 runBizTask、两个 editor-state Hook、V1-G 专项 E2E。
二次开发：禁止修改 useProjectPipeline/I4/SSE 返回语义、后端任务协议、导出链或数据库。
-->

# V1-G 任务成功后编辑态刷新围栏契约

> **状态：已冻结方案，待 failure-first 与实现。**
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** `a9ff414c626fb59a64e2575f81a11cc06b795ab9`；V1-A 至 V1-F 已完成并推送。

## 1. 问题真值

`useProjectPipeline.runTask()` 已正确阻止旧项目回调写入 pipeline 的 `lastTask/busy/error`，但旧会话失效时仍会把已取得的真实任务对象返回给页面。这个返回语义被 I4、SSE、导出和现有调用方共同使用，本包不得修改。

页面当前只检查 `task.status === "success"`：

1. 技术标的 `parse/analyze/outline/chapters/chapter` 随后调用旧闭包的 `reloadFromApi({ blocking: true })`；
2. 商务标的 `parse/biz_qualify/biz_toc/biz_quote/biz_commit` 随后由 `runBizTask` 调用旧闭包的 `refreshFromApi()`，并继续更新项目步进；
3. 两个 Hook 都会在确认请求仍属当前会话之前同步 `setLoading(true)`；
4. A 任务运行时软切 B，B 初始 editor-state GET 已完成后再收到 A success，旧 A 调用会把 B 置为 loading；其 `finally` 又因项目/session 不匹配而不清零，B 永久卡住，需手工刷新页面。

现有 P11 A→B 用例只覆盖“任务已成功并已发出 reload GET 后再切项目”，未覆盖“任务 pending/running 时切项目，B 就绪后才释放 A success”。Grok A 只读确认=`msg_7c2ceeff812547b9a3f148f9fcfffce3`，Grok B 只读确认=`msg_3183e06e07914fcb838451e8dfac6119`；Codex 已独立逐行复核。

## 2. 方案裁定

1. **修改 `runTask`，把旧 success 改成 cancelled/异常：拒绝。** 这会伪造服务端终态并破坏 I4/SSE、导出与其它调用方契约。
2. **只在页面比较当前 projectId：不足。** 能挡 A→B，但 A→B→A 或同项目新任务代次仍可能接纳旧完成；Hook 也仍允许其它旧闭包先置 loading。
3. **重写全局 loading 所有权令牌：后置。** 可以覆盖更广的同项目并发加载，但本包没有证据需要重构所有初始 GET、手动重载与 silent 恢复，风险和回归面过大。
4. **页面项目/任务代次门 + Hook 入口会话早退：采用。** 页面负责所有 success 后副作用的业务所有权，Hook 在改写 timer/epoch/loading 或发 GET 前拒绝明显失效的闭包，形成最小双层保护。

## 3. 页面所有权门

### 3.1 共同规则

- 两页复用已有 `currentProjectIdRef`，并新增只在内存中的任务刷新 generation；每次项目切换和每次受控任务启动都必须推进 generation。
- 启动任务时捕获 `startedProjectId + taskGeneration`；在 `await runTask` 后、编辑态 GET 后以及任何后续项目请求后，都必须重新检查两者仍为当前值。
- 任一检查失败必须静默返回真实 task；零 editor-state GET、零 loading、零成功提示、零项目详情覆盖、零步进 PATCH，且不得把旧 success 改写为 failed/cancelled。
- generation 必须覆盖 A→B→A 和同项目异常双飞；不得只靠 React 按钮的异步 `disabled` 状态冒充单飞。

### 3.2 技术标

- `parse/analyze/outline/chapters/chapter` 必须共用同一受控“任务成功后重载”入口，避免五处复制后遗漏门禁。
- 只有 task success、项目与 generation 仍有效时，才允许一次 `reloadFromApi({ blocking: true })`。
- 只有重载返回 true 且门仍有效时，才允许写入对应 `taskTip`；知识库引用和章节标题只能来自当前任务闭包。
- `content_fuse`、`response_match` 和 export 已有独立所有权协议，不纳入该 helper，也不得借机重写。

### 3.3 商务标

- `runBizTask` 在入口捕获项目与 generation；适用于 `parse/biz_qualify/biz_toc/biz_quote/biz_commit`。
- success 后只有门仍有效才调用一次 `refreshFromApi()`；完成后再次校验，再执行既有 `STEP_BY_TASK` 项目更新。
- 项目更新必须使用 `startedProjectId`，并在每个 await 后复核门，再决定是否 `setProject` 或发 fallback `getProjectAsync`。
- 同项目 refresh 失败时保持既有 P11 语义：固定加载失败卡、任务仍为 success，既有步进处理不因本包擅自改义。
- export 已在 V1-F 明确绕开 `runBizTask` 并拥有独立门，不得并回本 helper。

## 4. Hook 入口早退

- `useTechnicalPlanEditors.reloadFromApi` 和 `useBusinessBidWorkspace.refreshFromApi` 在改写 `writeEpoch`、清 timer、`setLoading(true)` 或发 GET 前，必须验证闭包项目仍是当前活跃项目/session。
- 入口已失效时固定返回 false，且零 React 状态、零 timer/epoch、零网络副作用。
- 请求启动后才发生项目切换时，继续沿用既有响应门和 finally 语义；B 的初始 GET 负责 B 的 loading，不允许旧 A finally 清 B。
- `silent` 检查点恢复、初始项目水合、手动重载、冲突阻断和 P11 失败卡语义保持。

## 5. 严格文件白名单

生产：

1. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`；
2. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`；
3. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`；
4. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`。

测试：

5. 新增 `frontend/e2e/v1g-writer-task-soft-switch-hydration.spec.ts`。

禁止修改 `useProjectPipeline.ts`、任务/SSE/I4、后端、数据库、迁移、依赖、Playwright 配置、`package.json`、导出/下载、editor-state API、事件面板或其它测试。确需扩围必须先由 Codex 发 question，Grok 只读确认存在后再修订本契约并授权。

## 6. failure-first 与反假绿矩阵

新专项使用真实技术/商务工作区与受控 route/SSE，不调用私有函数、不读源码、不访问外网或真实数据：

1. 技术 `parse`：A pending/running 时软切 B；B 就绪后释放 A success，A/B editor-state GET 增量均为 0，B 正文可编辑且零 sticky loading/旧提示。
2. 技术 `analyze`：同构，真实点击 AI 招标分析并精确断言 task type。
3. 技术 `outline`：真实点击 AI 生成大纲，不得用 analyze helper 调用冒充。
4. 技术 `chapters`：真实点击生成全部空章节，精确断言 `onlyEmpty` 与 task type。
5. 技术 `chapter`：真实选章并点击 AI 生成本章，精确断言 chapterId；与 chapters 独立覆盖。
6. 商务 `biz_qualify`：A→B 释放旧 success 后零额外 editor-state GET、零旧项目步进/项目对象污染，B 商务正文与资格内容保持权威值。
7. 同项目技术 analyze success：任务完成后 editor-state GET 精确增加 1，水合任务后权威正文并显示当前提示。
8. 同项目商务 biz_qualify success：editor-state GET 精确增加 1，水合当前正文并保持既有步进。

可控终态必须使用 pending/running task + per-task SSE 或等价可证明的 HoldGate；先证明 B 初始 GET 已完成且编辑控件可用，再释放 A success。禁止用固定 `waitForTimeout/setTimeout/sleep` 作完成证据、宽泛非零计数、`skip/fixme/only`、源码字符串扫描或条件分支假绿。

生产未改时预期前六项真实失败、后两项通过，参考目标为 **6 failed / 2 passed**；实际数字必须如实记录。首红优先落在释放 A success 后出现额外 A editor-state GET，其次为 B loading 粘住，不得以登录、端口、fixture、SSE 连接或选择器错误冒充红测。

## 7. 分级验收

严格串行，单 worker、零重试：

```powershell
cd C:\Users\Administrator\biaoshu-v1g-writer-refresh-impl\frontend
npx playwright test e2e/v1g-writer-task-soft-switch-hydration.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npx playwright test e2e/project-task-status-reconciliation.spec.ts --workers=1 --retries=0
npx playwright test e2e/project-task-event-update.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-event-update.spec.ts --workers=1 --retries=0
npm run lint
npm run build
git -C .. diff --check
```

测试 worktree 使用自身相对路径下的 `backend/data/biaoshu-e2e.db`，与主仓数据库物理隔离；只允许在确认 8010/5174 无监听后串行使用默认 E2E 端口。禁止并发 Playwright/pytest、整仓 318 E2E、后端全量、真实业务库/uploads/密钥或外网。

## 8. 非目标

本包不做后端任务取消、后台完成通知、I4 自动正文刷新、任务列表、多人锁、协同光标、导出、解析器安装、多章内容质量、V2 团队协作或 V3 SaaS 部署。V1-G 只关闭“当前页面主动发起任务后的 success 副作用所有权”。
