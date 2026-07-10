# 新会话交接：biaoshu（当前有效）

> **交接日期**：2026-07-10（换会话收口）  
> **仓库本地**：`C:\Users\Administrator\biaoshu`  
> **GitHub**：https://github.com/wmjagpjm/biaoshu  
> **分支**：`main`  
> **远程 HEAD**：`01f1005` — feat: 知识库混合向量检索（本地哈希+可选 API）  
> **本地状态**：与 `origin/main` 对齐（交接时以 `git status` 为准）  
> **验收基线**：`pytest` **44 passed**；`frontend npm run build` 通过  

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu。仓库 C:\Users\Administrator\biaoshu，请严格按 docs/HANDOFF-next.md 执行。
先 git status；对话/注释/Commit Message 一律简体中文。
【强制】遵守注释四字段：模块 / 用途 / 对接 / 二次开发（见本文 §2 与 docs/CONTRIBUTING.md）。
新写或大改的文件必须先补齐文件顶注释再合入；交接时必须更新「注释齐备表」。
用户自备 API Key，禁止把密钥写进仓库。
启动：仓库根 Start-Biaoshu-Dev.bat；或 backend/run-dev.bat + frontend/run-dev.bat。
```

---

## 1. 产品定位（锁定，勿擅自改）

| 项 | 决策 |
|----|------|
| 形态 | **Web 自托管**；非 Electron |
| 账号 | 个人版一账号 ≈ 一 `workspace`（默认 `ws_local`） |
| Key | 用户自备；**保密机允许明文存/回显**（勿擅自改加密） |
| 参考 | C 端 OpenBidKit **只参考交互，勿抄 AGPL 源码** |
| 语言 | 对话 / **代码注释** / Commit Message = **简体中文** |
| 目标图 | `docs/diagrams/target-roadmap.svg` |
| 架构图 | `docs/diagrams/architecture-current.svg` |

---

## 2. 代码注释规范（强制 · 换会话必读）

> **凡生成或更新交接文档，必须单独强调本节，并更新 §2.3 注释齐备表。**  
> 完整原文：`docs/CONTRIBUTING.md`（「注释要求」+「换会话 / 交接时的注释约定」）。

### 2.1 四字段定义（缺一不可）

每个 **模块文件顶部**，以及 **导出的公开函数/类**，用中文写清：

| 字段 | 含义 | 是否强制 |
|------|------|----------|
| **模块** | 一句话：是什么 | 文件顶强制 |
| **用途** | 解决什么问题、关键行为 | 文件顶 + 公开 API 强制 |
| **对接** | 路由 / 前端路径 / 依赖服务 / 环境变量 | 有外部依赖时强制 |
| **二次开发** | 扩展点、禁止事项、迁移注意 | 核心服务/入口强制；工具函数可选 |

**写法：**

- 后端：`""" ... """`（文件顶模块 docstring）  
- 前端：`/** ... */`  
- 标识符、路径、异常类名可英文；**禁止大段英文说明**  
- 注释描述**当前真实行为**，禁止「假装已完成」的 TODO 式注释  

**文件顶示例（后端）：**

```python
"""
模块：大纲生成服务
用途：根据招标分析生成三级目录，支持 FREE / ALIGNED。
对接：POST /api/...；前端 useXxx
二次开发：新模式加枚举，勿改默认 FREE 语义
"""
```

**文件顶示例（前端）：**

```ts
/**
 * 模块：技术方案工作区
 * 用途：六步流水线 + 异步任务 + 反馈修订。
 * 对接：useProjectPipeline、editor-state API
 * 二次开发：勿在页面堆业务；逻辑进 hooks
 */
