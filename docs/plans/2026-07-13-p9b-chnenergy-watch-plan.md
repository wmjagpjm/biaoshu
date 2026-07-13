# P9B 国能 e 招计划追踪实施计划

> **给实施者：** 必须按本计划逐任务实现、测试和提交；不得将本方案泛化为任意网址抓取器。

**目标：** 让用户上传本机招标计划 Excel 后，可在“国能 e 招”对每个计划做受控精确检索，并仅对命中的招标公告正文提取投标截止时间、开标时间，供用户人工确认后加入既有本地标讯库。

**架构：** 新增独立的“计划追踪”数据域，而不改变现有 `/api/opportunities/import` 的 CSV/JSON 本地导入契约。服务端以固定 HTTPS 主机和固定请求格式调用国能 e 招的检索页，严格过滤招标公告类别，再按候选记录生成唯一的公告详情地址并低频读取正文；原始正文、响应、Cookie 和凭据一律不落库。同步在后端背景任务中串行执行，前端只轮询本工作空间的运行状态与已脱敏的结构化结果。

**技术栈：** FastAPI、SQLAlchemy/SQLite、httpx、openpyxl、Python 标准库 `html.parser`/`zoneinfo`、React、TypeScript、Vite、pytest、Playwright。

---

## 1. 已确认的产品与来源决策

### 1.1 受控来源

- **来源名称：** 国能 e 招（国家能源招标网），唯一主机为 `www.chnenergybidding.com.cn`。
- **来源选择依据：** 用户本机已有每天 08:30 的 `DailyBidSearchEmail` 任务；该任务在 2026-07-13 08:30 成功执行，并以该站检索 108 条招标计划。用户已明确同意使用此站。
- **性质说明：** 该站的检索请求是网站内部使用的匿名检索端点，不宣称为该站的正式开放 API，也不应复用为其他站点的“通用 API”。本包仅实现用户指定单站、固定字段、固定频率的受控读取。
- **实际字段验证：** 用户给出的公告 `b2363623-ea1e-4cc1-8e2d-0c2d2850b697` 正文含“投标文件递交的截止时间（投标截止时间，下同）及开标时间为 2026-07-29 09:00:00（北京时间）”。实现不得只停留在检索结果的标题和发布日期。

### 1.2 输入、输出与人工确认

1. 用户上传本机的招标计划 Excel；应用只在请求内存中解析，**不读取或写死桌面路径**，不保存原始上传文件。
2. 支持与 `daily_bid_search_input.xlsx` 一致的中文列：`招标计划名称`（必填）、`招标人`、`范围`、`计划工期`、`预计发布公告时间`、`备注`；允许首行说明文字，扫描前 10 行定位表头。空计划名行跳过，其他行号可定位的错误整批零写入。
3. 点击“同步国能 e 招”后，系统仅用已保存计划名搜索；浏览器不能提交 URL、Cookie、请求体、站点名、Token 或任意搜索条件。
4. 每个候选仅在类别号以 `001002` 开头时继续处理；中标候选人公示（如 `001005001`）和中标结果公告（如 `001006001`）必须过滤，不显示为可立项标讯。
5. 从公告详情页提取到完整时间后，页面展示“投标截止：2026-07-29 09:00:00（北京时间）”和“开标时间”。仍须用户点击“加入本地标讯”；同步绝不自动建项目或自动立项。
6. 用户确认加入时，才写入既有 `bid_opportunities`。其 `deadline` 取北京时间的日期；完整时间仍保留在追踪命中记录中，供页面核对。

### 1.3 明确不做

- 不修改用户既有 08:30 Windows 计划任务、脚本、邮件配置或 Excel 文件。
- 不增加应用内定时器、Cron、浏览器后台轮询、全站爬取、公告附件下载、验证码处理、登录、账号、密钥或代理配置。
- 不接入其他网站，不允许前端传入网址，不把来源配置做成任意 URL。
- 不保存或回显 Cookie、原始 JSON、原始 HTML、全文正文、附件、请求头或远端异常原文。
- 不修改现有本地 CSV/JSON 标讯导入，不把详情读取混入 `opportunity_service`，不自动创建技术标项目。

