<!--
模块：P12B-A editor-state 全状态版本与可选 CAS 基础契约
用途：冻结当前服务端权威编辑态的规范版本算法、响应字段和可选条件写入边界。
对接：P12A 手动检查点；GET|PUT /api/projects/{id}/editor-state；responseMatrixVersion；后续 P12B-B/P12B-C/P12B-D。
二次开发：本包不是恢复功能或最终并发门；expectedStateVersion 暂为兼容期可选，禁止据此声称已阻止所有迟到任务或旧客户端覆盖。
-->

# P12B-A editor-state 全状态版本与可选 CAS 基础契约

> **状态**：只读审计完成，契约已冻结；等待后端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **前置基线**：P12A 计划/契约=`bf8ccd6`、后端=`9f53d92`、闭环=`6fd4c76`；当前后端全量 518 passed。

## 1. 为什么不能直接恢复

当前 editor-state 至少有以下写入者：

1. 技术标 `useTechnicalPlanEditors` 每次防抖保存会整包发送 outline、chapters、facts、mode、analysis 和 responseMatrix；虽然同页面串行并有矩阵版本，旧标签页仍可在恢复后发送较早整包。
2. 商务标 `useBusinessBidWorkspace` 防抖发送 parsedMarkdown 与四类 business 字段；现有会话代次只隔离项目切换，不能识别另一标签页或恢复后的旧内容。
3. `task_service` 的解析、分析、大纲、正文和批量正文任务，以及 `business_task_service`、`revise_service`，会在模型/解析完成后写入；任务可能在恢复之前启动、恢复之后才提交。
4. 个人兼容 `parse-callback` 与 P8C 一次性 callback 直接写 parsedMarkdown；P8C 票据当前没有编辑态版本绑定。
5. M3-D apply/consume 直接写 chapters；它有章节 base/after 漂移校验，但不是全状态版本。
6. 模板从快照只创建全新项目，不覆盖既有 editor-state，因此不是既有项目恢复竞态；P12A 创建检查点只读当前状态，也不是写入者。

仅在恢复事务中锁项目无法阻止未携带版本的旧 autosave 或任务在恢复提交后继续写入。安全恢复必须先建立服务端全状态版本，再分阶段让浏览器和延迟写入者携带/校验版本，最后才开放恢复。

## 2. 规范全状态版本

GET/PUT editor-state 成功响应新增 `stateVersion`，算法必须与 P12A 检查点 `stateVersion` 完全相同：

1. 从服务端规范输出只抽取以下精确 13 键：`outline`、`chapters`、`facts`、`mode`、`analysis`、`responseMatrix`、`guidance`、`parsedMarkdown`、`businessQualify`、`businessToc`、`businessQuote`、`businessCommit`、`analysisOverview`。
2. 使用 `ensure_ascii=False`、`sort_keys=True`、紧凑分隔符和 `allow_nan=False` 生成 UTF-8 标准 JSON。
3. 对上述字节做 SHA-256，取前 32 位小写十六进制，加 `esv_` 前缀。
4. 不包含 `projectId`、`updatedAt`、派生 `responseMatrixVersion`、项目/工作空间名称、用户、任务、路径、Token 或检查点元数据。
5. 空项目也必须有稳定版本；同一规范内容在不同项目可得到相同版本，因为版本描述内容而非身份。

共享算法以 `editor_state_service` 为权威。P12A `editor_state_checkpoint_service` 必须委托同一算法或保留兼容包装后委托，禁止复制出第二套可漂移的键集/序列化规则。P12A 创建检查点时，若当前 editor-state 在同一锁内未变化，检查点版本必须精确等于当前状态版本。

版本只描述规范内容，不是数据库行号、时间戳、审计 ID 或密钥；不能反推正文。任何响应只返回版本串，不返回哈希输入或数据库原始 JSON。

## 3. 可选条件写入

`EditorStateUpdate` 新增可选 `expectedStateVersion`：

- 格式必须精确为 `esv_` + 32 位小写十六进制；错误类型、空白、大写、长度或前缀固定 422，且不得进入写服务。
- 未携带时保持现有兼容写入语义；这是 P12B-A 的迁移窗口，不是最终安全门。
- 携带时，服务端必须先取得现有项目级数据库写锁，再从锁后当前行构造规范全状态版本并比较。
- 相等才允许执行本次部分更新；成功响应返回更新后的 `stateVersion`。
- 不相等固定 `409`，detail 只能包含：
  - `code=editor_state_version_conflict`
  - 固定中文 `message=编辑内容已被其他操作更新，请重新载入后再保存`
  - `currentStateVersion`
- 冲突不得回显当前 editor-state、矩阵、检查点正文、项目 ID、路径、SQL、异常或客户端投稿字段；整包零写，包含同时投稿的非矩阵字段和矩阵字段。

