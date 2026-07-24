<!--
模块：V1-O 知识库文档服务端真值实施计划
用途：把契约冻结、failure-first 红测、Codex 审查、生产实现与最终验收拆为可验证任务。
对接：docs/v1o-knowledge-doc-server-truth-contract.md、Grok A/B 本地信箱、Playwright 8010/5174。
二次开发：严格四文件 test-only；生产仅 hook+page 另授权；禁止恢复 local 成功态。
-->

# V1-O 知识库文档服务端真值实施计划

> **执行代理：** Grok A 本 worktree 仅契约/计划 + failure-first test-only；生产未授权。
> **分支：** `collab/v1o-production` @ `47ca7cb`（书面授权 worktree `biaoshu-v1o-prod`）；生产两文件未暂存变更必须原样保留。
> **R1：** 关闭 Codex Q1–Q12（`msg_c089ca8bc9ee4d5d98969944fd31c15b`）；**R1 重复/中断 Playwright 收集作废，不得恢复续跑**。
> **R2：** 关闭 Q1–Q9（`msg_3a048f3b68f14fd5b395f192dc4f8dcf`）；Grok **禁止** Playwright/浏览器/Vite/uvicorn/pytest/8010/5174；仅静态 TS parse + 只读 git/hash/端口。
> **R5-FINAL：** 关闭 Q1–Q10（`msg_4ad902ba574145568ea219dd665e5ee2`）；Q17 page.evaluate synthetic 冲突明确 NO；semantic-index 优先保哈希；Grok 仅静态 parse + 只读 git。
> **R6-FIX：** 七项确认后最终集中 test-only（`msg_0d71b9b59aa9438da2467eb9cd8e37f1`；Q `msg_c1bfb70021da43fdad97783b018b07a8` + YES×7 `msg_5b5635c73e5f49539fe41c3db278d57a`）；Grok 仅静态 parse + 只读 git；禁止 Playwright。
> **Codex failure-first：** 2026-07-24 单 worker 收集 144 项，`134 failed / 10 passed`，耗时 29.9 分钟；其中 1 项为两处宽 OR 触发自守卫，双方确认后以 R8 test-only 修复，聚焦自守卫 `1 passed / 7.2s`。其余完整 failure-first 不重复运行，生产仍未授权。
> **R9-TEST（test-first，写后对账 owner 代次）：** 前置确认 `msg_c7717cc4cb654d49828ffdda824ba8ab` Q1/Q2/Q3 全 YES；任务 `msg_9af257b363bd4abb93c41c62f726d205`。只补红门：T1 unmount 后旧 create finally 零新增双 GET/semantic/写/DOM；T2 主 refresh 新代次 ready 后旧 create 不得覆写列表/错误/选择、不得绑旧 opError。Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/两文件哈希；**Playwright did-not-run**（Codex 保留串行 failure-first 槽）；禁止改 production/契约/依赖/Git 提交。
> **R9-FIX（修正红门自身阻断并保留 T1）：** 前置 Codex `msg_9e9686cab64949a2a0401c1810f55d4d`；Grok Q1–Q3 全 YES `msg_7c29aa9eca3b4f198c0623be5604585e`；本任务 `msg_814282381ae44d96a13b0b450822144a`。
> - **Codex 聚焦实证（5 个含 unmount 用例，未完整重跑）：** T1 在业务门 `folderGetArrived - baseArrivedF` 处 **expected 0 / received 1**（生产 finally 仍对账的真实红，保留）；另三例精确因 `armBrowserRouteTerminal` 未选 waiter（Promise.race/双侧 waitForEvent 遗留 loser）在 test end 超时拒绝（红门自阻断，非业务）。
> - **最小修复：** ① `prefer=response` 只装 response waiter；`prefer=requestfailed` 只装 requestfailed waiter；`either` 用 `page.on` + 单 Promise + 单 timeout + 双侧 `off` cleanup（命中/超时/close 均收口），禁止 loser 遗留；② T1 `staleDocs[].folderId` 精确指向 `fld_owner_unmount_stale` 合法图；③ **完整删除**不可达 T2（真实 UI 在 create hold/busy 时刷新按钮禁用，禁止 force/dispatch 替代；程序化 refresh 竞态后续 hook 层另验）；④ 不改 Page/hook，不降 T1 精确差值/隐私/settle 门。
> - Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/两 production 哈希；**Playwright did-not-run / 无完整重跑**；禁止 Git 提交。
> **R9-Q3-FIX（修正 F-unmount 真值，保留 T1 真红）：** 前置 Codex `msg_0c92b55a030a4103942ad73f87f9f01f`；Grok Q1/Q2 全 YES `msg_afc2184c663b470bad54008b12d5cbeb`；本任务 `msg_c6255904c0e54e66a5c4d648c347dfcf`。
> - **Codex 聚焦实证（含 unmount 用例，未完整重跑）：** **3 passed / 2 failed**。假红：① F-unmount 硬编 `fulfilled=baseSettled+1`，hold 下 `pending=baseArrived-baseSettled` 在 StrictMode/并发双飞时可为 2 → expected 1 / received 2（测试真值错，非业务渲染红）；② F-unmount stale `makeDoc` 未设 `folderId`，默认 `FLD_INBOX` 与 `fld_um_stale` 不同图。真红保留：T1 `folderGetArrived-baseArrivedF` **expected 0 / received 1**（生产 finally 仍对账）。
> - **最小修复：** ① 释放前冻结 `pendingF/pendingD` 并断各 `>0`，释放后精确 `fulfilled=baseSettled+pending`，继续严格断 arrived 不增长及业务 API/写/semantic/DOM 不变，禁止宽 `>=` 代替终值；② stale `doc.folderId` 精确 `"fld_um_stale"`；③ 不改 production hook/page，不降 T1 精确门。
> - Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/两文件哈希；**Playwright did-not-run**；禁止 Git 提交。
> **Q10-TEST-FIX（最终 test-first 集中返修）：** 前置双方确认 Q1–Q4 YES `msg_f1551bd6111d446686c724959f957e20`/`msg_39fc38d822b34630b3169f4f4ce4dc81`；Q3a/Q3b/Q5 YES `msg_a6688493f79642a8a55d6a8be5e3ce65`/`msg_226bf0eab0824e6091c9b1500f2fca38`；Q5 增补/Q6 YES `msg_dfd69e9fb45b4b55890f77a7803e3900`/`msg_2f7dac2ba07349f4b9c0b4748d6722c5`；本任务 `msg_909dab0172744ceb88872b24d51986a1`。工作树 `C:\Users\Administrator\biaoshu-v1o-prod` @ `collab/v1o-production` HEAD `bff8b26`；**production 两文件未授权、须逐字节保留**。
> - **一次关闭 test-only：** ① 19 定位/顺序假红（三处单字符 badSentinel→唯一长 canary；nullable 状态限定 table/row；move 对账 folder 限定 tree button；delete 限定 `.kb-batch-bar`；选择清理先勾选再断 moveTarget）；② T1 写前 continuation + `arrived==fulfilled` 且各>0 冻结基线，AppShell SPA 侧栏 Link 离开并确认组件卸载后再释放旧写，response/requestfailed + knowledge/semantic/DOM/写精确零增量；③ semantic 红门（GET A/B 逆序、同 tick 双 rebuild=1 POST、旧 GET 不覆盖 building、building 后 503 保 building+继续轮询+固定错误）；④ finishedAt 非法 marker →「—」+ 公开表面零 marker；⑤ 同步契约与本计划，**不宣称 production 已完成**。
> - Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/四文件哈希；**Playwright / 浏览器 / Vite / uvicorn / pytest / 端口 / 真实数据 / 网络 did-not-run**；禁止 Git 写操作。
> **Q11-TEST-FIX（四项时序与隐私门返修）：** 前置 Codex question `msg_8d96ac331bd24203b769f0ca22c6aaea`；Grok A YES `msg_f7719cb11b144f5281af5f534b43d0ee`；本任务 `msg_20322d1b0a7c43ceb28d2e062246f8d1`。工作树 `biaoshu-v1o-prod` @ HEAD `bff8b26`；**production 未授权**。
> - **一次关闭：** Q1 building 后 hold 同代两 poll GET（禁 StrictMode）；Q2 fulfill B→DOM 证 B→释 A→仍 B；Q3 hold rebuild 前已到达 semantic GET（禁 POST 后 poll 冒充）；Q4 finishedAt 复用 knowledge 主 spec `preparePage`/`assertPrivacyClean` 全公开面，semantic 保留精确「—」，503 仅 panel/body。
> - **保留不弱化：** 19 locator、T1 settled+SPA unmount、双 rebuild 单飞、building 后 503 继续轮询。
> - Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/四文件哈希；**Playwright did-not-run**；禁止 Git 写。
> **Q12-TEST-FIX（后续 poll 掩盖门返修）：** 前置 Codex question `msg_350dea0570364f6582fa61fc579821d6`；Grok A Q1/Q2 YES `msg_338f4d763c584e368ea197c303818f0e`；初轮 `msg_3109bca3d57f40c1b057c927b1a89d61`；**最终返修授权** `msg_d9ed563e706c4ec8ba3da22872325eac`。工作树 `biaoshu-v1o-prod` @ HEAD `bff8b26`；**production 未授权**；**冻结** knowledge 主 spec（Q4 权威探针）与 production 两文件。
> - **一次关闭：** ① 同代 A/B：B DOM 9/9 commit 后 hold 全部后续 poll（含第 3+）；释 A 前 arm 精确 terminal；等 A terminal+业务 continuation；**`page.evaluate` 一次性** status/counts/degrade 仍 B（ready、9/9、非 not_built/building）；finally 释放/abort 全部 held。② rebuild 旧 GET：POST 后 poll 全 hold；释放 click 前已到达旧 GET 并等各 terminal+continuation；**held post poll>0** 证明未提交窗口；**`page.evaluate` 一次性**仍 building/rebuild disabled；finally 释放全部；删除 `>=0` 恒真。
> - **不得弱化** finishedAt、19、T1、双 rebuild、503、隐私与 Q11 其余节点。可写仅 `semantic-index.spec.ts` + 契约 + 本计划。
> - Grok **仅**一次 TS compiler API parse/transpile + `git diff --check`/`status`/三可写 + 两 production 哈希；**Playwright did-not-run**；禁止 Git 写。

