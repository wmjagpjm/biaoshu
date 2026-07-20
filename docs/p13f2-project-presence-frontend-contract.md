# P13-F2 项目近期成员前端契约

> 状态：已冻结，等待 Grok failure-first 实现与自测
> 开工基线：`66f4390999bd9da750a439bc13d0ec7627a7f9e9`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 后端真值：P13-F1 实现=`6164d8c`
> 协作：Grok 负责严格白名单内实现与自测；Codex 负责规划、审查、双确认返修门、独立验收、中文提交、文档闭环和 Git

## 1. 目标与产品语义

在技术标和商务标项目工作区接入 P13-F1 已交付的 heartbeat/leave：

1. 浏览器文档内存生成一个安全 clientId，不落盘；同一标签页在 SPA 项目切换时复用，整页刷新后重新生成。
2. 仅 required 已认证、当前活动成员角色精确为 `bid_writer`、项目 ID 非空且页面可见时，为当前项目建立短租约并按服务端固定 15 秒节奏续租。
3. 在两个项目标题区展示服务端返回的安全用户名快照和自身标记。
4. 页面隐藏、项目切换、组件卸载或 `pagehide` 时 best-effort leave；45 秒服务端过期仍是最终清理保证。

UI 只能称为“近期在此项目”或“近期成员”，不得称为在线、实时、正在编辑、正在输入、当前焦点或最后活跃。presence 只说明服务端最近收到短租约续期。

## 2. 架构选择

采用“共享 API/解析器 + 共享生命周期展示组件 + 两页面薄挂载”：

- `projectPresenceApi.ts` 只负责文档级 clientId、精确 API 请求和严格响应解析。
- `ProjectPresencePanel.tsx` 负责 eligible 门、可见性、定时器、串行 heartbeat/leave、迟到隔离和无敏感信息展示。
- 技术标、商务标工作区只传当前路由 `projectId` 与固定 testid，不把 presence 塞进两个大型 editor Hook。

不采用以下方案：

- 不修改 `EditorStateVersionFreshness` 发请求；该组件继续保持 P13-B/C/D2 的纯展示、零副作用契约。
- 不在 `useTechnicalPlanEditors` 与 `useBusinessBidWorkspace` 各复制一套定时器；这会产生双份生命周期和竞态实现。
- 不放到 `AppShell`；全局壳无法代表当前真实项目页面，也会把非项目路由误记为 presence。

## 3. 启用门与项目作用域

1. 组件从既有 `useAuthSession` 读取真值，只有 `phase === "authenticated"` 且 `activeMembership.role === "bid_writer"` 才启用。
2. `disabled`、loading、handshake_error、unauthenticated、活动成员缺失或其它角色一律隐藏且 heartbeat/leave 均为零请求。
3. 路由仍由 `RequireBusiness` 负责体验门，presence 的显式 eligible 门不能只依赖父路由；后端继续是最终授权边界。
4. 请求路径只使用当前 prop `projectId` 的 `encodeURIComponent` 结果；禁止 query、`X-Workspace-Id`、用户 ID、workspace ID 或任何外部主机。
5. 技术标和商务标只能各挂一个共享组件；同一页面不得因 step 路由变化重复建立租约。

## 4. clientId 与请求边界

1. clientId 通过浏览器 `crypto.randomUUID()` 延迟生成，36 位 UUID 满足后端 `[A-Za-z0-9_-]{22,64}`；不可用时保守禁用 presence，不得回退 `Math.random`、时间戳或设备指纹。
2. clientId 只存在当前 JavaScript 文档模块内存与 heartbeat/leave JSON body：`{"clientId":"..."}`。
3. 禁止写入 localStorage、sessionStorage、IndexedDB、Cookie、URL、DOM、React 属性、title、日志、错误文案、剪贴板、下载、埋点或外网。
4. 请求统一复用 `apiFetch`，由其注入同源 Cookie 与内存 CSRF；不得读取 Cookie 或 CSRF，不得修改统一 HTTP 客户端。
5. heartbeat 为普通同源 POST；leave 可使用 `keepalive: true`。禁止 `sendBeacon`，因为它无法按现有协议可靠携带 `X-CSRF-Token`。

## 5. 生命周期与串行化

1. 初次 eligible 且 `document.visibilityState === "visible"` 时立即 heartbeat；成功完成后等待响应中精确的 `refreshAfterSeconds=15` 再发下一次，禁止固定并发 `setInterval`。
2. 所有 heartbeat/leave 经同一文档级 Promise 链串行，单次最多一个在途 presence 写请求；失败不得打断后续链。
3. React StrictMode 首轮 effect 探测不得产生可观察的重复 heartbeat/leave 或留下错序租约；首次 heartbeat 应可取消地延迟到本轮 effect 稳定后执行。
4. 页面变 hidden 时取消下一次计时、立即清空可见成员并串行 leave；变 visible 时立即新 heartbeat，再从成功时刻计算 15 秒。
5. `projectId` 从 A 切到 B：同步隐藏 A 快照，旧 A heartbeat/错误/计时器全部失效，串行 leave A 后 heartbeat B；A 的迟到结果不得渲染到 B 或重启 A 计时器。
6. 组件卸载和 `pagehide` 执行 best-effort keepalive leave；leave 失败静默等待服务端 45 秒过期，不阻止导航、不弹窗、不重试风暴。
7. heartbeat 失败后显示固定不可用态，并在 15 秒后有限重试；禁止指数高频、并发补发或将一次失败解释为成员离线。

