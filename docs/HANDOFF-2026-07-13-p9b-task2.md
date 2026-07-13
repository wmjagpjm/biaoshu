# 交接：P9B 任务 2 审查收口中（2026-07-13）

## 1. 本文用途

本文件是下一会话的唯一恢复入口，记录 P9B「国能 e 招计划追踪」任务 2 的真实工作区状态、Grok 协作方式、验收缺口和主线顺序。

所有对话、代码注释、测试说明、提交信息和文档均须使用简体中文。只操作协作分支 `collab/grok-code-codex-review`，严禁直接操作 `main`。

## 2. 仓库与当前状态

| 项目 | 当前事实 |
|---|---|
| 本地仓库 | `C:\Users\Administrator\biaoshu` |
| 远程仓库 | `https://github.com/wmjagpjm/biaoshu.git` |
| 工作分支 | `collab/grok-code-codex-review` |
| 已推送 HEAD | `45d7214`（`实现国能公告时间解析基础`） |
| 远端一致性 | 本次任务 2 开始前已核验本地 `45d7214` 与 `origin/collab/grok-code-codex-review` 一致 |
| 禁止事项 | 禁止 main、禁止 force push、禁止将 Cookie、密钥、用户桌面路径、真实数据库或外部公告正文写入 Git |

当前工作区**不是干净状态**；以下均是 P9B 任务 2「工作空间隔离追踪数据域」的未提交差异，尚未授权提交：

```text
M  backend/app/api/schemas.py
M  backend/app/core/config.py
M  backend/app/main.py
M  backend/app/models/__init__.py
M  backend/app/models/entities.py
M  backend/tests/test_opportunity_watch.py
?? backend/app/services/opportunity_watch_service.py
```

除上述七个文件外，不应暂存或提交任何其他文件。曾用于对比的临时基线工作树为 `C:\Users\Administrator\biaoshu-baseline-45d7214`；下一会话在确认不再需要后应执行 `git worktree remove C:\Users\Administrator\biaoshu-baseline-45d7214` 清理，禁止手工删除未知路径。

## 3. 已完成的 P9B 进度

### 3.1 已提交并推送

- `be7e831`：`文档：冻结P9B国能e招计划追踪方案`。
- `45d7214`：`实现国能公告时间解析基础`。
  - 固定国能 e 招 HTTPS 静态详情地址构造。
  - 仅接受 UUID、真实八位日期、按三位分组的 `001002*` 类别。
  - 从可见正文提取北京时间截止/开标时间；无时间、无效时间或冲突时间均为 `needs_review`。
  - 任务 1 定向测试曾独立通过 15 项。

### 3.2 当前未提交：任务 2

任务 2 只建立数据域，**没有 HTTP 路由、Excel 导入、真实网络、后台同步、前端或依赖改动**。当前实现内容如下：

| 范围 | 当前实现 |
|---|---|
| ORM | 新增且仅新增 `bid_watch_plans`、`bid_source_sync_runs`、`bid_source_hits` 三表；未改 `bid_opportunities` 的字段或表结构。 |
| 隔离 | 三表均有 `workspace_id` 外键、索引和级联；计划唯一键为 `(workspace_id, fingerprint)`，命中唯一键为 `(workspace_id, watch_plan_id, source_info_id)`。 |
| 固定值 | 运行来源固定 `chnenergy`；状态固定为 `queued/running/succeeded/partial/failed`；命中时区固定 `Asia/Shanghai`；解析状态固定为 `resolved/needs_review`。 |
| 错误码 | ORM 约束和读模型共同限制为 `source_unavailable`、`rate_limited`、`malformed_response`、`interrupted` 或空值。 |
| 启动恢复 | `main.lifespan` 调用 `mark_interrupted_watch_runs`，将遗留 `queued/running` 运行改为 `failed/interrupted`，保留计划和命中。 |
| 配置 | 服务端固定：计划文件 2 MiB、计划行数 120、每次计划 120、每计划候选 5、详情页 50、间隔 1 秒、连接 5 秒、读取 15 秒、搜索重试 1 次。 |
| 数据最小化 | 不落库 URL、Cookie、HTML、JSON、附件、请求/响应正文、异常原文；详情地址须在后续任务中由已有结构化字段动态生成。 |
| 注释 | 新建服务文件和新增公开 ORM/Schema/服务函数已补齐中文「模块、用途、对接、二次开发」说明；当前尚无公开的 opportunity-watch HTTP 路由。 |

