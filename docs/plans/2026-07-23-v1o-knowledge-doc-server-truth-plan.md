<!--
模块：V1-O 知识库文档服务端真值实施计划
用途：把契约冻结、failure-first 红测、Codex 审查、生产实现与最终验收拆为可验证任务。
对接：docs/v1o-knowledge-doc-server-truth-contract.md、Grok A/B 本地信箱、Playwright 8010/5174。
二次开发：严格四文件 test-only；生产仅 hook+page 另授权；禁止恢复 local 成功态。
-->

# V1-O 知识库文档服务端真值实施计划

> **执行代理：** Grok A 本 worktree 仅契约/计划 + failure-first test-only；生产未授权。
> **分支：** `collab/v1m-m3-a` @ `eb64dc1a2fcd2ffa8bee85668f0b99a9ff6c4ffe`。
> **R1：** 关闭 Codex Q1–Q12（`msg_c089ca8bc9ee4d5d98969944fd31c15b`）；**R1 重复/中断 Playwright 收集作废，不得恢复续跑**。
> **R2：** 关闭 Q1–Q9（`msg_3a048f3b68f14fd5b395f192dc4f8dcf`）；Grok **禁止** Playwright/浏览器/Vite/uvicorn/pytest/8010/5174；仅静态 TS parse + 只读 git/hash/端口。
> **R5-FINAL：** 关闭 Q1–Q10（`msg_4ad902ba574145568ea219dd665e5ee2`）；Q17 page.evaluate synthetic 冲突明确 NO；semantic-index 优先保哈希；Grok 仅静态 parse + 只读 git。
> **R6-FIX：** 七项确认后最终集中 test-only（`msg_0d71b9b59aa9438da2467eb9cd8e37f1`；Q `msg_c1bfb70021da43fdad97783b018b07a8` + YES×7 `msg_5b5635c73e5f49539fe41c3db278d57a`）；Grok 仅静态 parse + 只读 git；禁止 Playwright。
> **Codex failure-first：** 2026-07-24 单 worker 收集 144 项，`134 failed / 10 passed`，耗时 29.9 分钟；其中 1 项为两处宽 OR 触发自守卫，双方确认后以 R8 test-only 修复，聚焦自守卫 `1 passed / 7.2s`。其余完整 failure-first 不重复运行，生产仍未授权。

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
