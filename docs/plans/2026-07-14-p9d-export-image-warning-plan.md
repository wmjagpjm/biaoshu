<!--
模块：P9D 导出图片失效引用浏览器提示实施计划
用途：把共享告警归一化/展示、双导出页接线和浏览器验收拆成一个纯前端受限任务。
对接：docs/p9d-export-image-warning-contract.md；技术标/商务标导出页；export-image-warnings E2E。
二次开发：Grok 只实现和自测，Codex 独立审查、验收、提交与推送；不得修改后端图片协议或导出逻辑。
-->

# P9D 导出图片失效引用浏览器提示实施计划

> **状态**：P9D 已完成、独立验收并推送。
> **工作分支**：`collab/grok-code-codex-review`。  
> **提交链**：计划=`4925a51`；实现=`e5adad7`。
> **最终验收**：后端项目图片专项 14 passed；P9D E2E 4 passed；前端 lint/build 通过、单 worker 串行全量 E2E 110 passed。

## 1. 目标与架构

复用后端现有成功任务 `result.imageWarnings`，让技术标和商务标导出页在不阻断下载的前提下显示图片降级原因。新增一个共享无状态组件，同文件导出纯归一化函数；两个页面只负责导出前清空、成功后写入当前项目内存状态和继续原下载路径。

不新增后端、API、任务、存储、依赖或 CSS 文件。

## 2. 单一实现任务与文件白名单

仅允许修改：

- `frontend/src/shared/components/ExportImageWarnings.tsx`（新）
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
- `frontend/e2e/export-image-warnings.spec.ts`（新）
- `frontend/package.json`（仅新增 `test:e2e:export-image-warnings`）

不得修改后端、数据库、迁移、`useProjectPipeline`、共享 API、图片上传、编辑器、模板、认证、路由、Playwright 配置、CSS、依赖锁文件或文档。若白名单不足，Grok 必须发送 `question`，不得自行扩围。

## 3. TDD 实施步骤

### 任务 1：先补失败 E2E

1. 通过本机 API 创建技术标项目并写入含无效 `biaoshu-image://` 独占行的章节正文；打开导出页，替换 `window.open` 为仅记录本机下载 URL 的测试桩。
2. 触发真实 export 任务，断言任务成功、页面出现后端图片无效原因，同时下载仍被调用且地址只指向本机 API。
3. 用商务标项目和 `parsedMarkdown` 重复同一主路径，证明商务导出也消费同一后端结果。
4. 增加受控路由桩覆盖非法/超量/超长 `imageWarnings` 结构、文本转义与下一次无告警清空；不得访问外网或使用固定 sleep。
5. 先运行定向命令，确认新增主断言因页面尚未显示告警而失败，并在 `review_request` 报告失败点。

### 任务 2：共享归一化与展示

1. 新建 `ExportImageWarnings.tsx`，文件顶和公开函数补齐中文“模块/用途/对接/二次开发”说明。
2. 导出纯函数，仅接受非空字符串数组项，最多 20 条、每条最多 240 个 Unicode 字符；不得解析 HTML、Markdown、URL、文件 ID 或路径。
3. 导出无状态展示组件：无告警返回空；有告警显示固定标题、条数、继续下载说明和文本列表。只用 React 文本节点，不使用 HTML 注入或可点击链接。

### 任务 3：双页面接线

1. 技术标和商务标页面分别保存当前导出告警数组；项目 ID 变化与每次导出开始前清空。
2. 任务成功后从 `result` 归一化告警并更新页面，随后无条件执行原成功下载逻辑；告警不得改变 `storedName`、`downloadPath` 或成功状态。
3. 失败、取消或非法结果保持空告警；不得新增请求、存储、日志、计时器或模块级缓存。
4. 两个页面复用同一组件，不复制归一化规则；保持既有解析、任务提示和导出按钮行为。

### 任务 4：自测与交接

依次串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run test:e2e:export-image-warnings
npm run lint
npm run build
```

完成后运行 `git diff --check`，只通过消息箱发送 `review_request`：报告精确文件、失败先测证据、最终结果、双页面下载未阻断证据、输入收敛边界、网络/存储边界、风险和未做项。Grok 不得 `git add`、commit 或 push。

## 4. Codex 独立验收与提交

Codex 核对白名单和契约，重点审查：是否只信后端成功结果；非法结构是否安全收敛；Unicode 截断是否按字符而非 UTF-16 码元；React 是否纯文本渲染；旧项目/旧导出告警是否清空；技术标和商务标下载是否始终继续；是否新增网络、存储或后端改动。

随后 Codex 独立串行运行后端项目图片定向测试、P9D E2E、lint、build 和单 worker 全量 E2E。通过后形成独立中文实现提交并推送，再更新本计划、契约、路线图、联调清单和 HANDOFF，形成独立中文文档闭环提交。

## 5. 实现、审查与独立验收记录

### 5.1 已交付实现

- 新建 `ExportImageWarnings.tsx`：只接受数组中的非空字符串，最多 20 条、每条 `Array.from` 码点最多 240；React 纯文本列表，无链接和 HTML 注入。
- 技术标与商务标导出页：告警状态绑定产生它的 `projectId`；导出/项目切换递增实例级代次；只有当前代次可写告警，旧任务仍沿用既有下载语义。
- 新建 P9D E2E 和 package 脚本：技术标/商务标真实本机导出、非法结构收敛、HTML 文本不解释、后续无告警清空、项目 A 迟到结果不污染项目 B。
- Grok 严格只修改 §2 五个白名单文件，未提交或推送；Codex 完成审查后以 `e5adad7` 独立提交并推送。

### 5.2 两轮 Codex 审查结论

1. 首轮拒绝：仅在 `useEffect([projectId])` 清空会让新项目首帧先渲染旧 state；旧项目 export 迟到还可能再次写入。返修后改为 `{projectId,warnings}` 同步过滤和 `exportImageWarningGenRef` 代次隔离。
2. 同轮补充：新共享文件同时导出纯函数与组件引入 oxlint fast-refresh warning；已用导出行最窄规则指令处理并写中文原因，禁止全局关闭。
3. 第二轮拒绝：迟到 E2E 只等 `route.fulfill`，断言可能在应用续体运行前假绿；并且实现先下载后写告警，与契约顺序不符。最终返修改为等待 A 下载 URL 出现在 `window.open` 桩后再断言 B，并让当前代次先写告警、随后始终下载。

### 5.3 Codex 独立验收

1. 后端使用仓库虚拟环境运行 `tests/test_project_images.py`：14 passed，只有 1 条既有 Starlette/httpx 弃用警告。
2. `npm run test:e2e:export-image-warnings`：4 passed，覆盖技术标真实导出、商务标真实导出、非法结构/文本转义/清空和项目切换迟到隔离。
3. `npm run lint`：零错误、零警告；`npm run build`：通过，仅既有大分块体积提示。
4. `npm run test:e2e`：单 worker 串行 110 passed，用时约 3 分钟；未与其他 Playwright 命令并发。
5. 前端实现差异 `git diff --check` 通过；Codex 发送 `ack=msg_6501bcc367fa4a26ab09cae11a4774fd`，独立中文提交并推送实现。
