# P13-F1 完成态操作级交接：项目在线租约后端基础

> 交接日期：2026-07-20
> 当前状态：**已完成、已验收、已提交并推送**；实现=`6164d8c`
> 实现冻结基线：`78302bc085b1ccdb1e98b843c782dc449dcbc1ed`（`文档：冻结P13F1项目在线租约后端协议`）
> 工作分支：`collab/grok-code-codex-review`，严禁操作 `main`
> 契约：`docs/p13f1-project-presence-lease-backend-contract.md`
> 实施计划：`docs/plans/2026-07-20-p13f1-project-presence-lease-backend-plan.md`
> Grok 任务：`msg_31bba4d10d154daca2acab7d3f6ea1e5`
> failure-first 状态：`msg_aad9a00220a44195965981cfe82dae22`
> Grok 最终审查请求：`msg_b05f2bb6294742fe994555b99e44f11b`
> Codex 最终验收：`msg_5aae77e9c06b436aaa9f46c5747e4648`
> 后续操作：P13-F2 已独立审计并冻结，当前现场转至 `docs/HANDOFF-p13f2-in-progress.md`

本文保留 P13-F1 从在途实现到双确认返修、独立验收和 Git 闭环的操作级真值。七个实现文件已在 `6164d8c` 提交并推送；后续不得重复执行在途命令或把 P13-F2 混入本包。

---

## 1. 新会话复制即用

```text
继续 biaoshu P13-F2 前端心跳与安全成员展示的只读审计和独立冻结。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先完整阅读：
1. docs/HANDOFF-p13f1-in-progress.md
2. docs/HANDOFF-next.md
3. docs/p13f1-project-presence-lease-backend-contract.md
4. docs/plans/2026-07-20-p13f1-project-presence-lease-backend-plan.md
5. docs/plans/2026-07-12-bid-writer-roadmap.md
6. docs/integration-checklist.md

P13-F1 已完成：冻结=78302bc，实现=6164d8c，Codex result=msg_5aae77e9c06b436aaa9f46c5747e4648。不得重复返修或重跑全量。

先执行 git status -sb、git rev-parse HEAD、git rev-parse origin/collab/grok-code-codex-review，确认本地与远端一致且工作区干净；不得 pull、reset、checkout、stash、rebase 或清理。

P13-F2 必须重新只读审计前端 API、技术/商务共用入口、页面生命周期和 E2E，再另写契约与计划；不得沿用 P13-F1 七文件白名单。

用户规则：Codex 发现疑似问题后，必须先向 Grok 发 kind=review 的只读确认；Grok 只读回复 kind=status。只有双方明确确认问题存在，Codex 才能另发全新的 kind=task 修复授权。确认消息本身不授权修改；有分歧时保持代码原状补证据，仍不能统一则交用户裁定。

所有 pytest 串行运行，禁止 xdist/并发分组；本包默认不跑后端全量、前端测试或整仓 E2E。通过后才由 Codex精确暂存七文件、中文提交并推送；Grok 永远不得执行 Git 写操作。随后由 Codex 更新契约、计划、路线图、主交接、本文和联调清单，再单独中文文档提交推送。

对话、注释和 Commit Message 一律简体中文；新写或大改文件遵守“模块/用途/对接/二次开发”四字段注释规范。
```

---

## 2. 当前 Git 真值