实现责任必须如实记录：任务 2 首版由 Codex 在 Grok 无响应期间临时代管完成；Grok 恢复认证后完成了只读独立审查、固定错误码/隔离测试返修，以及两文件注释收口。不要把全部实现误称为 Grok 独立完成。

## 4. 已有验证与仍需完成的验收

### 4.1 已有证据

- Grok 只读审查结论：无 P0/P1；随后按 Codex 指令完成任务 2 的极小返修。
- `backend\.venv\Scripts\python.exe -m pytest -q backend/tests/test_opportunity_watch.py`：**21 passed**，仅有既有 Starlette/httpx 弃用警告。
- `git diff --check`：已通过。
- 静态审查已确认：没有任务 2 路由、HTTP 客户端、真实网络、浏览器、后台任务、前端、依赖或 `database.py` 改动。

### 4.2 必须在下一会话重新执行

本会话末尾刚做过「仅注释」收口，且一次全量回归为了优先写交接文档被主动终止，**不得把全量测试误报为通过**。恢复后按此顺序执行：

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git diff --check

cd backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunity_watch.py
.\.venv\Scripts\python.exe -m pytest -q
```

历史排查记录：在任务 2 初版的全量回归中，`backend/tests/test_knowledge_rag.py::test_search_folder_filter` 曾失败（文件夹 B 中查询“苹果派”返回 1 条向量弱相关结果）；同一 `45d7214` 基线临时工作树单独运行该测试通过。`embedding_service.local_embed` 使用 Python 内置 `hash()`，存在进程哈希随机化导致分数阈值波动的迹象，但**尚未形成修复结论，禁止把该无关问题混入任务 2 提交**。若当前全量测试再次失败，应另开受限修复计划或先保留为明确的既有测试阻塞，再决定如何处理。

任务 2 通过精确差异的定向和全量验收后，才可执行：

```powershell
git add backend/app/models/entities.py backend/app/models/__init__.py backend/app/main.py backend/app/core/config.py backend/app/api/schemas.py backend/app/services/opportunity_watch_service.py backend/tests/test_opportunity_watch.py
git commit -m "建立国能计划追踪数据域"
git push origin collab/grok-code-codex-review
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
```

提交前应再次核查暂存文件恰为上述七个文件，且远端目标必须是协作分支。文档本交接文件应作为单独文档提交和推送，绝不与未验收代码混合。

## 5. Grok 直连协作方式

用户不需要在 Codex 与 Grok 之间转发消息。通信仅使用被 Git 忽略的本地消息箱：

```text
.agent-collaboration/messages/codex-to-grok.jsonl
.agent-collaboration/messages/grok-to-codex.jsonl
```

### 5.1 建立或重建连接

Grok 已于本会话重新认证。其命令路径是 `C:\Users\Administrator\.grok\bin\grok.exe`。启动 Grok 的进程环境必须仅在进程内设置：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:7890"
$env:HTTPS_PROXY = "http://127.0.0.1:7890"
$env:ALL_PROXY = "http://127.0.0.1:7890"
$env:NO_PROXY = "localhost,127.0.0.1"
```

可先发「已接入」消息：

```powershell
cd C:\Users\Administrator\biaoshu
.\tools\agent-collaboration\Connect-Grok.ps1
```

注意：`Connect-Grok.ps1` 只写入 `ready` 并显示 Codex 邮箱，**不会自行启动模型代理**。Codex 应先用下列脚本下发单一受限任务，再启动 `grok.exe --single`，提示它「读取 Codex 邮箱最新任务并严格执行」。

