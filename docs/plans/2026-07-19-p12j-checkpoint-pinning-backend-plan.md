# P12J-A 检查点固定与保护裁剪后端实施计划

> **状态：2026-07-19 已完成、独立验收并推送。** 冻结=`9f304da`，实现=`8edebd4`；实现者为 Grok，审查/验收/文档/提交/推送者为 Codex。
>
> **执行者：Grok**：严格九文件；先只新增/修改三个测试文件形成真实 failure-first，再改六个生产文件；所有 pytest 逐条串行；只通过消息箱请求审查，不暂存、不提交、不推送。

**目标：** 为当前工作空间/项目内单条检查点增加 5 条/10 MiB 受限固定状态，并把创建/恢复的最近 20 条裁剪升级为“固定行 + 本轮安全检查点 + 最新普通行”的原子保护算法，同时保持既有检查点七/八键响应和前端完全不变。

**权威契约：** `docs/p12j-checkpoint-pinning-backend-contract.md`。本计划只规定执行顺序；发生歧义时以契约为准。

## 任务 1：核验冻结基线

**输出：** Grok 明确工作在冻结提交、正确分支和干净工作区。

**验证：** `git status -sb`、HEAD/上游/远端三者一致；九文件现状和七个既有文件 SHA-256 等于契约第 6 节，两个新文件不存在。

1. 完整阅读 P12J-A 契约、P12F-J-A 修订固定契约、P12A/P12B-D/P12G/P12H/P12I 检查点契约，以及九个白名单文件。
2. 不得复制 P12F-J-B 的历史响应/UI 扩展；P12J-A 的 `isPinned` 只能出现在新 PATCH 请求/响应与内部存储，不能进入既有元数据。
3. 核对没有其它 Grok/Codex 进程同时改仓库或运行 pytest；发现脏文件先发送 `question/status`，不得覆盖。

## 任务 2：只写真实 failure-first

**输出：** 三个测试文件形成可复核的业务红测，六个生产文件哈希保持冻结。

**验证：** 契约第 7 节第 1 条命令出现 PATCH/列/保护算法缺失的真实失败，测试可完整收集且无 skip/xfail。

1. 新增 `backend/tests/test_p12j_checkpoint_pin.py`，先覆盖最小合法 PATCH、ORM/SQLite 最终结构、5 条/10 MiB 与创建/恢复保护裁剪；再补请求、安全、迁移、故障和反假绿矩阵。
2. 机械更新 `test_editor_state_checkpoints.py` 的裁剪投影期望和 `test_p12h_checkpoint_delete.py` 的精确字段清单，不改其它字符或既有行为期望。
3. 串行运行新专项并记录真实失败/通过数量与首个业务失败；import、fixture、数据库污染、服务启动或语法错误先修测试，不得冒充红测。
4. 对六个生产文件重新计算 SHA-256，确认与冻结值完全一致后才能进入任务 3。

## 任务 3：实现模型与 SQLite 幂等迁移

**输出：** ORM 新库和所有受支持旧 SQLite 表都得到非空 0/1 固定列，迁移失败原子回滚。

**验证：** 新专项中的 create_all、无列旧库、已有列无 CHECK、最终结构 no-op、约束/索引/行值、DROP 前后故障注入全部通过。

1. 在 `EditorStateCheckpointRow` 于 `display_name` 后加入 `is_pinned`，同时更新模块四字段注释和表级 CHECK；默认/服务端默认均为 false/0。
2. 在 `database.py` 增加独立检查点迁移函数，并在检查点 display_name 迁移之后调用；完整复制十一列、三个旧 CHECK、外键和四个索引。
3. 用零行 DML 确保 SQLite 物理事务；显式行数核对后才 DROP，所有异常由外层 begin 回滚并阻止启动。

## 任务 4：实现保护裁剪与单条固定服务

**输出：** 创建/恢复不再淘汰固定行或本轮安全检查点；独立服务按项目锁和配额只写一列。

**验证：** 新专项覆盖 5 固定+新建、5 固定+恢复安全、并列时间/不利 ID、跨空间/项目、21 行与坏元数据、三类事务故障。