## 2. 固定的安全、限频和数据契约

### 2.1 网络边界

1. 仅服务端可访问固定 HTTPS 主机 `www.chnenergybidding.com.cn`；禁用重定向，禁止经客户端指定主机、路径、端口或 IP。
2. 每次同步进程先请求固定门户地址取得匿名 `uid` Cookie；Cookie 仅保存在当前 `httpx.Client` 内存中，运行结束立即释放。不得写数据库、日志、API 响应或前端状态。
3. 检索只使用计划表中的完整计划名、固定的标题/正文搜索字段、固定每计划最多 5 条候选；不得接受用户自定义高级检索语法。
4. 公告详情地址不能直接采信远端 `linkurl` 为可访问 URL。先解析其中的 `infoid`、`categorynum` 和 `infodate`，分别校验 UUID、`001002` 类别和八位日期后，由服务端用固定 HTTPS 规则重建地址：

   ```text
   https://www.chnenergybidding.com.cn/bidweb/{类别前三位}/{类别前六位}/{完整类别}/{发布日期}/{公告ID}.html
   ```

5. 所有检索和详情请求共用最小间隔 **1 秒**；单次运行最多 120 条计划、每计划最多 5 条候选、全运行最多读取 50 页详情。收到 403、429 或连续两次网络错误时停止后续请求并将运行标记为失败或部分完成。
6. `httpx` 超时为连接 5 秒、读取 15 秒；单条计划检索最多重试 1 次，详情页不重试。重试前仍满足最小间隔。

### 2.2 持久化字段与保留

| 实体 | 允许持久化 | 禁止持久化 |
|---|---|---|
| 追踪计划 | 计划名、招标人、范围、工期、预计发布时间原文、备注、工作空间、稳定指纹、启用状态、时间戳 | 原始 Excel 字节、桌面路径、用户帐号信息 |
| 同步运行 | 来源固定标识、开始/结束时间、状态、数量统计、预定义错误码 | URL、Cookie、请求/响应正文、异常堆栈、原始远端错误 |
| 公告命中 | 公告 ID、类别号、发布日期文本、标题、所属计划、标准化的截止/开标本地时间、解析状态、接受后的本地标讯 ID | 原始公告 HTML/文本、附件、Cookie、完整响应 JSON |
| 既有本地标讯 | 使用现有字段；`source_key` 仅存 `chnenergy:{公告ID}` 这种不透明键 | URL、Cookie、Token、同步运行状态 |

追踪记录在工作空间内长期保留，用户可在后续手工删除本地标讯；本包不提供批量删除或自动清理。若以后需要保留期或来源变更，必须另开计划并迁移审计记录。

### 2.3 时间与解析规则

- 将页面可见文本 HTML 解码、去标签、折叠空白后，再匹配中文招标条款；不得用整页正则直接处理 HTML。
- 第一版只接受含年、月、日、时、分的完整时间，标准化为 `YYYY-MM-DD HH:mm:ss`，并固定标注 `Asia/Shanghai` / 北京时间。
- 优先匹配“投标文件递交的截止时间（投标截止时间，下同）及开标时间为”；兼容“投标截止时间为”“开标时间为”两个独立条款。若同一条款给出两个时间，首个为截止时间、第二个为开标时间；若只出现一个时间，开标时间可为空。
- 找不到完整截止时间、发现互相冲突的多个截止时间、或详情地址字段非法时，命中状态为 `needs_review`，不允许加入本地标讯。
- 解析成功不是招标真实性或计划匹配性的自动结论；页面必须显示“待人工确认”，并给出由服务端生成的公开公告链接供用户核对。

## 3. 数据模型与 HTTP 契约

### 3.1 新增表

`BidWatchPlanRow`（`bid_watch_plans`）