交接文档提交后 `HEAD` 会前移到新的文档提交，但七个 P13-F1 文件仍应保持未暂存。后续会话必须重新执行命令，不得只相信本文中的静态 SHA：

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
git diff --cached --name-only
git diff --name-status
git ls-files --others --exclude-standard
```

本交接编写前的现场：

- 当前分支：`collab/grok-code-codex-review`。
- 本地与 GitHub 远端基线均为 `78302bc085b1ccdb1e98b843c782dc449dcbc1ed`。
- 暂存区为空。
- P13-F1 代码没有 `git add`、commit 或 push。
- 工作区精确为 4 个已跟踪修改、3 个未跟踪新文件，共 7 文件。
- 当前 Grok 单次任务已经结束，没有正在执行的 P13-F1 Grok 子进程；不要重复启动原任务。
- 文档提交后只允许文档被暂存和提交；七个实现文件必须继续留在未暂存区。

严禁执行：

- `git pull`
- `git reset` 或 `git reset --hard`
- `git checkout --` 或切换分支
- `git stash`
- rebase、clean 或任何工作区清理
- 把未验收代码和交接文档混成一个提交

---

## 3. 当前全部在途实现文件

### 3.1 四个已跟踪修改

| 文件 | 当前变化 | Grok 报告的用途 |
|---|---:|---|
| `backend/app/models/entities.py` | `+63` | 新增 `ProjectPresenceLeaseRow`、唯一约束、索引和级联关系 |
| `backend/app/models/__init__.py` | `+2` | 导出 presence 租约实体 |
| `backend/app/api/schemas.py` | `+67` | 新增严格 client 请求、成员与 heartbeat 响应模型 |
| `backend/app/main.py` | `+3` | 唯一注册新实体与 presence router |

### 3.2 三个未跟踪新文件

| 文件 | 字节数 | 用途 |
|---|---:|---|
| `backend/app/services/project_presence_service.py` | `11960` | 租约事务、过期清理、限额、upsert、快照与 leave 服务 |
| `backend/app/api/project_presence.py` | `7977` | 私有作用域依赖和 heartbeat/leave 两个 POST 路由 |
| `backend/tests/test_p13f1_project_presence.py` | `38163` | 34 项真实 HTTP/DB/并发专项测试 |

### 3.3 当前冻结 SHA-256

以下值已由 Codex 在交接时重新计算，与 Grok `review_request` 完全一致：

| 文件 | SHA-256 |
|---|---|
| `backend/app/models/entities.py` | `FE935EEE0DED226A694F2CD61A0BE21239AB7EEB432CE3E0D800A1B4F0A0142A` |
| `backend/app/models/__init__.py` | `ADDDDDAE18A2DEC1CFBF67F382113DFF17E92E170FA8BD1CFA55C7D6E2F63F4B` |
| `backend/app/api/schemas.py` | `1ECC15036BB89F6ABC225A30FB88CED8A467B64C039C31EDB718C29AFB2BEFA9` |
| `backend/app/services/project_presence_service.py` | `A8C3BAED26753F6914B239D0DE37FDB59E43FCA8F86F01F6021C086C06D48888` |
| `backend/app/api/project_presence.py` | `BD546BFAFADD682B8C7698AE1509A5BE15F8913A1053E6AB7BF4818377216B79` |
| `backend/app/main.py` | `BFD98A36230B9D9CAFA566BDF327480777F737375379C3B22395A963A04A99BA` |
| `backend/tests/test_p13f1_project_presence.py` | `845E9DB51327429B96F52C3FFE6E0404AF341095B6D6854B90D763E7D4CD840B` |

任何获授权返修都会改变相应哈希；返修前先记录旧值，返修后重新计算，不得沿用本文冒充最终值。

---

## 4. P13-F1 要解决什么

P13-E 已让用户切换活动工作空间并只读查看成员，但“成员启用”不等于“当前正在这个项目里”。P13-F1 增加一个最小的项目级短租约后端协议，让服务端回答：最近 45 秒内，哪些严格标书制作者仍在为当前项目续租。

该能力不是人员真实在线状态，不表示正在输入、当前焦点、最后活跃历史或共同编辑成功。任务 SSE 里的 heartbeat 仍只是连接保活，与 presence 无关。

### 4.1 唯一 HTTP 协议

- `POST /api/projects/{projectId}/presence/heartbeat`
- `POST /api/projects/{projectId}/presence/leave`
- 不新增 GET、SSE、WebSocket、长轮询、后台 timer 或事件广播。
- heartbeat 成功 200，响应精确四键：`leaseExpiresAt/refreshAfterSeconds/members/truncated`。
- member 精确两键：`username/isSelf`。
- leave 成功固定 204 空 body且幂等。
- 所有成功响应 `Cache-Control: no-store`。

### 4.2 身份与作用域

- 只允许 `AUTH_MODE=required`。
- 只认会话权威 `activeWorkspaceId` 和可信 principal。
- 当前空间成员必须启用，角色必须精确为 `bid_writer`；owner 不能替代角色。
- 任何 `X-Workspace-Id` 存在都拒绝，包括空值。
- 项目必须属于当前活动空间；不存在、跨空间和已删除统一 404。
- 禁止从 body/query/header/Cookie 原文/clientId 推断用户。

### 4.3 租约与数据边界

- clientId 仅接受 22..64 位 `[A-Za-z0-9_-]`，不得 trim、别名或 snake_case。
- 数据库只存 SHA-256 摘要，不存、不回显、不日志记录原始 clientId。
- 租约固定 45 秒，建议续租固定 15 秒；时间只取服务端 UTC。
- 同一 client 心跳更新同一行；每用户每项目最多 8 个未过期 client。
- 到 8 条时已有 client 仍可续租，新 client 固定 429 且零新增。
- 心跳机会性清理全表过期行；没有后台常驻清理线程。

### 4.4 快照与隐私

- 当前项目、当前空间、未过期租约才参与快照。
- 再次联表确认用户启用、成员启用、成员属于同空间且角色仍为 `bid_writer`。
- 同一用户多个 client 聚合为一个成员。
- 用户名使用 P13-D2 同等级安全文本门；坏用户名整用户隐藏。
- 当前 actor 优先，其余按用户名大小写折叠稳定排序。
- 最多 50 人；候选超过预算时 `truncated=true`。
- 禁止输出 userId、memberId、leaseId、clientId/digest、角色、owner、时间明细、Cookie、CSRF、会话或项目内部字段。

### 4.5 事务边界

- 项目重验、过期清理、8-client 判断、同 client upsert 和快照读取在同一事务闭环。
- service 只能 flush，不得 commit；路由成功统一 commit，失败 rollback。
- leave 只删除当前 actor、当前活动空间、当前项目、当前摘要的租约。
- 数据库异常必须完整回滚并返回固定脱敏 500。

---

## 5. Grok 已完成的过程和消息链

### 5.1 原任务

- Codex task：`msg_31bba4d10d154daca2acab7d3f6ea1e5`
- 冻结 HEAD：`78302bc085b1ccdb1e98b843c782dc449dcbc1ed`
- 严格白名单：6 个生产文件 + 1 个新专项测试。
- 禁止范围：公共 `deps.py`、认证中间件、`auth_service.py`、项目/editor-state/task 服务、既有测试、前端、依赖、配置和文档。

### 5.2 failure-first

Grok 先只创建 `backend/tests/test_p13f1_project_presence.py`，未动生产文件：

- 状态消息：`msg_aad9a00220a44195965981cfe82dae22`
- 收集：34 tests
- 结果：**30 failed / 4 passed**，1 warning，约 25.12 秒
- 首个业务失败：`test_heartbeat_success_exact_shape_and_no_store`
- 真实失败：heartbeat 路由不存在，期望 200、实际 404 `Not Found`
- 当时四个既有生产文件哈希与计划完全一致；两个新生产文件不存在。
- 当时测试哈希：`000E479E4219BCBBC8D18CEFAA84C768A6A03905B0CFE59B103E6BA3DBA25A05`

这是一条合规的真实 HTTP 红测，不是 ImportError、源码字符串或预插入恒真证据。

### 5.3 Grok 完成实现与自测

- 最终审查请求：`msg_b176f13020d5470395f70792f811921b`
- P13-F1 专项：**34 passed**，27.30 秒
- 直接回归 `test_auth_rbac.py + test_health_and_projects.py + test_p13a_task_sse_workspace_auth.py`：**55 passed**，34.58 秒
- 7 文件 `py_compile`：通过
- `git diff --check`：通过，仅有 Windows 换行提示
- 工作区：精确 4M + 3??，暂存区空
- 未运行：后端全量、前端测试、xdist/并发 pytest、外网
- Grok 未写文档、未暂存、未提交、未推送。

### 5.4 Grok 主动报告的残余风险

- 过期比较在 Python 侧做 UTC 归一，避免 SQLite 朴素时间串偏差；需要 Codex 复核全表机会清理的正确性与成本。
- 同 client 并发使用 SAVEPOINT + `IntegrityError` 回落更新；需要 Codex 复核事务失效状态、唯一约束竞争和最终响应。
- 不同新 client 在 8 条上限附近依赖 SQLite 写串行；极端并发是否可能越限仍需独立审查和定点验证。

---

## 6. 完成结论与剩余事项

P13-F1 的逐文件审查、两轮双确认返修、Codex 独立专项 **41 passed**、代表回归 **3 passed**、七文件编译/差异/哈希门、result 回执、实现提交与 GitHub 推送均已完成。

仍未完成的下一独立包只有 P13-F2 前端 heartbeat/leave/安全成员展示；协同光标、章节锁、广播、WebSocket、评论审批和在线历史继续是更后续独立包。

---

## 7. Codex 独立审查清单

### 7.1 文件与装配边界

- `git status --short` 精确七文件，无任何额外生产、测试、文档或配置变化。
- `models/__init__.py` 和 `main.py` 只做必要导出/注册，没有重复 router、循环导入或全局副作用。
- 新表由既有 `Base.metadata.create_all` 建立；没有偷偷加入轻量 ALTER 迁移。
- 新文件顶部和触达公开类/函数符合“模块/用途/对接/二次开发”中文注释规范。

### 7.2 ORM、约束和级联

- 四元组唯一精确是 workspace/project/user/client digest。
- workspace/project/user 作用域列、摘要和 UTC 时间列长度/非空约束合理。
- 项目与用户删除确实级联清理；不能只在 ORM 关系上声称级联而数据库 FK 不生效。
- 两个索引能覆盖项目过期筛选和当前用户活动租约计数。
- 没有存储原始 clientId、用户名快照、角色、owner、Cookie 或会话信息。

### 7.3 请求与响应 Schema

- 仅 camelCase `clientId`，`extra=forbid`。
- 拒绝 snake_case、额外键、缺失、非字符串、首尾空白和长度/字符边界。
- 不因 Pydantic alias/populate 规则意外接受 `client_id`。
- heartbeat 响应顶层精确四键，成员精确两键；leave 204 为空。
- datetime 输出是明确 UTC，不输出本地朴素时间。

### 7.4 认证和作用域

- 私有依赖复用权威认证真值，不从客户端补身份。
- required/disabled、无会话、停用用户/成员、finance/hr/bidder/owner+非 bid_writer 的结果符合契约。
- 任何 `X-Workspace-Id` 存在都拒绝，特别检查空字符串和重复头。
- 项目不存在、跨空间、删除后统一 404 且零租约。
- 错误体、日志和 500 不泄漏 SQL、表/列、摘要、项目/空间/用户 ID。

### 7.5 服务与事务

- heartbeat 的项目重验、清理、计数、upsert、快照在同一 Session/事务。
- service 没有 commit；路由只在全链成功后一次 commit。
- 任何 flush、upsert、快照或 commit 故障都 rollback，不留下半租约或误清理。
- 同一 client 首次并发不会重复、500 或把 Session 留在 failed 状态。
- 8 条时已有 client 可续租；第 9 个新 client 429 且零新增。
- 不同新 client 在边界并发时不会越过 8。
- leave 精确四重作用域，重复 leave 204，不误删其它 client/用户/项目/空间。
- 全表过期清理不会把仍有效的朴素/aware UTC 时间误删，也不会被坏时间行拖垮。

### 7.6 快照与预算

- 查询重新联表验证用户、成员、空间、启用态和精确 `bid_writer` 角色。
- 同用户多 client 聚合，不出现重复成员。
- P13-D2 安全用户名门在 Unicode 码点、首尾空白、C0/C1/DEL、U+2028/U+2029、双向控制边界一致。
- 当前 actor 安全用户名优先；其余 casefold 稳定排序且有确定 tie-break。
- 50 人预算和 `truncated` 针对安全候选而不是原始租约数，坏/停用成员不会错误占满预算。
- 响应与异常没有任何内部 ID、摘要、原始 client、时间明细或身份材料。

### 7.7 测试反假绿

- 34 项测试主要走真实 HTTP、真实 DB 和实际并发，不用源码字符串/签名/`hasattr` 代替行为。
- failure-first 中 4 个已通过用例不能让关键业务在路由缺失时假绿。
- 并发测试真正让请求重叠，而不是顺序调用后只检查唯一行。
- 8-client 上限测试验证零副作用，不只断言 429。
- rollback 测试在新连接/新 Session 中验证零残留，不只检查当前对象。
- 泄漏门递归检查响应和数据库，不能用宽 `not in str(response)` 或跳过标记。
- 约束/索引/级联通过数据库行为与元数据双证据，而不是只看类属性。

---

## 8. 精确续跑顺序

### 8.1 第一步：状态和消息箱

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git rev-parse HEAD
git rev-parse origin/collab/grok-code-codex-review
git diff --cached --name-only
.\tools\agent-collaboration\Read-AgentMailbox.ps1 -From grok -Tail 8
```