当请求同时携带 `expectedStateVersion` 与既有 `responseMatrixVersion` 时：

1. 只取得一次项目锁并只做一次锁后读取。
2. 先比较全状态版本；若不匹配返回全状态固定 409，不进入矩阵三方冲突正文路径。
3. 全状态匹配后再比较矩阵版本；矩阵不匹配继续沿用既有 409 与三方合并契约。
4. 两项都匹配才写；不得因全状态版本新增而放宽矩阵版本。

## 4. 事务、资源和兼容边界

- CAS 的“锁后读版本→比较→部分写→commit”必须同一 Session 事务；SQLite 继续用 projects 无副作用 UPDATE 取得文件库写锁，其他方言使用 project/editor-state `FOR UPDATE`。
- CAS 冲突、项目不存在、规范序列化异常或数据库异常必须显式 rollback，不得留下写锁或部分字段。
- 未携带 expected 的兼容写入、任务、callback、M3-D 仍会改变下一次 GET 得到的 `stateVersion`；本包不要求它们在写前比较版本。
- `updatedAt` 单独变化而 13 键内容不变时，`stateVersion` 必须不变；响应矩阵内容变化会通过 `responseMatrix` 改变全状态版本。
- 不新增表、列、迁移、触发器、版本历史记录、自动检查点或审计正文；版本按需从当前规范状态计算。
- 不把版本放入 P12A `snapshot` 的 13 键，也不改变 P12A 2 MiB/最近 20 条/只读 API。

## 5. API、权限与缓存

- GET/PUT editor-state 的现有鉴权、工作空间、CSRF、technical/business 兼容和错误保持不变。
- 本包只新增 GET/PUT 成功响应 `stateVersion`、PUT 可选请求字段 `expectedStateVersion` 和固定全状态 409。
- 不新增 URL、query、restore、history、checkpoint 写入、delete、download 或前端入口。
- 版本冲突响应不得带历史正文；P12A 检查点详情仍需显式读取。
- 现有 editor-state 路由未统一 `no-store`，本包不得顺带扩大缓存策略；若后续需要，另立兼容审计。

## 6. 精确文件白名单

Grok 只允许修改/新增：

1. `backend/app/api/schemas.py`
2. `backend/app/api/projects.py`
3. `backend/app/services/editor_state_service.py`
4. `backend/app/services/editor_state_checkpoint_service.py`
5. `backend/tests/test_editor_state_full_version.py`（新增）

禁止修改模型、数据库基础设施、P12A 表/API/既有测试、task/business/revise/callback/M3-D、前端、依赖、文档或脚本；不得 commit/push。

## 7. 反假绿验收

专项至少真实覆盖：

- 空态、技术标、商务标版本由测试独立规范序列化/哈希后精确相等；格式严格；敏感/派生字段排除。
- P12A 同一时刻创建的检查点版本与当前 editor-state GET 版本精确相等；检查点 snapshot 仍只有 13 键。
- 当前 expected 成功写入并返回新版本；内容实际变化时版本变化；仅 updatedAt 变化时版本不变。
- 过期 expected 固定 409、固定最小 detail、所有投稿字段零写；跨项目/空间仍按既有 404。
- expected 格式错误 422；缺失 expected 保持兼容成功，并明确证明这不是安全恢复门。
- 同一旧 expected 的两个真实并发写入最多一个成功，另一个固定全状态 409；禁止顺序调用假装并发。
- expected + responseMatrixVersion 的全状态冲突优先级与既有矩阵冲突均真实验证。
- 冲突后 Session 无打开写事务；错误响应不含正文、ID、路径、SQL、异常或测试秘密。
- P12A 专项、editor-state/矩阵/M3-D/P8C/模板受影响回归与后端全量继续通过。

禁止 `or True`、宽泛状态码集合、捕获忽略、客户端自报版本当服务端当前版本、只测哈希函数不走 API/数据库、顺序请求冒充并发或以 `updatedAt` 替代规范版本。

## 8. 后续安全门

1. **P12B-B**：技术标和商务标 GET 保存 `stateVersion`，每次 PUT 携带 expected；整态冲突停止自动保存并要求显式重载；处理在途 PUT、项目切换与多标签页。
2. **P12B-C**：任务创建、个人 callback 启动、P8C 票据签发和 M3-D 操作绑定版本或等价字段级 base；迟到写入必须在数据库事务中拒绝，不能只靠前端。
3. **P12B-D**：恢复必须携带 expected current version，在同一锁内创建恢复前安全检查点、验证目标检查点、原子替换全状态并返回新版本；同时定义旧 M3-D 批次与在途任务处置。
4. 任一安全门未完成前，不得新增 restore API/按钮，不得把 P12A 详情直接 PUT 回当前状态，不得声称已实现通用历史或安全恢复。
