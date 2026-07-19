# P12I 检查点名称与可见内容显式搜索实施计划

> **状态：2026-07-19 已完成并推送。** 冻结=`86cc1a3`，实现=`8c41bbc`，Grok 最终 review_request=`msg_2a430c560a4d415d881a4fd58911ad9d`，Codex 验收回执=`msg_608e5dda4d59453b83ab068ce9879fbf`。
>
> **执行者：Grok**：严格六文件；先只改两个测试文件形成真实 failure-first，再改四个生产文件；所有 pytest/Playwright 逐条串行；只通过消息箱请求审查，不暂存、不提交、不推送。

**目标：** 在当前项目最多 20 条检查点内提供一次显式名称或可见内容搜索，返回既有七键元数据，并在技术标/商务标共用面板中保持创建、恢复、命名、删除与项目切换的真实互斥和迟到隔离。

**权威契约：** `docs/p12i-checkpoint-search-contract.md`。本计划只说明顺序，发生歧义时以契约为准。

## 任务 1：确认基线与冻结门

1. 核对分支为 `collab/grok-code-codex-review`，HEAD/上游/远端均为包含本计划的冻结提交，工作区干净。
2. 核对六文件及四个既有文件冻结哈希；新后端专项不存在。
3. 阅读 P12I 契约、P12G/P12H 契约、P12F-I 联合搜索契约和六个白名单文件；不得把修订 API/面板代码复制进白名单外文件。

## 任务 2：只写 failure-first

1. 新增 `backend/tests/test_p12i_checkpoint_search.py`，先用真实 ASGI/SQLite 证明合法 POST 仍为 405；覆盖精确请求、候选/校验/匹配、作用域、错误与零写。
2. 只在 `frontend/e2e/editor-state-checkpoint-restore.spec.ts` 增加 P12I 场景，先证明技术标和商务标页面不存在搜索入口。
3. 运行两条聚焦命令并记录真实失败/通过数量；收集、登录、服务未启动、语法错误或 skip 不算有效红测。
4. 记录四个生产文件哈希未变后，才可进入实现。

## 任务 3：实现后端有界搜索

1. 在 checkpoint service 中增加 query 规范化、NFKC+casefold、可见内容白名单提取、对象/字符串预算、存储名称校验和搜索函数。
2. 一次 SQL 投影八列，workspace/project 限定、倒序、LIMIT 20；完整物化并逐条重验后再匹配。
3. 在 checkpoint 路由增加精确 body 外壳与 POST search；复用既有列表响应模型，固定 request/query/corrupt 错误和 no-store。
4. 禁止任何写事务、模型/Schema/迁移、修订服务或共享 helper 扩围。

## 任务 4：实现前端显式搜索

1. API 封装增加客户端 query 判定、精确 POST `{query}` 和既有七键列表 parser 复用。
2. 面板增加搜索草稿、已应用查询、搜索状态、独立代次与同步 flight token；项目切换/折叠/卸载完整重置。
3. 搜索与全部既有意图双向互斥；active search 下刷新/创建/恢复只重发同一 POST，清除只发一次列表 GET；命名/删除保持原位合同。
4. 固定中文空态/失败；关键词、名称、ID、版本、快照、错误与 CSRF 零泄漏。

## 任务 5：自审与串行测试

严格按契约第 6 节顺序运行。不得并发启动 pytest 与 Playwright，不得使用 xdist、并发分组、`--workers>1` 或重试掩盖失败。

额外自审：

1. `git diff --name-only` 精确六文件，`git diff --cached --name-only` 为空。
2. 后端无 ORM 整体、LIKE/JSON SQL、COUNT/OFFSET、N+1、写操作、短路坏行或第 21 条补扫。
3. 前端无 query URL、自动搜索、客户端 snapshot、宽 parser、重复请求、旧 finally 解锁新任务或原始错误输出。
4. 测试无宽状态、弱断言、固定 sleep、skip/fixme/xpass、`force:true`、fallback 假成功或仅源码字符串证据。

## 任务 6：请求 Codex 审查

只通过消息箱发送一个 `review_request`，正文必须包含：

- failure-first 与最终聚焦的精确数字；
- 逐条串行命令及结果；
- 六文件清单和最终 SHA-256；
- body/SQL 投影/候选上限/完整校验/搜索预算证据；
- 技术标/商务标、单飞、A→B 迟到、失败保值和泄漏证据；
- 明确未做项、暂存区为空、未提交、未推送。

Codex 审查前不得继续扩围。若测试因额度或外部环境中断，发送 `status` 如实说明，禁止伪造 review_request。

## 完成记录

1. 首轮 Grok 完成六文件实现；Codex 代码审查发现失败同词重试与 active search 全路径单飞缺陷，随后把返修严格限制在面板与两个测试文件。
2. 返修先得到两项真实失败，再修复为通过；后端反假绿同步收紧为精确 CSRF/角色错误、严格八列投影、非法库存名称和 8193 字符叶预算证据。
3. Codex 独立串行验收为后端 **18/123/1235 passed**，前端 **8/76/61/28/18 passed**，lint/build/py_compile/diff/六文件/哈希门通过；整仓前端沿用 **318 passed** 基线，未重跑亦未冒充。
4. Grok 全程未暂存、未提交、未推送；Codex 完成验收回执、中文实现提交和协作分支推送。
