<!--
模块：P12D-B 修订与当前版本对比前端契约
用途：冻结技术标/商务标共用比较入口、严格响应解析、可见信息和迟到隔离边界。
对接：P12D-A comparison API、P12C-C3 共享修订历史面板与修订历史 E2E。
二次开发：禁止自动批量比较、正文 diff、内部字段名/ID/版本泄漏；所有 E2E 必须单 worker、零重试。
-->

# P12D-B 修订与当前版本对比前端契约

> **状态**：已完成只读审计并冻结，待 Grok failure-first 受限实现与 Codex 独立验收。
> **基线**：P12D-A 冻结=`2cc6ee3`、实现=`9445fcc`、闭环=`fcf7447`；后端/前端串行全量 **831/284 passed**，修订历史 E2E **21 passed**。

## 1. 选包与用户价值

P12D-A 已提供目标修订相对服务端当前 13 键状态的只读差异字段和两侧六项摘要，但技术标、商务标仍只有“查看摘要”和“恢复”入口，用户无法在恢复前看到“哪些数据域不同”。P12D-B 只补共享前端入口：用户显式点击某条修订的“与当前对比”后，页面按需请求 P12D-A，并以中文显示一致性、差异字段和两侧摘要。

不采用展开列表后自动比较：列表最多 10 条，自动请求会放大读取、制造竞态并破坏 P12C-C3 默认折叠零请求与按需最小化。也不替换“查看摘要”：单侧历史摘要仍是已有能力，比较是独立用户意图。

## 2. 交互与可见信息

- 每条修订在既有“查看摘要”“恢复”旁新增“与当前对比”；默认折叠、展开列表、刷新列表均不得自动发 comparison 请求。
- 点击后按钮显示“正在对比…”。成功结果仅显示：
  1. `与当前版本一致` 或 `与当前版本存在差异`；
  2. 不一致时的中文字段标签；
  3. “当前版本”和“所选修订”两侧各六项摘要：大纲节点、章节、事实、矩阵行、商务条目、是否含解析正文。
- 13 个固定中文标签：`outline=大纲`、`chapters=章节`、`facts=事实`、`mode=编写模式`、`analysis=分析`、`responseMatrix=响应矩阵`、`guidance=编写指导`、`parsedMarkdown=解析正文`、`businessQualify=商务资格`、`businessToc=商务目录`、`businessQuote=商务报价`、`businessCommit=商务承诺`、`analysisOverview=分析概览`。
- DOM、文案、URL、浏览器存储、日志、console、剪贴板和下载均不得出现内部字段键、`revisionId`、`stateVersion`、正文、字段值、后端错误 detail、路径或响应原文。数字摘要和固定中文标签是唯一可见业务信息。
- 固定失败文案：`修订差异加载失败，请稍后重试`；不得按 404/500/网络/解析失败显示不同内部原因，不得把失败伪装成“与当前版本一致”。

## 3. 严格 API 解析

新增前端调用：

```text
GET /api/projects/{projectId}/editor-state-revisions/{revisionId}/comparison
```

- 请求无 body、无查询参数、无重试、无轮询；项目和修订 ID 只经 `encodeURIComponent` 进入请求路径，不进入可见页面。
- 顶层必须精确四键：`sameState`、`changedFields`、`currentSummary`、`targetSummary`，禁止额外键。
- `sameState` 必须是布尔；`changedFields` 必须是 13 键的无重复、有序子序列，严格沿后端固定顺序；`sameState` 当且仅当数组为空。
- 两侧摘要都必须精确六键：`outlineNodeCount/chapterCount/factCount/responseMatrixRowCount/businessEntryTotal/hasParsedMarkdown`。五个计数必须是非负安全整数，最后一项必须是布尔；额外键、缺键、负数、浮点、字符串、`NaN` 或无限值全部固定失败。
- 未知、重复、乱序字段，`sameState` 与数组矛盾，非法摘要或额外泄漏字段均固定失败；解析失败时不得保留上一次比较结果。

## 4. 操作互斥与迟到隔离

- 同一时刻只显示一条修订的一种辅助视图：摘要或比较。点击比较会作废在途摘要、清除摘要和恢复确认；点击摘要会作废在途比较并清除比较；点击恢复会作废两类只读请求并清除两类结果。
- 再次点击当前比较按钮关闭结果并作废在途请求；点击另一条修订立即切换目标，旧请求的 `try/catch/finally` 均不得覆盖新结果、错误或 loading。
- 项目切换、折叠面板、刷新列表、恢复开始、恢复完成后的列表重载和组件卸载都必须递增比较请求代次并清空比较状态。
- A 修订挂起后点击 B，B 可先成功；A 真正完成后不得覆盖 B。项目 A 挂起后切到项目 B，A 真正完成后不得写入 B、不得自动为 B 发 comparison。
- comparison 是只读 GET，不受 `disabled` 恢复阻断控制；但恢复执行期间禁用比较。比较不得触发 restore POST、editor-state GET/PUT、检查点、详情 GET、外网或任何写入。

## 5. 严格实现白名单

Grok 只允许修改以下 3 个文件：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止修改后端、技术标/商务标页面或 hook、共享 `apiFetch`、样式文件、依赖、配置、其他 E2E、文档或用户数据。不得新增模块全局缓存、浏览器存储、URL 参数、自动刷新、轮询、批量比较、AbortController 作为唯一隔离证据。Grok 不得 `git add`、commit 或 push；文档、提交和推送由 Codex 负责。

## 6. failure-first 与验收门

- 在生产前端未改时，先扩充既有 E2E 探针并新增精确 3 个测试：技术标比较/严格解析/互斥，技术标迟到隔离，商务标共享入口。有效红测预期 **3 failed / 21 passed**，失败必须来自“与当前对比”按钮或结果不存在；不得用 TypeScript 错误、坏 fixture、route 漏接、缺依赖或浏览器启动失败冒充。
- 技术标成功测试必须精确证明：默认和展开后 comparison=0；点击后 GET=1、无 body；差异字段中文且顺序正确；两侧六项摘要精确；同状态文案；原始键/正文/ID/版本零泄漏；summary/compare/restore 互斥；GET/POST/PUT 和外网计数无旁路。
- 严格解析至少覆盖：额外顶层键、未知/重复/乱序字段、`sameState` 矛盾、摘要缺键/额外键、负数/浮点/字符串/非法布尔；全部固定失败并清除旧成功结果。
- 迟到测试必须用 gate 的 entered 与 complete 两类日志证明请求真的挂起和真的完成，覆盖 A→B 修订切换、项目切换、折叠/刷新/摘要或恢复作废；禁止只观察 arrived、固定 sleep、宽泛 `>=1`、`.or(...)` 或 route fallback 假成功。
- 商务标必须走同一共享面板和 API，精确 comparison=1、零 restore/PUT/editor-state GET 旁路，并保持正文不变。
- Grok 运行修订历史专项、检查点回归、技术/商务真值回归、lint/build；Codex 独立重跑并执行前端全量。所有 Playwright 命令必须 `--workers=1 --retries=0` 且逐条串行，禁止并行。

## 7. 非目标

本包不实现正文或字段值 diff、行级/字符级高亮、任意两个历史修订比较、自动批量比较、比较缓存、导出、复制、分享、搜索、分页、删除、保留策略、恢复逻辑修改、后端修改、审计事件、新角色、多人协作或外网请求。比较只针对“所选历史修订 vs 请求时服务端当前状态”；本地未保存编辑不在比较范围。
