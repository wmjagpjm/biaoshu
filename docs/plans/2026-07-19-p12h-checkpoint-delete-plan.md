# P12H 单条检查点删除实施计划

> **执行者：Grok**：严格七文件，先形成真实 failure-first，再实现最小生产代码；所有 pytest/Playwright 逐条串行；只通过协作消息箱请求审查，不暂存、不提交、不推送。

**目标：** 为技术标与商务标共用检查点面板增加单条检查点删除，后端以独立三重作用域 DELETE 物理删除恰好一行，前端显式确认、真单飞、成功原位移除、失败保值。

**架构：** 新后端服务只投影 Project.id 并执行 workspace/project/checkpoint 三谓词 DELETE；路由拒绝 query/任何非空 body，成功空 204；前端 API 只发 `{method:"DELETE"}`，共用面板用独立 flight token 和项目会话围栏管理确认与迟到响应。

**技术栈：** FastAPI、SQLAlchemy、SQLite、React、TypeScript、Playwright、pytest。

---

## 任务 1：建立真实后端红测与机械更新方法守卫

**文件：**

- 修改：`backend/tests/test_editor_state_checkpoints.py`
- 新增：`backend/tests/test_p12h_checkpoint_delete.py`

**步骤：**

1. 既有方法守卫仅从“详情 PUT/PATCH/DELETE 均 405”移除详情 DELETE；集合 DELETE 与详情 PUT/PATCH 仍精确 405，`/restore`、`/display-name` 方法不放宽。
2. 新专项覆盖成功 204 空体/no-store、query/非空 body、固定脱敏 404/422/500、required 角色/CSRF、SQL 投影/三谓词、rowcount、事务故障、commit 后零查询和全域零副作用。
3. 不修改任何生产文件，运行：

   `.\.venv\Scripts\python.exe -m pytest -q tests\test_p12h_checkpoint_delete.py tests\test_editor_state_checkpoints.py`

4. 记录真实 failed/passed/error 与首个业务失败；首个新 DELETE 应真实到达应用并返回 405，不得接受 404、2xx 或手工失败。
5. 计算后端路由哈希，必须仍等于契约第 8 节；新删除服务必须不存在。

**完成门：** 测试可收集；红测命中真实路由缺口；生产零改动。

## 任务 2：实现独立删除服务和精确 DELETE 路由

**文件：**

- 新增：`backend/app/services/editor_state_checkpoint_delete_service.py`
- 修改：`backend/app/api/editor_state_checkpoints.py`
- 测试：`backend/tests/test_p12h_checkpoint_delete.py`
- 测试：`backend/tests/test_editor_state_checkpoints.py`

**步骤：**

1. 新服务定义独立固定错误；先 `SELECT Project.id` 限定 workspace/project，再执行 checkpoint workspace/project/id 三谓词 DELETE。
2. rowcount 精确处理 0/1/其它；成功 flush + 唯一 commit 后零查询；业务/运行时失败全部 rollback，内部错误不保留异常原文。
3. 路由增加详情 DELETE：先固定 no-store，拒绝任意 query，读取原始 body 并要求零字节，再调用服务；成功返回严格空 204。
4. 禁止导入或修改模型、Schema、数据库、核心 checkpoint/restore 服务；禁止详情 GET 复用、快照读取、当前态/修订写入或安全检查点创建。
5. 逐组运行请求外壳、作用域、SQL、rowcount、事务和零副作用测试，最后运行两个后端文件完整聚焦。

**完成门：** 后端专项全绿；SQL/事务/错误/204 合同精确；四文件以内无扩围。

## 任务 3：建立前端真实红测和 DELETE 探针

**文件：**

- 修改：`frontend/e2e/editor-state-checkpoint-restore.spec.ts`

**步骤：**

1. 在既有 probe 增加 delete mode、arrived/complete 日志、按项目/检查点 hold 与成功/HTTP 失败行为；204 必须空体，query/body 进入可观测禁止日志。
2. 新增 P12H 分组，至少覆盖技术/商务成功、失败保值重试、确认/取消、`disabled=true`、同任务双击、全操作互斥、A→B success/failure 双 hold 和泄漏门。
3. E2E 不能修改或删除既有 59 条检查点测试；不能放宽网络白名单或用 route fallback 冒充成功。
4. 不修改前端 API/面板，运行：

   `npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12H" --workers=1 --retries=0`

