<!--
模块：V1-E 导出前最新编辑态落盘契约
用途：保证技术标与商务标创建 Word 导出任务前，最新浏览器编辑态已按既有 CAS 保存链写入服务端。
对接：两个 editor-state hooks、两个工作区导出页、export 任务与 Word 下载链。
二次开发：禁止把正文塞入 export payload、复制 PUT body、修改后端导出、绕过 CAS 或扩成版式/下载重构。
-->

# V1-E 导出前最新编辑态落盘契约

> **状态：冻结前草案。** 冻结提交后才允许在独立 worktree 写 failure-first 或生产代码。
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** `01de49d`；V1-D 已完成并推送。

## 1. 问题真值

Word 整章生成链已经存在：技术标遍历全部 `chapters`，商务标组装资格、清单、报价和承诺，后端只读取当前 workspace/project 的数据库 editor-state。

真正的 V1 正确性断点发生在浏览器与数据库之间：

- 技术标普通编辑在 800ms 防抖后才进入 `matrixSaveChainRef`；商务标为 600ms 后进入 `saveChainRef`；
- 两个导出按钮只受任务 `busy` 控制，直接创建 export 任务，不清 pending timer、不等待保存链；
- 保存已经在途时，export 与 PUT 也没有跨 hook 顺序保证；
- 保存失败或全状态 CAS 冲突时，按钮仍可能导出远端旧状态；
- 后端 export payload 不含浏览器正文，无法补偿这段竞态。

可判定失败序列为：用户编辑 → 防抖尚未触发 → 点击导出 → export 先读旧 DB 并生成 DOCX → PUT 才写入新内容。该问题由 Codex 提出，Grok B 只读确认=`msg_64f8eff1b2a84f3b821c7015ca5b0b66`，Grok A 独立审计=`msg_fa1aa5e7a9754e96b34d751169d3d3bc`，双方结论一致。

## 2. 本包目标

1. 两个 editor-state hook 提供同语义的 `flushPendingSaveForExport`；
2. 导出点击先通过该门，只有 `ready` 才允许创建 export 任务；
3. pending timer 必须转成既有保存链上的一次即时 PUT，已经在途的保存必须先完成；
4. 无本地待保存变化时不得为了导出额外 PUT 或产生无变化修订；
5. 保存失败、CAS 冲突、非法版本、项目切换或迟到结果均禁止导出旧状态；
6. 同一次导出准备与任务创建必须单飞，快速双击不得产生第二次 PUT/export；
7. 后端导出协议、整章内容、图片告警和下载行为保持不变。

## 3. Hook 协议

两个 hook 对外暴露同一结果：

```ts
type ExportSaveGateResult = "ready" | "blocked" | "failed";
```

### 3.1 无待保存变化

- 没有 pending timer 时先等待当前保存链快照；
- 当前项目/会话/写 epoch 仍有效、版本合法、未处于全状态或矩阵冲突、最近保存结果可用时返回 `ready`；
- 不发新的 editor-state PUT。

### 3.2 pending timer

- 原子清除本项目 timer，并设为 `null`；
- 在既有 `matrixSaveChainRef` / `saveChainRef` 尾部只追加一次现有 `executeImmediateEditorStatePut`；
- 执行时读取最新 `stateRef` / `workspaceRef` 与最新 `stateVersionRef`，禁止提前捕获旧 body/version；
- PUT body 必须完全复用既有执行器，不得复制第二套字段构造；
- `ok` 且项目/会话/epoch 未变化时返回 `ready`。

### 3.3 在途保存与结果

- 自动保存链必须留下可供导出门判断的最近执行状态；切项目/重新水合时重置为安全初态；
- 导出门必须等待调用时的保存链快照，不能只看 React `saveError`；
- `full_conflict`、`matrix_conflict`、`blocked`、`invalid_version`、`error` 或 `stale` 均不得创建 export 任务；
- 冲突继续使用既有冲突 UI；普通保存失败继续使用既有固定 `saveError`，不得展示服务端 detail、正文、版本或 ID；
- 不自动重试、不强制覆盖、不静默回退到远端旧状态。

