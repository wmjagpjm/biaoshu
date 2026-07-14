<!--
模块：标书制作者能力补全与角色化演进路线图
用途：锁定标书制作者优先开发顺序、阶段验收和后续多角色边界。
对接：docs/HANDOFF-next.md、docs/integration-checklist.md、Grok-Codex 协作消息箱。
二次开发：每阶段开工前补充本文件对应小节；完成后更新验收结果和未做项，文档必须与代码一同提交至 GitHub。
-->

# 标书制作者能力补全与角色化演进路线图

> **状态**：阶段 0/1/2 已完成；阶段 3 **已完成并推送**（M3-A=`5d37dba`，M3-B=`e2e5d04`）；阶段 4 **包 5** 已推送（`460097a`）；**包 6** 已推送（`1289c92` 实现响应矩阵源分页调用）；**包 7** 已推送（`2c7b3e0` 实现响应矩阵字段级三方合并）；**包 8** 已验收并推送（`6db1586` 实现可插拔解析引擎调度；MinerU 仅外置 callback、Docling 未接、`parseStrategy` 未接线）；**包 9A 已实现并完成完整独立验收**（`c1ff160`，含 WPS 技术标/商务标实际渲染抽检）。阶段 5 已完成 P10A 身份/RBAC、P10B/P10C strict 财务能力和 **P10D strict 人力人员资质素材卡**（后端=`d8f7cbd`，前端=`71f065a`）。
> **当前分支**：`collab/grok-code-codex-review`
> **协作方式**：Grok 负责限定范围的实现与测试；Codex 负责范围、审查、验收和提交授权。

## 1. 产品边界

系统面向 5–6 人小团队，以 AI 为核心引擎，把中标经验沉淀为可复用资产，并支持标书全生命周期管理。

当前阶段只补齐 **标书制作者** 的生产能力。标书制作者拥有项目、解析、模板、知识库、AI 生成、编辑、合规、导出和标讯能力的完整使用权。

财务、人力、投标人以及账号登录、角色权限、协作审批属于平台演进阶段。当前已受限交付 P10A 身份底座、P10B/P10C 财务与 P10D 人力最小卡片；任何新增角色能力仍须独立冻结数据、权限和审计边界，禁止绕过现有单 workspace 约束。

## 2. 已有基础

- 技术标全流程、商务标、异步任务、AI 生成与编辑、文档知识库检索、查重/废标检查、Word 导出、标讯本地库和资源中心已可用。
- 响应矩阵已支持人工映射、候选分批智能建议、冲突保护、来源 80 分页、双浏览器冲突/刷新来源/人工确认/来源分页 E2E；包 7 字段级三方合并 MVP 已推送（`2c7b3e0`）。
- 轻量解析和 MinerU Markdown 回传已经可用；包 8 MVP 可插拔调度已验收并推送（`6db1586`：默认 `lightweight` + 测试 fake），**真实 MinerU 仅外置 callback，Docling 未接，`parseStrategy` 未接线**。
- 当前真实缺口是中标内容模板资产化、多模板融合/差异预览、文档与图片统一卡片资产库、图片知识库后端化、外部标讯数据源，以及完整的生产化账号与权限体系。导出版式模板与中标内容模板是两个不同概念，后续不得混用术语。

## 3. 阶段顺序

### 阶段 0：现状审计与产品契约

**目标**：逐项核对现有解析、知识库、资源、导出和项目数据模型，明确可复用能力与真实缺口，避免重复建设。

**输出**：能力矩阵、目标数据模型、首个实现任务的文件范围和验收用例。

**验收**：审计结论与本路线图一致；明确每项能力是“已有 / 可扩展 / 新建”。

**当前进度（2026-07-12）**：已完成只读审计。轻量解析、MinerU 回传、文档 RAG、AI 生成/编辑、合规、Word 交付和本地标讯已存在；阶段 1–3 的核心缺口均为新建能力。

### 阶段 1：中标经验资产化

**目标**：把已中标标书沉淀为受项目/工作空间隔离的模板资产，可检索、可查看来源和可选择复用。

**范围**：仅做 workspace 内的**中标内容模板**快照，不与导出版式模板混用。包含模板元数据、来源项目追溯、标签、版本快照、列表检索、项目内“沉淀为模板”入口，以及“从模板创建新项目草稿”的单一路径。

