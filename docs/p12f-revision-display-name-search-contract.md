# P12F-I 修订名称与可见内容联合搜索契约

模块：P12F-I 技术标/商务标共用修订名称与可见内容联合搜索
用途：把 P12F-H 的可选展示名称纳入 P12F-F-A/B 既有显式搜索，同时保持候选上限、严格校验、筛选、返回形状和数据最小化不变。
对接：`editor_state_revision_history_service.list_editor_state_revision_search`、既有内容搜索专项、共用修订面板与 history E2E。
二次开发：本包只允许四文件；Grok 先写真实 failure-first，再实现并自测，不得暂存、提交或推送；Codex 负责审查、独立验收、中文闭环与协作分支推送。

状态：2026-07-19 已完成只读审计、Grok 受限实现与 Codex 独立验收；冻结=`060191e`，实现提交=`008e443`，验收回执=`msg_d954063f489248babb027b9bb335f666`。

## 1. 选择理由与范围

1. P12F-H 已让 list/page/search/detail 统一返回六键元数据，search SQL 也已经投影并严格校验 `display_name`，但匹配函数仍明确排除名称；因此联合搜索只需复用已验证元数据，不新增数据读取或接口。
2. 本包优先于固定/置顶。固定会改变 P12F-A 的 20 条/20 MiB 连续最新前缀、所有 transition 事务和“全为固定项时是否拒绝业务写”的失败语义，必须另立高风险配额契约，禁止顺手混入。
3. 既有唯一接口保持：`POST /api/projects/{projectId}/editor-state-revisions/search`。请求仍严格四键 `query/sourceKind/createdFrom/createdBefore`，响应仍 `{items}`，每项仍精确六键；不增加 mode、字段选择、分页、片段、高亮、分数或 total。
4. 前端仍是显式按钮/Enter 搜索，不做自动搜索、防抖、建议、历史或缓存。只把用户可见语义从“内容搜索”准确改为“名称或内容搜索”。

## 2. 后端联合匹配合同

1. 顺序继续固定：项目存在性 → 来源 → 时间范围 → 关键词；SQL 继续 workspace/project 三重作用域、可选来源/时间、`created_at DESC,id DESC`、七列投影和 `LIMIT 20`。
2. 必须先完整物化并严格校验全部候选的六键元数据和规范 13 键快照，再开始任何匹配。即使名称已命中，候选快照损坏、元数据损坏或预算超限仍使整次请求固定 `editor_state_revision_corrupt`；禁止用名称命中短路校验。
3. 对已通过 `_validate_stored_display_name` 的非 null 名称，使用与查询/快照相同的 NFKC + casefold 连续字面子串规则。匹配条件精确为“名称命中或既有允许快照字段命中”；null 名称只按快照匹配。
4. 同一修订同时命中名称和内容只返回一次；返回顺序严格沿候选倒序，不排序、不评分、不补扫第 21 条。
5. 名称不进入 SQL `LIKE`/JSON 查询、索引、游标或缓存；不得新增 N+1、COUNT、OFFSET、写操作或数据库迁移。
6. 查询词仍不 trim、不反射；1..64 码点和 C0/C1 禁止规则完全复用。项目/来源/时间/关键词/损坏错误码、状态码、中文与 `no-store` 不变。

## 3. 前端合同

1. 共用面板标签改为“名称或内容搜索”，活动态改为“当前为名称或内容搜索结果”，失败与空态使用相同联合语义的固定中文；技术标与商务标必须共用同一入口。
2. 输入、校验、显式应用、同值零重发、清除、来源/时间组合、刷新、恢复、删除成功重载、项目切换与迟到隔离全部复用 P12F-F-B/G-B/H 的既有行为。
3. 搜索结果中的 `displayName` 继续只以 React 文本渲染；HTML marker 不得执行。关键词与名称除合法输入值/结果名称外，不进入 URL、localStorage、sessionStorage、Cookie、console、错误或外网请求。
4. 不修改 `editorStateRevisionApi.ts`：请求/响应六键 parser 已满足本包；不得放宽 parser、增加可选键或客户端过滤。

## 4. Failure-first 与反假绿

