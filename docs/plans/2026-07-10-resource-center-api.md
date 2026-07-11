# 资源中心后端化实施计划

> **协作约定**：Codex 负责逐项实现与验证；Grok 在数据契约和最终差异两个节点进行只读反方审查。本计划不创建 Git 提交，除非用户明确要求。

**目标：** 将资源中心从前端 mock 和可选浏览器远程请求改为本地、可审计的 API；保留系统精选资源，并允许当前工作空间维护自有资源。

**架构：** 新增 `resources` 表。`source=system` 的记录不归属任何 workspace、只读且由启动期幂等写入；`source=user` 的记录必须归属一个 workspace，所有读写均在服务层校验归属。资源正文仅按纯文本 Markdown 展示，不在 v1 解析 HTML；浏览量通过单条 SQL 更新在服务端累加。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、React、TypeScript、pytest、Vite。

---

## 冻结的 v1 契约

- `GET /api/resources?q=&tag=&category=`：返回系统资源与当前 workspace 自建资源，正文为 `bodyMarkdown`，按更新时间倒序。
- `GET /api/resources/{id}`：只返回系统资源或当前 workspace 资源；其它 workspace 返回 404。
- `POST /api/resources`、`PATCH/DELETE /api/resources/{id}`：仅允许创建或维护 `source=user` 资源；系统资源写操作返回 403。
- `POST /api/resources/{id}/view`：可见资源的 `viewCount` 服务端原子加一，并返回更新后的资源；不修改 `updatedAt`。
- 字段：`id`、`workspaceId|null`、`source`、`title`、`description`、`category`、`tags`、`bodyMarkdown`、`tone`、`viewCount`、`createdAt`、`updatedAt`。
- 系统资源仅迁入现有六条 mock 的同等内容；不写入用户 workspace，不增加远程 URL、同步 Token、爬虫、富文本 HTML 或分析埋点。
- 现有 `X-Workspace-Id` 仅沿用个人版开发期 workspace 选择，不构成登录鉴权；文档必须保留这个边界。

## 任务 1：先写 API 回归测试

**文件：**

- 新建：`backend/tests/test_resources.py`
- 参考：`backend/tests/test_opportunities.py`

1. 断言应用启动后列表含六条 `source=system` 资源，且系统资源 `workspaceId` 为 `null`；断言来源与 workspace 不一致的直写被数据库约束拒绝。
2. 断言用户资源的创建、详情、关键词/标签/分类筛选、更新与删除；响应字段为 camelCase。
3. 创建第二 workspace，断言其不能读取、更新、删除或浏览另一 workspace 的用户资源，均返回 404。
4. 断言系统资源更新和删除返回 403、浏览量可累加但不改变更新时间；断言列表不会泄漏其它 workspace 用户资源，客户端伪造来源和 workspace 会被忽略。
5. 先运行专项测试，确认 API 尚不存在时失败；实现后再次运行专项测试。

## 任务 2：实现模型、种子、服务与路由

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/api/schemas.py`
- 新建：`backend/app/services/resource_service.py`
- 新建：`backend/app/api/resources.py`
- 修改：`backend/app/main.py`

1. 新增 `ResourceRow`；系统资源 `workspace_id=NULL`，用户资源 `workspace_id` 指向 `workspaces`；新库使用 CHECK，既有 SQLite 表启动期补同语义触发器。
2. 在服务层实现文本、标签、色调清洗，用户资源的可见性、写权限与资源详情读取。
3. 将 mock 的六条内容迁入服务层固定种子，按稳定 id 幂等写入；不得把系统资源写入默认 workspace。
4. 用 `UPDATE view_count = view_count + 1` 完成浏览量累加，避免浏览器本地状态和并发请求丢失计数。
5. 路由层只校验入参、映射 400/403/404、序列化响应；不直接操作 ORM。
6. 所有新文件和公开 API 补齐中文“模块 / 用途 / 对接 / 二次开发”注释。

## 任务 3：替换前端 mock

**文件：**

- 修改：`frontend/src/features/resources/types.ts`
- 新建：`frontend/src/features/resources/hooks/useResources.ts`
- 修改：`frontend/src/features/resources/pages/ResourcesPage.tsx`
- 修改：`frontend/src/features/resources/pages/ResourcesPage.css`
- 删除：`frontend/src/features/resources/mock.ts`

1. 删除 `VITE_RESOURCES_URL`、浏览器 `fetch` 和 mock 回退；统一使用 `shared/lib/api.ts`。
2. 保留搜索、书架和详情弹层；搜索继续即时在已加载列表筛选，并由刷新入口请求 API。
3. 增加紧凑的新增/编辑弹层；仅 `source=user` 显示编辑、删除图标。
4. 点击资源调用浏览接口，以服务端返回的资源更新列表和详情；失败显示可重试错误，不在前端假加浏览量。
5. 样式维持当前业务页的紧凑布局，弹层和卡片圆角不超过 8px；资源封面改为实体色，避免页面主视觉依赖渐变。

## 任务 4：复审、联调与交接

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`
- 更新：本文件执行记录

1. 运行 `backend/.venv/Scripts/python -m pytest -q`、`frontend npm run build`、`git diff --check`。
2. 启动本地服务，浏览 `/resources`，手动验证系统资源、用户资源 CRUD、浏览量刷新和跨 workspace 隔离。
3. 将实体、路由、服务、Hook 和页面交给 Grok 复审，优先检查跨 workspace 泄漏、系统资源篡改、计数原子性和前端远程源残留。
4. 更新验收基线、注释齐备表、接口表及未完成边界；不提交 `.env`、密钥、数据库或上传目录。

## 实施边界

- 本轮不做外站采集、RSS、同步任务、URL 白名单、Token、资源附件、版本历史、审批、付费资源、埋点、富文本 HTML 或多人鉴权。
- 不复用知识库的文件与检索模型；资源中心只保存少量精选 Markdown 条目，知识库仍用于可检索语料。
- 本轮不创建 Git 提交；如后续需要，提交信息必须使用简体中文。

## 执行记录（2026-07-10）

- 已完成资源实体、系统六条精选种子、SQLite CHECK/既有表触发器、服务层、路由、专项回归与前端 Hook、页面接入；删除 `resources/mock.ts`。
- 已删除 `VITE_RESOURCES_URL`、浏览器远程 fetch 和 mock 回退。系统资源全局只读且 `workspaceId=null`；用户资源强制绑定当前 workspace；Markdown 仅作文本展示。
- 已完成浏览器联调：系统资源加载、用户资源创建、详情展开和服务端浏览量 `0 → 1` 均成功；联调产生的资源已清理，未删除本机原有资源。
- Grok 复审未发现可复现 P0/P1；其提出的浏览排序、列表隔离、系统浏览与伪造字段测试缺口均已修复或补测。外部同步、多人鉴权和 Alembic 仍明确不在本轮范围。
- 最终验证已通过：`backend/.venv/Scripts/python -m pytest -q` 为 **82 passed**；`frontend npm run build` 通过，仅保留既有单包超过 500 kB 警告；`git diff --check` 通过。
