# P13-G1 项目章节编辑意图租约后端实施计划

> 执行要求：Grok 必须使用 `executing-plans` 工作流逐项执行
> 目标：为技术标真实章节提供单持有者、45 秒过期的后端编辑意图租约，不冒充强制锁
> 架构：新 ORM 表 + 独立 service/API；项目级数据库写锁串行 heartbeat；clientId 只存摘要；leave 精确释放
> 技术栈：FastAPI、Pydantic v2、SQLAlchemy、SQLite/PostgreSQL 兼容事务、pytest
> 审计基线：`f0325d0593b0b8c6fc291ee08f646cffe74164fe`
> 契约：`docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md`
> 测试：pytest 禁止 xdist/并发分组；只在线程并发用例内部创建独立客户端/会话

---

## 1. 冻结哈希与白名单

| 文件 | 审计基线 SHA-256 |
|---|---|
| `backend/app/models/entities.py` | `FE935EEE0DED226A694F2CD61A0BE21239AB7EEB432CE3E0D800A1B4F0A0142A` |
| `backend/app/models/__init__.py` | `ADDDDDAE18A2DEC1CFBF67F382113DFF17E92E170FA8BD1CFA55C7D6E2F63F4B` |
| `backend/app/api/schemas.py` | `1ECC15036BB89F6ABC225A30FB88CED8A467B64C039C31EDB718C29AFB2BEFA9` |
| `backend/app/main.py` | `BFD98A36230B9D9CAFA566BDF327480777F737375379C3B22395A963A04A99BA` |
| `backend/app/services/project_chapter_edit_lease_service.py` | 不存在 |
| `backend/app/api/project_chapter_edit_leases.py` | 不存在 |
| `backend/tests/test_p13g1_project_chapter_edit_lease.py` | 不存在 |

Grok 不得写文档、暂存、提交、推送、切分支或操作 `main`。本项目固定在同一协作分支工作区，不创建额外 worktree；该项覆盖通用 plan skill 的 worktree 建议。

## 2. 任务一：E2E 级 HTTP/DB failure-first 专项

**文件**：只新建 `backend/tests/test_p13g1_project_chapter_edit_lease.py`

1. 复用 P13-F1 required TestClient 夹具模式，但使用独立用户名、口令和项目，避免依赖测试顺序。
2. 通过公开 PUT 写入含真实唯一 `chapterId` 的技术标 `chapters`，不得直接预插租约冒充路由行为。
3. 先写最小 heartbeat/leave、冲突、章节不存在和表存在断言。
4. 串行运行新专项；预期因路由/表/模块缺失真实失败，记录 failed/passed、首个业务失败和四生产哈希。
5. failure-first 阶段不得修改六个生产文件。

