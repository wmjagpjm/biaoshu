# P12N 已加载修订固定优先前端实施计划

> **执行要求：** Grok 按本计划逐步测试先行实现；不得执行 Git 写操作。Codex 逐项审查、独立聚焦验收并负责提交推送。

> **状态：2026-07-20 已完成。** 冻结=`337b401`、实现=`394639a`；Grok 和 Codex 均按分级策略完成验收，未运行全量。

**目标：** 默认/筛选态把当前已加载修订稳定分为固定组和普通组，固定优先；搜索态保持原顺序。

**架构：** 不改变 state、API 或游标。面板 render 期从严格解析后的 `items` 单次遍历派生 `displayItems`，非搜索态拼接固定/普通两组，搜索态直接复用原数组；所有交互继续以 `revisionId` 为身份。

**技术栈：** React + TypeScript；Playwright Chromium；oxlint；Vite/TypeScript build。

---

## 任务 1：建立真实 failure-first

**文件：**

- 修改：`frontend/e2e/editor-state-revision-history.spec.ts`
- 冻结：`frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`

**步骤：**

1. 新增 P12N 聚焦用例，先让默认混合列表在真实 GET 完成后断言固定项位于普通项前，且两组内部原顺序不变。
2. 新增 pin/unpin 成功即时移动与失败保值；精确断言 PATCH 次数，list/page/search/editor-state 旁路为零。
3. 新增加载更多后第二页固定项进入已加载固定组、筛选态同规则、active search 保持服务端顺序与 `matchReasons` 索引。
4. 增加技术/商务共用、A→B 迟到隔离、动作按 revisionId 命中、泄漏与 marker 静态自检。
5. 复算面板哈希仍等于冻结值，运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12N" --project=chromium --workers=1 --retries=0
```

预期：业务用例因旧界面保持 `items` 原顺序而失败；静态 marker 自检可通过。不得以白页、请求失败或未加载元素作为红测。

## 任务 2：实现纯派生稳定分组

**文件：**

- 修改：`frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
- 测试：`frontend/e2e/editor-state-revision-history.spec.ts`

**步骤：**

1. 在面板 render 路径增加局部纯函数或纯派生逻辑：单次遍历 `items`，以 `isPinned === true` 分为固定/普通两组。
2. active search 返回原 `items`；其它状态返回 `[...pinnedItems, ...unpinnedItems]`，不调用原地 sort。
3. 主列表从 `displayItems.map` 渲染；key 仍为 `revisionId`，全部 handler 继续传 `revisionId`，不改 API/请求/状态/副作用。
4. 运行 P12N 聚焦命令，预期全部通过。

## 任务 3：受影响串行验证

**文件：**

- 验证：上述严格两文件

**步骤：**

1. 逐条运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12N" --project=chromium --workers=1 --retries=0
npx playwright test e2e/editor-state-revision-history.spec.ts --grep "P12F-C|P12F-J-B|P12M" --project=chromium --workers=1 --retries=0
npm run lint
npm run build
```

2. 运行 `git diff --check`，确认 `git diff --name-only` 精确两文件、`git diff --cached` 为空并记录最终 SHA-256。
3. 静态检查 P12N 区块无 `force:true`、`waitForTimeout`、skip、retry、sleep、Promise.race 或宽断言；面板无新增 fetch/axios/storage/console/state/effect/sort。
4. 通过消息箱发送唯一 `review_request`；不要 `git add/commit/push`。

## 任务 4：Codex 独立验收与闭环

**文件：**

- 审查：严格两文件差异
- 更新：本契约/计划、`docs/HANDOFF-next.md`、路线图、联调清单

**步骤：**

1. Codex 审查纯派生、稳定顺序、revisionId 身份、搜索冻结、请求零新增与迟到隔离。
2. 独立运行 P12N 聚焦、lint、diff/哈希/白名单/空暂存/泄漏门；聚焦失败才升级受影响范围。
3. 验收通过后由 Codex 使用中文提交实现与文档，推送 `collab/grok-code-codex-review` 并核对远端一致。

## 完成标准

- 已加载默认/筛选列表固定优先且组内稳定，pin/unpin/加载更多即时反映；
- active search 顺序、P12M 标签、游标与所有网络合同无回退；
- 两文件、串行聚焦、lint/build/静态门通过，Grok 零 Git 写操作；
- 文档明确这不是服务端权威第一页固定优先，后续增强边界不被掩盖。

## 执行结果

1. failure-first：**4 failed / 1 passed**；面板 SHA-256 与冻结值一致。三项真实业务失败为默认混合、筛选/时间或双工作区场景仍按旧 `items` 顺序显示，静态守卫同时证明尚无 `displayItems`。
2. 生产只新增 render 期 `displayItems` 纯派生并把主列表改为其 map；无可执行的新 state/effect/ref/fetch/sort，active search 原序不变。
3. Grok P12N/受影响 history **5/12 passed**，lint/build 通过；Codex 独立 P12N **5 passed in 9.4s**、lint 和静态/哈希/差异门通过。
4. 冻结文字中“新固定项进固定组末尾”与“组内保持服务端原序”冲突；验收以核心稳定原序合同为准，契约已澄清为按原始 `items` 位置进入固定组，不承诺绝对末尾。
5. 消息追溯：task/review/ack=`msg_821f2f19ef8044fcbd85f28cc764de29`/`msg_449e2631192944c39419507c4956c161`/`msg_77a0632fdf5e4eb5bd21ea9e32205430`。
