# P13-G2 项目章节编辑意图前端提示实施计划

> 执行要求：Grok 必须使用 `executing-plans` 工作流按任务逐项执行，先红后绿
> 目标：把 P13-G1 advisory chapter intent lease 接入技术标正文编辑步，不冒充强制锁
> 架构：端点专用严格 API + 独立串行生命周期面板 + 技术标 content 薄挂载 + 新 Playwright E2E
> 技术栈：React、TypeScript、Vite、Playwright、现有内存 Auth/CSRF 与 P13-F2 文档 clientId
> 审计基线：`6dac9da11ace946a06bba6e2588fccd6303e1e83`
> 契约：`docs/p13g2-project-chapter-edit-intent-frontend-contract.md`
> 工作区：固定当前协作分支与同一 worktree；该项覆盖通用 plan skill 的新 worktree 建议

## 1. 冻结哈希与白名单

| 文件 | 审计基线 SHA-256 / 状态 |
|---|---|
| `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx` | `B0904371AFA5587FAAA0ACF0814898A067091CE3EEEF4C6489CC82C89CAA86AB` |
| `frontend/src/features/editor-state-collaboration/chapterEditIntentApi.ts` | 不存在 |
| `frontend/src/features/editor-state-collaboration/ChapterEditIntentPanel.tsx` | 不存在 |
| `frontend/e2e/project-chapter-edit-intent.spec.ts` | 不存在 |

只读依赖必须保持哈希不变：

| 文件 | SHA-256 |
|---|---|
| `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts` | `0899D383135AFBA206B492DE5064BB304BA892AC2E8986BEBFCB07530427BCFB` |
| `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx` | `E9452B3AA830FCB96ABC263B7C4DC32FC18723E37B738F723E2F7F6F0E33CF18` |
| `frontend/e2e/project-presence.spec.ts` | `67C554ECAB2B4894DB235A8416B5E36E62F384F11E4701F5CAC74610C3AB0802` |
| `frontend/e2e/technical-editor-state-truth.spec.ts` | `996FEB2A711212C7312758219B67C03A020A8D581A60392D5B84EA27D00E0820` |

Grok 不得写文档、Git、测试产物清理或白名单外文件。

## 2. 任务一：新 E2E failure-first

只新建 `frontend/e2e/project-chapter-edit-intent.spec.ts`：

1. 建立 required strict bid_writer、真实技术标 content/editor-state、P13-F2 presence 和 P13-G1 chapter-edit-lease 的独立 route probe。
2. 先覆盖首跳、冲突不阻断编辑、章节切换、hidden/pagehide、资格零请求和 parser/隐私最小矩阵。
3. 使用真实页面与组件，不读取源码、不用 route stub 直接改 UI。
4. 串行运行新 spec，记录 failed/passed、首个业务失败与三个生产文件不存在/哈希真值。
5. failure-first 阶段不得创建两个生产新文件或修改页面。

## 3. 任务二：端点专用 API

新建 `chapterEditIntentApi.ts`：

1. 从 P13-F2 只读复用 `getOrCreatePresenceClientId()`；禁止另生成 UUID。
2. 用 `getApiBase()`、`getCsrfToken()` 与 `fetch` 构造同源 POST；clientId/chapterId 精确两键。
3. 实现独立 Promise 写队列，失败后后续仍执行。
4. 严格解析 200 精确两键；严格解析 409 顶层/detail 键、固定 code/message 和安全用户名。
5. 其它 HTTP、网络、解析异常固定 unavailable；leave 只认 204，可选 keepalive。
6. 禁止 console、存储、Cookie、sendBeacon、外网与错误原文。

## 4. 任务三：生命周期面板

新建 `ChapterEditIntentPanel.tsx`：

1. 私有校验 required authenticated strict bid_writer + projectId/chapterId。
2. effect 与 UI 双重绑定项目/章节，generation 隔离所有迟到分支。
3. 零延迟首跳吸收 StrictMode；heartbeat 完成后 15 秒续租，禁止 setInterval/并发。
4. 章节/项目切换和卸载排队 leave；hidden 清空并 leave；visible 首跳；pagehide keepalive。
5. 显示固定四类文案；holder 只作 React 文本；不禁用任何编辑能力。
6. 初始 hidden 与坏 clientId/缺 CSRF 均零写。

## 5. 任务四：技术标薄挂载

只修改 `TechnicalPlanWorkspace.tsx`：

1. 更新文件顶四字段对接说明并导入新面板。
2. 仅在既有 `active === "content"` 分支、工具栏之后和 `ChapterEditor` 之前挂载。
3. 只传 `projectId` 与 `editors.selectedChapterId`；空章节由面板零请求/零 UI。
4. 不改 hook、ChapterEditor、autosave、任务、按钮、CSS 或其它步骤。

## 6. 任务五：红绿补齐与反假绿

只补新 E2E：

1. 精确 200/409 shape、安全用户名边界、extra/缺键/坏类型固定 unavailable。
2. presence 与 chapter lease 使用同一真实 UUID；UUID 仅允许在两类精确 body。
3. conflict 后真实输入正文并观察既有 PUT，证明非强制锁。
4. 慢请求 gate 证明单在途；fake timer 证明完成后 15 秒才续租。
5. A heartbeat 在途时切 B，精确证明完成 A→leave A→heartbeat B，A 迟到不污染。
6. content→facts、项目切换、章节删除后有效选择变化、hidden/visible/pagehide。
7. disabled/非 writer/非 content/无章节/坏 UUID/缺 CSRF 零请求。
8. 原始 console、DOM/HTML/text/属性/URL/history/storage/IDB/clipboard/download/外网隐私门。

不得使用宽 `status in`、`or true`、未 await 的 poll、空集合、仅计数非零或过滤掉真实敏感 console 行。

## 7. Grok 串行自测门

```powershell
cd C:\Users\Administrator\biaoshu\frontend

npx playwright test e2e/project-chapter-edit-intent.spec.ts --workers=1 --retries=0
npx playwright test e2e/project-presence.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-version-freshness.spec.ts --workers=1 --retries=0
# 只在新面板导致技术标真值风险时运行必要 technical-editor-state-truth 代表节点
npm run lint
npm run build

cd ..
git diff --check
git diff --cached --name-only
git status --short
```

禁止并发 Playwright、整仓 318 E2E、后端 pytest 或前端无关 spec。运行后只报告真实数字，不把历史基线冒充本包结果。

## 8. Codex 独立审查与提交

1. 核对严格四文件、三新文件 failure-first、不改 P13-F2/共享 API/hook/CSS。
2. 审查 200/409 严格 parser、同一 clientId、CSRF/同源、零原文和 no storage。
3. 审查 StrictMode、完成后 15 秒、单在途、A→B 顺序、hidden/pagehide 与迟到隔离。
4. 审查 UI 只作提示，conflict/unavailable 下编辑与 PUT 继续工作。
5. 审查 E2E 真实施压，排除恒真、宽状态、过滤敏感行与空集合假绿。
6. 疑似问题先走只读双确认；确认后才授权返修。
7. 独立串行运行新专项和必要直接回归，lint/diff/白名单/哈希通过后中文提交推送并文档闭环。
