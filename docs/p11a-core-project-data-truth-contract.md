<!--
模块：P11A 核心项目真实数据收口契约
用途：冻结技术标/商务标项目列表、详情与创建只认服务端数据的生产边界，消除演示项目和 localStorage 静默回退。
对接：GET|POST /api/projects、GET /api/projects/{id}；技术标/商务标项目入口、创建页与项目选择器。
二次开发：本包不删除测试 fixture 或编辑器本地备份；不得用新的缓存、离线项目或假成功替代服务端项目权威。
-->

# P11A 核心项目真实数据收口契约

> **状态**：只读审计完成，方案已冻结，等待前端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 487 passed；前端 lint/build 通过、单 worker 串行全量 E2E 145 passed。

## 1. 审计结论

当前 `projectStore.ts` 默认把 `mockProjects` 合并进真实技术标列表；项目列表请求失败时读取 `biaoshu.projects.v1` 与演示项目；项目详情失败时也回退本地；创建 POST 失败则静默创建本地项目并导航。商务标列表和直达工作区另有 `mockBusinessProjects` 回退，创建失败会直接进入演示项目。结果是后端空库、离线、401/403/404/500 都可能被“看似成功”的假项目掩盖，用户无法判断项目是否真正写入当前工作空间。

这不是普通演示体验问题，而是核心数据权威错误：假项目不可被后端任务、文件、editor-state、权限或跨刷新一致性验证。P11A 因此只收紧项目元数据主链，让服务端 `/api/projects*` 成为唯一权威；错误必须显式、固定、可重试，绝不把 localStorage/mock 当成成功。

## 2. 服务端权威规则

1. 技术标列表只读 `GET /api/projects?kind=technical`；商务标列表只读 `GET /api/projects?kind=business`；不带 kind 的项目选择器按现有调用读取。`200 []` 是真实空态，不合并任何演示或本地项目。
2. 项目详情只读 `GET /api/projects/{id}`。404、跨空间、角色拒绝或网络失败都不得回退 `mockProjects`、`mockBusinessProjects` 或 localStorage；页面显示固定中文或回到真实列表。
3. 项目创建只走一次 `POST /api/projects`。失败时停留当前页面，显示固定中文并允许用户重试；不得生成 `proj_*` 本地 ID、写 `biaoshu.projects.v1`、导航到假工作区或打开演示商务标。
4. 删除 `VITE_USE_API_PROJECTS`、`VITE_MERGE_MOCK_PROJECTS` 对生产项目真值的控制语义。环境变量即使存在也不得关闭服务端项目、合并 mock 或启用 localStorage 项目 CRUD。
5. 允许保留创建成功后现有 `sessionStorage` 待上传文件名交接，但仅能保存真实 POST 返回的 projectId；创建失败不得新增或修改该记录。

## 3. 页面行为

- 技术标列表：加载中、真实空态、真实列表、固定失败态四态互斥；失败不显示历史 localStorage/mock 行，重试只再发一次真实 GET。
- 技术标新建页与创建方案页：提交期间按钮禁用；失败显示「项目创建失败，请稍后重试」，不回显服务端 detail、code、路径、项目 ID 或异常原文。
- 商务标列表：真实空态不得补演示卡；列表失败显示「商务标项目加载失败，请稍后重试」；创建失败停留本页并显示固定中文。
- 技术标/商务标直达详情：不存在或加载失败不得构造演示项目。商务标工作区必须删除按 `mockBusinessProjects` 查找的分支。
- 查重/废标项目选择器：项目列表失败时显示固定加载提示且选项为空，不读取演示项目；不改变检查 API、结果或既有规则。

## 4. 数据、网络与错误边界

P11A 不新增后端 API、表、依赖、路由、角色或权限。浏览器只可使用既有同源认证/健康与 `/api/projects*`；禁止外网、轮询、下载、剪贴板或项目缓存。不得读取、写入、删除或迁移 `biaoshu.projects.v1`，更不得把其旧值上传到服务端。旧键若已存在，本包流程必须忽略并保持原值不变。

错误页面和 console 不得包含响应 detail、固定 code、请求路径、workspace/project ID、Cookie、CSRF、Key、文件路径或测试秘密串。应用层 console error/warning 必须为空；浏览器自身失败资源噪声不得含敏感片段。

## 5. 明确非目标

- 不删除 `shared/mock/projects.ts`、`business-bid/mock.ts` 或其他测试/演示 fixture；只禁止生产项目入口引用它们。
- 不改技术标/商务标 editor-state 的本地备份、防抖保存或演示初始内容；这些属于后续独立审计。
- 不改知识库文档 localStorage 降级、首页未挂载死代码、财务/人力/投标人页面、认证/RBAC、项目后端或数据库。
- 不新增离线模式、Service Worker、IndexedDB、缓存同步、冲突合并、批量项目管理、归档、搜索或分页。

## 6. 验收底线

前端 E2E 至少覆盖：技术标/商务标真实列表与空态、API 失败不显示 local/mock、旧 `biaoshu.projects.v1` 被忽略且原值不变、技术标两处创建与商务标创建失败不假成功、真实创建只 POST 一次并导航真实 ID、演示 ID 直达不构造工作区、查重/废标选择器失败为空、固定错误脱敏、同源 method+路径白名单、外网/未知 API 阻断、local/session/IndexedDB/Cookie/clipboard/console 边界。认证/RBAC、解析策略、模板复用及全量 E2E 必须单 worker 串行回归。
