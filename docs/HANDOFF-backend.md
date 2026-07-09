# 新会话交接：开始后端开发（biaoshu）

> **状态锁定（前端）**：UI / 交互原型阶段已收口，可开后端。  
> **下一目标**：FastAPI 后端脚手架 → 项目 CRUD → LLM/revise → 上传解析 → 逐步替换前端 mock。

---

## 0. 新会话第一句（复制即用）

```text
开始 biaoshu 后端开发。仓库 C:\Users\Administrator\biaoshu。
前端已完成（npm run dev → http://127.0.0.1:5173/create），请严格按 docs/HANDOFF-backend.md 执行。
从 FastAPI 脚手架 + GET /api/health + 项目 CRUD 做起；对接 frontend/src/shared/lib/api.ts。
用户自备 API Key，禁止把密钥写进仓库。对话/注释/Commit Message 用简体中文。
```

---

## 1. 前端结论：可以进后端

| 项 | 结论 |
|----|------|
| 构建 | `cd frontend && npm run build` 通过 |
| 模块 | 创建、技术标六步、商务标六步、知识库、资源、查重、废标、标讯、模板、设置、本地解析说明均有页面 |
| 数据 | 大量 **mock + localStorage**；**无真实 API** |
| LLM | 设置页可配供应商/Base/Key/模型；**未接真实调用**（后端代接或代理） |
| UI | 易标式左侧栏 SaaS（主色 `#6366F1`）；目录步为 STEP 03 三栏；创建页仅「开工类」能力 |
| 背景 | 设置 → 站点背景，可上传本地背景图（`biaoshu.siteBackground.v1`） |

**明确非目标（本交接后）**：不再大改前端信息架构，除非对接 API 必须改 hook；后端优先。

---

## 2. 入口

### 前端启动

```bash
cd C:\Users\Administrator\biaoshu\frontend
npm install   # 首次
npm run dev
```

- 地址：http://127.0.0.1:5173/ （`/` → `/create`）
- 桌面 / 仓库根：
  - `Start-Biaoshu-UI.bat`（起 Vite 并打开浏览器）
  - `Biaoshu-UI.url`（仅打开页面，需服务已起）

### 主要路由

| 页面 | URL |
|------|-----|
| 标书生成（创建） | `/create` |
| 我的项目 | `/projects` |
| 技术标工作区 | `/technical-plan/:id/:step`（step: document/analysis/outline/facts/content/export） |
| 目录 STEP03 示例 | `/technical-plan/proj_01/outline` |
| 商务标 | `/business-bid`、`/business-bid/:id/:step` |
| 知识库 / 资源 | `/knowledge-base`、`/resources` |
| 查重 / 废标 | `/duplicate-check`、`/rejection-check` |
| 标讯 / 设置 / 模板 | `/bid-opportunity`、`/settings`、`/export-format` |
| 本地解析说明 | `/local-parser` |

### 仓库

| 项 | 路径 |
|----|------|
| 根 | `C:\Users\Administrator\biaoshu` |
| 前端 | `frontend/` |
| 后端（空） | `backend/`（仅占位） |
| 规范 | `docs/CONTRIBUTING.md` |
| 架构目标 | `docs/architecture.md` |
| 反馈契约 | `docs/ai-feedback-loop.md` |
| README | `README.md` |
| GitHub | https://github.com/wmjagpjm/biaoshu |

### 语言

- 对话、注释、Commit Message：**简体中文**
- 代码标识符英文

---

## 3. 产品决策（后端必须遵守）

1. **Web 自托管**；一账号 ≈ 一 `workspace`
2. **算力用户自备 API Key**（服务端加密存；**禁止提交密钥**）
3. **解析**：在线轻量 + 可选本地 MinerU 回传
4. **非 Electron**
5. **核心交互**：人工意见 → 基于原文定向修订（见 `docs/ai-feedback-loop.md`）
6. **导出模板**对齐 C 端 ExportFormat（前端类型：`features/export-format/model/`）
7. C 端 OpenBidKit **只参考交互，勿抄 AGPL 源码**

---

## 4. 前端对接点（后端对齐）

### HTTP 客户端

- 文件：`frontend/src/shared/lib/api.ts`
- Base：`import.meta.env.VITE_API_BASE_URL ?? "/api"`
- 建议开发：
  ```env
  # frontend/.env.local（勿提交密钥）
  VITE_API_BASE_URL=http://127.0.0.1:8000/api
  ```
  或 Vite 代理 `/api` → `8000`，避免 CORS。

### 建议 API 优先级

