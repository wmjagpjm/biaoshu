# 新会话交接：biaoshu（当前有效）

> **交接日期**：2026-07-14（P9A/P9B/P9C、P10A、P10B 与 **P10C 财务成本草案/毛利快照**已完成；提交以本分支 HEAD 为准）
> **仓库本地**：`C:\Users\Administrator\biaoshu`
> **GitHub**：https://github.com/wmjagpjm/biaoshu
> **当前工作分支**：`collab/grok-code-codex-review`（协作分支；**勿直接当 main**）
> **协作分支已推送功能基线**：P10C 后端=`6f30084`、前端=`737c7db`；P9C 最新代码为 `585e502`（合成评测与本地预检），前序为后端=`cc0d217`、前端=`a0bd84b`、运行时降级=`71c503c`；P9B 前序为解析=`45d7214`、数据域=`1c46e41`、Excel=`6491363`、同步=`229f1d7`、人工接受=`000b403`、界面=`a7cfcb8`。更早的审计基线为 `a1ba88a`，其下含 P9A、包 5 至包 8 和阶段 3。新会话必须以 `git rev-parse HEAD` 与远端分支一致为准。
> **参考 `origin/main`**：`4847a9d` — docs: 重写换会话交接并强制注释规范专章（非当前工作 HEAD）
> **本地状态**：P10A 已完成并推送：本机 scrypt 身份、HttpOnly 会话、成员/所有者保护、默认拒绝角色、设置 owner 收口、认证模式握手、前端会话门禁与刷新后 CSRF 续发。P10B 已完成：strict `finance` 只能查看当前工作空间商务标报价白名单投影。P10C 已完成：严格财务成员可维护人工成本条目并查看分精度毛利快照；成本不写回报价、审计不含正文、默认业务仍拒绝。P9C 的真实模型缓存仍未准备；预检受控返回 `model_unavailable`，因此搜索保持关键词降级，不能声称真实语义效果已验证。
> **验收基线**：后端全量 **314 passed**（按串行分组，1 条既有 Starlette/httpx 弃用警告）；P10C 成本 E2E **4 passed**、P10B 财务 E2E **7 passed**、P10A 认证 E2E **11 passed**、P9C 语义索引 E2E **9 passed**、知识卡片 E2E **1 passed**；`frontend npm run lint` / `build`；`git diff --check`。

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu 标书制作者剩余主线任务。仓库 C:\Users\Administrator\biaoshu，GitHub https://github.com/wmjagpjm/biaoshu.git。
工作分支只能是 collab/grok-code-codex-review，禁止直接操作 main；先执行 git status -sb，并核对 HEAD 与 origin/collab/grok-code-codex-review 一致且工作区干净。
完整阅读 docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md、docs/integration-checklist.md。
长期目标：持续完成卡片化知识与素材库、多模板融合与可控 AI 编写、质量与交付闭环；每包必须独立规划、限定实现、Codex 审查与独立验收、中文文档闭环、推送协作分支。
当前进度：P9A、P9B、P9C、P10A、P10B 与 P10C 均已完成各自计划内的实现、独立自动化验收、中文文档闭环与协作分支推送。P10A 固定契约见 `docs/p10a-local-identity-rbac-contract.md`，P10B 见 `docs/p10b-finance-business-quote-contract.md`，P10C 见 `docs/p10c-finance-cost-draft-contract.md`。P9C 仍仅允许纯离线 BAAI/bge-small-zh-v1.5、512 维、CPU、版本并存和可见关键词降级；正文/查询不得出域。
下一步：如扩展财务税务/审批/导出、人员资质/团队推荐或投标人匿名预览/版本/合规，必须先独立冻结数据来源、精度、审计与权限矩阵，不能按前端路径猜测授权，也不得把 P10C 路径扩为通用财务系统。除 P10B/P10C strict `finance` 能力外，P10A 的 `finance`、`hr`、`bidder` 继续默认拒绝既有业务。真实模型缓存或依赖尚未准备时，也不得安装、下载或伪造 P9C 指标；只有用户在受控运行时显式构建并通过固定预检后，才记录语义索引已就绪。
对话/注释/Commit Message 一律简体中文。
【强制】遵守注释四字段：模块 / 用途 / 对接 / 二次开发（见本文 §2 与 docs/CONTRIBUTING.md）。
新写或大改的文件必须先补齐文件顶注释再合入；交接时必须更新「注释齐备表」。
新增或修改 PowerShell 脚本后必须转换为 UTF-8 BOM 编码。
用户自备 API Key，禁止把密钥写进仓库。
启动：仓库根 Start-Biaoshu-Dev.bat；或 backend/run-dev.bat + frontend/run-dev.bat。四个启动脚本均后台静默执行，不等待、不弹浏览器、不重复拉起已监听端口。
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
7. **PowerShell 脚本**：新增或修改后必须为 UTF-8 BOM，避免 Windows PowerShell 解析中文注释或字符串异常。

### 2.3 功能注释齐备表（交接审计 · 2026-07-11）

