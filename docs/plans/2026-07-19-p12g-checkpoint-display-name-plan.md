# P12G 手动检查点展示名称实施计划

> **执行者：Grok**：严格十二文件，按任务逐步执行；先形成真实 failure-first，再实现最小生产代码；只自测并通过协作消息箱请求审查，不暂存、不提交、不推送。

**目标：** 为每项目最近 20 条手动/安全检查点增加可选展示名称，并在技术标、商务标共用检查点面板中完成保存、覆盖和清除。

**架构：** SQLite 检查点表增加 nullable `display_name`；独立服务以 workspace/project/checkpoint 三重作用域执行单列 UPDATE；既有创建、列表、详情元数据统一增加 `displayName`；共享面板通过精确 PATCH 原位更新，不触发快照、恢复或 editor-state 重载。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、SQLite、React、TypeScript、Playwright、pytest。

---

## 任务 1：冻结范围并建立真实后端红测

**文件：**

- 修改：`backend/tests/test_editor_state_checkpoints.py`
- 新增：`backend/tests/test_p12g_checkpoint_display_name.py`

**步骤：**

1. 在既有检查点测试中机械把元数据精确键集从六键改为七键、详情改为八键，并把 SQLite 表精确列集合增加 `display_name`。
2. 新专项先覆盖全新/旧库/幂等/失败迁移、PATCH 合法与非法值、固定错误、单列 UPDATE、rowcount、事务回滚、跨作用域、创建/list/detail/恢复不变量。
3. 不修改任何生产文件，运行：

   `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/test_p12g_checkpoint_display_name.py backend/tests/test_editor_state_checkpoints.py`

4. 记录真实 failed/passed/error 数与首个业务失败；预期至少出现无列、PATCH 404/405 或响应缺 `displayName`，不得硬编码失败数量。
5. 计算六个后端生产文件哈希，必须仍等于契约第 9 节冻结值。

**完成门：** 红测确实执行到应用；测试本身可收集；生产文件零改动。

## 任务 2：实现检查点名称列与幂等迁移

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/core/database.py`
- 测试：`backend/tests/test_p12g_checkpoint_display_name.py`
- 测试：`backend/tests/test_editor_state_checkpoints.py`

**步骤：**

1. ORM 只新增 nullable `display_name VARCHAR(160)`；更新四字段注释，明确不进快照/恢复/裁剪。
2. 新增 SQLite 专用幂等迁移：表不存在/列存在 no-op；否则单条 `ALTER TABLE ... ADD COLUMN`；失败向外抛出。
3. 在 `ensure_schema_columns` 唯一外层事务中显式调用，不吞迁移异常。
4. 逐条运行迁移专项，确认全新库、旧库、重复执行和故障回滚通过。
5. 运行两个后端测试文件；此时 PATCH 和响应相关测试仍应失败，迁移测试必须转绿。

**完成门：** 只增加一列；无新表、索引、依赖；失败回滚有物理数据库证据。

## 任务 3：实现独立名称服务与精确 PATCH

**文件：**

- 新增：`backend/app/services/editor_state_checkpoint_name_service.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/api/editor_state_checkpoints.py`
- 测试：`backend/tests/test_p12g_checkpoint_display_name.py`

**步骤：**

1. 在新服务实现独立 NFKC、1–40 码点、首尾空白、C0/C1/DEL、U+2028/U+2029 和双向控制字符校验；`null` 清除。
2. 项目只投影 `Project.id`；UPDATE 精确三谓词且只写 `display_name`。
3. 精确处理 `rowcount 0/1/其它`；所有失败 rollback；成功 flush + 唯一 commit，之后零查询。
4. Schema 新增精确请求/响应类型；路由自行有界读取原始 body 并统一固定脱敏错误，拒绝 query。
5. 按专项小组依次运行值合同、作用域、事务、SQL 投影和脱敏测试，再运行完整新专项。

**完成门：** PATCH 成功体精确一键/no-store；无快照读取、无当前态或修订写入、无异常原文泄漏。

## 任务 4：升级既有检查点元数据而不改变恢复语义

**文件：**

- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/api/editor_state_checkpoints.py`
- 修改：`backend/app/services/editor_state_checkpoint_service.py`
- 测试：`backend/tests/test_editor_state_checkpoints.py`
- 测试：`backend/tests/test_p12g_checkpoint_display_name.py`

**步骤：**

1. Meta Schema 增加 `displayName`，详情同步；恢复响应保持四键。
2. `_meta_from_row`、创建返回和列表显式投影增加名称；创建与 `_insert_checkpoint_row` 不接受名称参数，依靠 nullable 默认值固定为 null。
3. 列表继续只投影七个元数据列、不读正文；排序、20 条上限和裁剪只选 ID 保持不变。
4. 详情名称不参与快照校验；恢复命名检查点后安全检查点仍为 null。
5. 运行：

   `backend/.venv/Scripts/python.exe -m pytest -q backend/tests/test_p12g_checkpoint_display_name.py backend/tests/test_editor_state_checkpoints.py`

6. 再逐条运行 restore 与 P12C checkpoint restore 回归，不并发。

**完成门：** 后端聚焦与恢复回归通过；create/list/detail 为七/七/八键；恢复仍四键。

## 任务 5：建立真实前端红测并机械同步 history mock

**文件：**

- 修改：`frontend/e2e/editor-state-checkpoint-restore.spec.ts`
- 修改：`frontend/e2e/editor-state-revision-history.spec.ts`

**步骤：**

