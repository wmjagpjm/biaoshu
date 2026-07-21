<!--
模块：V1-G 任务成功后编辑态刷新围栏实施计划
用途：按冻结、failure-first、生产实现、独立验收和文档闭环拆分 V1-G。
对接：V1-G 契约、Grok A/B 消息箱、独立 worktree、前端串行 E2E。
二次开发：严格五文件；Grok 不做 Git 写入；疑似问题双确认后才返修。
-->

# V1-G 任务成功后编辑态刷新围栏实施计划

> **执行代理要求：** 必须使用 `executing-plans`，逐项执行并在每个审查点核对真实证据。
> **完成状态：** 冻结=`b9cacd1`，实现=`fb3b58f`；Codex 独立验收和推送已完成。

**目标：** A 项目任务迟到 success 不再触发旧编辑态重载、粘住 B 的 loading 或污染 B 的提示/项目步进，同时保持当前项目成功后精确一次自动水合。

**架构：** 页面以 `startedProjectId + taskGeneration` 管理任务完成后的业务副作用；两个 editor-state Hook 在任何 loading/epoch/timer/GET 副作用前拒绝失效闭包。`useProjectPipeline`、I4/SSE 和后端任务协议保持不变。

**技术栈：** React、TypeScript、现有 editor-state Hooks、Playwright route/per-task SSE、Chromium 单 worker。

---

### 任务 1：冻结契约与执行基线

**文件：**

- 新建：`docs/v1g-writer-task-success-refresh-fence-contract.md`
- 新建：`docs/plans/2026-07-22-v1g-writer-task-success-refresh-fence-plan.md`

**步骤：**

1. 核对主仓分支、HEAD、上游与工作区；必须为 `collab/grok-code-codex-review@a9ff414c` 且干净。
2. 记录 Grok A/B 只读确认消息及 Codex 裁定：纳入 parse，拒绝修改 pipeline，采用页面 generation + Hook 入口早退。
3. 运行 `git diff --check`，只暂存上述两个文档。
4. 中文提交 `文档：冻结V1G任务成功刷新围栏`，只推送协作分支。
5. 核对本地、上游和 GitHub 实际分支一致。

### 任务 2：创建独立实现 worktree

**路径：**

- worktree：`C:\Users\Administrator\biaoshu-v1g-writer-refresh-impl`
- 分支：`collab/v1g-writer-refresh-impl`
- E2E 数据库：该 worktree 的 `backend/data/biaoshu-e2e.db`
- 串行端口：8010/5174

**步骤：**

1. 从任务 1 的冻结提交新建分支和 worktree，不复用 V1-F worktree。
2. 核对 worktree 分支/HEAD/工作区，确认数据库物理路径落在该 worktree。
3. 检查 8010/5174 无监听；有监听先查归属，不得抢占或结束用户进程。
4. 向 Grok B 下发 test-only task；Grok A 只读等待，禁止两者并发跑 Playwright。

### 任务 3：Grok B 落 failure-first

**文件：**

- 新建：`frontend/e2e/v1g-writer-task-soft-switch-hydration.spec.ts`

**步骤：**

1. 先实现契约 §6 的 HoldGate/per-task SSE 受控夹具，不改生产文件。
2. 逐项实现技术 parse/analyze/outline/chapters/chapter 和商务 biz_qualify 的 A→B 用例。
3. 增加技术/商务同项目 success 精确一次 GET 的两个对照用例。
4. 运行单一专项：

   ```powershell
   npx playwright test e2e/v1g-writer-task-soft-switch-hydration.spec.ts --workers=1 --retries=0
   ```

5. 预期未修生产时约 `6 failed / 2 passed`；必须报告实际 total/failed/passed/did-not-run、首红、网络计数与测试 SHA-256。
6. 报告 `git diff --check`、精确单测试文件、空暂存区和端口清理；发送 `review_request`，不得 Git 写入。

### 任务 4：Codex 独立审查红测

**步骤：**

