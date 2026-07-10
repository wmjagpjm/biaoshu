# 新会话交接：biaoshu 当前状态与下一步

> **交接日期**：2026-07-09（会话收口，准备换会话）  
> **仓库本地**：`C:\Users\Administrator\biaoshu`  
> **GitHub**：https://github.com/wmjagpjm/biaoshu  
> **分支**：`main`  
> **远程最新提交（会话开始时）**：`57f7aa6` — docs: README 指向 HANDOFF-next  
> **本地状态**：**有大量未提交改动**（见 §8），`main` 与 `origin/main` 对齐后再叠本地工作区  
> **验收基线**：`pytest` **32 passed**；`frontend npm run build` 通过  

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu。仓库 C:\Users\Administrator\biaoshu，请严格按 docs/HANDOFF-next.md 执行。
先 git status 看未提交改动（上一会话未 push）。对话/注释/Commit Message 用简体中文；
遵守 docs/CONTRIBUTING.md 注释四字段（模块/用途/对接/二次开发）。
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

---

## 2. 注释与代码规范（换会话必读）

完整约定见 **`docs/CONTRIBUTING.md`**。摘要：

### 2.1 文件顶与公开 API（强制）

每个模块文件顶部 + 导出的公开函数/类，用中文写清：

| 字段 | 含义 |
|------|------|
| **模块** | 一句话：是什么 |
| **用途** | 解决什么问题、关键行为 |
| **对接** | 路由 / 前端文件 / 依赖模块 / 环境变量 |
| **二次开发** | 可选：扩展点、禁止事项 |

- 后端：`"""..."""`  
- 前端：`/** ... */`  
- 语法标识符保留英文；**禁止大段未翻译英文说明**

### 2.2 本轮已对齐注释的重点模块

| 路径 | 说明 |
|------|------|
| `backend/app/services/task_service.py` | 异步任务、取消、RAG 注入点 |
| `backend/app/services/knowledge_service.py` | 知识库入库/检索 |
| `backend/app/api/knowledge.py` | 知识库路由 |
| `backend/app/services/export_service.py` | Word 导出（编号/列表/表格） |
| `backend/app/api/tasks.py` | 含 cancel |
| `frontend/.../useProjectPipeline.ts` | 轮询+取消 |
| `frontend/.../useKnowledgeBase.ts` | API 优先 |
| `frontend/.../outlineTree.ts` | `markdownToOutline` |
| `frontend/.../ProjectGuidanceCard.tsx` | guidance + kb 范围 |

### 2.3 新会话写代码时

- **新文件**：先写文件顶四字段，再写逻辑  
- **改公开函数**：同步更新「用途/对接」  
- **不要**为注释单独做全仓大重构；但本轮触达文件应保持规范  
- 用户曾认可「就这样」——非触达区可保留简写  

---

## 3. 启动与联调

| 项 | 说明 |
|----|------|
| 一键双启 | 仓库根 `Start-Biaoshu-Dev.bat`（推荐） |
| 分启 | `backend\run-dev.bat` + `frontend\run-dev.bat` |
| 前端 | http://127.0.0.1:5173 （Vite 固定 127.0.0.1:5173） |
| 后端 | http://127.0.0.1:8000/api/health |
| 代理 | `frontend/vite.config.ts`：`/api` → `8000` |
| 清单 | `docs/integration-checklist.md`（含 §7 知识库） |
| 冒烟 | `backend/scripts/smoke_e2e.py`（需先起 uvicorn） |

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\activate
pip install -r requirements.txt   # 仅新环境
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

