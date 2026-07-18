# P12F-F-A 修订可见内容搜索后端契约

模块：P12F-F-A 双工作区修订可见内容搜索后端
用途：在有限自动修订账本中按用户可见标题/正文做受控字面搜索，只返回匹配修订的既有五键元数据。
对接：`editor_state_revision_history_service`、修订历史路由/Schema、P12F-A 20 条/20 MiB 保留边界、P12F-D/E 来源与时间筛选合同。
状态：2026-07-18 已完成 failure-first、两轮受限返修、Codex 独立验收和实现推送；冻结=`b2eed7c`，实现=`e6516e8`。

## 1. 审计结论与方案选择

现有 `GET .../editor-state-revisions/page` 明确只投影五个元数据列，绝不加载 `snapshot_json`；关键词若直接加入该 GET，还会进入浏览器 URL、代理/服务访问日志和错误诊断链。P12F-A 又把正常项目收敛为最多 20 条、单条 2 MiB、总计 20 MiB 的有限修订，因此本包不做数据库全文索引、JSON SQL、LIKE 粗筛或无限历史扫描，而新增一个请求体承载关键词的独立只读 POST。

比较过三种方案：扩展 GET 会泄露关键词并破坏五列投影；客户端批量读取详情会把完整快照暴露给浏览器并产生最多 20 次请求；独立 POST 在服务端有界解析快照、只返回元数据，能保持最小披露和固定资源上限。因此冻结第三种。前端入口另留 P12F-F-B，本包不改既有 page/list/detail 合同。

## 2. HTTP 与 Schema 合同

新增：

```text
POST /api/projects/{projectId}/editor-state-revisions/search
```

请求体精确为：

```json
{
  "query": "关键词",
  "sourceKind": "task",
  "createdFrom": "2026-07-16T00:00:00.000Z",
  "createdBefore": "2026-07-17T00:00:00.000Z"
}
```

- `query` 必填；`sourceKind`、`createdFrom`、`createdBefore` 可省略或为 `null`。模型必须 `extra="forbid"`，只接受 camelCase，不接受 snake_case、额外 `cursor/limit/offset/page/search/q` 或正文投稿字段。
- 请求模型只负责必填键与额外键外壳：四个值必须以 Pydantic `Any`（或能原样保留运行时类型的等价声明）承接，缺 `query` 或出现额外键固定 422；不得用 `str`、日期或枚举强类型在路由前抢先转换/拒绝，`query/sourceKind/createdFrom/createdBefore` 的运行时值由 service 严格判型。外壳成立后项目存在性最优先，再校验来源、时间、关键词；非法来源/时间沿用 P12F-D/E-A 固定错误，不复制第二套算法。
- 成功响应顶层精确 `{items}`；每项继续精确 `revisionId/stateVersion/snapshotBytes/sourceKind/createdAt`，最多 20 条，顺序固定 `created_at DESC,id DESC`。禁止 `nextCursor/total/hasMore/query/matchedFields/snippet/score/projectId/snapshot`。
- 所有成功和业务错误固定 `Cache-Control: no-store`。required 模式复用现有会话、bid_writer 与 CSRF 中间件；disabled 个人模式保持可用。搜索成功/业务错误本身不新增审计、事务提交或任何业务写；缺/错 CSRF 继续由既有中间件记录安全审计，不得冒充搜索服务写入。
- 搜索词只存在于 HTTPS/本机请求体和服务端调用栈；不得进入应用 URL、响应、错误、console、审计、数据库、Cookie、文件或浏览器存储。访问日志只可见路径与状态，不得新增 body 日志。

## 3. 关键词规范与匹配语义

- `query` 必须是原生字符串，首尾无空白，规范化后 1..64 个 Unicode 码点；拒绝 `null`、布尔、数值、对象/数组、空串、全空白、换行、制表、NUL、C0/C1 控制字符和超长值，固定 `400 editor_state_revision_search_query_invalid / 修订搜索关键词无效`，不得反射原值。
- 匹配双方统一 `unicodedata.normalize("NFKC", value).casefold()`，做单个连续字面子串判断；中文保持自然字面匹配，ASCII/拉丁大小写与全角兼容。禁止 regex、通配符、分词、模糊/语义搜索、HTML 解码和 Markdown 渲染。
- 同一修订只返回一次；不返回命中字段、次数、位置或片段。空匹配为 `200 {"items":[]}`，不得回退为未筛选列表。
- 只能搜索下列用户可见字符串：允许字段值仅在 `type(value) is str` 时参与；允许数组只检查其中对象项，未知键、非对象项和非字符串叶子忽略。`outline` 可为单对象或对象数组，只用显式栈沿 `children` 对象数组遍历，禁止 Python 递归和任意 dict/list 全树遍历。每快照最多访问 4096 个允许对象、8192 个允许字符串叶；超过固定损坏失败，不得截断后给假阴性。
  1. 技术标 `outline` 递归节点的 `title/description`，`chapters` 的 `title/preview/body`，以及 `parsedMarkdown`；
  2. 商务标 `businessQualify` 的 `requirement/response/evidence`，`businessToc` 的 `title/category/note`，`businessQuote.rows` 的 `name/unit/quantity/unitPrice/amount/remark` 与 `businessQuote.notes`，`businessCommit` 的 `title/body`，以及共享 `parsedMarkdown`。
