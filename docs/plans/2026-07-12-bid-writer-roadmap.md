<!--
模块：标书制作者能力补全与角色化演进路线图
用途：锁定标书制作者优先开发顺序、阶段验收和后续多角色边界。
对接：docs/HANDOFF-next.md、docs/integration-checklist.md、Grok-Codex 协作消息箱。
二次开发：每阶段开工前补充本文件对应小节；完成后更新验收结果和未做项，文档必须与代码一同提交至 GitHub。
-->

# 标书制作者能力补全与角色化演进路线图

> **状态**：阶段 0–5 已按下文拆包持续交付；P11A/B/C、P12A 至 P12N 版本治理链、P13-A/P13-B/P13-C 均已完成。P12N 冻结=`337b401`、实现=`394639a`；P13-B 冻结=`040d644`、实现=`1d4fe0b`；P13-C 冻结=`e62ea27`、实现=`6eaa89f`。继续按分级策略避免机械重复后端全量或整仓前端 **318 passed** 基线。
> **当前分支**：`collab/grok-code-codex-review`
> **协作方式**：Grok 负责限定范围的实现与测试；Codex 负责范围、审查、验收和提交授权。

## 1. 产品边界

系统面向 5–6 人小团队，以 AI 为核心引擎，把中标经验沉淀为可复用资产，并支持标书全生命周期管理。

当前主线持续补齐标书制作者生产能力及其所需的受限团队协作数据域。标书制作者拥有项目、解析、模板、知识库、AI 生成、编辑、合规、导出和标讯能力的完整使用权。

财务、人力、投标人以及账号登录、角色权限、协作审批属于平台演进阶段。当前已受限交付 P10A 身份底座、P10B/P10C/P10J/P10K 财务、P10D 人力资质卡、P10F 人力团队推荐快照、P10H 人员业绩卡、P10I 人员资质到期提示、P10E 投标人匿名汇总和 P10G 投标人单项目统计。任何新增角色能力仍须独立冻结数据、权限和审计边界，禁止绕过现有单 workspace 约束。

## 2. 已有基础

- 技术标全流程、商务标、异步任务、AI 生成与编辑、文档知识库检索、查重/废标检查、Word 导出、标讯本地库和资源中心已可用。
- 响应矩阵已支持人工映射、候选分批智能建议、冲突保护、来源 80 分页、双浏览器冲突/刷新来源/人工确认/来源分页 E2E；包 7 字段级三方合并 MVP 已推送（`2c7b3e0`）。
- 轻量解析和本机 Markdown 回传已经可用；包 8 MVP 可插拔调度已推送（`6db1586`：默认 `lightweight` + 测试 fake），P8B 已让 `parseStrategy` 驱动技术标/商务标的 `light/local/ask` 动作，P8C 已补 required 模式 10 分钟单项目单次回传票据；P8D/P8E 已分别提供离线调用本机既有 `mineru.exe`/`docling.exe` 并受控回传的标准库助手。**两种真实 CLI/模型仍需用户人工准备，自动部署未接**。
- 当前已具备中标内容模板资产化、多模板/卡片融合、差异确认、最多 20 条有限修订的手动游标浏览与受限恢复、统一卡片库、受限角色数据域，以及 P9C 固定模型运行时门和真实合成集预检。真实剩余缺口集中在多人协作与更完整版本治理、真实 MinerU/Docling 生产部署、P9C 真实用户语料评测/排序调优、Word `structure`/整章布局、更多合法外部标讯来源及完整生产部署治理。导出版式模板与中标内容模板是两个不同概念，后续不得混用术语。
- P11A 已把技术标/商务标列表、详情、创建与查重/废标选择器收口为 `/api/projects*` 单一真值；前端全量 E2E 由 145 增至 155。它未改 editor-state 本地备份、知识库降级、未挂载首页、后端或角色权限。
- P11B 已让商务标 workspace 只认 `GET|PUT /api/projects/{id}/editor-state`：旧 `biaoshu.businessBid.workspace.*` 忽略保值，真实空态保持空，加载/保存失败固定脱敏，A→B 的迟到 GET/PUT 被项目会话隔离；AI 反馈 history 本地键保持非目标，技术标大 Hook 未改。前端全量 E2E 由 155 增至 166。
- P11C 已让技术标 workspace 只认 `GET|PUT /api/projects/{id}/editor-state`：旧 `biaoshu.technicalPlan.editors.*` 忽略保值，真实空态保持空，加载/保存失败固定脱敏，required 普通/合并 PUT 使用同源 Cookie 与内存 CSRF，A→B 迟到及挂起保存链隔离，生产演示入口已清理。前端全量 E2E 由 166 增至 184；后端、响应矩阵算法、M3-D 业务与 guidance 历史未改。

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
| **M3-C** | 当前对话框最近成功批次的一次性漂移安全撤销 | **已完成并推送**（计划=`c63310f`、实现=`b8ff605`） |
| **M3-D** | 服务端原子确认、最近 20 批持久恢复、一次消费 | **已完成并推送**（计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`） |

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

**M3-B 后遗留**：持久化版本历史、多角色协作。（矩阵智能建议人工确认 E2E 见阶段 4 包 5；最近批次即时撤销由 M3-C 独立补齐。）

#### M3-C：融合写入最近批次单次撤销（已验收并推送）

**目标**：在既有融合对话框内，为最近一次成功确认写入提供会话内、一次性、漂移安全的即时撤销；恢复正文与原章节状态，并继续走既有串行防抖保存。

**冻结边界**：纯前端；快照只存在当前对话框实例，关闭/刷新/切项目后不保留；撤销前精确校验章节仍存在且标题、正文、状态均未漂移，手工改过的章跳过。无新 API、表、依赖、存储、通用撤销栈或持久化历史。完整契约见 `docs/m3c-content-fuse-undo-contract.md`，实施计划见 `docs/plans/2026-07-14-m3c-content-fuse-undo-plan.md`。

**交付与验收**：计划=`c63310f`、实现=`b8ff605`；M3-B/M3-C E2E 6 passed、M3-A E2E 1 passed、P10H 回归 10 passed，lint/build 通过，单 worker 串行全量 106 passed。撤销只消费最近批次一次，漂移章不覆盖，正文和原章节状态共同恢复。

#### M3-D：融合写入持久恢复批次（已验收并推送）

**目标**：把已成功 M3-A 任务中的用户勾选建议交给服务端原子确认，同时只保留每项目最近 20 个恢复批次；关闭或刷新后仍可对未漂移章节执行一次性恢复。

**冻结边界**：客户端只提交 taskId/suggestionIds，建议正文只取服务端任务结果；锁内校验章节 base，章节写入与恢复快照同事务。恢复仅覆盖 title/body/status 仍等于 after 的章节，一次尝试后消费。不是通用版本库，不回填旧批次，不保存 prompt/来源全文，不浏览历史正文，不扩商务标或其他角色。完整契约见 `docs/m3d-content-fuse-persistent-recovery-contract.md`，实施计划见 `docs/plans/2026-07-14-m3d-content-fuse-persistent-recovery-plan.md`。

**交付与验收**：计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`。后端经三轮受限审查后，专项 34 passed、受影响回归 71 passed、串行全量 487 passed；前端经两轮受限审查，将真实 editor-state 重载收敛为单次可判定请求，M3-D/M3-A/认证定向 23 passed、单 worker 串行全量 E2E 145 passed，lint/build/diff-check 通过。通用版本库、任意历史回滚和多人协作仍不在本包范围。

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

#### 功能包 9D：导出图片失效引用浏览器提示（已验收并推送）

**目标**：消费既有成功导出任务的 `result.imageWarnings`，在技术标和商务标导出页显示后端权威的图片降级原因，同时继续既有 Word 下载。

**冻结边界**：纯前端；共享归一化和展示组件只接受有限数量、有限长度的非空字符串，并以 React 文本渲染。每次导出前和项目切换时清空旧告警。无新 API、任务、后端、图片规则、存储、外链、依赖或下载阻断。完整契约见 `docs/p9d-export-image-warning-contract.md`，实施计划见 `docs/plans/2026-07-14-p9d-export-image-warning-plan.md`。

