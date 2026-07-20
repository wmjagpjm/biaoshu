# P13-B 已载入编辑版本更新时间可见性实施计划

> **执行者：Grok**：严格按六文件白名单先真实业务红测再实现；Codex 负责受限审查、独立验收、中文文档闭环、提交与推送。

> **状态**：2026-07-20 已完成并推送；冻结=`040d644`、实现=`1d4fe0b`，Codex 独立专项 **6 passed**。

**目标：** 不改后端和请求合同，利用 editor-state 既有 `updatedAt` 为技术标/商务标增加当前已载入版本的 UTC 更新时间，并在成功保存、重载和项目切换时保持准确隔离。

**技术栈：** React 19、TypeScript 6、既有 `apiFetch`、Playwright 1.61；无新依赖。

## 1. 基线与 failure-first

1. 核对分支精确为 `collab/grok-code-codex-review`，冻结 HEAD/远端一致且工作区仅含 Codex 已提交文档。
2. 完整读取 P13-B 契约、两份 Hook、两份页面和技术/商务 editor-state 真值 E2E；列出所有接受合法服务端 `stateVersion` 的 GET/PUT 路径。
3. 只新建 `frontend/e2e/editor-state-version-freshness.spec.ts`，先证明技术标/商务标标题区缺少固定标识；记录红测命令、数字、首个业务断言和五个生产文件未修改哈希。

## 2. 共享严格格式化组件

1. 新建 `EditorStateVersionFreshness.tsx`，补齐中文文件顶“模块/用途/对接/二次开发”注释。
2. 用纯函数只接受无后缀 UTC ISO：年-月-日、时:分:秒，允许 1–6 位小数；对月日和时分秒做真实范围校验，拒绝 `Z`、偏移、空白包裹和尾随字符。
3. 合法值输出到秒并追加 `UTC`；其余固定输出“更新时间未知”。组件不得产生 effect、timer、request 或持久化。

## 3. 技术标 Hook 与页面

1. `EditorStateApi` 增加可选 `updatedAt` 类型；新增 `versionUpdatedAt` 状态和窄 helper。
2. 切项目同步清空；初始 GET、显式 `reloadFromApi`、普通/即时/合并成功响应只在既有会话与版本合法门之后接受同一响应时间。
3. 失败、409、非法版本和迟到响应不更新；不得因此改变任何保存/阻断/矩阵/检查点/修订行为。
4. Hook 返回 `versionUpdatedAt`；页面标题区使用共享组件和固定技术标 testid。

## 4. 商务标 Hook 与页面

1. `EditorStateApi` 增加可选 `updatedAt`；新增并返回 `versionUpdatedAt`。
2. 复用商务标现有 session/write epoch：项目切换清空，成功 GET/PUT 接受，失败/409/迟到保留或忽略。
3. 页面标题区使用同一共享组件和固定商务标 testid；禁止复制格式化逻辑。

## 5. E2E 与静态门

1. 以可变 editor-state route 桩覆盖两类工作区的初始合法/非法时间、成功 PUT、409/失败、显式重载和 A→B 迟到隔离。
2. 精确计数 GET/PUT，证明新增展示零额外请求；监控 `pageerror` 与 console error。
3. 断言无未实现协作承诺文案；不得使用 sleep、宽泛 `or`、只读 route 自证或可被恒真满足的计数。
4. Grok 串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-version-freshness.spec.ts --project=chromium --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --project=chromium --workers=1 --retries=0
npm run lint
npm run build
```

5. 执行 `git diff --check`，核对精确六文件、空暂存区、无未跟踪非白名单文件；不得运行后端 pytest 或整仓 Playwright。

## 6. 审查、提交与下一增量

1. Grok 通过消息箱发送 `review_request`，如实报告红/绿数字、接受更新时间的全部响应路径、请求计数、六文件和未做边界；不得提交或推送。
2. Codex 审查时间语义、成功门、409 保值、A→B 隔离、无额外请求和未实现承诺；问题仅下发六文件内最小返修。
3. Codex 独立运行 P13-B 专项与必要静态门；确认受影响真值已由 Grok 通过且差异无疑点时，不机械重复两套真值或整仓 318 E2E。
4. Codex 提交实现并推送，再更新 HANDOFF、路线图、联调清单、契约和计划，单独提交中文闭环。
5. 下一增量 P13-C 再审计精确操作者归因；必须覆盖所有写入口和 SQLite 迁移，禁止沿用本包六文件或把旧浏览器操作者误报为自动任务后的最新操作者。

## 7. 明确未做

不改后端/API/数据库/请求数，不做用户身份、online/presence、轮询、SSE/WebSocket、相对时间刷新、协同编辑、评论审批、通知、审计或跨项目时间线。

## 8. 实际执行记录

初始任务/review_request=`msg_7cb045b4462c4339936da5b6d61847b3`/`msg_fcf02c791c7a4bc985f75f9358dec8f4`；真实 failure-first **6 failed / 0 passed**。实现后 Grok P13-B/技术商务真值为 **6/46 passed**，lint/build 通过。

Codex 仅针对测试证据下发返修=`msg_99198f2e001c4619b9913ad65cf67df6`，最终 review_request=`msg_5a0de7a89a624787a4d421c14faf0b6f`：关闭死 GET gate、宽泛请求数与缺失真实 PUT abort，生产五文件逐字冻结。Codex 独立 P13-B **6 passed（24.7s）**、lint 通过，验收回执=`msg_73ddfc7f7da243aaa2c5705e564664d9`。按分级策略未运行后端 pytest、未重复 46 truth、build 或整仓 318 E2E；实现由 Codex 提交推送，Grok 未执行 Git。
