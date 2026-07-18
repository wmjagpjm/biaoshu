# P12F-F-B 修订可见内容搜索前端契约

模块：P12F-F-B 技术标/商务标共用修订可见内容搜索前端
用途：把 P12F-F-A 已验收的有界 POST 搜索接入共用修订历史面板，以显式、无持久化方式搜索当前筛选下最近 20 条候选。
对接：`editorStateRevisionApi.ts`、`EditorStateRevisionPanel.tsx`、`editor-state-revision-history.spec.ts`、P12F-F-A 后端合同。
状态：2026-07-18 已完成只读审计，当前文档冻结前端三文件边界；Grok 负责 failure-first 实现，Codex 负责独立审查、串行验收、中文文档闭环和协作分支推送。

## 1. 审计结论与方案

现有 API 封装已经具备严格五键元数据 parser、九类来源、UTC 毫秒时间和 `apiFetch` POST/CSRF 能力；共用面板已经具备来源筛选、时间草稿/已应用值、刷新、恢复后重载、折叠/项目切换和 arrived/complete 迟到隔离；既有 history E2E 已有统一双工作区探针。P12F-F-B 不需要 CSS、hook、后端、路由、配置、依赖或新 spec，只改这三个前端文件。

选择“草稿 + 明确搜索/清除搜索”，不做输入即请求或防抖。搜索词不会进入 GET URL；搜索态直接替换当前列表并使用服务端最多 20 条完整结果，不再显示游标“加载更多”。来源与已应用时间继续作为服务端候选过滤条件，避免前端二次过滤造成排序、候选窗或损坏语义偏差。

## 2. API 封装合同

新增独立搜索请求类型与函数，路径精确为：

```text
POST /projects/{projectId}/editor-state-revisions/search
```

请求 body 只允许以下键，并按 `query → sourceKind → createdFrom → createdBefore` 构造：

```json
{
  "query": "关键词",
  "sourceKind": "task",
  "createdFrom": "2026-07-16T00:00:00.000Z",
  "createdBefore": "2026-07-17T00:00:00.000Z"
}
```

- `query` 必填且原样发送，不得 `trim/lower` 或编码进 URL；可选来源/时间仅在非空时进入 body。禁止 cursor/limit/offset/page/search/q/snippet/total/hasMore/snapshot。
- API 层做脱敏防御性校验：query 必须原生字符串、原值首尾无空白、无 C0/C1/DEL，NFKC 后 1..64 个 Unicode 码点；来源必须九类之一；时间复用现有精确 UTC 毫秒和 `from < before` 校验。错误只能抛固定内部错误名，不带输入、body、URL 或后端 detail。
- POST 必须无 query string；只通过 `apiFetch` 发送 `JSON.stringify(body)`，由既有 required Cookie/CSRF 链处理。不得新增直接 `fetch`、重试、轮询或日志。
- 新 parser 顶层精确 `{items}`，每项复用精确五键 `revisionId/stateVersion/snapshotBytes/sourceKind/createdAt`；items 最多 20、revisionId 唯一、保持服务端顺序。禁止接受 nextCursor/total/query/snippet/matchedFields/score/projectId/snapshot 或多余项键。
- 旧 list/page/detail/restore/comparison/body-diff/pair 类型、parser、路径、上限和错误保持字节兼容；不得把旧 list 的 10 条上限或 page 的 10 条/游标合同改成 20。

## 3. 面板交互与状态合同

### 3.1 控件与验证

- 面板仍默认折叠且零修订请求。展开后在既有来源/时间控件旁增加固定“内容搜索”标签、文本输入、明确“搜索”和“清除搜索”按钮；不得增加搜索历史、建议、自动完成或浏览器持久化。
- 固定测试标识：`editor-state-revision-search-input`、`editor-state-revision-search-apply`、`editor-state-revision-search-clear`、`editor-state-revision-search-error`、`editor-state-revision-search-active`。
- 搜索草稿与已应用关键词分离，均只存 React 内存；输入/删除字符不发请求。点击“搜索”（以及输入框 Enter 的等价显式动作）才校验并应用。
- 前端校验必须与 API/后端一致：不静默 trim；拒绝空串/全空白、首尾空白、C0/C1/DEL 及 NFKC 后 0 或超过 64 个 Unicode 码点。非法时固定显示“搜索关键词需为 1 至 64 个字符，且不能含首尾空白或控制字符”，零请求，保留当前列表和已应用搜索，不反射原值。
- 同一已应用关键词再次搜索不重发；网络/服务失败后的同条件重试使用既有“刷新”。“清除搜索”同时清草稿、已应用关键词和搜索校验错误；本来全空时零请求，否则保留来源/已应用时间并恢复 page 第一页。

### 3.2 搜索态、组合筛选与重载