**数据边界**：模板必须深拷贝大纲与章节；`source_project_id` 只作可空追溯，源项目删除不得删除模板快照；跨 workspace 一律 404；限制 snapshot 体积并拒绝空大纲。阶段 1 不做多模板融合、差异预览、图片卡片、自动扫描中标项目、跨 workspace 共享、RBAC、Alembic 或从 docx 反解析建模板。

**验收**：从项目沉淀模板后，可在同 workspace 检索并创建含独立 editor-state 副本的新项目；删除源项目不破坏模板快照；跨 workspace 不可访问；非法超大/空快照明确 400。

**实现进度（2026-07-12，已完成）**：

| 项 | 状态 | 说明 |
|---|---|---|
| 实体 `bid_templates` | 已实现 | `BidTemplateRow`；`source_project_id` FK `ON DELETE SET NULL` |
| API | 已实现 | `POST /api/templates/from-project`（含 snapshot）；`GET /api/templates`（摘要：chapterCount/outlineTitles，无完整 snapshot）；`GET/DELETE /api/templates/{id}`（详情含 snapshot）；`POST /api/templates/{id}/projects` |
| 服务 | 已实现 | `template_service`：深拷贝 outline/chapters（+ 可选 facts/guidance/mode）；列表 `template_to_summary_data`；空大纲/超大快照 400；仅 technical |
| UI | 已实现 | 工作区「沉淀为模板」；侧栏「中标模板」库；从模板新建进入大纲步 |
| 测试 | 已实现 | `backend/tests/test_bid_templates.py`；`frontend/e2e/bid-template-reuse.spec.ts`；`npm run test:e2e:templates` |

**交付记录**：`de43f2d 实现技术标中标内容模板资产化`。Codex 独立验收：后端 138 passed、模板 E2E 1 passed、响应矩阵 E2E 2 passed、lint/build 通过。

**未做（阶段 1 明确排除）**：商务模板、卡片库、多模板融合/差异预览、Docling、外部标讯、登录/RBAC、Alembic、导出版式模板语义变更、从 docx 反解析建模板。

### 阶段 2：卡片化知识与素材库

**目标**：统一沉淀文档片段、图片、资质与业绩为 workspace 内可检索卡片，服务标书编辑时的安全引用与复用。

**设计状态（2026-07-12）**：只读审计已完成，MVP 契约已冻结。

**实现状态：已完成**。交付 SHA=`53e012f`（实现卡片化知识与素材库）；含列表默认 active、bodyMarkdown 上限 20,000 等返修项。

**数据边界**：新建独立 `knowledge_cards` 表，禁止复用或污染 `kb_documents/kb_chunks`、`resources`、`project_files`、`bid_templates` 的既有语义。文本卡保存正文快照和可空弱来源引用；图片卡复制图片字节到 workspace 卡片存储（`data/knowledge_cards/{workspaceId}/`）。源项目、源文档或源分块删除后，卡片仍可预览和复用。

**MVP 范围（已实现）**：

- 类型：`document`、`image`、`qualification`、`performance`；统一保存标题、标签、摘要、状态、来源快照、正文或类型扩展数据。
- 能力：手工创建文本卡、从知识分块沉淀、上传/从项目图片沉淀、列表筛选检索、详情预览、归档/删除。
- 图片：只允许 PNG/JPEG/GIF；卡片入项目时先复制登记为当前项目 `role=image`，Markdown 只写 `biaoshu-image://file_*`，禁止外链、卡片路径或 data URL。
- 编辑：章节编辑器通过「插入卡片」取得文本引用块或项目化图片引用；只追加用户选择的内容，不自动覆盖正文。

