<!--
模块：P12B-C editor-state 延迟写入围栏契约
用途：冻结后台任务、商务修订、本地解析回调与 M3-D 对当前权威编辑态的版本绑定和迟到写拒绝语义。
对接：P12B-A 全状态版本；P12B-B 浏览器 CAS；P8C；M3-D；后续 P12B-D 原子恢复。
二次开发：本包只封住既有写入者，不新增恢复、历史、强制覆盖、自动重试或外部服务。
-->

# P12B-C editor-state 延迟写入围栏契约

> **状态**：契约已冻结，等待 Grok 分三批受限实现与 Codex 独立验收。

## 1. 目标与完成定义

P12B-A 已提供 13 键规范 `stateVersion` 与服务端 CAS，P12B-B 已让技术标和商务标普通浏览器 PUT 携带版本；但以下既有路径仍可绕过 CAS，因而可能在检查点恢复完成后把旧结果重新写回：

1. `task_service` 的 parse/analyze/outline/chapter/chapters 与 `business_task_service` 四类商务生成任务；
2. `revise_service` 的 `business_parse` 与四类商务结构化修订；
3. disabled 个人兼容 `parse-callback`，以及 required 模式 P8C 一次性票据回调；
4. M3-D `content-fuse-applications` 的确认写入与一次恢复。

完成 P12B-C 必须保证：每条上述写入在开始时绑定一个服务端权威全状态版本，最终写入在同一数据库事务的锁后比较该版本；不匹配时整次迟到写零落库。三批全部验收前，P12B-D restore API/按钮继续禁止实现。

## 2. 共用版本原语与固定错误

1. `editor_state_service` 增加供既有服务复用的公开锁后校验原语：取得与 P12B-A 相同的项目级数据库锁，只读一次 editor-state ORM 行，以同一 `_state_from_row` 规范视图计算当前版本，并比较 `expectedStateVersion`。禁止复制 13 键算法、信任 `updatedAt`、客户端正文、任务结果或缓存。
2. 原语不自行提交；调用方负责把“版本比较、业务字段写入、伴随任务/项目/批次/审计写入”放在同一事务，并在任何异常时 rollback。
3. 登录态 API 的陈旧版本统一 HTTP 409，`detail.code=editor_state_version_conflict`、固定中文 message，并仅在既有 P12B-A 兼容路径返回合法 `currentStateVersion`；不得返回正文、任务 payload、票据、项目/批次 ID、路径、SQL 或异常原文。
4. 公共 P8C 回调不返回当前版本，使用 HTTP 409、`code=local_parser_state_version_conflict`、固定 message“编辑内容已变化，请重新签发回传票据后重试”。一次有效票据的首次合法回调即使版本已陈旧也必须被消费，禁止回滚后等待状态哈希碰巧回到旧值再复用。
5. 任务不把版本或异常类型暴露到任务响应；版本冲突时终态精确为 `failed`、progress=100、message=`任务结果已过期`、error=`任务基于的编辑内容已变化，请重新载入后重试`，`result` 为空。禁止自动重试、静默重建任务或改用当前版本强写。

## 3. C1：后台任务与商务 revise

### 3.1 任务创建与 worker

1. 仅对真实 editor-state 写入任务 `parse|analyze|outline|chapter|chapters|biz_qualify|biz_toc|biz_quote|biz_commit`，在 `create_task_record` 创建事务内由服务端读取当前权威 `stateVersion`，覆盖写入 `payload_json` 的保留内部键。客户端同名投稿必须被服务端值覆盖；该键不得出现在 `task_to_dict`、SSE、日志、错误或审计中。
2. `export|response_match|content_fuse` 不写 editor-state，不得为满足测试伪造 CAS 写入；它们保持既有语义。
3. parse 必须删除直接 ORM 覆盖，改走带 expected 的 `upsert_editor_state`。analyze、outline、chapter 与四类商务任务都用创建时版本执行最终写入。
4. `chapters` 每次成功写一章后，必须从该次 `upsert_editor_state` 成功响应取得新 `stateVersion`，仅供本任务下一章推进 expected；若章间发生任何其他 editor-state 改动，下一章写入失败，已成功章保持，外部改动不得被覆盖。
5. 任务创建后发生并发写允许令任务保守失败；禁止为了提高成功率在 worker 启动或提交前重新捕获当前版本。

### 3.2 商务 revise

