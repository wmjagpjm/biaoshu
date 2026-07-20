# P13-F2 在途操作级交接：项目近期成员前端

> 日期：2026-07-20
> 当前状态：**只读审计与设计已完成，本版本冻结契约/计划；尚未下发 Grok、尚未实现**
> 开工基线：`66f4390999bd9da750a439bc13d0ec7627a7f9e9`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13f2-project-presence-frontend-contract.md`
> 计划：`docs/plans/2026-07-20-p13f2-project-presence-frontend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu P13-F2 项目近期成员前端。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13f2-in-progress.md、docs/p13f2-project-presence-frontend-contract.md、docs/plans/2026-07-20-p13f2-project-presence-frontend-plan.md、docs/p13f1-project-presence-lease-backend-contract.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 和 GitHub 实际分支。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add . 或并发测试。

P13-F2 必须严格四生产加一新 E2E：新 projectPresenceApi.ts、新 ProjectPresencePanel.tsx、技术标页面、商务标页面、新 project-presence.spec.ts。不得沿用 P13-F1 白名单，不改后端、api.ts、auth、router、editor Hook、CSS、依赖、已有测试或文档。

Grok 先只写 E2E 做真实 failure-first，再实现并串行自测。Codex 发现疑似问题后先发只读 review；只有 Grok 与 Codex 都确认问题存在，才另发独立返修 task。Grok 不得暂存、提交、推送或写文档。
```

## 2. Git 与文件真值

文档编辑前本地/远端均为：

```text
66f4390999bd9da750a439bc13d0ec7627a7f9e9
```

代码开工白名单：

| 文件 | 开工 SHA-256 |
|---|---|
| `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts` | 不存在 |
| `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx` | 不存在 |
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `80553F5A147199EAB87668FEE0932393ABD7D395B829052763D9780DE287D866` |
| `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx` | `0D648AE6432D2273CE43A91C0CB7E88665CDF8ECBE1268FA9ABE9251EC44F0C5` |
| `frontend/e2e/project-presence.spec.ts` | 不存在 |

## 3. 冻结设计摘要

- visible-only：required strict bid_writer 的可见项目页面才 heartbeat；hidden leave，visible 立即恢复。
- 文档级 UUID clientId，只在模块内存与精确 JSON body；整页刷新重建。
- heartbeat/leave 文档级 Promise 串行；成功后 15 秒调度，不用并发 setInterval。
- StrictMode 首轮 effect 探测不得产生重复租约；项目 A→B 同步隐藏 A、leave A、heartbeat B，迟到 A 零渲染。
- 响应精确四键，成员精确两键、最多 50、唯一 self、安全用户名；坏响应整包固定失败。
- UI 只称“近期在此项目”，不得称在线、实时、正在编辑或正在输入。

## 4. 协作消息状态

当前尚未发送 P13-F2 task。本文所在冻结提交推送后才能写消息箱；task 必须引用实际冻结提交 SHA。

发送工具：

```powershell
cd C:\Users\Administrator\biaoshu
powershell -ExecutionPolicy Bypass -File tools\agent-collaboration\Send-AgentMessage.ps1 `
    -From codex -To grok -Kind task -Body '<以冻结提交 SHA、五文件白名单、failure-first、测试门和禁止项组成的完整任务>'
```

后台静默启动前先确认没有同一 Grok 任务进程；只在新 task 存在时启动：

```powershell
cd C:\Users\Administrator\biaoshu
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:ALL_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
$stdout = '.agent-collaboration\grok-p13f2.stdout.log'
$stderr = '.agent-collaboration\grok-p13f2.stderr.log'
$arguments = '--cwd "C:\Users\Administrator\biaoshu" --single "读取 .agent-collaboration/messages/codex-to-grok.jsonl 中最新一条 Codex 消息，严格按消息执行；若是 review 只读确认，若是 task 才按白名单实现；完成后仅通过消息箱回复，不要提交或推送。" --always-approve --disable-web-search --no-subagents --output-format json'
Start-Process -FilePath 'C:\Users\Administrator\.grok\bin\grok.exe' -ArgumentList $arguments -WorkingDirectory 'C:\Users\Administrator\biaoshu' -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
```

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