**交付结果**：计划=`4925a51`、实现=`e5adad7`。技术标与商务标复用同一安全归一化/展示组件；告警与项目绑定并受实例级代次保护，旧任务迟到仍保持既有下载语义但不能污染新项目。两轮审查修复了项目切换首帧旧告警、迟到写入、E2E 假同步、告警/下载顺序和新增 lint warning。

**验收**：后端项目图片专项 14 passed；P9D E2E 4 passed；lint/build 通过；单 worker 全量 E2E 110 passed。真实本机技术标/商务标导出、非法结构收敛、下载不阻断和项目切换迟到隔离均已覆盖。

#### 功能包 8：可插拔解析调度（MVP）

**状态**：**已验收并推送**（SHA=`6db1586`，提交标题「实现可插拔解析引擎调度」，父提交 `834969e`）。计划见 `docs/plans/2026-07-13-pluggable-parse-plan.md`。

**范围**：`parse_engines` 注册/调度；生产仅 `lightweight`；任务 `payload.engine`；非法引擎 failed 且不静默回退；测试可注入 fake；`result.engine` 可追溯；callback Token 开关补测。

**原 P8 MVP 明确不做**：内嵌/安装真实 MinerU 或 Docling；改默认 requirements；改 callback 默认空 token；包 9。`parseStrategy` 接线已由后续独立 P8B 契约完成，不改变本 MVP 的引擎边界。

**验收命令**：backend `pytest -q`；定向 parse/callback tests；`npm run lint` / `build`；`git diff --check`。

#### 功能包 8B：工作空间解析策略接线

**状态**：**已完成、独立验收并推送**。契约=`docs/p8b-parse-strategy-wiring-contract.md`，计划=`f662674`，后端=`0994cc8`，前端=`80d2579`。

**范围**：新增只返回 `parseStrategy` 的 `GET /api/settings/parse-strategy`（strict `bid_writer` 工作空间语义、无设置行默认 `light`、不建行、`no-store`）；技术标和商务标每次解析重新读取 `light/local/ask`。`light` 明确创建 `engine=lightweight` 任务；`local` 只前往带项目 ID 的本地回传页；`ask` 一次性选择且取消不建任务、不改默认设置。浏览器不持久化或使用旧缓存决定策略。

**验收**：后端全量 **348 passed**（1 条既有弃用警告）、前端全量 E2E **69 passed**，其中 P8B E2E **6 passed**；`npm run lint` / `build` 与 `git diff --check` 通过。

**明确不做**：服务端 MinerU/Docling、外部进程或依赖、`parse_engines` 生产注册表、`task_service`、callback Token 默认策略、自动回传、策略版本历史和完整设置泄漏。

#### 功能包 8C：本地解析一次性回传票据（已验收并推送）

**目标**：补齐 `AUTH_MODE=required` 下外部本地解析助手无法安全回传的断点；strict `bid_writer` 为单一当前空间项目签发 10 分钟、单次票据，外部助手无需浏览器 Cookie、CSRF 或长期全局 Token。

**冻结边界**：新增精确公共 `/api/local-parser/callback`，只接受 `X-Local-Parse-Ticket` 与受限 MinerU Markdown；数据库只存票据摘要和 workspace/project/user/时间绑定，消费与解析结果、任务、项目步骤同事务。保留个人版旧回调，不安装或启动 MinerU/Docling，不改 `light/local/ask` 和 engine 注册。完整契约见 `docs/p8c-local-parser-one-time-callback-ticket-contract.md`，实施计划见 `docs/plans/2026-07-14-p8c-local-parser-one-time-callback-ticket-plan.md`。

**交付结果**：计划=`cabe99d`，后端=`af39ff8`，前端=`1cf5576`。后端实现流式 2 MiB 上限、摘要存储、精确公开路径、原子单次消费和同事务写入；前端 required 显式签发并只在组件内存显示绝对固定 curl，disabled 保留旧表单。Codex 独立通过后端 432 项、P8C 前端 9 项、P8B 6 项、lint/build 与第二轮单 worker 全量 E2E 131 项。

### 阶段 5：团队账号、角色与协作

**目标**：在标书制作者生产链稳定后，演进为多账号、多角色、最小权限和可审计协作平台。

**角色方向**：

| 角色 | 主要开放能力 | 明确限制 |
|---|---|---|
| 标书制作者 | 全部标书生产、模板、知识、AI、编辑、合规与融合能力 | 无业务功能限制 |
| 财务 | P10B 已交付商务标报价只读投影；P10C 已交付人工成本草案与毛利快照；P10J 已交付本人成功成本变更记录；P10K 已交付上线后项目级匿名成本变更记录；后续税务、预算、审批、导出、回款和报表 | 不修改技术方案和团队配置；P10J/P10K 均不得冒充完整审计；不以所有者身份绕过 strict `finance` |
| 人力 | P10D 已交付当前空间人员资质素材卡；P10F 已交付由 strict `hr` 维护的技术标团队推荐快照，strict `bid_writer` 仅可按需读取最小投影；P10H 已交付独立人员业绩素材卡；P10I 已交付只读资质到期提示 | 不看完整标书和定价细节；不收集证件号、联系方式、附件或外链；不把日期提示冒充真伪核验；不以所有者身份绕过 strict `hr` 或 strict `bid_writer` |
| 投标人 | P10E 已交付工作空间级匿名响应矩阵合规汇总；P10G 已交付当前空间技术标选择器和单项目五项统计；后续矩阵明细、版本和结果跟踪 | 不改标书核心内容；P10G 之外不返回项目、原文、人员或财务数据；不以所有者身份绕过 strict `bidder` |

**验收**：账号认证、工作空间隔离、服务端 RBAC、页面能力收敛、审计记录和跨角色数据脱敏均有自动化验证。

#### P10H：人员业绩素材卡（已验收并推送）

**目标**：为严格 `hr` 提供独立的最小人员业绩卡，记录协作显示名、人工项目名称、项目角色、可选完成年份、业绩摘要、备注与启停状态；摘要与详情字段分离。

**冻结边界**：仅 HR 手工维护当前工作空间数据；复用 `require_hr`，所有者不隐式绕过；不修改 P10D 资质卡、P10F 团队推荐、技术标工作区或任何标书制作者路径；不做附件、证件校验、合同金额、客户联系方式、项目关联、团队组装、导出、审批、外网或浏览器持久化。完整约束见 `docs/p10h-hr-performance-cards-contract.md`，实施拆分见 `docs/plans/2026-07-14-p10h-hr-performance-cards-plan.md`。

**交付与验收**：计划=`7694843`、后端=`6c76d80`、前端=`4eb8a14`；后端定向 14 passed、串行全量 392 passed，前端 P10H E2E 10 passed、单 worker 串行全量 93 passed，lint/build 通过。

#### P10I：人员资质到期提示（已验收并推送）

**目标**：严格 `hr` 读取当前空间启用 P10D 卡片的服务端 UTC 日期分类、固定 90 天风险计数和最小关注列表；页面明确仅为人工日期提示，不验证证件真实性。

**冻结边界**：独立只读 `/api/hr/credential-expiry` 与 `/hr/credential-expiry`；不新增表、不修改 P10D、不读取备注、不向其他角色投影，不做证件号、扫描件、附件、OCR、外网权威核验、审批、导出或自动修复。完整契约见 `docs/p10i-hr-credential-expiry-contract.md`，实施拆分见 `docs/plans/2026-07-14-p10i-hr-credential-expiry-plan.md`。

**交付与验收**：计划=`ddc1807`、后端=`d5201e9`、前端=`49daa16`；后端定向 14 passed、串行全量 406 passed，前端 P10I E2E 10 passed、单 worker 串行全量 103 passed，lint/build 通过。后端 SQL 仅投影必要列，前端首次严格单次读取且刷新不重复；均不把日期提示伪装为真伪结论。

