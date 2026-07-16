<!--
模块：P12D-B 修订与当前版本对比前端实施计划
用途：把严格 parser、共享面板状态机、迟到隔离和串行 E2E 拆成 Grok 可执行步骤。
对接：docs/p12d-revision-comparison-frontend-contract.md、P12D-A comparison API、P12C-C3 前端。
二次开发：Grok 只实现三文件白名单并发 review_request；不得提交、推送或扩成正文 diff。
-->

# P12D-B 修订与当前版本对比前端实施计划

> **给 Grok：** REQUIRED SUB-SKILL：按 `executing-plans` 逐项执行；先 failure-first，再最小实现。不得提交或推送。

**目标：** 在技术标和商务标共用修订历史面板中增加按需“与当前对比”，严格解析 P12D-A 响应并隔离所有迟到结果。

**架构：** 只扩展既有 API 封装、共享面板和同一 E2E。API 层把未知响应压缩成固定比较类型；面板用独立比较代次与现有详情代次交叉作废，保证摘要、比较、恢复只保留一个当前意图；Playwright route 探针以 arrived/complete 双日志证明真实竞态。

**技术栈：** React 19、TypeScript 6、既有 `apiFetch`、Playwright 1.61、oxlint、Vite。

---

## 1. 文件与总规则

只允许：

- 修改 `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
- 修改 `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
- 修改 `frontend/e2e/editor-state-revision-history.spec.ts`

三个文件顶部中文“模块 / 用途 / 对接 / 二次开发”必须更新到 P12D-B。禁止后端、workspace/hook、样式、依赖、配置、其他测试、文档和 Git 暂存/提交/推送。不得创建新文件、模块全局缓存、浏览器存储、URL 状态、轮询、自动比较或正文 diff。

## 2. 任务 1：先扩 E2E 探针与写有效红测

### 步骤 1：增加 comparison 探针状态

在 `ProbeState` 增加 comparison 请求/完成日志、按项目/修订 gate、响应 override 和固定响应表。新增正则必须只匹配：

```text
GET /api/projects/{projectId}/editor-state-revisions/{revisionId}/comparison
```

route 必须在通用 detail 处理前明确分支；记录 method、path 和 `postData()`，拒绝未知项目、非 GET、body 或查询参数。gate 前记 arrived，`await json` 后记 complete；不能用 arrived 冒充完成。

### 步骤 2：新增三个精确测试

保持既有 21 个测试不改名、不放宽，新增且仅新增：

1. 技术标：默认 comparison=0；按需差异/一致结果、13 键中文标签、两侧六项摘要、严格非法 shape 固定失败、summary/compare/restore 互斥与零泄漏。
2. 技术标：A0 comparison 挂起→A1 成功→A0 完成不覆盖；项目 A 挂起→切 B→A 完成不污染；折叠/刷新/摘要/恢复会作废旧 comparison。
3. 商务标：同一共享入口成功，comparison 精确 1，正文不变，零 detail/restore/editor-state GET/PUT/外网旁路。

禁止固定 sleep、`.or(...)`、宽泛 2xx/`>=1`、只看按钮或只看 arrived。每个成功断言必须检查固定 testid、精确中文、请求计数和 complete；非法响应必须证明旧成功结果消失且原始 token 不进 HTML/console。