## 6. 严格响应与 UI

heartbeat 200 必须整包严格解析：

1. 顶层精确四键 `leaseExpiresAt/refreshAfterSeconds/members/truncated`，额外或缺失键整包拒绝。
2. `leaseExpiresAt` 仅验证为非空字符串，不展示、不落盘、不用于客户端授权或本地过期判断。
3. `refreshAfterSeconds` 必须精确整数 `15`；`truncated` 必须原生 boolean；`members` 必须为最多 50 项数组。
4. 每项精确两键 `username/isSelf`；`isSelf` 必须原生 boolean；username 必须为 1..100 Unicode 码点、无首尾空白，并拒绝 C0/C1/DEL、U+2028/U+2029 和双向控制字符。
5. 整包最多一个 `isSelf=true`，且成功 heartbeat 必须包含自身；坏项、重复自身或坏顶层均不得部分展示。

展示规则：

- 标题固定为“近期在此项目”，成员只作 React 文本节点；自身追加可见文本“（我）”。
- `truncated=true` 只显示“另有更多近期成员”，不推算或伪造总数。
- 初始为固定加载态，失败/坏响应统一“近期成员暂不可用”，不回显 status、detail、code、URL、项目 ID、clientId 或响应原文。
- 技术标固定 `data-testid="technical-project-presence"`，商务标固定 `data-testid="business-project-presence"`。

## 7. 安全与隐私边界

- 用户名只进入可见文本节点；不得进入 key 以外的可观察 DOM 属性、HTML、title、URL、存储、console、剪贴板或外网。
- 任意响应夹带 userId/memberId/leaseId/clientId/digest/role/owner/time detail/secret marker 时，因额外键整包拒绝且敏感值零出口。
- heartbeat/leave 错误不影响 editor-state 加载、保存、冲突、任务或导航；presence 是独立非阻断展示。
- 不修改后端、认证上下文、路由、统一 HTTP 客户端、editor Hook、任务 SSE 或数据库。

## 8. 严格代码白名单

生产文件两个新建、两个修改：

- 新建 `frontend/src/features/editor-state-collaboration/projectPresenceApi.ts`
- 新建 `frontend/src/features/editor-state-collaboration/ProjectPresencePanel.tsx`
- 修改 `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- 修改 `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`

测试文件一个新建：

- 新建 `frontend/e2e/project-presence.spec.ts`

禁止修改后端、`api.ts`、认证 Hook、router、现有版本展示组件、两个 editor Hook、CSS、依赖、构建配置、已有 E2E 或文档。确需扩围时 Grok 必须停止，以 status 报告真实失败、必要性和最小文件名，等待 Codex 明确授权。

## 9. failure-first 与验收

1. Grok 先只创建新 E2E，生产四文件哈希保持冻结；先证明技术/商务 testid、required 初次 heartbeat、精确 body/CSRF 和成员展示真实缺失。
2. 聚焦 E2E 必须覆盖：StrictMode 稳定窗口仅一次首跳、15 秒无重叠续租、visible/hidden、pagehide、A→B leave/heartbeat 顺序与迟到隔离、disabled/非 bid_writer 零请求。
3. 必须覆盖精确响应 parser、用户名安全门、自身标记、truncated、错误脱敏、clientId 和敏感响应字段零 DOM/存储/URL/console/外网。
4. E2E 请求计数必须来自真实路由命中；稳定断言必须观察完整窗口，不接受初值满足即通过、源码字符串、恒真集合或未触发事件。
5. Playwright 固定 `--workers=1 --retries=0` 串行；Grok 完成前运行新专项、P13-B/C/D2 freshness 受影响回归、lint、build、diff-check，不运行整仓 318 E2E 或后端全量。

## 10. 双确认返修门

Codex 发现疑似问题后先发送只读 review，Grok 只能只读确认或否认并给证据。只有双方明确确认问题存在，Codex 才另发独立 task 授权精确返修；确认消息不含修复授权。双方不一致时保持代码原状补证据，仍不能统一则交用户裁定。所有确认、授权、review_request 和 result 消息 ID 写入完成态文档。

## 11. 明确不做

- 不做 WebSocket、SSE presence、广播、事件重放、跨标签页同步或 Service Worker。
- 不做协同光标、选区、正在输入、章节锁/租约、冲突自动合并、评论、审批或通知。
- 不做在线历史、最后活跃时间、成员详情、头像、角色、owner、设备/IP 或分页搜索。
- 不持久化 clientId，不把任务 SSE heartbeat 冒充成员租约，不改变 P13-F1 后端协议。