1. 在 checkpoint service 增加固定数量/字节常量和原始 0/1、快照字节校验；`_trim_checkpoints` 投影精确三列，先全验再计算保留/删除集合。
2. 保留所有固定行，再保留存在的 `protect_id`，最后按稳定倒序补普通行到 20；缺失保护 ID、坏值或超固定配额使用既有 corrupt 并让调用方全事务回滚。
3. 新建 `editor_state_checkpoint_pin_service.py`：项目级锁、21 行侦测、完整元数据验证、三谓词单列 UPDATE、同值幂等、5/10 MiB 配额、唯一 commit 与统一 rollback。
4. 新建检查点与安全检查点显式/默认 false；不得改变 display_name、created_at、快照算法、恢复 transition 或 P12H 显式删除。

## 任务 5：实现精确 PATCH 外壳

**输出：** 新增唯一 `/pin` PATCH，精确一键请求/响应和固定脱敏错误。

**验证：** 新专项真实 HTTP 覆盖合法/同值/取消、query、空/坏/超长 body、snake_case、额外/缺失键、非 bool、required/disabled、CSRF、角色与跨作用域。

1. 在 schemas 中增加 Any 承接的 `EditorStateCheckpointPinUpdate` 和精确 `EditorStateCheckpointPinOut`；业务层仅接受 `type(value) is bool`。
2. 在 checkpoint 路由增加 1024 字节读取、Pydantic 错误统一映射、服务错误映射和静态 `/pin` 路径；所有响应 no-store。
3. 禁止回显路径 ID、输入、快照、名称、版本、配额、SQL、异常、Cookie/CSRF；禁止日志、外网和新审计。

## 任务 6：自审与逐条串行验收

**输出：** Grok 自测形成可复核回执，工作区仅九文件且暂存区为空。

**验证：** 契约第 7 节五道门逐条通过。

1. 依次运行新专项、受影响回归、后端全量、py_compile；禁止 xdist、并发分组、同时启动其它 pytest 或 Playwright。
2. 审查 SQL：无 `snapshot_json`/ORM 整体/当前态/修订读取；固定/裁剪投影用原始 Integer；UPDATE/DELETE 三谓词；无越权补扫。
3. 审查事务：迁移、pin execute/flush/commit、创建 trim、恢复 trim 任一故障均全回滚；成功提交后无 refresh/补查。
4. 审查测试：无宽状态、弱 OR、恒真、固定 sleep、skip/xfail、异常吞噬、共享 SQLite 并发污染或仅字符串证据。
5. `git diff --name-only` 精确九文件；`git diff --cached --name-only` 为空；计算最终 SHA-256 并执行 `git diff --check`。

## 任务 7：请求 Codex 审查

**输出：** 消息箱中恰好一个内容完整的 `review_request`。

**验证：** 回执满足契约第 8 节，Grok 未暂存、未提交、未推送。

1. 发送 failure-first/最终数字、逐条命令、九文件/哈希、迁移/SQL/事务/权限/泄漏证据和未做项。
2. Codex 审查前不得扩围或自行修补白名单外文件；若中断只发送 `status`。
3. Codex 将独立复跑受影响回归与后端全量；任何返修都重新下发精确文件/问题边界，Grok 仍只通过消息箱回执。

## 完成标准

- 模型/迁移/PATCH/配额/固定+安全保护裁剪全部有真实行为证据；
- 既有七/八键响应、前端、搜索/命名/删除/恢复语义不变；
- Grok 完成实现与自测但没有 Git 写操作；
- Codex 独立审查和串行验收后，才允许中文实现提交、文档闭环提交及协作分支推送。

## 执行结果（2026-07-19）

- Grok 初始真实 failure-first **16 failed / 3 passed**；首轮专项/受影响回归/后端全量 **19/140/1254 passed**。
- Codex 审查下发三文件受限返修，真实红测 **2 failed / 0 passed**，修后专项/受影响回归 **23/140 passed**；关闭不完整迁移误判、空候选保护 ID 静默返回、真实 5+15 创建/恢复边界和测试假绿。
- Codex 最终独立串行通过专项/受影响回归/后端全量 **23/140/1258 passed**，全量耗时 **1454.53 秒**，仅 1 条既有弃用告警；`py_compile`、diff-check、九文件、空暂存区、哈希与安全静态门均通过。
- 本包不改前端，未运行 Playwright；前端沿用 **318 passed** 基线。Grok 未执行 Git 写操作，Codex 已提交并推送实现 `8edebd4`。