1. 在 checkpoint E2E probe 中把 `CheckpointMeta` 改为七键，所有既有合法 mock 显式增加 `displayName:null`。
2. 新建 P12G 聚焦分组，覆盖技术/商务保存、覆盖、清除、取消、坏响应、失败保值、双击、互斥、A→B 两类迟到和泄漏门。
3. history E2E 只把检查点 create mock 增加 `displayName:null`，不得改修订历史断言或生产逻辑。
4. 不修改前端生产文件，运行：

   `npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --grep "P12G" --workers=1 --retries=0`

5. 记录真实 failed/passed/did-not-run 与首个业务失败；预期严格 parser 缺键或 UI 入口不存在。
6. 前端两个生产文件哈希必须仍等于契约冻结值。

**完成门：** 红测是真实浏览器业务缺口，既有非 P12G 测试没有被删除、跳过或放宽。

## 任务 6：实现严格 API 与共用命名入口

**文件：**

- 修改：`frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts`
- 修改：`frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx`
- 测试：`frontend/e2e/editor-state-checkpoint-restore.spec.ts`

**步骤：**

1. API 类型/parser 升为精确七键并严格校验名称；新增同源无 query 的单一 PATCH helper，响应必须精确一键且等于规范化目标。
2. 共用面板显示非空名称，增加命名/重命名、输入、保存、清除和取消。
3. 名称成功只 `setItems` 原位更新目标；失败固定中文、保留名称和草稿，零重试/重载。
4. 在第一个 await 前以同步 ref 单飞；名称与 list/create/restore/toggle/其它名称操作互斥且真实 disabled。
5. 建立 mounted/project/session/generation/checkpoint 围栏；分别用 A-success hold 与 A-failure hold 切 B 后开始新请求，证明旧 finally 不解锁 B。
6. 逐条运行 P12G 聚焦测试，直至全绿；不得同时运行 history 或后端测试。

**完成门：** 技术/商务共用入口通过；网络次数和迟到证据可观测；名称仅进入同源请求体和 React 文本。

## 任务 7：Grok 自审与 review_request

**文件：** 严格十二文件。

**步骤：**

1. 逐文件审查 diff，确认没有页面/hook/CSS/共享请求层/配置/依赖/文档改动。
2. 依次运行后端两个聚焦文件、两个恢复回归；再运行 P12G E2E、checkpoint 全套和 history 全套。每条命令完成后再启动下一条。
3. 运行 Python 编译、lint、build、`git diff --check`、白名单和空暂存区检查。
4. 搜索禁区：正文投影、commit 后 query、异常原文、console/storage/Cookie/外网、宽松键集、`.skip`/`.only`、并发测试命令。
5. 通过消息箱发送 `review_request`，包含真实 failure-first、最终命令结果、十二文件清单、最终哈希、风险与明确“未暂存/未提交/未推送”。

**完成门：** review_request 可复核；任何失败或超范围先报告，不得自行扩围。

## 任务 8：Codex 独立审查、验收与交付

**执行者：** Codex。

**步骤：**

1. 对照冻结 HEAD、十二文件和最终哈希审查实现；检查 SQL、事务、脱敏、严格 parser、互斥和迟到围栏。
2. 若有缺陷，只下发最小白名单返修；Grok 重新发送 review_request。
3. Codex 按契约第 8.4 节逐条串行执行聚焦、受影响回归、必要的全量、编译、lint/build 和静态门。全量最多各一次，不重复无影响套件。
4. 发送明确验收回执；使用中文提交实现并推送协作分支。
5. 更新契约、计划、路线图、交接与联调清单，记录真实结果、偏差、消息 ID、提交和剩余项；再以独立中文文档提交推送。

**完成门：** HEAD/跟踪远端一致、工作区干净、文档事实与测试日志一致。

## 实际执行结果（2026-07-19）

- 任务 1–7 已由 Grok 在严格十二文件内完成；任务 8 已由 Codex 完成。冻结=`9696ec1`，实现=`077e7d4`。
- 初始后端红测 **37 failed / 25 passed**，首个业务失败为 PATCH 404。Codex 首轮审查否决缺键 `.get()`、伪同步单飞和未真正重叠的 A→B 迟到测试；四文件返修先得到后端 **1 failed**、前端 **1 failed / 4 passed / 3 did-not-run**，再修为真实同步单飞和 A/B 双 hold 围栏。
- 消息链：任务=`msg_a30143a9cd0743e5bc20589ccd941759`，首轮 review=`msg_1b3e0ffcfc164586a641c4c70669f058`，返修 task/review=`msg_ef6e51ac93f849a9bf58d4699519da48`/`msg_f472fcf56377451a8c92c5dbc7b69031`，Codex ack=`msg_cd2908a39cc1438186b0f41d13062443`。
- Codex 独立串行验收：后端 **62/47/1203 passed**；前端 **8/59/61/28/18 passed**；lint、build、py_compile、diff-check、十二文件、空暂存区和 SHA-256 全部通过。整仓前端沿用已验收 **318 passed** 基线，未重复扫描不受影响套件。
- 实现提交由 Codex 使用中文提交并推送；Grok 未执行任何 Git 暂存、提交或推送。文档闭环由本次独立中文提交完成。

## 交付后仍未实现

创建时命名、自动名称、检查点名称搜索/排序、检查点固定/删除/下载/分享、批量/标签/备注、跨项目检查点、完整版本时间线、多人协作，以及 MinerU/Docling 生产部署、真实语料调优、Word 整章布局和更多外部标讯来源均继续另包。
