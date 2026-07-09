# 标书（biaoshu）

面向招投标场景的 **Web 自托管 AI 标书工作台**。  
产品能力对齐开源桌面端「易标」C 端工作流，先交付前端可交互原型，后端逐步接入。

## 仓库结构

```text
biaoshu/
├── frontend/          # Web 前端（Vite + React + TypeScript）
├── backend/           # 后端（待开发，FastAPI 规划中）
├── docs/              # 设计说明、接口约定、开发规范
└── README.md
```

## 快速开始（前端）

```bash
cd frontend
npm install
npm run dev
```

浏览器访问终端提示的本地地址（默认 `http://localhost:5173`）。

```bash
npm run build    # 生产构建
npm run preview  # 预览构建产物
```

## 产品范围（对齐 C 端模块）

| 模块 | 路由 | 说明 |
|------|------|------|
| 工作台首页 | `/` | 项目入口、快捷操作 |
| 技术方案 | `/technical-plan/*` | 解析→分析→大纲→全局事实→正文→导出 |
| 知识库 | `/knowledge-base` | 企业素材沉淀与引用 |
| 标书查重 | `/duplicate-check` | 重复表达检查 |
| 废标项检查 | `/rejection-check` | 响应完整性/废标风险 |
| 商务标 | `/business-bid` | 商务标工作区（占位可二开） |
| 标讯 | `/bid-opportunity` | 标讯入口（占位可二开） |
| 本地解析插件 | `/local-parser` | MinerU 本地助手说明与对接 |
| 导出格式 | `/export-format` | Word 导出样式预设 |
| 设置 | `/settings` | API Key、模型、解析策略 |

## 设计原则

1. **前端先行**：UI / 交互 / 状态机先落地，数据层使用 mock，接口位预留。
2. **一账号一工作空间**：为后续 B 端隔离预留 `workspace` 概念。
3. **长任务异步**：生成类操作在 UI 上按「任务进度」展示，对接后端队列。
4. **解析可插拔**：在线轻量解析 + 本地 MinerU 插件。
5. **可二开**：目录按 feature 拆分，注释与类型齐全，避免业务逻辑堆在页面里。

## 开发规范

详见 [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)。

## 技术栈

- 前端：React 19、TypeScript、Vite、React Router、lucide-react
- 后端（规划）：FastAPI、任务队列、PostgreSQL/SQLite、Redis

## License

私有仓库；引入第三方代码时请遵守其许可证（参考实现勿直接复制 AGPL 源码）。
