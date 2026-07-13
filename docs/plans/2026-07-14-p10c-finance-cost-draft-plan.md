# P10C：财务成本草案与毛利快照实施计划

## 1. 决策与目标

P10C 在 P10B 的商务标报价只读投影之上，新增由财务成员人工维护的项目成本草案，并基于当前报价合计给出确定性的毛利快照。它解决“报价已有、成本无来源、不能安全讨论毛利”的缺口，但不把草案伪装成会计核算、审批结论或税务报表。

本包完成后，严格 `finance` 可在当前工作空间内：

1. 查看某个商务标的报价合计、成本草案合计、毛利金额和毛利率；
2. 新建、修改、删除人工成本条目；
3. 看到以人民币分为精度的确定性汇总；
4. 由服务端记录不含金额、名称和备注正文的变更审计事件。

## 2. 范围与非目标

### 2.1 本包范围

- 仅 `AUTH_MODE=required`、当前成员角色严格为 `finance`；
- 仅当前工作空间且 `kind=business` 的项目；
- 新增独立 `finance_cost_entries` 数据表，每条包含项目、类别、名称、人民币分金额、备注、创建/更新时间和创建人；
- 成本类别固定 `labor`（人工）、`material`（材料）、`service`（服务）、`other`（其他）；
- 报价收入由 P10B 已规范的 `quoteTotal` 转换为人民币分后只读计算；
- 汇总公式：`毛利 = 报价合计 - 成本合计`；当报价合计小于等于零时 `grossMarginBasisPoints=null`，不作除零或虚假百分比；
- 新增财务成本草案接口与 `/finance` 页面中的受控录入、编辑、删除、汇总和空/错误状态。

### 2.2 明确不做

- 不写回 `business_json`、报价行、项目状态或技术标；
- 不新增税率、含税/未税口径、币种换算、发票、预算、回款、审批流、导出、锁账、版本历史或会计凭证；
- 不让 `owner`、`bid_writer`、`hr`、`bidder` 访问或修改成本草案；
- 不把报价、成本、毛利或备注发送到外部服务、URL、日志、浏览器存储或审计事件正文；
- 不修改 P10A 的会话、CSRF、中间件公开路径、成员管理或 P10B 只读接口语义；
- 不为旧 SQLite 库增加手写 ALTER；新表由既有 `Base.metadata.create_all` 建立，生产迁移问题仍由 Alembic 后续处理。

## 3. 权限与数据契约

### 3.1 权限矩阵

| 行为 | strict `finance` | `owner` / `bid_writer` / `hr` / `bidder` | 未登录 required | disabled |
| --- | --- | --- | --- | --- |
| 查看成本草案与毛利快照 | 允许 | `403 role_forbidden` | `401 auth_required` | `403 role_forbidden` |
| 新建、修改、删除成本条目 | 允许（会话 + CSRF） | `403 role_forbidden` | `401 auth_required` | `403 role_forbidden` |
| 技术标、跨空间或不存在项目 | `404 project_not_found` | 不泄露 | 不泄露 | 不开放 |
| P10B 报价只读接口 | 保持既有 | 保持既有 | 保持既有 | 保持既有 |

### 3.2 表与字段

表名：`finance_cost_entries`。

| 字段 | 规则 | 说明 |
| --- | --- | --- |
| `id` | 服务端随机不透明 ID | 不接受客户端指定 |
| `workspace_id`、`project_id` | 服务端从已校验商务标写入 | 禁止客户端伪造或跨空间 |
| `category` | `labor` / `material` / `service` / `other` | 固定枚举 |
| `name` | 1–120 个字符 | 成本项名称 |
| `amount_fen` | 正整数，最大 999,999,999,999 分 | 人民币分，禁止浮点持久化 |
| `remark` | 最多 500 个字符，可空 | 仅财务成本草案可见 |
| `created_by_user_id` | 从已验证会话取得 | 不接受客户端传入 |
| `created_at`、`updated_at` | 服务端 UTC | 不接受客户端传入 |

数据库与服务层都必须拒绝非法类别、非正金额、过长文本和非当前工作空间项目。读取排序固定为 `updated_at` 降序、`id` 作为稳定次序，不允许客户端排序字段或筛选表达式。

### 3.3 汇总响应

`GET /api/finance/business-bids/{projectId}/cost-draft` 只返回：

```json
{
  "projectId": "proj_xxx",
  "projectName": "某商务标",
  "quoteTotalFen": 12800000,
  "costTotalFen": 8350000,
  "grossProfitFen": 4450000,
  "grossMarginBasisPoints": 3477,
  "costEntries": [
    {
      "id": "fce_xxx",
      "category": "material",
      "name": "设备采购",
      "amountFen": 8000000,
      "remark": "",
      "createdAt": "2026-07-14T00:00:00+00:00",
      "updatedAt": "2026-07-14T00:00:00+00:00"
    }
  ]
}
```

`quoteTotalFen` 仅由 P10B 的有限数值报价合计以十进制、四舍五入到分转换；绝不从字符串、对象或非有限值推算。`grossMarginBasisPoints` 是基点（万分之一），以整数公式取最近值；报价合计小于等于零时为 `null`。响应不能含报价行、`business_json`、技术标、用户姓名、创建人 ID、审计细节、设置或认证字段。

### 3.4 写入接口