cd ..\frontend
npm install   # 仅新环境
npm run dev
```

**注意**：旧 SQLite 缺列时 `ensure_schema_columns()` 会 ALTER；知识库新表靠 `create_all`。仍异常可删 `backend/data/*.db` 重建（个人版可接受）。

---

## 4. 已完成能力（勿重复造）

### 4.1 技术标主链路

| 能力 | 说明 |
|------|------|
| 项目 CRUD | `/api/projects` |
| 设置 | Key 明文；`exportFormat` |
| 上传/解析 | project_files + parse 任务；MinerU 回传通道 |
| 结构化分析 | analyze → analysis_json |
| 大纲/章节 | outline / chapter / chapters |
| 任务 | 默认异步轮询；`?sync=true` 测试 |
| **取消** | `POST /api/projects/{id}/tasks/{taskId}/cancel` → `cancelled`（协作式检查点，LLM 中途不能硬杀） |
| **大纲 revise 写回** | `markdownToOutline` +「应用到大纲树」 |
| editor-state | outline/chapters/facts/analysis/guidance/parsedMarkdown |

### 4.2 知识库 RAG 简版

| 项 | 说明 |
|----|------|
| 表 | `kb_folders` / `kb_documents` / `kb_chunks` |
| 磁盘 | `data/knowledge/{workspace_id}/{doc_id}/`（相对 upload 父目录） |
| API | `/api/knowledge/folders`、`docs`、`docs/upload`、`reindex`、DELETE、`docs/move`、`search?q=&folderId=` |
| 检索 | 关键词子串打分；**非向量** |
| 注入 | 仅 **outline / chapter / chapters**；**analyze 不注入** |
| 范围 | guidance：`kbEnabled`、`kbFolderIds`（空=全库） |
| 展示 | 任务 `result.kbCitations` + 工作区任务栏「知识库引用」 |
| 前端 | 文档库走 API；**图片库仍 localStorage** |

### 4.3 导出 Word（ExportFormat）

| 已映射 | 说明 |
|--------|------|
| 页面 | 纸张、方向、边距、首页不同、页眉页脚、页码域 |
| 正文/标题 | 字体、字号、对齐、段前段后、行距、颜色、首行缩进（正文） |
| 章前分页 | `heading_level1_page_break_before` |
| **标题编号** | `numbering_format` / `numbering_template`：`{zh}` `{num}` `{tail}` `{full}` `{circled}` `outline-decimal` |
| **列表** | `list_style` / `ordered_list_style` / `list_indent_chars` |
| **表格** | 评分点表 + 章节 MD 表；`table` 边框/表头/首列/正文格 |

未映射：标题边框、图片样式、Word 原生多级列表 abstractNum。

### 4.4 关键路径索引

```text
backend/app/
  api/knowledge.py          # 知识库路由
  api/tasks.py              # 含 cancel
  services/knowledge_service.py
  services/task_service.py  # 取消 + RAG 注入
  services/export_service.py # 编号/列表/表格
  models/entities.py        # Kb* 三表 + Task cancelled

frontend/src/features/
  knowledge-base/hooks/useKnowledgeBase.ts
  technical-plan/hooks/useProjectPipeline.ts
  technical-plan/hooks/useTechnicalPlanEditors.ts  # replaceOutline
  technical-plan/lib/outlineTree.ts                # markdownToOutline
  technical-plan/components/ProjectGuidanceCard.tsx # kb 范围
  technical-plan/pages/TechnicalPlanWorkspace.tsx   # 取消/写回/引用

backend/tests/
  test_task_cancel.py
  test_knowledge_rag.py
  test_heading_numbering.py
  test_export_list_table.py
```

---

## 5. 明确未完成

| 优先级 | 项 | 现状 |
|--------|----|------|
| 工程 | **本地未 commit / 未 push** | 见 §8，新会话应先处理 |
| 业务 | 商务标六步 | 纯 mock，未接 project/task |
| 业务 | 查重 / 废标引擎 / 标讯 / 资源中心 | mock |
| RAG | 向量 embedding | 未做 |
| 导出 | 标题边框、图片 | 未做 |
| 体验 | SSE 推送 | 仍 1s 轮询 |
| 库 | Alembic | 仅 create_all + 轻量 ALTER |
| 生产 | 登录/多用户/HTTPS/Key 加密/PG/Docker | 未做 |

**粗估**：本机写技术标 ~90%；内网多人 ~30%；公网 SaaS ~15%。

---

## 6. 建议下一会话方向

1. **先收口 Git**：中文 commit（可拆 2～3 个）并 push；再开新功能  
2. **商务标**接同一套 project/task/editor-state（工作量大，1～2 周量级）  
3. **向量检索**或 **导出边框/图片**（打磨）  

默认推荐：**1 提交推送** → 再按产品优先级选 2 或 3。

---

## 7. 验证命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest -q
# 期望：32+ passed

cd ..\frontend
npm run build

git status -sb
# 期望：提交后 main...origin/main 干净；当前会话结束时仍是「脏」工作区
```

---

## 8. 未提交改动清单（换会话必须先看）

上一会话**未执行 git commit / push**。新会话请先：

```powershell
cd C:\Users\Administrator\biaoshu
git status -sb
git diff --stat
```

### 建议提交拆分（中文 Message 示例）

| 建议 commit | 范围 |
|-------------|------|
| `feat: 任务取消与大纲 revise 写回大纲树` | tasks cancel、pipeline、outlineTree、Workspace |
| `feat: 知识库 RAG 简版与生成注入` | knowledge_*、entities Kb*、task 注入、useKnowledgeBase、guidance kb* |
| `feat: 导出标题编号与列表表格样式` | export_service、相关 tests |
| `docs: 更新 HANDOFF-next 与联调清单` | docs/* |

**禁止提交**：`.env`、真实 `sk-`、`*.db`、`uploads/`、`data/`、`node_modules/`、`.venv/`。

---

## 9. 安全

- `.gitignore` 已忽略敏感与本地数据目录  
- 测试可用 `sk-test-plain-key` 类假密钥  
- 知识库文件含历史标书时，生成 system 已要求「勿编造招标未出现的硬指标」；仍勿上传机密到公共仓库  

---

## 10. 旧文档关系

| 文档 | 状态 |
|------|------|
| **docs/HANDOFF-next.md** | **当前有效交接（本文件）** |
| docs/HANDOFF-backend.md | 历史，已过时 |
| docs/integration-checklist.md | 联调操作，含知识库 §7 |
| docs/CONTRIBUTING.md | 目录与**注释强制规范** |
| docs/architecture.md | 目标架构；部分「仅前端」描述已滞后 |

---

## 11. 负责人提示

1. 新会话**只做清单内目标**；勿大改 UI 信息架构。  
2. 先改 **hook / service**，页面只组合。  
3. **注释按 CONTRIBUTING 写清**，并与本 HANDOFF 路径一致。  
4. GitHub 远程若仍停在 `57f7aa6`，以**本地工作区 + 本文件**为准，先 commit 再协作。  

**换会话可直接：pull（若有）→ 处理未提交 → 按 §6 开干。**