1. 核对测试只写一个新文件，无 package/config/生产变化。
2. 确认每个软切用例先观察 pending/running 与 B 就绪，再释放 A success；禁止固定 sleep。
3. 确认五个技术入口真实独立点击，chapter/chapters 的 type/payload 不互相冒充。
4. 确认旧 success 后 A/B GET 增量精确为 0、B loading/正文/tip/step 均有断言；同项目 GET 精确 +1。
5. 独立运行专项确认红点来自现有生产缺口。
6. 发现疑似夹具或覆盖问题时先向 Grok B 发 question；收到明确 YES 后才发 test-only task。
7. 冻结测试哈希，向 Grok A 下发 production-only task。

### 任务 5：Grok A 最小生产实现

**文件：**

- 修改：`frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- 修改：`frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
- 修改：`frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- 修改：`frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
- 只读：`frontend/e2e/v1g-writer-task-soft-switch-hydration.spec.ts`

**步骤：**

1. 技术页新增项目切换/任务启动都会推进的 refresh generation，并建立统一 task-success-reload helper。
2. 把 parse/analyze/outline/chapters/chapter 接到该 helper；每个 await 后检查项目和 generation，只有重载成功且仍当前时写 tip。
3. 商务 `runBizTask` 捕获 startedProjectId/generation；success 后 refresh、项目 PATCH/GET/setProject 的每个异步边界均复核所有权。
4. 两个 Hook 在 timer/epoch/loading/GET 前增加当前项目/session 入口早退；不改 silent、P11 失败卡和初始 GET。
5. 禁止修改 `useProjectPipeline`、I4/SSE、后端、导出、配置、测试或依赖。
6. 先运行新专项至全绿，再串行运行技术/商务 truth；发送包含四生产文件哈希和真实结果的 `review_request`，不得 Git 写入。

### 任务 6：Codex 独立验收

**步骤：**

1. 核对严格五文件、测试哈希未变、暂存区为空。
2. 静态追踪 A→B、A→B→A、同项目异常双飞和每个 await 后的副作用门。
3. 串行运行契约 §7 的新专项、技术 truth、商务 truth、I4、I3、H3。
4. 串行运行 `npm run lint`、`npm run build` 和 `git diff --check`。
5. 核对无 editor-state 多余 GET、无旧 tip/project/step、P11 GET 失败仍固定失败卡且任务 success 不反转。
6. 确认 8010/5174 无监听、E2E 只使用 worktree 相对数据库，未触及真实数据。
7. 发现生产问题时严格走 `Codex question → Grok 只读确认 → Codex task → Grok review_request`。

### 任务 7：提交、推送与文档闭环

**步骤：**

1. Codex 只暂存严格五文件，中文提交 `修复：隔离迟到任务成功后的编辑态刷新`。
2. 将实现提交快进到 `collab/grok-code-codex-review`，只推送该分支。
3. 更新 V1-G 契约/计划、`HANDOFF-next.md`、路线图和联调清单，记录真实红绿数字、消息链、五文件哈希、未运行项与下一主线。
4. 中文提交 `文档：闭环V1G任务成功刷新围栏` 并推送。
5. 核对主仓与实现 worktree 干净，本地/上游/GitHub HEAD 一致。

### 任务 8：继续 V1 主线

V1-G 完成后重新只读审计下一项本机/内网实际可用断点，优先多章正文内容质量与最终标书可交付性；不得提前混入 V2/V3。

## 执行结果

1. Grok B 首轮单文件 failure-first 为 **6 failed / 2 passed**；Codex 审查发现 ABA 代次未锁且 task POST 计数过宽，经 `msg_55bcb5327e8d45ba8e588449850fd7ad`/`msg_c0fe6fb76a4e44e5b955f2f1d528d21c` 双确认后加固为 **7 failed / 2 passed**。
2. Grok A 严格四生产实现并保持测试哈希，review=`msg_36a13fd6fcc046c3b1267f016aa1a829`；未暂存、提交或推送。
3. Codex 独立串行通过新专项/技术 truth/商务 truth/I4/I3/H3 **9/28/18/8/5/15 passed**，lint、build、diff-check、严格五文件、空暂存和端口门通过。
4. 实现提交=`fb3b58f`，已快进并推送 `collab/grok-code-codex-review`；未运行后端 pytest、整仓 318 E2E 或并发测试。
