<!--
模块：P11C 技术标编辑态真实数据收口契约
用途：冻结技术标工作区只认服务端 editor-state 的生产边界，消除本地编辑器缓存、默认 mock、失败假成功与 required 模式保存失效。
对接：GET|PUT /api/projects/{id}/editor-state；useTechnicalPlanEditors；TechnicalPlanWorkspace；P11A/P11B；响应矩阵与 M3-D。
二次开发：本包不改后端、响应矩阵算法、通用版本历史或 guidance 历史；不得用新缓存、离线草稿或演示数据替代服务端权威。
-->

# P11C 技术标编辑态真实数据收口契约

> **状态**：已按冻结契约完成、独立验收并推送。计划/契约=`24b7ba8`，安全细化=`c5b3eec`，前端实现=`1441509`。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收结果**：后端未改，沿用串行全量 487 passed；前端 lint/build 通过、P11C 18 passed、Chromium headless 单 worker 串行全量 E2E 184 passed。

## 1. 审计结论

P11A 已让技术标项目列表、详情和创建只认 `/api/projects*`，但技术标编辑内容仍存在完整假成功链：

1. `useTechnicalPlanEditors` 初始读取 `biaoshu.technicalPlan.editors.{projectId}`；无旧键时 `defaultState()` 直接装入 `mockOutline/mockChapters/mockFacts`。
2. 服务端合法空 editor-state 仍经 `fromApi(remote, local)` 按字段回退本地/mock。由于空矩阵也有 `responseMatrixVersion`，页面会把混入本地/mock 的内容标成「后端」；用户首次编辑后又可能把整套假内容 PUT 到服务端。
3. 初始 GET 失败后 Hook 静默使用本地值、标记 hydrated 并继续挂载全部写控件；加载期间也会先渲染本地/mock，没有独立加载失败态。
4. 防抖 PUT 非 409 失败仅把来源改成「本地」，页面没有明确保存失败；当前 raw `fetch` 不携带内存 `X-CSRF-Token`，`AUTH_MODE=required` 下正常编辑会被中间件拒绝。
5. `reloadFromApi` 不校验项目会话。项目 A 的任务或融合请求迟到后仍可 GET A 并覆盖已经切换到 B 的同一个 Hook 状态。
6. 页面还暴露「填入演示数据」、伪装为「从招标/知识库抽取」的演示事实、固定时间戳生成日志和无实际父级处理的「恢复示例目录」入口；这些生产入口可把示例内容写入真实项目。
7. 页面项目详情状态未绑定请求 projectId；SPA 快速切换时，旧项目对象在 effect 执行前可能短暂参与新路由首帧渲染。

因此 P11C 只修技术标编辑态真值和相关生产演示入口，不扩为技术标大重构。

## 2. 服务端唯一权威

1. 技术标编辑内容首次加载、显式重试及任务后刷新只读当前项目 `GET /api/projects/{id}/editor-state`。
2. `outline=null|[]`、`chapters=null|[]`、`facts=null|[]`、空 analysis、空 responseMatrix、`parsedMarkdown=null|""` 与 `updatedAt=null` 都是合法真实空态，不得回填本地或 mock。
3. 允许继续从同一服务端响应的 analysis 与 responseMatrix 做既有确定性 `mergeResponseMatrix` / 死链接收敛；禁止引入响应外数据。
4. 初始内存状态和切项目重置状态必须为空：空大纲、空章节、空事实、空分析、空矩阵、空解析文，mode 默认 `ALIGNED`。
5. `frontend/src/features/technical-plan/mock.ts` 可暂留历史文件，但生产 Hook、页面和组件不得导入或使用其中数据；不得为旧项目做迁移。

## 3. 浏览器存储边界

- 禁止读取、写入、删除、迁移或上传 `biaoshu.technicalPlan.editors.{projectId}`。旧键存在时必须忽略并保持键和值精确不变，也不得改名为 v2/cache/draft/其他别名。
- 不新增 localStorage、sessionStorage、IndexedDB、URL 查询参数、模块全局编辑器缓存、离线队列、Service Worker、下载、剪贴板或外网请求。
- `useProjectGuidance` 的既有本地 guidance/反馈历史属于本包明确非目标；它不得参与 editor-state 水合、加载成功、保存成功或失败回退。
- Cookie 和 CSRF 只沿用认证层同源内存语义；不得读取 `document.cookie`，不得把 CSRF/Cookie/项目正文写入浏览器存储、console 或错误文案。

## 4. 加载、刷新与页面三态

1. Hook 对外提供 `loading`、固定 `loadError`、`apiReady` 与 `reloadFromApi`。首次 GET 或显式重试期间只显示加载态，不挂步骤、表格、编辑器、模板沉淀或任务入口。
2. GET 失败统一显示「技术标工作区加载失败，请稍后重试」；工作区不可见、不可编辑、不可 PUT，只提供「重试」和「返回列表」。不得回显 detail/code/路径/项目 ID/异常原文。
3. 重试每次只新增一次当前项目 GET；成功后完整替换为服务端状态、清加载/保存错误并允许保存。
4. 普通 parse/analyze/outline/chapters/chapter 任务成功后的刷新若失败，任务成功事实不反转，但页面进入同一固定加载失败态，不把旧内容称为最新。
5. M3-D `ContentFuseDialog` 保留既有 `reloadFromApi(): Promise<boolean>` 契约：融合 create/consume 成功后的 GET 失败仍让对话框显示既有「业务已完成但刷新失败」提示并禁止二次业务提交；对话框关闭后必须落到 P11C 固定失败卡，底层旧内容不得重新成为可编辑真值。
6. 项目详情结果必须与请求 projectId 绑定。路由 A→B 首帧不得渲染 A 标题、编辑内容、错误或动作；项目不存在仍沿用 P11A 返回技术标列表的行为。