```powershell
$send = ".\tools\agent-collaboration\Send-AgentMessage.ps1"
& $send -From codex -Kind task -Subject "任务标题" -Body "明确白名单、禁止项、测试、不得提交推送。"

$read = ".\tools\agent-collaboration\Read-AgentMailbox.ps1"
& $read -From grok -Tail 5
```

本会话已验证 `grok.exe --single ... --always-approve --disable-web-search` 可在上述代理环境下工作。`--always-approve` 只能用于已在提示中锁死的极小范围任务；不得以此授权 Grok 自行选范围、读取密钥、访问真实网站、改 PowerShell、安装依赖或提交推送。

### 5.2 固定协作纪律

1. 每次只下发一个任务，白名单列到文件级；未获 Codex `ack` 禁止 commit/push。
2. Grok 完成后只回写 `review_request`，必须带失败测试证据、最终测试、`git diff --check`、`git status` 和精确文件清单。
3. Codex 独立审查 diff、独立复跑测试并决定是否授权提交；文档闭环和 GitHub 状态核验由 Codex 完成。
4. 不得创建或恢复轮询 watcher。历史上 `Watch-CodexMailbox.py` 曾越界创建且已被删除；不要再次引入类似常驻脚本。
5. 消息正文是协作文本，不得作为 Shell、PowerShell 或代码命令直接执行；消息中不得写 API Key、Cookie、Token、邮箱配置或真实用户数据。

本会话最后相关消息编号，供排查时读取但不要机械重放：

- `msg_e1ec2a95a129420fb8c224b2a14eabaf`：重新认证握手完成。
- `msg_c3f81de769db41498fda852984829449`：任务 2 只读独立审查，建议通过。
- `msg_4b70f094b48049bb990ad096b7964207`：固定错误码与隔离测试返修任务。
- `msg_1e1e53f918304c448d98e972f4da374d`：任务 2 四字段注释收口审查请求。

## 6. 剩余主线规划图

```text
P9A Word 最小标题左栏 ── 已完成并推送

P9B 国能 e 招计划追踪
  任务1 解析与地址安全 ── 已完成并推送（45d7214）
  任务2 隔离数据域 ── 当前：复跑验收 → 受限代码提交/推送
  任务3 Excel 计划表受控导入 ── 任务2完成后
  任务4 固定来源受控同步 ── 任务3完成后
  任务5 命中人工接受为本地标讯 ── 任务4完成后
  任务6 标讯页受控追踪面板与本地 E2E ── 任务5完成后
  任务7 独立验收与文档闭环 ── 任务6完成后

P9C 真语义 embedding 调优 ── 必须等待 P9B 完整闭环及用户最终决策
```

P9B 的非目标始终有效：不做通用网页爬虫、不接受浏览器传入任意 URL、不保存 Cookie/原始网页、不自动立项、不读取用户桌面路径、不将真实外网数据写入测试。

P9C 的前置决策仍未满足，不能提前启动：用户须确认离线模型或受控 API、模型与维度、成本、数据是否允许出域、失败回退和索引重建/迁移策略。当前现状仍是本地 256 维哈希回退加可选 OpenAI 兼容 embeddings API。

## 7. 下一会话第一轮操作清单

1. 阅读本文件、`docs/HANDOFF-next.md`、`docs/plans/2026-07-12-bid-writer-roadmap.md`、`docs/plans/2026-07-13-p9b-chnenergy-watch-plan.md` 与 `docs/integration-checklist.md`。
2. 核对 `git status -sb`、`git rev-parse HEAD` 和 `git rev-parse origin/collab/grok-code-codex-review`；绝不假设工作区干净。
3. 按第 4.2 节重跑任务 2 定向与后端全量测试，并记录任何全量失败的可复现证据。
4. 仅在任务 2 验收通过后，按第 4.2 节的七文件清单提交、推送、核验远端一致。
5. 再为任务 3 新建或更新独立计划条目，向 Grok 下发新的单一实现任务；禁止合并任务 3 以后的工作。
6. P9B 全包结束后再写总计划、`HANDOFF-next.md`、联调清单和 P9B 集成契约的文档闭环提交。