必须能读到：

- task：`msg_31bba4d10d154daca2acab7d3f6ea1e5`
- failure-first：`msg_aad9a00220a44195965981cfe82dae22`
- review_request：`msg_b176f13020d5470395f70792f811921b`

当前原 Grok 任务已经完成退出，不要重复启动。

### 8.2 第二步：完整静态审查

```powershell
cd C:\Users\Administrator\biaoshu
git diff -- backend/app/models/entities.py
git diff -- backend/app/models/__init__.py
git diff -- backend/app/api/schemas.py
git diff -- backend/app/main.py
Get-Content -LiteralPath backend/app/services/project_presence_service.py -Encoding UTF8
Get-Content -LiteralPath backend/app/api/project_presence.py -Encoding UTF8
Get-Content -LiteralPath backend/tests/test_p13f1_project_presence.py -Encoding UTF8
```

不要只审查生产代码；38 KiB 新测试必须全文阅读。

### 8.3 第三步：发现问题时先双确认

如果 Codex 发现疑似缺陷：

1. 保持七文件原样，不修改。
2. 发送 `kind=review`，逐项写明代码位置、行为理由、严重性、最小文件范围和建议测试。
3. 明确要求 Grok 只读确认，禁止修改、测试、清理和 Git 写操作。
4. Grok 以 `kind=status` 逐项回复“确认存在/不确认”。
5. 只有双方明确确认存在，Codex 才发送新的 `kind=task` 修复授权，并给出精确文件白名单、failure-first 要求和验收命令。
6. 如果双方不一致，继续只读补证据；仍不能统一则交用户裁定。