1. `ReviseIn.expectedStateVersion` 为合法 `esv_` 格式；仅 `business_parse|business_qualify|business_toc|business_quote|business_commit` 这些会写 editor-state 的 stage 强制要求，其他只返回预览的技术修订保持兼容。
2. 商务前端在既有 `saveChainRef` 中执行 revise：先等待旧保存，再在真正执行时读取最新内存版本并投稿；成功响应必须携带新的合法 `stateVersion`。
3. revise 成功后在同一队列内阻断后续旧本地写，强制重读 editor-state；重读成功才解除阻断。409、200 缺/非法版本或网络结果不确定时都禁止自动覆盖，保留本地内容并要求显式重载。
4. 服务端 LLM 调用期间不得持有 SQLite 写锁；最终写入用请求 expected 在锁后 CAS。冲突时不写商务字段，响应 409，禁止返回已经生成的模型正文。

## 4. C2：个人 callback 与 P8C 票据

### 4.1 disabled 个人兼容回调

1. `ParseCallbackIn` 必须接收合法 `expectedStateVersion`；缺失或格式非法固定 422，零写。
2. 旧回调把 CAS、`parsed_markdown`、成功 parse task、项目 status/step 更新时间放在一个事务；不得先调用会自行 commit 的 `upsert_editor_state` 再补任务或项目。
3. 陈旧 expected 固定登录态 409，editor-state、任务与项目步骤全部不变。成功响应增加合法 `stateVersion`。
4. disabled 页面在显式提交前读取当前项目 editor-state 取得服务端版本，再 POST；curl 示例必须明确带 `expectedStateVersion` 占位，禁止本地计算、持久化或输出真实版本到 console/URL/Cookie。

### 4.2 P8C 一次性票据

1. 签发时服务端读取当前权威版本并写入票据行 `expected_state_version`；原始票据仍只返回一次，库内仍仅存 SHA-256 摘要，版本不是客户端投稿。
2. SQLite 旧库通过幂等轻量加列升级；迁移列允许旧行为空，但新签发行必须非空且格式合法。升级前遗留的空版本票据不得写 editor-state。
3. 合法公共回调先原子消费票据，再在同一写事务中锁后比较票据版本；匹配时才原子写解析结果、成功任务、项目步骤与固定审计。
4. 版本冲突时解析正文、任务和项目步骤零写，但票据消费必须提交；再次使用同票据固定 401。异常中途失败继续维持既有完整 rollback 语义，不得误把实现异常当已消费成功。
5. 公共请求体仍只允许 `markdown/source/filename`，不得让外部助手投稿版本；MinerU/Docling 助手、header、路径、2 MiB 上限与不重试规则不变。

## 5. C3：M3-D 确认与恢复

1. `ContentFuseApplicationCreate` 新增强制 camelCase `expectedStateVersion`；consume 从无 body 改为仅接受强制 `expectedStateVersion` 的 `extra=forbid` 请求体。snake_case、缺失、非法格式或额外键均 422。
2. apply/consume 在既有项目级数据库锁后用共用原语先比较全状态版本，再执行原有 task/suggestion/chapter base、after 漂移校验。全状态冲突优先，整次章节、批次创建/消费均零写。
3. 原有章节 base/after 校验不得放宽：全状态版本匹配也不能绕过章节精确校验；consume 的 0/部分/全部恢复仍一次消费，前提是请求 expected 匹配。
4. apply/consume 成功响应增加合法新 `stateVersion`；零章恢复时版本应等于操作前当前 editor-state 版本，批次消费本身不进入 13 键哈希。
5. 技术主 hook 提供受限“版本化外部写”队列，M3-D apply/consume 必须进入与普通 editor-state PUT、矩阵合并相同的 `matrixSaveChainRef`。真正执行时读取最新 expected，成功校验响应版本，并在随后的唯一重读完成前阻断自动保存。
6. M3-D POST 若 409、响应缺/非法版本或网络结果不确定，一律保守阻断；不得自动重试、拿当前版本重发、静默强制覆盖或让旧本地 UI 带新版本自动保存。重读失败保留本地 UI 与阻断，用户只能显式重载。

## 6. 数据、兼容与安全边界

- 仅新增 P8C 票据的单个版本列；不新增版本历史表、触发器、恢复记录、自动检查点、通用任务快照或正文审计。
- 不修改 P12A 13 键、2 MiB/20 条/只读详情边界，不实现 restore/delete/download。
- 不改变 P8D/P8E 助手请求体、真实 CLI/模型部署或代理策略；不启动外部解析器。
- 不让客户端为任务或 P8C 票据选择基准版本；只在个人 callback、商务 revise、M3-D 这些登录态显式请求中接受 expected。
- 不把版本写入 localStorage/sessionStorage/IndexedDB/URL/Cookie/剪贴板/console；任务内部版本不得通过 API 泄露。
- 所有 PowerShell 后台静默；Playwright 仅 Chromium headless、workers=1，严格串行，共享 SQLite 时禁止并行测试进程。

