# P12F-E-B 修订时间范围筛选前端实施计划

> **执行者：Grok**：严格三文件，先形成真实前端业务红测再实现；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成并推送；冻结=`a31e50e`，实现=`f9127ec`，文档闭环见当前 HEAD。

**目标：** 在技术标与商务标共用修订面板中，以浏览器本地时间显式应用单边/双边范围，并安全转换为 P12F-E-A 的 UTC 毫秒 query 与 `esrc3` 稳定分页。

**架构：** API 封装负责严格 UTC query 与 V3 不透明游标外壳；共用面板区分本地草稿和已应用 UTC 条件，以同步 ref/请求代次绑定来源、时间、cursor 与项目；既有 history E2E 扩展真实时区、探针过滤和迟到完成证据。后端保持不变。

**技术栈：** React 19、TypeScript 6、原生 `datetime-local`/`Date`、Playwright Chromium headless。

## 1. 基线与真实红测

1. 核验协作分支、HEAD/远端和干净工作区，阅读 P12F-E-A/B 契约、API 页封装、共用面板及 history 探针。
2. 只修改 `frontend/e2e/editor-state-revision-history.spec.ts`：扩展 page 探针记录 `createdFrom/createdBefore`，按来源与 `[from,before)` 服务端过滤，提供合法 `esrc3` 第二页，并新增至少三项 P12F-E-B 技术/商务场景。
3. 记录两个生产文件 SHA-256 仍为冻结值；运行 `npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-E-B" --workers=1 --retries=0`，确认因控件/API/V3 缺失产生真实业务失败，记录精确 passed/failed/did-not-run 与首个业务失败。

## 2. API 封装

1. 将游标外壳校验扩展为 V1/V2 各自上限 192、V3 上限 256；仍只检查前缀/长度/base64url/无填充，不解码。
2. 给页查询对象加入可空 `createdFrom/createdBefore`；用严格 UTC 毫秒正则、日历规范往返、1970..9999 与双边顺序做发送前校验，错误固定且不含输入。
3. 按 `sourceKind → createdFrom → createdBefore → cursor` 构造 query，保留旧字符串第二参和无时间请求字节兼容；响应 parser 接受合法 V3 nextCursor。
4. 运行 P12F-E-B 聚焦，确认 API 层 query/V3 场景推进，失败只剩面板交互缺口。

## 3. 共用面板

1. 增加开始/结束本地草稿、已应用 UTC 范围及同步 ref；项目切换全部重置，折叠只作废请求而保留当前项目筛选。
2. 实现本地年月日时分严格解析、`Date` 本地字段回验、`toISOString()` UTC 毫秒转换和双边顺序校验；无效固定错误、零请求、当前结果保值。
3. 增加两个 `datetime-local`、应用和清除按钮；应用/清除时同步更新 ref、清旧列表/游标/意图并只取第一页；同值和全空清除不重发。
4. `loadList`、加载更多、刷新和恢复重载同时捕获并校验 source/from/before；第二页显式重复范围；来源切换只读已应用范围，草稿不进入请求。
5. 将时间控件纳入列表/加载更多/恢复在途禁用及项目/session/filter/cursor 迟到隔离，保持手动加载、最多 20 条、失败保值与五意图互斥。

## 4. E2E 完整证据

1. 在 P12F-E-B 场景中固定浏览器 `timezoneId: "Asia/Shanghai"`，以本地 08:00 → UTC 00:00 证明真实时区转换；同时覆盖单边、双边、倒序、清除、来源组合和精确 query 顺序。
2. 证明 V3 第二页条件全量重复、失败同 cursor 重试、首屏/第二页 arrived+complete 迟到隔离、折叠保留、项目切换重置及草稿/已应用分离。
3. 商务标证明共用入口、刷新/恢复保留范围、在途真实禁用、唯一写链和零额外 API；扩展 URL/storage/Cookie/console/DOM 泄漏扫描到时间 query 与 `esrc3`。
4. 清理所有恒真 OR、宽泛计数、固定 sleep、`force:true`、只看 arrived 或 route fallback；聚焦应全部通过。

## 5. 串行回归与交付

1. 依次运行：
   - `npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-E-B" --workers=1 --retries=0`
   - `npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
   - `npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
   - `npx playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
   - `npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
   - `npm run lint`
   - `npm run build`
2. 运行 `git diff --check`、精确三文件白名单、空暂存区和弱断言/跳过扫描；通过消息箱发送 review_request，报告红测、逐项绿测、时区/分页/迟到/泄漏证据、风险和未做项；不得提交推送。
3. Codex 独立审查 UTC/DST 边界、草稿/已应用分离、V3 长度、query 顺序、刷新/恢复条件保留与 E2E 反假绿；只允许三文件内最小返修。
4. Codex 独立重跑聚焦、受影响回归和前端全量；验收后中文提交实现、推送，再更新契约/计划/主交接/路线图/联调清单形成独立文档闭环。

## 6. 未做

不修改后端，不做日期预设、自动提交、防抖、整日快捷键、正文/标题搜索、来源多选、命名/固定/删除、自动加载、跨项目历史、多人协作、SSE、数据库或依赖变更。

## 7. 执行结果

1. Grok 真实红测 **0 passed / 2 failed / 1 did not run**；实现后首轮通过聚焦/history/技术 truth/商务 truth/checkpoint **3/40/28/18/51**，lint/build/diff/白名单通过。
2. Codex 首轮审查不要求生产代码返修，仅限定 E2E 关闭五处宽松计数、V3 257 字符假覆盖、第二页 query 非精确断言和迟到 load-more 可能被项目切换掩盖四类问题；返修后生产哈希不变。
3. Codex 独立复验同五组 **3/40/28/18/51 passed**，lint/build 通过；前端全量首轮暴露冻结范围外既有 Promise.all 双击竞态 **294/1/8**，检查点独立 51/51 后无代码改动完整复验 **303/303 passed（8.3m）**。
4. 冻结=`a31e50e`、实现=`f9127ec`；任务/首轮回执=`msg_e3d1972aa28d442c92382f67e85003b0`/`msg_c322467045704332a69c55bf9d57ee94`，E2E-only 返修 task/review=`msg_aa86d5c6708c4b6fb7d0c7f7e917c5f2`/`msg_5c2808c3069d424c9714b5e7c7915255`，验收=`msg_489249aa6c264cc8a7125f07179b2d36`。