**实际 API**：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cards` | 列表摘要（q/type/status）；status 缺省=active，archived\|all 显式；无正文/base64；bodyMarkdown 上限 20,000 |
| POST | `/api/cards` | 手工创建文本卡 |
| POST | `/api/cards/upload-image` | 上传图片卡 |
| POST | `/api/cards/from-chunk` | 从知识分块沉淀 |
| POST | `/api/cards/from-project-image` | 从项目图片沉淀 |
| GET/PATCH/DELETE | `/api/cards/{id}` | 详情/更新/删除 |
| GET | `/api/cards/{id}/content` | 图片卡二进制 |
| POST | `/api/projects/{projectId}/insert-card` | 返回可插入 Markdown + 可选 projectImageId |

**明确不做（本阶段仍排除）**：不把卡片自动注入 AI 生成；不做多卡片融合、差异预览、向量排序、历史项目批量扫描、跨 workspace 共享、商务标专用卡、版本历史、登录/RBAC、Alembic 或依赖升级。卡片作为 AI 上下文的配额、选择与确认写入留给阶段 3。

**验收命令**：`backend pytest -q`（含 `test_knowledge_cards`）；`frontend npm run lint` / `build`；`npm run test:e2e:cards`；`npm run test:e2e:templates`；`npm run test:e2e:matrix`；`git diff --check`。

### 阶段 3：可控 AI 编写与模板融合

**目标**：支持选择多个模板/卡片作为生成上下文，并在写入前展示结构与内容差异，保持人工可控。

**设计状态（2026-07-12）**：只读审计通过；契约冻结为任务类型 `content_fuse`。拆分为：

| 子里程碑 | 范围 | 状态 |
|---|---|---|
| **M3-A** | 选择模板/卡片/目标章 → 只读融合建议（result_json） | **已完成**（合并 SHA=`5d37dba`） |
| **M3-B** | 差异预览、checkbox、base 漂移跳过、逐项确认写入 | **已完成并推送**（SHA=`e2e5d04`，实现融合建议人工确认写入） |

**M3-A 冻结边界**（已落地）：

- 成功路径**仅写** `ProjectTask.result_json`；**禁止** `upsert_editor_state`、禁止改 chapters/outline/responseMatrix。
- payload：`templateIds`(0~3)、`cardIds`(0~8)、合计 1~10、`targetChapterIds`(1~5)、`mode=merge_suggest`。
- 创建阶段只校验 shape/配额/目标章；来源可用性由 worker 处理；跨 workspace/缺失统一 `skippedSources.unavailable`。
- 卡片仅 active 的 document|qualification|performance；image/archived → skipped。
- `result.suggestions[].sourceRefs` 形状为 `{kind,id,title}`：`title` 由服务端按**实际进入 prompt** 的模板/卡片目录补齐；无有效来源建议整条丢弃并计入 `skippedInvalidCount`；`quota.templatesUsed/cardsUsed` 与入 prompt 一致，`promptChars≤24000`。
- 不开放 `candidateBatchIndex`；不改阶段 1/2 templates/cards/insert-card/response_match 语义。

**验收命令（M3-A）**：`pytest -q`（含 `test_content_fuse`）；`npm run lint` / `build`；`npm run test:e2e:fuse`；回归 `test:e2e:cards` / `templates` / `matrix`；`git diff --check`。

**M3-B 冻结边界**：

- **纯前端**：不新增后端 API/任务/表/依赖；`content_fuse` worker 仍只写 `result_json`。
- 确认写入**仅**修改用户勾选且实时 base 匹配的 `chapters` body，经既有 `replaceChapterBody` → debounce PUT `editor-state`；**不**改 responseMatrix/outline/analysis。
- base 全匹配：章节存在 + `bodyHash`/`bodyLength`/`title(trim)` 一致；哈希为纯同步 SHA-1（UTF-8、hex 前 20、`bh_` 前缀）；`bodyLength=Array.from(body).length`；哈希失败不得放行。
- 空 `proposedMarkdown` 永不应用；`action=expand` 追加（非空旧正文双换行）；其余规范 action 替换。默认不预勾；确认瞬间再校验。
- 未确认关闭、取消、项目切换/迟到结果均不写；部分成功允许；**无专用 undo/history**，由用户手工编辑恢复。
- 保存失败/409 行为复用既有 editor-state/UI，不静默覆盖矩阵或回滚已编辑章节。

**M3-B 允许文件**：`contentFuse.ts`、`ContentFuseDialog.tsx`、`TechnicalPlanWorkspace.tsx`、`TechnicalPlan.css`、`content-fuse-apply.spec.ts`（新）、`package.json`（`test:e2e:fuse-apply`）、本路线图 / HANDOFF / integration-checklist。若必须改 `useTechnicalPlanEditors.ts` 须先 question。

**验收命令（M3-B）**：`npm run lint` / `build`；`npm run test:e2e:fuse`；`npm run test:e2e:fuse-apply`；回归 `test:e2e:cards` / `templates` / `matrix`；后端 `pytest -q`（无后端 diff 仅回归）；`git diff --check`。

**验收结果**：已推送 SHA=`e2e5d04`（差异预览 + 勾选确认写入 E2E）。

**M3-B 后遗留**：写入后专用回滚/历史、多角色协作。（矩阵智能建议人工确认 E2E 见阶段 4 包 5）

### 阶段 4：生产链质量与交付闭环

**目标**：补齐高价值质量缺口，提升大项目与最终交付的稳定性。

**范围**：智能建议人工确认浏览器 E2E、来源超过 80 条的分页策略、响应矩阵字段级合并评估、Word 整章版式/最小标题左栏、外部标讯数据源方案。

**验收**：每项独立立项；有后端测试、前端构建检查和按风险需要的 E2E；版式项必须先确认效果图和规则。

#### 功能包 5：响应矩阵智能建议“人工确认后应用”浏览器 E2E

**状态**：**已完成并推送**（SHA=`460097a`）。**无业务代码改动。**

**范围**：本机 OpenAI-compatible mock LLM + API 种子；真实驱动分析步响应矩阵 UI；`response_match` 应用前不写 editor-state；部分勾选应用；notes 保护；base 漂移跳过。

#### 功能包 6：响应矩阵来源超过 80 条的分页建议

**状态**：**已完成并推送**（SHA=`1289c92`，提交标题「实现响应矩阵源分页调用」）。

**范围**：`sourceBatchIndex` 与 `candidateBatchIndex` 共存；单次 prompt 来源 ≤80；前端外层来源页 × 内层候选批串行；任务只写 result_json；E2E 覆盖第 2 页唯一来源。

#### 功能包 7：响应矩阵字段级三方合并

**状态**：**已完成并推送**（SHA=`2c7b3e0`，提交标题「实现响应矩阵字段级三方合并」）。

**允许文件**：
- `frontend/src/features/technical-plan/lib/responseMatrix.ts`
- `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
- `frontend/src/features/technical-plan/components/ResponseMatrixPanel.tsx`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/technical-plan/types.ts`（仅必要时）
- `frontend/e2e/response-matrix-field-merge.spec.ts`（新）
- `frontend/package.json`（仅 `test:e2e:matrix` 追加本 spec）
- `docs/plans/2026-07-13-response-matrix-field-merge-plan.md`（新）
- `docs/plans/2026-07-12-bid-writer-roadmap.md`
- `docs/HANDOFF-next.md`
- `docs/integration-checklist.md`

**范围**：base 快照；可编辑字段原子三方合并；409 合并预览；冲突显式选择；应用 PUT 仅矩阵+版本；E2E 无冲突/冲突/二次 409。

**明确不做**：改 backend/API/DB；自动重试循环；并集/deep-merge；智能建议语义变更；包 8/9。

**验收命令**：backend `pytest -q`；`npm run test:e2e:matrix`；`npm run lint` / `build`；`git diff --check`。

**未做（包 9）**：P9B 外部标讯和 P9C 真语义 embedding——均须在各自前置决策确认后独立 task；P9A 最小标题左栏已完成。

#### 功能包 9A：Word 精细版式（最小标题左栏）

**状态**：**已实现并完成完整独立验收**，实现提交 `c1ff160`（实现P9A最小标题左栏）；自动化检查与 WPS 技术标/商务标实际渲染抽检均通过。计划与验收证据见 `docs/plans/2026-07-13-p9a-word-layout-plan.md`。

**冻结范围**：仅在 `heading_border.enabled` 与 `min_heading_left_enabled` 同时开启时，为每个标题分支的叶子标题写入左侧段落强调线，并在前端模板预览中同步显示。复用既有边框颜色；不新增配置字段、API 或数据迁移。

**明确不做**：整章页框/节级版式、`heading_border.structure` 接线、封面/目录重排、P9B 外部标讯、P9C embedding。

**验收**：标题边框后端定向测试、后端全量 `pytest -q`、前端 lint/build、技术标与商务标 Word 人工打开检查、`git diff --check`。

#### 功能包 8：可插拔解析调度（MVP）

**状态**：**已验收并推送**（SHA=`6db1586`，提交标题「实现可插拔解析引擎调度」，父提交 `834969e`）。计划见 `docs/plans/2026-07-13-pluggable-parse-plan.md`。

**范围**：`parse_engines` 注册/调度；生产仅 `lightweight`；任务 `payload.engine`；非法引擎 failed 且不静默回退；测试可注入 fake；`result.engine` 可追溯；callback Token 开关补测。

**明确不做**：内嵌/安装真实 MinerU 或 Docling；改默认 requirements；改 callback 默认空 token；`parseStrategy` 接线；包 9。

**验收命令**：backend `pytest -q`；定向 parse/callback tests；`npm run lint` / `build`；`git diff --check`。

### 阶段 5：团队账号、角色与协作

**目标**：在标书制作者生产链稳定后，演进为多账号、多角色、最小权限和可审计协作平台。

**角色方向**：

| 角色 | 主要开放能力 | 明确限制 |
|---|---|---|
| 标书制作者 | 全部标书生产、模板、知识、AI、编辑、合规与融合能力 | 无业务功能限制 |
| 财务 | P10B 已交付商务标报价只读投影；P10C 已交付人工成本草案与毛利快照；后续税务、预算、审批、导出、回款和报表 | 不修改技术方案和团队配置；不以所有者身份绕过 strict `finance` |
| 人力 | P10D 已交付当前空间人员资质素材卡（登记、摘要、详情、编辑、启停）；后续团队推荐、人员/业绩卡片 | 不看完整标书和定价细节；不收集证件号、联系方式、附件或外链；不以所有者身份绕过 strict `hr` |
| 投标人 | 投标看板、合规总览、版本、结果跟踪、预览 | 不改标书核心内容，不使用模板生成 |

**验收**：账号认证、工作空间隔离、服务端 RBAC、页面能力收敛、审计记录和跨角色数据脱敏均有自动化验证。

## 3.1 标书制作者剩余能力包（阶段 2–4）

以下按可独立验收的功能包计数，当前共 **9 项**；不包含阶段 5 的登录、多角色与协作平台。

| 序号 | 功能包 | 所属阶段 | 优先级 | 前置依赖 |
|---|---|---|---|---|
| 1 | 文档/图片/资质/业绩统一卡片库 | 阶段 2 | P0 | 现有文档知识库、项目图片安全协议 |
| 2 | 卡片检索、筛选、来源追溯与写作引用 | 阶段 2 | P0 | 功能包 1 |
| 3 | 多内容模板/卡片选择与上下文配额 | 阶段 3 | P0 | 阶段 1、功能包 1–2 |
| 4 | 章节级融合建议、差异预览与逐项确认写入 | 阶段 3 | P0 | 功能包 3、editor-state 冲突保护 |
| 5 | 智能建议“人工确认后应用”浏览器 E2E | 阶段 4 | P1 | 现有 response_match |
| 6 | 响应矩阵来源超过 80 条的分页建议 | 阶段 4 | P1 | 现有候选分批能力 |
| 7 | 响应矩阵字段级三方合并 | 阶段 4 | P1 | 现有 409 版本保护 |
| 8 | 生产级可插拔解析（MinerU/Docling） | 阶段 4 | P1 | 现有轻量解析与 MinerU 回传 |
| 9 | 交付增强：Word 精细版式、外部标讯源、真语义 embedding 调优 | 阶段 4 | P2 | 各自独立立项与效果/来源规则 |

**执行原则**：功能包 1–4 是“经验资产 → 可控 AI 生产”的主链，必须按顺序推进；5–9 可在主链稳定后按收益和外部依赖拆分立项。第 9 项包含三个相互独立的 P2 子项，实施时必须拆为单独任务，不得一次性合并。

## 4. 每阶段文档与 GitHub 留存规则

1. 开工前：在本文件补充阶段目标、允许改动文件、数据边界、验收命令和明确不做项。
2. 实现中：Grok 通过协作消息箱报告范围与测试；不得绕过 Codex 审查提交。
3. 完成后：更新本文件、`docs/HANDOFF-next.md`、必要的联调/测试文档，并与代码一同提交、推送到协作分支。
4. 提交前：必须通过 `git diff --check`；按改动范围运行后端测试、lint、build 和 E2E。
5. GitHub 历史：一个可验收阶段至少一个中文提交，提交信息明确功能与阶段；禁止把数据库、密钥、构建产物和协作消息带入提交。

## 5. 当前下一步

阶段 0/1/2/3 已完成并推送（M3-A=`5d37dba`，M3-B=`e2e5d04`）。阶段 4 **包 5** 已推送（`460097a`）；**包 6** 已推送（`1289c92` 实现响应矩阵源分页调用）；**包 7** 已推送（`2c7b3e0` 实现响应矩阵字段级三方合并）；**包 8** 已验收并推送（`6db1586` 实现可插拔解析引擎调度；MinerU 仅外置 callback、Docling 未接、`parseStrategy` 未接线）。阶段 5 的 P10A 身份/RBAC、P10B 财务报价只读投影、P10C 财务成本草案/毛利快照与 P10D 人员资质素材卡均已完成（P10D 后端=`d8f7cbd`，前端=`71f065a`）。下一步如扩展财务税务/审批/导出、人员业绩/团队推荐或投标人能力，必须先冻结新的独立数据契约，禁止沿用 P10C/P10D 路径顺带扩权。
