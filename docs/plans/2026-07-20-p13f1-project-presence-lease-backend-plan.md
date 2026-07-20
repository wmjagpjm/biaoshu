# P13-F1 项目在线租约后端基础实施计划

> 契约：`docs/p13f1-project-presence-lease-backend-contract.md`
> 基线：`70dfb39bd4ea2b9417b0ed46e652c1d4a0ec7c8d`
> 分支：仅 `collab/grok-code-codex-review`
> 测试：pytest 严格串行，禁止 xdist/并发/机械全量
> 当前进度：**已完成并推送**；实现提交=`6164d8c`。Codex 已完成逐文件审查、两轮双确认返修、独立验收与结果回执，完成态记录见 `docs/HANDOFF-p13f1-in-progress.md`。

## 1. 基线与冻结哈希

| 文件 | 开工 SHA-256 |
|---|---|
| `backend/app/models/entities.py` | `2D989B1F2210063CB76CB25F57E0EEC13B4457D8E6C3846F707C567E16697DDF` |
| `backend/app/models/__init__.py` | `631F6F103E689BCDFF67EB42AB847A279F48C7617F52E7FE91DD419B9B130DFC` |
| `backend/app/api/schemas.py` | `FD869254F236B16E94B846E89FFD1A7FB713D96DAA3198796C4BE3CCD9581F25` |
| `backend/app/main.py` | `13DE5B2F966FC045E0ED85038C744FF31D5FD832D89484BD07B2870B37325196` |
| `backend/app/services/project_presence_service.py` | 不存在 |
| `backend/app/api/project_presence.py` | 不存在 |
| `backend/tests/test_p13f1_project_presence.py` | 不存在 |

Grok 不得写文档、暂存、提交、推送、切分支或修改 `main` 以外装配边界。协作消息和测试产物保持 Git 忽略。

## 2. 任务一：真实 failure-first

1. 只创建 `test_p13f1_project_presence.py`，构造 required 管理员与第二个 strict bid_writer、双 workspace/project 和可信 CSRF。
2. 先覆盖真实 heartbeat/leave HTTP 行为与表约束；四个既有生产哈希保持冻结、两个新生产文件仍不存在时串行运行专项。
3. 通过 status 回报真实 failed/passed、首个业务失败、冻结哈希和测试文件哈希，再开始生产。

## 3. 任务二：ORM 与严格 Schema

1. 新增 `ProjectPresenceLeaseRow`，只存 workspace/project/user、client 摘要和服务端 UTC 租约时间；组合唯一、CHECK/索引/级联严格按契约。
2. 在 `models/__init__.py` 与 `main.py` 注册新实体；新表依靠既有 `create_all` 建立，不添加轻量加列迁移。
3. heartbeat/leave 请求模型只接受精确 camelCase `clientId`，`extra=forbid` 且不启用 snake_case 别名；响应严格四键/两键。

## 4. 任务三：事务服务

1. 服务端验证 clientId 后只存 SHA-256 摘要，使用可信 actor；不记录原值。
2. 单事务完成项目作用域重验、全局过期清理、8 client 上限、同 client 原子 upsert、启用用户/成员/角色重验与安全用户名快照。
3. 同用户多 client 聚合；50 人上限、自身优先、稳定排序、保守 truncated；任何响应零内部 ID/摘要/时间明细。
4. leave 按四重作用域精确删除且幂等；所有 service 函数只 flush，不自行 commit。

## 5. 任务四：薄路由与装配

1. 新路由私有依赖只允许 required 活动 workspace strict bid_writer；任何 `X-Workspace-Id` 存在即拒绝，不修改公共 `deps.py`。
2. heartbeat/leave 调服务，成功一次 commit；业务错误固定映射，未知异常 rollback 并脱敏。
3. 所有成功 `no-store`；heartbeat 200 严格模型，leave 204 空 body；在 `main.py` 唯一挂载。

## 6. Grok 串行自测

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13f1_project_presence.py --tb=short
.\.venv\Scripts\python.exe -m pytest -q tests\test_auth_rbac.py tests\test_health_and_projects.py tests\test_p13a_task_sse_workspace_auth.py --tb=short
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\models\__init__.py app\api\schemas.py app\services\project_presence_service.py app\api\project_presence.py app\main.py tests\test_p13f1_project_presence.py

cd ..
git diff --check
git status --short
```

禁止后端全量、前端测试、并发 pytest 或 xdist。测试产生数据库/缓存时按既有忽略与安全路径清理，不得删用户文件。

## 7. Codex 审查与双确认

1. 核对精确六生产加一新测试白名单、开工哈希、新表/路由唯一装配和空暂存。
2. 审查 active workspace、strict role、可信 actor、项目重验、client 摘要、UTC 租约、上限、并发 upsert、rollback、级联和响应零泄漏。
3. 审查测试确实走 HTTP/DB/并发，不接受字符串、恒真集合、预插入后假验证或宽错误断言。
4. 如发现问题，先下发只读确认；双方确认后才另发精确返修任务。未双确认前不改代码。
5. 独立串行运行专项与必要回归、py_compile/diff-check；不机械全量。

## 8. 提交与闭环

1. 先提交并推送契约、计划、路线图、主交接与联调清单。
2. Grok review_request 后由 Codex 精确暂存六生产加一测试，以中文功能提交推送。
3. 写回 failure-first、双确认/返修消息、Grok/Codex 实测、最终哈希、未运行项和风险，再单独中文文档提交推送。
4. 完成后工作区必须空，本地 HEAD 与远端协作分支一致；P13-F2 重新冻结，不沿用本包白名单。

## 9. 实际完成结果

1. 初始 failure-first **30 failed / 4 passed**；Grok 初始专项/直接回归 **34/55 passed**。
2. 第一轮 Codex/Grok 双确认关闭 422 clientId 原文回显、SQLite 并发写串行化、rollback 假证据和测试口径四项问题；返修红测 **16 failed / 5 passed**，修后 **19/39/55 passed**。
3. 第二轮双确认关闭锁前时钟导致 TTL 与过期真值陈旧；返修红测 **2 failed**，修后 **2/41/55 passed**。
4. Codex 独立完整专项 **41 passed**、代表回归 **3 passed**；七文件编译、差异、白名单、哈希与暂存门通过，验收回执=`msg_5aae77e9c06b436aaa9f46c5747e4648`。
5. 七文件以中文提交 `6164d8c` 推送协作分支；未运行后端全量、前端或整仓 E2E，未使用 xdist。