- 明确禁止搜索任何 `id`、`stateVersion`、来源、状态、模式、布尔、数值派生字段、路径、响应矩阵内部引用，以及 `facts/analysis/analysisOverview/responseMatrix/guidance`。不得用“递归收集所有字符串”绕过字段白名单。

## 4. 有界扫描与 SQL 合同

1. 服务先只投影 `Project.id` 做 workspace/project 存在性检查，再规范来源、时间和关键词。
2. 搜索查询只投影 `id/state_version/snapshot_bytes/source_kind/created_at/snapshot_json` 六列，固定 workspace/project，按需增加来源与时间谓词，固定 `ORDER BY created_at DESC,id DESC LIMIT 20`；禁止 OFFSET、COUNT、全实体、当前 editor-state、检查点、详情逐条 N+1、SQL LIKE/JSON_EXTRACT/正则或数据库自定义函数。
3. `LIMIT 20` 是搜索候选窗，不是结果截断后的补扫：只搜索元数据条件下最新 20 条候选；旧库若仍有超过 20 条历史，不扫描第 21 条以后。该边界与当前 UI/保留上限一致，防止稀疏命中导致无界工作。
4. 必须完整物化并逐条复用 13 键、规范 JSON、版本、字节、来源和时间校验；候选窗任一行损坏使整次搜索固定 `editor_state_revision_corrupt`，不得跳过坏行或让关键词决定是否校验。
5. Python 侧只对校验后的对象执行字段白名单提取；最多 20 条、单条最多 2 MiB，正常 P12F-A 项目总计最多 20 MiB，旧库理论硬上界为 40 MiB。对象/字符串叶预算必须在规范化前计数。不得缓存索引、把正文写临时表/磁盘、并发解析、启动线程/进程或新增依赖。

## 5. 兼容、安全与明确禁区

- 旧 list、page、detail、comparison、body-diff、pair、restore 的路径、方法、响应、错误优先级和 SQL 投影字节兼容；现有 GET 的未知 `search/q` 继续完全忽略，不得暗中启用搜索。
- 搜索必须 workspace/project 双作用域；跨空间、跨项目和不存在项目统一既有固定 404。搜索成功与 service 业务错误零编辑态、修订、检查点、任务、业务审计五域写入，禁止锁、flush、commit、rollback、refresh；认证/CSRF 中间件既有会话触碰和失败安全审计不计为搜索服务写入，也不得被测试隐去。
- 本包不做前端、游标/分页、命中高亮、片段、相关性排序、搜索历史、自动搜索、防抖、缓存、全文索引、FTS、迁移、模型/表/索引、跨项目搜索、来源多选、日期预设、命名/固定/删除、导出、分享、多人协作或 SSE。
- P12F-F-B 只有在本包独立验收并闭环后才能另行审计；不得提前修改 API 封装、共用面板或 E2E。

## 6. 四文件白名单与冻结哈希

Grok 只允许修改：

1. `backend/app/api/editor_state_revisions.py`
2. `backend/app/api/schemas.py`
3. `backend/app/services/editor_state_revision_history_service.py`
4. 新建 `backend/tests/test_p12f_revision_content_search.py`

禁止修改模型、数据库、迁移、其他服务/测试、前端、配置、依赖/锁文件、文档或 Git 历史。Grok 不得 `git add/commit/push`。

冻结前三个生产文件 SHA-256：

- 路由：`A5B6A9CE4DA528021C88E8A50E6D507B35BDE3AC26D220BA6863EDED69C789FC`
- Schema：`852E0D691B004DCE754A4E90F034E970B3302B78710691C212CA738EED04AA65`
- history service：`89F4254D11E03E5C3E5F3D4F62CA75C8AAE22FC9FCA6CDCB93C03E3C1D8FB1AA`

## 7. Failure-first 与验收门