严禁把 `review` 确认消息当作修复任务。参考 P13-E 成功链：只读确认 `msg_c1e71b76...` → Grok 确认 `msg_e6f70945...` → 新授权 `msg_f3914a68...`。

### 8.4 第四步：Codex 独立最小验收

仅在静态审查没有未解决问题，或双确认返修已重新 review 后执行。所有 pytest 串行：

```powershell
cd C:\Users\Administrator\biaoshu\backend

.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_p13f1_project_presence.py `
  --tb=short

.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_auth_rbac.py `
  tests\test_health_and_projects.py `
  tests\test_p13a_task_sse_workspace_auth.py `
  --tb=short
```

第二组是否完整复跑由 Codex 根据静态审查决定；若专项已完整覆盖且生产触达范围未扩，允许只挑作用域/项目装配代表节点，但必须如实记录，不能把 Grok 的 55 项冒充 Codex 独立结果。

默认禁止：

- 后端全量 pytest
- xdist 或并发 pytest 分组
- 前端 Playwright、lint、build
- 整仓 E2E
- 外网或真实浏览器启动

### 8.5 第五步：静态门与哈希门

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m py_compile `
  app\models\entities.py `
  app\models\__init__.py `
  app\api\schemas.py `
  app\services\project_presence_service.py `
  app\api\project_presence.py `
  app\main.py `
  tests\test_p13f1_project_presence.py

cd ..
git diff --check
git diff --cached --name-only
Get-FileHash -Algorithm SHA256 `
  backend/app/models/entities.py, `
  backend/app/models/__init__.py, `
  backend/app/api/schemas.py, `
  backend/app/services/project_presence_service.py, `
  backend/app/api/project_presence.py, `
  backend/app/main.py, `
  backend/tests/test_p13f1_project_presence.py
