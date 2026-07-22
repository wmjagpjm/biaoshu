# 标书（biaoshu）

面向招投标场景的 **Web 自托管 AI 标书工作台**。  
当前已具备技术标/商务标的本机日用主链路、FastAPI 后端、异步任务、版本恢复、Word 导出和受限团队身份能力；真实外置解析器与可信内网发布仍需按部署环境准备。

## 仓库结构

```text
biaoshu/
├── frontend/          # Web 前端（Vite + React + TypeScript）
├── backend/           # FastAPI 后端、SQLite 数据与任务执行
├── docs/              # 设计说明、开发规范
└── README.md
```

## 快速开始（本机联调）

先准备已有的 `backend/.venv` 与 `frontend/node_modules`。启动脚本不会自动安装、下载或修复依赖，也不会自动打开浏览器。

```powershell
# 后台静默启动前后端，并写入 tmp/dev-start-status.json
.\Start-Biaoshu-Dev.bat

# 显式只读诊断，不启动或停止进程
.\Diagnose-Biaoshu-Dev.bat
```

浏览器访问：`http://127.0.0.1:5173/create`。默认仍只监听本机回环。

### 可信内网访问（V1-L，显式 opt-in）

同一局域网 5–6 人访问时，**禁止**把后端 `:8000` 或无鉴权前端直接绑到 `0.0.0.0`。正确拓扑：浏览器只打开 Vite 单入口，后端始终 `127.0.0.1:8000`，业务请求走同源 `/api` 代理。

1. **先在本机完成管理员 bootstrap**（不自动创建口令；脚本交互输入，勿写入 `.env`）：

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\bootstrap_local_admin.py
```

2. **显式指定本机一块 RFC1918 IPv4 后启动**（示例，请换成你的私有地址）：

```powershell
.\tools\v1-ops\Start-Biaoshu-Dev.ps1 -ListenProfile lan -LanHost 192.168.1.20
```

3. **内网浏览器只访问** `http://<LanHost>:5173`（如 `http://192.168.1.20:5173/create`）。**禁止**访问或映射 `:8000`、`/docs`、`/redoc`。

4. **Windows 防火墙仅手工配置**（生产代码不会改防火墙）。确认当前网络配置文件为 **Private** 后：

```powershell
# 查询
Get-NetFirewallRule -DisplayName 'Biaoshu-V1L-Vite-5173' -ErrorAction SilentlyContinue

# 创建：仅 Private + LocalSubnet + TCP 5173
New-NetFirewallRule -DisplayName 'Biaoshu-V1L-Vite-5173' -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 5173 -Profile Private -RemoteAddress LocalSubnet

# 回滚删除（不得删除其它规则）
Remove-NetFirewallRule -DisplayName 'Biaoshu-V1L-Vite-5173'
```

回滚顺序：停止服务 → 删除上述固定规则 → 恢复默认 `.\Start-Biaoshu-Dev.bat` 回环启动。远端设备可达性需在另一台可信内网机器上验证。

前端也可单独启动：

```bash
cd frontend
npm install
npm run dev
```

```bash
npm run build    # 生产构建
npm run preview  # 预览构建产物
```

也可使用仓库根目录桌面入口（若已配置）：`Biaoshu-UI.url` / `Start-Biaoshu-UI.bat`。

## 产品范围（路由）

| 模块 | 路由 | 说明 |
|------|------|------|
| 创建方案 | `/create` | 喜鹊式创建页；上传后创建项目并进入工作区 |
| 我的项目 | `/projects`、`/technical-plan` | 内置演示 + 本地新建项目列表 |
| 技术方案六步 | `/technical-plan/:id/:step` | 解析→分析→**大纲可编**→**事实可编**→**正文编辑**→导出 |
| 新建项目 | `/technical-plan/new` | 支持标讯预填名称 |
| 知识库 | `/knowledge-base` | 文件夹树 + 文档状态 + 图片库 |
| 资源中心 | `/resources` | 写作/合规/模板精选资源书架 |
| 标书查重 | `/duplicate-check` | 命中列表 + 本文/来源对照 |
| 废标项检查 | `/rejection-check` | 风险列表 + 条款/现状对照 |
| 商务标 | `/business-bid`、`/business-bid/:id/:step` | 六步分步工作区（解析→资格→目录→报价→承诺→导出） |
| 标讯 | `/bid-opportunity` | 列表筛选 + 一键创建技术方案项目 |
| 本地解析插件 | `/local-parser` | MinerU 说明与对接 |
| 模板设置 | `/export-format/*` | 对齐 C 端 ExportFormatConfig |
| 设置 | `/settings` | 工作空间、模型 Key、解析策略与模板配置 |

## 前端进度（摘要）

1. **导航**：左侧栏（易标式 SaaS，主色 `#6366F1`）  
2. **创建页**：仅开工类能力；查重/废标/模板等走侧栏，避免重复入口  
3. **技术标六步**：大纲为 STEP 03 三栏；正文左右编辑；事实、版本与任务状态已接服务端
4. **商务标六步**、知识库、资源、查重/废标对照、标讯、导出模板、站点背景  
5. **数据层**：本机 API + SQLite 为业务真值；浏览器存储仅保留受限本地偏好，模型调用按工作空间配置

## 后端与主链路（当前进度）

**新会话请读：[docs/HANDOFF-next.md](docs/HANDOFF-next.md)**（以 `collab/grok-code-codex-review` 最新提交为准，禁止直接操作 `main`）。

已落地：技术标本机日用主链路（上传/解析/分析结构化/大纲/章节/全书空章/Word）、异步任务、MinerU 回传、Key 明文、导出模板轻量接入。

```powershell
# 推荐：仓库根
.\Start-Biaoshu-Dev.bat

# 或使用 backend/run-dev.bat、frontend/run-dev.bat 分别启动
```

联调步骤见 **[docs/integration-checklist.md](docs/integration-checklist.md)**。  
前端 Vite 默认：`/api` → 回环 `8000`，host `127.0.0.1`；LAN 模式 host 为显式 `LanHost`，proxy 仍只指向回环。

## 设计原则

1. **前端先行**：UI / 交互 / 状态机先落地，接口位预留。  
2. **一账号一工作空间**：个人版与 `workspace` 1:1。  
3. **解析可插拔**：在线轻量 + 可选本地 MinerU。  
4. **可二开**：`features/<name>` 自包含；中文模块注释。  
5. **人工反馈 → AI 调整**：见 `docs/ai-feedback-loop.md`（UI 已接，模型调用待接）。  

## 开发规范

详见 [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)、[`docs/architecture.md`](docs/architecture.md)。

### 关键路径

```text
frontend/src/
├── app/layout/AppShell.tsx     # 顶栏导航
├── app/router.tsx
├── features/
│   ├── create/                 # 创建页
│   ├── technical-plan/         # 六步 + projectStore
│   ├── business-bid/           # 商务标六步
│   ├── knowledge-base/
│   ├── resources/
│   ├── duplicate-check/
│   ├── rejection-check/
│   ├── bid-opportunity/
│   ├── export-format/
│   └── settings/
└── shared/
    ├── components/EmptyState | LoadingBlock | AiFeedbackPanel
    ├── lib/api.ts              # 本机业务 API 封装
    └── mock/projects.ts
```

## 技术栈

- 前端：React 19、TypeScript、Vite、React Router、lucide-react  
- 后端：FastAPI、SQLAlchemy、SQLite、本机异步任务；生产级 PostgreSQL/Redis 仍后置

## License

私有仓库；引入第三方代码时请遵守其许可证（参考 C 端实现勿直接复制 AGPL 源码）。
