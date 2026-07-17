# P12E-C 双修订正文差异前端契约

模块：P12E-C editor-state 双历史修订正文差异前端选择与展示
对接：P12E-B `GET /api/projects/{projectId}/editor-state-revisions/{beforeRevisionId}/body-diff/{afterRevisionId}`、P12E-A 共用正文差异模型。
状态：2026-07-17 已完成；冻结=`8b40bf4`，实现=`b6a4375`，Codex 独立验收通过并已推送。

## 1. 目标与边界

P12E-B 已提供同一 workspace/project 两条历史修订之间的只读正文差异。P12E-C 在技术标与商务标共用的修订历史面板内增加两个修订的内存选择、一次性比较和有界结果展示，使用户能够明确指定“差异前”和“差异后”。

本包只实现：

- 展开修订历史后，按列表项选择一条“差异前修订”和一条“差异后修订”；
- 选择必须是两条不同修订，选择动作不发请求；
- 点击比较后只发一次 P12E-B GET，展示固定中文标签、前后章节计数、变化章数量、截断提示和已有有界行差异；
- 技术标与商务标共用同一面板和同一 API 封装；
- 项目切换、折叠、列表刷新、摘要/与当前对比/单修订正文差异/恢复确认等其他意图会清除双修订选择和结果，并使迟到响应失效。

本包明确不实现：分页、搜索、自动批量比较、完整时间线、修订恢复/删除、导出、分享、缓存、跨项目历史、URL 状态、浏览器存储、多人协作、后端修改、数据库迁移和新依赖。

## 2. API 封装与严格解析

新增唯一前端请求函数：

```text
GET /api/projects/{projectId}/editor-state-revisions/{beforeRevisionId}/body-diff/{afterRevisionId}
```

要求：

1. `projectId`、两个 revision ID 均通过既有格式校验；两个 ID 相同必须在 API 层固定失败，不发请求；路径段分别 `encodeURIComponent`；
2. 使用既有 `apiFetch` 的无第二参数调用，禁止 body、查询参数、重试、轮询或旁路请求；
3. 成功体顶层精确六键 `sameBody/changedChapterCount/beforeChapterCount/afterChapterCount/truncated/items`；章节项精确五键，hunk 精确二键；
4. 复用 P12E-A 的枚举、序号、计数一致性和预算校验：`sameBody` 当且仅当 `items=[]`，`changedChapterCount === items.length`，拒绝未知键、重复/乱序 ordinal、非法 kind/op、超限标题/hunk/文本；
5. 解析失败固定错误标识，不把响应原文、ID、版本、字段键或后端 detail 带入用户文案。

## 3. 面板交互与隐私

### 3.1 选择

- 每个修订项提供固定中文按钮“选为差异前”和“选为差异后”，并用 `data-testid` 绑定索引；DOM、可见文本、URL、存储、console 不得出现 revision ID/stateVersion；
- 已选状态只显示“已选为差异前/已选为差异后”等固定中文；同一项不得同时承担两侧；
- 未完成两侧选择时比较按钮禁用；清除按钮只清内存状态，不发请求；
- 选择动作不清空列表，不触发 detail、comparison、单修订 body-diff、restore、editor-state GET/PUT 或外网请求。

### 3.2 比较与展示

- 比较按钮固定中文“比较两条修订”，两侧 ID 均存在且不同才可点击；点击后只发一次 pair GET，按钮进入“正在比较…”；
- 成功结果只展示“差异前修订/差异后修订”、前后章节数量、变化章节数量、正文一致/有变化、截断提示和有界章节/hunk 文本；不得展示内部 ID、版本、sourceKind 原值或快照字段键；
- 失败只显示固定文案“双修订差异加载失败，请稍后重试”；旧成功结果必须清除；
- pair 结果与摘要、与当前对比、单修订正文差异、恢复确认互斥；开始任一其他意图时清除 pair 选择/结果或使其不可见。

### 3.3 迟到隔离

双修订请求必须拥有独立 generation，并同时检查组件 mounted、项目会话和 pair generation。以下任一操作发生后，A0 迟到响应不得覆盖新状态：

- 重新选择任一侧、清除选择、折叠、刷新列表、切换项目；
- 启动摘要、与当前对比、单修订正文差异或恢复确认；
- A0 挂起后选择 B 并发起新 pair 请求。

## 4. 实现白名单

Grok 只允许修改以下三个文件，不得 `git add/commit/push`：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止修改后端、其它前端组件、CSS、路由、依赖、数据库、其它 E2E、文档、浏览器存储或 URL 状态。

## 5. Failure-first 与验收门

先只在三文件白名单内增加真实前端红测，证明 pair 入口/请求/结果尚不存在；不得用 TS 收集失败、依赖缺失、白页或固定 sleep 冒充业务红测。至少覆盖：

- 技术标：选择不发请求、两侧选择后精确一次 GET、无 query/body、严格成功与同正文结果、中文展示和零 ID 泄漏；
- 技术标：非法顶层/项/hunk/计数/预算响应固定失败且清除旧结果；
- 技术标：A0→A1、重新选择、折叠/刷新、摘要/当前对比/单修订正文差异/恢复和项目切换的迟到隔离；
- 商务标：共享入口精确一次 pair GET，正文不变，零 detail/current/body-diff/restore/PUT/editor-state GET/外网旁路；
- 选择清除、空选择、相同修订选择不得发请求。

Grok 完成后只发送 `review_request`，报告真实红/绿数字、三文件白名单、零旁路证据和未做边界。Codex 随后独立运行 P12E-C E2E、P12E-A/P12D/P12C 受影响回归、前端全量单 worker 零重试、lint、build、`git diff --check` 和白名单检查。

## 6. 完成与验收记录

- Grok 任务=`msg_70f49042da2e46d5a7d2783ee8f7575f`，最终 review_request=`msg_fa38202aa5d641d5b111d914995d6f4f`，Codex 验收回执=`msg_fd6c844f235644e9b3c4bd597d049d36`；Grok 未 `git add/commit/push`。
- 真实 failure-first：只增加 P12E-C 探针和三组测试后为 **3 failed / 0 passed**，首个失败是生产面板不存在 `editor-state-revision-pair-select-before-0`；不是 TypeScript 收集、fixture、依赖、白页或服务启动失败。实现后聚焦测试 **3 passed**。
- Codex 独立串行验收：P12E-C 聚焦 **3 passed**；P12E-A/P12D-B/P12C-C3 受影响 history 回归 **27 passed**；前端全量 **293 passed (8.2m)**，均使用 `--workers=1 --retries=0`。
- `npm run lint`、`npm run build`、`git diff --check`、精确三文件白名单和空暂存区通过；构建只有既有 chunk 大小提示。
- 交付保持本契约未实现边界：没有分页、搜索、自动批量比较、完整时间线、恢复/删除、导出、分享、缓存、跨项目历史、URL/浏览器存储或多人协作。