5. 记录真实 failed/passed/did-not-run 和首个业务失败；生产 API/面板哈希必须仍等于冻结值。

**完成门：** 红测首先证明真实删除入口缺失或零 DELETE；探针能精确区分 arrived 与 complete。

## 任务 4：实现前端 API 与共用删除入口

**文件：**

- 修改：`frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts`
- 修改：`frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx`
- 测试：`frontend/e2e/editor-state-checkpoint-restore.spec.ts`

**步骤：**

1. API 新增严格 ID 校验的 DELETE helper；URL 无 query，init 精确只含 method，不发 body、不解析响应。
2. 面板增加固定确认文案、pendingDeleteId/deleteBusy、delete generation 和独立 flight token/active ref；项目切换与卸载使旧代次失效。
3. 进入确认清恢复/命名意图，确认前/取消零请求；delete 不受 `props.disabled` 限制，但与 list/create/restore/name/toggle/其它 delete 全互斥。
4. success 只 `setItems(prev => prev.filter(...))`、清确认并显示成功；failure 保留 items/确认并显示固定失败；两者均零列表或 editor-state 重载。
5. 同一 token 才可清理 flight；A/B 均已进入 hold 后释放 A，证明旧 success/catch/finally 不能污染、移除或解锁 B。
6. 逐条运行 P12H 聚焦直至全绿；不得同时启动其它 Playwright 或 pytest。

**完成门：** 技术/商务入口、真单飞、失败重试、迟到隔离和零旁路全部有动态证据。

## 任务 5：Grok 串行自测、自审与 review_request

**文件：** 严格七文件。

**步骤：**

1. 后端依次运行 P12H+既有检查点、P12G+恢复回归；前端依次运行 P12H、checkpoint 全套、history 全套。
2. 运行四后端文件 py_compile、lint、build、`git diff --check`、白名单、空暂存区和 SHA-256。
3. 静态扫描：快照 SELECT、当前态/修订写入、commit 后 query、原始异常、body/query、console/storage/Cookie/外网、`.skip/.only`、宽计数、`force:true`、`toBeGreaterThanOrEqual` 和并发测试命令。
4. 逐文件自审确认未改模型、数据库、Schema、核心恢复服务、页面/hook/CSS/共享请求层、配置、依赖、脚本或文档。
5. 发送 `review_request`，包含真实红测、最终结果、七文件、最终哈希、风险/未做项和“未暂存/未提交/未推送”。

**完成门：** 消息可复核；任何失败或越界先发 question，不带病交付。

## 任务 6：Codex 独立审查、验收与交付

**执行者：** Codex。

**步骤：**

1. 对照冻结 HEAD、七文件和哈希审查 DELETE SQL、事务、错误优先级、204/no-store、真同步单飞、失败保值和 A→B 双 hold。
2. 如有缺陷，只下发最小文件白名单返修；Grok 必须重新给出 failure-first 与 review_request。
3. Codex 按契约第 6.4 节逐条串行运行聚焦、受影响回归、必要全量、编译、lint/build 和静态门；全量最多一次，不重复无影响套件。
4. 独立验收通过后发送 ack，以中文提交实现并推送；随后更新契约、计划、路线图、交接与联调清单，再以独立中文文档提交推送。
5. 最终核验 HEAD、跟踪远端、远端引用完全一致且工作区干净。

**完成门：** 代码、测试、文档、消息链和 Git 状态全部闭环。

## 交付后仍未实现

批量/软删除、撤销/回收站、自动清理、审计、固定/置顶与保护裁剪、检查点搜索/排序、跨项目检查点、完整时间线、跨客户端互斥、多人协作、presence、SSE/WebSocket，以及 MinerU/Docling 生产部署、真实语料调优、Word 整章布局和更多外部标讯来源继续另包。
