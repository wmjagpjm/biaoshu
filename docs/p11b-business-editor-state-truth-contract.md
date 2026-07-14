<!--
模块：P11B 商务标编辑态真实数据收口契约
用途：冻结商务标工作区只认服务端 editor-state 的生产边界，消除 workspace localStorage、演示初始内容与加载失败假成功。
对接：GET|PUT /api/projects/{id}/editor-state；BusinessBidWorkspace；P11A 项目真值。
二次开发：本包不处理技术标 editor-state 与 AI 反馈历史；不得用新缓存、演示内容或吞错替代服务端权威。
-->

# P11B 商务标编辑态真实数据收口契约

> **状态**：只读审计完成，方案已冻结，等待前端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 487 passed；前端 lint/build 通过、单 worker 串行全量 E2E 155 passed。

## 1. 审计结论

P11A 已让商务标项目列表、详情和创建只认服务端，但 `useBusinessBidWorkspace` 仍先读取 `biaoshu.businessBid.workspace.{projectId}`，对 `bb_*` 生成整套演示工作区；GET editor-state 失败后继续展示本地/演示内容，并在 API 是否成功都把当前 workspace 写回 localStorage。页面只等待请求结束，没有加载失败态。旧项目 A 的迟到 GET/PUT 也缺少项目会话代次保护，可能覆盖 B 或把 A 的保存错误显示到 B。

因此真实项目虽已收口，编辑内容仍可被本地旧值伪装成服务端成功。P11B 只修商务标编辑态：项目详情继续沿用 P11A，商务内容只认既有 editor-state GET/PUT；真实空字段保持空，失败显式、不可编辑、可重试，绝不回退本地或演示数据。

## 2. 服务端权威规则

1. 工作区首次内容与显式刷新只读 `GET /api/projects/{id}/editor-state`。`parsedMarkdown=null`、`businessQualify=[]`、`businessToc=[]`、空 `businessQuote`、`businessCommit=[]` 均为真实空态，不补 mock。
2. 用户编辑只在初始 GET 成功、当前项目会话仍有效后进入既有 600 ms 防抖 `PUT /api/projects/{id}/editor-state`；body 继续只含商务字段，不改变后端部分更新语义。
3. GET 失败显示固定「商务标工作区加载失败，请稍后重试」，不渲染旧内容、不挂可写工作区；重试每次只新增一次当前项目 GET。
4. PUT 失败显示固定「商务标工作区保存失败，请稍后重试」，不得回显 detail、code、API 路径、项目 ID 或异常原文；内存编辑可保留供用户再次操作触发保存，但不得落浏览器项目缓存。
5. 任务或修订成功后的 editor-state 刷新若失败，业务成功事实不反转；页面进入同一固定加载失败态并允许显式重试，不得继续把旧内存内容显示成最新服务端状态。
6. 项目 A→B 时，A 的迟到 GET、PUT 成功/失败与定时器不得覆盖 B 的内容、加载态、保存错误或 API 就绪态。

## 3. 浏览器数据边界

- 禁止读取、写入、删除、迁移或上传旧 `biaoshu.businessBid.workspace.{projectId}`。旧键若存在必须忽略并保持原值精确不变，也不得改写为 v2/cache/其他别名。
- `biaoshu.businessBid.feedback.{projectId}` 的 AI 反馈历史保持既有本地语义，是本包明确非目标；只可按当前精确键格式存在，不得被当成 editor-state 或服务端成功依据。
- 不新增 localStorage/sessionStorage/IndexedDB/URL/模块全局缓存、离线模式、轮询、下载、剪贴板或外网请求。
- 页面和应用层 console error/warning 不得包含服务端 detail/code、路径、workspace/project ID、Cookie、CSRF、Key、文件路径或测试秘密串。

## 4. 页面状态

1. 项目详情加载与 editor-state 加载相互独立但最终必须一致：项目不存在仍显示 P11A 的「未找到项目」；项目存在而 editor-state 失败显示 P11B 固定失败卡。
2. 加载中、加载失败、真实工作区三态互斥。失败卡只提供「重试」和「返回列表」，不得短暂闪现 local/demo 内容。
3. 重试成功后才挂载步骤、表格和编辑控件；真实空态使用现有空数组/空文本 UI，不创建演示资格项、目录、报价或承诺块。
4. 保存失败在当前真实工作区显示固定中文；项目切换立即清空旧错误。

## 5. 明确非目标

- 不改技术标 `useTechnicalPlanEditors` 的 mock/localStorage 回退，不改 `useProjectGuidance` 本地历史。
- 不把商务标 AI 反馈历史迁移服务端，不删除 `business-bid/mock.ts`；只禁止 workspace 生产路径使用 `createDemoWorkspace`。
- 不改 P11A 项目列表/详情/创建、后端 editor-state、任务/解析/修订/export、认证/RBAC、财务投影、知识库或样式。
- 不新增版本历史、冲突合并、多人协作、离线编辑、保存队列、服务端事件、附件或外网。

## 6. 验收底线

前端 E2E 至少覆盖：服务端真实内容与真实空态、预置旧 workspace 键不读取且原值不变、GET 失败固定卡且零旧内容/零 PUT、显式重试单次 GET、成功后防抖 PUT 精确 body、PUT 失败固定脱敏、任务后刷新失败不谎报最新、A→B GET/PUT 迟到隔离、旧 workspace 键族无别名、反馈历史精确允许、method+路径白名单、未知 API/外网阻断及 local/session/IndexedDB/Cookie/clipboard/console 边界。P11A、解析策略、导出图片告警和全量 E2E 必须单 worker 串行回归。
