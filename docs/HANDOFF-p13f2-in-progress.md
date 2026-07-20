# P13-F2 完成态操作级交接：项目近期成员前端

> 日期：2026-07-20
> 当前状态：**P13-F2 已完成 Codex 独立审查、串行验收、中文功能提交并推送**
> 开工基线：`66f4390999bd9da750a439bc13d0ec7627a7f9e9`
> 契约冻结：`a5709edee5cea66e2c9f5ee2978b562d6075ab67`
> 功能实现：`dfa6bc04d21b6919d722e25d86920f39678916f1`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13f2-project-presence-frontend-contract.md`
> 计划：`docs/plans/2026-07-20-p13f2-project-presence-frontend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu 剩余主线。P13-F2 项目近期成员前端已完成并推送，仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13f2-in-progress.md、docs/p13f2-project-presence-frontend-contract.md、docs/plans/2026-07-20-p13f2-project-presence-frontend-plan.md、docs/p13f1-project-presence-lease-backend-contract.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 和 GitHub 实际分支。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add . 或并发测试。

先确认 P13-F2 功能提交 dfa6bc0 与文档闭环均已在远端，工作区为空。P13-F2 严格四生产加一新 E2E 已完成，不得继续沿用该白名单或把它扩成真实在线协作。

下一能力包必须重新只读审计、冻结独立契约和白名单，再由 Grok 实现与自测、Codex 独立审查。疑似问题仍须双方只读确认存在后才可另发返修 task。
```

## 2. Git 与文件真值

功能提交后本地、远端引用与 GitHub 实际分支均为：

```text
dfa6bc04d21b6919d722e25d86920f39678916f1
```

代码开工白名单：

| 文件 | 开工 SHA-256 |
|---|---|
| `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts` | 不存在 |
| `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx` | 不存在 |
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `80553F5A147199EAB87668FEE0932393ABD7D395B829052763D9780DE287D866` |
| `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx` | `0D648AE6432D2273CE43A91C0CB7E88665CDF8ECBE1268FA9ABE9251EC44F0C5` |
| `frontend/e2e/project-presence.spec.ts` | 不存在 |

最终白名单 SHA-256：

| 文件 | 最终 SHA-256 |
|---|---|
| `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts` | `0899D383135AFBA206B492DE5064BB304BA892AC2E8986BEBFCB07530427BCFB` |
| `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx` | `E9452B3AA830FCB96ABC263B7C4DC32FC18723E37B738F723E2F7F6F0E33CF18` |
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `B0904371AFA5587FAAA0ACF0814898A067091CE3EEEF4C6489CC82C89CAA86AB` |
| `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx` | `6BE2959C50454C3528087D39A288288DF9D5AC4D70D74337A02AE6D6993619BB` |
| `frontend/e2e/project-presence.spec.ts` | `67C554ECAB2B4894DB235A8416B5E36E62F384F11E4701F5CAC74610C3AB0802` |

## 3. 冻结设计摘要

- visible-only：required strict bid_writer 的可见项目页面才 heartbeat；hidden leave，visible 立即恢复。
- 文档级 UUID clientId，只在模块内存与精确 JSON body；整页刷新重建。
- heartbeat/leave 文档级 Promise 串行；成功后 15 秒调度，不用并发 setInterval。
- StrictMode 首轮 effect 探测不得产生重复租约；项目 A→B 同步隐藏 A、leave A、heartbeat B，迟到 A 零渲染。
- 响应精确四键，成员精确两键、最多 50、唯一 self、安全用户名；坏响应整包固定失败。
- UI 只称“近期在此项目”，不得称在线、实时、正在编辑或正在输入。

## 4. 协作消息链

- 初始 task/failure-first/哈希补全/review：`msg_c4b8c3db2b844373a2d9473e2cada9ab` / `msg_85e70a7b4d6e4a2783eb7d1d3bbf072a` / `msg_dc586657dc734fc891be0156e5825614` / `msg_d496bf3eb7874f95ab5ff1ca1e109247`。
- 第一轮 Codex 只读问题/Grok 确认：`msg_1e069e62860443f3b40ff942a71c8a78` / `msg_e09a746f34af4c6cbff56a0e7119e0fd`，八项全部确认存在。
- 第一轮返修 task/红测/review：`msg_dce02edef2f64cbb8c869cf8c38fb496` / `msg_ea67dfcc73b740a1b7708c62b6db681b` / `msg_33b51876c9a04a5590e2bfbee366b9b1`。
- 第二轮 Codex 只读问题/Grok 确认：`msg_cb83ccf6fe9844138e83fae417829d13` / `msg_4bc81573d30d4f80ada262a540cb81ba`，console 实际 UUID 门与 hidden 延迟生成证据缺口均确认存在。
- 第二轮 E2E-only task/review/Codex result：`msg_534a0dc70d9e4ff7ae53e9a54d7f7d0b` / `msg_d46854d96aac4c6db75e4348f9012dc3` / `msg_f19ceb09650a4f0584e2d4b1d1985fb4`。

## 5. 串行测试门

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e\project-presence.spec.ts --workers=1 --retries=0
npx playwright test e2e\editor-state-version-freshness.spec.ts --workers=1 --retries=0
npm run lint
npm run build

cd ..
git diff --check
git status --short
```