## 4. 页面协议

1. 技术标与商务标页面分别维护项目绑定的导出操作令牌/准备状态；同步入口先占位，快速双击只接受第一次。
2. 准备期间按钮禁用并显示固定中文进行态；它与现有任务 `busy` 共同决定禁用。
3. `await flushPendingSaveForExport()` 只有返回 `ready` 才调用现有 `runTask("export")` / `runBizTask("export", {mode:"business"})`。
4. `blocked` / `failed` 不创建任务、不下载、不写图片告警；错误展示复用 hook 既有保守文案。
5. 项目切换、页面卸载或操作令牌失效后，旧回调不得为新项目创建任务、设置告警、清理新令牌或下载。
6. export 成功后的 `imageWarnings` 归一化、代次隔离和现有 `window.open` 下载保持冻结；弹窗拦截与人读文件名另包处理。

## 5. 严格请求顺序

有待保存编辑时：

```text
用户点击导出
  -> 既有 editor-state PUT（最新完整 body + expectedStateVersion）
  -> PUT 200 且版本被当前会话接受
  -> POST /projects/{id}/tasks  type=export
  -> 既有任务完成与下载
```

PUT 未完成时 export POST 必须为 0；PUT 409、普通失败、非法成功体或项目切换后 export POST 仍必须为 0。无待保存变化时允许直接 export，editor-state PUT 必须为 0。

## 6. 严格文件白名单

生产：

1. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`；
2. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`；
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`；
4. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`。

测试：

5. 新增 `frontend/e2e/export-latest-editor-state.spec.ts`。

禁止修改 `useProjectPipeline.ts`、共享 API/auth/router、后端、Schema、数据库、export service/route、模板、图片协议、依赖、配置或既有测试。若证据要求扩围，必须先 question、双方确认并修订冻结文档。

## 7. failure-first 与反假绿矩阵

新 E2E 必须使用真实技术标/商务标页面和编辑控件；允许对本机请求做可控路由同步，但不得读前端源码、调用 hook 私有函数或用固定 sleep。

1. 技术标：写入唯一新章节锚点后立即进入导出并点击；拦住 PUT 时 export POST 精确 0，PUT body 含新锚点且不含旧正文；释放合法 200 后 export 精确 1。
2. 商务标：至少修改资格响应或报价备注唯一锚点，锁定相同 `PUT < export` 顺序与精确 business payload。
3. 已在途自动保存：等待 PUT 请求已到达后点击导出；PUT 未释放时 export 为 0，释放后为 1。
4. 无待保存变化：点击导出时 editor-state PUT 为 0，export 为 1，防止无变化修订。
5. PUT 409 全状态冲突、矩阵冲突、普通 HTTP/网络失败、非法成功版本：export 均为 0，并出现既有固定安全状态。
6. 快速双击：一次保存准备、一次 export；不得依赖 React disabled 的异步刷新假装单飞。
7. A 项目保存挂起后切换 B：A 的迟到完成不得在 B 创建 export、告警或下载。
8. 扫描测试源禁止 `waitForTimeout`、`setTimeout`、`sleep`、宽松 `or`、`skip/xfail`、真实外网、浏览器存储或源码读取；请求计数必须按项目、method、path、body 精确归属。

failure-first 预期当前生产至少在技术/商务 pending edit 顺序用例失败：export POST 会先出现。实际数字必须如实报告，不得改断言凑红绿。

## 8. 验收

严格串行：

```powershell
cd C:\Users\Administrator\biaoshu-v1e-export-flush-impl\frontend
npx playwright test e2e/export-latest-editor-state.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-image-warnings.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
git -C .. diff --check
```

Codex 根据新专项覆盖决定是否完整复跑两个 truth 文件；禁止并发 Playwright/pytest、整仓 E2E、后端全量、真实业务数据或外网。

## 9. 非目标与下一步

本包不实现 DOCX 新版式、`structure`、Markdown inline、Blob 下载、人读文件名、弹窗拦截修复、导出历史、后端快照、任务 payload 正文、OCR、V2 协作或 V3 部署。完成后再按证据评估稳健下载和多章内容质量门。
