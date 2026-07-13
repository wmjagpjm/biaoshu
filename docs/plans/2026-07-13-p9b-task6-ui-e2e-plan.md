# P9B 任务 6：国能计划追踪面板与本地 E2E 实施计划

> **给 Grok：** 逐项 TDD 实施；完成后仅发送 `review_request`，不得提交或推送。

**目标：** 在既有“标讯”页增加受控追踪面板，使用户能上传计划表、触发同步、查看脱敏命中并人工加入本地标讯；浏览器只访问本机 `/api`，不会直连国能 e 招。

**架构：** 先补齐已冻结但尚未实现的 `GET /api/opportunity-watch/dashboard` 后端只读聚合接口，动态生成安全公告链接；再由 `useOpportunities` 管理独立追踪状态与运行轮询。Playwright 仅驱动本地测试后端及 MockTransport，禁止真实外网。

## 范围与不可变规则

- 面板必须明确显示“国能 e 招候选公告，需人工确认；不会自动创建项目”。
- 支持 `.xlsx` 导入计数、同步运行状态、计划数、最近运行、命中标题、北京时间截止/开标时间和待复核状态。
- 只有 `resolved` 命中展示“加入本地标讯”；接受后刷新本地标讯列表，重复点击不得重复。`needs_review` 不展示接受操作。
- 同步运行期间禁用同步按钮；前端只轮询本空间 `GET /runs/{runId}`，不得接收/拼接任意来源 URL、Cookie、Token、站点或搜索条件。
- `announcementUrl` 只能由后端按命中结构化字段动态生成，前端外链必须 `target="_blank"` 与 `rel="noreferrer"`。
- 既有 CSV/JSON 导入、本地标讯卡片、状态计算和创建项目逻辑不得改义。

## 严格白名单

- 修改：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/tests/test_opportunity_watch.py`
- 修改：`frontend/src/features/bid-opportunity/types.ts`
- 修改：`frontend/src/features/bid-opportunity/hooks/useOpportunities.ts`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunityPage.tsx`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunity.css`
- 新建：`frontend/e2e/opportunity-watch-chnenergy.spec.ts`
- 修改：`frontend/package.json`

## 实施步骤

### 任务 1：后端仪表盘只读契约

1. 写失败测试：当前空间返回计划数、最近运行和更新时间倒序命中；命中响应仅含结构化字段和动态 `announcementUrl`，跨空间不可见，非法详情字段不得生成链接。
2. 在服务和 schema 增加只读 dashboard 模型与 `GET /dashboard`；不保存 URL、HTML 或正文，不添加同步/接受副作用。
3. 运行 `python -m pytest -q tests/test_opportunity_watch.py -k "dashboard"`。

### 任务 2：前端状态、面板与人工接受

1. 先写失败 E2E：导入 `.xlsx` 显示计数；点击同步显示进行中，轮询终态后显示命中与北京时间；只对 resolved 露出接受按钮。
2. 在现有 hook 增加独立追踪 API 状态、文件上传、运行轮询、接受后刷新；不改变既有机会列表请求与 CSV/JSON 导入函数。
3. 页面与样式增加紧凑面板、安全外链和明确的人工确认文案；无 URL 输入框。
4. E2E 用本地 fixture/MockTransport 验证重复接受、needs_review 隐藏按钮、既有导入仍可用；禁止外网。

### 任务 3：交付与验收

1. Grok 只运行：后端 dashboard 定向、`npm run lint`、`npm run build`、新增 `npm run test:e2e:opportunity-watch`、`git diff --check`。
2. Codex 独立审查前端无外部 fetch、后端无任意 URL/重定向/Cookie/原文持久化，复跑后端全量和全部前端检查。
3. 通过后提交信息固定为：`新增国能计划追踪界面`，只推送协作分支；随后进入任务 7 文档闭环。
