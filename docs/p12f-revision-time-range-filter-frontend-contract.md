# P12F-E-B 修订时间范围筛选前端契约

模块：P12F-E-B 双工作区修订历史时间范围筛选前端
用途：在 P12F-E-A 严格 UTC 时间范围后端之上，为技术标与商务标共用修订面板增加本地时间输入、显式应用/清除、来源组合与稳定分页。
对接：`editorStateRevisionApi`、`EditorStateRevisionPanel`、既有 `editor-state-revision-history.spec.ts`、P12F-E-A `createdFrom/createdBefore/esrc3` 合同。
状态：2026-07-18 已完成只读审计，当前文档即冻结边界；Grok 严格三文件 failure-first 实现，Codex 负责独立审查、串行 E2E 验收、中文文档闭环和协作分支推送。

## 1. 审计与方案选择

现有共用面板已用内存 state/ref 绑定 `sourceKind`、`nextCursor` 与首屏/加载更多请求，刷新、恢复、折叠、项目切换和迟到响应已有统一入口；API 封装仍只接受 `esrc1/esrc2` 且游标上限固定 192。P12F-E-B 无需新增页面、hook、后端、CSS、依赖或浏览器存储，只需扩展同三个前端文件。

比较过三种交互：输入变化立即请求会在只填完一个字段、清空或逐字编辑时产生临时结果集；只提供“今天/近七天”等预设无法表达任意单边范围；采用“本地时间草稿 + 明确应用/清除”既允许单边条件，又能在一次确定动作中原子切换结果集。因此本包采用第三种，不做自动提交、防抖、预设或日期库。

## 2. API 封装合同

`EditorStateRevisionPageQuery` 只新增：

```ts
createdFrom?: string | null;
createdBefore?: string | null;
```

规则：

- 两个值进入 API 前必须已是精确 24 字符 ASCII UTC 毫秒格式 `YYYY-MM-DDTHH:MM:SS.sssZ`，合法日历且在 1970 至 9999 闭区间；双边必须严格 `createdFrom < createdBefore`；空串、空白、偏移、非三位毫秒和非法范围在发请求前固定抛内部脱敏错误；
- query 顺序固定为 `sourceKind`、`createdFrom`、`createdBefore`、`cursor`，只发送存在的非空值；无 body，不引入 `dateFrom/dateTo/start/end/limit/offset/page/search/q`；
- `isValidPageCursor`/页 parser 增加不透明 `esrc3_` 外壳，V3 总长最多 256；V1/V2 仍最多 192。前端禁止解码、生成或从游标采用来源/时间条件；
- 首屏无条件仍无 query；仅来源、仅单边时间、双边时间、来源+时间及其第二页均精确构造。服务端继续负责 V1/V2/V3 版本与显式条件的最终绑定；
- 旧字符串第二参及既有无时间四组合保持兼容；响应 parser、条数、去重和失败脱敏合同不变。

## 3. 本地时间输入与校验

面板筛选区新增：

```text
editor-state-revision-created-from       开始时间（含）
editor-state-revision-created-before     结束时间（不含）
editor-state-revision-time-apply          应用时间
editor-state-revision-time-clear          清除时间
editor-state-revision-time-error          时间范围无效，请检查开始和结束时间
```

- 两个输入均为 `datetime-local`、分钟步长；值按浏览器本地时区解释。实现须严格解析本地年月日时分，构造本地 `Date` 后逐字段回验，拒绝不存在日期、DST 归一化、越界或转换后非四位 UTC 年；合法值用 `toISOString()` 形成精确 UTC 毫秒；不得手工拼接 `Z` 或把本地字面量当 UTC；
- 允许只填开始或只填结束；双边转换后必须严格开始早于结束。无效草稿显示上述固定中文错误、发送零 page 请求、保留当前已应用条件、列表、游标和意图；编辑任一输入清除旧校验错误；
- “应用时间”至少一个草稿非空时可用；同一规范范围重复应用不得重发。合法新范围先同步更新已应用 ref，再清空旧列表/游标/摘要/比较/正文差异/pair/恢复确认并只取新第一页；失败显示既有列表失败文案，不回退旧范围结果；
- “清除时间”清空草稿、已应用范围及错误；原本无草稿且无已应用范围时不重发，否则保留当前来源并只取无时间条件第一页；
- 草稿与已应用范围分离。来源切换、刷新、恢复后重载和加载更多只能读取已应用 UTC 条件，禁止采用尚未应用的草稿。

## 4. 组合、分页与生命周期