- `id`、`workspace_id`、`title`、`buyer`、`scope`、`duration`、`expected_publish_text`、`remark`、`fingerprint`、`enabled`、`created_at`、`updated_at`。
- `(workspace_id, fingerprint)` 唯一；同文件重复计划应幂等跳过。指纹由服务端对清洗后的计划名、招标人和范围计算，前端不可传入。

`BidSourceSyncRunRow`（`bid_source_sync_runs`）

- `id`、`workspace_id`、固定 `source_name="chnenergy"`、`status`（`queued` / `running` / `succeeded` / `partial` / `failed`）、开始/结束时间、计划数、候选数、详情页数、已解析数、待复核数、跳过数、预定义 `error_code`。
- 同一工作空间存在 `queued` 或 `running` 运行时拒绝再次启动；进程重启后将未完成运行标为 `failed` 和 `interrupted`，不删除既有命中。

`BidSourceHitRow`（`bid_source_hits`）

- `id`、`workspace_id`、`watch_plan_id`、`sync_run_id`、固定 `source_name="chnenergy"`、`source_info_id`、`category_num`、`source_publish_text`、`title`、`deadline_at_local`、`opening_at_local`、固定 `source_timezone="Asia/Shanghai"`、`extraction_status`、`accepted_opportunity_id`、创建/更新时间。
- `(workspace_id, watch_plan_id, source_info_id)` 唯一；同一公告在本次运行只读取一次详情页，随后复用内存解析结果，但可保留其与不同计划的匹配关系。
- 不保存 `announcement_url`。读取和响应时通过 `source_info_id + category_num + source_publish_text` 动态生成固定 HTTPS 链接。

### 3.2 新 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/opportunity-watch/plans/import` | 上传 `.xlsx`，内存校验并原子导入追踪计划；返回 `inserted/skipped/total`。 |
| `GET` | `/api/opportunity-watch/dashboard` | 返回当前工作空间计划摘要、最近运行（脱敏统计）和按更新时间倒序的命中列表。 |
| `POST` | `/api/opportunity-watch/sync` | 创建后台同步运行，返回 202 与 `runId`；不接受请求体。 |
| `GET` | `/api/opportunity-watch/runs/{run_id}` | 查询当前工作空间某次运行状态及数量；跨工作空间返回 404。 |
| `POST` | `/api/opportunity-watch/hits/{hit_id}/accept` | 仅在已解析截止时间且人工点击后创建本地标讯；同一 `source_key` 重复请求返回原标讯，不重复创建。 |

所有输出遵循现有 camelCase；`announcementUrl` 只在命中响应中由服务端生成，前端不可提交或修改。同步 API 的 `errorCode` 只能是固定字典值，例如 `source_unavailable`、`rate_limited`、`malformed_response`、`interrupted`，不得透传远端错误文本。

## 4. 受限文件清单

实施仅可改动以下文件；发现需要扩展其他业务域时应先暂停并由 Codex 更新计划。

**后端：**

- 修改：`backend/requirements.txt`（仅新增 `openpyxl`）
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/api/schemas.py`
- 新建：`backend/app/api/opportunity_watch.py`
- 新建：`backend/app/services/chnenergy_client.py`
- 新建：`backend/app/services/opportunity_watch_service.py`
- 新建：`backend/tests/test_opportunity_watch.py`
- 新建：`backend/tests/fixtures/chnenergy_notice_deadline.html`
- 新建：`backend/tests/fixtures/chnenergy_notice_needs_review.html`

**前端：**

- 修改：`frontend/src/features/bid-opportunity/types.ts`
- 修改：`frontend/src/features/bid-opportunity/hooks/useOpportunities.ts`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunityPage.tsx`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunity.css`
- 新建：`frontend/e2e/opportunity-watch-chnenergy.spec.ts`
- 修改：`frontend/package.json`（仅增加单独的 `test:e2e:opportunity-watch` 命令）

**文档：**

- 修改：`docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md`
- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`（仅在实现验收通过后）
- 新建：`docs/p9b-chnenergy-integration-contract.md`（实现时，记录固定字段、限频、错误码和不存字段）