| 优先级 | 能力 | 说明 |
|--------|------|------|
| P0 | `GET /api/health` | 探活 |
| P0 | 鉴权 + workspace | 个人版 token / 单用户可先简化 |
| P0 | 项目 CRUD | 对齐 `shared/types/workspace.ts` 的 `Project` |
| P0 | LLM 配置读写 | 对齐设置页字段；Key 加密 |
| P0 | `POST .../revise` | 反馈修订（ai-feedback-loop.md） |
| P1 | 文件上传 + 轻量解析任务 | 技术标 document 步 |
| P1 | 任务进度 | 轮询或 SSE |
| P1 | 大纲 / 事实 / 章节 持久化 | 替换 localStorage editors |
| P2 | 知识库 / 导出 Word | |
| P2 | 查重 / 废标 | 可先简版 |
| P3 | 商务标流水线 / 标讯源 | |

### 前端 localStorage 键（迁移参考）

| Key | 用途 |
|-----|------|
| `biaoshu.projects.v1` | 用户新建项目列表 |
| `biaoshu.technicalPlan.editors.{projectId}` | 大纲/正文/事实 |
| `biaoshu.projectFeedback.{projectId}` | guidance + 反馈 history |
| `biaoshu.businessBid.workspace.{id}` | 商务标工作区 |
| `biaoshu.businessBid.feedback.{id}` | 商务标反馈 |
| `biaoshu.settings.v1` | 模型/Key/解析策略 |
| `biaoshu.knowledgeBase.docs.v1` | 知识库文档+文件夹 |
| `biaoshu.knowledgeImages.v1` | 用户上传图片 |
| `biaoshu.siteBackground.v1` | 站点背景图 dataURL |

对接原则（CONTRIBUTING）：**先改 service/hook，尽量不动页面结构**。

### 关键前端文件

```text
frontend/src/
├── shared/lib/api.ts              # 统一 fetch
├── shared/types/workspace.ts      # Project / Workspace
├── shared/types/aiFeedback.ts     # 反馈阶段与记录
├── features/technical-plan/lib/projectStore.ts
├── features/technical-plan/hooks/useProjectGuidance.ts
├── features/technical-plan/hooks/useTechnicalPlanEditors.ts
├── features/settings/hooks/useWorkspaceSettings.ts
├── features/export-format/model/  # 导出配置类型
└── features/business-bid/hooks/useBusinessBidWorkspace.ts
```

---

## 5. 后端建议技术栈与目录

```text
浏览器 (React)
  → HTTP +（长任务）SSE
FastAPI
  → Worker（解析/生成）
  → SQLite 起步（可升 PostgreSQL）· 本地文件存储 · Redis 可选
```

```text
backend/
├── app/
│   ├── api/          # 薄路由
│   ├── services/     # 业务（文件头中文「用途」）
│   ├── models/
│   ├── tasks/
│   └── core/         # 配置、鉴权、依赖
├── tests/
├── requirements.txt / pyproject.toml
├── .env.example      # 无真实密钥
└── README.md         # 启动说明
```

---

## 6. 建议实施顺序（第一个后端会话）

1. **脚手架**：FastAPI + CORS + 配置 + `GET /api/health` + `backend/README.md`  
2. **SQLite 模型**：User / Workspace / Project（最小字段对齐前端）  
3. **项目 API**：list / create / get / patch（个人版可暂跳复杂登录）  
4. **前端**：`projectStore` 或 list/create 改 `apiFetch`，`.env.local` 指后端  
5. **设置 API**：模型配置；Key 环境变量或加密字段  
6. **LLM 代理** + **revise** 接 `useProjectGuidance.submitRevise`  
7. **上传 + 解析任务**；再章节生成与导出  

---

## 7. MVP 验收清单

- [ ] `uvicorn` 启动成功，`GET /api/health` → 200  
- [ ] 前端配置 `VITE_API_BASE_URL` 后能拉项目列表（非纯 mock）  
- [ ] 至少一条 LLM 调用路径可通（测试接口或 revise）  
- [ ] `.env` / 密钥在 `.gitignore`；仓库无 sk-  
- [ ] `frontend` 的 `npm run build` 仍通过  
- [ ] Commit Message 中文  

---

## 8. 前端已做能力速查（避免后端重复造 UI）

- 侧栏：标书生成、我的项目、知识库、资源、查重、废标、商务标、标讯、模板、本地解析、设置  
- 创建页：仅开工类（技术/商务/完整/施工/以标写标/单章/框架/清单）；**无**查重/废标/模板重复入口  
- 技术标：六步；**outline = STEP 03 三栏**（过程 / 树 / 详情）  
- 商务标：六步 mock  
- 设置：模型 Key、解析策略、**站点背景上传**  
- 反馈面板：UI 已接，结果仍可先 mock 后换 API  

---

## 9. 不要做

- 再大改 UI 视觉体系（除非联调阻塞）  
- 引入 Electron  
- 复制 C 端 AGPL 源码  
- 把 API Key 写进代码或提交 Git  

---

**交接日期参考**：前端收口后进入后端。  
**负责人提示**：下一会话只做后端 + 必要对接，前端以稳为主。