**目标：** 知识库文档/文件夹以服务端 GET/写响应为唯一真值；消灭 local 成功态、旧键污染、假 ID 与敏感透传；P9C 在文档非 ready 时不可构建。

**架构：** 前端文档主状态机 `loading|ready|error`；写仅 ready + 共享单写锁；五类 mutation 结束后原子 folders+docs 双 GET 对账；旧键完全旁路；语义面板仍内存 + 固定模型。后端已具备，本包不改。

**技术栈：** React/TypeScript、`apiFetch`、Playwright Chromium、Vite proxy `/api`、TypeScript compiler API（E2E 自守卫）。

---

### 任务 1：契约与计划冻结（含 R1/R2 schema·隐私·对账补强）

1. 更新 `docs/v1o-knowledge-doc-server-truth-contract.md`：
   - folder/doc 精确运行时 schema；GET 批坏项+合法 sentinel；写响应 3×3 独立接线；
   - 隐私措辞：异常敏感 path/key/id 禁出口；经 schema 的正常服务端资源 ID 仅允许在 reindex/move/delete path/body 且必须使用；
   - 早期 arm、全出口（request/console args drain 至 pending=0/DOM 历史同引用/Cookie 打开前终态全量对账）、IDB 空 baseline 且终态读取全过程不 disarm、扫描分层；
   - mutation 精确门：双 GET 各 +1、独立 handler 防死锁、first×second 共享锁矩阵（first 不可变 op token）、action promise 与 outcome 分离、禁止吞 promise；
   - moved 含小数/-0；真实重复 ids 注入读回 [A,B,A] 后请求去重；选择清理；
   - refresh/unmount：folders+docs settled 严格>基线、业务 API 与导航请求分账、response/requestfailed barrier、释放前 DOM/请求/写/semantic 基线；
   - nullable 缺失与 null 均为合法（含 parentId）；tags/chunks 缺失非法；全 request+allHeaders+context.cookies 全量对账；
   - Codex 单次 Playwright 策略与 R1 重复测试作废说明。
