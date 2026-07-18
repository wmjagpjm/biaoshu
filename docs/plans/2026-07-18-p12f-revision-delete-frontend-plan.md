# P12F-G-B 单条修订删除前端实施计划

> **执行者：Grok**：严格三文件，先只扩展 history E2E 形成真实业务红测，再实现 API 与共用面板；只自测并通过消息箱请求审查，不暂存、不提交、不推送。
>
> **状态：** 2026-07-18 已完成只读审计；契约/计划提交并推送后冻结，冻结提交号以该提交为准。

**目标：** 为技术标/商务标共用修订历史接入 P12F-G-A 单条物理删除，闭合二次确认、唯一 DELETE、成功按已应用条件重载、失败保留和跨项目迟到隔离。

**架构：** API 只做 ID 校验与一次 `apiFetch<void>(DELETE)`；共用面板增加独立删除意图与 generation，复用现有 page/search 第一批加载；E2E 探针以 arrived/complete、状态突变和精确计数证明不可恢复写链及零旁路。

**技术栈：** React、TypeScript、既有 `apiFetch`、Playwright Chromium headless。

## 1. 基线与 failure-first

1. 核对分支、远端、干净工作区与冻结提交；完整阅读 G-A/G-B 契约、API、面板、history E2E 及既有 restore/search/page 迟到模式。
2. 第一阶段只改 `frontend/e2e/editor-state-revision-history.spec.ts`：扩展 DELETE 探针并新增三个独立 P12F-G-B 用例，生产两个文件 SHA-256 保持冻结值。
3. 串行运行 `--grep "P12F-G-B" --workers=1 --retries=0`，记录精确 failed/passed/did-not-run 与首个删除能力缺失；禁止语法/导入/服务/登录错误和 serial 跳过。

## 2. API 实现

1. 文件顶注释加入 delete 对接；新增 `deleteEditorStateRevision(projectId, revisionId): Promise<void>`。
2. 非法 revisionId 零请求；合法路径 URL 编码；init 精确 `{ method: "DELETE" }`，无 body/query/retry/响应 JSON。
3. 不修改既有 parser、共享 `apiFetch`、CSRF、错误映射或任何其它 API。

## 3. 共用面板实现

1. 增加待确认 ID、delete busy、delete generation；项目切换/卸载作废旧代次并清空当前删除 UI。
2. 点击删除先清空 summary/comparison/body-diff/pair/restore 意图，只进入固定内联确认，零 DELETE；取消零副作用。
3. 确认精确一次 DELETE；确认/执行期间除确认与取消规则外，折叠、筛选、搜索、刷新、加载更多和全部行操作真实 disabled，禁止并发或重试。
4. success/catch/finally 严格核对 mounted/session/generation/project；旧 A 不得污染或解锁 B。
5. 成功显示固定文案并复用 `loadList`：普通态第一页 GET，搜索态同条件 POST；失败保留列表且不重载。删除成功后的列表重载失败仍保留成功事实并显示既有列表失败。
6. revisionId 仅内存使用；固定中文文案不反射任何输入/后端 detail；不触发 editor-state、restore、checkpoint 或外网旁路。

## 4. 反假绿审查

1. DELETE 探针必须区分 arrived/complete；hold、HTTP error 与成功数据突变真实发生，不能仅增加计数器。
2. 断言确认前精确零、确认后精确一、query 为空、postData 为 null/空、无重试；成功目标从探针和 DOM 消失，失败探针/DOM保值。
3. 普通页与 search 重载分别检查精确一次、无 cursor、完整已应用条件；第二页删除后只回第一批。
4. A→B 双 gate 证明旧 success/catch/finally 与新 busy 重叠，必须等待 complete 后再断言；disabled 控件不得 `force:true`。
5. 扫描宽状态、`>=`、条件断言、`.or`、固定 sleep、skip/xpass、吞异常、ID/关键词/快照/CSRF 泄漏和超范围文件。

## 5. 串行验收与交付

按契约第 7 节逐条运行 P12F-G-B、完整 history、checkpoint、技术 truth、商务 truth、lint、build；禁止并行 Playwright。Grok 完成后发送 `review_request`，包含红测、精确文件、结果、DELETE/重载/迟到/零旁路证据、哈希、风险和未做项。

Codex 进行受限差异审查，必要时只在三文件内下发最小返修；独立重跑上述门及前端全量，执行 `git diff --check`、白名单、空暂存区、哈希与静态扫描，通过后才中文提交、推送并更新契约/路线图/交接/联调文档。

## 6. 未做

不做批量/范围/软删除、撤销/回收站、自动清理、命名/固定、检查点删除、审计报表、导出/分享、跨项目历史、多人协作、SSE、缓存/离线队列、后端/数据库/共享请求层/依赖/配置变化。
