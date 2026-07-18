# P12F-F-A 修订可见内容搜索后端实施计划

> **执行者：Grok**：严格四文件，先新建专项形成真实路由红测再实现；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成；冻结=`b2eed7c`，实现=`e6516e8`，Codex 独立后端全量 `1096 passed`。

**目标：** 用不进入 URL 的请求体，在当前项目最新 20 条有限修订中搜索严格白名单内的用户可见标题/正文，只返回既有五键元数据。

**架构：** 新增独立 POST 路由与严格请求 Schema；history service 复用既有项目、来源、时间、快照校验，在固定六列/20 候选 SQL 后用 NFKC+casefold 字面子串匹配；现有 GET/list/page/detail 与数据库保持不变。

**技术栈：** FastAPI、Pydantic v2、SQLAlchemy、Python `json/unicodedata`、SQLite 测试库、pytest。

## 1. 基线与有效红测

1. 核验协作分支、HEAD/远端和干净工作区；阅读 P12F-A/B/D/E、P12C-C1 契约，路由/Schema/history service、13 键规范服务、修订表/索引及相关测试。
2. 只新建 `backend/tests/test_p12f_revision_content_search.py`，覆盖路由、请求体、字段白名单、资源窗、SQL、损坏/权限/零写和兼容边界；禁止先改生产代码。
3. 记录三个生产文件 SHA-256 仍为冻结值；从 `C:\Users\Administrator\biaoshu\backend` 运行 `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_content_search.py`，保留精确 passed/failed 与首个 POST 路由业务失败。fixture/导入/收集失败必须先修正，不能冒充红测。

## 2. Schema 与路由

1. 新增请求模型：`query` 必填，`sourceKind/createdFrom/createdBefore` 可空；四个值使用 Pydantic `Any`（或等价原样类型）承接，只认 camelCase、`extra=forbid`，外壳只处理缺失/额外键，禁止 `str`/日期/枚举预转换抢先改变错误优先级，运行时值交 service 判型；不接受游标、分页、命中片段或快照字段。
2. 新增响应模型或复用精确 `{items: MetaOut[]}` 结构，确保顶层无游标、总数、查询回显、匹配字段与正文。
3. 在动态 revision 路由前注册 `POST /{project_id}/editor-state-revisions/search`，设置 `no-store`，复用 `get_workspace_id` 和现有错误映射；required POST 自动走 CSRF。
4. 不改旧 GET 的未知参数语义；不新增 GET 搜索别名。

## 3. 有界搜索服务

1. 新增严格关键词规范 helper：原生字符串、首尾无空白、1..64 码点、无 C0/C1/换行/制表；错误固定不反射。匹配值和 query 均 NFKC+casefold，单个连续字面子串。
2. 在 `list_editor_state_revision_search` 中先项目存在性，再复用来源/时间规范；查询精确六列并固定 metadata 条件、倒序与 `LIMIT 20`。
3. 先完整校验全部候选元数据和 `snapshot_json` 的 13 键/字节/版本，再以显式结构 helper 提取契约允许字段；允许叶子只有原生字符串，数组只看对象项，outline 用显式栈只沿 children；未知/异型项忽略，4096 对象/8192 字符叶预算超限固定失败，禁止递归收集所有字符串。
4. 过滤后只组装五键元数据，保持原序且同修订一次；无匹配返回空 items。禁止补扫第 21 条、N+1 详情、当前编辑态、缓存/临时文件或数据库正文搜索。
5. 更新文件头四字段与注释，明确 list/page 五列不变，只有 search 显式有界加载 snapshot。

## 4. 专项反假绿

1. 每个允许字段放唯一标记并分别命中；每个禁止字段放另一唯一标记并分别断言零命中，不能只检查某个标记没出现在响应，因为响应本就不回正文。
2. 构造 21 条候选，关键词只在第 20/21 条，证明第 20 可命中、第 21 不扫描；同时捕获 SQL 编译/执行结构，断言六列、来源/时间谓词、双键倒序、`LIMIT 20` 和零禁用构造。
3. 在候选窗放置坏行并使用不命中关键词，仍必须整次固定损坏失败，证明没有“先搜后验”；跨空间/跨项目同关键词只返回当前作用域。
4. required 模式以真实 Cookie+CSRF 走 POST；缺/错 CSRF、finance/hr/bidder、owner 绕过均按既有门禁。记录成功/业务错误五域前后全等且无业务审计；缺/错 CSRF 必须保留既有安全审计，不能把它误判为搜索写入；全路径无请求体日志。

## 5. 串行回归与交付

1. 依次运行：
   - `cd C:\Users\Administrator\biaoshu\backend`
   - `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_content_search.py`
   - `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_time_range_filter.py`
   - `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_source_filter.py`
   - `.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_cursor_page.py`
   - `.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py`
   - `.venv\Scripts\python.exe -m pytest -q`
2. 对三个生产文件和新测试运行 `py_compile`、`git diff --check`、精确四文件白名单、空暂存区，以及无写操作/全实体/OFFSET/COUNT/LIKE/JSON SQL/递归全字符串扫描的 AST/源码检查。
3. 通过消息箱发送 review_request，报告真实红测、逐组绿测、SQL/字段白名单/第 21 条/坏行/权限/零写证据、风险和未做项；Grok 不得暂存、提交或推送。
4. Codex 独立审查输入规范、字段白名单、资源上限、错误优先级、SQL 精确性和测试反假绿；只允许四文件内最小返修。
5. Codex 独立重跑专项、受影响回归和后端全量；通过后中文提交实现、推送，再更新契约/计划/主交接/路线图/联调清单形成文档闭环。

## 6. 未做

不做前端 P12F-F-B、游标/分页、命中高亮/片段/分数、搜索历史、自动搜索/防抖、缓存、FTS/索引/迁移、跨项目搜索、来源多选、日期预设、命名/固定/删除、导出/分享、多人协作、SSE、数据库或依赖变更。

## 7. 执行结果

1. 真实红测为 **18 failed / 3 passed**，首个业务失败 405；冻结三个生产文件哈希保持不变。
2. 首轮实现后经两轮受限审查：第一轮修复固定脱敏 422、报价容器对象预算及 11 类反假绿；第二轮仅改专项测试，关闭 8 个残余假绿。任务链见契约第 8 节。
3. 最终实现严格保持四文件：路由、Schema、history service 和新专项测试；Grok 未暂存、提交或推送。
4. Grok 最终专项/受影响回归 **23/203 passed**；第一轮返修全量 **1096 passed**。Codex 独立串行结果为 **23/203/1096 passed**，全量耗时 1658.59 秒，仅 1 条既有弃用告警。
5. `py_compile`、直接 `git diff --check`、AST/弱断言扫描、精确四文件、空暂存区和最终 SHA-256 均通过；实现提交 `e6516e8` 已推送协作分支。
6. 下一步只允许先审计 P12F-F-B 前端入口；不得沿用 A 包后端白名单或顺带加入片段、高亮、自动搜索、缓存及写能力。
