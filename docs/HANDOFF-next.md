# 新会话交接：biaoshu 当前状态与下一步

> **交接日期**：2026-07-09（会话收口）  
> **仓库**：`C:\Users\Administrator\biaoshu`  
> **GitHub**：https://github.com/wmjagpjm/biaoshu  
> **分支**：`main`  
> **最新提交（已推送）**：`f9a0814` — `feat: 招标分析结构化与导出模板轻量接入`  
> **本地与远程**：`main` = `origin/main`（无未推送提交）

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu。仓库 C:\Users\Administrator\biaoshu，GitHub 已同步 main@f9a0814。
请严格按 docs/HANDOFF-next.md 执行。目标 A（本机技术标主链路）已基本可用。
对话/注释/Commit Message 用简体中文；用户自备 API Key，禁止把密钥写进仓库。
启动：仓库根 Start-Biaoshu-Dev.bat；或 backend/run-dev.bat + frontend/run-dev.bat。
```

---

## 1. 产品定位（锁定）

- **Web 自托管**，个人版一账号 ≈ 一 `workspace`（默认 `ws_local`）
- **算力用户自备 API Key**；**保密机允许明文存/回显**（产品决策，勿擅自改加密）
- **非 Electron**；C 端 OpenBidKit **只参考交互，勿抄 AGPL 源码**
- 语言：对话 / 注释 / Commit Message = **简体中文**

---

## 2. 启动与联调

| 项 | 说明 |
|----|------|
| 一键双启 | `Start-Biaoshu-Dev.bat`（推荐） |
| 分启 | `backend\run-dev.bat`、`frontend\run-dev.bat` |
| 前端 | http://127.0.0.1:5173/create （Vite **固定 host 127.0.0.1:5173**） |
| 后端 | http://127.0.0.1:8000/api/health |
| 代理 | `frontend/vite.config.ts`：`/api` → `8000` |
| 侧栏 | API 在线/离线状态点 |
| 清单 | `docs/integration-checklist.md` |
| 冒烟 | `backend/scripts/smoke_e2e.py`（需先起 uvicorn） |

```powershell
# 后端依赖（若新环境）
cd backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm install
npm run dev
```

**注意**：旧 SQLite 缺新列时，启动会 `ensure_schema_columns()` 尝试 `ALTER TABLE`；仍异常可删 `backend/data/*.db` 重建（个人版可接受）。

---

## 3. 已完成能力（勿重复造）

### 3.1 后端 API（`backend/app/`）

| 能力 | 路径/说明 |
|------|-----------|
| 探活 | `GET /api/health`（含 `defaultWorkspaceId`、`dbOk`） |
| 项目 CRUD | `/api/projects` |
| 设置 | `GET/PUT /api/settings`（Key **明文**；含 `exportFormat`） |
| LLM 测通 | `POST /api/llm/test` |
| 反馈修订 | `POST /api/projects/{id}/artifacts/{aid}/revise` |
| 编辑器状态 | `GET/PUT /api/projects/{id}/editor-state`（大纲/章节/事实/guidance/解析/结构化分析） |
| 上传 | `POST/GET /api/projects/{id}/files` |
| 任务 | `POST/GET /api/projects/{id}/tasks`（**默认异步线程**；`?sync=true` 同步测） |
| 任务类型 | `parse` / `analyze` / `outline` / `chapter` / `chapters` / `export` |
| MinerU 回传 | `POST /api/projects/{id}/parse-callback`（可选 `X-Local-Token`） |
| Word 下载 | `GET /api/projects/{id}/export/download/{stored}` |

### 3.2 技术标前端主链路

- 创建/列表项目对接 API（可 `VITE_MERGE_MOCK_PROJECTS=false` 关掉演示混排）
- document：上传 + 轻量解析 + 预览 `parsedMarkdown`
- analysis：**结构化**概述/技术要求/废标风险/评分点（可编辑，非死绑 mock）
- outline / content：AI 生成 + 全书空章；任务进度条轮询
- export：Word 下载；默认模板样式轻量接入
- 设置：Key 明文；模板「设为默认」同步 `exportFormat` 到后端
- `/local-parser`：Markdown 回传表单

### 3.3 关键目录

```text
backend/app/
  api/          # 路由
  services/     # 业务（task/llm/parse/export/editor_state…）
  models/       # ORM
  core/         # config/database
frontend/src/
  features/technical-plan/   # 六步工作区 + hooks
  features/settings/
  features/export-format/
  features/local-parser/
  shared/lib/api.ts
docs/
  HANDOFF-next.md            # 本文（新会话用）
  HANDOFF-backend.md         # 旧交接（历史，已被超越）
  integration-checklist.md
  CONTRIBUTING.md
  architecture.md
  ai-feedback-loop.md
```

### 3.4 近期提交（已 push）

1. `a422bad` — 后端脚手架 + 上传解析/生成/导出  
2. `1557f40` — 异步任务轮询 / 全书生成 / MinerU 回传  
3. `f9a0814` — 招标分析结构化 + 导出模板轻量接入  

---

## 4. 明确未完成（下一会话优先参考）

### 4.1 技术标打磨（高优先若继续 A）

| 项 | 现状 |
|----|------|
| 任务暂停/取消 | UI 禁用，无实现 |
| SSE 推送 | 仍轮询 |
| 大纲 revise 自动写回树 | 仅预览文本 |
| 完整 ExportFormat → docx | 只映射字体/标题/页边距等核心字段 |
| 真 MinerU 安装包/进程 | 仅回传通道 |
| Alembic 迁移 | 仅 create_all + 轻量 ALTER |
| E2E 自动化 | 基本无 |

### 4.2 整站仍 mock（无真实后端）

- **商务标**六步  
- **知识库**索引/RAG（前端 mock + 本地图）  
- **查重 / 废标**规则引擎  
- **标讯**数据源  
- **资源中心**（可选远程 URL）  

### 4.3 工程/生产（多人/公网）

- 登录鉴权、多用户、HTTPS、限流、Key 加密  
- PostgreSQL、备份、Docker 部署、监控审计  

**粗估**：本机写技术标 ~80%；内网多人 ~30%；公网 SaaS ~15%。

---

## 5. 建议下一会话方向（三选一或组合）

1. **技术标继续打磨**：任务取消、大纲 revise 写回、导出模板字段扩展  
2. **质量跃升**：知识库检索注入生成（RAG 简版）  
3. **业务扩展**：商务标接同一套 project/task 模型  

默认推荐：**1 小步收口 + 2 若要质量**。

---

## 6. 注释规范提醒

- 后端核心文件多数有「模块/用途/对接」  
- **并非 100% 每个函数都四字段写满**；前端 mock 模块更简  
- 新代码继续遵守 `docs/CONTRIBUTING.md`；用户曾表示「就这样」——不必为注释单开大重构，但新改动要写清  

---

## 7. 安全与仓库

- `.gitignore` 已忽略：`.env`、`*.db`、`uploads/`、`data/`、`node_modules/`、`.venv/`  
- **禁止**提交真实 `sk-` / `.env`  
- 测试里可有 `sk-test-plain-key` 类假密钥  

---

## 8. 验证命令（换会话后先跑）

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest -q
# 期望：14+ passed

cd ..\frontend
npm run build
```

```powershell
git status -sb
# 期望：main...origin/main 且干净
git log -1 --oneline
# 期望：f9a0814 或更新
```

---

## 9. 旧文档关系

| 文档 | 状态 |
|------|------|
| **docs/HANDOFF-next.md** | **当前有效交接（用这份）** |
| docs/HANDOFF-backend.md | 前端收口时写的，描述已过时，仅作历史 |
| docs/integration-checklist.md | 联调操作清单，保持更新 |

---

**负责人提示**：下一会话只做清单内目标；勿大改 UI 信息架构；先改 hook/service。  
**GitHub 已推送，可直接 pull 后开干。**
