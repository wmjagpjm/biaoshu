# P12F-A 修订有限保留扩容与总字节配额实施计划

> **执行者：Grok**：严格按六文件白名单先红后绿；Codex 独立审查、验收、中文文档闭环和提交推送。

**目标：** 把自动修订保留从固定 10 条改为“最近最多 20 条且总快照最多 20 MiB”，同时保持既有默认列表只返回最近 10 条。
**架构：** 写入服务以连续最新前缀同时执行条数/字节裁剪；只读历史服务把列表上限解耦为固定 10；不改 API、模型或前端。
**技术栈：** Python、SQLAlchemy、SQLite/PostgreSQL 兼容查询、pytest。

## 1. 基线与红测

1. 核对分支、HEAD/远端一致和工作区干净；读取 P12F-A 契约及 P12C-A/C1/C2/C3 相关服务与测试。
2. 先只修改四个既有测试文件，增加计数 20、总字节配额、连续前缀、列表仍为 10、非法元数据零删除和跨域隔离断言。
3. 串行运行最小测试并记录真实业务失败；不得用导入错误、fixture、缺依赖或语法失败冒充红测。

## 2. 写入保留策略

1. 在 `editor_state_revision_service.py` 固定 20 条与 20 MiB 两个生产常量。
2. `_trim_revisions` 只投影 id/state_version/snapshot_bytes，完整校验后按最新连续前缀累计；先到任一上限即删除当前及所有更旧行。
3. 保持三重作用域 DELETE、flush-only、同事务回滚语义；不得加载正文或引入后台清理。

## 3. 默认列表兼容

1. 在 `editor_state_revision_history_service.py` 把 `MAX_REVISIONS_LIST` 固定为 10，不再引用写入保留上限。
2. 保持列表 SQL 五列投影、顶层仅 items、既有顺序与错误映射完全不变。
3. 不修改 schema、路由、前端 parser 或 history E2E。

## 4. 受限审查与验收

1. Grok 串行运行 `test_editor_state_revisions.py`、`test_p12c_revision_history_read.py`、`test_p12c_browser_put_revisions.py`、`test_p12c_revision_restore.py`，再运行必要的 P12C/P12E 后端回归、`py_compile`、diff-check 和六文件检查；后端全量留给 Codex。
2. Codex 逐行审查常量、连续前缀、元数据校验时机、SQL 投影、三重 DELETE、flush-only、列表兼容和测试反假绿。
3. Codex 独立串行运行四文件专项、P12C/P12E 受影响回归、后端全量、`py_compile`、`git diff --check`、白名单和空暂存区。

## 5. 文档闭环

验收通过后，Codex 更新主交接、路线图、联调清单、本契约和本计划，记录真实 failure-first、专项/全量数字、Grok/Codex 消息 ID、实现提交和未做边界。P12F-A 完成后才能另行冻结 P12F-B 游标分页；本包不实现分页 API 或前端“加载更多”。
