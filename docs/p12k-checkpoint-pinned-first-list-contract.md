# P12K 检查点固定优先默认列表契约

模块：P12K editor-state 检查点默认列表固定优先排序

用途：在 P12J-A/B 已交付固定存储、保护裁剪、八键读取与共用前端入口之后，让用户下一次展开或显式刷新默认检查点列表时优先看到固定项，同时保持搜索与所有写路径原样。

对接：`list_editor_state_checkpoints`、既有 `GET /api/projects/{projectId}/editor-state-checkpoints`、P12J-A pin PATCH、P12J-B 共用检查点面板。

二次开发：Grok 只能在两文件白名单内先新增真实 failure-first 专项测试，再修改一个生产服务文件并自测；不得暂存、提交或推送。Codex 负责独立规划、受限审查、独立验收、中文文档、提交和协作分支推送。

状态：2026-07-20 已完成实现、独立审查、分级验收并推送；代码审计基线=`90cfd58`，契约冻结=`fe0fa08`，启动口径修订=`ff48495`/`6666af6`，实现=`3c3cbf9`。生产文件冻结 SHA-256=`20A0FBACFE20DF4D6FE0157B2DF6F41436EDAC5B298F6D2174803E7A66CF4DC3`，最终 SHA-256=`8C08B546E0DB8FA00FE4D6E15FB93A23650F15FA12C42E23EC100ED6EA7E371E`。

## 1. 选择理由与严格边界

1. 检查点现有每项目最多 20 条、固定最多 5 条；另做分页收益有限。跨项目时间线与多人协作会扩大权限、身份和会话边界。固定优先默认列表只改变一个现有只读查询的稳定排序，是可独立验收的最小用户价值包。
2. 只改默认 GET 列表：`is_pinned DESC, created_at DESC, id DESC`。固定组与普通组内部仍按创建时间倒序、同时间按 ID 倒序；返回仍最多 20 条、顶层精确 `{items}`、每项精确八键。
3. search 明确保持 P12I/P12J-B 原合同：候选仍为最新 20 条 `created_at DESC,id DESC`，输出仍保持候选时间倒序；固定旧行不得挤入搜索候选窗口，搜索中的 pin 成功仍只原位更新，不重排。
4. P12J-B 前端成功路径仍 `map` 原位更新；用户点击固定/取消固定后当前列表不瞬时重排，只有下一次默认列表 GET（展开、刷新、创建/恢复后的既有重载）才采用固定优先顺序。不得为本包修改前端或增加额外请求。
5. 不改表、迁移、模型、Schema、路由、pin service、5 条/10 MiB 配额、20 条保护裁剪、create/detail/search/restore/name/delete、修订历史、页面/hook、共享请求层、依赖或配置。

## 2. 默认列表排序合同

1. `list_editor_state_checkpoints` 的显式八列投影保持不变；`snapshot_json` 仍不得进入列表 SQL。唯一生产语义变化是 ORDER BY 首项增加原始固定列倒序。
2. ORDER BY 必须精确等价于：

```python
type_coerce(EditorStateCheckpointRow.is_pinned, Integer).desc(),
EditorStateCheckpointRow.created_at.desc(),
EditorStateCheckpointRow.id.desc(),
```

禁止 Python 排序、二次查询、UNION、CASE、COUNT、OFFSET、ORM 整实体加载或先截断再排序。
3. 固定值仍必须经现有 `_validate_is_pinned_raw` 完整校验；原始 `2` 不得因排序被当作真值，必须固定 `editor_state_checkpoint_corrupt`、no-store 且五域零写。
4. 在正常存储不超过 20 条时，列表返回全部行：先全部固定项，再全部普通项。对绕过约束形成的超过 20 条异常/旧数据，仍只按新排序取前 20 条；本包不做后台修复、回填或扩容。
5. 空列表、单条、全固定、全普通、混合固定、并列时间戳均必须稳定；相同固定状态与时间使用 ID 倒序消除不确定顺序。

## 3. 搜索与写路径冻结合同

1. `search_editor_state_checkpoints` 的 ORDER BY 必须继续精确 `created_at DESC,id DESC`，不得加入固定列；候选上限仍 20，不补扫第 21 条，不因名称/内容或固定状态改变候选集合。
2. 既有 create 返回 `isPinned=false`；pin/unpin PATCH 仍只改目标一列且不触发 list；安全检查点初始 false；裁剪继续保护全部固定项、本轮安全点和最新普通项。
3. detail、search、create 的八/九键、严格 raw int 0/1、名称/内容 NFKC+casefold、坏未命中候选整次失败、三重作用域、Cookie/CSRF/RBAC/no-store 与脱敏错误全部不变。
4. 列表读取不得 flush/commit/写 editor-state、检查点、修订、项目、任务或审计；不得访问知识库、文件、任务、导出、外网或浏览器状态。

## 4. Failure-first 与反假绿证据

1. 第一阶段只新建 `backend/tests/test_p12k_checkpoint_pinned_first_list.py`；生产服务 SHA-256 必须仍等于冻结值。至少一个真实业务红测必须因旧列表仍按纯时间倒序而失败；import/收集错误、skip/xfail、空测试、宽状态集合或未运行不算红测。
2. 专项必须通过真实 HTTP 与真实 SQLite 证明：
   - 较旧固定项排在较新普通项之前；
   - 固定组和普通组分别按时间/ID 倒序；
   - PATCH 固定后下一次 GET 上移，取消固定后下一次 GET 回归时间位置，PATCH 自身不额外 GET；
   - create 的普通新项仍位于固定组之后；
   - 原始非法 `is_pinned=2` 固定失败且五域零写；
   - 其它项目/空间不参与排序或响应。