2. 更新本计划；同步验收矩阵与未运行项。
3. 不修改 production；不 Git 提交。

### 任务 2：Grok A failure-first（R2 静态 only）

**唯一可写测试：**

1. `UPDATE frontend/e2e/knowledge-doc-server-truth.spec.ts` — 矩阵 A–H 全门（见契约 §4）；优先重构已有 helper，**禁止**再堆叠第二套探针/AST analyzer；
2. `MODIFY frontend/e2e/semantic-index.spec.ts` — **仅**修正文件头/helper 中 “folders/docs 失败进入 local” 失真注释，以及末个文档失败用例与新契约必要断言；**不得弱化**其它 7 个 P9C 用例，**不得**把该文件整体改造成新 route 框架。保留：双空态、独立参数化、无 serial、partial 仅 move/delete、delete 停止链、分离 canary、semantic 原 8 用例主体。

**强制反假绿（R2）：**

- 精确拦截同源 API；未知 `/api/knowledge*` 与外网 fail-closed（主 spec 权威）；`/api/cards` 显式 `[]`。
- 隐私：同步 capture→preset+baseline→arm；全 request；console `args()` + drain 至 pending=0；DOM MutationObserver 同引用历史；Cookie 打开前/终态全量对账（含 HttpOnly）；IDB 终态读取不 disarm；扫描分层排除未变 baseline；synthetic 自证（禁手工 push 掩盖）。
- schema 穷举共享 GET 矩阵 + 写响应 3×3；parentId 缺失/null 合法；tags/chunks 缺失非法；合法 null 可观测。
- loading/error 写门；mutation 参数化独立 test；共享锁 first×second（不可变 first token）；双 GET 精确 +1 与独立返回。
- moved 全矩阵；真实重复 ids 注入 [A,B,A]；服务端 ID 后续写；选择清理。
- refresh success/HTTP error/abort 分测 + unmount；settled 严格>基线；导航与业务 API 分账。
- 单一 `analyzeSpecSource` + synthetic 正反表；仅 test.describe+固定标题豁免；short-circuit 直接右操作数。

