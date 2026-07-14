<!--
模块：P10K 财务项目成本变更记录实施计划
用途：把上线后项目级最小不可变成本事件拆成后端事务写入/只读 API 与前端显式面板两个可审查任务。
对接：docs/p10k-finance-project-cost-change-events-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：必须遵守文件白名单；Grok 只实现和自测，Codex 独立审查、验收、提交与推送。
-->

# P10K 财务项目成本变更记录实施计划

> **状态**：计划内后端、前端、独立验收与提交推送均已完成。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 432 passed；前端 lint/build 通过、单 worker 串行全量 E2E 131 passed。
> **执行顺序**：计划提交并推送 → 后端实现/审查/验收/提交 → 前端实现/审查/验收/提交 → 中文文档闭环。
> **交付提交**：计划=`2e53007`、后端=`1eaa75e`、前端=`dbf301c`。

## 1. 决策

P10J 审计没有项目字段，删除成本条目后无法可靠关联项目。P10K 不做错误回填，新增最小不可变事件表，只记录本包上线后 P10C 成功写入的 workspace/project/entry/action/actor/time，并与业务变更和原审计同事务提交。

前端复用 `/finance` 既有选中项目，仅在用户点击后读取该项目最近 50 条事件；对其他成员只显示匿名 `other`。这比新建全局审计页更小，也不改变 P10B/P10C 首屏网络和角色边界。

## 2. 冻结行为

1. 新表只含 `id/workspace_id/project_id/entry_id/action/actor_user_id/created_at`，不含业务正文或快照。
2. P10C create/update/delete 在原事务内追加事件；失败全部回滚，删除后事件仍在。
3. 新 GET 先用最小 SQL 校验当前空间商务标，再以四列投影固定查询最近 50 条；返回 `action/entryId/actorScope/occurredAt`。
4. 前端不自动读取，显式打开一次、刷新再一次；项目切换立即清空并隔离迟到响应。
5. 无旧历史回填、用户身份、金额/内容、失败尝试、筛选分页导出、路由导航、依赖或外部连接。

## 3. 任务 1：后端受限实现

