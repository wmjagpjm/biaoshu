# P12F-B 修订历史后端游标页实施计划

> **执行者：Grok**：按四文件白名单先真实 404 红测再实现；Codex 独立审查、验收、中文文档闭环和提交推送。

> **状态**：已完成；冻结=`4ddd896`、实现=`c84a94d`，后端全量基线 **905 passed**。

**目标：** 新增固定每页 10 条的只读键集分页路由，让 P12F-A 已保留的第 11～20 条修订可被后续前端访问，同时保持旧列表 `{items}` 合同完全不变。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、SQLite/PostgreSQL 兼容键集查询、pytest。

## 1. 基线与 failure-first

1. 核对分支、HEAD/远端一致且工作区干净；读取 P12F-B 契约、P12F-A 完成交接、历史服务/路由/schema 和既有 P12C-C1 测试。
2. 只新建 `backend/tests/test_p12f_revision_cursor_page.py`，先验证新 `/editor-state-revisions/page` 路由为真实 404；不得用导入、fixture、依赖或语法错误冒充红测。
3. 红测必须记录命令、数字、首个业务失败和生产文件尚未修改的证据。

## 2. 后端只读页实现

1. schema 新增精确 `items/nextCursor` 页模型，列表项复用既有五键模型。
2. 历史服务新增固定 10 条、`LIMIT 11` 的键集页原语；游标使用 `esrc1_` 版本前缀、规范 base64url 紧凑 JSON、UTC 微秒与合法修订 ID，并严格规范往返校验。
3. 路由在动态 `/{revision_id}` 之前新增静态 `/page` GET；只传可选 cursor，成功/错误 `no-store`，非法游标固定 400。
4. 旧列表函数、路由、schema 和未知查询参数兼容语义不得改变。

## 3. 测试闭环

1. 新专项覆盖 0/1/10/11/20、两页不重不漏、并列稳定、重复确定、非法游标矩阵、跨域、SQL 五列/LIMIT 11/无主动或非零 OFFSET/COUNT、lookahead corrupt 和五域零写。SQLite 方言允许绑定为 0 的 OFFSET 占位，但源码禁止 `.offset(`。
2. 运行既有 `test_p12c_revision_history_read.py` 与 P12F-A 四文件回归，证明旧 `{items}` 列表、详情、写入 20/20 MiB、恢复与 browser PUT 不回归。
3. 运行必要 P12D/P12E 只读回归、`py_compile`、`git diff --check`、四文件白名单和空暂存区；后端全量留给 Codex 独立执行。

## 4. 审查与提交

1. Grok 只发送 review_request，如实报告红/绿数字、游标格式、SQL 证据、精确四文件、风险和未做边界；不得提交或推送。
2. Codex 审查游标规范、错误优先级、静态路由顺序、键集谓词、无正文投影、旧合同兼容和只读零写；不合格只下发最小返修。
3. Codex 独立专项、受影响回归和后端全量通过后，先提交实现，再更新交接/路线图/联调清单/契约/计划并单独提交推送。

## 5. 明确未做

本包不修改前端或 E2E，不提供“加载更多”；不新增客户端 limit/offset/页码/total/hasMore；不做搜索、筛选、删除、命名、固定、导出、分享、缓存、跨项目历史、多人协作、历史回填或后台清理。P12F-C 必须另行冻结。

## 6. 实际执行记录

Grok 原任务=`msg_b044740a30cc4e82ac4c98c4c42731c4`，真实 failure-first **27 failed / 3 passed**；首个业务形态是 `/page` 被动态 revision ID 路由吞掉为旧 404，生产三文件在红测前未修改。首版专项 **30 passed**，review_request=`msg_5df53113b2894ea984694c8d21d15601`。

Codex 一轮最小返修只允许服务和新测试两文件，修复 Windows 最大时间平台依赖、编码端 pre-1970 不可用游标与 lookahead 恒真断言。返修 task/review_request=`msg_628cbdef5bf24ac09f4f08d676f79d25`/`msg_6a45abaf4cc141d7bcf066c809b7a11f`。Codex 独立通过新专项/受影响 7 文件/后端全量 **34/171/905 passed**，仅既有 Starlette/httpx 弃用告警；静态、diff、四文件和空暂存区门禁通过，验收回执=`msg_6163277b22da433a8ae672560eeec3b5`。