```

### 8.6 第六步：通过后的实现提交

只有 Codex 明确独立验收通过后才能：

1. 向 Grok 发送 `kind=result`/ack；Grok 仍不做 Git 写操作。
2. 精确暂存七文件，不使用宽泛 `git add .`。
3. 检查 `git diff --cached --name-status` 和 `git diff --cached --check`。
4. 中文提交建议：`功能：完成P13F1项目在线租约后端基础`。
5. 推送协作分支：

```powershell
git -c http.proxy=http://127.0.0.1:7890 `
    -c https.proxy=http://127.0.0.1:7890 `
    push origin collab/grok-code-codex-review
```

6. 再更新契约、计划、路线图、主交接、本文和联调清单，写入 Codex 真实验收数字、结果消息、最终实现提交和已知风险。
7. 单独中文文档提交并再次推送。
8. 最终确认本地 HEAD 与远端一致、工作区干净后，才能冻结 P13-F2。

---

## 9. Grok 协作方式

### 9.1 固定职责

- Grok：只按 Codex 的单任务、文件级白名单实现与串行自测；通过本地消息箱返回 status/review_request；不得改文档口径、暂存、提交、推送、切分支、stash、reset 或 checkout。
- Codex：独立规划与冻结、逐文件审查、发起问题双确认、在双方确认后决定受限返修、独立验收、中文文档闭环、唯一负责 Git 提交与推送。
- 用户：只在授权范围变化、Codex/Grok 长期无法统一问题真值、认证确实 401/402 或外部依赖需要用户选择时介入；日常不需要在两者之间手工搬消息。

### 9.2 消息箱命令

发送消息：

```powershell
cd C:\Users\Administrator\biaoshu
.\tools\agent-collaboration\Send-AgentMessage.ps1 `
  -From codex `
  -Kind review `
  -Subject 'P13-F1审查发现只读确认' `
  -Body '只读确认内容；禁止修改、测试、清理和Git写操作'