禁止整仓 318 E2E、后端 pytest、并发 Playwright、重复 Grok 进程或将历史基线冒充本轮结果。

## 6. Codex 审查顺序

1. 白名单、开工哈希、暂存区和运行工件门。
2. E2E failure-first 是否真实，测试是否存在初值假稳定、未触发事件或宽网络断言。
3. clientId 零持久化/零出口与 CSRF/同源边界。
4. StrictMode、串行写链、timer、hidden/visible、pagehide、A→B 和迟到隔离。
5. 精确响应 parser、username 安全文本、唯一 self、truncated 和固定错误。
6. 技术/商务薄挂载与现有 editor-state 行为零漂移。
7. 发现问题先走只读双确认；双方确认后才发新返修 task。

## 7. 禁止事项

- 禁止 Codex 直接代写 P13-F2 主实现；高耗费实现与自测交给 Grok。
- 禁止 Grok 写文档、Git、后端或白名单外文件。
- 禁止把确认消息当修复授权，禁止未双确认先返修。
- 禁止 `git add .`、force push、操作 `main`、清理未知文件或并发测试。
- 禁止声称真实在线、协同编辑、光标、锁、广播、WebSocket、评论或审批已完成。

## 8. 真实红绿与最终验收

- 初始 failure-first：`7 failed / 1 passed`；初版 Grok 专项/freshness：`8/17 passed`，lint/build/diff-check 通过。
- 第一轮返修真实红测：`2 failed / 0 passed`，分别证明非法 UUID 被旧长度门放行、初始 hidden 错误显示 loading；返修后专项/freshness：`10/17 passed`。
- 第二轮严格 E2E-only 收口：Grok 聚焦 `3 passed`、完整专项 `11 passed`，lint/diff-check 通过；没有重复 freshness/build。
- Codex 独立串行验收：专项 `11 passed（26.6s）`，freshness `17 passed（56.5s）`，lint 与 diff-check 通过。
- build 沿用第一轮最终生产改动后的 Grok 成功结果；未运行整仓 318 E2E、后端 pytest 或并发 Playwright。

## 9. 最终实现边界

- 文档级 canonical UUID v4 只在首次 visible 延迟生成，模块内存复用；缺失、抛错或非法格式固定禁用且零写。
- heartbeat/leave 统一 Promise 串行；StrictMode 首跳稳定唯一，成功或失败后 15 秒再调度，慢请求不并发。
- hidden 清 UI 并 leave，visible 立即 heartbeat；A→B 先完成旧在途写、leave A、再 heartbeat B，迟到 A 零渲染与零续排。
- parser 严格四键/两键、最多 50、唯一 self、1..100 Unicode 码点安全用户名；坏包整包固定不可用。
- 原始 console、fetch body、DOM/HTML、URL、storage、Cookie、IndexedDB 写、clipboard 和外网均有隐私门；pagehide 真实证明 `keepalive=true`。

## 10. 已接受残余与下一步

pagehide leave 仍进入模块级 Promise 队列，可能被挂起 heartbeat 延迟。契约同时要求所有 presence 写串行，服务端 45 秒过期为最终兜底，因此双方接受为残余风险，不阻断 P13-F2。

P13-F2 白名单已失效。下一步只读审计新的多人协作能力包；协同光标、章节锁、事件广播/重放、WebSocket、评论、审批和在线历史仍未实现，必须独立冻结，禁止从本包直接扩围。