图例：**齐** = 文件顶含模块+用途+对接（核心服务另有二次开发）；**部分** = 有用途但缺对接/二次开发或仅部分文件；**弱/无** = 缺文件顶或仅零星行内注释。

#### 后端 `backend/app`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 应用入口 | `main.py` | **齐** | 含二次开发 |
| 项目 CRUD | `services/project_service.py`、`api/projects.py` | **齐** | kind/business 与 editor-state responseMatrix 映射已写清 |
| 任务引擎 | `services/task_service.py`、`api/tasks.py` | **齐** | 取消、biz 分发、RAG 注入、SSE 短 Session；含 `content_fuse` |
| 模板/卡片融合 M3-A | `services/fuse_context_service.py`、`services/task_service.py`（content_fuse）、`tests/test_content_fuse.py` | **齐** | 只读建议；禁写 editor-state；跨 workspace 不泄漏；sourceRefs 含服务端 title；裁剪后 *Used |
| 商务任务 | `services/business_task_service.py` | **齐** | qualify/toc/quote/commit |
| 编辑态 | `services/editor_state_service.py` | **齐** | business_json、response_matrix_json 规范化与死引用收敛 |
| 响应矩阵 | `services/editor_state_service.py`、`services/task_service.py`、`api/projects.py`、`api/tasks.py`、`services/export_service.py`、`models/entities.py`；前端 `useTechnicalPlanEditors` / `ResponseMatrixPanel` | **齐/部分** | service/API/导出与乐观锁注释齐；`response_match` 支持 `candidateBatchIndex` 候选分批且只产出待确认建议；前端冲突 UX 与串行分批进度注释齐；`entities.py` 仍按历史文件部分 |
| 知识库 | `services/knowledge_service.py`、`api/knowledge.py` | **齐** | P9C 版本化离线索引、工作空间隔离、关键词降级；`get_chunk` 供卡片沉淀 |
| 知识卡片 | `services/card_service.py`、`api/cards.py`、`models/entities.py`（KnowledgeCardRow） | **齐** | 独立 knowledge_cards；列表摘要/详情；from-chunk/from-project-image；insert-card → biaoshu-image |
| 向量与预检 | `services/embedding_service.py`、`scripts/semantic_model_preflight.py` | **齐** | 固定离线 BGE、显式重建加载、512 维、固定合成评测与本地只读预检；旧哈希不参与 P9C 语义检索 |
| 导出 | `services/export_service.py` | **齐** | 标题段落边框/分级底色、叶子标题左栏、项目内正文图片嵌入与无效引用降级已做 |
| 修订 | `services/revise_service.py` | **齐** | 商务结构化写回 |
| 查重 | `services/duplicate_service.py`、`api/compliance.py` | **齐** | |
| 废标 | `services/rejection_service.py` | **齐** | |
| 相似度 | `services/text_similarity.py` | **齐** | |
| LLM | `services/llm_service.py` | **齐** | |
| 设置 | `services/settings_service.py`、`api/settings.py` | **部分** | 文件顶有；embedding_model 已在模型/schema 体现 |
| 解析/文件 | `parse_service` / `file_service` / `api/files.py` | **齐** | `source`/`image` 角色隔离、Pillow 校验（公开 `verify_image_content`）和项目内安全读取 |
| 本地标讯库与国能追踪 | `services/opportunity_service.py`、`api/opportunities.py`、`services/opportunity_watch_service.py`、`api/opportunity_watch.py` | **齐** | 本地 CRUD、服务端状态、跨 workspace 404、离线 CSV/JSON；P9B 另含固定来源、内存 Excel、人工接受和不存敏感网络数据 |
| 资源中心 | `services/resource_service.py`、`resource_sync_service.py`、`api/resources.py` | **齐** | 全局系统只读资源、workspace 用户资源、服务端原子浏览量、签名清单受控同步与来源审计 |
| 中标内容模板 | `services/template_service.py`、`api/templates.py`、`models/entities.py`（BidTemplateRow） | **齐** | workspace 快照沉淀/列表摘要/详情快照/删除/从模板新建；源项目 SET NULL；空大纲与超大快照 400 |
| 实体 | `models/entities.py` | **部分** | 类 docstring 齐；文件顶视历史版本；KnowledgeCardRow / BidTemplateRow 已补语义 |
| 测试 | `backend/tests/*.py` | **齐/部分** | 含 `test_content_fuse`、`test_knowledge_cards`、`test_bid_templates` 及标题边框/SSE/标讯/资源/响应矩阵等 |