**Grok R2 运行（仅静态）：**

```powershell
# 1) 一次 Node TS compiler API parse 两 spec（不得加载 playwright config / webServer）
# 2) git diff --check
# 3) git status --short 精确四文件
# 4) 四文件 SHA256/bytes
# 5) 8010/5174 零监听；无 node/python 测试服务
```

- 只发一个 `[GROK-A][V1-O-A][R2] review_request`；**不得提交**；**不得**跑 Playwright。

**Codex 审查后单次 Playwright（Grok 不跑）：**

```powershell
cd frontend
.\node_modules\.bin\playwright.cmd test e2e/knowledge-doc-server-truth.spec.ts e2e/semantic-index.spec.ts --workers=1 --retries=0
```

### 任务 3：Codex 审查 test-only

1. 逐条核对 Q1–Q9 / 矩阵 A–I、route 白名单、旧键快照、mutation 对账、代次围栏、自守卫。
2. 静态 PASS 后独立单次 Playwright；禁止为绿改断言或 production；R1 旧结果作废。
3. 疑似测试问题：question → Grok 确认 → 才授权 test-only 返修。
4. 合格后仅提交四文件（或测试+文档拆分策略由 Codex 定），中文提交；记录 blob/SHA。

### 任务 4：Grok 生产实现（另 task，未授权）

**候选白名单：**

1. `frontend/src/features/knowledge-base/hooks/useKnowledgeBase.ts`
2. `frontend/src/features/knowledge-base/pages/KnowledgeBasePage.tsx`

实现要点：

1. 删除 local 成功路径、`loadLocal`/`saveLocal` 业务读写、mock seed 回退、客户端 `fld_`/`kb_`/`Math.random`/假 setTimeout ready。
2. 引入 `loading|ready|error`；双 GET 结构校验（契约 §2.2）；固定错误常量；写门 + 共享单写锁。
3. 五类 mutation 结束后原子双 GET 对账；move 校验 `moved`；delete 中途失败停并 GET；响应仅结构校验不作长期列表真值。
4. **statusMessage：** 不渲染服务端原文，按 status 映射固定安全文案。
5. refresh 代次 + mounted；选择清理（folder/doc/batch/moveTarget）；UI 互斥；去掉“离线本地演示”。
6. 语义重建仅文档 ready；不改 cards/images/types 业务语义/mock 文件本身（可停止 import mock）。
7. **旧键注释必须解释：** 演示种子与历史用户数据不可可信区分，自动迁移有数据/隐私风险，故不读不写不删不迁移不上传。
8. 不修改冻结测试；串行复跑至绿。

### 任务 5：Codex 最终验收与文档闭环

1. 严格两 E2E 全绿；diff-check；端口清理；可选 lint/build 若授权。
2. 更新契约完成记录与交接；推送协作分支；**不操作 main**。
3. 未运行项明确列出（整仓 E2E、真实 API 鉴权、真实 uploads、production 实现前等）。

### 验收矩阵同步（R2）

| 门 | 责任文件 | 说明 |
|---|---|---|
| schema / 空态 / 旧键 / 隐私 / mutation / refresh / AST | `knowledge-doc-server-truth.spec.ts` | 权威 |
| P9C 原 8 用例 + 文档失败末例 | `semantic-index.spec.ts` | 不扩 route；不弱化 |
| 契约语义 | `v1o-knowledge-doc-server-truth-contract.md` | 冻结 |
| 任务拆分 | 本计划 | 冻结 |

### 风险与残余

