# P13-G2 在途操作级交接：项目章节编辑意图前端提示

> 日期：2026-07-20
> 当前状态：**功能与文档闭环已完成，代码提交 `86abbbf` 已推送**
> 审计基线：`6dac9da11ace946a06bba6e2588fccd6303e1e83`
> 契约冻结：`3a74fbb`
> 功能实现：`86abbbf`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13g2-project-chapter-edit-intent-frontend-contract.md`
> 计划：`docs/plans/2026-07-20-p13g2-project-chapter-edit-intent-frontend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu 剩余产品主线。P13-G2 项目章节编辑意图前端提示已经完成并推送。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13g2-in-progress.md、docs/p13g2-project-chapter-edit-intent-frontend-contract.md、docs/plans/2026-07-20-p13g2-project-chapter-edit-intent-frontend-plan.md、docs/HANDOFF-p13g1-in-progress.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 与 GitHub 实际分支一致且工作区干净。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add .、并发 Playwright 或沿用旧包白名单。

P13-G2 已交付技术标 content 步的 advisory 章节处理意图提示，不是硬锁。严格四文件已提交为 86abbbf，Codex 独立专项/直接回归为 13/11/17 passed，lint/build/diff/哈希门通过。

下一能力包必须先重新只读审计、冻结契约与白名单，再交 Grok 做高耗费 failure-first 和实现。Codex 疑似问题仍须先让 Grok 只读确认；双方确认存在后才发新返修 task。
```

## 2. Git 与文件真值

P13-G2 功能提交推送后，本地、远端引用与 GitHub 实际分支均包含：

```text
86abbbf 功能：交付P13G2章节编辑意图前端提示
```

严格白名单最终哈希：

| 文件 | SHA-256 / 状态 |
|---|---|
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `28FC4A924A440EF826A68B0E93F6809E829AC42867A37995F6B92BFBDB7A0E34` |
| `frontend/src/features/editor-state-collaboration/chapterEditIntentApi.ts` | `0F21861AC8F985EE453CBC810A71959AF02291611639C84B2B29AAAC31FA3EA8` |
| `frontend/src/features/editor-state-collaboration/ChapterEditIntentPanel.tsx` | `064D639D67C08886F93FB04E1A3419BCA15AC32CE8F92BD79AA39B2B7992DCE1` |
| `frontend/e2e/project-chapter-edit-intent.spec.ts` | `364325A90D6AFD59079A2D6E83915A1F6FA07D0F185C1BA278F6B8ADB6158369` |

只读依赖哈希见实施计划，必须保持不变。

## 3. 冻结结论

- 只在技术标 `content` 步对当前有效 `editors.selectedChapterId` 启用；不得在标题区或 Hook 常驻。
- 复用 P13-F2 文档内存 UUID，不修改 P13-F2；章节租约使用独立串行队列。
- 新 API 用内存 CSRF + 同源 fetch 严格解析 200/409；共享 `apiFetch` 会丢 holderUsername，因此不得直接使用或扩公共客户端。
- 冲突只显示安全 holderUsername，不禁用编辑器、按钮或 editor-state PUT。
- 章节/项目/步骤/可见性切换必须 heartbeat/leave 串行并隔离迟到；初始 hidden 零 UUID/零写。
- 商务标、强制锁、协同光标、SSE/WebSocket、广播、历史、通知、评论审批全部不在本包。

## 4. 协作状态

P13-G2 已按 failure-first、实现、自测、Codex 独立审查、双确认返修、最终验收和精确提交完成：

- failure-first=`msg_b20b7dbe314943ba806fcf62f37d95c9`，真实 `8 failed / 1 passed`。
- 第一轮只读双确认=`msg_9fa0bb83f0f348f99eca175567b3983d`。
- 第二轮宽计数问题只读双确认=`msg_24da16ad88c94f7585de0a34ef88095d`。
- Grok 最终 review=`msg_7a542b4e3d444c13800cc401141a0d90`，专项 `13 passed`、聚焦关键序列 `7 passed`，lint/diff 通过。
- Codex 独立静态复核未发现剩余阻断问题，因此未触发第三轮返修。

Grok OAuth 已重新认证成功；后续启动仍须显式继承本机 Clash `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`，并先确认没有同包重复进程。

## 5. 已验收边界

1. selectedChapter 是有效回退选择，不等于原始 `useState`；面板必须使用页面已返回的 `editors.selectedChapterId`。
2. API 409 必须严格校验顶层/detail 精确键、固定 code/message 和安全用户名；坏包不得部分展示。
3. P13-F2 presence 与 G2 chapter lease 共享一个文档 UUID，但两个队列独立。
4. StrictMode、慢请求、A→B、hidden/visible/pagehide 必须用真实 timer/gate/顺序证据。
5. UI 与状态同步绑定项目/章节；旧项目/旧章节迟到不得出现一帧。
6. conflict/unavailable 不得阻断正文输入、autosave、AI、卡片、图片或 CAS。
7. clientId/chapterId/holderUsername 隐私门不得过滤掉真实 console 或 fetch body。

## 6. 最终测试与提交

Codex 最终独立串行结果：

- `project-chapter-edit-intent.spec.ts`：`13 passed (1.5m)`；
- `project-presence.spec.ts`：`11 passed (28.4s)`；
- `editor-state-version-freshness.spec.ts`：`17 passed (57.6s)`；
- `npm run lint`：通过；
- `npm run build`：通过，仅既有 chunk 大小警告；
- `git diff --check`、严格四文件、P13-F2 四个只读依赖哈希、空暂存与临时工件清理：通过。

功能提交=`86abbbf`，已推送 `origin/collab/grok-code-codex-review`。未运行整仓 318 E2E、后端 pytest、xdist 或并发 Playwright。

P13-G2 仍只表示近期处理意图，不是强制锁、实时协作或在线状态。协同光标、事件广播/重放、WebSocket、评论、审批、通知与完整多人协作继续拆为独立后续包。
