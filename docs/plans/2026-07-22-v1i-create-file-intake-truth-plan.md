<!--
模块：V1-I 创建页招标文件摄入真值实施计划
用途：把独立测试 worktree、failure-first、生产 worktree、双确认返修和最终验收拆为可验证任务。
对接：V1-I 契约、Grok A/B 本地消息路由、Playwright 8010/5174 与 P11A 真值回归。
二次开发：严格五文件；B 只写测试，A 只写生产；Codex 负责测试转移、审查、提交和推送。
-->

# V1-I 创建页招标文件摄入真值实施计划

> **执行代理要求：** 必须使用 `executing-plans`，逐项执行；用户已授权持续推进，但任何扩围或疑似问题仍须双确认。
> **完成状态：** 2026-07-22 已完成并推送；冻结=`c1cde54`，测试=`35b0f6b`/`b8c9776`，生产=`4e7a3c3`。

**目标：** 技术类创建入口使用真实 File，项目创建成功后完成真实 multipart 上传再导航；失败路径不伪造文件、不重复创建项目，技术工作区只展示服务端文件。

**架构：** CreatePage 持有内存 File/上传状态；projectStore 提供项目创建和单文件上传两个薄门面；工作区删除 pending 回退。Grok B 在独立测试 worktree 先锁行为，Codex 审查并提交冻结测试，再转入 Grok A 独立实现 worktree。

**技术栈：** React/TypeScript、既有 `apiUploadFile`、Playwright Chromium、Vite。

---

### 任务 1：冻结文档

1. 只提交 V1-I 契约和本计划，中文提交并推送 `collab/grok-code-codex-review`。
2. 核对 HEAD/上游一致、空暂存与空工作区；禁止操作 `main`。
3. 记录 Grok A/B 只读审计回执和旧 P11A 必须 test-only 更新的双方确认。

### 任务 2：建立独立 A/B worktree 与路由

1. 从冻结提交创建：
   - B 测试：`C:\Users\Administrator\biaoshu-v1i-create-file-intake-test`，分支 `collab/v1i-create-file-intake-test`；
   - A 实现：`C:\Users\Administrator\biaoshu-v1i-create-file-intake-impl`，分支 `collab/v1i-create-file-intake-impl`。
2. 仅结束旧 A/B 路由 PID，绝不结束用户交互式 Grok PID `12456`。
3. 后台静默重启路由：B 绑定测试 worktree，A 绑定实现 worktree；socket 前缀固定 `v1i-create-file-intake-grok`，继续代理 `127.0.0.1:7890`。
4. 两个 worktree 的 E2E SQLite 和产物天然按路径隔离；任何时刻只允许一个 Playwright 运行。

### 任务 3：Grok B failure-first

**唯一可写：**

- `frontend/e2e/create-file-intake-truth.spec.ts`；
- `frontend/e2e/core-project-data-truth.spec.ts`。

1. 先实现契约 §5 的真实 input/drop、multipart 字节、顺序、单飞、创建失败、部分上传重试、无文件、旧 pending 隔离和泄漏探针。
2. P11A 只改必然冲突的创建页用例与必要 route 计数/响应；其它 P11A 用例和反假绿边界不得删减。
3. 确认 8010/5174 空闲后，只串行运行新专项和 P11A；生产未改必须报告真实首红与首个业务红点。
4. 报告两个测试哈希、精确 diff、存储/外网/console 探针、`git diff --check` 和空暂存；不得提交或推送。

### 任务 4：Codex 审查并转移冻结测试

1. 逐行排除无效 multipart 解析、宽 route、按文件名冒充字节、固定 sleep、同步双击不可达、失败重试假绿和 P11A 断言被放宽。
2. 独立串行复跑两个红测；疑似测试问题先 question，Grok B 只读确认后才授权 test-only 返修。
3. 合格后由 Codex 在 B 分支仅提交两个测试文件，中文提交；记录提交与 SHA-256。
4. 由 Codex 把该 test-only 提交 cherry-pick 到 A 实现分支；不得通过未审查复制或让 Grok 操作 Git。

### 任务 5：Grok A 最小生产实现

**唯一可写：**

- `frontend/src/features/create/pages/CreatePage.tsx`；
- `frontend/src/features/technical-plan/lib/projectStore.ts`；
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`。

1. 实现真实 input/drop、多文件稳定状态、同步单飞、一次创建、串行上传与同项目剩余项重试。
2. projectStore 删除 `fileNames`/pending 读写，新增复用 `apiUploadFile` 的薄上传门面。
3. 工作区删除 pending 回退，仅展示 `pipeline.files`；商务入口和其它技术工作区逻辑不动。
4. 不修改冻结测试。先跑两个 V1-I/P11A 专项，再跑技术 editor-state truth、lint/build/diff-check；完成后只发 review_request。

### 任务 6：Codex 独立审查与验收

1. 核对严格五文件、冻结测试哈希、空暂存和无额外产物。
2. 静态审查 create/upload 顺序、同步 ref 单飞、部分成功集合、固定错误、File 生命周期、storage/URL/console 边界和商务隔离。
3. 严格执行契约 §6，禁止机械全量；清理 8010/5174 和 `test-results`。
4. 发现问题先发 question；Grok A 或 B 只读确认存在后，Codex 才按 production-only/test-only 下发返修 task。

### 任务 7：提交、推送与文档闭环

1. Codex 在 A 分支中文提交三个生产文件；确认历史为冻结文档 → test-only → production。
2. 主协作分支从冻结提交快进到 A 分支，严禁 merge 到 `main`；只推送 `collab/grok-code-codex-review`。
3. 更新契约/计划、`HANDOFF-next.md`、路线图和联调清单，记录真实红绿、消息链、五文件哈希与未运行项；中文提交并推送。
4. 再只读审计 V1 首日主链的下一个单一断点，V2/V3 继续后置。

### 完成记录

1. B 生产未实现基线最终保持新专项 **8 failed / 0 passed**、P11A **1 failed / 9 passed**；Codex 独立复跑结果相同。两次 test-only 提交为 `35b0f6b` 与 `b8c9776`。
2. A 生产实现只改三个白名单文件；Codex 独立 V1-I/P11A/技术 editor-state truth 为 **8/10/28 passed**，lint、build、diff-check、端口和产物门通过。
3. 生产实现=`4e7a3c3` 已快进并推送 `collab/grok-code-codex-review`；`main` 未操作。完整消息链、五文件 blob/SHA-256 与未运行项见契约 §8。
4. 所有测试强度、生产和注释问题均按 `question → Grok YES → task → review_request` 关闭；Grok A/B 全程未暂存、提交或推送。