- Windows CRLF 导致字节 SHA 与 blob 不一致：跨 worktree 以 Git blob / `git diff` 为准。
- 旧 E2E 若仍依赖 local 演示文档可见性，仅允许在已授权文件内收紧，不得扩散改其它 spec。
- 生产若误删语义面板行为，P9C 回归会红——实现时保持面板 testid 与固定模型展示。
- 参数化独立 case 增加测试数属于有效覆盖，不算无意义膨胀；failure-first 首红不中止后续独立 case 的收集（workers=1 仍会跑完文件内各 test）。
- R2 Grok 仅静态；业务红/绿以 Codex 单次 Playwright 为准。
- R6-FIX 七项关闭点：① DOM synthetic 查 detail+oldValue/跨 task；② refresh/unmount 绑 response|requestfailed + 本轮 settled 精确 +1 + 业务 continuation；③ 写 phase 分账 + multi-delete 冻结剩余 + diagonal synthetic；④ parentId 根级 DOM、statusMessage/sizeLabel 字段精确空；⑤ [A,B,A] 仅同步派发 + 精确 hook poll；⑥ 五类写 GET 逐字段 + reindex chunks=88 排 99；⑦ moveTarget="" + 全部文档 is-active + selectedIds 精确保留/清除。
- **R9-TEST 状态（2026-07-24）：** Grok-A 已在 `knowledge-doc-server-truth.spec.ts` 追加两门「写后对账 owner 代次」failure-first（T1 unmount finally 零副作用；T2 主 refresh 新代次不接受旧写对账/opError）。计划已同步。**did-not-run：** Playwright / Vite / uvicorn / 端口监听 / 整仓 E2E；Grok 仅静态 TS parse/transpile + git 两文件检查。Codex 持有串行 failure-first 测试槽，未授权前不得声称业务红/绿。
- **R9-FIX 状态（2026-07-24）：** Codex 聚焦 5 含 unmount 用例：T1 业务门 `folderGetArrived-baseArrivedF` expected 0 / received 1；另三例因 loser waiter test-end 拒绝。Grok 已修 `armBrowserRouteTerminal` 分流/cleanup、T1 合法图、**完整删除 T2**（UI busy 不可达；不保留 force/dispatch；hook 层 refresh 竞态另验）。**明确没有完整重跑**；Grok 仅静态 parse + git 检查；生产两文件字节保持。T1 精确门保留作 failure-first 真红。
- **R9-Q3-FIX 状态（2026-07-24）：** Codex 聚焦实证 **3 passed / 2 failed**。假红：F-unmount 硬编 settled `+1`（pending 可为 2）+ stale doc 默认 `folderId=FLD_INBOX` 非法图。Grok 已改为释放前 `pendingF/pendingD` 冻结并 `>0`、释放后精确 `fulfilled=baseSettled+pending`、stale `folderId:"fld_um_stale"`。**T1 expected 0 / received 1 仍是真业务红**（生产 finally 对账）。**Playwright did-not-run**；Grok 仅静态 parse + git 检查；hook/page 字节保持。
- **Q10-TEST-FIX 状态（2026-07-24）：** Grok-A 已在四可写文件落地 test-first 集中返修（定位假红 19、T1 真值基线/SPA Link unmount、semantic 四红门、finishedAt 隐私红门、契约/计划节点）。**未改 production**；**Playwright did-not-run**；静态 parse/transpile + git check + 四文件哈希见 review_request。后续 production 另授权：Page category 列、hook request seq + rebuild ref lock、building 失败保 last-known + 继续轮询、formatFinishedAt 非法仅「—」。
- **Q11-TEST-FIX 状态（2026-07-24）：** Grok-A 已按 Codex Q1–Q4 最小 test-only 返修：semantic 同代 poll A/B（先 building 再 hold）、B 先 DOM 提交再释 A、rebuild 前旧 GET hold、finishedAt 全公开面迁入 knowledge 主 spec `preparePage`/`assertPrivacyClean`、semantic 保留精确「—」、503 诚实 panel/body。**未改 production**；**Playwright did-not-run**；静态见 review_request。
- **Q12-TEST-FIX 状态（2026-07-24）：** Grok-A 关闭后续 poll 掩盖门。最终授权 `msg_d9ed563e706c4ec8ba3da22872325eac`：同代 A/B 全 hold（含第 3+）+A terminal/continuation + **`page.evaluate`** status/counts/degrade 仍 B；rebuild 旧 GET 全 hold post poll + 各 terminal + **held post poll>0** + **`page.evaluate`** 仍 building/disabled；删 >=0 恒真。可写三文件；主 spec 与 production 冻结。**Playwright did-not-run**；静态见 review_request。
