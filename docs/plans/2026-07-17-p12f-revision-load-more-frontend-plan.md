# P12F-C 修订历史前端加载更多实施计划

> **执行者：Grok**：严格三文件先写真实业务红测再实现；Codex 独立审查、串行验收、中文文档闭环和提交推送。
>
> **完成状态：** 2026-07-17 已完成并推送；冻结=`bb1ae3e`、实现=`fe99f5a`。

**目标：** 让技术标与商务标共用修订面板手动消费 P12F-B 游标页，按需从默认 10 条扩展到最多 20 条，同时保持既有详情、恢复、对比、保存链和迟到隔离。

**技术栈：** React 19、TypeScript 6、现有 `apiFetch`、Playwright Chromium headless。

## 1. 基线与 failure-first

1. 核对分支、HEAD/远端和干净工作区；读取 P12F-C 契约、P12F-B 后端合同、API/面板及完整修订历史 E2E。
2. 只修改 `editor-state-revision-history.spec.ts`：补 `/page` arrived/complete/cursor 探针及 P12F-C 新测试，旧列表探针保留为精确零请求守卫。
3. 显式 `--workers=1 --retries=0 --grep P12F-C` 运行真实红测；首个失败必须是旧路由仍被调用、加载更多按钮/第二页能力缺失，而不是环境或语法问题。

## 2. API 严格页封装

1. 新增精确 `items/nextCursor` 页类型和 parser；每页最多 10、页内 ID 唯一、游标仅做长度/前缀/base64url 外壳校验，禁止解码或生成。
2. 新增第一页/带 cursor GET 封装；只允许一个可选 cursor，无 body、无客户端分页或搜索参数。
3. 保留旧 `{items}` parser 与函数，不改变详情、恢复、comparison/body-diff/pair API。

## 3. 面板加载更多

1. 首次展开、刷新和恢复后重载切换为新页第一页；保存 `nextCursor`。
2. 增加固定按钮、独立 loading/error、同步在途 ref 与请求代次；成功追加且最多 20，失败保留列表与游标可重试。
3. 折叠/卸载/项目切换/刷新/恢复重载作废迟到分页；旧 catch/finally 不得污染新会话。
4. 追加项复用现有摘要、对比、正文差异、跨页 pair 与恢复动作；不新增 API、存储或全局状态。

## 4. 测试与反假绿

1. 技术标覆盖 20 条加载、请求精确性、严格 parser、重复/超限、失败重试、双击单飞、跨页摘要/pair 和零泄漏。
2. 商务标覆盖第二页恢复、执行时 expected、成功唯一 editor-state GET、历史只重载第一页。
3. 用 arrived + complete gate 覆盖折叠、刷新、项目切换和恢复重载迟到隔离；禁止固定 sleep、`.or(...)`、宽泛计数或 `force:true` 冒充可交互证据。
4. 串行运行完整修订历史、技术/商务 truth、checkpoint restore、lint/build；Codex 再独立运行前端全量。

## 5. 审查、提交与未做

1. Grok 只发送 review_request，列出红/绿数字、精确三文件、门禁与风险；不得提交推送。
2. Codex 审查游标不透明、20 条上限、失败保值、代次/单飞、既有写链和数据最小化；必要返修仍锁定最小文件。
3. 独立定向、受影响回归、lint/build、前端全量通过后，由 Codex 中文提交实现并单独完成文档闭环。
4. 不做无限滚动、自动预取、搜索/筛选/删除、total/hasMore、页码、跨项目历史、多人协作或后端变更。

## 6. 实际执行结果

1. Grok 原任务=`msg_878d37c5db1946a59b7dcc70d605a4ea`，failure-first **2 failed / 0 passed / 2 did not run**；生产文件在红测前未改。首轮实现后 P12F-C **4 passed**、完整 history **34 passed**。
2. 第一次受限返修关闭空字符串 cursor、假双击、宽泛重开计数、Cookie 漏检和禁止旁路未断言；任务/回执=`msg_0dff84f4f11349da87ff8695ff105a36`/`msg_021c43c667e348948dfad51d6c927298`。
3. 第二次仅 E2E 返修把宽泛 knowledge 例外收紧为精确 `GET /api/knowledge/folders`；任务/回执=`msg_8bc571cf0bf544fe8206134e5ec43155`/`msg_319b7051f10f45089a18a1a77beb4d68`。
4. Codex 独立通过 P12F-C/history/技术真值/商务真值/checkpoint **4/34/28/18/51 passed**，前端全量 **297 passed**；lint/build/diff-check/三文件/空暂存区通过。实现提交=`fe99f5a`，验收回执=`msg_f83db79a50aa4e3d9e4aa65c9dcc9263`。
5. 自然 UI 在 load-more 在途时禁用刷新与恢复；实现中的重载代次失效是防御性围栏。E2E 不用 `force:true` 越过禁用态，也不声称同时触发了不可达交互。
