# P9B 任务 4：国能 e 招固定来源低频同步实施计划

> **给 Grok：** 只实施本计划白名单；测试先失败、后实现。完成后只发送 `review_request`，不得自行提交或推送。

**目标：** 当前工作空间的已导入计划可创建一次受控同步运行，只读取国能 e 招唯一 HTTPS 主机的固定门户、检索接口和经校验重建的详情页，持久化最小化的结构化命中与脱敏统计。

**架构：** 匿名 `uid` Cookie 只位于单个 `httpx.Client` 内存；候选的 `linkurl` 只作字段解析，不能访问、保存或返回。`POST /sync` 只创建 `queued` 运行并投递 `BackgroundTasks`，执行器使用独立数据库会话串行运行，`GET /runs/{id}` 仅返回当前工作空间的脱敏状态。

**技术栈：** FastAPI、`BackgroundTasks`、SQLAlchemy、SQLite、httpx、pytest、`httpx.MockTransport`。

---

## 冻结协议与边界

- 唯一主机为 `www.chnenergybidding.com.cn`；全部请求 HTTPS 且 `follow_redirects=False`，不接受浏览器传入的主机、路径、端口、IP、Cookie、Token、请求体或搜索条件。
- 先读固定门户 `https://www.chnenergybidding.com.cn/bidweb/`，仅在当前客户端 Cookie 容器确认匿名 `uid`；缺失时安全结束并使用 `source_unavailable`。
- 固定检索接口为 `https://www.chnenergybidding.com.cn/bidfulltextsearch/rest/inteligentSearch/getFullTextData`。仅 POST 固定 JSON：标题/正文检索、固定 HTTPS Referer；只允许 `wd=quote(已保存计划名)` 与 `rn=5` 变化。
- 只读取 `result.records` 的最前五条，字段仅限 `title`、`infodate`、`linkurl`。`linkurl` 必须安全解析 `infoid/categorynum/infodate` 后重建详情地址，只有 `001002*` 继续读取。
- 所有请求共享最小 1 秒间隔，连接/读取超时为 5/15 秒；每计划检索重试最多 1 次，详情不重试；全运行详情页不超过 50，同一 `infoid` 只读一次。
- 403/429、门户无 Cookie、响应结构异常或连续两次网络失败必须安全停止，错误码只能是 `source_unavailable`、`rate_limited`、`malformed_response`、`interrupted` 或空值。
- Cookie、完整 URL、原始 HTML/JSON、请求/响应正文、附件与远端异常原文不得落库或写日志；详情 HTML 仅在内存交给既有时间解析器后丢弃。不得自动创建项目。

## 严格白名单

- 修改：`backend/app/services/chnenergy_client.py`
- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

禁止修改实体、`database.py`、配置、依赖、`main.py`、既有标讯服务或路由、前端、PowerShell、其它文档；禁止真实网络、浏览器、依赖安装、读取桌面/密钥/数据库文件、提交或推送。

## 实施步骤

### 任务 1：固定客户端与模拟传输测试

1. 在 `test_opportunity_watch.py` 使用 `httpx.MockTransport` 写失败用例：门户 `Set-Cookie: uid` 后检索必须为 HTTPS POST、禁重定向、固定 Referer/JSON 字段、每计划最多五候选；测试替身拒绝所有未声明请求。
2. 增加失败用例：403/429、无 `uid`、无 `result.records`、非法 `linkurl`、非 `001002*` 与详情无时间分别得到安全错误码或 `needs_review`。
3. 在 `chnenergy_client.py` 实现固定客户端和安全跳转字段解析，复用既有详情地址构造与可见文本时间解析。支持注入 `MockTransport`、睡眠函数和时间源；生产默认才使用 `httpx.Client` 与 1 秒间隔。
4. 运行 `python -m pytest -q tests/test_opportunity_watch.py -k "sync"`，先证明失败，最小实现后通过；不得真实网络。

### 任务 2：同步状态机与最小命中写入

1. 写失败测试：服务创建 `queued` 运行；同空间已有 `queued/running` 时拒绝；跨空间运行不可查询。
2. 在 `opportunity_watch_service.py` 实现 `create_watch_sync_run` 与 `execute_sync_run(run_id)`：执行器自行创建/关闭 `SessionLocal`，开始转 `running`，终态仅为 `succeeded`、`partial`、`failed`，写入脱敏计数、结束时间和固定错误码。
3. 实现运行内 `infoid` 详情缓存和命中 upsert：同计划同公告不重复插入；不同计划可复用详情解析；只写实体已有允许字段。
4. 写失败测试：详情最多 50、同一公告只读一次、类别过滤不读详情、失败不删既有命中/本地标讯、连续两次网络失败停止。测试睡眠必须为零等待替身。

### 任务 3：最小 API 与后台调度

1. 在 `schemas.py` 新增仅含 `runId` 的 202 响应模型；不增加 URL、Cookie、错误原文或请求模型。
2. 在 `opportunity_watch.py` 新增 `POST /sync`（无请求体）和 `GET /runs/{run_id}`。前者只建运行并注册 `BackgroundTasks`，后者仅返回当前工作空间的既有脱敏运行读模型；同空间并发为 409，跨空间/不存在为 404。
3. 写 API 失败/通过用例；替换执行器或客户端，禁止 `TestClient` 真实联网。静态确认没有 dashboard、Excel 新入口、人工接受或前端功能。

### 任务 4：交付检查

1. 运行 `python -m pytest -q tests/test_opportunity_watch.py -k "sync"`、`python -m pytest -q tests/test_opportunities.py`、`git diff --check`、`git status -sb` 并保留原始结果。
2. 复核无 `follow_redirects=True`、`requests`、浏览器、任意 URL 入参、Cookie/HTML/JSON/异常正文持久化或日志，也没有自动立项。
3. 只发送 `review_request`，附失败测试证据、最终命令结果、精确文件清单、状态与未解决问题。未获 Codex `ack` 前不得提交、推送或开始任务 5。

## Codex 验收门槛

1. 白名单精确匹配；审查固定 HTTPS、禁重定向、限频、详情上限、固定错误码、工作空间隔离和无自动立项。
2. 独立运行同步定向、任务 2—4 相关回归与完整后端套件；默认哈希种子下的既有知识库不稳定用例单独记录，绝不混入本任务。
3. 通过后仅提交白名单，提交信息固定为：`实现国能公告受控同步`，普通推送 `origin/collab/grok-code-codex-review` 后核对本地/远端 SHA。

## 未完成项

- 任务 5 人工接受、任务 6 前端面板、任务 7 总验收与文档闭环均未实现。
- P9C 的模型、维度、成本、数据出域、回退和索引迁移决策仍未满足；P9B 完整闭环前不得启动。
