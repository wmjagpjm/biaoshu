# P10B：财务只读商务投标报价实施计划

## 1. 目标与完成定义

为已启用本机身份模式的团队提供一个最小、可审计的财务报价查看能力。财务角色只能查看本工作空间内商务标项目的项目摘要和已落库报价分项，不获得技术标、编辑器完整状态、文件、任务、设置或任何写入能力。

完成定义：

1. `finance` 可通过专用只读接口查看商务标报价列表与单项目明细；
2. 接口返回严格白名单投影，不能泄露 `business_json` 的资格、目录、承诺字段，也不能泄露技术标、文件或模型设置；
3. 前端只对财务角色显示“财务报价”入口，并可完成列表、明细、空状态和错误状态展示；
4. 非财务角色以及财务越权访问既有业务接口继续被拒绝；
5. 后端、前端、端到端和回归测试全部通过，中文交接文档随实现提交并推送。

## 2. 范围、边界与决策

### 2.1 本包范围

- 数据来源限定为既有 `Project(kind=business)` 与 `ProjectEditorState.business_json.quote`；
- 财务报价列表：项目 `id`、名称、行业、状态、更新时间、报价行数与报价合计；
- 财务报价明细：列表字段，加上 `quote.rows` 的 `id`、`name`、`unit`、`quantity`、`unitPrice`、`amount`、`remark`，以及 `quote.notes`；
- 仅在 `AUTH_MODE=required` 且当前会话角色为严格 `finance` 时开放；
- 新增独立的 `/api/finance/business-bids` 与 `/api/finance/business-bids/{project_id}` 路由，不复用或放宽通用项目、编辑器路由的角色检查。

### 2.2 明确不做

- 不新增、推算或伪造成本、利润、税率、毛利率等没有可靠数据源的财务数据；
- 不实现财务编辑、导出、审批、项目创建、文件下载、任务创建或设置管理；
- 不向 `owner`、`bid_writer`、`hr`、`bidder` 扩展本专用接口；现有角色矩阵继续生效；
- 不返回 `businessQualify`、`businessToc`、`businessCommit`、技术方案、解析文本、资源、知识库、供应商信息、LLM 设置或 API Key；
- 不修改 P10A 的会话、CSRF、成员管理和通用工作空间授权逻辑。

### 2.3 权限矩阵

| 操作 | `finance` | `owner` / `bid_writer` | `hr` / `bidder` | 未登录或个人兼容模式 |
| --- | --- | --- | --- | --- |
| 查看财务报价列表、明细 | 允许 | 拒绝（`role_forbidden`） | 拒绝（`role_forbidden`） | 拒绝（`role_forbidden`） |
| 修改报价或项目 | 拒绝 | 保持现有业务标权限 | 拒绝 | 保持现有兼容逻辑 |
| 访问通用项目、编辑器、文件、设置 | 拒绝（P10A 既有规则） | 保持既有规则 | 拒绝（P10A 既有规则） | 保持既有兼容逻辑 |

“严格 finance”是刻意选择：专用财务读模型不因所有者或投标编写者身份而隐式扩大可见面；他们仍通过原有、受控的业务标路径完成日常工作。

## 3. 数据契约

### 3.1 列表响应

`GET /api/finance/business-bids` 返回：

```json
{
  "items": [
    {
      "projectId": "proj_xxx",
      "name": "某商务标",
      "industry": "通用",
      "status": "draft",
      "updatedAt": "2026-07-14T00:00:00+00:00",
      "quoteRowCount": 2,
      "quoteTotal": 128000.0
    }
  ]
}
```

### 3.2 明细响应

`GET /api/finance/business-bids/{project_id}` 返回列表字段、`quoteRows` 和 `quoteNotes`。金额合计只能由已存在的 `amount` 数值安全累加；不能解析或展示未规范的原始字段。缺失、非数值或异常行以既有编辑器状态规范化后的安全默认值处理。

若项目不存在、不属于当前工作空间，或不是商务标，统一返回 `404 project_not_found`，不得泄露项目存在性。所有响应均不得含有未列入本节白名单的字段。

## 4. 实施任务

### 任务 1：后端只读投影与权限收口（Grok 实现）

涉及文件：

- `backend/app/api/deps.py`：增加不改变既有通用角色判断的严格财务依赖；
- `backend/app/services/finance_service.py`：新增白名单投影、商务标筛选、金额安全归一与工作空间隔离；
- `backend/app/api/finance.py`：新增两个 `GET` 路由，固定中文错误码/消息与不缓存响应；
- `backend/app/api/schemas.py`：新增显式 Pydantic 输出模型；
- `backend/app/main.py`：挂载财务路由；
- `backend/tests/test_finance_role.py`：新增独立鉴权、投影和越权回归测试。

实现约束：