## 7. 三批实现白名单

### C1 白名单

1. `backend/app/services/editor_state_service.py`
2. `backend/app/services/task_service.py`
3. `backend/app/services/business_task_service.py`
4. `backend/app/services/revise_service.py`
5. `backend/app/api/revise.py`
6. `backend/app/api/schemas.py`
7. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
8. `backend/tests/test_p12b_delayed_writer_fences.py`（新增）
9. `frontend/e2e/p12b-delayed-writer-fences.spec.ts`（新增）

### C2 白名单

1. `backend/app/models/entities.py`
2. `backend/app/core/database.py`
3. `backend/app/api/parse_callback.py`
4. `backend/app/services/local_parser_ticket_service.py`
5. `frontend/src/features/local-parser/pages/LocalParserPage.tsx`
6. `backend/tests/test_local_parser_callback_tickets.py`
7. `backend/tests/test_async_and_callback.py`
8. `frontend/e2e/local-parser-callback-ticket.spec.ts`
9. `backend/tests/test_p12b_delayed_writer_fences.py`
10. `frontend/e2e/p12b-delayed-writer-fences.spec.ts`

### C3 白名单

1. `backend/app/api/schemas.py`
2. `backend/app/api/content_fuse_applications.py`
3. `backend/app/services/content_fuse_application_service.py`
4. `frontend/src/features/technical-plan/lib/contentFuseApplications.ts`
5. `frontend/src/features/technical-plan/components/ContentFuseDialog.tsx`
6. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
7. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
8. `backend/tests/test_content_fuse_applications.py`
9. `backend/tests/test_p12b_delayed_writer_fences.py`
10. `frontend/e2e/content-fuse-apply.spec.ts`
11. `frontend/e2e/content-fuse-persistent-recovery.spec.ts`
12. `frontend/e2e/p12b-delayed-writer-fences.spec.ts`

任何新增白名单必须由 Codex 根据真实失败证据另行授权。Grok 不得修改文档、依赖、配置、迁移框架、其他测试或 Git 历史，不得 commit/push。

## 8. 反假绿验收

新增专项必须先真实失败、后实现；测试文件时间和 Grok 回执要说明 failure-first 证据。至少精确证明：

1. 任务内部版本不出现在 REST/SSE；创建后改状态再运行会 failed 且零覆盖；当前版本成功；批量章节自推进版本且章间外部改动不被覆盖。
2. 商务 revise 第二个队列操作使用第一个成功响应版本；陈旧/缺失 expected 零写；LLM 期间并发改动真实触发 409；200 缺版本或不确定失败后零自动 PUT。
3. 旧个人 callback 缺/坏版本 422、陈旧 409、任务/项目/state 原子零写，成功返回新版本。
4. P8C 票据行绑定签发时版本；签发后变更状态再回调固定 409、票据已消费、正文/任务/项目零写；新票据成功；旧空版本票据不能写；中途异常仍完整 rollback。
5. M3-D 在章节 base 未变但任一其他 13 键变化时仍 409；apply/consume 冲突均不创建/消费批次；当前 expected 成功并返回独立计算一致的新版本；章节原漂移规则继续通过。
6. M3-D 前端两个 POST 的 expected 精确来自队列最新服务端版本；成功后只重读一次；网络不确定或非法成功版本时停止自动保存并保留本地内容。
7. 禁止 `or True`、宽泛状态码集合、`.or(...)` 备用选择器、只测 helper 不走真实 API/数据库、顺序调用冒充并发、捕获忽略、客户端自报当前版本或修改旧断言迎合实现。

最终独立验收必须包含新增后端专项、task/revise/callback/M3-D 受影响回归、后端全量；新增前端专项、P11B/P11C、P8C、M3-D、矩阵回归、lint/build 与单 worker 全量 E2E；并执行工作树和暂存区 `git diff --check`。

## 9. 提交门

每批 Grok 只发 `review_request`，附精确文件、测试命令/计数、failure-first 证据和已知限制。Codex 独立读差异、受限返修、独立测试后才可中文提交并推送。C1/C2/C3 任一未完成时，路线图只能写“P12B-C 部分完成”，不得声称安全恢复门已闭合。