1. 第一阶段只允许修改两个测试文件：后端新增名称唯一命中、名称+内容并集/去重/顺序、null/非命中、Unicode 规范化、20/21 候选、损坏候选不短路、组合筛选和零写测试；前端新增 P12F-I 标签、技术/商务共用、名称结果文本安全、请求复用与泄漏测试。
2. 两个生产文件在 failure-first 期间必须保持冻结哈希。后端首个有效失败应是名称唯一命中期望 1 而实际 0；前端首个有效失败应是页面已加载但“名称或内容搜索”标签不存在。收集、语法、服务、登录或 serial 跳过不算红测。
3. 禁止 `.or(...)`、宽状态、`>=1`、truthy/defined、条件断言、固定 sleep、skip/fixme/xpass、`force:true`、可选首项、`Math.min`、空数组兜底、只等 arrived 不等 complete、route fallback 假成功或客户端自造过滤。
4. 后端必须用真实 SQL/ASGI/数据库状态证明七列投影、LIMIT 20、先验后搜、三重作用域、返回去重顺序和五域零写；不得只扫描源码字符串冒充运行时语义。

## 5. 四文件白名单与冻结哈希

| 文件 | 初始 SHA-256 | 允许变化 |
|---|---|---|
| `backend/app/services/editor_state_revision_history_service.py` | `3C222AEDC77330F04604994625C51827DBF1B991B38D489A5D6D96B8ECCBBC2C` | 名称联合匹配与事实注释 |
| `backend/tests/test_p12f_revision_content_search.py` | `84ADD28967D12B34CECBC0FF37D7566C47827C66D107F3C92358A3D08DF34D7A` | P12F-I 后端 failure-first/回归证据 |
| `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx` | `6A13CBCD5645440BFF8FD3FFF49D19E3C4C9E2DFFCB9232D0CC25BBD6C3D8976` | 联合搜索固定文案与事实注释 |
| `frontend/e2e/editor-state-revision-history.spec.ts` | `6E964D355E04CD82DE84DF880A7F43B736B6C11954ACDAA0BDD86CA347048872` | P12F-I failure-first、探针与静态门 |

禁止修改路由、Schema、API 封装、模型、迁移、数据库初始化、删除/命名服务、CSS、共享请求层、workspace hook、其它测试、配置、依赖/锁文件或 Git 历史。

## 6. 串行验收门

Grok 与 Codex 均逐条串行；pytest 禁止 xdist/并发分组，Playwright 必须显式单 worker、零重试：

1. `python -m pytest -q tests/test_p12f_revision_content_search.py`
2. `python -m pytest -q tests/test_p12f_revision_name.py tests/test_p12c_revision_history_read.py tests/test_p12f_revision_cursor_page.py tests/test_p12f_revision_source_filter.py tests/test_p12f_revision_time_range_filter.py tests/test_p12f_revision_delete.py`
3. 后端全量 `python -m pytest -q`
4. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-I" --workers=1 --retries=0`
5. `npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0`
6. `npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0`
7. `npx --no-install playwright test e2e/technical-editor-state-truth.spec.ts --workers=1 --retries=0`
8. `npx --no-install playwright test e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0`
9. `npm run lint`、`npm run build`
10. Codex 额外串行前端全量 `npx --no-install playwright test --workers=1 --retries=0`

最后检查 `py_compile`、`git diff --check`、精确四文件、空暂存区、最终哈希、SQL/AST/弱断言/泄漏禁区和干净测试产物。

Codex 独立结果：后端专项/兼容/全量 **29/247/1146 passed**；前端 P12F-I/history/checkpoint/技术 truth/商务 truth/全量 **3/55/51/28/18/318 passed**；lint/build/py_compile/diff-check/精确四文件/空暂存区/哈希与静态门通过。Grok review_request=`msg_82cd1e26df03413389a92604830cdb9c`，Grok 未暂存、提交或推送；Codex 中文实现提交并推送 `008e443`。

最终 SHA-256：history service=`2A45E20DBD22E3894456B8930EA420BA2884D31546A783BCB595E439D157DFFD`；后端专项=`F6D0FC753CD40090CB6182CFF6E356F474F30DACF2A6377A4E68C9DE7C09C839`；共用面板=`636CAD938B088C4F24C5C713F0DC2988504C92C2CCB6283F7A4829A66F5BAAD8`；history E2E=`280E84D7049AECA9EA68B85327EC75CE7EAA13E76066BF1D9E366902E8D7BB3A`。

## 7. 明确未做

不做固定/置顶/收藏、裁剪保护、名称编辑变化、批量命名、标签/备注、搜索片段/高亮/评分/游标/缓存、自动搜索、跨项目搜索/历史、检查点命名、导出/分享、多人协作、SSE/WebSocket、数据库索引/迁移或通用 metadata 搜索。
