# P13-E 活动工作空间切换与成员只读可见性实施计划

> 契约：`docs/p13e-active-workspace-switch-member-visibility-contract.md`
> 协作：Grok 负责受限实现与自测；Codex 负责规划、范围冻结、审查、独立验收、中文文档闭环和 Git
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 测试：pytest 串行；Playwright 固定 `--workers=1 --retries=0`；禁止并发或机械全量

## 1. 基线与冻结哈希

- 开工基线：P13-D2 实现=`44c9196`、文档闭环=`986ace2`；本地 HEAD 与远端一致、工作区干净。
- 后端活动空间切换、成员只读列表及 owner 权限已由 P10A 交付，本包严格前端六生产文件加一 E2E 文件。
- Grok 不得写文档、暂存、提交或推送；任何扩围先提交证据并等待 Codex 授权。

| 文件 | 开工 SHA-256 |
|---|---|
| `frontend/src/features/auth/types.ts` | `ACAB112031161AE70B9AAD79D16FF35108F72C1257FF6E7A5E6574D4A17B26BC` |
| `frontend/src/features/auth/hooks/useAuthSession.ts` | `7734912401D226F538E98E8F9C16B6CA239FEA0EA3D12C49CF04D238F9828B4A` |
| `frontend/src/app/layout/AppShell.tsx` | `E9C96FA7F7B6FA18A09729E9ADCFF7911C22969B1FAF6648C02ED17C98B1B07A` |
| `frontend/src/app/layout/AppShell.css` | `58110618B10804552B2FAF55F43A0DA128D254DE6B353F522AF44896C6D40350` |
| `frontend/src/features/settings/pages/SettingsPage.tsx` | `C5BA962EE43AE53EAE9A525E29B9162621D6D50202D59ABE778B2F9CD23361F5` |
| `frontend/src/features/settings/pages/Settings.css` | `D2ED3471789F1B4E1694AF30AB63C7D3EFD43748C35D6041A8F85920C813FC0A` |
| `frontend/e2e/auth-rbac.spec.ts` | `FF1084583B9BB9994F4E6BBC535382AE7F79EA38E232EF089C65FDBE58280EBB` |

## 2. 任务一：E2E failure-first

1. 扩展 required 路由桩为可变双空间会话，精确记录 `/auth/active-workspace`、`/auth/me` 对账与 `/auth/members` 请求；其它 API 继续同源阻断。
2. 先写聚焦用例证明全局选择器、成功角色落点、失败对账、成员显式加载和 required 设置真值尚不存在。
3. 在生产六文件哈希未变时串行运行聚焦用例，记录真实 failed/passed 数与首个业务失败。
4. 测试不得用未触发事件、恒真数组、源码字符串或只验证 option 存在代替请求、重载、权限与泄漏行为。

## 3. 任务二：认证上下文切换状态机

**文件**：`types.ts`、`useAuthSession.ts`

1. 补齐脱敏 `AuthMember` 类型；不加入口令、Cookie、Token、审计或在线状态。
2. 上下文增加单飞 `switchWorkspace` 与在途状态；目标必须来自当前 `me.workspaces` 且不等于当前空间。
3. 精确 PUT `{workspaceId}`，严格校验返回用户、目标活动空间和角色，再接受脱敏 me；不得用客户端选项伪造服务端成功。
4. 失败统一调用可返回对账结果的 `refresh`：目标已生效则返回成功，否则保持服务端确认后的空间并抛固定错误；对账失败进入既有保守非业务态。
5. 登出/会话刷新与迟到切换用操作代次隔离，旧 finally 不得清除新在途状态。

## 4. 任务三：全局选择器与整页落点

**文件**：`AppShell.tsx`、`AppShell.css`

1. 在当前用户/工作空间区域增加可访问、可键盘操作的选择器；单空间无 PUT，多空间切换期间禁用。
2. 固定显示切换中与失败文案，不回显服务端错误、路径或 ID。
3. 成功或失败对账确认目标已生效后，按目标角色执行同源整页导航；禁止 SPA 保留旧页面状态。
4. 保持既有导航、健康探针、退出、移动端侧栏与 disabled 壳行为；不修改遗留 `TopBar/Sidebar`。

## 5. 任务四：设置真值与成员只读列表

**文件**：`SettingsPage.tsx`、`Settings.css`