1. 服务层使用 `list_projects(..., kind="business")` 与编辑器状态服务读取数据，不直接将数据库 `business_json` 或编辑器状态字典透传；
2. 对每一行报价构造新字典，只读取契约字段；计算总额仅接受有限数值，确保 `NaN`、无穷大、字符串和嵌套对象不会进入响应；
3. 详情先以当前工作空间查项目，再确认 `kind == business`，错误一律归并为 404；
4. 严格财务依赖只在 required 模式接受 `finance`，禁用模式或无会话固定 `role_forbidden`，不污染 P10A 个人兼容分支；
5. 路由没有任何写方法，也不接受模型设置、文件路径或项目状态等输入；响应加 `Cache-Control: no-store`；
6. 代码注释、错误消息、测试名说明均使用中文。

后端验收：

- 财务用户只能看到本工作空间商务标；技术标、其他工作空间和不存在项目均 404；
- 明细只含约定字段，资格、目录、承诺、解析内容和完整 `business_json` 均不存在；
- 财务访问 `/api/projects`、编辑器、文件、设置仍返回 P10A 既有拒绝；
- 所有非财务角色及未登录用户调用新接口返回 `403 role_forbidden`；
- 对新 URL 的 `POST`、`PUT`、`PATCH`、`DELETE` 不产生写入，框架返回 405；
- 新测试、鉴权回归和后端全量测试通过。

### 任务 2：前端财务报价视图（Grok 实现）

涉及文件：

- `frontend/src/features/finance/`：新增严格类型、请求封装、报价列表与明细组件；
- `frontend/src/pages/FinanceQuotePage.tsx` 与配套样式：实现只读列表、项目选择、分项表、备注、无数据与故障状态；
- `frontend/src/app/router.tsx`、`frontend/src/app/layout/AppShell.tsx`、`frontend/src/app/layout/Sidebar.tsx`：注册并仅对 finance 显示 `/finance`；
- `frontend/e2e/finance-role.spec.ts`、`frontend/package.json`：补充可独立运行的端到端验收命令。

实现约束：

1. 页面只调用两个专用财务端点；禁止调用通用项目、编辑器、设置或写接口作为降级路径；
2. 前端角色判断必须复用 P10A 的 `AuthProvider`，财务入口仅在 `authMode=required && role=finance` 时显示；禁用模式不显示且访问路由显示受限页；
3. 所有金额按本地显示格式输出，未提供金额显示“—”，不在浏览器推算成本或利润；
4. 不在 localStorage、URL、日志或错误界面写入会话、CSRF、业务完整状态或原始服务端异常；
5. 保持已有技术标和商务标页面的路由、导航及视觉行为不变。

前端验收：

- 财务登录后可见侧栏入口，并可看到仅限本工作空间的报价列表与明细；
- 空报价、空项目、接口失败均可读且不会白屏；
- 所有者、投标编写者、人力、投标人不能看见入口，直达 `/finance` 时受限；
- 浏览器网络记录不出现通用项目/编辑器/设置的财务页面回退请求；
- lint、构建、专用 E2E 与既有认证 E2E 通过。

### 任务 3：Codex 独立审查、验收和文档闭环

1. 对 Grok 未提交差异逐文件审查：接口白名单、授权顺序、跨工作空间、浮点/异常值、前端路由绕过与敏感字段泄露；
2. 运行后端全量分组测试、前端 lint/build、认证与财务 E2E、语义和卡片检查；必要时补充或要求修复测试；
3. 在 `docs/HANDOFF-next.md`、集成检查表与本计划中记录 P10B 能力、边界、命令、基线和后续 P10C 候选；
4. 仅由 Codex 以中文提交信息提交，执行差异检查、推送协作分支，并向 Grok 回传验收结果。

## 5. 独立验收命令

在仓库根目录执行：

```powershell
cd backend
pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:auth-rbac
npm run test:e2e:finance-role
```

若 Windows 并行测试进程导致 SQLite 锁冲突，按现有 P10A 分组命令串行执行；不得为绕过失败而删减断言或跳过鉴权测试。

## 6. 风险与后续决策点

| 风险或未知项 | 本包处置 | 后续路径 |
| --- | --- | --- |
| 现有报价没有成本和利润数据 | 只呈现事实报价，明确不推算 | P10C 先定义成本、税率、权限和审计数据契约后再实施 |
| `business_json` 是混合业务包 | 只读服务逐字段投影 | 后续可迁移到独立财务只读模型，但不得改变本包接口含义 |
| 人力与投标人缺少稳定数据域 | 保持拒绝，绝不以路由名称猜测权限 | 分别完成领域契约和脱敏策略后单独立项 |
| 财务查看数据敏感 | 保持会话、工作空间隔离和 no-store | 后续审计查看事件、导出审批等能力另行设计 |

## 7. 未完成项

- 成本、利润、税务、审批流与导出；
- 人力资源团队协同数据域；
- 投标人匿名预览、版本管控与合规脱敏；
- 财务查看行为的专用审计事件。

这些内容均不属于 P10B，不得在实施中顺带扩权。