#### P10J：财务个人成本变更记录（已验收并推送）

**目标**：严格 `finance` 查看本人在当前活动工作空间最近 50 条成功成本条目新增、修改、删除记录；页面明确这不是完整财务审计。

**冻结边界**：唯一 GET 只按当前 workspace/current actor/三 action/`success`/合法 `fce_*` 查询既有审计表，SQL 仅投影 action/target/created_at，返回 action/entryId/occurredAt。无项目、金额、业务内容、前后快照、失败尝试、其他成员、表/迁移、筛选、分页、导出或浏览器存储。完整契约见 `docs/p10j-finance-personal-cost-change-events-contract.md`，实施拆分见 `docs/plans/2026-07-14-p10j-finance-personal-cost-change-events-plan.md`。

**交付与验收**：计划=`701c946`、后端=`4e662d6`、前端=`fce6cb6`；后端 P10J 定向 16 passed、受影响回归 63 passed、串行全量 422 passed，前端 P10J E2E 12 passed、单 worker 串行全量 122 passed，lint/build 通过。两轮后端审查把所有 target 合法性过滤前移到 SQL 上限前；前端审查收紧外网阻断与可观测断言。

#### P10K：财务项目成本变更记录（已验收并推送）

**目标**：严格 `finance` 在既有 `/finance` 页面显式读取当前空间选定商务标项目最近 50 条、从 P10K 上线后记录的成功成本变更，并匿名区分本人/其他财务成员。

**冻结边界**：新增最小不可变事件表，与 P10C 成功 create/update/delete 及原审计同事务；事件只存 workspace/project/entry/action/actor/time。唯一项目 GET 只返回 action/entryId/actorScope/occurredAt；无旧历史回填、金额/内容/前后值、成员身份、失败尝试、筛选分页导出、自动读取或浏览器存储。完整契约见 `docs/p10k-finance-project-cost-change-events-contract.md`，实施拆分见 `docs/plans/2026-07-14-p10k-finance-project-cost-change-events-plan.md`。

**交付与验收**：计划=`2e53007`、后端=`1eaa75e`、前端=`dbf301c`；后端 P10K 定向 21 passed、受影响回归 79 passed、串行全量 453 passed，前端 P10K E2E 9 passed、P10C 4 passed、P10B 7 passed、单 worker 串行全量 140 passed，lint/build 通过。后端与前端各经历一次反假绿退回修复后通过独立复核。

## 3.1 标书制作者剩余能力包（阶段 2–4）

以下按可独立验收的功能包计数，当前共 **10 项**；不包含阶段 5 的登录、多角色与协作平台。

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
| 10 | 融合确认写入的最近批次单次撤销 | 阶段 3 | P1 | M3-B 确认写入、编辑器串行防抖保存 |

**执行原则**：功能包 1–4 是“经验资产 → 可控 AI 生产”的主链，必须按顺序推进；5–9 可在主链稳定后按收益和外部依赖拆分立项。第 9 项包含三个相互独立的 P2 子项，实施时必须拆为单独任务，不得一次性合并。

## 4. 每阶段文档与 GitHub 留存规则

1. 开工前：在本文件补充阶段目标、允许改动文件、数据边界、验收命令和明确不做项。
2. 实现中：Grok 通过协作消息箱报告范围与测试；不得绕过 Codex 审查提交。
3. 完成后：更新本文件、`docs/HANDOFF-next.md`、必要的联调/测试文档，并与代码一同提交、推送到协作分支。
4. 提交前：必须通过 `git diff --check`；按改动范围运行后端测试、lint、build 和 E2E。
5. GitHub 历史：一个可验收阶段至少一个中文提交，提交信息明确功能与阶段；禁止把数据库、密钥、构建产物和协作消息带入提交。

## 5. 当前下一步

### P13-B：已载入编辑版本更新时间可见性（已完成并推送）

**目标**：在技术标/商务标标题区显示当前客户端已接受 editor-state 版本的服务端 UTC `updatedAt`，成功保存或显式重载后更新，失败/409/项目迟到不污染。

**冻结边界**：严格六文件、纯前端、零新请求、零持久化；共享组件只做严格无后缀 UTC ISO 格式化，两份 Hook 复用既有 session/write epoch 接受响应元数据，两份页面只展示。禁止后端、API、数据库、身份、presence、轮询、SSE/WebSocket 和“实时/最后由谁”承诺。完整契约见 `docs/p13b-editor-state-version-freshness-contract.md`，实施计划见 `docs/plans/2026-07-20-p13b-editor-state-version-freshness-plan.md`。

**选择依据**：现有 CAS/冲突 UI 已完整，不重复包装；精确操作者归因必须覆盖浏览器、任务、解析回调、融合与恢复等全部写链和 SQLite 迁移，不能为赶首版只标记浏览器写入而产生错误归因，因此留给独立 P13-D1/D2。

**验收**：真实 failure-first **6 failed / 0 passed**；Grok P13-B/技术商务真值 **6/46 passed**，lint/build 通过。Codex 首轮仅退回 E2E 反假绿，关闭死 GET gate、宽泛计数与缺失真实 PUT abort 后独立 P13-B **6 passed（24.7s）**、lint/diff-check 通过。纯前端展示包未运行后端 pytest，不重复整仓 318 E2E。

### P13-C：当前已载入版本修订来源可见性（已完成）

**目标与结果**：`GET|PUT editor-state` 增加必出可空 `currentRevisionSourceKind`；服务端只投影当前项目最新修订的 `state_version/source_kind`，仅在最新版本与响应版本精确匹配且来源属于既有九类时返回。技术/商务工作区复用唯一九类标签，在 P13-B 时间下显示“当前版本来源”，不增加前端请求。

**快速版边界**：无表/列/迁移，无 actor、用户名、设备、IP、presence、轮询、SSE/WebSocket 或“远端实时最新”承诺；不回扫旧同版本，不改变 13 键哈希、修订写入、去重、裁剪、固定、搜索、恢复与 CAS。完整契约=`docs/p13c-current-revision-source-visibility-contract.md`，计划=`docs/plans/2026-07-20-p13c-current-revision-source-visibility-plan.md`，冻结=`e62ea27`、实现=`6eaa89f`。

**验收**：真实 failure-first 后端 **18 failed**、前端 **5 failed**；Grok 后端 P13-C **18 passed**、P13-B/C E2E **11 passed**、lint/build 通过。Codex 定点回归发现两条旧 P12C 合同冲突并退回 test-only，同时关闭 SQLite PRAGMA 污染和 SQL 宽证据；最终独立后端 **32 + 19 passed**、前端 **11 passed**，lint/py_compile/diff-check/白名单通过。未跑后端全量或整仓 E2E。

**下一步口径**：系统已具备可用的版本时间与流程来源快速第一版。精确操作者归因拆为连续 P13-D1/D2：D1 先为修订与异步任务建立可信可空 actor 账本，覆盖浏览器、任务、revise、两类解析、融合和两类恢复九条写链；D2 再解析当前版本用户名并接入技术/商务标题区。不得只给浏览器写入贴用户名。D1 契约=`docs/p13d1-editor-state-revision-actor-ledger-contract.md`，计划=`docs/plans/2026-07-20-p13d1-editor-state-revision-actor-ledger-plan.md`。真实 MinerU/Docling 制品、用户真实语料调优、外部标讯来源与 Word 整章版式仍分别受本机制品、用户语料、合法授权和跨页视觉决策约束，不混入本包。

### P13-D1：editor-state 修订操作者可信账本（已完成并推送）

**目标**：为 `editor_state_revisions` 与异步 `project_tasks` 增加可空 `actor_user_id`，required 模式只认认证 request state，disabled/旧数据固定未知；九类真实写链在原事务传播 actor。

**关键真实性**：空账本或断链时补入的 `before` 修订 actor 固定 `NULL`，只有真实不同的 `after` 才记录本次 actor；无变化、stale、零恢复和同版本恢复不伪造操作者。P13-D1 不公开用户名或新响应字段，完成后立即推进 P13-D2 展示。