```

读取 Grok 回执：

```powershell
.\tools\agent-collaboration\Read-AgentMailbox.ps1 -From grok -Tail 20
```

### 9.3 后台静默启动 Grok

只有存在新 task/review，且确认没有同一任务进程运行时才启动；所有进程后台静默，不弹终端、不打开浏览器、不抢前台：

```powershell
cd C:\Users\Administrator\biaoshu
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:ALL_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
$stdout = '.agent-collaboration\grok-p13f1-followup.stdout.log'
$stderr = '.agent-collaboration\grok-p13f1-followup.stderr.log'
$arguments = '--cwd "C:\Users\Administrator\biaoshu" --single "读取 .agent-collaboration/messages/codex-to-grok.jsonl 中最新一条 Codex 消息，严格按消息执行；若是 review 只做只读确认，若是 task 才按白名单实现；完成后仅通过消息箱回复，不要提交或推送。" --always-approve --disable-web-search --no-subagents --output-format json'
Start-Process -FilePath 'C:\Users\Administrator\.grok\bin\grok.exe' `
  -ArgumentList $arguments `
  -WorkingDirectory 'C:\Users\Administrator\biaoshu' `
  -WindowStyle Hidden `
  -RedirectStandardOutput $stdout `
  -RedirectStandardError $stderr `
  -PassThru
