# 开发规范

本文档约束本仓库的协作与二开方式，避免模块耦合失控。

## 目录约定

### 前端 `frontend/src`

```text
src/
├── app/                 # 应用壳：布局、路由、全局 Provider
├── features/            # 业务功能（按域拆分，自包含）
│   └── <feature>/
│       ├── pages/       # 路由页面
│       ├── components/  # 仅本 feature 使用的组件
│       ├── hooks/       # 本 feature hooks
│       ├── types.ts     # 本 feature 类型
│       └── mock.ts      # 本 feature 假数据（后端就绪后删除）
├── shared/              # 跨 feature 复用
│   ├── components/
│   ├── styles/
│   ├── types/
│   └── lib/
└── main.tsx
```

**规则：**

- 新业务优先放进 `features/<name>`，不要往 `App.tsx` 堆逻辑。
- 仅当 ≥2 个 feature 使用时，才上移到 `shared/`。
- 页面只负责组合与交互；请求、任务状态进 hooks / services。

### 后端 `backend/`（后续）

```text
backend/
├── app/
│   ├── api/             # 路由层：薄，只做参数与响应
│   ├── services/        # 业务服务：每个文件开头写清「用途」
│   ├── models/          # 数据模型
│   ├── tasks/           # 异步任务
│   └── core/            # 配置、鉴权、依赖
└── tests/
```

**注释要求（强制，便于二次开发）：**

每个 **模块文件顶部** 与每个 **导出的公开函数/类** 必须用中文写清：

| 字段 | 含义 |
|------|------|
| 模块 | 一句话命名（是什么） |
| 用途 | 解决什么问题、关键行为（做什么） |
| 对接 | 路由 / 前端文件 / 环境变量 / 依赖模块 |
| 二次开发 | 可选：扩展点、禁止事项、迁移注意 |

文件顶示例：

```python
"""
模块：大纲生成服务
用途：根据招标分析结果生成三级标书目录，支持 FREE / ALIGNED 两种模式。
对接：POST /api/outline/generate；前端 useOutlineEditor
二次开发：新模式加枚举，勿改默认 FREE 语义
"""
```

公开函数示例：

```python
def generate_outline(...):
    """
    用途：生成三级目录并落库。
    对接：POST /api/outline/generate
    异常：ProjectNotFoundError
    """
```

前端同理（`/** ... */`）：feature 入口文件、hooks、lib 门面、共享组件均需「模块 / 用途 / 对接」。  
页面组件至少说明本页职责与依赖的 hook/store。

## 命名

| 类型 | 约定 | 示例 |
|------|------|------|
| 组件 | PascalCase | `OutlineEditor.tsx` |
| hooks | camelCase + use | `useTaskProgress.ts` |
| 类型 | PascalCase | `TechnicalPlanStep` |
| 样式类 | kebab 或 BEM 语义 | `.tp-stepper__item` |
| API 路径 | 复数资源 + 动词清晰 | `/api/projects/{id}/tasks` |

## Git

- 提交信息使用**中文**，说明「做了什么 / 为什么」。
- 一次提交聚焦一个主题；前端页面与无关重构勿混提。
- 不要提交 `.env`、密钥、本机绝对路径。

## Mock 与后端切换

- Mock 数据放在 feature 内 `mock.ts`。
- 网络层预留 `shared/lib/api.ts`，通过环境变量 `VITE_API_BASE_URL` 切换。
- 后端接口就绪后：先改 service，不动页面结构。

## UI

- 设计 token 统一在 `shared/styles/tokens.css`，禁止页面硬编码零散色值（特殊装饰除外）。
- 中文界面文案直接写在组件中；后续若要做 i18n 再抽。
