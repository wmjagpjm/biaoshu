# P12F-F-B 修订可见内容搜索前端实施计划

> **执行者：Grok**：严格三文件，先只补 history E2E 形成真实业务红测再实现；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成只读审计，当前文档即冻结边界。

**目标：** 在技术标/商务标共用修订面板中，以显式、内存态、无 URL 泄漏方式调用 P12F-F-A POST 搜索，并与来源/时间/刷新/恢复/折叠/项目切换语义闭合。

**架构：** API 新增严格 query/body/response parser；面板新增搜索草稿与已应用值，由统一 `loadList` 在 page GET 与 search POST 间二选一；history E2E 扩展统一探针和三个前端场景。

**技术栈：** React、TypeScript、现有 `apiFetch`、Playwright、既有双工作区 E2E 探针。

## 1. 基线与真实红测

1. 核对分支/远端、干净工作区、P12F-F-A 实现 `e6516e8` 和三文件冻结哈希；阅读 API/parser、面板 state/ref/loadList/render、history route/probe 及 P12F-C/D/E-B 迟到真值。
2. 第一阶段只改 `frontend/e2e/editor-state-revision-history.spec.ts`：增加 search arrived/complete、mode/override/body 探针和三个 P12F-F-B 场景；探针路由必须实现冻结后端合同，不能因探针仍 404/forbidden 形成假红。
3. 两个生产文件哈希仍为冻结值；分别或以非 serial 跳过方式运行三个聚焦测试，记录精确 failed/passed/did-not-run 与首个真实 UI/API 失败。

## 2. API 封装

1. 更新文件顶四字段为 P12F-F-B；新增搜索请求类型、query 校验 helper、精确 `{items}`/最多 20/ID 唯一 parser。
2. 新增 `searchEditorStateRevisions(projectId, query)`：原样 query，复用来源/UTC 时间校验，body 只含允许键；`apiFetch` 单次 POST，无 URL query、cursor、重试或日志。
3. 保持旧 list/page 的 10 条上限、page 游标、详情/恢复/差异 parser 字节兼容；不得为了复用把全局 `MAX_LIST_ITEMS` 改为 20。

## 3. 共用面板

1. 新增 searchDraft/appliedSearch/searchError state 与 appliedSearchRef；项目切换清空，折叠保留，卸载不持久化。
2. 增加固定标签、输入、搜索/清除按钮、固定校验错误和搜索态提示；输入零请求，按钮/Enter 显式应用，不 trim、不反射。
3. 改造 `loadList`：捕获 query/source/from/before；query 非空走 search POST 并清 cursor，空则保持 page GET；success/catch/finally 四条件迟到校验完整。
4. 来源/时间/刷新/恢复重载继续调用统一 `loadList`；搜索态隐藏加载更多，空态/失败使用搜索专用固定中文；新搜索/清除同步清理旧列表与互斥意图。
5. 搜索控件与现有筛选一起受 list/load-more/restore 在途禁用；搜索结果继续复用摘要/比较/body-diff/pair/restore，不新增 ID/版本/关键词显示。

## 4. E2E 反假绿

1. ProbeState 新增精确 searchLog/searchCompleteLog、按项目/关键词 mode、response override；search 路由必须先于旧 list/detail 匹配，精确记录 method/path/search/query keys/postData/解析 body。
2. 后端探针按元数据顺序返回最多 20 条或显式 override；非法方法/query/body/额外键进入 forbiddenHits 或固定错误。测试不能用客户端自造过滤证明后端语义。
3. 用 gate 分离 arrived/complete，覆盖折叠重开与项目切换；释放旧请求后精确断言当前 items/error/loading 不变。所有计数用基线精确增量。
4. 明确检查 query 不在 URL、页面文本（输入控件值除外）、固定错误/空态、local/session/cookie/console/其它请求；清除和项目切换后输入值为空。

## 5. 串行验收与交付

所有 Playwright 显式 `--workers=1 --retries=0`：

1. `npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-F-B" --workers=1 --retries=0`
2. `npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
3. `npx playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
4. `npx playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
5. `npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
6. 后端：`.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_content_search.py`
7. `npm run lint`；`npm run build`
8. `npx playwright test --workers=1 --retries=0`
9. `git diff --check`、精确三文件、空暂存区、禁区与弱断言扫描。

Grok 通过消息箱发 review_request，报告真实红测、三个场景、精确 body/计数/迟到/泄漏证据、每组结果、最终哈希和未做项；不得暂存、提交或推送。Codex 独立审查后只允许三文件内最小返修，最终独立重跑、中文提交、推送和文档闭环。

## 6. 未做

不改后端、CSS、hook、配置、依赖或其它测试；不做自动搜索/防抖、片段/高亮/分数、搜索历史、缓存、游标搜索、跨项目搜索、来源多选、日期预设、命名/固定/删除、导出/分享、多人协作、SSE 或移动端重构。
