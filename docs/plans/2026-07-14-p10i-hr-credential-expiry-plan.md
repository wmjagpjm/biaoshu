<!--
模块：P10I 人员资质到期提示实施计划
用途：将服务端固定日期分类与严格 HR 页面拆为后端、前端两个可审查受限任务。
对接：docs/p10i-hr-credential-expiry-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：必须遵守文件白名单；Grok 只实现和自测，Codex 独立审查、验收、提交与推送。
-->

# P10I 人员资质到期提示实施计划

> **状态**：独立契约与实施边界已冻结，等待受限实现。<br>
> **工作分支**：`collab/grok-code-codex-review`。<br>
> **前置基线**：P10H 文档闭环=`8678eac`；后端串行全量 392 passed；前端 lint/build 与单 worker 串行全量 E2E 93 passed。Playwright 共用 SQLite 重置库，必须串行。

## 1. 决策

P10I 不做“证件真伪校验”。当前系统没有证件号、扫描件或合法权威来源，接入这些数据会显著扩大隐私和外网边界。当前可安全交付的是：复用 P10D 人工 `validUntil`，由服务端按 UTC 日期和固定 90 天窗口生成只读到期提示。

新能力使用独立 `/api/hr/credential-expiry` 和 `/hr/credential-expiry`，不修改 P10D CRUD 语义、不新增表、不向 P10F 团队推荐或标书制作者投影。

## 2. 冻结数据与行为

1. 仅服务端读取当前空间 `HrCredentialCardRow`；启用卡分类，停用卡只计数排除。
2. 固定状态为 `expired`、`expiring_soon`、`valid`、`missing_expiry`；窗口固定 90 天，无查询参数。
3. 响应只含契约列出的固定计数和关注项；有效卡只计数，备注、时间戳、创建人和空间不出域。
4. 成功读取 `no-store` 并写固定审计；无写接口、无 CSRF、无新表、无后台任务。
5. 页面明确“仅日期提示，不验证真实性”，只请求 P10I GET，不在客户端读取 P10D 列表后二次推断。

## 3. 任务 1：后端受限实现

仅允许修改或新增：

- `backend/app/api/schemas.py`
- `backend/app/api/hr.py`
- `backend/app/services/hr_credential_expiry_service.py`（新建）
- `backend/tests/test_hr_credential_expiry.py`（新建）

不得修改实体、`main.py`、`deps.py`、认证/CSRF、P10D/P10F/P10H 服务、项目/文件/财务/投标人接口、依赖、迁移、脚本或既有测试。

实现要求：

- 先写失败测试，服务函数允许测试显式传入 `as_of: date`，生产路由不接收日期/窗口输入；
- SQL 只按当前 `workspace_id` 读取资质卡，分类与排序严格遵守契约；
- 输出 Pydantic 模型使用固定别名与枚举，不以裸 dict 泄漏字段；
- 路由只复用 `require_hr`，成功响应加 `no-store` 并写固定脱敏审计；
- 文件顶和公开 API 补齐中文“模块 / 用途 / 对接 / 二次开发”注释；
- 完成后只发送 `review_request`，报告精确文件、失败先测、定向/受影响回归、`git diff --check`、风险和未做项，不 commit/push。

Codex 验收重点：日期边界是否由服务端决定；`valid` 是否只计数；停用卡是否不出列表；审计是否无业务值；是否意外扩大 P10D 或增加查询参数。

## 4. 任务 2：前端受限实现

后端验收提交后才派发。仅允许修改或新增：

- `frontend/package.json`
- `frontend/src/app/router.tsx`
- `frontend/src/app/layout/AppShell.tsx`
- `frontend/src/features/hr-credential-expiry/types.ts`（新建）
- `frontend/src/features/hr-credential-expiry/lib/hrCredentialExpiryApi.ts`（新建）
- `frontend/src/features/hr-credential-expiry/hooks/useHrCredentialExpiry.ts`（新建）
- `frontend/src/features/hr-credential-expiry/pages/HrCredentialExpiryPage.tsx`（新建）
- `frontend/src/features/hr-credential-expiry/pages/HrCredentialExpiryPage.css`（新建）
- `frontend/e2e/hr-credential-expiry.spec.ts`（新建）

不得修改 `useAuthSession`、共享 API/认证层、P10D/P10F/P10H feature、Playwright 配置、依赖、Sidebar 或后端文件。

实现要求：

- 复用 `RequireHr`，新增 `/hr/credential-expiry` 和严格 HR 导航项，确保 `/hr` 精确激活不冲突；
- 页面初始只请求 `GET /hr/credential-expiry`，展示服务端日期、固定窗口、计数和关注项，不在浏览器重算状态；
- 关注项不展示 `cardId`，无详情、编辑、跳转、自动修复或导出；
- 错误固定中文且不回显 detail，数据只在内存；
- E2E 阻断 P10D/P10F/P10H、项目、文件、财务、投标人、外网与未知 API，验证无敏感存储；
- 完成后只发送 `review_request`，报告定向 E2E、lint/build、网络/存储断言和 `git diff --check`，不 commit/push。

## 5. 独立验收与提交顺序

Codex 依次完成：

1. 审查后端白名单与契约，运行 P10I 定向、P10D/P10F/P10H/认证相关回归和后端串行全量；形成独立中文后端提交并推送。
2. 派发前端单一任务，审查白名单、固定投影、门禁、网络和存储；运行 lint、build、P10I 定向及全量 E2E（单 worker 串行）；形成独立中文前端提交并推送。
3. 更新本计划验收记录、契约、路线图、联调清单和 HANDOFF 注释齐备表；形成独立中文文档闭环提交并推送。

建议验证命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_hr_credential_expiry.py
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:hr-credential-expiry
npm run test:e2e
```

所有 Playwright 命令必须等待前一个完成，禁止并行。所有 PowerShell 与 Grok 子进程后台静默运行，不启动可见窗口、浏览器或前台应用。