Grok 必须先只新建专项测试，三个生产文件哈希保持冻结值；运行专项时必须因 POST 搜索路由/Schema/服务不存在得到真实业务失败。收集、导入、fixture、数据库初始化、语法或环境失败不算红测。

专项至少覆盖：

1. 技术标与商务标全部允许字段逐类命中；NFKC、casefold、连续字面与空结果；同修订去重、固定倒序和精确五键响应；
2. 禁止字段逐类不命中，尤其 ID/stateVersion/source/status/mode/矩阵引用/guidance；未知嵌套键、异型叶子不参与；4097 对象/8193 字符叶固定失败；不得用秘密标记缺席冒充字段白名单；
3. 关键词空白/控制字符/65 字符/非字符串固定 400，缺失/额外键固定 422，且全部零反射；结构外壳成立后项目 404、来源、时间、关键词错误优先级明确；
4. 来源+单/双边时间组合、边界包含/排除、最新 20 候选窗、第 21 条不扫描、无 cursor/total/片段；
5. SQL 精确六列、谓词、顺序、`LIMIT 20`，无全实体/OFFSET/COUNT/LIKE/JSON/N+1；旧 page/list 的五列/10+1 与未知 search/q 兼容不变；
6. 候选窗中坏元数据、坏 JSON、非规范 13 键、版本/字节不符均整次固定失败且不泄漏；跨项目/跨空间零泄漏；
7. required bid_writer + Cookie + CSRF 成功，缺/错 CSRF 与非 bid_writer 受既有门禁；disabled 可用；成功/业务错误五域零写且无业务审计，CSRF 失败保留既有安全审计；无正文/关键词日志或响应。

Grok 至少依次运行：新专项；P12F-E-A 时间范围；P12F-D 来源；P12F-B 游标；P12C-C1 只读历史；后端全量；`py_compile`；`git diff --check`；精确四文件、空暂存区和 AST/源码禁区扫描。测试一律串行，禁止 xdist。Codex 独立重跑并审查反假绿后才可提交。

## 8. 完成交付与独立验收

- 冻结提交=`b2eed7c`；实现提交=`e6516e8`。原始任务/首轮回执=`msg_ab2e31c47bec41cea1800673d62dd866`/`msg_ca71c93c8daf4297901972b7f17b21a6`。
- 真实 failure-first 为 **18 failed / 3 passed**，首个真实业务失败是 POST 搜索路由 405；没有用收集、fixture 或环境失败冒充红测。
- 第一轮返修 task/review=`msg_5288187034e54751a8663e1262d6f284`/`msg_82c572a14d2544c88161bbcc58c84e05`：关闭默认 Pydantic 422 回显原始 `input`、`businessQuote` 容器漏计、非字符串宽状态码、SQL/N+1、真实禁止字段、对象/字符串预算、CSRF 审计与任务域假绿。路由改为手工安全解析 JSON 对象，外壳失败统一固定脱敏 422。
- 第二轮 test-only 返修 task/review=`msg_c32879f80cc5474f8ef0ae91413a7bd9`/`msg_2188e539e693431cb29b0211afd48e08`：补齐 4097 对象早期允许字段命中、精确当前 writer actor、跨空间固定 message、唯一项目 SELECT、真实 `bad_out`、搜索函数 AST regex 禁区、最终路由精确成功命名及未使用变量清理。
- Grok 最终专项/受影响回归为 **23/203 passed**，并保留第一轮返修后端全量 **1096 passed**。Codex 独立串行复验为专项 **23 passed（16.48s）**、受影响回归 **203 passed（295.39s）**、后端全量 **1096 passed（1658.59s）**；均只有 1 条既有 Starlette/httpx 弃用告警。
- Codex 独立 `py_compile`、`git diff --check`、AST/弱断言扫描、精确四文件、空暂存区和无根目录临时文件均通过；验收回执=`msg_554d0035e24d437086f3a1d14bbef1ad`。
- 最终 SHA-256：路由=`E56B0BF69A1DD425DFBF3FCD68F210E2664A9D693571E11467C462F10DDFDC08`，Schema=`474680ECEC41BEACACE624A6F154B5951167C1EEC23AEF4D48AAC708CD277221`，service=`8EACFAD08E213B14F8FF3FC5A3DBE93F3F9A17D02BCA282FF79BF8D51C350B2C`，专项测试=`584441E80D4C22DF4D616DB94E2D70CBBBF849260B5A314666F8C891F1B3995B`。
- P12F-F-B 前端搜索入口仍未实现；必须另行审计 API 封装、共用面板和 E2E，再冻结独立前端白名单。片段/高亮、自动搜索、缓存、跨项目搜索、来源多选、日期预设、命名/固定/删除及多人协作继续不在 A 包。