- `loadList` 统一读取当前已应用关键词、来源和已应用 UTC 时间：无关键词走既有 page GET；有关键词走新 search POST。禁止先 page 再客户端过滤、详情 N+1、把 cursor 传给 search 或用搜索响应补 page。
- 有效新关键词应用前同步更新 ref/state，清空旧 items/cursor/错误/摘要/比较/正文差异/双修订选择/恢复确认，再发一次 POST。成功直接按服务端顺序展示 0..20 条，`nextCursor` 固定清空；搜索态永不显示“加载更多”。
- 搜索空结果固定显示“未找到匹配修订”，不得回退未筛选列表；普通 page 空态继续“暂无修订记录”。搜索失败固定显示“修订内容搜索失败，请稍后重试”，列表为空且已应用关键词保留，用户可刷新重试；不得显示后端 detail、关键词或请求体。
- 搜索态切换来源、应用/清除时间、刷新、恢复成功或 editor-state 重载失败后的历史重载，都必须保留已应用关键词并重新 POST；这些入口不得退回 page GET。
- 折叠再展开保留同项目内的搜索草稿、已应用关键词、来源和已应用时间，并重新 POST；项目切换必须清空搜索草稿/已应用值/错误，保持默认折叠，新项目首次展开走无搜索 page GET。
- 列表、加载更多或恢复在途时，搜索输入/搜索/清除与来源/时间控件一起真实 disabled。搜索在途沿用 `listLoading`；不得并发 page/search/load-more/restore。

### 3.3 迟到隔离

- `loadList` 必须在发请求前捕获 applied query/source/from/before，并在 success/catch/finally 同时核对 mounted、session 及四个 ref；任何一个变化都不得写 items/error/loading/cursor。
- 折叠、卸载、项目切换、来源/时间/搜索切换、刷新和恢复重载必须作废旧 page/search 结果。旧搜索的 success/catch/finally 不得污染同项目重开或新项目，也不得清除新请求 loading。
- 搜索结果中的摘要、当前对比、单/双修订正文差异和恢复继续使用现有按 revisionId/代次语义；搜索入口不得把 ID、版本或关键词渲染到列表元数据之外的新位置。

## 4. 隐私、兼容与禁区

- 搜索词只可存在于当前输入控件值、组件内存、API 调用栈和一次 POST body。不得进入页面 URL、GET query、响应回显、固定错误/状态/空态文案、console、localStorage、sessionStorage、Cookie、剪贴板、下载、审计模拟数据或其它请求。
- 清除搜索、项目切换和组件卸载后不得由存储恢复关键词；浏览器刷新后自然丢失。折叠保留仅限同一组件实例内存，不是持久化。
- 技术标与商务标必须共享同一控件、parser、错误、组合筛选、重载和迟到隔离语义；不得复制两套实现或只给技术标接入口。
- 本包不改 P12F-F-A 后端，不做自动搜索/防抖、片段/高亮/命中字段/分数、搜索历史、缓存、游标搜索、跨项目搜索、来源多选、日期预设、命名/固定/删除、导出/分享、多人协作、SSE、CSS 重做或移动端重构。

## 5. 三文件白名单与冻结哈希

Grok 只允许修改：

1. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
2. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
3. `frontend/e2e/editor-state-revision-history.spec.ts`

冻结 SHA-256：

- API：`DD49CC4D53389C3760797CDA8D87536131DAF12671AEF1F642EAADFC09372375`
- 面板：`1F29D4FB0A9A840B954963CC51D8176DC254E6D4EBFC4C02B4C52C2D0F2546D9`
- history E2E：`AB27FE3E1DEB0CD8A3BD8AAF5DDB8CDD0F6DE0D6517CEB1F28B0FDC1B45B23C7`

禁止修改后端、其它前端/测试、CSS、hook、package.json、Playwright 配置、依赖/锁文件、文档或 Git 历史。Grok 不得 `git add/commit/push`。

## 6. Failure-first 与验收门

Grok 必须先只修改 history E2E，增加三个互不因 serial 首失败而跳过的 P12F-F-B 用例；两个生产文件哈希保持冻结值。真实红测必须来自搜索控件/API/状态不存在，不能用路由探针缺失、语法、收集、fixture、服务启动或宽泛超时冒充。

三个用例至少覆盖：

1. 技术标显式搜索：输入零请求、非法零请求保值、有效 POST 无 URL query/精确 body、严格五键/唯一/最多 20 parser、空态/失败/刷新/清除、搜索态无加载更多；
2. 技术标组合与迟到：来源+单/双边时间 body、来源/时间变化保持 query、折叠保留、项目切换重置、search arrived/complete gate 及旧 success/catch/finally 零污染；
3. 商务标共享与恢复：同一入口、组合条件、搜索结果现有操作可用、恢复成功/重载失败后仍 search POST、项目重置和 URL/存储/Cookie/console/其它请求零关键词泄漏。

所有请求计数必须用操作前基线精确 `+1` 或精确 0；不得用 `toBeGreaterThan`、宽状态、`.or`、`force:true`、`waitForTimeout`、skip/fixme/xfail、secret marker 缺席或 Promise.all 假双击制造通过。网络探针必须精确区分 search POST、page GET、旧 list、detail/restore 与 forbiddenHits。

Grok 至少串行运行：

- P12F-F-B 聚焦三个用例；
- 完整 history E2E（现有基线 40，新增后应为 43）；
- 技术 editor-state truth 28；商务 truth 18；checkpoint restore 51；
- P12F-F-A 后端专项 23；
- `npm run lint`、`npm run build`；
- 前端全量 E2E（现有基线 303，新增后应为 306）；
- `git diff --check`、精确三文件、空暂存区和弱断言/禁区扫描。

所有 Playwright 命令必须显式 `--workers=1 --retries=0`，禁止 xdist/并行。Codex 独立审查和重跑后才可提交。
