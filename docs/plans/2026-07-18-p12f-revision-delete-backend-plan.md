# P12F-G-A 单条修订删除后端实施计划

> **执行者：Grok**：严格四文件，先只新增专项形成真实业务红测，再实现独立删除服务与 DELETE 路由；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成只读审计，当前文档即冻结边界。

**目标：** 为技术标/商务标共用自动修订提供单条物理删除后端，成功 204 且不可撤销，同时确保作用域、CSRF、事务回滚、脱敏和当前编辑态零副作用。

**架构：** 路由只做空 query/body 门、工作空间依赖与错误映射；新服务在单事务中投影项目 ID并执行三重作用域单行 DELETE；新专项通过 SQL/故障注入/权限与受影响读取链证明真实语义。

**技术栈：** FastAPI、SQLAlchemy 2、SQLite 测试库、pytest、既有 AuthMiddleware/get_workspace_id。

## 1. 基线与真实红测

1. 核对分支/远端/干净工作区及冻结提交；完整阅读契约、修订实体、history/restore/retention 服务、路由注册顺序、auth/CSRF 测试和事务故障测试模式。
2. 第一阶段只新建 `backend/tests/test_p12f_revision_delete.py`，生产路由/实体哈希不变、删除服务不存在；测试必须真实请求尚不存在的 DELETE，并覆盖相互独立的业务场景。
3. 串行运行专项，记录精确 failed/passed/did-not-run、首个 405/能力缺失及生产哈希；不得用导入不存在服务让测试收集失败。

## 2. 独立删除服务

1. 新建四字段完整的 `editor_state_revision_delete_service.py`，固定错误类与三组错误；业务错误不拼接输入，未知失败统一 rollback 后映射 delete_failed。
2. 以 `SELECT Project.id` 确认 workspace/project，再以 workspace/project/id 三谓词执行单行 DELETE；禁止加载 revision ORM/snapshot 或调用 history detail。
3. 0 行映射 revision 404，1 行唯一 commit，非 1 行与 execute/flush/commit 异常 rollback；commit 后无 refresh/query，服务返回 None。
4. 通过 SQL 监听和五域快照证明只触及目标修订；后续 transition 继续复用 P12F-A 原配额，不做补写。

## 3. DELETE 路由

1. 更新文件顶四字段，导入新服务/错误；动态 revision 路由下增加 DELETE，不得改变静态 `/page`、`/search` 优先级或旧路由函数。
2. 手工读取并拒绝任意 query 与非空 body，固定脱敏 422；合法请求调用服务，成功返回空 204 + no-store。
3. 复用 `get_workspace_id` 与中间件 CSRF；project/revision/delete_failed 映射固定 status/code/message/no-store，不反射路径参数或异常。
4. `entities.py` 只同步类注释，结构、约束、索引和字段字节不变；测试/AST 必须证明零 schema 变化。

## 4. 反假绿与兼容回归

1. 成功测试同时断言响应、目标消失、非目标/当前态/检查点完整、commit 次数和 SQL 三谓词；不能只断言 204。
2. 404/422/权限/故障测试均以操作前后数据库快照精确相等证明零写；敏感标记必须真实进入请求或异常后再验证无泄漏。
3. list/page/search/detail/comparison/body-diff/restore 走真实路由，不以 mock 返回冒充；删除后的 404 与非目标有序结果都需断言。
4. 故障注入分别打到 execute/flush/commit 的真实删除路径；rollback 后使用独立 Session 复核持久数据。
5. 扫描禁止宽泛状态、恒真/条件断言、吞异常、只计调用不验参数、xpass/skip；生产禁止整实体/snapshot/current/checkpoint/多行删除/日志扩展。

## 5. 串行验收与交付

按契约第 7 节从专项、受影响回归、auth 到后端全量逐组串行执行，禁止 xdist；再运行 `py_compile`、`git diff --check`、精确四文件、空暂存区、实体结构与禁区扫描。

Grok 通过消息箱发 `review_request`，必须包含真实 failure-first、精确文件清单、每组结果、事务/SQL/权限/零副作用证据、最终哈希、风险和未做项；不得暂存、提交或推送。Codex 仅允许四文件内最小返修，独立验收后才可中文提交和推送。

## 6. 未做

不做前端确认/重载、多选/批量/范围删除、软删除/回收站/撤销、自动清理、命名/固定/标签、检查点删除、审计报表、导出/分享、跨项目历史、多人协作、SSE/WebSocket、表/迁移/依赖/配置变更。P12F-G-B 必须在 A 包完成后重新审计冻结。
