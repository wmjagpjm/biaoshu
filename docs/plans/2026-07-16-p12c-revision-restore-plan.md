<!--
模块：P12C-C2 editor-state 修订受限恢复实施计划
用途：落实九来源旧库迁移、共享恢复原语、七生产文件加四测试文件的 failure-first 顺序。
对接：p12c-revision-restore-contract.md；P12C-C1；P12B-D checkpoint restore。
二次开发：后端恢复与前端拆包；迁移失败必须阻止启动，禁止静默忽略。
-->

# P12C-C2 editor-state 修订受限恢复实施计划

> **状态**：已完成并独立验收；冻结=`54af600`、范围修订=`2276366`、实现=`0803250`。
> **基线**：C1 冻结=`26b504e`、实现=`7023ecd`、闭环=`5be234c`；后端/前端串行全量 **777/263 passed**。

## 1. 交付目标

交付单条 revision 的后端原子恢复：严格 expected CAS、恢复前安全检查点、目标规范重验、13 键写回、准确 `revision_restore` 新时间点、10/20 双配额裁剪和失败三域全回滚。同时幂等迁移旧 SQLite 八来源 CHECK；不做前端。

## 2. 实施顺序

1. 仅新增专项，先覆盖旧表迁移、POST body/权限、正常/同内容/断链/并发/失败回滚与来源隔离，保持生产未改运行 failure-first；
2. 在模型、revision service 与数据库迁移中增加第九来源；先让新旧 SQLite 的 DDL、数据保真、幂等和失败阻断测试通过；
3. 从 checkpoint service 抽取无提交 `stage_locked_canonical_restore` 类共享原语，并让既有 checkpoint restore 复用，先跑全部 P12B-D/P12C-D3 回归证明语义不变；
4. 新增 revision restore service：锁/CAS 后调用 C1 目标读取，再调用共享原语并以 `revision_restore` 编排唯一 commit/rollback；
5. 在既有 revision router/schema 增加严格 POST 与精确三字段响应；不改 `main.py`；
6. 仅按契约修订 C1 无 restore、P12C-A 八来源、D3 直接 recorder 三个过时阶段守卫，保持其他断言不变；
7. 串行运行 C2 专项、C1/D3/checkpoint/editor-state/auth/迁移扩大回归和后端全量，再做十一文件 `py_compile`、diff、暂存区与白名单检查；完成后仅发送 `review_request`。

## 3. Grok 最低自测

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_restore.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_checkpoints.py tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_auth_rbac.py tests\test_local_parser_callback_tickets.py --tb=line
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\core\database.py app\services\editor_state_revision_service.py app\services\editor_state_checkpoint_service.py app\services\editor_state_revision_restore_service.py app\api\editor_state_revisions.py app\api\schemas.py tests\test_p12c_revision_restore.py tests\test_p12c_revision_history_read.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_editor_state_revisions.py
```

## 4. Codex 验收门

Codex 独立审查迁移是否事务化/幂等/保留 DDL 与全部旧行，准确来源是否贯穿模型、服务、SQL CHECK 和公开列表；审查共享原语无查询/锁/事务所有权漂移，CAS 顺序、三重作用域、同内容零修订、断链补点、双配额与三域回滚。专项和扩大回归通过后运行后端串行全量；前端无改动，沿用单 worker、零重试 **263 passed** 基线。

## 5. 后续拆包

C2 闭环后再冻结前端 C3：列表展开才读、详情按需、二次确认、执行时最新 expected、成功唯一 editor-state/list 重读、项目切换/折叠/连点迟到隔离及正文不落 DOM/URL/存储/日志。删除、diff、搜索与多人协作继续不在 C3 默认范围。

## 6. 实际执行与验收

1. Grok 首轮 failure-first 为 **8 failed / 15 passed**，实现后专项 **23 passed**；扩大回归最初有 3 个合法过时阶段守卫，范围修订提交 `2276366` 只允许 C1 路由、D3 共享原语和九来源集合三处精确演进，最终四文件 **121 passed**。
2. Codex 审查拒绝原迁移“故障注入”假证据：它没有调用迁移，也没有注入故障。真实 DROP 前异常测试得到专项 **1 failed / 22 passed**、四文件 **1 failed / 120 passed**，唯一失败为 `editor_state_revisions__p12cc2_mig` 残留。
3. Grok 按受限任务在 CREATE 前增加零行 DML，触发 SQLite 物理事务；原红测转绿，且旧表 DDL、八列逐值、索引、FK、旧 CHECK 和临时表不存在均保持强断言。随后收紧路由必须 200、共享原语伪造来源、双配额精确一次以及五类固定 500/no-store。
4. Codex 独立结果：专项 **23 passed**；C2/C1/D3/P12C-A 四文件 **121 passed**；后端串行全量 **800 passed**，只有 1 条既有 Starlette/httpx 弃用告警；11 文件 `py_compile`、白名单、暂存区与 `git diff --check` 通过。前端无改动，沿用单 worker、零重试 **263 passed** 基线。