```

调用端超时不等于子进程停止。先按命令行和 cwd 查进程，再看消息箱和日志；不得重复下发或并发启动同一任务。Grok 发出 review_request 后单次进程退出是正常行为。

---

## 10. 路线图：接下来仍未实现什么

### 10.1 当前下一步：按冻结边界实现 P13-F2

P13-F1 已闭环；P13-F2 也已完成所需前端文件、生命周期、迟到隔离与 E2E 的只读审计和独立冻结。当前应先读 `docs/HANDOFF-p13f2-in-progress.md`，以实际冻结提交向 Grok 下发严格 failure-first 实现。

### 10.2 当前独立包：P13-F2 前端心跳与安全展示

P13-F2 不沿用 F1 文件白名单，已冻结为严格四生产加一新 E2E；完整契约见 `docs/p13f2-project-presence-frontend-contract.md`：

- 浏览器页面内存生成随机 clientId；禁止 localStorage、sessionStorage、IndexedDB、URL、日志或外网。
- 进入技术标/商务标项目后每 15 秒 heartbeat；切项目、卸载或退出时 best-effort leave。
- 只把后端 `username/isSelf` 表示为“最近仍在本项目续租”，禁止宣传为真实在线、正在输入或当前焦点。
- 项目切换、workspace 切换、认证失效和迟到响应必须有 session/project epoch 隔离。
- 单飞、退避、页面隐藏/恢复、网络失败、StrictMode 双 effect 和定时器清理必须先冻结。
- 前端不得读取或显示任何内部 user/member/lease/client ID。
- P13-F2 不顺手加入 WebSocket、协同光标、章节锁或事件广播。

### 10.3 真正多人协作主线仍未实现

即使 P13-F1/F2 完成，系统仍不是实时协同编辑器，以下均未实现：

- 协同光标、选区和“正在编辑”状态。
- 字段/章节锁、锁抢占、续租、失联释放和冲突 UI。
- editor-state 事件广播、事件 ID、SSE 游标和断线重放。
- WebSocket 双向通道和多副本广播。
- 项目级多任务事件总线、跨客户端进度一致和断线恢复。
- 评论、批注、审批流、通知、提及和完整审计报表。
- 跨项目历史、完整时间线、按 actor 搜索/筛选。

这些必须继续拆成可独立验收的小包，不能与 F1/F2 合包。

### 10.4 标书制作者产品主线仍未实现的 10 个能力包

路线图 `docs/plans/2026-07-12-bid-writer-roadmap.md` 仍列出以下阶段 2–4 主线：

1. 文档/图片/资质/业绩统一卡片库。
2. 卡片检索、筛选、来源追溯与写作引用。
3. 多内容模板/卡片选择与上下文配额。
4. 章节级融合建议、差异预览与逐项确认写入。
5. 智能建议“人工确认后应用”浏览器 E2E。
6. 响应矩阵来源超过 80 条的分页建议。
7. 响应矩阵字段级三方合并。
8. 生产级可插拔解析（MinerU/Docling）。
9. 交付增强：Word 精细版式、外部标讯源、真实语义 embedding 调优；这三个子项必须再拆开。
10. 融合确认写入最近批次的单次撤销。

执行优先级仍是 1→2→3→4 的“经验资产 → 可控 AI 生产”主链；其余按收益和外部依赖独立排期。

### 10.5 其它明确未完成或受外部条件限制的方向

- 真实 MinerU/Docling CLI、模型制品安装/打包、自动部署和真实用户文档验收。
- Word 整章/节级页框、跨页标题与整体版式决策。
- 国能 e 招之外的合法外部标讯站点/API/RSS、定时同步和附件链。
- 真实用户语料评测、召回/排序和语义检索效果调优。
- 修订搜索片段、高亮、评分、自动搜索、FTS/缓存和多源聚合。
- 既有 Settings `workspace_settings.workspace_id` 并发 `UNIQUE` 日志问题；如单独处理，也必须先走 Codex/Grok 双确认。

---

## 11. 禁止事项

1. 禁止把 Grok 自测结果写成 Codex 独立验收。
2. 禁止把 P13-F2 或后续协作能力写成已完成、已提交或已推送。
3. 禁止直接修改发现的问题；必须先让 Grok 只读确认，双方确认后再发新修复授权。
4. 禁止在确认消息中夹带修复授权。
5. 禁止 `git add .`、混提文档与未验收代码或误提交 `.agent-collaboration` 运行工件。
6. 禁止全量重复测试、并发 pytest、xdist、前后端并发访问共享 SQLite。
7. 禁止为了“保险”运行后端全量、整仓 E2E 或无关前端测试。
8. 禁止把任务 SSE heartbeat 冒充成员 presence。
9. 禁止把短租约成员显示宣传成真实在线、正在输入或协同编辑成功。
10. 禁止把 clientId 存浏览器持久化、数据库明文、日志、URL、Cookie 或响应。
11. 禁止在 P13-F1 顺手实现 P13-F2、WebSocket、广播、光标、章节锁或评论审批。
12. 禁止操作 `main`、切分支、pull、reset、checkout、stash、rebase 或 clean。

---

## 12. 文档与 GitHub 记录

P13-F1 GitHub 记录：

- `78302bc`：`文档：冻结P13F1项目在线租约后端协议`
- 新增契约：`docs/p13f1-project-presence-lease-backend-contract.md`
- 新增计划：`docs/plans/2026-07-20-p13f1-project-presence-lease-backend-plan.md`
- 同步更新：路线图、主交接、联调验收清单
- `6164d8c`：`功能：完成P13F1项目在线租约后端基础`

本文和五份关联文档作为单独中文文档提交推送；该提交以实际 HEAD 为准。推送后须确认本地 HEAD、远端协作分支一致且工作区干净，之后才可冻结 P13-F2。