## 5. 实施任务（TDD、独立提交）

### 任务 1：先冻结解析器与地址安全测试

**文件：**

- 新建：`backend/tests/test_opportunity_watch.py`
- 新建：`backend/tests/fixtures/chnenergy_notice_deadline.html`
- 新建：`backend/tests/fixtures/chnenergy_notice_needs_review.html`
- 新建：`backend/app/services/chnenergy_client.py`

**步骤：**

1. 写失败测试：给 `jump.html?infoid=...&categorynum=001002001&infodate=20260709` 的查询字段，断言只生成固定 HTTPS 静态详情地址；外部主机、非 UUID、非八位日期、非 `001002` 类别均抛出受控错误。
2. 运行 `pytest backend/tests/test_opportunity_watch.py -k "detail_url" -q`，确认当前因模块不存在而失败。
3. 写失败测试：以最小 HTML fixture 断言提取 `2026-07-29 09:00:00` 为截止时间、同时提取开标时间；另一 fixture 无完整时间时返回 `needs_review`。
4. 实现 `chnenergy_client.py`：使用 `html.parser.HTMLParser` 收集可见文本、使用 `html.unescape` 和小范围正则解析；实现地址字段验证和标准化时间函数。文件顶注释必须写清“模块/用途/对接/二次开发”四字段。
5. 重新运行定向测试，确认通过；不发真实网络请求。
6. 提交：`git add backend/app/services/chnenergy_client.py backend/tests/test_opportunity_watch.py backend/tests/fixtures && git commit -m "实现国能公告时间解析基础"`。

