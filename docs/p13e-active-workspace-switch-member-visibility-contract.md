# P13-E 活动工作空间切换与成员只读可见性契约

> 状态：已实现、独立验收并推送
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 协作：Grok 负责受限实现与自测；Codex 负责规划、范围冻结、审查、独立验收、中文文档闭环和 Git

## 1. 目标

复用 P10A 已存在的活动工作空间与成员授权接口，补齐前端真实入口：

1. required 模式登录用户可在全局业务壳查看本人全部启用成员空间，并切换当前会话活动工作空间。
2. 切换后角色、所有者权限、导航和业务请求必须全部来自新活动空间，旧空间页面内存不得继续使用。
3. 当前活动空间所有者可在设置页显式加载脱敏成员列表；本包只读，不开放新增、改角色、启停、删除或密码管理。
4. 设置页不再用“我的工作空间（后端）/`ws_local`”冒充 required 模式真实空间。

## 2. 既有后端真值

- `PUT /api/auth/active-workspace` 已校验当前用户的启用成员关系，更新当前 Cookie 会话的 `activeWorkspaceId`，并返回 `AuthMeOut`；非成员固定 403。
- `GET /api/auth/members` 已由 `require_owner` 限定为当前活动空间所有者，返回含停用成员的脱敏列表；非所有者固定 403。
- `GET /api/auth/me` 已返回可访问空间的 `id/name/role/isOwner`、当前 `activeWorkspaceId` 与脱敏用户。
- 本包不修改后端 Schema、服务、路由、模型、迁移、审计或权限，不把成员列表放宽给非所有者。

## 3. 活动空间选择器

1. 选择器放在权威 `AppShell`，只在 `phase=authenticated` 且 `activeWorkspaceId` 精确命中唯一 `activeMembership` 时显示；disabled、加载、握手失败、未登录或活动真值非法时不显示、不发切换请求。
2. 单空间仍显示当前真实空间但不可产生切换请求；多空间用可访问名称展示，选中值严格等于 `activeWorkspaceId`。
3. 用户选择当前值、空值或不在当前 `me.workspaces` 的值时零请求；不得信任 DOM 注入的任意 workspace ID。
4. 合法切换精确一次 `PUT /api/auth/active-workspace`，JSON 仅 `{ "workspaceId": "目标空间" }`；CSRF 继续由统一 `apiFetch` 内存令牌注入，不加查询参数、`X-Workspace-Id`、Cookie 读取或外部主机。
5. 切换期间选择器禁用并显示固定中文状态；同一会话只允许一个在途切换，快速重复操作不得产生第二个 PUT。

## 4. 成功、失败与对账

1. 2xx 响应必须仍为同一用户，`activeWorkspaceId` 精确等于请求目标，且目标空间在响应 `workspaces` 中、角色为四种既有角色之一；否则不得把坏响应直接写入可用业务态。
2. 合法成功后按目标空间角色整页导航：`bid_writer → /create`、`finance → /finance`、`hr → /hr`、`bidder → /bidder`。整页重载用于清空旧项目、表单、任务、列表、Hook 和请求缓存，并由新一轮 `/auth/me` 重新建立真值。
3. HTTP、网络、解析或响应不一致均显示固定中文错误，不回显 detail、code、URL、workspace ID 或响应原文。
4. 因网络中断可能发生“服务端已提交、客户端未收到”，任何失败都必须调用既有 `refresh` 读取 `/auth/me` 对账：
   - 若对账确认目标已成为活动空间，按成功路径整页导航；
   - 若仍是原空间，保留原 UI 与路由并允许重试；
   - 若对账也失败，沿用现有保守认证态，不得继续渲染未经确认的可写业务壳。
5. 对账、迟到 success/catch/finally 必须受当前切换操作约束；登出、会话丢失或后续切换不得被旧请求覆盖或解锁。

## 5. 成员只读可见性

