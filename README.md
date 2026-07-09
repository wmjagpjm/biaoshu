# 标书（biaoshu）

面向招投标场景的 **Web 自托管 AI 标书工作台**。  
产品能力对齐开源桌面端「易标」C 端工作流；**前端可交互原型已齐**，后端与真实 LLM 逐步接入。

## 仓库结构

```text
biaoshu/
├── frontend/          # Web 前端（Vite + React + TypeScript）
├── backend/           # 后端（占位，FastAPI 规划中）
├── docs/              # 设计说明、开发规范
└── README.md
```

## 快速开始（前端）

```bash
cd frontend
npm install
npm run dev
```

浏览器访问：`http://127.0.0.1:5173/`（或终端提示地址）。

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
| 设置 | `/settings` | 工作空间 / 模型 Key / 解析 / 模板分区，localStorage |

## 前端进度（摘要）

1. **导航**：左侧栏（易标式 SaaS，主色 `#6366F1`）  
2. **创建页**：仅开工类能力；查重/废标/模板等走侧栏，避免重复入口  
3. **技术标六步**：大纲为 STEP 03 三栏；正文左右编辑；事实可编（localStorage）  
4. **商务标六步**、知识库、资源、查重/废标对照、标讯、导出模板、站点背景  
5. **数据层**：mock + localStorage；**后端与真实 LLM 待做**  

## 后端与主链路（当前进度）

**新会话请读：[docs/HANDOFF-next.md](docs/HANDOFF-next.md)**（以 GitHub `main` 最新提交为准）。

已落地：技术标本机日用主链路（上传/解析/分析结构化/大纲/章节/全书空章/Word）、异步任务、MinerU 回传、Key 明文、导出模板轻量接入。

```powershell
# 推荐：仓库根
.\Start-Biaoshu-Dev.bat

# 或分别启动 backend :8000 + frontend :5173
```

联调步骤见 **[docs/integration-checklist.md](docs/integration-checklist.md)**。  
前端 Vite：`/api` → `8000`，host 固定 `127.0.0.1`。  

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
    ├── lib/api.ts              # 业务后端占位
    └── mock/projects.ts
```

## 技术栈

- 前端：React 19、TypeScript、Vite、React Router、lucide-react  
- 后端（规划）：FastAPI、任务队列、PostgreSQL/SQLite、Redis  

## License

私有仓库；引入第三方代码时请遵守其许可证（参考 C 端实现勿直接复制 AGPL 源码）。