### 步骤 3：运行 failure-first

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
```

预期：**3 failed / 21 passed**；三个新增测试只因比较按钮/请求/结果尚未实现而失败。若 fixture、route、TypeScript、依赖或浏览器失败，先只修测试，生产仍不得修改，直到获得有效红测。

## 3. 任务 2：实现严格 comparison parser

### 步骤 1：增加固定类型和键集

在 `editorStateRevisionApi.ts` 增加：

- 13 键字面量类型和固定顺序；
- 13 个固定中文标签映射；
- `EditorStateRevisionComparison`：仅 `sameState/changedFields/currentSummary/targetSummary`；
- comparison summary 复用 `EditorStateRevisionSummary` 类型，但使用独立的严格响应解析，不从 snapshot 重新计算。

### 步骤 2：实现精确解析

顶层精确四键；`changedFields` 逐项校验白名单、无重复、严格递增顺序，并验证 `sameState === (length === 0)`。两侧摘要精确六键，五个计数均调用既有非负安全整数规则，解析正文标志必须是布尔。任何额外/缺失/未知/重复/乱序/类型或一致性错误统一抛内部固定错误，不携带值。

### 步骤 3：实现 GET 封装

新增 `getEditorStateRevisionComparison(projectId, revisionId)`：先复用 `isValidRevisionId`，再以两个 `encodeURIComponent` 构造 `/comparison` 路径；`apiFetch<unknown>` 无第二参数，因此固定 GET、无 body。只返回严格解析后的比较对象。

### 步骤 4：静态验证

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
```

预期：均成功；E2E 新测试仍因面板入口未实现保持红色。

## 4. 任务 3：实现共享面板比较状态机

### 步骤 1：增加比较状态与代次

在 `EditorStateRevisionPanel.tsx` 增加当前 comparison revision、结果、错误、loading ID 和 `comparisonGenRef`。固定失败文案为 `修订差异加载失败，请稍后重试`。项目切换、卸载、折叠、列表刷新、恢复开始及恢复后的列表重载必须递增比较代次并清空所有比较状态。

### 步骤 2：交叉作废摘要、比较与恢复确认

- `handleComparisonClick` 首先递增现有详情代次、清摘要/详情错误/恢复确认；再次点击同一项则关闭并结束。
- 新 comparison 请求捕获 `myGen` 和当前项目会话；`try/catch/finally` 写状态前同时验证 mounted、session、comparison generation。
- `handleSummaryClick`、`handleRestoreClick`、确认恢复和列表重载都递增比较代次并清比较；比较请求不得触发 restore、editor-state 读写或 checkpoint。
- 不以 AbortController 代替代次校验；允许用户在旧请求挂起时点击另一项。

### 步骤 3：渲染严格可见信息

在每条记录的按钮区加入 `editor-state-revision-compare-{index}`。结果使用固定 testid 显示一致性、中文差异标签、“当前版本/所选修订”两侧六项摘要；不得渲染原始键。比较按钮不受只读 `disabled` 控制，但恢复执行期间禁用；loading 只绑定当前目标。

### 步骤 4：运行专项至全绿

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
```

预期：**24 passed**。

## 5. 任务 4：Grok 串行自测与交接

从 `frontend` 目录逐条运行，禁止并行：

```powershell
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
```

再从仓库根运行 `git diff --check`，核对暂存区为空且 `git status --short` 恰好三文件白名单。Grok 不运行前端全量（留给 Codex），不安装依赖、不联网、不改文档、不提交或推送。

最终只通过消息箱发送 `review_request`，必须包含：taskId、有效 failure-first 精确计数和首个业务失败、三文件清单、逐条最终命令与计数、严格 parser 分支、中文字段标签、状态互斥、arrived/complete 迟到证据、零泄漏/零旁路、风险和未做项。

## 6. Codex 独立验收

Codex 先逐行审查 parser 的键集/顺序/一致性，面板所有清理入口和旧 `finally`，以及 E2E 是否真实等待 gate completion。随后逐条独立运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
npx playwright test --workers=1 --retries=0
```

在只新增 3 个测试且既有用例不删除的前提下，预期修订历史 **24 passed**、前端全量 **287 passed**；精确计数以实际收集为准。再执行 `git diff --check`、三文件白名单、暂存区为空和工作区产物检查。全部通过后才由 Codex 中文提交、推送并更新契约、计划、主交接、路线图和联调清单。

## 7. 后续边界

P12D-B 只把 P12D-A 摘要比较安全呈现给技术/商务标书制作者。正文 diff、任意历史两两比较、删除、搜索、分页、完整历史/保留策略、导出、分享和多人协作继续独立排期；不得从比较结果自动触发恢复或生成结论。
