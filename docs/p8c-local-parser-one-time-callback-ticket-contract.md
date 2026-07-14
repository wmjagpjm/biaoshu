<!--
模块：P8C 本地解析一次性回传票据契约
用途：冻结 required 模式下外部本地解析助手回传 Markdown 的短期、单项目、单次授权边界。
对接：parse_callback；auth_middleware；P8/P8B 解析调度；docs/plans/2026-07-14-p8c-local-parser-one-time-callback-ticket-plan.md。
二次开发：票据不是登录会话或长期 API Key；不得安装/启动 MinerU、Docling，不得把票据写入日志、URL、数据库明文或浏览器存储。
-->

# P8C 本地解析一次性回传票据契约

> **状态**：**已完成、独立验收并推送**；计划=`cabe99d`，后端=`af39ff8`，前端=`1cf5576`。
> **工作分支**：`collab/grok-code-codex-review`。
> **当前验收基线**：后端串行全量 432 passed；前端 lint/build 通过、单 worker 串行全量 E2E 131 passed。

## 1. 现状与方案选择

现有 `POST /api/projects/{projectId}/parse-callback` 在个人兼容模式可直接回传，在 `AUTH_MODE=required` 下则受会话、CSRF 和 strict `bid_writer` 工作空间解析约束。浏览器内手工粘贴可以复用会话，但外部 MinerU/解析助手若只持有 `X-Local-Token`，仍会先被认证中间件拦截；把浏览器 Cookie/CSRF 交给外部进程又会扩大为完整高权限会话。

本包比较并拒绝两种方案：

1. 继续使用长期静态 `X-Local-Token`：不绑定项目/工作空间，不能单次使用，泄露影响面过大；
2. 让外部助手携带浏览器会话与 CSRF：无需新表，但外部进程会获得超出解析回传所需的权限。

最终选择一次性回传票据：strict `bid_writer` 在已登录浏览器中为单一当前空间项目签发 256 位随机票据，固定 10 分钟过期、成功消费一次；服务端只保存 SHA-256 摘要。外部助手只向一个精确公开回调路径提交票据和受限 Markdown，不获得 Cookie、CSRF、工作空间选择权或其他 API 权限。

## 2. 签发权限与响应

唯一签发接口：`POST /api/projects/{projectId}/parse-callback-ticket`。

- 仅 `AUTH_MODE=required` 且当前活动成员角色精确为 `bid_writer` 可签发；复用 `require_strict_bid_writer`，所有者身份不隐式绕过；
- 写请求继续由既有认证中间件校验会话和 CSRF；无请求体、查询参数或客户端 TTL；
- 项目必须属于当前活动工作空间；跨空间、已删除或不存在项目统一固定 404，不泄漏项目归属；技术标和商务标均允许，因为 P8B 两条生产链都支持 local 策略；
- 原始票据由 `secrets.token_urlsafe(32)` 生成，数据库只保存 `sha256(raw_ticket)` 十六进制摘要；禁止保存、审计、日志输出或再次读取原文；
- 固定有效期 10 分钟，以服务端 UTC 时间计算；每张票据绑定 workspace、project 和签发 user，且只能成功消费一次；
- 成功 `201`，固定 `Cache-Control: no-store`，响应精确为 `ticket`、`expiresAt`、`callbackPath`，其中 callbackPath 固定 `/api/local-parser/callback`；
- 成功签发审计固定为 action=`local_parser_callback_ticket_issue`、result=`success`、target=`single_project_10m`，不得记录票据、摘要、项目 ID、文件名或正文。

新增表只保存：票据行 ID、摘要、workspace ID、project ID、签发 user ID、expires_at、consumed_at、created_at。项目/工作空间/用户删除后级联删除票据；摘要唯一。不得保存客户端地址、User-Agent、Cookie、CSRF、文件路径或 Markdown。

## 3. 精确公开回调

新增唯一公开接口：`POST /api/local-parser/callback`。认证中间件只对这个**精确路径**放行，不允许前缀、通配、其他方法或旧项目回调路由借此公开。

请求规则：

