# P13-D1 editor-state 修订操作者账本实施计划

> 契约：`docs/p13d1-editor-state-revision-actor-ledger-contract.md`
> 协作：Grok 实现与专项测试；Codex 冻结范围、受限审查、独立验收、中文文档闭环、提交推送
> 测试：pytest 串行，禁止 xdist/并行分组；本包无前端生产改动，不跑 Playwright 全量

## 1. 实施顺序

1. failure-first：新增 P13-D1 专项，覆盖两列新/旧库迁移、recorder 的 before/after/no-op 语义、九类传播、任务异步持久身份、disabled 与注入/泄漏/回滚门。
2. 模型与迁移：给 `EditorStateRevisionRow`、`ProjectTaskRow` 增加可空 actor；补两个 SQLite 幂等迁移并接入 `ensure_schema_columns`。
3. 身份入口：在 API 依赖模块增加只读 request-state helper；九类 HTTP 入口只从 helper 或一次性票据签发者取得 actor。
4. 写链传播：扩展 recorder、editor-state upsert、任务、revise、个人 callback、票据 callback、融合 apply/consume、共享恢复原语和两类恢复 service。
5. 受影响回归：专项通过后只运行既有 schema/migration、任务 writer、两类 callback、融合、检查点/修订恢复的定点集合；失败证据指向共享事务或迁移时才扩大。
6. Codex 审查：逐条核对九类矩阵、无 actor 泄漏、before 固定 null、异步任务不丢身份、disabled 不猜测、原子回滚不退化。
7. P13-D1 闭环后立即冻结 P13-D2：最新版本精确匹配后联表解析当前用户名，并复用 P13-B/C 前端接受门展示。

## 2. 允许修改的生产范围

- `backend/app/models/entities.py`
- `backend/app/core/database.py`
- `backend/app/api/deps.py`
- `backend/app/api/projects.py`
- `backend/app/api/tasks.py`
- `backend/app/api/revise.py`
- `backend/app/api/parse_callback.py`
- `backend/app/api/content_fuse_applications.py`
- `backend/app/api/editor_state_checkpoints.py`
- `backend/app/api/editor_state_revisions.py`
- `backend/app/services/editor_state_revision_service.py`
- `backend/app/services/editor_state_service.py`
- `backend/app/services/task_service.py`
- `backend/app/services/revise_service.py`
- `backend/app/services/local_parser_ticket_service.py`
- `backend/app/services/content_fuse_application_service.py`
- `backend/app/services/editor_state_checkpoint_service.py`
- `backend/app/services/editor_state_revision_restore_service.py`

测试优先新增 `backend/tests/test_p13d1_revision_actor_ledger.py`；只有既有精确 schema/签名断言因本契约合法变化时，Grok 才可在 review_request 中逐文件说明并请求 test-only 扩围。禁止修改前端生产文件、公开响应 schema、历史列表/详情 service 或无关业务。

## 3. 审查重点

- 两列迁移不得错误重建已存在的 revision CHECK/index，也不得为 actor 建 FK。
- request actor helper 只读 `auth_db_user_id`；disabled 与异常状态必须 null。
- 任务创建必须由服务端覆盖 actor，后台线程只读任务行；`task_to_dict` 与 SSE 不能出现字段。
- recorder 补账 `before=NULL`、真实变化 `after=actor`；验证必须早于任何 revision 插入。
- `stage_locked_canonical_restore` 接受 actor，但安全检查点本身不扩 actor 字段。
- 个人 callback 创建的 task 行也应保存同一 actor；票据 callback 创建的 task 行保存 ticket issuer，以保持任务追溯一致，但不公开。
- 所有新参数用关键字传递，避免位置参数错位。

## 4. 分级验收命令

Grok 默认：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13d1_revision_actor_ledger.py
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\core\database.py app\api\deps.py app\api\projects.py app\api\tasks.py app\api\revise.py app\api\parse_callback.py app\api\content_fuse_applications.py app\api\editor_state_checkpoints.py app\api\editor_state_revisions.py app\services\editor_state_revision_service.py app\services\editor_state_service.py app\services\task_service.py app\services\revise_service.py app\services\local_parser_ticket_service.py app\services\content_fuse_application_service.py app\services\editor_state_checkpoint_service.py app\services\editor_state_revision_restore_service.py tests\test_p13d1_revision_actor_ledger.py
cd ..
git diff --check
```

Codex 根据 diff 选择直接受影响回归；不得默认后端全量。若专项暴露旧测试与合法新参数/列冲突，先证明是测试契约过期，再授权 test-only 修改。

## 5. 提交边界

- 冻结提交：契约、计划、路线图/交接状态；中文 Commit Message。
- 实现提交：只含审查通过的生产与测试文件；Grok 不得提交或推送。
- 闭环提交：更新本计划真实 red/green、独立验收数字、路线图、交接与联调清单。
- 每次提交前 `git diff --check`；推送仅到 `collab/grok-code-codex-review`。
