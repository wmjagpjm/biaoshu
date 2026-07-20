# P13-F2 项目近期成员前端实施计划

> 执行要求：实现时必须按 `executing-plans` 工作流逐项执行
> 契约：`docs/p13f2-project-presence-frontend-contract.md`
> 开工基线：`66f4390999bd9da750a439bc13d0ec7627a7f9e9`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 测试：Playwright 固定 `--workers=1 --retries=0`；禁止并发或机械全量

## 1. 目标、架构与技术栈

**目标**：在技术标和商务标项目页安全接入 P13-F1 短租约，展示“近期在此项目”的成员快照。

**架构**：新增共享 presence API/严格 parser 与共享生命周期面板；两工作区只做薄挂载。文档级 clientId 和 presence 写链为模块内存，visible-only heartbeat，隐藏/切项目/卸载/pagehide best-effort leave。

**技术栈**：React 19、TypeScript、既有 `apiFetch`、Playwright 1.61；不新增依赖、CSS、后端或存储。

## 2. 基线与冻结哈希

| 文件 | 开工 SHA-256 |
|---|---|
| `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts` | 不存在 |
| `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx` | 不存在 |
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `80553F5A147199EAB87668FEE0932393ABD7D395B829052763D9780DE287D866` |
| `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx` | `0D648AE6432D2273CE43A91C0CB7E88665CDF8ECBE1268FA9ABE9251EC44F0C5` |
| `frontend/e2e/project-presence.spec.ts` | 不存在 |

Grok 不得写文档、暂存、提交、推送、切分支或修改 `main`。任何扩围必须先 status 报告并等待 Codex 授权。

## 3. 任务一：真实 E2E failure-first

1. 只创建 `frontend/e2e/project-presence.spec.ts`，建立 required strict bid_writer 的同源路由桩，记录 heartbeat/leave 的路径、body、CSRF、headers、开始/完成顺序和成员响应。
2. 写首组真实用例：技术/商务固定 testid、首次 heartbeat、精确 body 与安全成员文案；此时生产文件不变，应因 UI/请求缺失真实失败。
3. 补生命周期红测：StrictMode 稳定窗口、15 秒续租、hidden/visible、pagehide、A→B 迟到隔离；不得以源码或初值计数替代真实事件和请求。
4. 补安全红测：disabled/非 bid_writer 零请求、坏响应整包拒绝、clientId/secret marker 零出口。
5. 串行运行聚焦测试并通过 status 报告 failed/passed、首个业务失败、四生产哈希和新测试哈希；生产实现前不得改其它文件。

## 4. 任务二：共享 API 与严格 parser

**文件**：新建 `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts`

1. 写四字段文件头注释：模块、用途、对接、二次开发。
2. 延迟生成文档级 `crypto.randomUUID()` clientId；失败返回 null，禁止弱随机或任何持久化。
3. 定义最小 `ProjectPresenceMember` 与 heartbeat 快照类型，不导出 lease/client/user 内部字段。
4. 实现精确对象键校验、最多 50 成员、唯一自身和与后端一致的用户名安全文本门；任何坏值返回 null。
5. 实现 heartbeat/leave 函数：路径编码、精确 JSON body、`apiFetch`、leave `keepalive`；不展示或记录原始错误。
6. 实现模块级串行队列 helper，使 heartbeat/leave 按入队顺序执行且单次失败不毒化后续队列。

## 5. 任务三：共享生命周期与展示组件

**文件**：新建 `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx`

1. 写四字段文件头注释，并从 `useAuthSession` 计算显式 eligible 门。
2. state 必须绑定 `projectId`；渲染时 ID 不匹配同步视为空，避免 effect 后置清理造成 A→B 首帧泄漏。
3. 用可取消的零延迟首次调度吸收 React StrictMode effect 探测；不得首屏双 heartbeat/leave。
4. heartbeat 成功后才按严格 15 秒安排下一次；同一 generation、当前可见、当前项目才可写 UI 或续排。
5. hidden 时清 UI、取消 timer、串行 leave；visible 时新 generation 立即 heartbeat。项目切换和卸载同样作废旧结果并 leave。
6. `pagehide` 发 best-effort keepalive leave；不得阻止导航、弹窗或 console。
7. 只渲染固定加载/失败/成员/truncated 文案；用户名仅文本节点，自身后缀“（我）”，全文不得出现“在线/实时/正在编辑”。

## 6. 任务四：技术标与商务标薄挂载

**文件**：

- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

步骤：

1. 更新四字段文件头的 P13-F2 对接与禁止语义。
2. 导入共享 `ProjectPresencePanel`。
3. 在各自标题区、`EditorStateVersionFreshness` 后各挂一次，传当前路由 `projectId` 与固定 testid。
4. 不把 presence 状态传入 editor Hook，不改变加载失败、保存、冲突、任务、step 路由或现有标题元信息。

## 7. 任务五：红绿收口与反假绿

1. 先运行 failure-first 用例，确认生产实现后转绿；逐项检查真实网络命中，不通过放宽断言消红。
2. 用 Playwright clock 或等价可观察时间推进验证成功完成后 15 秒续租；证明慢 heartbeat 不并发。
3. 用可控 visibility getter/事件与 `pagehide` 真实触发生命周期；证明 hidden leave、visible heartbeat、A→B 顺序和迟到零污染。
4. 对 clientId 与 secret marker 检查 DOM、HTML、URL、local/session storage、IndexedDB、Cookie、console、剪贴板探针和外网命中。
5. 最终记录五文件 SHA-256、测试数字、未运行项和所有风险。

## 8. Grok 串行自测门

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

红绿循环可先用 `--grep "P13-F2"`，但 review_request 前必须串行跑完整新专项和完整 freshness 受影响回归。禁止整仓 E2E、后端 pytest、并发 Playwright 或伪造未运行结果。

## 9. Codex 独立审查与验收

1. 核对严格四生产加一新 E2E 白名单、开工哈希、空暂存区；无后端/api/auth/router/editor Hook/CSS/依赖扩围。
2. 逐路径审查 clientId 生成与零出口、eligible 门、CSRF、Promise 串行、StrictMode、visibility、pagehide、A→B generation 和定时器清理。
3. 审查严格 parser 确实拒绝 extra/缺键/坏类型/坏用户名/多 self，错误固定且成员只作文本。
4. 审查 E2E 真实走页面与路由，稳定窗口、timer、visibility、迟到 gate、存储/console/外网泄漏门不是恒真。
5. 独立串行运行新专项、必要的 freshness 代表回归、lint 与 diff-check；build 若 Grok 已通过且生产未返修可不机械重复，不跑整仓 318 E2E 或后端全量。
6. 发现疑似问题先发送只读 review；只有 Codex/Grok 双方确认存在，才另发精确返修 task。分歧时保持代码原状补证据。

## 10. 提交与闭环

1. 先提交并推送契约、计划、路线图、主交接、在途交接和联调清单的中文冻结提交。
2. 冻结提交后，通过消息箱下发 Grok 单一 failure-first task；Grok 只实现/自测并发送 review_request。
3. Codex 独立审查、双确认返修（如有）、验收后，精确暂存五代码/测试文件，以中文功能提交推送。
4. 写回真实测试、消息 ID、最终哈希、未运行项与风险，单独中文文档闭环提交推送。
5. 完成后工作区为空，本地 HEAD、远端引用和 GitHub 实际分支一致；后续协作能力重新冻结，不沿用本包白名单。