```

### 2.2 新会话写代码时的铁律

1. **新文件**：先写齐文件顶四字段（至少模块/用途/对接），再写逻辑。  
2. **改公开函数**：同步改「用途/对接」。  
3. **本轮触达文件**：合入前自检文件顶是否齐全。  
4. **非触达历史文件**：可不顺手全仓扫注释；但若打开大改，顺手补齐。  
5. **禁止**为「好看」做与需求无关的全仓注释重构。  
6. **写完能力更新 HANDOFF 时**：必须在 §2.3 表更新该模块注释状态。

### 2.3 功能注释齐备表（交接审计 · 2026-07-10）

图例：**齐** = 文件顶含模块+用途+对接（核心服务另有二次开发）；**部分** = 有用途但缺对接/二次开发或仅部分文件；**弱/无** = 缺文件顶或仅零星行内注释。

#### 后端 `backend/app`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 应用入口 | `main.py` | **齐** | 含二次开发 |
| 项目 CRUD | `services/project_service.py`、`api/projects.py` | **齐** | kind/business 已写清 |
| 任务引擎 | `services/task_service.py`、`api/tasks.py` | **齐** | 取消、biz 分发、RAG 注入 |
| 商务任务 | `services/business_task_service.py` | **齐** | qualify/toc/quote/commit |
| 编辑态 | `services/editor_state_service.py` | **齐** | business_json |
| 知识库 | `services/knowledge_service.py`、`api/knowledge.py` | **齐** | 混合检索 |
| 向量 | `services/embedding_service.py` | **齐** | 本地哈希 + 可选 API |
| 导出 | `services/export_service.py` | **齐** | 注明边框/图片未做 |
| 修订 | `services/revise_service.py` | **齐** | 商务结构化写回 |
| 查重 | `services/duplicate_service.py`、`api/compliance.py` | **齐** | |
| 废标 | `services/rejection_service.py` | **齐** | |
| 相似度 | `services/text_similarity.py` | **齐** | |
| LLM | `services/llm_service.py` | **齐** | |
| 设置 | `services/settings_service.py`、`api/settings.py` | **部分** | 文件顶有；embedding_model 已在模型/schema 体现 |
| 解析/文件 | `parse_service` / `file_service` | **齐** | |
| 实体 | `models/entities.py` | **部分** | 类 docstring 齐；文件顶视历史版本 |
| 测试 | `backend/tests/*.py` | **齐/部分** | 多数模块 docstring 含用途 |

#### 前端 `frontend/src/features`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 技术标工作区 | `technical-plan/pages/TechnicalPlanWorkspace.tsx` | **齐** | |
| 技术标 hooks | `useProjectPipeline` / `useTechnicalPlanEditors` / `useProjectGuidance` | **齐** | |
| projectStore | `technical-plan/lib/projectStore.ts` | **齐** | kind 过滤 |
| outlineTree | `technical-plan/lib/outlineTree.ts` | **齐** | markdownToOutline |
| 商务标 | `business-bid/pages/*`、`hooks/useBusinessBidWorkspace.ts` | **齐** | 空态/API |
| 知识库 | `knowledge-base/hooks/useKnowledgeBase.ts`、pages | **齐** | |
| 查重 | `duplicate-check/pages`、`types.ts` | **齐** | 已接 API |
| 废标 | `rejection-check/pages`、`types.ts` | **齐** | 已接 API |
| 设置 | `settings/hooks`、`pages`、`types` | **齐** | embeddingModel 字段 |
| 创建/首页 | `create`、`home` | **齐** | |
| 导出模板 | `export-format/*` | **齐** | |
| 本地解析 | `local-parser` | **齐** | |
| 标讯 | `bid-opportunity` | **齐** | 仍 mock，注释已标明 |
| 资源中心 | `resources` | **齐** | 仍 mock/可远程 URL |
| shared/api | `shared/lib/api.ts` | **齐** | |
| shared 杂项 | `siteBackground` 等 | **部分** | 缺「对接」字段 |

#### 仍偏 mock、注释已标明「二期」的模块

- 标讯 `bid-opportunity`  
- 资源中心 `resources`（可配 `VITE_RESOURCES_URL`）  
- 图片库部分仍 localStorage（知识库文档已 API）

**结论**：主链路（技术标/商务标/任务/知识库/查重/废标/导出/设置）**文件顶注释整体达标**；新会话**不得降低标准**。历史 mock 页允许「对接：二期」，但不可无文件顶。

---

## 3. 启动与联调

| 项 | 说明 |
|----|------|
| 一键双启 | 仓库根 `Start-Biaoshu-Dev.bat` |
| 前端 | http://127.0.0.1:5173 |
| 后端 | http://127.0.0.1:8000/api/health |
| 代理 | Vite `/api` → `8000` |
| 联调清单 | `docs/integration-checklist.md` |
| 图 | `docs/diagrams/*` |

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\activate
.\.venv\Scripts\python -m pytest -q
# 期望：44+ passed

cd ..\frontend
npm run build
```

**注意**：旧 SQLite 缺列时 `ensure_schema_columns()` 会 ALTER（含 `embedding_json`、`kind`、`business_json` 等）。异常可删 `backend/data/*.db` 重建。

---

## 4. 已完成能力（勿重复造）

### 4.1 技术标

项目 CRUD、上传/解析、分析、大纲/章节、全书空章、任务异步+取消、revise、editor-state、Word 导出（编号/列表/表格）、guidance、知识库注入（outline/chapter）。

### 4.2 商务标

`kind=business`、editor-state 商务字段、`biz_*` 任务、export mode=business、revise 结构化写回、空态不回填 mock。

### 4.3 知识库 RAG

入库分块、**混合检索**（关键词 + 本地哈希向量；可选 `embeddingModel` API）、生成注入、`kbCitations`。

### 4.4 合规

- 查重：`POST /api/projects/{id}/duplicate-check`  
- 废标：`POST /api/projects/{id}/rejection-check`  

### 4.5 路径索引

```text
backend/app/
  api/compliance.py knowledge.py tasks.py projects.py settings.py
  services/
    task_service.py business_task_service.py knowledge_service.py
    embedding_service.py duplicate_service.py rejection_service.py
    export_service.py revise_service.py editor_state_service.py
    text_similarity.py

frontend/src/features/
  technical-plan/  business-bid/  knowledge-base/
  duplicate-check/  rejection-check/  settings/
```

---

## 5. 明确未完成

| 优先级 | 项 | 现状 |
|--------|----|------|
| 导出 | 标题边框 `heading_border`、正文图片样式 | 未映射 |
| 体验 | SSE 替代 1s 轮询 | 未做 |
| 业务 | 标讯 / 资源中心真 API | mock |
| RAG | 真语义大模型 embedding 调优 | 有本地+可选 API，可继续增强 |
| 库 | Alembic | 仅 create_all + ALTER |
| 生产 | 登录/多用户/HTTPS/Key 加密/PG/Docker | 未做 |

**粗估**：技术标 ~90%；商务 ~80%；合规工具可用；内网多人 ~30%；公网 SaaS ~15%。

---

## 6. 建议下一会话方向

1. **导出**标题边框 / 图片（打磨）  
2. **SSE** 任务进度（体验）  
3. 标讯/资源后端化  
4. 内网多人底座（登录 · PG）—— 工作量大，单独立项  

默认：先 1 或 2，保持注释规范。

---

## 7. 安全

- 禁止提交：`.env`、真实 Key、`*.db`、`uploads/`、`data/`、`node_modules/`、`.venv/`  
- 测试用假 Key 如 `sk-test-plain-key`  
- 生成/知识库提示已要求勿编造招标未出现的硬指标  

---

## 8. 旧文档关系

| 文档 | 状态 |
|------|------|
| **docs/HANDOFF-next.md** | **当前有效交接（本文件）** |
| docs/CONTRIBUTING.md | 注释与目录强制规范 |
| docs/integration-checklist.md | 联调步骤 |
| docs/diagrams/ | 架构图 + 目标图 |
| docs/HANDOFF-backend.md | 历史，过时 |

---

## 9. 换会话时交接文档必须包含（模板清单）

以后**每一份**交接 / HANDOFF 更新，至少包含：

1. 仓库地址、分支、HEAD、pytest 基线  
2. **复制即用的第一句提示词**（含注释四字段强制句）  
3. **注释规范专章**（四字段表 + 铁律）  
4. **按功能域的注释齐备表**（齐/部分/弱）  
5. 已完成 / 未完成 / 下一步  
6. 启动与验证命令  
7. 安全与禁止提交项  

缺第 3、4 条视为交接不合格，新会话应先补文档再写功能。

---

## 10. 负责人提示

1. 只做清单内目标；勿大改 UI 信息架构。  
2. 先改 hook / service，页面只组合。  
3. **注释与 HANDOFF 路径表保持一致。**  
4. 远程以 GitHub `main` 为准；有本地脏文件先 `git status`。  

**换会话可直接：pull → 读本文 §0～§2 → 按 §6 开干。**
