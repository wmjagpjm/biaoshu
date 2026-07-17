# P13-A 任务 SSE 工作空间鉴权闭环实施计划

> **执行者：Grok**：严格按三文件白名单先真实业务红测再实现；Codex 负责受限审查、独立验收、中文文档闭环、提交与推送。

> **状态**：已完成并推送；冻结=`e8dfa61`、实现=`1509aa2`，后端全量新基线 **918 passed**。

**目标：** 让单任务 SSE 与普通任务 REST 使用同一工作空间、成员和 bid_writer 角色语义，并让连接前校验及每次快照读取都保持短 Session，不改变原生 EventSource 和既有事件合同。

**技术栈：** FastAPI 0.139、Starlette StreamingResponse、SQLAlchemy 2、SQLite、pytest/TestClient。

## 1. 基线与 failure-first

1. 核对分支只能为 `collab/grok-code-codex-review`，冻结 HEAD/远端一致且工作区干净；完整读取 P13-A 契约、`tasks.py`、`deps.py`、`task_service.py`、认证中间件、SSE 与 RBAC 既有测试。
2. 只新建 `backend/tests/test_p13a_task_sse_workspace_auth.py`；先证明 required 非 bid_writer、非成员头和 active workspace 三类真实旧行为不符合契约。
3. 记录 failure-first 命令、数字和首个业务失败；确认两个生产文件尚未修改，测试可正常收集且没有外网、等待型终态或不受控后台任务。

## 2. 连接前短会话鉴权

1. 在 `tasks.py` 增加私有 SSE 专用依赖，接收 path 参数、Request、Settings 与可选 `X-Workspace-Id`。
2. 依赖内部打开 `SessionLocal`，显式调用既有 `get_workspace_id(request, db, settings, header)`，再调用 `task_service.get_task`；统一映射既有项目/任务 404，所有路径 finally 关闭。
3. SSE 路由只接收该依赖返回的 workspace 字符串，不接收 request-scope `get_db`，不捕获 Session/ORM 行；删除手写默认空间选择与重复 ensure 逻辑。
4. 更新文件顶和函数注释，准确写明 required 使用活动空间、disabled 保持头兼容、长连接不得持有请求 Session。

## 3. 流内工作空间再校验

1. `_read_task_snapshot` 签名改为 `workspace_id, project_id, task_id`。
2. 每次新开 `SessionLocal` 并复用 `get_task`；项目或任务任一不存在/越界统一返回 `None`，同空间才 `task_to_dict`，finally 关闭。
3. 异步生成器的 `run_in_threadpool` 每轮精确传入连接前授权 workspace；不得使用全局默认、头原文或只按任务主键读取。
4. 保持签名比较、snapshot/task/heartbeat/terminal、断开、超时和响应头原样。

## 4. 测试与回归

1. 新专项覆盖 required 401、三角色 403、非成员 403、默认/活动/显式成员空间成功、跨空间 404、disabled 兼容、query token 无效、错误体脱敏。
2. 用可观察 Session 与快照替身证明：连接前 Session 在首帧读取前关闭；每轮接收授权 workspace；直接快照读取同空间成功、跨空间为空且短 Session 关闭。
3. 运行既有 `test_task_sse.py`、`test_auth_rbac.py`、`test_p12b_delayed_writer_fences.py`；不得通过放宽原测试或修改认证公共语义取得绿测。
4. 运行 `py_compile`、`git diff --check`，核对精确三文件、无暂存内容和无非白名单未跟踪文件；后端全量由 Codex 独立执行。

## 5. 审查与提交

1. Grok 只发送 review_request，报告真实红/绿数字、实现结构、Session 生命周期证据、三文件、风险与未做；不得提交或推送。
2. Codex 审查错误优先级、角色/成员边界、active workspace、disabled 兼容、流内再校验、会话关闭、公开载荷和 SSE 兼容；问题只下发最小白名单返修。
3. Codex 独立专项、受影响回归和后端全量通过后提交实现并推送；再更新 HANDOFF、路线图、联调清单、契约和计划，单独提交中文闭环并推送。

## 6. 明确未做

不修改 `deps.py`/中间件/前端/E2E/数据库；不做事件游标、重放、多任务总线、WebSocket、presence、工作空间 UI、URL 鉴权、审计扩展或任务 schema 变更。本包不是 P12F 搜索/删除的延续。

## 7. 实际执行记录

原任务/首轮 review_request=`msg_7b03139e43024424ab5707426d2b02bf`/`msg_ea83529fa69a42c7a91a88ac775f96d3`。生产文件未改时真实 failure-first 为 **8 failed / 5 passed**；实现后 Grok 专项/指定回归为 **13/72 passed**。

Codex 首轮审查发现测试泄漏断言的恒真 `or`、secret marker 跳过、宽松三参和宽松 404，返修 task/review_request=`msg_b7cb9c7720a646a0976591d5cc4d3baf`/`msg_367b8a5ef9b54e89875bc16ea3b89974`。返修只改新测试，生产两文件内容哈希未动；原始 failure-first 未重跑或篡改。

Codex 独立专项/受影响回归/后端全量 **13/72/918 passed**，仅 1 条既有弃用告警。首次全量被 20 分钟外层时限终止且没有 pytest 失败摘要；确认子进程退出后，以 40 分钟外层干净重跑得到 **918 passed in 1310.97s**。静态、diff、精确三文件与空暂存区门禁通过，验收回执=`msg_c1023b623e3e40fea59ba798676d451d`；Grok 未执行 Git。