### 任务 2：建立工作空间隔离的追踪数据域

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/core/config.py`
- 修改：`backend/app/api/schemas.py`
- 新建：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 写失败测试：新表创建后，另一工作空间无法读取计划、运行或命中；`queued/running` 运行在应用启动后会被标记为 `interrupted`，既有命中不丢失。
2. 运行对应 pytest 用例，确认 ORM/API 尚不存在。
3. 添加三个实体、必要索引和外键；为已有 SQLite 数据库只补“未完成运行”状态处理，**不**对现有 `bid_opportunities` 加列或改表。
4. 在 `Settings` 中加入不可由前端覆盖的计划数量、详情页数量、最小间隔和超时配置；默认值必须为本计划第 2.1 节的上限。
5. 在 schema 中新增计划、运行、命中和接受结果模型；禁止把 `cookie`、`url`、原文或任意外部请求参数暴露为入参。
6. 运行 `pytest backend/tests/test_opportunity_watch.py -q`，确认数据隔离与启动恢复通过。
7. 提交：`git add backend/app backend/tests/test_opportunity_watch.py && git commit -m "建立国能计划追踪数据域"`。

### 任务 3：实现 Excel 计划导入的原子契约

**文件：**

- 修改：`backend/requirements.txt`
- 修改：`backend/app/services/opportunity_watch_service.py`
- 新建：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 使用内存 `openpyxl.Workbook` 写失败测试：前两行说明、第三行中文表头、108 条以内计划可导入；同一文件第二次导入全部跳过。
2. 补失败测试：缺少 `招标计划名称`、计划名为空、超过文件/行数上限、同批两条冲突计划均返回可定位错误且零写入；跨工作空间同一计划可各自导入。
3. 运行 `pytest backend/tests/test_opportunity_watch.py -k "plan_import" -q`，确认失败。
4. 仅在 `backend/requirements.txt` 直接新增 `openpyxl`；实现内存解析、前 10 行表头定位、稳定指纹、单事务写入。上传路由只接受 `.xlsx`，读取后立即丢弃 bytes。
5. 注册独立 `/opportunity-watch` 路由，保留 `/opportunities/import` 的扩展名、错误语义和测试不变。
6. 运行计划导入定向测试和 `pytest backend/tests/test_opportunities.py -q`，确认旧 CSV/JSON 导入无回归。
7. 提交：`git add backend/requirements.txt backend/app backend/tests/test_opportunity_watch.py && git commit -m "实现国能计划表受控导入"`。

### 任务 4：实现固定来源的低频后台同步

**文件：**

- 修改：`backend/app/services/chnenergy_client.py`
- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 写失败测试：使用 `httpx.MockTransport` 模拟门户 Cookie、搜索候选、招标公告详情；断言只查询导入的计划、仅处理 `001002*` 类别、详情请求不超过全局上限、详情结果写入完整截止/开标时间。
2. 补失败测试：403/429、无 Cookie、格式异常、详情无时间和单条网络失败分别产生固定错误码或 `needs_review`；已有命中和既有本地标讯不被删除。
3. 补失败测试：同一工作空间已有运行时 POST 返回 409；跨工作空间不能查询运行或命中；同一 `infoid` 在同一运行只读取一次详情。
4. 运行 `pytest backend/tests/test_opportunity_watch.py -k "sync" -q`，确认失败。
5. 实现固定 HTTPS 客户端和 `execute_sync_run(run_id)`：服务端自己创建会话，串行限频，Cookie 只存内存，停止条件与第 2.1 节一致。严禁 `follow_redirects=True`、`requests.get(linkurl)`、浏览器自动化和原始响应日志。
6. `POST /sync` 只创建 `queued` 记录并交给 FastAPI `BackgroundTasks`；`GET /runs/{id}` 供页面轮询。同步成功、部分成功和失败都必须结束运行并写入脱敏计数。
7. 运行同步定向测试，确认无真实网络访问；再运行 `pytest backend/tests/test_opportunities.py -q`。
8. 提交：`git add backend/app backend/tests/test_opportunity_watch.py && git commit -m "实现国能公告受控同步"`。

### 任务 5：人工接受命中并复用本地标讯库

**文件：**

- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 写失败测试：有完整截止时间的 `resolved` 命中经 `POST /hits/{id}/accept` 后创建一条工作空间内 `BidOpportunityRow`，其 `deadline` 为北京时间日期、`source_key` 为 `chnenergy:{infoid}`。
2. 补失败测试：`needs_review` 命中返回 400；重复接受返回同一标讯且不重复创建；跨工作空间命中返回 404；已截止的接受记录仍照实创建但不能经既有立项 API 创建项目。
3. 运行 `pytest backend/tests/test_opportunity_watch.py -k "accept" -q`，确认失败。
4. 实现一次事务内的人工接受；标题取公告标题，采购人/摘要只可从本机计划字段补充，默认地区为“其他”，来源标签固定为“国能 e 招计划追踪”。不得从公告正文臆造采购人、预算或范围。
5. 运行接受定向测试及 `pytest backend/tests/test_opportunities.py -q`。
6. 提交：`git add backend/app backend/tests/test_opportunity_watch.py && git commit -m "接入国能命中人工确认"`。

### 任务 6：在标讯页增加受控追踪面板

**文件：**

- 修改：`frontend/src/features/bid-opportunity/types.ts`
- 修改：`frontend/src/features/bid-opportunity/hooks/useOpportunities.ts`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunityPage.tsx`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunity.css`
- 新建：`frontend/e2e/opportunity-watch-chnenergy.spec.ts`
- 修改：`frontend/package.json`

**步骤：**

1. 写 Playwright 失败用例：打开“标讯”页后可上传计划 Excel、看到导入计数；点击同步后显示“正在同步”，轮询结束后显示命中公告、北京时间截止时间和“待人工确认”。
2. 补 E2E：只有解析成功的命中可点击“加入本地标讯”；点击后新本地标讯出现，重复点击不重复；`needs_review` 不显示接受按钮；原有 CSV/JSON 导入按钮仍可用。
3. 运行 `npm run test:e2e:opportunity-watch`，确认当前失败。
4. 在现有 `useOpportunities` 中增加独立追踪 API 状态与轮询函数，绝不让前端直接访问国能 e 招。页面使用一个紧凑面板展示计划数、最近运行、同步按钮和命中列表；外链由后端 `announcementUrl` 生成，使用 `target="_blank"` 与 `rel="noreferrer"`。
5. 画面必须明确写出“国能 e 招候选公告，需人工确认；不会自动创建项目”，并在同步按钮禁用期间显示运行状态。不得改变现有本地标讯卡片的数据来源或状态计算。
6. 运行 `npm run lint`、`npm run build` 和新增 E2E；无真实外网依赖，E2E 仅使用本地测试后端的 mock client/fixture。
7. 提交：`git add frontend && git commit -m "新增国能计划追踪界面"`。

### 任务 7：独立验收与文档闭环

**文件：**

- 新建：`docs/p9b-chnenergy-integration-contract.md`
- 修改：`docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md`
- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`