仅允许修改或新增：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/main.py`
- `backend/app/api/schemas.py`
- `backend/app/api/finance.py`
- `backend/app/services/finance_cost_service.py`
- `backend/app/services/finance_project_cost_change_event_service.py`（新建）
- `backend/tests/test_finance_project_cost_change_events.py`（新建）

不得修改 `database.py`、`deps.py`、认证/CSRF、中间件、P10B/P10J 服务、既有测试、依赖、脚本、前端或其他角色文件。

实现要求：

- 先写失败测试；实体使用服务端 `fpce_` ID、三值 CHECK 和 workspace/project/time 必要索引，`entry_id` 不设外键；
- 在新服务提供 `record_project_cost_change_event(..., commit=False)` 和只读列表；P10C 三个成功路径在唯一原事务内调用，不新增第二次 commit；
- 删除路径在删除业务行前保留 entry/project/actor；测试证明删除后事件存在，且事件或审计异常时业务变更不会提交；
- 读取前以 `select(Project.id)` 精确校验 workspace/project/business，禁止加载 editor-state/报价/成本实体；
- 事件 SELECT 只投影 action/entry_id/actor_user_id/created_at，SQL 在 LIMIT 前过滤三 action、合法字面 `fce_` 非空无首尾空白 entry、非空 actor，固定 50 和稳定倒序；
- 服务端映射 `actorScope=self|other`；响应白名单、固定 `no-store` 与固定脱敏读取审计；
- P10J 和 P10C API/响应保持兼容；不得回填旧审计、返回项目/成员/金额或添加通用查询；
- 文件顶和公开 API 补齐中文“模块 / 用途 / 对接 / 二次开发”注释；
- 完成后只发送 `review_request`，报告原任务 ID、精确八文件、失败先测、表/索引、事务证据、SQL 投影、定向/P10C/P10J/认证回归、`git diff --check`、风险与未做项，不 commit/push。

Codex 审查重点：是否出现双 commit 或事件与业务非原子；删除事件是否因 FK/顺序丢失；是否加载业务正文；其他项目/空间/actor 是否泄露；非法行是否在 LIMIT 后过滤；是否把上线后记录宣传成完整历史。

## 4. 任务 2：前端受限实现

后端独立验收提交后才派发。仅允许修改或新增：

- `frontend/package.json`
- `frontend/src/features/finance/types.ts`
- `frontend/src/features/finance/lib/financeApi.ts`
- `frontend/src/features/finance/pages/FinanceQuotePage.tsx`
- `frontend/src/features/finance/pages/FinanceQuotePage.css`
- `frontend/e2e/finance-project-cost-change-events.spec.ts`（新建）

不得修改 router/AppShell、认证/共享 API、P10J feature、Playwright 配置、依赖、后端或其他角色文件。

实现要求：

- 既有详情确认与当前选中项目一致后挂载面板；初始只显示限制说明和“查看项目记录”，严格零 P10K GET；
- 点击后请求编码后的项目路径一次；刷新累计一次；禁止模块全局缓存，组件实例状态即可；
- 切项目立刻收起清空，代次或 AbortController 必须防止旧响应写入新项目；
- 只显示固定动作、entryId、`本人/其他财务成员`、时间；未知枚举不原样泄露；错误固定中文；
- E2E 必须断言 P10B/P10C 首屏请求不变、显式请求次数与精确 URL、特殊字符项目 ID 编码、切换迟到隔离、P10J/未知业务/外网阻断和 local/session/IndexedDB/Cookie/clipboard/console 零泄漏；
- 完成后只发送 `review_request`，报告原任务 ID、精确六文件、失败先测、定向/P10C/P10B E2E、lint/build/diff-check、网络/存储边界，不 commit/push。

## 5. 独立验收与提交顺序

Codex 依次完成：

1. 审查后端八文件、表与索引、原子事务、删除保留、最小项目校验、四列投影和字段/权限/审计边界；运行 P10K 定向、P10C/P10J/财务角色/认证回归和后端串行全量，中文提交并推送。
2. 再派发前端，审查六文件、零自动请求、请求计数、项目切换迟到隔离、网络/存储反假绿；运行 lint、build、P10K/P10C/P10B 定向及单 worker 全量 E2E，中文提交并推送。
3. 更新契约、计划、路线图、联调清单和 HANDOFF，形成独立中文文档闭环提交并推送。

建议验证命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_finance_project_cost_change_events.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_finance_cost_draft.py tests/test_finance_cost_change_events.py tests/test_finance_role.py tests/test_auth_rbac.py
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:finance-project-cost-change-events
npm run test:e2e:finance-cost-draft
npm run test:e2e:finance-role
npm run test:e2e
```

所有 Playwright 命令必须等待前一个完成，禁止并行。所有 PowerShell 与 Grok 子进程后台静默运行，不启动可见窗口、浏览器或前台应用。

## 6. Grok review_request 必报项

后端必须报告原任务 ID、失败先测、精确八文件、表字段/约束/索引、三写路径同事务证据、删除保留、项目隔离、SQL 四列投影和 LIMIT 前过滤、权限/错误/审计、定向与回归测试、`git diff --check`、风险和未做项。前端必须报告原任务 ID、失败先测、精确六文件、零自动读取、显式/刷新次数、项目切换迟到隔离、路径编码、P10J/未知 API/外网阻断、浏览器存储与敏感信息检查、定向 E2E、lint/build/diff-check。两包均不得 commit/push。

## 7. 执行结果

1. 后端在精确八文件内完成最小事件表、P10C 三写路径同事务记录、删除保留、严格项目读取 API、四列投影、LIMIT 前合法性过滤和脱敏读取审计。Codex 退回一次测试假绿后复核通过；独立运行 P10K **21 passed**、受影响回归 **79 passed**、串行全量 **453 passed**，提交=`1eaa75e`。
2. 前端在精确六文件内完成既有 `/finance` 显式项目记录面板、刷新、切项目清空和迟到隔离。Codex 退回一次网络/存储测试假绿后复核通过；独立运行 P10K **9 passed**、P10C **4 passed**、P10B **7 passed**、lint/build 和单 worker 串行全量 **140 passed**，提交=`dbf301c`。
3. 未回填 P10K 上线前历史，未返回金额、内容、其他成员身份、失败尝试或变更前后值；未新增路由、依赖、筛选、分页、导出、审批、税务、预算、回款或完整审计。
