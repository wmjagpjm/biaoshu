# P13-G2 项目章节编辑意图前端提示契约

> 状态：已实现、经 Codex 独立验收并推送；冻结=`3a74fbb`，实现=`86abbbf`
> 审计基线：`6dac9da11ace946a06bba6e2588fccd6303e1e83`
> 前置：P13-G1 后端=`015ab37`，P13-F2 项目近期成员前端=`dfa6bc0`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`

## 1. 目标与诚实语义

在技术标正文编辑步为当前实际显示的章节接入 P13-G1 heartbeat/leave：

1. 当前活动 workspace 的 required strict `bid_writer` 打开技术标 `content` 步时，对当前有效章节建立或续期 45 秒“近期处理意图”。
2. 本 client 持有时显示固定自身状态；其它 client 持有时只显示服务端重新校验的安全用户名。
3. 章节、项目、步骤、可见性或页面生命周期变化时精确 leave，异常离开由后端 45 秒过期兜底。
4. 冲突只作提示。不得禁用标题、正文、图片、卡片、AI 生成、revise 或 editor-state PUT，不得声称已阻止覆盖。

本包仍不是强制锁、实时协同、正在输入或在线状态。若未来需要硬锁，必须先让所有章节写入口携带并校验租约，另包设计。

## 2. 只读审计真值

1. 当前章节只存在于 `useTechnicalPlanEditors` 的 `state.chapters`；`selectedChapter` 会在原始选择为空时回退到首个 done 章节或第一章。
2. `TechnicalPlanWorkspace` 只有 `active === "content"` 时实际渲染 `ChapterEditor`；因此面板必须在该分支薄挂载，不能放全局标题区或 editor Hook。
3. P13-F2 已有文档级 `crypto.randomUUID()`、内存 clientId 和 presence 写队列。G2 只复用 `getOrCreatePresenceClientId()`，不修改 P13-F2 文件；章节租约使用自己的串行队列，允许与项目 presence 独立并行。
4. 共享 `apiFetch` 的 `ApiError` 不保留 `holderUsername`。G2 新 API 必须用 `getApiBase()`、`getCsrfToken()` 和同源 `fetch` 做端点专用严格解析，不修改共享 HTTP 客户端。
5. `ChapterEditor` 当前没有租约门，所有编辑仍走既有 hook、防抖与 CAS。G2 只在其上方增加无卡片嵌套的紧凑状态行。

## 3. 严格作用域

仅在以下条件全部成立时启用：

- `phase === "authenticated"`；
- 当前活动成员角色精确 `bid_writer`；
- `projectId` 为当前技术标路由项目；
- 页面实际处于 `content` 步；
- `editors.selectedChapterId` 为当前 `chapters` 中有效章节 ID；
- 页面 `document.visibilityState === "visible"`；
- 文档级 clientId 与内存 CSRF 均可用。

disabled、未认证、其它角色、非 content 步、空章节、初始 hidden、坏 clientId 或缺 CSRF 都必须零 chapter-edit-lease 请求。不得用 query、`X-Workspace-Id`、Cookie、存储或 URL 切换作用域。

## 4. API 与严格解析

新 API 文件只负责：

- `POST /projects/{projectId}/chapter-edit-lease/heartbeat`
- `POST /projects/{projectId}/chapter-edit-lease/leave`
- 独立模块级 Promise 串行链，失败不毒化后续写。

请求体必须精确：

```json
{"clientId":"<P13-F2 文档内存 UUID>","chapterId":"<当前章节原生 ID>"}
```

共同网络边界：

- URL 只含 `encodeURIComponent(projectId)`；chapterId/clientId 只进 JSON body。
- `Content-Type: application/json`、`credentials: same-origin`、内存 `X-CSRF-Token`。
- 禁止读取 Cookie、local/session storage、IndexedDB，禁止 sendBeacon、外网、日志或错误原文。
- leave 的 `pagehide` 调用使用 `keepalive: true`；其它 leave 为 false。

heartbeat 200 必须精确两键：`leaseExpiresAt` 为有限可解析时间字符串，`refreshAfterSeconds === 15`。解析成功返回自身持有状态。

heartbeat 409 必须顶层精确只有 `detail`，detail 精确三键：

```json
{
  "code":"chapter_edit_lease_conflict",
  "message":"此章节近期已有处理意图",
  "holderUsername":"<安全用户名>"
}
```

用户名规则与 P13-G1/P13-F2 相同：原生字符串、1..100 Unicode 码点、无首尾空白，拒绝 C0/C1/DEL、U+2028/U+2029 和双向控制。任何 extra/缺键/坏类型/坏时间/坏用户名、其它 HTTP、网络或解析失败一律固定 unavailable，禁止部分展示。

leave 只接受精确 204；其它结果静默失败，由后端 TTL 兜底。

## 5. 生命周期与串行顺序

面板 effect 绑定 `(eligible, projectId, chapterId)`，UI 也必须同步绑定项目与章节，拒绝旧状态首帧泄漏。

1. 首次 visible 使用可取消的零延迟 timer，吸收 React StrictMode 首轮 effect 探测；稳定窗口只能一个章节 heartbeat。
2. heartbeat 完成后再等待 15 秒续租；慢请求期间不得发同章节第二次 heartbeat。
3. 成功、冲突和 unavailable 都可在 15 秒后有限重试；hidden/unmount/切换后不得继续调度。
4. 章节 A→B：若 A heartbeat 在途，必须按 `A heartbeat 完成 → leave A → heartbeat B` 串行；A 迟到 success/conflict/catch/finally 均不得污染 B。
5. 项目 A→B、content→其它步骤、章节被删除后的有效选择变化使用同样代次隔离和 leave 顺序。
6. hidden 同步清空状态并排队 leave；visible 立即重新 heartbeat。初始 hidden 不生成 clientId、不 heartbeat、不 leave。
7. pagehide 对曾尝试 heartbeat 的当前项目/章节发送 keepalive leave；重复 leave 依赖后端幂等。
8. clientId 不可用时固定 unavailable 且零写；不得弱随机回退。

章节租约队列与 P13-F2 presence 队列相互独立，不需要跨能力全局串行；二者必须复用同一文档 clientId。

## 6. UI

在技术标 content 工具栏与 `ChapterEditor` 之间薄挂载 `ChapterEditIntentPanel`，固定 `data-testid="technical-chapter-edit-intent"`。不新增卡片嵌套，不改 CSS。

允许显示：

- 标题：`本章处理意图`
- 自身持有：`已记录你的近期处理意图`
- 冲突：`近期由 {holderUsername} 处理`
- 失败：`章节处理意图暂不可用`

loading/hidden 可只保留标题或不显示状态。禁止显示在线、实时、正在编辑、正在输入、独占、锁定、不可编辑、最后活跃、倒计时、lease/client/user/member/project/chapter ID、HTTP status/code/detail/URL 或异常原文。

冲突和失败状态下编辑器、已有按钮、autosave 与 CAS 必须继续可用；E2E 必须真实输入并证明既有 editor-state PUT 未被 G2 门禁。

## 7. 隐私与安全

1. clientId 仅允许出现在 P13-F2 presence 和 P13-G2 chapter-edit-lease 的精确 JSON body；不得出现在 DOM、URL、属性、title、存储、Cookie、console、剪贴板、下载或外网。
2. chapterId 可继续存在于既有 editor-state/任务业务 JSON，但 G2 不得新增 URL、DOM、日志、存储或外网出口。
3. holderUsername 只作 React 文本节点；不得进入属性、title、URL、存储、日志、剪贴板、下载或外网。
4. 非法 409 中的用户名、message、code 和任意 secret marker 都不得进入可见 UI。
5. 所有错误捕获固定收敛，不调用 `console.*`，不使用共享 `ApiError.message` 回显服务端正文。

## 8. 严格四文件白名单

新建：

- `frontend/src/features/editor-state-collaboration/chapterEditIntentApi.ts`
- `frontend/src/features/editor-state-collaboration/ChapterEditIntentPanel.tsx`
- `frontend/e2e/project-chapter-edit-intent.spec.ts`

修改：

- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`

