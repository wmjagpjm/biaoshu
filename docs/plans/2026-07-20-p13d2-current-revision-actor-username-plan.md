# P13-D2 当前已载入版本操作者用户名展示实施计划

> 契约：`docs/p13d2-current-revision-actor-username-contract.md`
> 协作：Grok 负责受限实现与自测；Codex 负责规划、范围冻结、审查、独立验收、中文文档闭环和 Git
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 测试：pytest 串行；Playwright 固定 `--workers=1 --retries=0`；禁止双方机械重复全量
> 完成：冻结=`4b95ab5`，实现=`44c9196`；已独立验收并推送协作分支

## 1. 基线与约束

- 开工基线：P13-D1 实现=`a8982e3`、文档闭环=`d89b006`，本地 HEAD 与远端一致且工作区干净。
- 本计划以既有路线图和 P13-B/C 状态机为已确认产品方向；不另建 worktree，不偏离用户指定协作分支。
- 先 failure-first，再生产实现；Grok 不得暂存、提交、推送或修改文档。
- 初始严格九个生产文件、三个测试文件；任何扩围必须先停下并向 Codex 提交失败证据、必要性与最小文件名。

九个生产文件开工 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `backend/app/api/schemas.py` | `884E0FA997F8CF757C5F1895C9E80FAFDF846127195BD218BAD537CB846231FF` |
| `backend/app/api/projects.py` | `EB17EAC50F66DBA91F912E09C8E314CD497BB1603643D2AB28DF7B7CE062DFC9` |
| `backend/app/services/editor_state_revision_service.py` | `D78571129DAA18C9D2867CB1A45B409C892922B9DD57EA9648D07F3D664F3678` |
| `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts` | `8B7381D444DB989A3C43A4DD829CE3F86047FFB5EF7763C7C48027A9B07EBC5C` |
| `frontend/src/features/editor-state-collaboration/EditorStateVersionFreshness.tsx` | `CD36EEB4E9CF4F2FB6DC2C6A3025D9607093A44D228CF19665CD60564E03A949` |
| `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts` | `A216D04103C32C4BA09E3FFEC59C3A76E95AD4A0B48DEE60BF44A526CF6CDD1F` |
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `BD4FF0D305C192E4021435CD967A3B0D8148FB9B25A688A9BE3D891DFF92AD3B` |
| `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts` | `A7142A57AC8384C5273DFA332454FB022572C22A9F4511A079DEB036E5F7FCD3` |
| `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx` | `5892A7A7C059EC057AAF064740CE19565E72732EF60ADA9BBB1CA572068636FD` |

## 2. 任务一：后端 failure-first 专项

**文件**：新增 `backend/tests/test_p13d2_current_revision_actor_username.py`

1. 建立 required 模式的用户、同工作区活动成员、项目与 P13-D1 修订真实夹具。
2. 断言 GET 与真实 browser PUT 的 200 响应必含 `currentRevisionActorUsername`，且值为修订 actor 的用户名；客户端同名字段和 actor ID 投稿无效。
3. 参数化 actor null、用户缺失/停用、成员缺失/停用、仅其它工作区成员、用户名空白/超长/控制字符，全部返回 null 且不 500。
4. 建立“旧同版本合法 actor + 最新不同版本/坏 actor”组合，证明只查最新、不回扫。
5. 证明活动成员角色改为 finance/hr/bidder 仍显示用户名；直接更新当前用户名后返回新名称，且没有历史快照承诺。
6. 建立合法 actor + 非法来源、非法 actor + 合法来源两组，证明两个公开字段独立校验。
7. 捕获 SQL：精确一条最新元数据查询、`LIMIT 1`、同 workspace 成员联接；禁止 snapshot、password、hash、salt、session/audit 与 actor ID 出现在响应。
8. 监听 Session 行为，证明解析路径零 add/delete/flush/commit/rollback/refresh，GET 五域零写。
9. 在九个生产文件哈希不变时串行运行专项，记录真实 red 数和首个业务失败。

## 3. 任务二：后端单查询元数据解析

**文件**：

- `backend/app/services/editor_state_revision_service.py`
- `backend/app/api/schemas.py`
- `backend/app/api/projects.py`

步骤：

1. 在 revision service 增加不可变当前修订元数据返回结构，字段只含 `source_kind` 与 `actor_username`。
2. 用一次 `SELECT` 投影最新修订版本/来源、用户当前名、用户启用位、同 workspace 成员启用位；两个布尔位用严格原始投影，禁止 truthy 宽判。
3. 实现用户名安全文本 helper：原样 1..100 Unicode 码点、无首尾空白、无 C0/C1/DEL、行分隔和双向控制；非法只返回 null。
4. 版本/来源/用户名分别校验；版本不匹配时两项均 null，来源或用户名单项损坏不连带另一项。
5. 保留 `resolve_current_revision_source_kind` 兼容入口，但复用新 resolver，禁止第二次查询或第二套最新排序。
6. 给 `EditorStateOut` 增加必出可空 alias `currentRevisionActorUsername`；更新四字段中文注释。
7. `_editor_out` 只调用一次新 resolver，同时填来源和用户名；GET/PUT 路由及 13 键业务数据不变。
8. 串行运行 P13-D2 后端专项与 P13-C 受影响测试；只有精确 SQL 合同被合法替代时，才在授权文件内机械同步 P13-C 测试。

