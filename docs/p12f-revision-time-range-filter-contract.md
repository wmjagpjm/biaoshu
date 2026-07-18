# P12F-E-A 修订时间范围筛选后端契约

模块：P12F-E-A 修订历史时间范围筛选后端基础
用途：在 P12F-B/D 的五列键集游标页上增加严格 UTC 时间范围条件，并把范围、可选来源与分页位置共同绑定，供后续 P12F-E-B 前端日期筛选器使用。
对接：`editor_state_revisions` 路由、`editor_state_revision_history_service`、P12F-B/D 既有游标与来源筛选、后端新专项测试。
状态：2026-07-18 已完成只读审计，待冻结提交后由 Grok 严格三文件 failure-first 实现；Codex 负责独立审查、全量验收、中文文档闭环和提交推送。

## 1. 审计结论

现有修订页已按 `(workspace_id, project_id, created_at, id)` 建立复合索引，查询固定投影 `id/state_version/snapshot_bytes/source_kind/created_at`，并以 `created_at DESC,id DESC` 和 `LIMIT 11` 做键集分页。日期范围可以直接进入同一 SQL，无需数据库迁移、正文索引、COUNT、OFFSET、缓存或新响应模型。

真正风险不是加两个 WHERE，而是分页条件漂移：若第二页缺少或改变时间范围，游标会在另一结果集中继续，造成静默漏项或混入。因此本包先只交付后端基础，以第三版游标绑定下界、上界、可选来源和末条位置；前端日期控件、时区展示及交互生命周期留给独立 P12F-E-B。

## 2. API 合同

只扩展既有静态页，新增两个可选 query alias：

```text
GET /api/projects/{projectId}/editor-state-revisions/page?createdFrom={utcMillis}
GET /api/projects/{projectId}/editor-state-revisions/page?createdBefore={utcMillis}
GET /api/projects/{projectId}/editor-state-revisions/page?createdFrom={utcMillis}&createdBefore={utcMillis}
GET /api/projects/{projectId}/editor-state-revisions/page?sourceKind={kind}&createdFrom={utcMillis}&createdBefore={utcMillis}&cursor={esrc3Cursor}
```

时间规则：

- `createdFrom` 为包含下界，SQL 使用 `created_at >= createdFrom`；`createdBefore` 为不包含上界，SQL 使用 `created_at < createdBefore`；允许只给一个边界；两个都缺失表示无时间筛选；
- 每个显式值必须精确为 24 个 ASCII 字符的 UTC RFC3339 毫秒格式 `YYYY-MM-DDTHH:MM:SS.sssZ`，例如 `2026-07-18T00:00:00.000Z`，且范围限定在 `1970-01-01T00:00:00.000Z` 至 `9999-12-31T23:59:59.999Z`；只接受大写 `T/Z` 和三位毫秒；拒绝空串、空白、越界、日期不存在、闰日错误、时区偏移、无时区、空格分隔、大小写变体、不同小数位、首尾空白及任何别名；
- 两个边界同时存在时必须严格 `createdFrom < createdBefore`；相等或倒序固定 HTTP 400，detail 精确 `code=editor_state_revision_time_range_invalid`、固定中文 message `修订时间范围筛选无效`，不得反射输入；
- 时间范围激活后，成功响应 shape 仍精确为顶层 `items/nextCursor`，每页最多 10 条；每项和旧接口完全不变；
- 未知 `dateFrom/dateTo/start/end/search/q/limit/offset/page/order/total/hasMore` 仍按既有兼容语义忽略；旧 `/editor-state-revisions` 继续不受任何 query 影响；
- 项目不存在或跨空间固定 404 最优先；合法项目上的独立非法来源沿用 source-invalid，独立非法时间范围使用 time-range-invalid，非法/错配游标使用 cursor-invalid；成功及业务错误均 `Cache-Control: no-store`。

## 3. 游标版本与条件绑定

无时间范围时，P12F-B/D 行为必须逐字兼容：无来源使用 `esrc1 {i,t}`，单一来源使用 `esrc2 {i,s,t}`，既有编码、长度、错误优先级和测试不得改变。

任一时间边界存在时使用新 `esrc3_` 规范游标，载荷精确为 `{b,f,i,s,t}`：