3. 搜索冻结证据必须用 21 条直接种子：最旧第 21 条即使固定且命中，也不得进入搜索候选；最新 20 条多项命中仍按时间/ID 倒序。禁止只做源码字符串断言冒充行为覆盖。
4. SQL/AST 门必须分别定位 list 与 search 函数，证明 list 精确三项排序、search 精确两项排序；不得用文件级 `"is_pinned" in source`、宽 OR、集合忽略顺序或只断言首项。

## 5. 两文件白名单与冻结哈希

| 文件 | 冻结 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/services/editor_state_checkpoint_service.py` | `20A0FBACFE20DF4D6FE0157B2DF6F41436EDAC5B298F6D2174803E7A66CF4DC3` | 仅默认列表文档与精确三项 ORDER BY |
| `backend/tests/test_p12k_checkpoint_pinned_first_list.py` | 新建 | P12K failure-first、真实行为、零写、隔离、SQL/AST 与搜索冻结证据 |

禁止修改既有测试以适配实现，禁止修改 API/Schema/模型/迁移/pin service/前端/E2E/配置/依赖/锁文件/脚本/其它文档。若两文件不足，Grok 只能发送 `question`，不得自行扩围。

## 6. 串行验收门

所有命令逐条串行；pytest 禁止 xdist 或并发分组：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12k_checkpoint_pinned_first_list.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_p12g_checkpoint_display_name.py tests\test_p12h_checkpoint_delete.py tests\test_p12i_checkpoint_search.py tests\test_p12j_checkpoint_pin.py tests\test_p12k_checkpoint_pinned_first_list.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\services\editor_state_checkpoint_service.py tests\test_p12k_checkpoint_pinned_first_list.py
```

最后执行 `git diff --check`、精确两文件、空暂存区、最终 SHA-256、list/search 独立 AST/SQL 顺序、写调用与弱断言扫描。本包不修改或运行 Playwright；前端沿用 P12J-B checkpoint **82 passed** 与整仓 **318 passed** 基线，不得冒充本包重跑。

冻结时的命令清单用于确保本包至少存在一次完整后端全量证据，不要求 Grok 与 Codex 重复运行同一全量。交付阶段按用户指示采用分级验收：Grok 提供专项、受影响集和一次后端全量；Codex 独立复跑受影响集并完成静态、哈希和范围审查，不再重复 27 分钟级全量。后续小型局部包默认由 Grok 跑专项/受影响集，Codex 根据风险至多补一次全量；只有跨域、迁移、鉴权、共享状态或回归信号要求时才运行全量。

## 7. Grok 回执合同

Grok 只发送一个完整 `review_request`：真实 failure-first 与冻结生产哈希、逐条串行命令和精确结果、两文件列表/最终哈希/空暂存区、默认列表三项顺序、搜索两项顺序、pin/unpin 下一次 GET、21 条搜索冻结、非法固定零写、空间隔离、静态门和明确未做项。额度/认证/进程中断只发送 `status`，禁止补造数字或完成结论。

## 8. 明确未做

不做当前列表瞬时重排、前端排序开关、固定分组标题、搜索固定优先、分页/游标、批量固定、固定数/容量展示、乐观更新、自动重试、创建时命名、标签/备注、跨项目检查点、完整时间线、导出/分享、多人协作、presence、SSE/WebSocket、表/索引/依赖变更。

## 9. 完成交付与验收证据（2026-07-20）

1. Grok 初始任务/review=`msg_24d08a0202954060b4c4ab3b0a35942d`/`msg_131b165976c64b2fb05ceb0792122a5c`。failure-first 为 **8 failed / 4 passed**，首个真实业务失败是“较旧固定项仍排在较新普通项之后”；其中一个隔离用例曾因测试夹具构造 `Workspace` 的 `TypeError` 失败，Grok 在修改生产代码前先修正夹具，未将该基础设施错误冒充业务红测。
2. 初始实现后，Grok 串行通过专项 **12 passed**、六文件受影响集 **132 passed**、后端全量 **1273 passed in 1674.75s**。Codex 审查发现 PATCH 不额外触发默认列表、list/search ORDER BY 精确序列等 test-only 证据不足，下发返修 task/review=`msg_b1b3d1fb809c4a579ed35dfd9a875615`/`msg_4e2f742d8ac2469fad123e367922f6fa`；返修保持生产哈希不变，并通过专项 **12 passed**、受影响集 **132 passed**。
3. Codex 独立串行复跑六文件受影响集 **132 passed in 106.74s**，并通过 `py_compile`、`git diff --check`、严格两文件、空暂存区、SQL/AST 精确排序和 SHA-256 核验。根据分级验收策略，Codex 未重复 Grok 已完成的后端全量；验收确认=`msg_3048a39db0c04969978a7e2dd7ea0c60`。
4. 最终文件哈希：生产服务=`8C08B546E0DB8FA00FE4D6E15FB93A23650F15FA12C42E23EC100ED6EA7E371E`，专项测试=`49A6FEA0F2C08FF44E9E7CC57FC216A967B03EFCF6DA6ED78624DDC573821591`。实现严格只有两文件，Grok 零 Git 写操作；Codex 提交并推送实现=`3c3cbf9`。