## 5. 保存、认证与冲突

1. 只有当前项目初始 GET 成功且会话仍有效时，用户编辑才进入既有 800 ms 防抖 PUT；body 保持 outline/chapters/facts/mode/analysis 和既有 responseMatrix/version 语义。
2. 普通 PUT 与「应用矩阵合并」PUT 必须 `credentials: same-origin`，并在存在认证层内存 CSRF 时携带精确 `X-CSRF-Token`。不得从存储或 Cookie 取 Token。
3. PUT 网络错误、401/403、404、422、500 等非 409 失败统一显示「技术标工作区保存失败，请稍后重试」；当前内存编辑可保留，再次编辑可触发新的单次保存，成功后清错。
4. 409 继续走既有响应矩阵冲突/三方合并流程，不显示通用保存错误、不自动覆盖、不自动重试；应用合并仍只 PUT `responseMatrix + responseMatrixVersion`。客户端只可按既有类型读取收敛后的远端矩阵与版本，冲突提示使用固定中文，不得直接展示服务端 `detail.message`、code、路径、项目 ID 或其他原文。
5. 移除所有 `saveLocal` 副作用。矩阵成功、冲突、二次 409 和 M3-D 重载都不得复活旧本地键。

## 6. 项目会话与迟到隔离

1. 项目切换同步递增会话代次、清防抖定时器、清 loading/load/save/conflict/merge 状态、禁止旧会话保存，并把内存编辑态重置为空。
2. 首次 GET、显式重试、任务后 `reloadFromApi`、普通防抖 PUT、409 解析、合并 PUT 的成功/失败/finally 都必须在写状态前同时校验 requestProjectId 与 requestSession。
3. A 的迟到 GET 不得覆盖 B；A 的迟到 PUT 成功/失败/409 不得改变 B 的内容、版本、base、冲突、loading、loadError、saveError 或 apiReady。
4. B 初始 GET 成功前不得发送 B PUT；A 定时器不得用 B 的最新 state 写回 A。

## 7. 生产演示入口清理

- 删除 Hook 的 `fillDemoAnalysis` 与 `extractDemoFacts`，页面移除「填入演示数据」，事实编辑器移除伪抽取按钮与相关 props。
- 大纲组件移除固定 `DEMO_LOGS` 和固定时间戳；仅显示由真实 `generating/progress/outline` 推导的有限状态，不冒充真实任务事件。
- 未接 `onReset` 时不得显示无效「恢复示例目录」按钮；本包不新增清空 API 或真实重置行为。
- 正文空态文案删除「前端 mock 见 mockChapters」，只说明需先生成大纲/章节。
- 文档预览的「尚未解析」说明属于纯 UI 空态，不写 editor-state，可保留。

## 8. 明确非目标

- 不改后端 editor-state schema/service/API/数据库，不改任务、解析、导出、文件、模板、知识库、认证中间件或角色权限。
- 不改 responseMatrix 归一化、版本哈希、409 三方合并算法、来源分页、建议应用或字段合并语义。
- 不改 M3-D create/consume 服务端业务，不改 ContentFuseDialog 的文案与一次提交边界。
- 不把 `useProjectGuidance` 历史迁移服务端，不做自动事实抽取替代实现。
- 不做通用 editor-state 版本历史、任意历史浏览/回滚、多人实时协作、离线草稿、保存队列或 CRDT。
- 不删除整个 `technical-plan/mock.ts`，不改 CSS/依赖/Playwright 配置，不顺手重构 1200 行 Hook 的非真值逻辑。

## 9. 验收底线

新 E2E 至少覆盖：真实服务端内容、服务端全空态、旧 editor 键忽略保值、加载期间零工作区、GET 500/401/404 固定失败与显式重试、普通防抖 PUT 精确 body、required 登录后的 Cookie/CSRF、PUT 失败固定脱敏与再次保存、409 仍走矩阵冲突且任意 detail 原文不展示、普通任务后刷新失败、M3-D 刷新失败兼容、A→B 旧项目/GET/PUT/reload/409 迟到隔离、生产演示入口消失、method+精确路径白名单、未知 API/外网阻断及 local/session/IndexedDB/Cookie/clipboard/console 边界。

必须串行回归 P11B、P11A、认证/RBAC、解析策略、响应矩阵五 spec、M3-D 原子确认与持久恢复、模板复用及全量 E2E。所有 Playwright 均为 Chromium headless、单 worker、逐条串行。

## 10. 交付结论

- 实现严格限定为计划中的七个前端文件，无后端、共享客户端、响应矩阵算法、M3-D 业务、路由、样式或依赖扩散。
- Grok 首版和第一次返修分别因验收证据不完整、required 登录不真实及跨项目保存链阻塞被 Codex 退回；最终版补齐真实登录 Cookie/内存 CSRF 和 A 挂起 PUT 不阻塞 B 的自动化证据。
- Codex 独立通过 P11C 18、P11B 11、P11A 10、认证 11、解析 6、矩阵 8、融合确认 6、持久恢复 5、模板 1 及全量 184 项 E2E；lint、build、`git diff --check` 通过。
- 实现提交为 `1441509`，已推送协作分支；本契约的通用版本历史、多人协作、guidance 历史服务端化与真实解析器部署非目标继续有效。