#### 前端 `frontend/src/features`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 技术标工作区 | `technical-plan/pages/TechnicalPlanWorkspace.tsx` | **齐** | ResponseMatrixPanel；串行 `response_match`；编写步 M3-A/M3-B 融合入口 |
| 模板/卡片融合 UI | `technical-plan/components/ContentFuseDialog.tsx`、`lib/contentFuse.ts`；E2E `e2e/content-fuse-suggest.spec.ts`、`content-fuse-apply.spec.ts` | **齐** | M3-A 只读建议；M3-B 双栏预览/勾选确认写入/base 漂移跳过；`test:e2e:fuse` / `fuse-apply` |
| 技术标 hooks | `useProjectPipeline` / `useTechnicalPlanEditors` / `useProjectGuidance` | **齐** | SSE、项目切换隔离、取消终态保护、正文图片上传、responseMatrix；TaskType 含 content_fuse |
| 响应矩阵 | `technical-plan/lib/responseMatrix.ts`、`hooks/useTechnicalPlanEditors.ts`、`components/ResponseMatrixPanel.tsx`、`pages/TechnicalPlanWorkspace.tsx`；E2E conflict/refresh/suggest-apply/source-pagination/field-merge | **齐** | sourceKey 合并、跨批建议择优、409 字段级三方合并预览、仅矩阵 PUT、双 context E2E |
| projectStore | `technical-plan/lib/projectStore.ts` | **齐** | kind 过滤 |
| outlineTree | `technical-plan/lib/outlineTree.ts` | **齐** | markdownToOutline |
| 商务标 | `business-bid/pages/*`、`hooks/useBusinessBidWorkspace.ts` | **齐** | 空态/API |
| 财务报价/成本 P10B/P10C | `services/finance_service.py`、`finance_cost_service.py`、`api/finance.py`；前端 `features/finance/*`、`e2e/finance-*.spec.ts` | **齐** | strict `finance` 当前空间报价白名单、人工成本草案和毛利快照；整数分、审计脱敏、无税务/审批/导出；`npm run test:e2e:finance-role` / `finance-cost-draft` |
| 知识库/卡片 | `knowledge-base/**`（useKnowledgeCards、cardsApi、KnowledgeBasePage）、`ChapterEditor`/`InsertCardDialog`；E2E `e2e/knowledge-cards.spec.ts` | **齐** | 图片 Tab 后端化；章节插入卡片；`npm run test:e2e:cards` |
| 查重 | `duplicate-check/pages`、`types.ts` | **齐** | 已接 API |
| 废标 | `rejection-check/pages`、`types.ts` | **齐** | 已接 API |
| 设置 | `settings/hooks`、`pages`、`types` | **齐** | embeddingModel 字段 |
| 创建/首页 | `create`、`home` | **齐** | |
| 导出模板 | `export-format/*` | **齐** | 标题边框与叶子标题左栏控件、实时预览已补齐 |
| 本地解析 | `local-parser` | **齐** | |
| 标讯 | `bid-opportunity`、`e2e/opportunity-watch-chnenergy.spec.ts` | **齐** | 已接本地标讯库 API 与 CSV/JSON 离线导入；P9B 面板只访问 `/api`，无浏览器外网请求 |
| 资源中心 | `resources` | **齐** | 已接 API；页面逻辑在 `hooks/useResources.ts`，无浏览器远程 URL |
| 中标内容模板 | `bid-templates/*`、工作区沉淀入口、E2E `e2e/bid-template-reuse.spec.ts` | **齐** | 与导出版式模板（export-format）分离；`npm run test:e2e:templates` |
| shared/api | `shared/lib/api.ts` | **齐** | |
| shared 杂项 | `siteBackground` 等 | **部分** | 缺「对接」字段 |

#### 仍偏 mock、注释已标明「二期」的模块

- 知识库文档在 API 失败时仍可回退 localStorage mock；图片/素材卡片已改为后端 `/api/cards`，不再依赖 localStorage 存图。

**结论**：主链路（技术标/商务标/任务/知识库/卡片/查重/废标/导出/设置）**文件顶注释整体达标**；新会话**不得降低标准**。历史 mock 页允许「对接：二期」，但不可无文件顶。

---

## 3. 启动与联调

| 项 | 说明 |
|----|------|
| 一键双启 | 仓库根 `Start-Biaoshu-Dev.bat`（后台静默，不自动打开浏览器） |
| Grok-Codex 协作 | `tools/agent-collaboration/Connect-Grok.ps1`（发送 `ready` 并读取 Codex 待办） |
| 前端 | http://127.0.0.1:5173 |
| 后端 | http://127.0.0.1:8000/api/health |
| 代理 | Vite `/api` → `8000` |
| 联调清单 | `docs/integration-checklist.md` |
| 图 | `docs/diagrams/*` |

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\activate
.\.venv\Scripts\python -m pytest -q
# 当前完整串行基线：314 passed（1 条既有 Starlette/httpx 弃用警告）