1. 入口位于设置页工作空间区，仅 `phase=authenticated && activeMembership.isOwner` 显示“加载成员列表”；disabled 不显示成员列表、不请求 `/auth/members`。
2. 必须由用户显式点击才发一次 `GET /api/auth/members`；加载中按钮禁用，重复点击不并发。允许失败后由用户显式重试，禁止自动重试、轮询或定时刷新。
3. 响应须运行时严格校验为数组；每项只接受 `userId/username/role/isOwner/isActive/createdAt/updatedAt` 的既有脱敏形状。坏数组或坏成员整批失败，不展示半真半假的结果。
4. UI 只展示用户名、角色中文标签、所有者标记和启用/停用状态；`userId` 仅可在内存中用于行稳定性，不进入可见文本、属性、title、URL、存储、日志、剪贴板或外网。
5. 成员加载失败只显示固定中文错误；切换空间依靠整页重载清空旧列表，不得把 A 空间成员带到 B 空间。

## 6. 设置页真实性

- required 模式当前空间名称、ID、角色和所有者状态均来自 `activeMembership`，不得根据设置 API 来源猜测空间。
- disabled 模式继续明确显示个人版默认空间；不得调用认证成员接口或伪造多人能力。
- 原有模型 Key、解析策略、背景和导出模板行为不在本包重构；既有本机设置回退也不得被描述成成员/会话真值。

## 7. 安全与数据边界

- 活动空间和成员数据只保存在 React 内存；禁止新增 localStorage、sessionStorage、IndexedDB、URL 参数、模块全局缓存、Cookie 读取或持久化。
- 禁止通过选择器给业务请求新增 `X-Workspace-Id`；切换后的业务空间只认服务端 Cookie 会话活动空间。
- 禁止 console、下载、剪贴板或外网传播用户名、用户 ID、空间 ID、CSRF、Cookie 或错误原文。
- 不把“当前空间成员”宣传为在线成员；`isActive` 仅表示账号/成员关系启用状态，不代表 presence。

## 8. 双确认返修门

1. Codex 审查发现疑似问题后，必须先向 Grok 下发**只读独立确认**，附文件/代码位置、可观察行为或复现证据、风险与建议最小范围；该消息不是修复授权。
2. Grok 必须独立明确回复“确认存在”或“否认存在”及理由。除非 Codex 另行允许定点只读复现，否则确认阶段不得修改、清理、暂存文件，不得运行会写产物的测试或执行 Git 写操作。
3. 只有 Codex 与 Grok 都明确确认问题存在后，Codex 才可另发一条独立修复任务，冻结精确生产/测试白名单和验收命令；在修复任务送达前，任何一方不得继续改代码。
4. 双方结论不一致时保持代码原状，继续补充只读证据；仍无法统一时交由用户裁定，不得静默修复或用新改动反证旧问题。
5. 若确认完成前已意外产生局部改动，立即停止并冻结现场：不得继续、提交、推送、清理或声称返修完成；先补齐双确认，再由新的修复授权决定继续完成或恢复。
6. 问题发现、只读确认、双方裁定、修复授权和返修 review_request 的消息 ID 必须写入契约/交接闭环，保证“确认”与“修复”是两次可审计动作。

本轮第二次审查的只读确认请求=`msg_c1e71b76f13c418f99d6f73fbf778b77`，Grok 确认=`msg_e6f7094596fc4d3db79661611b217f10`；活动空间真值回退/重复 ID 与 `expectCountStable` 伪稳定两项均由双方确认。此前提前启动的返修 `msg_c5e4959b6e9b4276b6ed0f15334ef865` 已中止；双确认后的新修复授权=`msg_f3914a680ccf4b9fbf3b3a099fb3f3cb`，返修 review_request=`msg_1bfe78d7492e476d9b7187ad847dbdbd`，Codex 验收回执=`msg_1ab08b68c9e74278ad7b17e537633321`。确认与修复已按两次独立动作闭环。

## 9. 严格修改范围

生产文件仅六个：

- `frontend/src/features/auth/types.ts`
- `frontend/src/features/auth/hooks/useAuthSession.ts`
- `frontend/src/app/layout/AppShell.tsx`
- `frontend/src/app/layout/AppShell.css`
- `frontend/src/features/settings/pages/SettingsPage.tsx`
- `frontend/src/features/settings/pages/Settings.css`

测试文件仅一个：

- `frontend/e2e/auth-rbac.spec.ts`

禁止修改后端、路由表、统一 HTTP 客户端、依赖、构建配置、其它页面或测试。若确有必要扩围，Grok 必须先停下，提交真实失败、必要性和最小文件名，等待 Codex 明确授权。