- `i`：末条合法 revision ID；`t`：末条 `created_at` 的 UTC 微秒整数；
- `f`：包含下界的 UTC 微秒整数，未给下界时为 JSON `null`；
- `b`：不包含上界的 UTC 微秒整数，未给上界时为 JSON `null`；
- `s`：显式来源字面量，未筛选来源时为 JSON `null`；不得用空串、缺键或游标值替代显式 query；
- 继续使用 `sort_keys=True`、紧凑 JSON、无填充 base64url、精确键集/类型/数值闭区间及规范全等往返；V3 总长度上限固定 256，V1/V2 原 192 上限不变；布尔不得冒充整数；
- `esrc3` 第二页必须显式重复与载荷完全一致的 `createdFrom`/`createdBefore` 和 `sourceKind` 组合。缺失、额外激活、格式非法、值不匹配或范围倒序均固定 `editor_state_revision_cursor_invalid`，禁止从游标采用任何筛选条件；
- 时间范围激活时携 `esrc1/esrc2` 固定 cursor-invalid；无时间范围时携 `esrc3` 固定 cursor-invalid；来源单独筛选仍只认 `esrc2`；
- 错误优先级固定：先项目 404；其后若 cursor 具有 `esrc2_`/`esrc3_` 版本形状，优先执行对应绑定合同；V3 形游标与任何非法/缺失/错配来源或时间条件均 cursor-invalid。无 V3 形游标时先沿用 P12F-D 来源校验，再校验时间范围，最后校验游标版本。

## 4. SQL、完整性与只读边界

- 页查询始终只投影五列；WHERE 必须先含 workspace/project，再按显式合法 `source_kind`、`created_at >= from`、`created_at < before` 追加零至三个筛选谓词，再追加既有 `(created_at,id)` 键集谓词；
- 排序严格 `created_at DESC,id DESC`，查询语义上固定 `LIMIT 11`；不得读取 `snapshot_json`、当前 editor-state、检查点或详情，不得 COUNT、OFFSET、窗口函数、模糊匹配或客户端后过滤；
- 0/1/10/11/20 条、边界同值、同时间不同 ID、来源与时间组合均必须不重不漏且确定性重复；第 11 条 lookahead 也必须完整校验，损坏仍使整页固定 corrupt；
- 全程禁止 commit/rollback/flush/refresh、行锁、审计、裁剪、模型/Schema/数据库/索引修改及五域任何写入；
- 不新增依赖、外网、环境变量、计时器、日志正文或输入回显。

## 5. 三文件白名单

Grok 只允许修改：

1. `backend/app/api/editor_state_revisions.py`
2. `backend/app/services/editor_state_revision_history_service.py`
3. `backend/tests/test_p12f_revision_time_range_filter.py`（新建）

禁止修改 schema/model/database/migration、任何既有测试、前端、共享依赖、配置、锁文件、文档或 Git 历史。Grok 不得 `git add/commit/push`。

冻结前两个生产文件 SHA-256：

- `backend/app/api/editor_state_revisions.py`：`E7EED65519F249E91628D567BD1D74F699E5231ED43564CC7A4A6B439D8CBD6F`
- `backend/app/services/editor_state_revision_history_service.py`：`C391684BA10EEA29D57051663E3E56B43BA87E0CAF2576F7E8827A859DBBB49B`

## 6. Failure-first 与测试合同

Grok 必须先只新增新专项测试，两个生产文件不得先改；记录生产文件 SHA-256。首次专项必须因路由仍忽略 `createdFrom/createdBefore`、无 `esrc3` 绑定而产生真实业务失败；收集、导入、fixture、语法或数据库初始化错误不算红测。

新专项至少覆盖：

1. 下界包含、上界排除、单边/双边、空结果、0/1/10/11/20、同毫秒内微秒值和同时间 ID 稳定；
2. 与九类来源逐值组合，确认 SQL 服务端过滤而非返回后过滤；无时间范围的 V1/V2 字节级兼容；
3. V3 规范编码/解码/往返、精确 `{b,f,i,s,t}`、null/整数/布尔、边界数值、超长、填充、非法字符、额外/缺失键、非规范 JSON；
4. 第二页缺失、增加、改变、非法或倒序时间边界，缺失/改变/非法来源，以及 V1/V2/V3 交叉版本矩阵；严禁从游标反向采纳条件；
5. 所有非法时间字符串矩阵、固定错误码/中文/no-store/零回显，项目 404 和 V3 cursor-invalid 优先级；
6. 五列投影、workspace/project/source/from/before/keyset 谓词、`LIMIT 11`，并从 SQL/AST 精确证明无 snapshot/COUNT/主动 OFFSET；
7. 跨 workspace/project、lookahead 损坏、元数据损坏、五域零写、无 commit/rollback/flush/refresh/锁/审计；
8. 测试源码禁止恒真 `or`、宽泛 2xx/计数、异常二选一、条件跳过关键断言或 route/monkeypatch 旁路生产入口。

实现后 Grok 至少依次运行：新专项；`test_p12f_revision_source_filter.py`、`test_p12f_revision_cursor_page.py`、`test_p12c_revision_history_read.py` 合并回归；三个 Python 文件 `py_compile`；`git diff --check`；精确三文件白名单与空暂存区。后端全量由 Codex 独立执行。

## 7. 明确未做

本包不做前端日期控件、浏览器本地时区转换、来源多选、正文/标题搜索、命名、固定、删除、导出、分享、自动加载、总数/页码、跨项目历史、多人协作、SSE 扩展、历史回填、数据库迁移或索引调整。P12F-E-B 必须在 A 包实现验收和文档闭环后重新审计、冻结与下发。