| 方法 | 路径 | 请求字段 | 成功 |
| --- | --- | --- | --- |
| GET | `/api/finance/business-bids/{projectId}/cost-draft` | 无 | 200 汇总草案 |
| POST | `/api/finance/business-bids/{projectId}/cost-entries` | `category`、`name`、`amountFen`、`remark` | 201 条目 |
| PATCH | `/api/finance/business-bids/{projectId}/cost-entries/{entryId}` | 至少一个可修改字段 | 200 条目 |
| DELETE | `/api/finance/business-bids/{projectId}/cost-entries/{entryId}` | 无 | 204 |

所有变更请求走 P10A 既有 CSRF 校验。条目不属于当前空间或项目时统一 404；不允许借 `entryId` 探测其他工作空间。每次成功创建、更新、删除都调用既有审计服务，行动名固定为 `finance_cost_create`、`finance_cost_update`、`finance_cost_delete`，审计目标仅含条目 ID，禁止放金额、名称、备注或原始请求。

## 4. 实施任务

### 任务 1：后端成本草案域与受限 API（Grok 实现）

允许文件（初版白名单）：

- `backend/app/models/entities.py`、`backend/app/models/__init__.py`；
- `backend/app/api/schemas.py`、`backend/app/api/finance.py`；
- `backend/app/services/finance_cost_service.py`（新建）；
- `backend/app/main.py`（仅实体导入，如确有必要）；
- `backend/tests/test_finance_cost_draft.py`（新建）。

若需要改动 P10B `finance_service.py` 或 `deps.py`，必须先报告原因，等待 Codex 将其加入白名单；禁止自行扩大范围。

实现要点：

1. 新模型文件顶和公开类/函数均写中文四字段注释；ORM 约束与服务层校验双重存在；
2. 复用 P10B `require_finance`，路由取得已验证主体的 user id 仅用于创建人/审计，不能从客户端 body 或 header 接受；
3. 先验证项目属于当前空间且为商务标，再操作条目；统一 404；
4. 使用 `Decimal(str(...))` 与固定量化把既有报价总额转为分；毛利与基点计算全程使用整数/Decimal，响应不输出浮点金额；
5. `DELETE` 只删除当前项目条目；成功后记录审计而不记录敏感正文；
6. 响应加 `Cache-Control: no-store`，响应 Schema 作为字段白名单；
7. 不能改动 P10B 两个只读端点、认证中间件、会话、CSRF、配置、数据库脚本或依赖。

后端验收：

- 财务可以完成创建、读取、修改、删除，汇总与基点计算可复现；
- 边界金额、报价为零、无成本条目、报价含非有限值、非法十进制输入均有测试；
- 非财务、disabled、未登录、技术标、跨空间和伪造条目均按契约拒绝；
- 审计只记录动作和条目目标，不包含成本正文；
- P10B 只读接口响应不因 P10C 增加成本字段；
- 定向测试、P10A/P10B 鉴权回归和 `git diff --check` 通过。

### 任务 2：前端成本草案与毛利快照（Grok 实现）

允许文件在任务 1 验收后另行冻结。预期仅触达：

- `frontend/src/features/finance/types.ts`、`lib/`、`hooks/`、`pages/FinanceQuotePage.tsx` 及配套样式；
- `frontend/e2e/finance-cost-draft.spec.ts`（新建）、`frontend/package.json`；
- 若确有必要，前端现有财务 E2E 文件。

体验约束：

1. 仅 strict finance 可见，沿用 `/finance` 门禁，不给其他角色任何前端绕过；
2. 输入金额使用“元，最多两位小数”的纯文本校验，前端转为整数分后再调用 API；服务端仍是权威校验；
3. 明确标示“成本草案”和“基于当前报价的毛利快照”，不使用“已审批”“最终利润”“含税”等表述；
4. 创建、编辑、删除均有加载/失败处理且不乐观伪造成功；无成本、报价为零、毛利为负和 `grossMarginBasisPoints=null` 都可读；
5. 只请求 P10B 两个 GET 和本包四个成本草案接口；不得请求通用项目、editor-state、设置、文件或外部地址；
6. 端到端测试覆盖金额分转换、增改删、汇总、角色门禁、网络白名单、错误脱敏和不写浏览器敏感存储。

### 任务 3：Codex 独立验收与文档闭环

1. 审查差异是否只触及白名单，重点检查整数金额、舍入、CSRF、审计脱敏、跨空间 404、条目归属和报价只读不变；
2. 独立运行定向、P10A/P10B 回归、后端全量串行分组、前端 lint/build 和相关 E2E；
3. 新增 P10C 数据契约、更新交接/联调/路线图/注释齐备表，记录精确测试基线；
4. 仅 Codex 以中文提交、推送协作分支并向 Grok 回传验收结论。

## 5. 风险与停止条件

| 风险 | 本包处置 |
| --- | --- |
| 报价金额是历史浮点值 | 仅在读取汇总时十进制量化到分；不回写历史报价 |
| 成本敏感 | 工作空间隔离、strict finance、no-store、审计不记正文，浏览器不持久化 |
| 毛利被误认为会计结论 | 页面和契约固定称“草案/快照”；不引入税务、审批、导出或锁账 |
| 扩展为通用财务系统 | 严格禁止；超出字段/流程须另建 P10D 计划 |
| 旧数据库演进 | 仅新表 create_all；若生产需要迁移治理，先立 Alembic 计划 |

若发现无法在不修改 P10A 会话/中间件、P10B 只读语义或白名单外文件的前提下完成，Grok 必须停止实施并通过消息箱报告；Codex 再作范围决策。