**2026-07-20 完成状态**：冻结=`3132684`，实现=`a8982e3`。Grok 首轮任务/review=`msg_a0c6083215454410b9a95c3c19c54c02`/`msg_1a838890b3384c4cbbd6b238e37d5ede`，failure-first **16 failed / 0 passed**、首轮专项 **16 passed**；Codex 接受 `business_task_service.py` 必要扩围，并因恒真断言、假 worker、signature-only 传播、缺空账本同状态与迁移回滚真证据退回 test-only。返修任务/review=`msg_6cf099e801f544e69efbe51e6eab6c44`/`msg_de747706fcb64a188eef50d77e29d451`，返修后专项 **17 passed**。

**最终验收**：Codex 独立专项+精确 schema **18 passed**、PRAGMA 顺序回归 **2 passed**、融合/恢复/本地票据五条代表性真实事务路径 **5 passed**；py_compile、diff-check、19 个生产哈希和精确暂存白名单均通过。顺序污染真实根因是 P13-C 测试跨池连接恢复 PRAGMA，已改为同一显式连接闭环，未改生产或放宽守卫。本包未跑后端全量、Playwright、前端 lint/build 或整仓 E2E。P13-D1 已推送，下一包可冻结 P13-D2。

### P13-D2：当前已载入版本操作者用户名展示（待 D1 完成后冻结）

**目标**：在最新修订与当前 `stateVersion` 精确匹配时，把可信 actor 解析为当前版本操作者用户名或 `null`，复用 P13-B/C 的接受门和技术标/商务标标题区，不增加轮询或额外前端请求。

**冻结前必须决定**：用户停用、删除、改名时的展示语义；建议缺失或不可安全解析时保守未知，不公开内部 `actor_user_id`，不把当前会话用户冒充历史 actor。P13-D2 不包含历史列表 actor、按 actor 搜索、presence、在线状态、SSE/WebSocket、协同光标/锁、评论、审批或完整审计。

### P13 后续协作主线（未实现）

账号、workspace、RBAC、CAS、冲突提示与任务 SSE 工作空间鉴权已经存在，但真正多人协作仍缺活动空间切换 UI、成员可见性、presence/心跳、协同光标、章节锁/租约、事件广播与游标重放、WebSocket、多任务总线、断线恢复、评论/审批/通知。建议在 P13-D2 后先做不依赖实时协议的工作空间切换与成员可见性，再分别冻结 presence 和事件协议，禁止一次合包。

阶段 0/1/2、阶段 3 M3-A 至 M3-D、阶段 4 **包 5** 至 **包 8/P8B/P8C/P8D/P8E**、P9A/P9B/P9C/P9D、阶段 5 P10A 至 P10K、**P11A/P11B/P11C 三个真实数据收口包**，以及 **P12A/P12B-A/B/C/D/P12C-A/B/C/P12D-A/B/P12E-A/B/C/P12F-A/B/C/D/P13-A** 均保持已交付。P8E 完整契约见 `docs/p8e-docling-local-helper-contract.md`，实施与独立验收记录见 `docs/plans/2026-07-15-p8e-docling-local-helper-plan.md`。

P8D 与 P8E 本机外置解析助手均已完成并推送：P8D 计划=`30d066f`、实现=`e1fe316`、闭环=`38b9318`；P8E 计划=`73b1264`、后端=`79b346e`、助手=`e3f9cc4`。P8E 独立验收为 Docling 46、MinerU 54、后端受影响回归 37、P8C E2E 9、P8B E2E 6 passed；真实 Docling/模型未安装、未验收，自动安装/模型打包/服务端内嵌仍不是已交付能力。

**P12A editor-state 手动检查点只读库已完成并推送**：计划/契约=`bf8ccd6`，后端=`9f53d92`。它只允许用户显式保存当前服务端权威技术/商务 editor-state，固定每项目最近 20 个，列表和淘汰 SQL 不读取正文、详情按工作空间/项目/检查点三重作用域按需读取；不自动记录所有写入，不恢复、不删除、不下载，也不改 P8C/M3-D/普通 PUT。两轮受限审查修复资源投影、提交后假失败、规范 JSON 完整性、跨项目正文加载、完整回滚域和非有限浮点；Codex 独立通过专项 29、受影响回归 97、P8C/异步 callback 15、后端串行全量 518 passed。

