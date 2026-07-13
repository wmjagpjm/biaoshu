# P9B 任务 5：国能命中人工接受实施计划

> **给 Grok：** 仅实施本计划白名单；先写失败测试，再完成最小实现。完成后只发送 `review_request`，不得自行提交或推送。

**目标：** 用户明确点击后，才将当前工作空间一条已解析截止时间的国能命中创建或复用为既有本地标讯；同步与命中本身绝不自动立项。

**架构：** 在 `opportunity_watch_service` 内以单事务读取命中与其计划、校验解析状态、通过 `source_key=chnenergy:{infoid}` 幂等创建 `BidOpportunityRow`，再回写 `accepted_opportunity_id`。路由只接收命中 ID，不接收正文、URL、采购字段或项目参数。

**技术栈：** FastAPI、SQLAlchemy、SQLite、pytest、FastAPI `TestClient`。

---

## 冻结约束

- 唯一入口：`POST /api/opportunity-watch/hits/{hit_id}/accept`；必须由用户人工调用，路由和服务不得批量接受、不得在同步完成时调用、不得创建项目。
- 只允许 `extraction_status="resolved"` 且有完整 `deadline_at_local` 的当前工作空间命中；`needs_review` 或时间非法返回 400，跨工作空间/不存在返回 404。
- 创建的本地标讯使用命中标题；采购人和摘要只可从关联追踪计划的 `buyer/scope` 补充；地区固定“其他”，来源标签固定“国能 e 招计划追踪”，截止日期取北京时间完整时间的日期部分。
- `source_key` 固定为 `chnenergy:{source_info_id}`，不得包含 URL、Cookie、Token 或同步状态。重复接受、同空间已有相同来源键时复用同一标讯，不得重复创建；响应仅含 `opportunityId`、`created`。
- 接受已截止命中仍照实写入本地标讯；是否可立项仍由既有标讯 API 的截止状态规则决定。
- 不得持久化或返回公告正文、URL、Cookie、HTML、JSON、附件和远端错误原文。

## 严格白名单

- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

禁止修改实体、既有 `opportunity_service`、数据库、配置、依赖、同步客户端、前端、PowerShell、文档以外文件；禁止真实网络、浏览器、依赖安装、提交或推送。

## 实施步骤

### 任务 1：服务事务与幂等测试

1. 写失败用例：`resolved` 命中接受后创建一条工作空间内标讯，断言标题、采购人、摘要、地区、来源标签、`deadline` 与 `source_key`；`accepted_opportunity_id` 回写。
2. 写失败用例：`needs_review`/无时间为 400 等价服务错误；重复接受返回同一标讯且第二次 `created=False`；跨空间命中不可读取；已有相同来源键可复用；接受失败时命中与标讯均不留下半成品。
3. 在服务中实现单事务 `accept_watch_hit`，只读取当前工作空间命中与计划；解析日期使用严格本地日期；调用已有标讯模型但不改既有服务语义。
4. 运行 `python -m pytest -q tests/test_opportunity_watch.py -k "accept"`，先失败、后通过。

### 任务 2：最小人工接受 API

1. 复用既有 `OpportunityWatchAcceptOut`，仅新增必要的安全服务异常映射；禁止请求体模型与 URL/正文类字段。
2. 新增 `POST /hits/{hit_id}/accept`，成功 200；未解析/无时间 400；跨空间/不存在 404；不自动创建项目。
3. 写 `TestClient` 用例固定状态码与 camelCase 响应，不得真实网络。

### 任务 3：交付检查

1. 运行 `python -m pytest -q tests/test_opportunity_watch.py -k "accept"`、`python -m pytest -q tests/test_opportunities.py`、`git diff --check`、`git status -sb`。
2. 静态确认无同步客户端改动、无 `BackgroundTasks`、无自动立项、无 URL/Cookie/HTML/JSON/异常正文持久化。
3. 只发送 `review_request`，附失败测试证据、最终结果、精确文件清单、状态与未解决问题；未获 Codex `ack` 不得提交或开始任务 6。

## Codex 验收门槛

1. 差异精确匹配白名单，人工接受与既有 `/api/opportunities/{id}/projects` 截止规则保持分离。
2. 独立运行接受定向、标讯回归和完整后端套件；既有哈希向量不稳定测试单独记录。
3. 通过后提交信息固定为：`接入国能命中人工确认`，仅推送协作分支并核对 SHA。

## 未完成项

- 任务 6 前端追踪面板和任务 7 总验收/文档闭环尚未开始。
- P9C 决策前置仍不满足，P9B 完整闭环前不得启动。
