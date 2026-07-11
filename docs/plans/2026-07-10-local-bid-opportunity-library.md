# 本地标讯库实施计划

> **协作约定：** Codex 逐项实施与验证；Grok 在数据契约和最终差异两个节点做只读反方审查。本计划不创建 Git 提交，除非用户明确要求。

**目标：** 将标讯页从演示 mock 改为工作空间内可维护的本地标讯库，并支持按开放标讯一键创建关联技术标项目。

**架构：** 新增 workspace 级 `bid_opportunities` 表，截止状态由服务端根据 `deadline` 计算而非持久化。`POST /opportunities/{id}/projects` 在同一服务事务内校验标讯、拒绝已截止记录、创建 `technical` 项目并写入弱关联；删除标讯只清关联，不删除项目。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、React、TypeScript、pytest、Vite。

---

## 已冻结的 v1 契约

- 标讯只属于一个 workspace；不抓取网站、不同步 RSS、不保存外部 URL、附件、API Key 或抓取游标。
- `deadline` 为必填 `YYYY-MM-DD` 日期，服务端本地日期超过截止日为 `closed`；剩余 0 至 7 天为 `closing_soon`，其余为 `open`。
- API 为 `GET/POST /api/opportunities`、`GET/PATCH/DELETE /api/opportunities/{id}`、`POST /api/opportunities/{id}/projects`；所有跨 workspace 访问均返回 404。
- 列表 query：`q`（标题、采购人、摘要、标签，不区分大小写）、`region`（精确）、`status`（计算状态）；按截止日期升序、更新时间降序。
- 立项接口只创建技术标项目，默认项目名为标讯标题；允许同一标讯多次立项；已截止标讯返回 400。
- `projects.source_opportunity_id` 是可空弱关联；删除标讯设为 NULL，不删除关联项目、文件、任务或编辑态；删除项目不影响标讯。
- 示例数据不在正常启动时写入。仅本地演示环境显式设置 `SEED_SAMPLE_OPPORTUNITIES=true` 时，才向默认 workspace 幂等写入现有 mock 改写的“本地示例”；不保留前端 mock 作为运行时兜底，API 异常须明确显示错误。

## 任务 1：先写 API 失败测试

**文件：**

- 新建：`backend/tests/test_opportunities.py`
- 参考：`backend/tests/test_health_and_projects.py`

**步骤：**

1. 写创建、详情、PATCH、DELETE、`q/region/status` 筛选、三种截止状态的失败/成功断言。
2. 写从开放标讯创建项目、已截止拒绝、重复立项、删除标讯后项目保留且关联清空的断言。
3. 运行专项测试，预期在 API 尚不存在时失败。

## 任务 2：实现后端实体、服务与路由

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/api/schemas.py`
- 新建：`backend/app/services/opportunity_service.py`
- 新建：`backend/app/api/opportunities.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/project_service.py`

**步骤：**

1. 增加 `BidOpportunityRow` 及 `Project.source_opportunity_id`，补 SQLite 轻量迁移；启动时注册，且仅在显式演示开关开启时为默认 workspace 幂等写入示例标讯。
2. 实现日期解析、计算状态、CRUD、搜索筛选、归属校验和清理关联。
3. 在标讯服务内完成“读取标讯、计算状态、创建项目”的单次事务；失败时不得留下半成品项目。
4. 运行专项和完整后端测试。

## 任务 3：接入标讯页面

**文件：**

- 修改：`frontend/src/features/bid-opportunity/types.ts`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunityPage.tsx`
- 视需要删除：`frontend/src/features/bid-opportunity/mock.ts`
- 视需要新建：`frontend/src/features/bid-opportunity/hooks/useOpportunities.ts`

**步骤：**

1. 通过现有 `apiFetch` 加载列表和筛选，保留页面现有筛选交互与状态空态。
2. 增加紧凑的“新增标讯”入口和编辑/删除操作；不改变侧栏信息架构。
3. “创建技术方案项目”直接调用立项 API，成功后跳转项目正文第一步；关闭状态保留禁用。
4. 运行 `npm run build` 并在浏览器验证加载、筛选、编辑、立项和删除。

## 任务 4：Grok 复审、交接与回归

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`
- 更新：本文执行记录

**步骤：**

1. 向 Grok 提供实体、服务、路由和页面差异，重点审查 workspace 越权、截止状态、事务边界和删除影响。
2. 运行 `backend` 全量 `pytest -q`、`frontend npm run build`、`git diff --check`。
3. 更新基线、注释齐备表、手工验收路径和仍未接入的外部数据源边界；不得提交密钥、数据库或上传目录。

## 实施边界

- 本轮不做公开站点采集、RSS、定时任务、标讯附件、全文搜索、推送提醒、审批、商务标双建或软删除。
- 未经用户明确指示不得把模拟标讯伪装成实时公开数据；种子记录必须标为“本地示例”。
- 本轮不创建 Git 提交；如后续需要，提交信息必须使用简体中文。

## 执行记录（2026-07-10）

- 已完成实体、SQLite 补列、服务层、路由、前端 hook 与页面接入；删除 `bid-opportunity/mock.ts`，运行时只使用 API。
- 已落实 Grok 反方审查：默认关闭示例标讯写入；新增状态边界、跨 workspace 404、默认无种子及项目创建异常回滚测试。
- 已由 Grok 审查数据契约、事务、删除弱关联和差异实现：未发现可复现 P0；`X-Workspace-Id` 的多用户可信边界属于既有个人版架构限制，后续登录改造时统一解决。
- 验证已通过：`backend/.venv/Scripts/python -m pytest -q` 为 **77 passed**；`frontend npm run build` 通过，仅保留既有单包超过 500 kB 警告。