1. 默认首次展开仍精确一次无 query 页 GET；应用时间后第一页按当前来源组合发送 `createdFrom/createdBefore`，不带 cursor；来源切换保留已应用时间，时间应用/清除保留当前来源。
2. 时间范围激活且 `nextCursor` 非空时，第二页必须显式重复同一 `sourceKind`（若有）、`createdFrom`、`createdBefore`，并原样回传服务端 `esrc3`；成功追加、失败保值可重试、同步单飞、最多 20 条及禁止自动预取合同不变。
3. 列表、加载更多或恢复在途时，来源选择、两个时间输入、应用和清除均真实 disabled；不得用 `force:true` 构造不可达切换。
4. 应用/清除时间与来源切换一样作废在途详情、当前比较、单/双正文差异、双侧选择、恢复确认和加载更多；旧 success/catch/finally 不得写新范围状态。
5. 刷新、恢复成功后的重载及恢复成功但编辑态刷新失败后的历史重载，均保留已应用来源和时间；折叠再展开保留同项目草稿与已应用范围；`projectId` 变化重置来源、草稿、已应用范围和错误。
6. 请求有效性必须同时绑定 project/session、来源、开始、结束、cursor 与请求代次；P12F-E-B E2E 必须分别证明首屏与加载更多 arrived+complete 迟到结果不污染新会话。

## 5. 数据最小化与明确禁区

- 本地时间草稿、UTC 条件与 `esrc3` 只允许存在于组件内存、输入值和规定 API query；不得写应用 URL、localStorage、sessionStorage、Cookie、console、剪贴板或下载。
- DOM 只可显示用户自己输入的本地时间与固定中文标签/错误；不得渲染 UTC query、游标、revision ID、stateVersion、快照正文、后端 detail、路径或异常原文。
- 不新增外网、依赖、日期库、计时器、防抖、自动轮询、自动加载、预取、缓存、AbortController 唯一隔离证据或第二套技术/商务实现。
- 本包不做后端修改、日期预设、整日快捷键、正文/标题搜索、来源多选、命名、固定、删除、导出、分享、跨项目历史、多人协作、SSE 扩展或数据库变更。

## 6. 三文件白名单

Grok 只允许修改：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止修改后端、任何其他前端文件、既有测试文件、共享 `apiFetch`、workspace/hook、配置、依赖/锁文件、文档或 Git 历史。Grok 不得 `git add/commit/push`。

冻结前三文件 SHA-256：

- API 封装：`E4C5590FD76A754F7589DA5E330F2CF3E4A2F35DE540BB4003869BEC7AC6F5D7`
- 共用面板：`7C925E3AA7E71B09EDAB70F674488DA08D3D2BAA5619782E1C8147B42B7E6363`
- history E2E：`382C5919A13A815706707109020BF0EE0C9C18EE75CCCADE6158A89743400182`

## 7. Failure-first 与验收门

Grok 必须先只修改 history E2E，生产两文件哈希保持冻结值；新增至少三项 P12F-E-B 场景并运行聚焦命令。真实红测应来自日期控件不存在、API 不发送时间 query 或拒绝 `esrc3`，收集、TypeScript、fixture、浏览器/服务启动或语法错误不算红测。

E2E 至少覆盖：

1. 技术标默认零时间 query；`Asia/Shanghai` 下本地 `08:00` 精确转 UTC `00:00:00.000Z`；单边/双边、来源组合、固定 query 顺序、同值零重发、无效/倒序零请求保值、清除时间；
2. 时间结果服务端过滤、空态、首屏失败不回退、V3 外壳/256 上限、第二页显式重复来源/时间并原样回传 `esrc3`、失败保值同 cursor 重试、最多 20 条；
3. 应用新范围清摘要/比较/正文差异/pair/恢复确认；草稿不影响刷新/来源；折叠保留、项目切换重置；首屏与加载更多 arrived+complete 迟到隔离；
4. 商务标共享同一入口；刷新/恢复保留来源+时间；恢复/加载在途控件真实禁用；唯一 restore、唯一 editor-state GET、只重载筛选第一页；
5. history 旧 37 项不回归；零旧 list、零额外 API、零外网、零 URL/存储/Cookie/console/DOM 敏感泄漏；
6. 禁止固定 sleep、`.or(...)`、宽泛 2xx/计数、只等 arrived、条件跳过关键断言、`force:true` 或 route fallback 冒充成功。

Grok 至少依次运行：P12F-E-B 聚焦；完整 history；技术 truth；商务 truth；checkpoint restore；`npm run lint`；`npm run build`；`git diff --check`；精确三文件与空暂存区。所有 Playwright 显式 `--workers=1 --retries=0` 串行。前端全量由 Codex 独立执行。