## 4. 任务三：前端 failure-first 与严格 parser

**文件**：

- `frontend/e2e/editor-state-version-freshness.spec.ts`
- `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`

步骤：

1. 先扩展 mock editor-state 形状与响应注入能力，新增技术/商务 actor 行探针，但不得改变既有 source/time 默认语义。
2. 在生产前真实运行聚焦用例，证明 actor 行/更新/隔离行为失败，记录 red。
3. 导出 `parseRevisionActorUsername`，与后端相同规则原样接受或归一 null；禁止 trim/normalize 后放宽。
4. 测试至少包含中文名、缺失/null/非字符串、前后空白、101 码点、C0/C1/DEL、U+2028/U+2029 和双向控制字符。

## 5. 任务四：技术标/商务标同门接入

**文件**：

- `frontend/src/features/editor-state-collaboration/EditorStateVersionFreshness.tsx`
- `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

步骤：

1. 两份 `EditorStateApi` 增加未知输入字段；新增 `currentRevisionActorUsername` state 和唯一 parser 接受 helper。
2. 在所有现有 `acceptCurrentRevisionSourceKind` 同门接受点，同步接受 actor；不得在 POST/SSE/task callback 中新增旁路。
3. 项目切换/会话清理时与时间、来源同拍清空；409/请求失败继续保值，非法版本不得先更新元数据。
4. Hook 返回 actor；页面只传共享组件，不创建页面副作用或第二份 formatter。
5. 共享组件增加 `actorUsername`、`actorTestId`，固定显示“当前版本操作者”；用户名只作文本节点。
6. 更新所有改动生产文件顶部四字段中文注释，使 P13-D2 行为和禁止项与代码一致。

## 6. 任务五：专项绿测与直接回归

Grok 默认串行执行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13d2_current_revision_actor_username.py --tb=short
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13c_current_revision_source.py tests\test_p12c_browser_put_revisions.py --tb=short
.\.venv\Scripts\python.exe -m py_compile app\api\schemas.py app\api\projects.py app\services\editor_state_revision_service.py tests\test_p13d2_current_revision_actor_username.py

cd ..\frontend
npx playwright test e2e\editor-state-version-freshness.spec.ts --workers=1 --retries=0
npm run lint
npm run build

cd ..
git diff --check
git status --short
```

若 P12C 定点明显过宽，可先用失败节点精确重跑，但最终 review_request 必须如实列出实际命令、passed/failed/did-not-run 和耗时；不得声称未运行的套件通过。

## 7. Codex 独立审查与验收

1. 对照冻结基线核验九个生产文件，无模型/迁移/身份 API/history API/配置/依赖扩围。
2. 审查 SQL 是否一次查询、同 workspace 联接、`LIMIT 1`、无 snapshot/敏感列；审查用户名安全文本和 source/actor 独立降级。
3. 审查两 Hook 每个接受/清理点；用差异与 E2E 证明 A→B 迟到 success/catch/finally、409、非法版本和外部写唯一 GET 没有新旁路。
4. 重点反假绿：禁止只测字段存在、函数签名、源码字符串、mock 假 worker、恒真泄漏断言或未 arrived 的并发场景。
5. 独立至少运行 P13-D2 后端专项、P13-B/C 聚焦 E2E、lint、py_compile、diff-check；根据 SQL/响应回归信号选择 P13-C/P12C 定点，不默认后端全量或整仓 318 E2E。
6. 审查不通过时只给 Grok 下发精确返修白名单；生产已正确而证据不足时优先 test-only 返修。

## 8. 提交与文档闭环

1. 冻结提交只含本契约、本计划、路线图、主交接和联调清单；中文提交并推送协作分支。
2. Grok review_request 后，Codex 精确暂存审查通过的实现文件，以中文功能提交并推送。
3. 最后把真实 failure-first、Grok 绿测、Codex 独立验收、消息 ID、最终文件/哈希、未运行套件和遗留风险写回契约/计划/路线图/交接/联调清单，单独中文闭环提交并推送。
4. 每次提交前核对分支、`git diff --check`、暂存白名单；完成后核对工作区干净且本地 HEAD 与远端一致。

## 9. 执行结果

1. 严格 9 个生产文件完成，测试范围由初始 3 个扩为 4 个：新增 P13-D2、同步 P13-C、扩展 freshness，并经 Codex 明确授权机械同步 P13-D1 公开键守卫；未改模型、迁移、身份/成员 API、历史 API、配置或依赖。
2. 后端真实 failure-first **26 failed / 0 passed**；前端没有合规 E2E-only red，已明确记录而未补造。Grok 首轮实现后经 Codex 审查，仅对 3 个测试文件和 service docstring 做受限返修。
3. Grok 最终后端/前端为 **44/17 passed**；Codex 独立后端核心/受影响回归/前端为 **44/15/17 passed**，并定点通过外部写唯一 GET 路径 **1+2+1 passed**。
4. lint、py_compile、diff-check、精确文件白名单、冻结哈希、递归 actor ID/敏感字段泄漏门、空暂存区均通过；Grok 初轮 build 通过，Codex 未机械重复。
5. 未运行后端全量、完整受影响 E2E 套件或整仓 318 E2E。实现由 Codex 以 `功能：完成P13D2当前版本操作者展示` 提交为 `44c9196` 并推送；详细消息、哈希和风险见契约第 10 节。