**P12B 安全恢复审计四道门已全部完成**：A 计划/契约=`0b55c30`、实现=`780cc82`、闭环=`bf3e86a`，后端全量 537；B 契约/计划=`0636302`、实现=`473e823`，前端全量 201；C 冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`，后端/前端全量 570/212；D 冻结=`613818f`、D1=`551caba`、D2=`0f81dd6`，恢复专项 58、D2 专项 51、受影响回归 81/63、当时后端/前端全量 **599/263 passed**。D2 四轮返修关闭不确定创建阻断、恢复误 PUT、跨项目 token 假绿、不完整水合、非法 create 版本和 HTTP code 冒充内部错误。

**P12C-A 有限自动修订账本基础已完成**：冻结=`daa8c43`、实现=`226e1c1`。独立表与手动/安全检查点配额隔离，每项目最近 10 条；无提交 transition 原语只投影版本/ID、拒绝缺任一权威键的假状态，跨项目/空间裁剪严格隔离。Codex 独立通过专项 **67**、受影响回归 **77**、后端串行全量 **666 passed**。

**P12C-B-A 浏览器 PUT 原子接入已完成**：冻结=`fbf93c0`、实现=`acf3139`。公开 PUT 只传服务端固定 `browser_put`，Codex 独立通过专项 **14**、受影响回归 **107**、后端串行全量 **680 passed**。

**P12C-B-B1 九类任务原子接入已完成**：冻结=`05864f6`、实现=`5a0d1c0`。五类技术任务与四类商务任务每次成功 upsert 固定记录 `task`，批量章节保持逐章提交；非冲突 upsert 内部异常固定脱敏，冲突仍走既有 stale 流程。Codex 独立通过专项 **10**、扩展受影响回归 **126**、后端串行全量 **690 passed**。

**P12C-B-B2 五类商务 revise 原子接入已完成**：冻结=`3a30c03`、实现=`5149385`。两个真实 upsert 写点固定记录 `revise`；结构化解析失败、空 revised、普通技术 revise、陈旧 expected 与 LLM 期间漂移保持本次修订零增量。Codex 独立通过专项 **11**、扩展受影响回归 **147**、后端串行全量 **701 passed**。B2 完成后已只读审计个人 callback 与 P8C 一次性本地解析 callback 的不同事务边界，并据此先交付 C1；content-fuse、checkpoint restore 继续拆包。不得直接跳到任意版本时间线/浏览/回滚、删除、diff 或多人协作。Word `structure` 因缺少容器/跨页视觉决策继续不接线；外部来源和真实语义调优不得合包。

**P12C-B-C1 个人 callback 原子接入已完成**：冻结=`76834f5`、实现=`1d0ce0e`。同一次锁后 before、提交前内存 after 与固定 `callback` 共享个人回调唯一事务；失败时 editor-state/任务/项目/revision 全域回滚。Codex 独立通过专项 **10**、扩大受影响回归 **224**、后端串行全量 **711 passed**。C1 完成后下一主线曾固定为 C2 `local_parser`；其 stale/null 只消费零修订与非版本失败可重用语义随后已由 C2 独立交付。content-fuse、checkpoint restore 继续拆包，不得跳到历史浏览/恢复或多人协作。

**P12C-B-C2 P8C 票据 callback 原子接入已完成**：冻结=`52bbabf`、实现=`82cc82e`。fresh 成功以同一次锁后 before/行和固定 `local_parser` 与票据消费、正文、任务、项目、审计同事务留史；stale/null 只提交消费且零修订，recorder/commit 失败全域回滚并允许同票重用。Codex 独立通过专项 **20**、扩大受影响回归 **272**、后端串行全量 **721 passed**。C2 后只读审计的 content-fuse apply 已由 D1 独立交付；consume 与 checkpoint restore 继续按 D2/D3 拆包，不得跳到历史浏览/恢复或多人协作。

**P12C-B-D1 content-fuse apply 原子接入已完成**：冻结=`e8ffaeb`、实现=`a6a28f6`。一至五条建议同批与章节、恢复批次、裁剪和固定 `content_fuse_apply` 共享唯一事务；空账本记录 before+after，已有基线精确 +1。Codex 返修 consume 隔离假绿后独立通过专项 **11**、扩大回归 **285**、后端串行全量 **732 passed**。D1 当时明确把完整/部分恢复记账与零恢复只消费留给 D2；该包随后已按独立冻结完成，checkpoint restore 继续留给 D3。

**P12C-B-D2 content-fuse consume 原子接入已完成**：冻结=`6b83fc1`、实现=`f256f5b`。完整/部分恢复只在原唯一事务内固定记录一次 `content_fuse_consume`；零恢复继续消费批次但 13 键、`updatedAt`、版本和修订身份序列全等。Codex 两轮仅测试返修关闭部分集合、跨项目/跨空间隔离、精确并发错误码、完整状态全等与 500 脱敏假绿，独立通过专项 **25**、扩大回归 **299**、后端串行全量 **746 passed**。D2 留出的 checkpoint restore 已由 D3 独立完成。

**P12C-B-D3 checkpoint restore 原子接入已完成**：冻结=`1d44484`、实现=`b91a7ff`。不同规范版本恢复固定以 `checkpoint_restore` 留下 before→after transition；同内容仍创建恢复前安全检查点并更新 `updatedAt`，但不伪造修订；回到历史版本形成新时间点。Codex 两轮仅测试返修关闭来源隔离同义反复、失败路径只比版本、裁剪失败不可重试和同内容时间语义弱断言，独立通过专项 **18**、扩大回归 **270**、后端串行全量 **764 passed**。

**P12C-C1 修订历史只读接口已完成**：冻结=`26b504e`、实现=`7023ecd`。列表固定最近 10 条五列元数据且 SQL 不读取 `snapshot_json`；详情以 revision/workspace/project 三重作用域按需读取并重验规范 13 键。Codex 首次审查复现坏时间在 ORM 物化阶段裸 500，受限返修后以真实 SQLite 行关闭越界字节、非法来源、坏时间和正文损坏的固定 500/no-store 门，独立通过专项 **13**、扩大回归 **201**、后端串行全量 **777 passed**。C1 只读边界随后由 C2 复用，未被放宽为客户端投稿快照或来源。

**P12C-C2 修订受限恢复已完成**：冻结=`54af600`、范围修订=`2276366`、实现=`0803250`。POST 只接受执行时 `expectedStateVersion`，锁后复用 C1 三重作用域目标重验；恢复前安全检查点、共享 13 键写回、准确 `revision_restore` 新时间点、10/20 双配额和唯一 commit 属于同一事务。同内容仍更新时间并创建安全检查点，但零修订。Codex 把无真实故障的迁移假证据改成 DROP 前异常，先得到 **1 failed / 22 passed** 并证明临时表残留；零行 DML 触发物理事务返修后，独立通过专项 **23**、四文件 **121**、后端串行全量 **800 passed**。

**P12C-C3 双工作区修订历史前端已完成**：冻结=`6b9143a`、实现=`5e4f9f6`。技术标与商务标共用默认折叠面板，列表只显示时间/中文来源/大小，详情在 API 层把严格 13 键快照压缩为六项有界摘要；恢复复用既有保存链和检查点操作令牌，执行时读最新 expected，成功唯一 editor-state GET。Codex 多轮关闭条件断言、互斥空跑、迟到空跑、详情旧请求覆盖及 arrived 冒充 fulfill 等假绿，独立通过 C3 **21**、checkpoint **51**、truth **46**、前端串行全量 **284 passed**，lint/build/diff/七文件白名单通过。P12C 最小链至此闭环。

**P9C-R1 固定离线模型运行时门已完成并推送**：冻结=`cd70ef0`、实现=`b53dcce`。只允许固定 `BAAI/bge-small-zh-v1.5`、提交 `26478543676740eb665f803ca07f3f7f478857c8`、10 个必需文件与 safetensors SHA-256；显式准备 CLI 是唯一联网路径，生产加载与预检严格本地。Codex 独立完成固定依赖和真实制品验收，真实合成集 Recall@5=`1.0`、NDCG@5=`0.927295`，专项/语义/知识库完整 **17/21/28 passed**，后端全量 **817 passed**。完整契约见 `docs/p9c-fixed-model-runtime-gate-contract.md`，实施计划见 `docs/plans/2026-07-16-p9c-fixed-model-runtime-gate-plan.md`。

**P12D-A 修订与当前状态差异摘要 API 已完成并推送**：冻结=`2cc6ee3`、实现=`9445fcc`。只读组合服务端当前权威 13 键与 P12C-C1 已校验目标修订，逐字段用共享规范 JSON 比较；只返回变更字段名与两侧六项有界计数摘要，不返回正文、字段值、ID 或版本。有效 failure-first **14 failed**，Codex 独立通过专项 **14**、受影响回归 **132**、后端全量 **831 passed**；严格四文件白名单、五域零写、脱敏错误和 `True`/`1` 反假绿均通过。完整契约见 `docs/p12d-revision-current-diff-summary-contract.md`，实施计划见 `docs/plans/2026-07-16-p12d-revision-current-diff-summary-plan.md`。

**P12D-B 技术/商务共用前端对比入口已完成**：冻结=`fc19d93`、实现=`35ab377`、闭环=`c7cf67f`。保留“查看摘要”，新增按需“与当前对比”；严格解析四键响应、13 键有序无重复子集和两侧六项摘要，只显示固定中文字段标签。摘要/比较/恢复互斥，项目、修订、折叠、刷新和恢复以请求代次隔离迟到。真实首轮红测为 **2 failed / 21 passed / 1 did not run**，最终历史 24、检查点 51、truth 46、前端全量 287 passed，lint/build/diff 通过。

**P12E-A 单条修订正文差异预览已完成**：冻结=`5aa205c`、实现=`f9f067e`。只读 GET 返回精确六键和有界章节行差异；前端技术/商务共用按需入口、严格 parser、四意图互斥与 arrived/complete 迟到隔离。Codex 首轮审查复现第 101 个差异章仍进入 difflib，Grok 以真实 **1 failed / 1 passed** 红测返修为 **2 passed**；Codex 独立通过专项/回归/后端全量 **23/27/854**，history/checkpoint/truth/前端全量 **27/51/46/290 passed**。任意历史两两比较、删除、搜索、分页、正文自动恢复和多人协作继续不进入 A 包。

**下一步**：P13-C 快速第一版已完成。下一包继续只读审计剩余主线，优先选择无需外部模型、真实用户语料、来源授权或未决跨页视觉方案的高收益增量；不自动扩成精确操作者、完整多人在线协作或 Word 整章版式。

**P12E-B 已完成并推送**：双修订正文差异后端基础，契约=`docs/p12e-revision-pair-body-diff-contract.md`，计划=`docs/plans/2026-07-17-p12e-revision-pair-body-diff-plan.md`，冻结=`00ef081`、实现=`5a5b08a`。只比较同 workspace/project 的两个历史修订，暂不提供前端入口；Grok 仅改四个后端文件并发送 review_request，Codex 独立验收后提交推送。专项/回归/全量 **13/23/50/867 passed**，合并专项 **86 passed**，仅 1 条既有 Starlette/httpx 弃用告警。

P12E-B 真实 failure-first 为 13 项红测：11 项路由缺失 404、1 项同正文夹具 `stateVersion` 碰撞、1 项 AST 缺少入口；夹具修正后 13 项通过。后端服务复用 P12E-A 的完整值扫描、章节配对、有界 difflib 和脱敏错误，新增路径不读当前 editor-state、不写五域。前端选择器、分页/搜索、恢复/删除/导出/分享、跨项目历史、缓存和多人协作继续留待独立规划。

**P12E-C 已完成并推送**：前端双修订正文差异选择与展示，契约=`docs/p12e-revision-pair-frontend-contract.md`，计划=`docs/plans/2026-07-17-p12e-revision-pair-frontend-plan.md`，冻结=`8b40bf4`、实现=`b6a4375`。Grok 严格只改 API 封装、共用修订面板和既有 history E2E 三文件；选择只存内存且零请求，比较精确一次无 query/body 的 P12E-B GET。真实 failure-first **3 failed / 0 passed**，实现后聚焦 **3 passed**；Codex 独立通过受影响 history **27 passed**、前端全量 **293 passed (8.2m)**，lint/build/diff-check/白名单通过。严格 parser、中文有界展示、零 ID 泄漏、技术/商务共享、五意图互斥和 A0→A1/项目切换迟到隔离已交付；分页、搜索、恢复、删除、导出、分享、缓存、跨项目历史和多人协作仍不在本包。

**P12F-A 已完成并推送**：契约=`docs/p12f-revision-retention-quota-contract.md`，计划=`docs/plans/2026-07-17-p12f-revision-retention-quota-plan.md`，冻结=`e713fb3`、实现=`24f4cf2`。写入账本最多保留 20 条且项目总快照最多 20 MiB，按 `created_at DESC, id DESC` 保留连续最新前缀；默认列表上限独立固定为 10。真实 failure-first **9 failed**；Codex 经一轮 test-only 补强后独立通过六文件专项/受影响回归/后端全量 **121/134/871 passed**。本包未改 API、schema、模型、数据库或前端，未回填旧历史，也未实现分页/搜索/删除/多人协作。

**P12F-B 已完成并推送**：契约=`docs/p12f-revision-cursor-page-contract.md`，计划=`docs/plans/2026-07-17-p12f-revision-cursor-page-plan.md`，冻结=`4ddd896`、实现=`c84a94d`。独立后端只读页固定 `LIMIT 11`/返回最多 10 条，游标按 `created_at DESC,id DESC` 键集位置生成；旧列表 `{items}` 合同和未知查询参数兼容语义不变。真实 failure-first **27 failed / 3 passed**；Codex 一轮返修关闭 Windows 最大时间平台依赖、pre-1970 不可用游标和恒真测试断言，独立通过 **34/171/905 passed**。SQLite 方言仅出现绑定为 0 的被动 OFFSET 占位，源码无主动偏移。P12F-B 当时未实现前端加载更多，后续已由 P12F-C 完成；搜索、删除、筛选、total/hasMore、跨项目历史和多人协作仍未实现。

**P12F-C 已完成并推送**：契约=`docs/p12f-revision-load-more-frontend-contract.md`，计划=`docs/plans/2026-07-17-p12f-revision-load-more-frontend-plan.md`，冻结=`bb1ae3e`、实现=`fe99f5a`。共用面板首屏/刷新/恢复重载改用新页；手动按钮以同步 ref 精确单飞，成功最多追加到 20，失败保留原 items/cursor 与既有意图，同 cursor 可重试；折叠、刷新、项目切换和恢复重载用独立代次隔离迟到分页。真实 failure-first **2 failed / 0 passed / 2 did not run**；Codex 两轮返修关闭空 cursor、假双击、宽泛计数/Cookie/禁止旁路和 knowledge 宽放行，独立通过 **4/34/28/18/51/297 passed**，lint/build/diff/三文件白名单通过。

**P12F-D 已完成并推送**：契约=`docs/p12f-revision-source-filter-contract.md`，计划=`docs/plans/2026-07-17-p12f-revision-source-filter-plan.md`，冻结=`a2acdf3`、实现=`587df9a`。后端 page 以显式 `sourceKind` 在 workspace/project 范围内精确筛选，默认继续 `esrc1 {i,t}`，筛选页使用与来源绑定的 `esrc2 {i,s,t}`；前端共用面板增加“全部来源”与九类中文选项，刷新/恢复/折叠/项目切换和分页迟到语义均闭环。真实 failure-first 后经三轮受限返修关闭测试假绿、契约错误优先级和 SQL 精确证据；Codex 独立后端 **68/48/986**、前端 **3/37/28/18/51/300 passed**，lint/build/编译/白名单均通过。正文/日期或多来源筛选、命名/固定/删除、跨项目历史和多人协作仍未实现。

**P12F-E-A 已完成并推送**：契约=`docs/p12f-revision-time-range-filter-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-plan.md`，冻结=`af3798a`、实现=`c66b69d`。A 包只改后端路由、history service 和新专项测试：严格 `createdFrom` 包含下界、`createdBefore` 排除上界，任一边界存在时以 `esrc3 {b,f,i,s,t}` 绑定范围、可选来源和末条位置；无范围 V1/V2 完全兼容。真实 failure-first **74 failed / 12 passed**；Codex 经一轮受限返修关闭 V3 非法时间语义与 SQL 上界假绿后，独立通过 **87/116/1073 passed**。前端日期控件、时区交互、正文搜索、来源多选及任何写能力仍留后续独立包。

**P12F-E-B 已完成并推送**：契约=`docs/p12f-revision-time-range-filter-frontend-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-frontend-plan.md`，冻结=`a31e50e`、实现=`f9127ec`。只改 API 封装、共用修订面板和既有 history E2E：本地时间草稿经显式应用后严格转 UTC 毫秒，无效零请求保值；已应用时间与来源共同用于首屏、刷新、恢复和 `esrc3` 第二页。真实红测 **0/2/1**；Codex E2E-only 返修审查关闭宽松计数、V3 257 假覆盖、第二页查询串和同项目迟到污染假绿，独立通过 **3/40/28/18/51/303 passed** 及 lint/build/diff/白名单。全量首轮冻结范围外既有双击竞态 **294/1/8** 已保留，检查点独立 51/51 后无代码改动完整复验 303/303。后端、日期预设、正文搜索、来源多选和写能力未进入本包。

**P12F-F-A 已完成并推送**：契约=`docs/p12f-revision-content-search-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-content-search-plan.md`，冻结=`b2eed7c`、实现=`e6516e8`。独立 POST 只扫描来源/时间条件下最新 20 条六列候选，完整校验后以 NFKC+casefold 连续字面搜索严格可见字段，只回五键元数据；缺失/额外键使用固定脱敏 422。真实红测 **18 failed / 3 passed**；两轮受限返修关闭 422 原始 input 泄漏、报价容器对象预算及测试假绿。Codex 独立通过 **23/203/1096 passed**，编译/diff/AST/四文件/空暂存区通过。前端随后由 P12F-F-B 完成；搜索游标/片段、FTS/索引/迁移、缓存和跨项目搜索仍未实现。

**P12F-F-B 已完成并推送**：契约=`docs/p12f-revision-content-search-frontend-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-content-search-frontend-plan.md`，冻结=`4585388`、实现=`be2fe77`。严格三文件接入显式 POST 搜索：草稿/已应用关键词分离，输入零请求，搜索与来源/已应用时间组合，结果最多 20 且无加载更多；刷新/恢复/折叠保留，项目切换重置，四条件迟到隔离。真实红测 **3 failed / 0 passed / 0 did-not-run**；Codex 受限 E2E-only 返修关闭严格坏响应、DEL/C1/astral 码点边界和旧 `catch/finally` 与新 loading 重叠三类假绿，独立通过 **3/43/28/18/51/23/306 passed** 及 lint/build/diff/三文件白名单。最终哈希与消息链见契约第 7 节；自动搜索、片段/高亮、缓存、游标/跨项目搜索和写能力仍未实现。

**P12F-G-A 已完成并推送**：契约=`docs/p12f-revision-delete-backend-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-delete-backend-plan.md`，冻结=`c176cb5`、实现=`d2555d4`。新增单一无 query/body 的 DELETE，成功空 204；独立服务只投影 Project.id，再以 workspace/project/revision 三谓词删除恰好一行并唯一 commit，故障全 rollback。真实 failure-first 为 **10 failed / 3 passed / 0 did-not-run**；首轮实现暴露并行测试污染共享 SQLite、`rowcount=None` 误映射 404、断言假绿和旧历史只读守卫冲突，最终以严格五文件边界修正并关闭。Codex 独立串行通过专项/历史搜索回归/恢复保留回归/鉴权/后端全量 **14/71/93/39/1110 passed**，编译、diff、AST、哈希与空暂存区通过。当前 editor-state、检查点、其它修订及既有 list/page/search/detail/diff/restore 合同不变；前端入口随后由 P12F-G-B 交付，其它版本治理能力仍未实现。

**P12F-G-B 已完成并推送**：契约=`docs/p12f-revision-delete-frontend-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-delete-frontend-plan.md`，冻结=`89b5728`、实现=`bb7c4f4`。严格三文件新增单条“删除”与内联确认；确认前/取消零请求，确认精确一次无 query/body DELETE。成功保留已应用来源/时间/搜索并重载第一批，失败保留列表；project/session/delete generation 隔离旧 success/catch/finally。真实红测 **3/0/0**，两轮受限返修关闭项目旧闭包和 E2E 假绿；Codex 独立通过 **4/47/51/28/18/310 passed** 及 lint/build/diff/白名单/哈希门。后端、共享请求层、workspace hook、多选/批量/软删除/撤销/回收站均未改变。

**P12F-H 已完成并推送**：契约=`docs/p12f-revision-display-name-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-display-name-plan.md`，初始冻结=`0660145`、两次范围修订=`0db935b`/`aca68b6`、实现=`b4338ba`。最终严格十七文件为修订行增加 nullable `display_name`，新增单条 PATCH 和 list/page/search/detail 六键元数据，技术/商务共用面板原位保存/覆盖/清除，并机械同步六份既有精确元数据合同与一份真实 SQLite 列集合基线。Codex 后端独立 **30/240/132/1140 passed**，前端 **5/52/51/28/18/315 passed**，lint/build/py_compile/diff/哈希/静态门通过；首轮全量单个既有 P8B 瞬时导航失败已独立及完整复验闭合。名称不进入搜索匹配、游标、恢复复制或裁剪保护；固定/置顶、批量、检查点命名、跨项目历史与多人协作均未授权。

**P12F-I 已完成并推送**：契约=`docs/p12f-revision-display-name-search-contract.md`，计划=`docs/plans/2026-07-18-p12f-revision-display-name-search-plan.md`，冻结=`060191e`，实现=`008e443`。严格四文件复用既有 POST/search SQL 七列与六键响应；后端完整验证全部候选后，以同一 NFKC+casefold 规则联合匹配非空名称和既有可见字段，同一行只返回一次且保持倒序。前端只修正“名称或内容搜索”入口/活动/失败/空态文案并补技术/商务 E2E；路由、Schema、API、数据库、索引、候选 20 与所有写能力保持冻结。Codex 独立通过后端 **29/247/1146 passed**、前端 **3/55/51/28/18/318 passed** 以及 lint/build/py_compile/diff/四文件/哈希门；验收回执=`msg_d954063f489248babb027b9bb335f666`。下一主线必须重新只读审计并单独冻结，固定/置顶及裁剪保护不得直接沿用本包边界。

**P12F-J-A 已完成**：契约=`docs/p12f-revision-pinning-backend-contract.md`，计划=`docs/plans/2026-07-19-p12f-revision-pinning-backend-plan.md`，冻结=`2f03b8c`，实现=`a7021c4`，Grok review=`msg_88f4752ef1cf4a929c6b194df00d9398`，Codex ack=`msg_c630805296ac48d6941809bbca957b7f`。最终独立结果 **16/96/1/1165 passed**；SQLite 非法 `is_pinned=2` 通过原始整数投影被固定 500/裁剪整次回滚，迁移中途 DROP 回滚、execute/flush/commit 零写均有真实证据。前端、七键历史响应、固定按钮与 E2E 留给 P12F-J-B。

**P12F-J-B 已完成**：契约=`docs/p12f-revision-pinning-frontend-contract.md`、计划=`docs/plans/2026-07-19-p12f-revision-pinning-frontend-plan.md`，冻结=`f019a4b`，实现=`5ef7abd`，Codex ack=`msg_8399a348aa1543e2b4b61cbdd25b4ac9`。严格十四文件把 list/page/search 扩为七键、detail 扩为八键，四类 SQL 以原始 Integer 投影拒绝坏固定值；前端严格解析、精确一键 PATCH、固定/取消入口、全局单飞、全操作互斥、成功原位更新、失败保值与 A→B 迟到隔离全部交付。Codex 独立串行通过后端 **297/1170 passed**、前端 **6/61/51/28/18 passed**，lint/build/py_compile/diff/哈希/静态门通过。排序、游标、候选上限、P12F-J-A 配额/裁剪不变；固定排序、批量、检查点命名、跨项目历史和多人协作继续另包。

**P12G 已完成并推送**：契约=`docs/p12g-checkpoint-display-name-contract.md`，计划=`docs/plans/2026-07-19-p12g-checkpoint-display-name-plan.md`，冻结=`9696ec1`，实现=`077e7d4`，Codex ack=`msg_cd2908a39cc1438186b0f41d13062443`。严格十二文件为手动/安全检查点增加 nullable 展示名称、独立三重作用域单列 PATCH、create/list/detail 七/七/八键元数据，以及技术标/商务标共用面板原位保存/覆盖/清除。Codex 首轮审查关闭 `.get()` 缺键掩盖、伪单飞和 A→B 假重叠测试；独立串行通过后端 **62/47/1203 passed**、前端 **8/59/61/28/18 passed**，lint/build/py_compile/diff/白名单/哈希门通过。创建请求仍精确 `{}`，安全检查点初始名称固定 null；名称不进入快照、恢复、排序、20 条裁剪或自动修订。检查点搜索/固定/删除/下载、跨项目历史和多人协作继续另包。

**P12H 已完成并推送**：契约=`docs/p12h-checkpoint-delete-contract.md`，计划=`docs/plans/2026-07-19-p12h-checkpoint-delete-plan.md`，冻结=`b81546e`，实现=`1ff8839`，Codex ack=`msg_c7168985bed9415ab1fc44420474d857`。严格七文件交付无 query/body 的单条检查点 DELETE、独立 Project.id+三谓词删除服务、技术/商务共用内联确认、真同步单飞、失败保值和 A→B 双 hold 隔离。首轮 Grok 因 402 中断且缺正式 failure-first 回执，Codex 不补造计数；随后以真实前端 **8/1** 和代码审查下发两文件返修，关闭空体弱 OR、假 disabled、泄漏门和恢复→删除互斥漏洞。Codex 独立通过后端 **43/80/1217**、前端 **9/68/61/28/18 passed**，lint/build/py_compile/diff/哈希门通过。模型、迁移、Schema、核心恢复服务、页面/hook 与修订历史均未扩围。

**P12I 已完成并推送**：契约=`docs/p12i-checkpoint-search-contract.md`，计划=`docs/plans/2026-07-19-p12i-checkpoint-search-plan.md`，冻结=`86cc1a3`，实现=`8c41bbc`，Codex ack=`msg_608e5dda4d59453b83ab068ce9879fbf`。严格六文件新增 POST search、后端专项、前端 API/共用面板和既有 checkpoint E2E；候选固定当前项目最近 20 条，先完整重验名称与规范快照，再用 NFKC+casefold 匹配名称或可见内容，只返回既有七键元数据。Codex 首轮审查关闭失败同词不可重试、active refresh 双飞和多项反假绿缺口；独立串行通过后端 **18/123/1235 passed**、前端 **8/76/61/28/18 passed**，lint/build/py_compile/diff/哈希门通过。模型、Schema、迁移、索引、分页、固定、跨项目或多人协作仍未进入本包。

**P12J-A 已完成并推送**：契约=`docs/p12j-checkpoint-pinning-backend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-backend-plan.md`，冻结=`9f304da`、实现=`8edebd4`。严格九文件交付检查点 `is_pinned`、SQLite 迁移、5 条/10 MiB 配额、精确单条 PATCH 与固定/安全双保护裁剪；P12J-A 当时保持 create/list/search 七键、detail 八键、前端、显式删除和恢复 transition 不变，响应/UI 后续已由 P12J-B 交付。Grok 初始 failure-first **16 failed / 3 passed**；Codex 审查下发真实 **2 failed** 返修，关闭不完整迁移误判、空候选保护 ID 和真实 5+15 边界缺口。Codex 独立串行通过 **23/140/1258 passed**，py_compile、diff-check、精确九文件、空暂存区与哈希门通过。

**P12J-B 已完成并推送**：契约=`docs/p12j-checkpoint-pinning-frontend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-frontend-plan.md`，代码哈希基线=`262683e`、冻结=`65fe259`、口径澄清=`1471c31`、实现=`7d1d5c9`。严格十一文件把 create/list/search 七键、detail 八键升级为含 `isPinned` 的八/九键，后端三处原始 Integer 投影拒绝非法固定值；共用 checkpoint API/面板交付严格 parser、一键 PATCH、badge、全局单飞、全部操作互斥、active search 原位更新和 A→B success/catch/finally 隔离。真实 failure-first **6 failed**；Codex 独立串行通过后端 **120/1261 passed**、前端 **6/82/61/28/18 passed** 及 lint/build/py_compile/diff/哈希门。Grok 曾遇到一次既有 history 双击元素 detached，未改代码与 Codex 独立复验均 **61 passed**，作为非阻断稳定性风险保留。表/迁移/pin service/配额/裁剪、页面/hook/共享请求层及其它主线保持冻结。

**P12K 已完成并推送**：契约=`docs/p12k-checkpoint-pinned-first-list-contract.md`，计划=`docs/plans/2026-07-19-p12k-checkpoint-pinned-first-list-plan.md`，代码审计基线=`90cfd58`、契约冻结=`fe0fa08`、启动口径修订=`ff48495`/`6666af6`、实现=`3c3cbf9`，Codex ack=`msg_3048a39db0c04969978a7e2dd7ea0c60`。严格两文件把默认 GET 列表改为 `is_pinned DESC,created_at DESC,id DESC`；search 继续最新 20 条 `created_at DESC,id DESC`，前端当前列表仍只原位更新、下一次默认 GET 才重排。failure-first **8 failed / 4 passed**；Grok 串行通过专项/受影响集/全量 **12/132/1273 passed**，Codex 独立受影响集 **132 passed** 并通过编译、diff、哈希与 SQL/AST 门，按分级策略未重复全量。表/迁移/Schema/API/pin service/配额/裁剪/前端和其它主线全部保持冻结。

**P12L 已完成并推送**：契约=`docs/p12l-checkpoint-pinned-count-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12l-checkpoint-pinned-count-frontend-plan.md`，代码哈希基线=`5258f84`、契约冻结=`4526832`、启动口径=`d21cfb5`、实现=`cc6bf11`，Codex ack=`msg_a685c7123a4f4c9fac68481b99a25cec`。严格两文件在技术标/商务标共用 checkpoint 面板显示默认列表固定条数与 5 条上限，并以既有 E2E 覆盖 pin/unpin/delete/失败/搜索隐藏/项目隔离；数量纯派生且零新增请求。真实 failure-first **4 failed / 1 passed**；Grok 聚焦/受影响 checkpoint **5/87 passed**，Codex 独立聚焦 **5 passed**，lint/build 通过。后端/API、字节容量、分组/重排和其它主线全部冻结。

**P12M 已完成**：契约=`docs/p12m-revision-search-match-reasons-contract.md`，计划=`docs/plans/2026-07-20-p12m-revision-search-match-reasons-plan.md`，冻结=`95b298f`、实现=`cc23542`。修订搜索成功项已增加精确 `matchReasons` 八键，按固定顺序说明名称、可见内容或双命中；技术/商务共用面板显示固定中文标签。真实 failure-first **3 failed**；Grok 搜索专项 **33 passed**、P12M/受影响 history E2E **2/6 passed**。受影响后端首轮 **265 passed / 2 failed** 只暴露两份旧七键测试，获 Codex 明确 test-only 扩围后两条均通过；Codex 独立后端 **1/1/3 passed**、前端 **2/6 passed**，lint/py_compile/静态门通过，未重复后端全量或整仓 318 E2E。候选 20 条、排序、过滤、完整校验、错误/零写和一次 POST 不变；正文片段、高亮、自动搜索、FTS、缓存、跨项目版本、完整时间线及多人协作仍未实现。

**P12N 已完成**：契约=`docs/p12n-revision-loaded-pinned-first-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12n-revision-loaded-pinned-first-frontend-plan.md`，冻结=`337b401`、实现=`394639a`。严格两文件在非 active search 时对当前已加载 `items` 做 render 期稳定固定/普通分组；pin/unpin 与加载更多成功后立即反映，搜索保持服务端顺序。真实 failure-first **4 failed / 1 passed**；Grok P12N/受影响 history **5/12 passed**，Codex 独立 **5 passed**，lint/build/静态门通过，未跑完整 history、整仓 318 E2E 或后端 pytest。后端、API、esrc 游标和请求数未变；尚未加载固定项提前进入第一页仍未实现。

**P13-A 已完成并推送**：契约=`docs/p13a-task-sse-workspace-auth-contract.md`，计划=`docs/plans/2026-07-17-p13a-task-sse-workspace-auth-plan.md`，冻结=`e8dfa61`、实现=`1509aa2`。SSE 连接前短 Session 复用统一 workspace/成员/bid_writer 解析，流内每轮按 workspace/project/task 再校验；disabled、原生 EventSource、事件/回退不变。真实 failure-first **8 failed / 5 passed**；Codex 一轮 test-only 返修关闭恒真泄漏断言、secret marker 跳过和宽松三参，独立通过 **13/72/918 passed**。首次全量只因 20 分钟外层时限不足终止，40 分钟外层干净重跑为 **918 passed in 1310.97s**。
## P12D-B 完成状态（2026-07-17）

P12D-B 技术/商务共用前端修订对比入口已完成。Grok 任务 `msg_a8258d4b49f44678bf43fe2a2356d583`，仅修改三文件白名单并未提交；Codex 独立通过历史 24、检查点 51、技术/商务真值 46、前端全量 287 passed，lint/build/diff 通过。首轮红测实际为 2 failed / 21 passed / 1 did not run，因串行分组在首个缺失入口失败后跳过一条；该过程偏差已保留为事实，不冒充 3/21。

本包交付边界：只读单条历史修订与请求时当前状态的对比呈现；保留摘要入口；不新增后端、不触发写入、不读取详情正文、不做正文 diff、任意历史两两比较、删除、搜索、分页、导出、分享或多人协作。下一包需基于新需求另行规划、冻结并通过消息箱交 Grok。

## P12E-A 完成交接（2026-07-17）

P12E-A 冻结=`5aa205c`、实现=`f9f067e`。Grok 任务经历 402 额度中断后恢复，完成七文件受限返修并发送 review_request=`msg_c24f270186a741a09a33781e84b1e762`；Codex 首轮审查以真实红测发现第 101 个正文差异章仍进入 difflib，返修任务=`msg_f09905515e974049827cd981087884c6`，红测 **1 failed / 1 passed**、修后 **2 passed**。

Codex 独立通过后端专项/受影响回归/全量 **23/27/854 passed**（1 条既有 Starlette/httpx 弃用告警），前端 history/checkpoint/truth/全量 **27/51/46/290 passed**；Playwright 全部单 worker、零重试串行，lint/build/diff-check/精确七文件/空暂存区均通过。P12E-A 本身只覆盖单条历史修订对请求时当前状态；双历史修订手动比较随后已由 P12E-B/C 完成。正文自动恢复、自动批量比较、删除、搜索、分页或多人协作仍未实现。
