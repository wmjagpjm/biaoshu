# P12N 已加载修订固定优先前端契约

模块：P12N editor-state 修订历史已加载列表固定优先

用途：在 P12F-J-A/B 已交付修订固定与保护性裁剪后，先提供一版立即可见的固定优先体验：默认/筛选态中，当前已经加载到浏览器的固定修订显示在普通修订之前；固定、取消固定或加载第二页后立即按该规则重排。

对接：`EditorStateRevisionPanel`、技术标/商务标共用修订历史入口、既有 `editor-state-revision-history.spec.ts`。

二次开发：Grok 只允许在两文件白名单内测试先行与实现，不得暂存、提交或推送；Codex 负责独立规划、受限审查、聚焦验收、中文文档、提交和协作分支推送。

状态：2026-07-20 已完成并由 Codex 提交，冻结=`337b401`、实现=`394639a`。该包是快速前端版，不冒充服务端游标级固定优先。

## 1. 选择理由与版本边界

1. 当前固定修订可防止自动裁剪，但默认页面仍按服务端时间顺序显示；用户固定当前可见修订后，位置不变，价值反馈较弱。
2. 服务端权威固定优先需要同时升级 list/page 排序、三类游标及 pin 后游标失效策略，范围较大。按“先出一版、后续增强”的要求，本包只重排当前已加载的严格前端元数据。
3. 不改变后端、API、Schema、数据库、游标、请求数量、分页上限或搜索结果顺序；因此可用两文件快速闭环，并为后续服务端版本保留清晰边界。

## 2. 用户可见合同

1. 非 active search 时，当前 `items` 中所有 `isPinned === true` 项显示在普通项之前；固定组和普通组内部均保持服务端返回/分页追加的原始顺序。
2. 点击“固定”成功后，该项立即进入已加载列表固定组，并按其在原始服务端 `items` 中的位置参与固定组稳定排序；点击“取消固定”成功后立即回到普通组对应原序位置。PATCH 失败完全保留原位置与状态。本包不承诺新固定项绝对追加到固定组末尾。
3. 加载更多成功后，第二页新出现的固定项移动到全部已加载固定项的相应原始顺序位置；总数、去重、20 条上限和 `nextCursor` 处理不变。
4. 来源筛选和时间筛选仍属于非搜索列表，应用相同的“已加载固定优先”；active search 必须保持服务端搜索顺序，`matchReasons` 标签和索引对应关系不变。
5. 技术标与商务标共用同一面板；摘要、对比、正文差异、双修订选择、恢复、命名、删除和固定动作必须继续按 `revisionId` 绑定，不得因显示索引变化操作错行。

## 3. 实现合同

1. 只在 render 期从 `items` 纯派生显示数组；禁止新增 state、effect、ref、请求、缓存、定时器或本地持久化。
2. 禁止原地 `items.sort()` 或修改 state 数组。推荐单次遍历形成 `pinnedItems` 与 `unpinnedItems` 后拼接；active search 直接使用原 `items`。
3. 固定判断必须为 `item.isPinned === true`；不得 truthy 宽判、按 badge/文案/索引猜测或从 DOM 回读。
4. testid 中的 index 表示当前显示顺序；React key 继续使用 `revisionId`。所有 handler 继续接收目标 `revisionId`/当前值，不得把 index 当业务身份。
5. A→B 项目切换、折叠/展开、筛选/搜索代次、迟到 success/catch/finally、全局单飞与全部既有互斥门保持不变。

## 4. Failure-first 与反假绿

1. 第一阶段只修改既有 history E2E；面板 SHA-256 必须仍为 `5C41D4A3C2807B1A69DB40D34F22E40A7A664280765A3F8D7C7DFCE3EB25E31D`。
2. 真实浏览器先证明：默认混合列表仍普通/固定交错、pin/unpin 后不移动、第二页固定项仍停在末尾；至少一项静态门证明尚无纯派生分组实现。
3. 红测必须在页面、列表请求和目标行均真实到达后因顺序错误失败；白页、路由失败、元素未加载、skip/xfail、收集错误或宽断言不算业务红测。
4. 禁止 `force:true`、`waitForTimeout`、sleep、retry、Promise.race、并发 Playwright、“至少一次”、条件断言或只搜源码冒充浏览器行为。

## 5. 串行验收门

Grok 逐条运行 P12N 聚焦、P12F-C 分页/P12F-J-B 固定/P12M 搜索受影响 history E2E、lint、build、diff-check 与静态门。Codex 独立复跑 P12N 聚焦和 lint/静态/哈希/差异门；不机械重复完整 history 文件、整仓 318 E2E 或任何后端 pytest。全部 Playwright 固定 `--workers=1 --retries=0`。

## 6. 严格两文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx` | `5C41D4A3C2807B1A69DB40D34F22E40A7A664280765A3F8D7C7DFCE3EB25E31D` | 非搜索态纯派生稳定分组及中文顶注释 |
| `frontend/e2e/editor-state-revision-history.spec.ts` | `64ADC634816E34A8398E2D0694F0714E5191A313ABD68F12CA7F57A4D1ED2CB7` | P12N failure-first、默认/筛选/分页/pin/search/双工作区/隔离证据 |

禁止修改 API、后端、样式、页面/hook、依赖、配置、其它测试或文档；两文件不足时只能发送 `question`。

## 7. Grok 回执合同

只发送一个完整 `review_request`：failure-first 精确结果与面板冻结哈希、P12N/受影响 E2E、lint/build、两文件最终哈希、空暂存区、默认/筛选/分页/pin/unpin/search/技术商务/A→B 证据、静态门、风险和未做项。额度或进程中断只发 `status`，不得补造数字。

## 8. 明确未做

本包不保证尚未加载的固定修订提前进入第一页；不做服务端 list/page 固定优先、esrc 游标升级、固定组标题、项目总固定数/容量、自动加载第二页、pin 后额外 GET、搜索固定优先、拖拽排序、批量固定、跨项目历史、完整时间线、导出/分享、多人协作、presence、SSE/WebSocket、后端/API/数据库/依赖变更。服务端权威固定优先必须后续独立立项。

## 9. 完成与验收记录

1. Grok task/review=`msg_821f2f19ef8044fcbd85f28cc764de29`/`msg_449e2631192944c39419507c4956c161`。仅改 E2E 时真实 failure-first **4 failed / 1 passed**，其中三项业务失败均在 page 与行真实到达后因旧顺序失败；面板哈希保持冻结。
2. Grok 实现后 P12N/受影响 history **5/12 passed**，均 Chromium `workers=1,retries=0`；lint/build/diff-check 通过。未运行完整 history、整仓 318 E2E 或任何后端 pytest。
3. Codex 独立串行通过 P12N **5 passed in 9.4s**、lint、精确两文件、空暂存区、diff/哈希/纯派生/禁止项门；未机械重复受影响 12 项或 build。验收 ack=`msg_77a0632fdf5e4eb5bd21ea9e32205430`。
4. 最终 SHA-256：面板=`FEAD15B6CB4043D1E6A96C1BFF9782A3B1F072A28D6619E375D9B5F07A23FF3B`；E2E=`617C7481B55A2F7760A36127E5E5DB8C50E193526206D444F13D56AA6F65698F`。