禁止修改 P13-G1 后端、P13-F2 API/面板/E2E、共享 `api.ts`、auth、router、editor Hook、`ChapterEditor`、CSS、依赖、配置或已有测试。

## 9. failure-first 与验收

新 E2E 必须先只写测试并真实失败，至少覆盖：

1. required 技术标 content 首跳、精确路径/body/CSRF、200 自身状态；与 presence heartbeat 复用同一 clientId。
2. 安全 409 holder 展示；冲突下正文仍可编辑并触发既有 editor-state PUT。
3. 200/409 严格 parser 矩阵，坏包固定 unavailable、零原文泄漏。
4. 成功/冲突/unavailable 后 15 秒重试，慢 heartbeat 不并发。
5. A→B 真正在途顺序与迟到隔离；项目/步骤切换 leave。
6. hidden/visible/pagehide keepalive；初始 hidden 延迟 UUID 且零写。
7. disabled、非 bid_writer、非 content、无章节、clientId/CSRF 不可用零写。
8. clientId、chapterId、holderUsername 和 secret marker 的 DOM/URL/存储/console/外网零出口。

禁止源码字符串、`hasattr`、宽 status、空集合、未施压 gate、仅 route stub 计数或固定 sleep 冒充生命周期证据。Playwright 必须 `--workers=1 --retries=0`。

串行验收：新专项、P13-F2 `project-presence.spec.ts`、`editor-state-version-freshness.spec.ts`、必要的 `technical-editor-state-truth.spec.ts` 代表节点、lint、build、diff-check、严格四文件、空暂存和 SHA-256。不默认整仓 318 E2E 或后端 pytest。

## 10. 双确认返修门

Codex 发现疑似问题后先发只读 question/review；Grok 只能确认或否认并给证据。双方确认存在后，Codex 才另发独立 task 精确授权返修。确认前禁止修改、测试、清理或 Git 写。

## 11. 明确不做

- 不做强制锁、禁用编辑器、保存门禁、租约令牌写入 editor-state PUT 或旧客户端阻断。
- 不做商务标章节意图；P13-G1 只接受 technical 项目。
- 不做 GET/list、在线历史、最后活跃、倒计时、通知、评论或审批。
- 不做协同光标、选区、正在输入、正文增量同步、CRDT/OT、SSE/WebSocket、广播或游标重放。

## 12. 完成记录

真实 failure-first=`msg_b20b7dbe314943ba806fcf62f37d95c9`，结果 **8 failed / 1 passed**。两轮疑似问题均先只读双确认：`msg_9fa0bb83f0f348f99eca175567b3983d`、`msg_24da16ad88c94f7585de0a34ef88095d`；确认存在后才授权 Grok 返修。最终 Grok review=`msg_7a542b4e3d444c13800cc401141a0d90`，专项/聚焦关键序列 **13/7 passed**。

Codex 独立串行通过 P13-G2 专项/P13-F2 presence/freshness **13/11/17 passed**，lint、build、diff-check、严格四文件、八文件哈希与临时工件清理门均通过。build 只有既有 chunk 大小警告。未运行整仓 318 E2E、后端 pytest、xdist 或并发 Playwright。