**步骤：**

1. 在集成契约中记录固定来源、请求上限、解析条款、可存/不存字段、固定错误码、人工确认与非目标；不得包含 Cookie 或真实用户路径。
2. 运行 `pytest -q`、`npm run lint`、`npm run build`、`npm run test:e2e:opportunity-watch`、`git diff --check`。测试替身必须阻断所有真实国能网络请求。
3. Codex 审查：确认没有新建任意 URL 输入、没有浏览器直连、没有 `follow_redirects=True`、没有 Cookie/HTML/原始响应持久化或日志、没有自动立项，且 `source_key` 无 URL。
4. 对照用户给出的公告做一次**人工只读**页面核验：展示的北京时间截止时间与正文一致；此步骤不写入真实本地数据库。
5. 更新总计划、交接和联调清单中的实际 SHA、测试数量和未完成 P9C 前置；单独提交：`git add docs && git commit -m "文档：完成P9B国能计划追踪验收"`。
6. 仅推送 `collab/grok-code-codex-review`，并用 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review` 核验工作区干净且远端一致。

## 6. 验收矩阵

| 类别 | 必须证明 |
|---|---|
| Excel | 能导入用户实际结构的计划表；异常文件整批零写入；原始字节不落盘。 |
| 类别过滤 | `001002*` 以外记录均不会读取正文、不会出现在可接受列表。 |
| 截止时间 | 用户示例条款能得到 `2026-07-29 09:00:00` 北京时间；无时间或冲突时间为待复核。 |
| 网络安全 | 固定 HTTPS 主机、禁重定向、前端无 URL/Cookie/Token 入参、请求有上限和限频。 |
| 数据最小化 | 数据库/API/日志没有 Cookie、原始 HTML/JSON、附件或远端错误原文；链接动态生成。 |
| 隔离与幂等 | 计划、运行、命中、接受结果均按工作空间隔离；重复导入、同步和接受不产生重复本地标讯。 |
| 本地库兼容 | CSV/JSON 导入、手工新增、截止状态、从未截止标讯创建项目维持原语义。 |
| 前端 | 显示待人工确认、同步状态和北京时间；无自动项目创建；E2E 不依赖真实网络。 |

## 7. 实施前后的协作规则

1. 本计划提交并推送后，Grok 才能在公开协作分支或临时公开克隆中按“任务 1 至任务 6”实现；每次只做一个任务，未获 Codex 审查确认不得提交。
2. Grok 不得读取 `C:\Users\Administrator\Desktop\daka`、邮件配置、Cookie、数据库或用户密钥；该目录只作为 Codex 规划阶段已核验的本机任务行为参考。
3. Grok 启动时仅在其进程环境设置代理：`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY=http://127.0.0.1:7890` 和 `NO_PROXY=localhost,127.0.0.1`；不得写入仓库。
4. 新增或修改 PowerShell 文件必须 UTF-8 BOM；本包预计不需要 PowerShell 改动。所有代码注释、测试说明、提交信息和文档均使用简体中文。
5. Codex 必须独立复跑测试、审查网络与数据边界、完成文档闭环并核验 GitHub 协作分支；P9B 完整闭环前，不启动 P9C。
