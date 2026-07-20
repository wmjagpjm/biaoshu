# P13-G2 在途操作级交接：项目章节编辑意图前端提示

> 日期：2026-07-20
> 当前状态：**只读审计与设计已完成，等待冻结提交后下发 Grok**
> 审计基线：`6dac9da11ace946a06bba6e2588fccd6303e1e83`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13g2-project-chapter-edit-intent-frontend-contract.md`
> 计划：`docs/plans/2026-07-20-p13g2-project-chapter-edit-intent-frontend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu P13-G2 项目章节编辑意图前端提示。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13g2-in-progress.md、docs/p13g2-project-chapter-edit-intent-frontend-contract.md、docs/plans/2026-07-20-p13g2-project-chapter-edit-intent-frontend-plan.md、docs/HANDOFF-p13g1-in-progress.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 与 GitHub 实际分支一致。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add .、并发 Playwright 或沿用 P13-G1 后端白名单。

P13-G2 只做技术标 content 步的 advisory 章节处理意图提示，不是硬锁。严格四文件：新 chapter API、新 panel、TechnicalPlanWorkspace 薄挂载、新 E2E。不得修改 P13-F2、共享 api/auth/router、editor Hook、ChapterEditor、CSS、后端、依赖、配置或已有测试。

Grok 先只写新 E2E 做真实 failure-first，再实现并串行自测。Codex 疑似问题必须先让 Grok 只读确认；双方确认存在后才发新返修 task。Grok 不得暂存、提交、推送或写文档。
```

## 2. Git 与文件真值

审计时仓库干净，本地、远端引用与 GitHub 实际分支均为：

```text
6dac9da11ace946a06bba6e2588fccd6303e1e83
```

严格白名单基线：

| 文件 | SHA-256 / 状态 |
|---|---|
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `B0904371AFA5587FAAA0ACF0814898A067091CE3EEEF4C6489CC82C89CAA86AB` |
| `frontend/src/features/editor-state-collaboration/chapterEditIntentApi.ts` | 不存在 |
| `frontend/src/features/editor-state-collaboration/ChapterEditIntentPanel.tsx` | 不存在 |
| `frontend/e2e/project-chapter-edit-intent.spec.ts` | 不存在 |

只读依赖哈希见实施计划，必须保持不变。

## 3. 冻结结论

- 只在技术标 `content` 步对当前有效 `editors.selectedChapterId` 启用；不得在标题区或 Hook 常驻。
- 复用 P13-F2 文档内存 UUID，不修改 P13-F2；章节租约使用独立串行队列。
- 新 API 用内存 CSRF + 同源 fetch 严格解析 200/409；共享 `apiFetch` 会丢 holderUsername，因此不得直接使用或扩公共客户端。
- 冲突只显示安全 holderUsername，不禁用编辑器、按钮或 editor-state PUT。
- 章节/项目/步骤/可见性切换必须 heartbeat/leave 串行并隔离迟到；初始 hidden 零 UUID/零写。
- 商务标、强制锁、协同光标、SSE/WebSocket、广播、历史、通知、评论审批全部不在本包。

## 4. 协作状态

当前尚未发送 P13-G2 task。必须先提交并推送冻结文档，再让 task 引用实际冻结提交 SHA、严格四文件与基线哈希。

Grok OAuth 可用；启动命令须显式继承本机 Clash `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`。启动前确认没有同一 P13-G2 Grok 进程，禁止重复进程。

## 5. 审查重点

1. selectedChapter 是有效回退选择，不等于原始 `useState`；面板必须使用页面已返回的 `editors.selectedChapterId`。
2. API 409 必须严格校验顶层/detail 精确键、固定 code/message 和安全用户名；坏包不得部分展示。
3. P13-F2 presence 与 G2 chapter lease 共享一个文档 UUID，但两个队列独立。
4. StrictMode、慢请求、A→B、hidden/visible/pagehide 必须用真实 timer/gate/顺序证据。
5. UI 与状态同步绑定项目/章节；旧项目/旧章节迟到不得出现一帧。
6. conflict/unavailable 不得阻断正文输入、autosave、AI、卡片、图片或 CAS。
7. clientId/chapterId/holderUsername 隐私门不得过滤掉真实 console 或 fetch body。

## 6. 测试边界

Grok：新专项、P13-F2 presence、freshness、必要技术标真值代表节点、lint/build/diff。Codex 独立复跑新专项和必要直接回归。

明确不跑：整仓 318 E2E、后端 pytest、xdist、并发 Playwright 或无关前端 spec。Playwright 固定 `--workers=1 --retries=0`。
