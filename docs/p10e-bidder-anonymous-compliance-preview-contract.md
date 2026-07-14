# P10E 投标人受限匿名合规预览契约

## 1. 目标与边界

P10E 仅向当前工作空间中角色严格为 `bidder` 的已登录成员，提供一张只读的匿名合规汇总卡。该卡依据技术标项目中已收敛的响应矩阵，显示覆盖、未覆盖、豁免与覆盖率基点，用于了解整体响应矩阵的合规准备度。

本包不是项目列表、评审结论、法律意见或投标结果，也不生成、修改、导出、审批、分享或保存任何标书内容。它不新增数据表、不调用 LLM、不创建任务、不访问外网，也不读取或暴露财务、人力、知识库、文件、模板、标讯、设置或项目详情。

## 2. 权限与隔离

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色精确为 `bidder` | 允许读取当前工作空间预览 |
| required 未登录 | 全局认证中间件返回 `401 auth_required` |
| disabled、所有者隐式绕过、`bid_writer`、`finance`、`hr` | `403 role_forbidden` |
| 已登录但 `X-Workspace-Id` 指向非成员空间 | `403 workspace_forbidden` |

新增 `require_bidder` 必须与 `require_finance`、`require_hr` 对称：它只检查当前活动成员的精确角色，绝不因 `is_owner` 或个人版兼容模式放行。前端门禁和导航隐藏仅用于体验，不能替代后端检查。

## 3. 数据来源与匿名投影

唯一数据来源是当前工作空间、`kind=technical` 项目的既有 `editor-state.responseMatrix`。服务端必须通过既有 `reconcile_response_matrix` 语义收敛失效章节和大纲引用后再计数：非豁免行若已无有效关联，按 `uncovered` 计入。

接口只返回下列固定投影：

```json
{
  "dataState": "ready",
  "summary": {
    "totalItems": 12,
    "coveredItems": 8,
    "uncoveredItems": 3,
    "waivedItems": 1,
    "coverageBasisPoints": 7273
  }
}
```

`dataState` 仅为 `ready` 或 `empty`。当总条目为零时为 `empty`，全部计数为零，`coverageBasisPoints` 为 `null`。覆盖率分母为 `coveredItems + uncoveredItems`，豁免项不进入分母；结果以整数基点表示，按最接近整数的半入规则计算。响应不得包含项目数量、项目 ID、项目名称、工作空间 ID、人员身份、时间戳、招标原文、来源标题、`sourceKey`、章节/大纲 ID 或标题、备注、文件、报价、成本、审计内容或任意原始矩阵行。

## 4. 接口、缓存与审计

| 方法 | 路径 | 成功 | 说明 |
|---|---|---:|---|
| GET | `/api/bidder/compliance-preview` | 200 | 当前空间匿名聚合；固定 `Cache-Control: no-store` |

没有写接口、详情接口、项目参数、分页参数、筛选参数或导出接口。浏览器只能通过本机 `/api/bidder/compliance-preview` 读取业务数据，且不得使用 `localStorage`、`sessionStorage`、Cookie 附加数据或本地 mock 缓存预览结果。

每次成功读取都写一条审计：`action=bidder_compliance_preview_read`、`result=success`、`target=anonymous_aggregate`。审计只允许保留既有操作者与工作空间关联；不得记录项目 ID/名称、任何计数、矩阵内容、请求头或响应体。

## 5. 前端体验与错误收口

严格 `bidder` 仅看到独立的「投标人 / 合规预览」导航和 `/bidder` 页面；不能看到标书制作者、财务或人力入口。页面只展示固定中文说明、四项统计和覆盖率。`coverageBasisPoints=null` 显示「暂无可计算覆盖率」，不得在客户端自行推导不同口径。

加载、空数据和失败均为中文状态；失败文案固定为「暂时无法读取匿名合规预览」，不得回显后端 `detail`、错误码、URL、项目名或矩阵内容。非 `bidder` 直达 `/bidder` 只显示固定受限页，且不得发起投标人预览请求。

## 6. 验收与非目标

后端测试必须证明：严格角色、disabled、所有者、未登录和跨空间隔离；投影字段精确且没有内部标识或原文泄漏；空态、基点计算、失效引用收敛、`no-store` 与脱敏审计均正确。前端 E2E 必须证明：仅投标人可进入、网络白名单仅含认证/健康检查/本接口、非投标人不请求接口、无浏览器持久化、错误脱敏和匿名 UI。

项目级匿名卡片、详情钻取、版本比较、合规规则执行、废标检查结果、人工确认、导出、通知、评分、投标结果与任何写操作均不在 P10E 范围。它们若有需要，必须先单独冻结数据保护、授权、审计和保留策略。