cd ..\frontend
npm run lint
npm run build
# 响应矩阵双浏览器 E2E（独立 8010/5174 + biaoshu-e2e.db；首次需 npx playwright install chromium）
npm run test:e2e:matrix
```

**已知 lint 状态**：`npm run lint` **已通过**（**0 errors、0 warnings**）。此前 Hooks 误判（`useApiProjects` / `useApiSettings` → `shouldUseApiProjects` / `shouldUseApiSettings`）与 5 条既有 warnings（`BusinessStepStepper` / `StepStepper` 的 `only-export-components`、`useSiteBackground` 的 `exhaustive-deps`、`ChapterEditor` / `useTechnicalPlanEditors` 的 `no-useless-escape`）已在**独立 lint-only 任务**中清理完毕；`npm run build` 通过 TypeScript 类型检查。

**代理分工**：Grok 负责限定范围内的代码与测试落地；Codex 负责把任务写入 `.agent-collaboration/messages/codex-to-grok.jsonl`、审查 Grok 的 diff、运行验收并回复结果。Grok 接入命令见 `docs/agent-collaboration.md`；运行时消息目录已被 Git 忽略，禁止写入 API Key、令牌或真实密钥。

**注意**：旧 SQLite 缺列时 `ensure_schema_columns()` 会 ALTER（含 `embedding_json`、`kind`、`business_json`、`response_matrix_json` 等）。异常可删 `backend/data/*.db` 重建。

---

## 4. 已完成能力（勿重复造）

### 4.1 技术标

项目 CRUD、上传/解析、分析、大纲/章节、全书空章、任务异步+取消、SSE 进度（失败后 2 秒 GET 回退）、revise、editor-state、Word 导出（编号/列表/表格/标题段落边框与分级底色/项目内正文图片）、guidance、知识库注入（outline/chapter）。

响应矩阵 v1：editor-state 已持久化 `responseMatrix`，覆盖技术要求/评分点到大纲节点和章节正文的手工映射。前端用稳定 `sourceKey` 合并分析结果，避免要求重排后错绑；前后端都会过滤已删除的大纲/章节引用，非 `waived` 且无有效链接时降级为 `uncovered`。`responseMatrix: null` 视为不更新，显式 `[]` 才清空。技术标 Word 导出会再次收敛失效引用，并在“六、响应矩阵”中按模板表格样式输出类型、来源、权重、响应状态、当前关联位置和备注；不输出内部 ID，商务标不含该章节。

智能建议（已实现候选分批 + 来源分页）：`response_match` 使用用户已配置模型生成**待确认**建议，结果仅在任务中返回，**绝不**直接写 `editor-state`/`responseMatrix`。`payload.sourceBatchIndex` 与 `payload.candidateBatchIndex`（缺省/非法/负值→0；越界→任务 failed，模型 0 次）共存：非 waived 来源按 80 分页（`prompt_sources = sources[i*80:(i+1)*80]`），章节每批 120、大纲每批 160；`sourceBatchCount = ceil(来源数/80)`，`candidateBatchCount = max(章批数, 大纲批数, 1)`。result 含 `sourceBatchIndex/Count`、`isLastSourceBatch`、`sourceCount`（本页条数）、`totalSourceCount` 与候选批元数据。前端 **外层来源页 × 内层候选批 await 串行**，仅当末来源页且末候选批才停止；按 `sourceKey` 累计（confidence 高优先，平手关联数多优先，整条择优、禁止字段级合并）；展示「来源页/候选批/累计建议数」；失败或取消即停并保留已成功批；会话/代次保护避免项目切换、取消、重入后的迟到污染。人工应用仍：勾选、`base` 快照跳过已改行、`waived`/notes 保护、关联并集、仅 `uncovered` 可被建议改状态。旧客户端不传 source 批号等价来源页 0。

多端冲突（已实现）：GET/PUT 均返回稳定的 `responseMatrixVersion`（仅对收敛后矩阵内容哈希，空矩阵亦有版本；改概述/正文/updatedAt 不改变版本）。PUT 同时带 `responseMatrix` + `responseMatrixVersion` 时先取 **DB 写锁**（SQLite：projects 行无副作用 UPDATE；PostgreSQL：`SELECT … FOR UPDATE`）再比对，版本不匹配返回 **409**，`detail` 含 `message`、`responseMatrix`、`currentResponseMatrixVersion`，**整包不写**；同 expected version 并发 PUT 恰一成一败。不带版本的旧客户端仍可写矩阵。前端 hook **串行**版本化矩阵保存（飞行中不发下一带矩阵 PUT，完成后用新版本+最新 state），409 时保留本地矩阵、停止旧版本重试，面板「重新载入远端矩阵」显式恢复；无静默强制覆盖。

字段级三方合并（包 7 MVP，**已完成并推送** SHA=`2c7b3e0`，提交标题「实现响应矩阵字段级三方合并」）：成功 GET / 成功带矩阵 PUT / 显式载入远端时深拷贝 `matrixBase`+`baseVersion`；409 且 baseVersion=请求版本且请求后本地未再改时，对 `notes`/`status`/`chapterIds`/`outlineNodeIds` 做原子三方比较（数组去重排序；notes 不 trim；禁止并集/deep-merge）。无冲突展示「可安全合并」；有冲突逐字段 base/local/remote 对照，须显式「采用本地/远端」后「应用合并」可用。应用 PUT **仅** `responseMatrix`+`responseMatrixVersion`（远端版本）；再次 409/网络失败不自动循环；合并成功后跳过一次全量 autosave；项目切换丢弃过期合并。合并后跑既有 reconcile。智能建议语义不变。

**浏览器 E2E（`npm run test:e2e:matrix`）已覆盖**：双 context 409 主路径；「刷新来源」按 `sourceKey` 保留人工映射；**智能建议人工确认**；**来源 80 分页**；**字段级三方合并**（无冲突安全合并、同字段冲突显式选择、二次 409 不循环；`response-matrix-field-merge.spec.ts`）。

正文图片 v1：`project_files.role=source|image`；`/files` 与 parse 只处理 source，`/images` 只处理 PNG/JPEG/GIF（5 MiB、像素和数量限制）。SQLite 个人版在当前项目行写锁内完成图片计数和保存，避免并发绕过上限；未来迁移 PostgreSQL/多进程时必须另行实现等价的行锁或原子计数。正文只接受独占行 `![替代文字](biaoshu-image://file_<16位十六进制> "题注")`，导出按当前 workspace、项目和 `role=image` 二次校验；无效引用显示 warning，不读取外链、任意路径或项目外文件。

### 4.2 商务标

`kind=business`、editor-state 商务字段、`biz_*` 任务（复用技术标 SSE 进度与回退）、export mode=business（含标题段落边框）、revise 结构化写回、空态不回填 mock。

### 4.3 知识库 RAG

入库分块、**混合检索**（关键词 + 本地哈希向量；可选 `embeddingModel` API）、生成注入、`kbCitations`。

### 4.4 合规

- 查重：`POST /api/projects/{id}/duplicate-check`  
- 废标：`POST /api/projects/{id}/rejection-check`  

### 4.5 本地标讯库

`bid_opportunities` 是 workspace 内本地维护的线索库，支持 `GET/POST /api/opportunities`、`GET/PATCH/DELETE /api/opportunities/{id}` 与 `POST /api/opportunities/{id}/projects`。`deadline` 在服务端计算 `open`、`closing_soon`、`closed`，关闭标讯不可立项；立项在单次事务中创建 `technical` 项目并写入 `projects.source_opportunity_id`。删除标讯仅清空该弱关联，不删除项目及其产物。

离线导入：`POST /api/opportunities/import` 接收本机 UTF-8 CSV 或 JSON（默认不超过 2 MiB/2,000 行，由 `MAX_OPPORTUNITY_IMPORT_BYTES`、`MAX_OPPORTUNITY_IMPORT_ROWS` 配置）。应用不持久化原始文件；任一行非法时返回行号明细并零写入。可选 `sourceKey` 在同 workspace 幂等跳过，未提供来源键的行按新记录导入；不接受 URL、RSS、附件、密钥或客户端伪造的 workspace。标讯页的导入弹层直接显示成功统计和逐行错误。

新工作空间默认保持为空；只有在本地演示时显式配置 `SEED_SAMPLE_OPPORTUNITIES=true`，启动时才写入两条标注为“本地示例”的演示标讯。页面不再以内置 mock 兜底，接口异常会明确显示错误。当前 `X-Workspace-Id` 仅是个人版开发期工作空间选择，不构成多用户鉴权。

**P9B 国能计划追踪（已验收）**：另有 `/api/opportunity-watch` 数据域，浏览器仍只访问本机 `/api`。用户上传 `.xlsx` 后，服务端仅按固定国能 e 招主机/请求和 120 条计划、每计划 5 个候选、50 页详情、1 秒间隔执行受控读取；命中的公告链接动态生成且不入库。只有具有完整北京时间截止时间的 `resolved` 命中，才能由用户点击加入本地标讯；绝不自动立项。完整安全边界、错误码、验收和非目标见 `docs/p9b-chnenergy-integration-contract.md`。

### 4.6 资源中心

资源中心已接本地 API：`GET/POST /api/resources`、`GET/PATCH/DELETE /api/resources/{id}`、`POST /api/resources/{id}/view`、`GET /api/resources/sync-sources`。现有六条精选内容作为 `source=system` 的全局只读记录启动期幂等写入，`workspaceId=null`，不会写入任何用户 workspace；`source=user` 记录只能被当前 workspace 读取和维护。浏览量使用数据库表达式原子加一，且不修改 `updatedAt`，避免阅读改变资源排序。新库由 CHECK 约束保证来源与 workspace 一致；已存在的 SQLite 资源表会在启动期补同语义触发器。

受控同步 v1：管理员在 `backend/.env` 用 `RESOURCE_SYNC_SOURCES` 配置签名 HTTPS 清单及其 Ed25519 **公钥**，再执行 `python scripts/sync_resources.py`。默认来源为空且不发网络请求；浏览器无同步 POST、无 URL 入参。请求要求 HTTPS/443、精确主机白名单、公共 IP DNS、固定 IP TLS/SNI、无重定向、无压缩、响应上限；仅验签后的白名单 Markdown 字段可写入新的 `source=system` 资源。来源 URL、公钥指纹、版本/摘要与运行审计在独立同步表中，`ResourceRow` 不存 URL、密钥或同步状态；API 和命令均不回显 URL、公钥、远端正文或原始错误。详见 `docs/resource-sync-manifest.md`。

前端不再使用 `mock.ts`、`VITE_RESOURCES_URL` 或浏览器远程请求。正文 Markdown 仅以 React 文本节点和 `<pre>` 展示，不渲染 HTML。外部标讯抓取、RSS、附件、版本历史、应用内定时器、同步 Token/Cookie 与浏览器同步触发均不在本轮范围。Grok CLI 通过代理恢复后完成只读复审：首轮指出 `tags` 静默截断/去重及并发旧版本覆盖新版本两个 P1；Codex 已修复并补测试，Grok 二次确认“未发现 P0/P1，上一轮两个 P1 已修复”。剩余 P2：陈旧同步失败会把来源 `last_status` 记为 `failed`，当前语义为“最近一次尝试状态”，不是“最新成功数据不可用”。

### 4.7 中标内容模板（阶段 1 MVP）

独立表 `bid_templates`（**非**导出版式模板 / `export_format`）。API：`POST /api/templates/from-project`（仅 technical、深拷贝 outline/chapters，可选 facts/guidance/mode；响应含完整 snapshot）、`GET /api/templates`（**列表摘要**：元数据 + `chapterCount`/`outlineTitles`，**不含**完整 snapshot）、`GET|DELETE /api/templates/{id}`（详情含完整 snapshot）、`POST /api/templates/{id}/projects`（仅创建新项目草稿 + 独立 editor-state，绝不覆盖已有项目）。`source_project_id` 可空，源项目删除 `ON DELETE SET NULL`，快照与 `source_project_name` 保留。空大纲与超过约 1.5MB 的 snapshot → 400；跨 workspace → 404。前端：`/bid-templates` 模板库仅用列表摘要展示章节数/大纲标题；技术标工作区「沉淀为模板」；E2E `npm run test:e2e:templates`。

**未做**：商务模板、多模板融合/差异、从 docx 反解析（卡片库 MVP 见阶段 2）。

### 4.8 P10B/P10C 财务报价、成本草案与毛利快照

P10B 已实现并推送：后端 `GET /api/finance/business-bids` 与 `GET /api/finance/business-bids/{projectId}` 仅在 `AUTH_MODE=required` 且当前成员角色严格为 `finance` 时开放。接口只投影当前工作空间 `kind=business` 项目的项目摘要、报价分项和备注，响应 `Cache-Control: no-store`；技术标、跨空间和不存在项目统一 `404 project_not_found`。金额只接受有限数值，异常值为 `null` 且不计入合计。

P10C 已实现并推送：同一 `/finance` 门禁下，严格财务成员可通过独立 `cost-draft` / `cost-entries` 端点维护人工成本条目，并看到基于当前报价的毛利快照。金额仅为人民币整数分，前端元输入按字符串转换；写入走既有 CSRF，成功后重新读取服务端草案，审计只写动作与条目 ID。它不新增税务、审批、导出、预算、回款或版本历史。前端不调用通用项目、editor-state、设置或文件接口，不把业务数据、Cookie 或 CSRF 写入浏览器存储；项目切换在对应报价明细就绪前不挂载成本面板。完整契约见 `docs/p10b-finance-business-quote-contract.md` 与 `docs/p10c-finance-cost-draft-contract.md`。

### 4.9 路径索引

```text
backend/app/
  api/compliance.py finance.py knowledge.py tasks.py projects.py settings.py opportunities.py resources.py templates.py
  services/
    task_service.py parse_engines.py business_task_service.py knowledge_service.py
    embedding_service.py duplicate_service.py rejection_service.py
    export_service.py revise_service.py editor_state_service.py
    file_service.py finance_service.py opportunity_service.py resource_service.py resource_sync_service.py
    template_service.py text_similarity.py

frontend/src/features/
  technical-plan/  business-bid/  knowledge-base/  bid-templates/
  duplicate-check/  rejection-check/  settings/  bid-opportunity/  resources/  finance/
```

---

## 5. 明确未完成

| 优先级 | 项 | 现状 |
|--------|----|------|
| 导出 | `structure` / `min_heading_left_enabled` | P9A 已实现：叶子标题左侧强调线（`c1ff160`）；整章布局与 `structure` 仍不做，详见 `docs/plans/2026-07-13-p9a-word-layout-plan.md` |
| 业务 | 其他外部标讯数据源 | P9B 已完成唯一的国能 e 招单站受控追踪；其他网站/API/RSS、定时同步和浏览器外网请求仍未接，须另立计划 |
| 技术标 | 响应矩阵增强 | v1 已做手工映射、持久化、Word 导出联动、待确认智能建议（**来源 80 分页 + 候选章/大纲分批 + 前端嵌套串行累计**）、`responseMatrixVersion` DB 写锁乐观锁、前端串行保存、双浏览器 409、刷新来源、智能建议人工确认与**来源分页** E2E；**字段级三方合并 MVP**（包 7 已推送 `2c7b3e0`）。**包 8** 可插拔解析调度 MVP 已验收并推送（`6db1586`：默认 lightweight + 测试 fake；MinerU 仅外置 callback；Docling 未接；`parseStrategy` 未接线）。仍未接：包 9 交付增强相关扩展 |
| 资产 | 卡片化知识/多模板融合 | 阶段 1 模板 + 阶段 2 卡片库（`53e012f`）；阶段 3 已完成并推送：M3-A=`5d37dba`，M3-B=`e2e5d04` |
| RAG | 真语义大模型 embedding 调优 | 有本地+可选 API，可继续增强 |
| 财务 | 税务、审批、导出、预算、回款、版本与财务查看审计 | P10B/P10C 已完成报价只读、人工成本草案与毛利快照；其余数据源、精度和权限必须另立契约，禁止从报价推算 |
| 团队角色 | 人力团队推荐、投标人预览/版本/合规 | P10A 角色存在但除 P10B finance 外仍默认拒绝；各自数据域与脱敏规则未定义 |
| 库 | Alembic | 仅 create_all + ALTER |
| 生产 | HTTPS/Key 加密/PG/Docker | 本机身份和成员 RBAC 已有；生产部署能力未做 |

**粗估**：技术标 ~93%；商务 ~80%；合规工具可用；内网多人 ~30%；公网 SaaS ~15%。

---

## 6. 建议下一会话方向

1. 阶段 4 **功能包 8** MVP 已验收并推送（`6db1586`）；真实 MinerU/Docling 外置部署与 `parseStrategy` 接线另开 task。
2. 阶段 4 **P9A/P9B/P9C** 与阶段 5 **P10A/P10B/P10C** 均已实现、独立验收并文档闭环。P9C 的真实模型门仍是运行时前置：固定依赖和模型缓存就绪后，用户显式构建索引，再运行固定预检；未通过前继续关键词降级。
3. P10D 若选择财务税务/审批/导出、人员资质/团队推荐，或投标人匿名预览/版本/合规，必须先冻结独立数据契约；禁止借用 P10C 成本路径扩大权限。M3-B 后遗留的写入历史/回滚仍为可选项。

资源同步后续只可由管理员配置新的签名发布方，绝不可放开浏览器 URL 或外网抓取。图片管线已冻结项目内资源引用协议，后续扩展不得放开外链或客户端路径。SSE 的多工作空间鉴权、事件游标和项目级总线不在当前范围。

---

## 7. 安全

- 禁止提交：`.env`、真实 Key、`*.db`、`uploads/`、`data/`、`node_modules/`、`.venv/`、`.agent-collaboration/`
- 测试用假 Key 如 `sk-test-plain-key`  
- 生成/知识库提示已要求勿编造招标未出现的硬指标  

---

## 8. 旧文档关系

| 文档 | 状态 |
|------|------|
| **docs/HANDOFF-next.md** | **当前有效交接（本文件）** |
| docs/CONTRIBUTING.md | 注释与目录强制规范 |
| docs/integration-checklist.md | 联调步骤 |
| docs/agent-collaboration.md | Grok-Codex 本地消息箱协议与接入命令 |
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
4. 协作开发以 `origin/collab/grok-code-codex-review` 为准；`main` 仅作参考，严禁直接提交、推送或合并到 `main`。有本地脏文件先查明归属，不得覆盖用户改动。
5. Grok-Codex 协作时：Grok 先通过接入器发送 `ready`，Codex 写任务与审查结论；Codex 直接使用协作消息箱，不要求用户中转。若没有活跃 Grok 进程，必须如实记录并停止假装已分派；不得绕过消息箱把密钥写入工作区。
6. 当 Codex 界面可见额度约剩 10%，或用户明确要求换新会话时，停止启动新代码包：先核验 `git status`、HEAD/远端、最近测试与未提交差异，再更新本文件和相关计划/联调文档，中文提交并推送协作分支。若代理无法读取账户额度，应在用户提醒时立即执行，不得声称能后台监控不可见额度。

---

## 11. 当前会话状态（2026-07-14）

- **用户长期目标（必须完整保留）**：持续完成 biaoshu 标书制作者剩余主线任务，按既定路线图完成独立规划、受限实现审查、独立验收、中文文档闭环与协作分支推送；不直接操作 `main`。
- 当前分支仍为 `collab/grok-code-codex-review`；P10C 前端实现 SHA=`737c7db`，本交接文档提交将位于其后。新会话第一步必须用 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review` 重新核验，不可只信本文静态 SHA。
- 阶段 3 **已完成并推送**：M3-A 只读融合建议；M3-B 差异预览 + 勾选确认写入（SHA=`e2e5d04`）。
- 阶段 4 **包 5** 已推送：`460097a` 智能建议人工确认 E2E。
- 阶段 4 **包 6** 已推送：`1289c92` 实现响应矩阵源分页调用。
- 阶段 4 **包 7** 已推送：`2c7b3e0` 实现响应矩阵字段级三方合并（base 快照 + 原子字段三方合并 + 冲突显式选择 + 仅矩阵 PUT + field-merge E2E）。
- 阶段 4 **包 8** MVP：**已验收并推送** `6db1586` 实现可插拔解析引擎调度（父提交 `834969e`；`parse_engines` + `_run_parse` 调度；默认 lightweight；测试 fake；非法引擎 failed 不静默回退；MinerU 仅外置 callback；Docling 未接；`parseStrategy` 未接线）。
- **包 9A** 已实现并完成完整独立验收：计划=`57b394a`，实现=`c1ff160`，自动化文档闭环=`6d36365`，WPS 视觉验收闭环=`3dadaf8`。技术标父标题保持普通边框，叶子标题“部署架构/机房节点/售后保障”强化左栏；商务标叶子小节“二、资格响应”强化左栏；均无整章页框。不接 `structure`。
- **包 9B 交付完成**：初始审计=`a1ba88a`；用户指定国能 e 招单站后，依次推送 `45d7214`、`1c46e41`、`6491363`、`229f1d7`、`000b403`、`a7cfcb8`。P9B 不使用未获授权的通用来源；完整固定契约、数据最小化、人工确认、验收和非目标见 `docs/p9b-chnenergy-integration-contract.md`。
- **P9B 国内来源补充审计**：已将全国公共资源交易平台、中国政府采购网、天津/北京开放数据的公开资料写入包 9 总计划。全国平台公开公告页不等于读取 API；中国政府采购网规范是签名发布接口；天津候选虽有截止时间字段但公开页无实际端点且数据元信息陈旧；北京候选需 `userKey` 且无独立截止时间字段。均未满足完整受控读取契约，禁止据此写网页抓取或同步代码。
- **P9B 最终验收**：Codex 独立运行后端全量 230 passed（固定 `PYTHONHASHSEED=0`，仅 1 条既有弃用警告）、前端 lint/build、P9B E2E 1 passed 和 `git diff --check`；并对用户给定公告执行只读核验，正文北京时间截止时间为 `2026-07-29 09:00:00`。无真实数据库写入、无浏览器外网同步。
- **P9C 交付与真实模型门**：P9C 已按纯离线 BAAI/bge-small-zh-v1.5（512 维、CPU）、版本并存与可见关键词降级完成 `cc0d217`、`a0bd84b`、`71c503c`、`585e502` 四个实现提交。正文/查询不得出域，旧 API embeddingModel 与旧哈希均不参与知识库语义检索。固定评测集有 20 条完全合成查询，评测文件的版本、模型、维度和阈值均为硬校验；预检无下载/路径/跳过磁盘参数。本机无模型缓存时，Codex 实测返回 `model_unavailable`/退出码 2；这不是缺陷，未通过真实预检前不得称语义索引就绪。完整契约见 `docs/p9c-offline-semantic-index-contract.md`。
- **P10A 身份/RBAC 交付**：实现提交为 `a025627`（身份会话）、`c60a2d2`（成员管理和权限收口）、`64d32e0`（前端会话、认证模式握手和 CSRF 续发）；两份实施修订文档为 `1a442c0`、`3716e4f`。`required` 使用 HttpOnly 不透明会话、scrypt、成员工作空间校验、最后所有者保护和设置 owner 收口；前端不会持久化口令/Cookie/CSRF，硬刷新用受会话保护的 `/api/auth/csrf` 安全续发。P10B 以独立严格 `finance` 依赖补充报价只读能力，没有放宽 P10A 的默认业务拒绝。
- **P10B 财务报价交付**：计划=`5d99888`，后端=`bc0517c`，前端=`ef1e369`。严格财务角色只能读取当前空间商务标报价白名单投影；无会话在 required 下保持中间件 `401 auth_required`，已登录非财务与 disabled 为 `403 role_forbidden`；技术标、跨空间和不存在项目统一 404。完整契约见 `docs/p10b-finance-business-quote-contract.md`。
- **P10C 财务成本草案交付**：计划=`b662e85`，后端=`6f30084`，前端=`737c7db`。strict `finance` 可维护当前空间商务标人工成本条目，并以整数分读取报价、成本、毛利和毛利基点；金额输入服务端 `StrictInt` 拒绝浮点/字符串/布尔；成功写入仅审计动作和条目 ID；前端不持久化敏感数据，项目切换明细未就绪前不挂载成本面板。无税务、审批、导出、预算、回款、版本或审计查看。完整契约见 `docs/p10c-finance-cost-draft-contract.md`。
- **已验证基线**：后端全量 314 passed（按串行分组，1 条既有弃用警告）；P10C 成本 E2E 4 passed、P10B 财务 E2E 7 passed、P10A 认证 E2E 11 passed、P9C 语义索引 E2E 9 passed、知识卡片 E2E 1 passed；前端 lint/build 通过（仅既有大 chunk 警告）；`git diff --check` 通过；P9A WPS `12.1.0.26895` 实际打开技术标/商务标通过。
- 新任务分工不变：Grok 只负责限定实现与自测，未经 Codex 审查确认不得提交；Codex 负责计划、范围冻结、差异审查、独立测试、验收、中文提交、文档闭环和 GitHub 状态核验。每一包仍按“计划提交 → 实现提交 → 文档闭环提交 → 推送协作分支”执行，禁止合包。
- GitHub 若出现连接重置，可在当前 PowerShell 进程临时配置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY=http://127.0.0.1:7890` 与 `NO_PROXY=localhost,127.0.0.1` 后重试；不得把代理或凭据写入仓库。

**换会话可直接：核验分支与 HEAD → 读本文 §0～§2、§6、§11、P9B/P9C 集成契约、P9C 决策门和离线实施计划 → 先核验真实模型门或为新能力另立计划，再向 Grok 下发单一受限实现任务。**