1. required 模式从 `useAuthSession` 展示真实活动空间名称、ID、角色和所有者；disabled 明确为个人版默认空间。
2. 仅 required 所有者显示显式加载按钮；单击后单飞 GET `/auth/members`，严格校验整批响应。
3. 表格/列表只渲染用户名、中文角色、所有者和启用状态；不得渲染 userId、时间、内部 ID 或“在线”文案。
4. 加载、空态、失败和显式重试均有固定中文可访问状态；无自动读取、自动重试、轮询、定时器或持久化。

## 6. Grok 自测门

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e\auth-rbac.spec.ts --workers=1 --retries=0
npm run lint
npm run build

cd ..\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_auth_rbac.py::test_active_workspace_switch_and_non_member_rejected tests\test_auth_rbac.py::test_owner_member_crud_list_role_toggle_and_delete tests\test_auth_rbac.py::test_non_owner_members_api_forbidden --tb=short

cd ..
git diff --check
git status --short
```

若全文件 E2E 过慢，可先以 `--grep "P13-E"` 做红绿循环，但 review_request 前必须串行跑完整 `auth-rbac.spec.ts`。不得并行 pytest 或 Playwright，不得宣称未运行套件通过。

## 7. Codex 独立审查与验收

1. 先核对严格六生产加一测试白名单和开工哈希，无后端、router、api、依赖或配置扩围。
2. 逐路径审查当前目标验证、单飞、CSRF、失败对账、操作代次、角色落点和硬重载；证明不会出现界面空间与服务端会话空间分叉。
3. 审查成员只在 owner 显式加载、坏响应整批拒绝、userId 无出口、disabled/非 owner 零请求；确认 `isActive` 未被写成在线状态。
4. 审查 E2E 请求计数真实到达、重载确实发生、旧页面确实卸载、存储/URL/console/外网断言覆盖目标值，不接受恒真泄漏门。
5. 独立串行运行 P13-E 聚焦与完整 auth-rbac E2E、lint、`git diff --check`；build 若 Grok 已通过且生产未返修可不机械重复。按信号选择三个后端既有节点，不运行后端全量或整仓 E2E。
6. 审查发现疑似问题时，Codex 先下发只读确认消息，给出位置、行为、风险与最小范围；该消息不是返修授权，Grok 不得据此改文件或做 Git 写操作。
7. 仅当 Codex 与 Grok 都明确确认问题存在后，Codex 才另发精确返修白名单与验收命令；若结论不一致，保持代码不动并补只读证据，仍无共识则交用户裁定。
8. 若双确认前误触文件，立即中止并冻结现场，不继续、不清理、不提交；先补齐双方确认，再由新的独立返修任务接管。发现、确认、返修与 review_request 消息 ID 都写入闭环文档。

## 8. 提交与闭环

1. 先把契约、计划、路线图、主交接、联调清单作为中文冻结提交推送协作分支。
2. Grok review_request 后，Codex 独立验收并精确暂存通过的六生产加一测试文件，以中文功能提交推送。
3. 将真实 failure-first、Grok/Codex 命令与结果、消息 ID、最终 SHA-256、未运行套件和遗留风险写回五份文档，单独中文闭环提交推送。
4. 每次提交前核对分支、暂存白名单与 `git diff --check`；完成后核对空工作区、本地 HEAD 与远端一致。

## 9. 完成记录

- 文档冻结=`19f0bfe`，功能实现=`5685441`，均已推送 `collab/grok-code-codex-review`；代码提交严格六生产加一 E2E。
- Failure-first 在生产哈希冻结时为 **14 failed / 2 passed**；Grok 最终 P13-E/完整认证 **25/36 passed**，lint/build/diff-check 通过。
- Codex 独立 P13-E/完整认证 **25/36 passed**，lint/diff-check 通过；此前三个既有后端节点 **3 passed**，最终未改后端所以未重复。未运行后端全量或整仓 E2E。
- 第二轮发现先经只读双确认 `msg_c1e71b76f13c418f99d6f73fbf778b77`/`msg_e6f7094596fc4d3db79661611b217f10`，再以新任务 `msg_f3914a680ccf4b9fbf3b3a099fb3f3cb` 修复；review_request=`msg_1bfe78d7492e476d9b7187ad847dbdbd`，Codex result=`msg_1ab08b68c9e74278ad7b17e537633321`。
- 已清理测试产物，暂存区按提交边界核对；既有 Settings `get_or_create` 并发 UNIQUE 日志噪声另行立项，不在 P13-E 静默修复。
