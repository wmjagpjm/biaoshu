# 标书后端（biaoshu backend）

FastAPI + SQLite 起步实现。当前能力：探活、项目 CRUD。  
**用户自备 LLM API Key**，请勿把密钥写入仓库或提交 `.env`。

## 环境要求

- Python 3.11+（推荐）
- Windows / macOS / Linux

## 快速启动

```powershell
cd C:\Users\Administrator\biaoshu\backend
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 可选：复制环境变量
copy .env.example .env

uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

- 探活：http://127.0.0.1:8000/api/health  
- OpenAPI：http://127.0.0.1:8000/docs  

## 前端对接（仅本机开发）

本机开发任选其一（**不是**可信内网多人访问方式）：

1. **Vite 代理**（推荐，前端默认 `API_BASE=/api`）  
   `frontend/vite.config.ts` 已配置 `/api` → `http://127.0.0.1:8000`。
   后端与 OpenAPI/docs 仅本机回环可达。

2. **本机直连后端**（仅开发机；禁止用于局域网其它电脑）
   在 `frontend/.env.local`（勿提交）中：
   ```env
   VITE_API_BASE_URL=http://127.0.0.1:8000/api
   VITE_USE_API_PROJECTS=true
   ```

可信内网 5–6 人访问见仓库根 `README.md` 的 V1-L 说明：只暴露 Vite `5173`，**禁止**内网浏览器直连 `:8000`。

## 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 探活 |
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 创建 |
| GET | `/api/projects/{id}` | 详情 |
| PATCH | `/api/projects/{id}` | 部分更新 |
| DELETE | `/api/projects/{id}` | 删除 |
| GET | `/api/settings` | 读 LLM/解析配置（**apiKey 明文**） |
| PUT | `/api/settings` | 写配置（明文 Key，保密机决策） |
| POST | `/api/llm/test` | 用当前配置测模型连通 |
| POST | `/api/projects/{id}/artifacts/{artifactId}/revise` | 按反馈定向修订 |
| GET/PUT | `/api/projects/{id}/editor-state` | 大纲/正文/事实/概述/guidance |
| GET | `/api/resources/sync-sources` | 已配置受控资源来源的脱敏同步状态 |

联调清单见仓库 `docs/integration-checklist.md`。  
一键双启：仓库根 `Start-Biaoshu-Dev.bat`。

个人版默认 workspace：`ws_local`。可通过请求头 `X-Workspace-Id` 覆盖（高级）。

响应字段为 **camelCase**（`workspaceId`、`updatedAt`、`technicalPlanStep`、`wordCount`），对齐前端 `Project` 类型。

## 受控资源同步

资源中心可由管理员从预配置、Ed25519 签名的 HTTPS 清单同步只读系统资源。默认不配置来源且不会联网；同步命令不接受 URL、Token 或浏览器请求。

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe scripts\sync_resources.py
```

来源配置和清单签名规则见仓库 `docs/resource-sync-manifest.md`。禁止把发布方私钥、Token、Cookie 或 `.env` 提交到仓库。

## 测试

```powershell
cd backend
.\.venv\Scripts\activate
pytest -q
```

## 目录约定

见仓库 `docs/CONTRIBUTING.md` 与 `docs/HANDOFF-backend.md`。

## 注释约定（二次开发必读）

每个模块文件顶部 + 每个公开函数/类，中文写清：

- **模块**：是什么  
- **用途**：做什么、关键规则  
- **对接**：HTTP 路径 / 调用方 / 配置项  
- **二次开发**（可选）：扩展点与禁止事项  

路由层保持薄：只做参数与状态码；业务进 `services/`。