## 10. failure-first 与验收

1. 六个生产文件哈希不变时，先扩展 `auth-rbac.spec.ts` 路由桩和行为用例，记录选择器缺失、成员入口缺失或设置假值导致的真实 E2E red；禁止源码字符串、签名或恒真断言冒充失败。
2. 切换至少覆盖：真实双空间、四角色落点、精确请求体与 CSRF、零 `X-Workspace-Id`、同值/非法值零请求、单飞、成功整页重载、失败对账、对账后已切换、坏响应、角色/所有者导航变化和旧空间页面卸载。
3. 成员至少覆盖：所有者显式单次 GET、加载/重试、含停用成员、角色与所有者标签、非所有者/disabled 零请求、坏响应整批失败、用户 ID 不进入 DOM/存储/URL/console/外网。
4. 设置页覆盖 required 真实名称/ID/角色/所有者与 disabled 个人版文案，不再由 `source` 伪造 workspace。
5. Grok 只串行运行 P13-E 聚焦 E2E、既有 auth-rbac 全文件、lint/build、`git diff --check`，并可串行运行后端三个既有定点用例证明复用接口未漂移；Playwright 固定 `--workers=1 --retries=0`。
6. Codex 独立审查单飞、失败对账、整页重载、权限收敛、敏感数据出口和 E2E 反假绿；按证据选择定点回归，不机械运行后端全量或整仓 E2E。

## 11. 实现与验收闭环

- 文档冻结=`19f0bfe`，功能实现=`5685441`；严格六个生产文件加一个 E2E 文件，无后端、router、统一 HTTP 客户端、依赖或配置扩围。
- 真实 failure-first：生产文件哈希冻结时 P13-E 聚焦 **14 failed / 2 passed**，首个业务失败为缺少工作空间选择器；状态消息=`msg_8a89abb09ed1464f9dfe820c32888ee3`。
- Grok 最终串行结果：P13-E **25 passed**、完整 `auth-rbac.spec.ts` **36 passed**、lint/build/diff-check 通过；Playwright 固定单 worker、零重试。
- Codex 独立串行结果：P13-E **25 passed（40.5 秒）**、完整认证/RBAC **36 passed（50.7 秒）**、lint 与 diff-check 通过；首轮已独立通过三个既有后端定点 **3 passed**，最终生产未改后端，未机械重复。
- 最终 SHA-256：`types.ts=5B8DEFE16EF69A39BC88399B6BA3C08D30D35EE0C0118F3D69BBCCC59422A5CA`、`useAuthSession.ts=C656C86F4ABEE013AED561F21349A30DB41B408287BA784E2761448ABD3F7FF0`、`AppShell.tsx=AD2F91E89FD5D8E03CD10B3DA3231BC656869965264EC820C3B203702C964984`、`AppShell.css=7516F7C0E27C3C9EA5E9EF7734B09DB090D575B00E41D3033745043CBC6D3584`、`SettingsPage.tsx=D9D90789EAD3BA093C7421AAA136FEA98190DD412BBCB5650EFEFA64914416F5`、`Settings.css=2B538955D347276B8F3EACEA6F594186F1466FED2A5F652B84B8D297EBBA799E`、`auth-rbac.spec.ts=74E5A63ABC2C8BA614D4A9470EF0E25C25CFBF9FFAA096CA15837C2E8AD6E5D9`。
- 未运行后端全量 pytest 或整仓 E2E。完整认证 E2E 再现既有 disabled 设置页 `workspace_settings.workspace_id` 并发 `UNIQUE` 日志噪声，但 36 项仍全部通过；该缺陷不在本包白名单，后续须按双确认门独立立项。

## 12. 明确不做

- 不做成员新增、邀请、改角色、所有者转移、启停、删除、重置密码或自助入组 UI。
- 不向非所有者开放成员列表，不增加跨工作区成员目录、搜索、分页、导出或历史审计。
- 不做 presence、在线/离线、心跳、最后活跃时间、协同光标、章节锁/租约、评论、审批或通知。
- 不做 SSE/WebSocket 事件广播、游标重放、多任务总线或断线恢复。
- 不修改业务 API 的活动空间解析，不新增表、列、索引、迁移、依赖、轮询或定时器。