- 原始票据只允许放在 `X-Local-Parse-Ticket` 请求头，不允许 URL、查询参数或 JSON 字段携带；
- JSON 对象只允许 `markdown`、`source`、`filename` 三个键；`markdown` 为去首尾空白后 1–1,000,000 个 Unicode 码点；`source` 必须精确为 `mineru`；`filename` 可空，否则去首尾空白后 1–255 个码点，禁止 CR/LF、NUL、`/`、`\`；
- 原始 HTTP body 上限固定 2 MiB；超限固定 413。JSON 非对象、额外键、类型错误、非法字段统一固定 400，不得在验证错误中回显正文、文件名、票据、路径或原始输入；
- 缺失、格式错误、未知、过期、已消费、项目已删除或摘要不匹配的票据统一 `401 local_parser_ticket_invalid`，不得区分原因；
- 票据消费必须使用带 `consumed_at IS NULL` 与 `expires_at > now` 条件的原子更新，受影响行数必须严格为 1；并发或重放只能有一次成功；
- 票据消费、`parsed_markdown` 写入、成功 parse 任务创建和项目步骤更新在同一数据库事务内完成；任一写入失败须整体回滚，不能只烧毁票据或留下半成品；
- 成功响应只含 `ok`、`chars`、`taskId`，固定 `Cache-Control: no-store`；不得返回项目、工作空间、签发用户、票据、摘要、过期时间、文件路径或正文；
- 成功回调审计固定为 action=`local_parser_callback_apply`、result=`success`、target=`one_time_ticket`，actor/workspace 来自已验证票据绑定，不记录项目 ID、文件名、正文、字符数或票据。

## 4. 兼容与前端边界

现有 `/api/projects/{projectId}/parse-callback` 路径、个人兼容模式和可选 `LOCAL_PARSER_TOKEN` 语义保持不变；新公开回调绝不接受长期 `X-Local-Token` 作为票据或回退。P8/P8B 的 `light/local/ask` 决策、`parse_engines`、轻量解析、任务 payload 和现有 editor-state 读写语义均不改变。

后端验收提交后，前端 `/local-parser` 才增加“生成一次性回传票据”：

- required strict `bid_writer` 用户显式点击后才发送一次签发 POST；不在挂载、项目 ID 变化或计时器中自动签发；
- 原始票据只保存在当前组件内存，显示固定回调路径和 Windows curl 示例；禁止写入 localStorage、sessionStorage、IndexedDB、URL、剪贴板或日志；刷新/离开页面立即丢失；
- 不自动调用公开回调、不启动或探测 MinerU/Docling、不读取本地文件、不请求外网；
- disabled 个人兼容模式保留既有手工粘贴/旧回调，并明确无需一次性票据；非 bid_writer 不挂载页面且不得签发；
- 签发错误只显示固定中文，不回显服务端 detail、路径、项目 ID、票据或敏感标记；过期不做浏览器倒计时，用户需要时重新显式签发。

## 5. 明确非目标

- 不安装、下载、启动或打包 MinerU/Docling，不新增 subprocess、外部服务地址、可执行路径或依赖；
- 不把 `local`/`ask` 注册为解析 engine，不改默认 `lightweight`；
- 不实现上传原文件给外部助手、自动轮询、自动回传、回调重试、票据续期、批量项目票据或长期 API Key；
- 不开放任意公共项目路由，不让票据访问项目、文件、设置、成员、编辑态读取或其他业务 API；
- 不新增浏览器密钥存储，不把票据放入命令历史以外的仓库文件、日志、测试快照或协作消息；测试只使用固定假票据；
- 不处理持久化融合历史、Word structure、语义模型缓存、角色后续数据域或生产部署容器化。

## 6. 验收底线

后端必须覆盖签发权限矩阵、跨空间项目、精确响应字段、原文不落库、固定 10 分钟、精确公开路径、缺失/错误/过期/重放统一拒绝、原子单次消费、事务回滚、请求体/字段上限、固定错误脱敏、成功 parse 任务/editor-state/项目步骤、固定审计以及旧回调回归。前端必须覆盖显式单次签发、内存票据与固定 curl、disabled 兼容、非制作者零请求、错误脱敏、网络白名单和零浏览器存储；所有 Playwright 命令继续单 worker 串行。

## 7. 交付与验收记录

- 后端提交 `af39ff8`：新增只存 SHA-256 摘要与绑定元数据的票据表，签发端点、POST-only 精确公开回调、流式 2 MiB 正文硬上限、条件 UPDATE 单次消费，以及票据消费/解析结果/成功任务/项目步骤/审计同事务提交；旧 callback 与长期 `X-Local-Token` 兼容语义未改。
- 前端提交 `1cf5576`：required strict `bid_writer` 仅在显式点击后签发并用组件 `useState` 展示绝对固定 `curl.exe`；disabled 保留旧手工表单；其他角色继续由既有路由门禁阻断。票据不进 URL、控制台、剪贴板或任何浏览器存储。
- 后端首版因先整包读取正文后检查 2 MiB 被拒绝，返修为 ASGI 分块累计并在缺票据时先返回固定 401；测试同时收紧了跨空间、精确八字段与流式上下界断言。Codex 独立通过定向 10 项、解析/鉴权回归 51 项、串行全量 432 项，仅 1 条既有 Starlette/httpx 弃用警告。
- 前端首版因相对 curl 无法直接在 Windows 终端执行、E2E 未真实断言 body/CSRF/旧 Token 且 disabled 宽泛放行未知 API 被拒绝；后续又收紧 IndexedDB 枚举失败和未使用特殊字符验证路径编码的假绿。Codex 独立通过 P8C E2E 9 项、P8B 6 项、lint/build；第一次全量 130/131 仅既有矩阵分页用例出现一次初始化时序波动，单独复跑通过，第二次单 worker 全量 131/131 全绿。
- Grok 只负责限定实现与自测，未提交推送；最终验收回执为后端 `msg_6f934b06d32641e98fddd153fb41b0e8`、前端 `msg_5c581d747cba4ccb8a2413cc152d1851`，Git 与文档闭环均由 Codex 完成。
