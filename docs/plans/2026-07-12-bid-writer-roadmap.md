<!--
模块：标书制作者能力补全与角色化演进路线图
用途：锁定标书制作者优先开发顺序、阶段验收和后续多角色边界。
对接：docs/HANDOFF-next.md、docs/integration-checklist.md、Grok-Codex 协作消息箱。
二次开发：每阶段开工前补充本文件对应小节；完成后更新验收结果和未做项，文档必须与代码一同提交至 GitHub。
-->

# 标书制作者能力补全与角色化演进路线图

> **状态**：阶段 0/1/2 已完成；阶段 3 M3-A=`5d37dba`、M3-B=`e2e5d04`、M3-C 计划=`c63310f`/实现=`b8ff605`、M3-D 计划=`d326c7d`/后端=`6a5f61f`/前端=`b89a387` 均已完成；阶段 4 **包 5** 已推送（`460097a`）；**包 6** 已推送（`1289c92`）；**包 7** 已推送（`2c7b3e0`）；**包 8/P8B/P8C/P8D/P8E** 均已完成（调度=`6db1586`，P8B 计划=`f662674`/后端=`0994cc8`/前端=`80d2579`，P8C 计划=`cabe99d`/后端=`af39ff8`/前端=`1cf5576`，P8D 计划=`30d066f`/助手=`e1fe316`，P8E 计划=`73b1264`/后端=`79b346e`/助手=`e3f9cc4`；两种真实 CLI/模型均需人工准备）；包 9A 与 P9D 已完成。阶段 5 已完成 P10A 至 P10K；P10K 计划=`2e53007`、后端=`1eaa75e`、前端=`dbf301c`。P11A/P11B/P11C 三个真实数据收口包均已完成。P12A 手动检查点只读库、P12B-A/B/C/D 四道安全恢复门、P12C-A 有限自动修订账本基础和 P12C-B-A 浏览器 PUT 原子接入均已完成；P12C-B-A 冻结=`fbf93c0`、实现=`acf3139`，后端/前端串行全量基线为 **680/263 passed**。自动历史目前只覆盖公开浏览器 PUT，其余生产写入者尚未接入。
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
- 当前已具备中标内容模板资产化、多模板/卡片融合、差异确认、有限持久恢复、统一卡片库和受限角色数据域。真实剩余缺口集中在通用版本历史/多人协作、真实 MinerU/Docling 生产部署、P9C 固定模型运行时门与真实语义调优、Word `structure`/整章布局、更多合法外部标讯来源及完整生产部署治理。导出版式模板与中标内容模板是两个不同概念，后续不得混用术语。
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

阶段 0/1/2、阶段 3 M3-A 至 M3-D、阶段 4 **包 5** 至 **包 8/P8B/P8C/P8D/P8E**、P9A/P9B/P9C/P9D、阶段 5 P10A 至 P10K、**P11A/P11B/P11C 三个真实数据收口包**，以及 **P12A/P12B-A/P12B-B/P12B-C/P12B-D/P12C-A/P12C-B-A** 均保持已交付。P8E 完整契约见 `docs/p8e-docling-local-helper-contract.md`，实施与独立验收记录见 `docs/plans/2026-07-15-p8e-docling-local-helper-plan.md`。

P8D 与 P8E 本机外置解析助手均已完成并推送：P8D 计划=`30d066f`、实现=`e1fe316`、闭环=`38b9318`；P8E 计划=`73b1264`、后端=`79b346e`、助手=`e3f9cc4`。P8E 独立验收为 Docling 46、MinerU 54、后端受影响回归 37、P8C E2E 9、P8B E2E 6 passed；真实 Docling/模型未安装、未验收，自动安装/模型打包/服务端内嵌仍不是已交付能力。

**P12A editor-state 手动检查点只读库已完成并推送**：计划/契约=`bf8ccd6`，后端=`9f53d92`。它只允许用户显式保存当前服务端权威技术/商务 editor-state，固定每项目最近 20 个，列表和淘汰 SQL 不读取正文、详情按工作空间/项目/检查点三重作用域按需读取；不自动记录所有写入，不恢复、不删除、不下载，也不改 P8C/M3-D/普通 PUT。两轮受限审查修复资源投影、提交后假失败、规范 JSON 完整性、跨项目正文加载、完整回滚域和非有限浮点；Codex 独立通过专项 29、受影响回归 97、P8C/异步 callback 15、后端串行全量 518 passed。

**P12B 安全恢复审计四道门已全部完成**：A 计划/契约=`0b55c30`、实现=`780cc82`、闭环=`bf3e86a`，后端全量 537；B 契约/计划=`0636302`、实现=`473e823`，前端全量 201；C 冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`，后端/前端全量 570/212；D 冻结=`613818f`、D1=`551caba`、D2=`0f81dd6`，恢复专项 58、D2 专项 51、受影响回归 81/63、当时后端/前端全量 **599/263 passed**。D2 四轮返修关闭不确定创建阻断、恢复误 PUT、跨项目 token 假绿、不完整水合、非法 create 版本和 HTTP code 冒充内部错误。

**P12C-A 有限自动修订账本基础已完成**：冻结=`daa8c43`、实现=`226e1c1`。独立表与手动/安全检查点配额隔离，每项目最近 10 条；无提交 transition 原语只投影版本/ID、拒绝缺任一权威键的假状态，跨项目/空间裁剪严格隔离。Codex 独立通过专项 **67**、受影响回归 **77**、后端串行全量 **666 passed**。

**P12C-B-A 浏览器 PUT 原子接入已完成**：冻结=`fbf93c0`、实现=`acf3139`。公开 PUT 只传服务端固定 `browser_put`，锁后 before、写后 after 与账本记录共用同一事务；冲突、记录失败与 commit 失败均证明 editor-state/revision 双零写。Codex 独立通过专项 **14**、受影响回归 **107**、后端串行全量 **680 passed**。当前自动历史只覆盖公开浏览器 PUT。下一主线固定先只读审计 task/revise 的所有调用、锁与 commit/rollback 边界，再冻结 P12C-B-B 最小接入包；两类 callback、content-fuse、checkpoint restore 继续按事务边界拆包。不得直接跳到任意版本时间线/浏览/回滚、删除、diff 或多人协作。Word `structure` 因缺少容器/跨页视觉决策继续不接线；外部来源和真实语义调优不得合包。