命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend
python -m pytest tests\test_p13g1_project_chapter_edit_lease.py -q
```

## 3. 任务二：ORM 表与 Schema

**文件**：

- 修改 `backend/app/models/entities.py`
- 修改 `backend/app/models/__init__.py`
- 修改 `backend/app/api/schemas.py`

步骤：

1. 新增 `ProjectChapterEditLeaseRow`，写齐模块/用途/对接/二次开发注释。
2. 定义精确单章节唯一键、两个复合索引、workspace/project/user FK 级联和七个持久字段。
3. 模型导出只增加新实体；不得改 P13-F1 表或现有迁移。
4. 新增精确两键 body、两键成功响应 Schema；clientId/chapterId 均用原生类型和严格 validator，`extra=forbid`。
5. Schema 验证错误内容不得直接由 FastAPI 返回；路由后续必须手工捕获并固定脱敏。

## 4. 任务三：租约服务

**文件**：新建 `backend/app/services/project_chapter_edit_lease_service.py`

步骤：

1. 定义固定 TTL=45、refresh=15、每用户项目上限=8、固定错误 code/message 和不可变结果类型。
2. 实现 clientId SHA-256、UTC 收敛、安全用户名门和不透明行 ID。
3. 实现项目级数据库写锁：SQLite 无值变化 UPDATE 当前项目行，非 SQLite `SELECT ... FOR UPDATE`；锁后才采样 `now`。
4. 实现技术标项目重验和权威 `chapters_json` 目标精确一次命中；无状态/非数组/缺失固定 chapter_not_found，重复目标固定 chapter_state_invalid，不得标题回退或依赖前端类型。
5. 实现过期清理、活动计数、单章节持有人查询，以及 actor/holder 用户、成员、角色和安全用户名重验；不安全 actor 固定 role_forbidden，失效 holder 才可接管。
6. 实现 heartbeat：同 user+digest 续期；失效 holder 接管；活动 holder 固定冲突；新章节上限固定 429。
7. 实现 leave：项目作用域重验后只删五维精确匹配，章节已删除仍可释放。
8. service 只 `flush` 不 `commit`；禁止日志、后台 timer、GET/list 或修改 editor-state。

## 5. 任务四：路由与注册

**文件**：

- 新建 `backend/app/api/project_chapter_edit_leases.py`
- 修改 `backend/app/main.py`

步骤：

1. 路由独立实现 required 活动 workspace strict bid_writer 依赖，任意 `X-Workspace-Id` 拒绝；禁止放宽公共 deps。
2. 手工读取有限 JSON body并用 Schema 校验；解析/校验失败统一固定 422，不回显原始 clientId/chapterId。
3. heartbeat 调 service、唯一 commit、错误完整 rollback；200 精确两键并 no-store。
4. 冲突 409 仅返回固定 code/message 和安全 `holderUsername`；其它错误固定脱敏。
5. leave 唯一 commit、204 空 body/no-store；重复/错 client 保持幂等。
6. `main.py` 只导入实体注册 metadata、导入并 include 新 router；不得改路由顺序语义或 lifespan。

## 6. 任务五：红绿补齐与反假绿

**文件**：只补 `backend/tests/test_p13g1_project_chapter_edit_lease.py`

逐项补真实测试：

1. exact body/响应/no-store、client 原文零库、digest 正确、45/15 时间。
2. 同 client 续期一行、同用户不同 client 冲突、不同用户安全 holder。
3. 两线程并发抢同章节，断言恰一 200、一 409、最终一行；禁止只断言不 500。
4. 先让请求在锁外等待，再推进时钟，证明锁后 fresh now 决定过期接管与新 expires。
5. 8 章节上限：旧持有续期成功，第 9 个新章节 429，零部分写。
6. 章节缺失、重复目标、非数组、业务项目、章节删除后 leave。
7. required/disabled、角色矩阵、owner 不绕过、停用用户/成员、跨空间、CSRF、X-Workspace。
8. holder 停用/改角色/坏用户名后当前 actor 接管，旧 holder 不泄漏。
9. leave 隔离其它 client/章节/用户/项目，重复幂等。
10. 表精确列/唯一键/索引/FK 级联；service/flush/commit 故障 rollback；响应敏感 marker 零出口。
11. HEAD/GET/query/未知后缀均不形成新能力；现有 editor-state PUT 不因租约被强制拒绝，证明诚实边界。

不得用源码字符串、`hasattr`、预插最终行、宽 `status in`、空集合、只检查计数非零或 mock service 冒充 HTTP/DB 证据。

## 7. Grok 串行自测门

```powershell
cd C:\Users\Administrator\biaoshu\backend
python -m pytest tests\test_p13g1_project_chapter_edit_lease.py -q
python -m pytest tests\test_p13f1_project_presence.py -q
python -m pytest tests\test_auth_rbac.py -q -k "active_workspace or role or csrf"
python -m pytest tests\test_editor_state.py -q -k "get_editor_state or put_editor_state"
python -m py_compile app\models\entities.py app\models\__init__.py app\api\schemas.py app\services\project_chapter_edit_lease_service.py app\api\project_chapter_edit_leases.py app\main.py

cd ..
git diff --check
git diff --cached --name-only
git status --short
```

不得运行 pytest-xdist、多个并发 pytest、后端全量、Playwright、lint/build 或前端测试。代表回归节点若实际名称不同，先只读列出并选择等价最小节点，不得改已有测试。

## 8. Codex 独立审查与提交

1. 核对严格七文件、开工哈希、空暂存、无 P13-F1/F2/editor-state/auth/config 扩围。
2. 审查锁前零业务判断、锁后单次 now、唯一事务、并发单赢家、上限与 rollback。
3. 审查章节唯一命中与删除后 leave、holder 身份重验、client 摘要和错误零原文。
4. 审查测试真实 HTTP/DB/线程/故障路径，排除恒真、宽断言和未施压 gate。
5. 疑似问题先走只读双确认，双方确认后才发独立返修 task。
6. 独立串行运行新专项、必要代表回归、py_compile 和 diff-check；不机械重复全量。
7. 验收后 Codex 精确暂存七文件，以中文功能提交并推送；再写回文档闭环。
