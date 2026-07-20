# 新会话交接：biaoshu（当前有效）

> **交接日期**：2026-07-20（P12L 固定名额提示已闭环；下一包待只读审计）
> **仓库本地**：`C:\Users\Administrator\biaoshu`
> **GitHub**：https://github.com/wmjagpjm/biaoshu
> **当前工作分支**：`collab/grok-code-codex-review`（协作分支；**勿直接当 main**）
> **协作分支功能基线**：P12F-J-A 冻结=`2f03b8c`、实现=`a7021c4`；P12F-I 冻结=`060191e`、实现=`008e443`；P12F-H 冻结=`0660145`、范围修订=`0db935b`/`aca68b6`、实现=`b4338ba`；P12F-G-B 冻结=`89b5728`、实现=`bb7c4f4`；P12F-G-A 冻结=`c176cb5`、实现=`d2555d4`；P12F-F-B 冻结=`4585388`、实现=`be2fe77`；P12F-F-A 冻结=`b2eed7c`、实现=`e6516e8`；P12F-E-B 冻结=`a31e50e`、实现=`f9127ec`；P12F-E-A 冻结=`af3798a`、实现=`c66b69d`；P12F-D 冻结=`a2acdf3`、实现=`587df9a`；P13-A 冻结=`e8dfa61`、实现=`1509aa2`；P12F-C 冻结=`bb1ae3e`、实现=`fe99f5a`；P12F-B 冻结=`4ddd896`、实现=`c84a94d`；P12F-A 冻结=`e713fb3`、实现=`24f4cf2`；P12E-A 冻结=`5aa205c`、实现=`f9f067e`；P12E-B 冻结=`00ef081`、实现=`5a5b08a`；P12E-C 冻结=`8b40bf4`、实现=`b6a4375`；其余既有功能基线见本文 §11。新会话必须以 `git rev-parse HEAD` 与远端分支一致为准。
> **最新增量基线**：P12J-A 已交付检查点固定列/迁移、5 条/10 MiB 配额、单条 PATCH 与固定/安全双保护裁剪；冻结=`9f304da`、实现=`8edebd4`。P12J-B 已交付固定状态八/九键读取与技术/商务共用前端入口；代码哈希基线=`262683e`、冻结=`65fe259`、口径澄清=`1471c31`、实现=`7d1d5c9`。P12K 已交付默认检查点列表固定优先；冻结=`fe0fa08`、启动口径修订=`ff48495`/`6666af6`、实现=`3c3cbf9`。P12L 已交付固定名额提示；契约冻结=`4526832`、启动口径=`d21cfb5`、实现=`cc6bf11`。
> **参考 `origin/main`**：`4847a9d` — docs: 重写换会话交接并强制注释规范专章（非当前工作 HEAD）
> **本地状态**：只允许分支 `collab/grok-code-codex-review`；P12L 契约冻结=`4526832`、启动口径=`d21cfb5`、实现=`cc6bf11` 已推送，中文闭环文档正在本次提交。
> **验收基线**：P12K Grok 串行专项/受影响集/后端全量 **12/132/1273 passed**；Codex 独立受影响集 **132 passed**。P12L Grok 聚焦/受影响 checkpoint **5/87 passed**，Codex 独立聚焦 **5 passed**；lint/build 通过，未重复受影响套件或整仓 318 E2E。**所有 pytest 与 Playwright E2E 共用 SQLite 重置库，pytest 禁止 xdist/并发分组，Playwright 必须显式 `--workers=1 --retries=0` 逐条串行运行；按风险分级验收，避免 Grok 与 Codex 重复全量。**

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu 标书制作者剩余主线任务。仓库 C:\Users\Administrator\biaoshu，GitHub https://github.com/wmjagpjm/biaoshu.git。
工作分支只能是 collab/grok-code-codex-review，禁止直接操作 main；先执行 git status -sb，并核对 HEAD 与 origin/collab/grok-code-codex-review 一致且工作区干净。
完整阅读 docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md、docs/integration-checklist.md。
长期目标：持续完成卡片化知识与素材库、多模板融合与可控 AI 编写、质量与交付闭环；每包必须独立规划、限定实现、Codex 审查与独立验收、中文文档闭环、推送协作分支。
当前进度：P12A、P12B-A/B/C/D、P12C-A/B/C、P12D-A/B、P12E-A/B/C、P12F-A/B/C/D/E-A/E-B/F-A/F-B/G-A/G-B/H/I/J-A/J-B/P12K/P12L、P13-A、P9D、P9C-R1、M3-A 至 M3-D、P8B/P8C/P8D/P8E、P9A/P9B/P9C、P10A 至 P10K、P11A/P11B/P11C 均已完成。P12L 实现=`cc6bf11`；Grok 聚焦/受影响 checkpoint **5/87 passed**，Codex 独立聚焦 **5 passed**，前端整仓 **318 passed** 仍仅作既有基线。
当前状态：修订历史已闭合来源、UTC 时间、联合搜索、游标分页、单条删除、展示名称、固定与保护性裁剪；检查点已有创建、列表、详情、安全恢复、展示名称、单条删除、当前项目显式搜索、固定状态读取/入口、固定/安全双保护裁剪及默认列表固定优先排序。
当前执行包：无；P12L 已完成并推送，契约=`docs/p12l-checkpoint-pinned-count-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12l-checkpoint-pinned-count-frontend-plan.md`。
下一步：审计剩余主线并重新冻结下一包。Grok 默认跑专项/受影响集；Codex 独立复核并按风险至多补一次全量，禁止机械重复。后端/API、字节容量、分组/重排、分页、跨项目版本、完整时间线和多人协作不得顺手扩入。
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

### 2.3 功能注释齐备表（交接审计 · 2026-07-16）

图例：**齐** = 文件顶含模块+用途+对接（核心服务另有二次开发）；**部分** = 有用途但缺对接/二次开发或仅部分文件；**弱/无** = 缺文件顶或仅零星行内注释。

#### 后端 `backend/app`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 应用入口 | `main.py` | **齐** | 含二次开发 |
| 项目 CRUD | `services/project_service.py`、`api/projects.py` | **齐** | kind/business 与 editor-state responseMatrix 映射已写清 |
| 任务引擎 | `services/task_service.py`、`api/tasks.py`、`tests/test_p13a_task_sse_workspace_auth.py` | **齐** | 取消、biz 分发、RAG 注入；P13-A 连接前短 Session 统一角色/成员/活动空间，流内 workspace 三层再校验；含 `content_fuse` |
| 模板/卡片融合 M3-A | `services/fuse_context_service.py`、`services/task_service.py`（content_fuse）、`tests/test_content_fuse.py` | **齐** | 只读建议；禁写 editor-state；跨 workspace 不泄漏；sourceRefs 含服务端 title；裁剪后 *Used |
| 融合写入持久恢复 M3-D | `models/entities.py`、`api/content_fuse_applications.py`、`services/content_fuse_application_service.py`、`api/schemas.py`、`tests/test_content_fuse_applications.py` | **齐** | 任务结果权威、锁内 base 校验、原子写入/快照/裁剪、最近 20 批、漂移安全一次消费；后端专项 34 passed |
| 商务任务 | `services/business_task_service.py` | **齐** | qualify/toc/quote/commit |
| 编辑态 | `services/editor_state_service.py` | **齐** | business_json、response_matrix_json 规范化与死引用收敛；P12B-A 共享 13 键版本、一次锁后 CAS、提交前响应和非有限值兼容 |
| 编辑态手动检查点 P12A | `models/entities.py`、`api/editor_state_checkpoints.py`、`services/editor_state_checkpoint_service.py`、`api/schemas.py`、`tests/test_editor_state_checkpoints.py` | **齐** | 服务端权威 13 键标准 JSON、最近 20 条、最小 SQL、完整显式回滚、只读详情；专项 29、后端全量 518 passed |
| 检查点固定与保护裁剪 P12J-A | `models/entities.py`、`core/database.py`、`services/editor_state_checkpoint_service.py`、`services/editor_state_checkpoint_pin_service.py`、`api/schemas.py`、`api/editor_state_checkpoints.py`、`tests/test_p12j_checkpoint_pin.py` | **齐** | 5 条/10 MiB、原始 Integer 投影、SQLite 原子重建、单条 PATCH、固定行与本轮安全点双保护；专项/回归/全量 23/140/1258 passed |
| 编辑态全状态版本 P12B-A | `api/projects.py`、`api/schemas.py`、`services/editor_state_service.py`、`services/editor_state_checkpoint_service.py`、`tests/test_editor_state_full_version.py` | **齐** | GET/PUT `stateVersion`、可选 expected CAS、固定最小 409、矩阵冲突优先级；专项 19、后端全量 537 passed |
| 编辑态有限修订与受限恢复 P12C | 后端 `models/entities.py`、revision 三服务、`api/editor_state_revisions.py`、C1/C2 测试；前端 `editor-state-revisions/*`、技术/商务 hooks/pages、C3 E2E | **齐** | 最近 10 条、九类来源、列表五列无正文、按需有界摘要、expected CAS、安全检查点、原子恢复、共享令牌/保存链与迟到隔离；C2 后端 23/121/800，C3 前端 21/51/46/284 passed |
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
| 投标人匿名合规 P10E | `api/deps.py`（require_bidder）、`api/bidder.py`、`services/bidder_compliance_preview_service.py`、`api/schemas.py`、`tests/test_bidder_compliance_preview.py` | **齐** | strict `bidder` 只读聚合当前空间技术标收敛矩阵；匿名五计数、`no-store`、无表/无任务、审计 target 固定；`AUTH_MODE=disabled` 与所有者不放行 |
| 投标人项目合规 P10G | `api/bidder.py`、`services/bidder_project_compliance_service.py`、`api/schemas.py`、`tests/test_bidder_project_compliance.py` | **齐** | strict `bidder` 只读当前空间技术标 `id/name` 选择器和按需单项目五计数；跨空间/不存在/商务标统一固定 404、`no-store`、详情审计 target 固定；不返回项目字段、矩阵原文、人员或财务数据 |
| 人员业绩 P10H | `models/entities.py`（HrPerformanceCardRow）、`api/hr.py`、`api/schemas.py`、`services/hr_performance_service.py`、`tests/test_hr_performance_cards.py` | **齐** | strict `hr` 当前空间独立业绩卡；摘要/详情分离、严格年份/布尔、固定 404/422、`no-store`、CSRF 与审计业务字段脱敏；后端定向 14 passed |
| 资质到期提示 P10I | `api/hr.py`、`api/schemas.py`、`services/hr_credential_expiry_service.py`、`tests/test_hr_credential_expiry.py` | **齐** | strict `hr` 当前空间只读提示；SQL 仅投影必要列、UTC 日期与固定 90 天窗口、有效卡只计数、停用卡排除、固定审计脱敏；后端定向 14 passed |
| 财务个人成本记录 P10J | `api/finance.py`、`api/schemas.py`、`services/finance_cost_change_event_service.py`、`tests/test_finance_cost_change_events.py` | **齐** | strict `finance` 本人当前空间最近 50 条成功成本变更；SQL 三列投影，字面前缀/非空后缀/无首尾空白均在上限前过滤，读取审计固定脱敏；后端定向 16 passed |
| 财务项目成本记录 P10K | `models/entities.py`、`api/finance.py`、`api/schemas.py`、`services/finance_cost_service.py`、`services/finance_project_cost_change_event_service.py`、`tests/test_finance_project_cost_change_events.py` | **齐** | strict `finance` 当前空间商务标上线后最近 50 条；三写路径与业务/原审计同事务，四列 SQL 投影、LIMIT 前过滤、匿名 actorScope 和固定读取审计；后端定向 21 passed |
| 实体 | `models/entities.py` | **部分** | 类 docstring 齐；文件顶视历史版本；KnowledgeCardRow / BidTemplateRow 已补语义 |
| 测试 | `backend/tests/*.py` | **齐/部分** | 含 `test_content_fuse`、`test_knowledge_cards`、`test_bid_templates` 及标题边框/SSE/标讯/资源/响应矩阵等 |

#### 前端 `frontend/src/features`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 技术标工作区 | `technical-plan/pages/TechnicalPlanWorkspace.tsx` | **齐** | ResponseMatrixPanel；串行 `response_match`；编写步 M3-A/M3-B 融合入口；P8B `light/local/ask` 解析决策 |
| 模板/卡片融合 UI | `technical-plan/components/ContentFuseDialog.tsx`、`lib/contentFuse.ts`、`lib/contentFuseApplications.ts`；E2E `e2e/content-fuse-suggest.spec.ts`、`content-fuse-apply.spec.ts`、`content-fuse-persistent-recovery.spec.ts` | **齐** | M3-A 只读建议；M3-B 双栏预览；M3-D 服务端原子确认、最近 20 批、完整/部分/零恢复、一次消费、固定失败语义与迟到隔离；`test:e2e:fuse` / `fuse-apply` / `fuse-persistent-recovery` |
| 技术标 hooks | `useProjectPipeline` / `useTechnicalPlanEditors` / `useProjectGuidance` | **齐** | SSE、项目切换隔离、取消终态保护、正文图片上传、responseMatrix；`reloadFromApi` 为 M3-D 提供单次 `Promise<boolean>` 真实重载结果，其他调用方可保持旧静默语义；TaskType 含 content_fuse |
| P9D 导出图片告警 | `shared/components/ExportImageWarnings.tsx`、技术标/商务标导出页、`e2e/export-image-warnings.spec.ts` | **齐** | 20 条/240 码点纯文本收敛；双页面不阻断下载；项目绑定与迟到代次隔离；`test:e2e:export-image-warnings` |
| 响应矩阵 | `technical-plan/lib/responseMatrix.ts`、`hooks/useTechnicalPlanEditors.ts`、`components/ResponseMatrixPanel.tsx`、`pages/TechnicalPlanWorkspace.tsx`；E2E conflict/refresh/suggest-apply/source-pagination/field-merge | **齐** | sourceKey 合并、跨批建议择优、409 字段级三方合并预览、仅矩阵 PUT、双 context E2E |
| P11A 核心项目真值 | `technical-plan/lib/projectStore.ts`、技术标列表/新建、创建方案、商务标列表/工作区、查重/废标选择器；`e2e/core-project-data-truth.spec.ts` | **齐** | `/api/projects*` 单一真值；真实空态与固定失败；零 mock/localStorage 假成功；项目存储键族与 pending 反假绿；`test:e2e:core-project-data-truth` |
| outlineTree | `technical-plan/lib/outlineTree.ts` | **齐** | markdownToOutline |
| 商务标/P11B 编辑态真值 | `business-bid/pages/BusinessBidWorkspace.tsx`、`hooks/useBusinessBidWorkspace.ts`、`e2e/business-editor-state-truth.spec.ts` | **齐** | 服务端 editor-state 唯一真值；真实空态；固定加载/保存失败；旧 workspace 键忽略保值；A→B GET/PUT 会话隔离；上传、重解析与反馈重生成仍按 P8B 策略决策 |
| P8B 解析策略 | `parse-strategy/*`、`local-parser/LocalParserPage.tsx`、`e2e/parse-strategy-wiring.spec.ts` | **齐** | 仅读取脱敏策略；轻量任务、本地回传跳转与一次性询问；无策略持久化、无服务端 MinerU/Docling |
| 财务报价/成本 P10B/P10C | `services/finance_service.py`、`finance_cost_service.py`、`api/finance.py`；前端 `features/finance/*`、`e2e/finance-*.spec.ts` | **齐** | strict `finance` 当前空间报价白名单、人工成本草案和毛利快照；整数分、审计脱敏、无税务/审批/导出；`npm run test:e2e:finance-role` / `finance-cost-draft` |
| 人员资质 P10D | `models/entities.py`（HrCredentialCardRow）、`api/deps.py`（require_hr）、`services/hr_credential_service.py`、`api/hr.py`；前端 `features/hr/*`、`e2e/hr-credential-cards.spec.ts` | **齐** | strict `hr` 当前空间最小资质卡；摘要不含备注、按需详情、CSRF、StrictBool、审计脱敏、无删除/附件/推荐；`npm run test:e2e:hr-credential-cards` |
| 投标人匿名合规 P10E | `features/bidder/*`、`useAuthSession.canAccessBidder`、`router.tsx`、`AppShell.tsx`、`e2e/bidder-compliance-preview.spec.ts` | **齐** | strict `bidder` 仅 `/bidder`；只请求匿名汇总 GET、无存储、固定错误脱敏、无项目/财务/人力 API；`npm run test:e2e:bidder-compliance-preview` |
| 投标人项目合规 P10G | `features/bidder-project-compliance/*`、`router.tsx`、`AppShell.tsx`、`e2e/bidder-project-compliance.spec.ts` | **齐** | strict `bidder` 仅 `/bidder/project-compliance`；先取最小选择器、选中才取五计数，旧响应不覆盖新项目，无存储/URL 参数/回退 P10E；`npm run test:e2e:bidder-project-compliance` |
| 人员业绩 P10H | `features/hr-performance/*`、`router.tsx`、`AppShell.tsx`、`e2e/hr-performance-cards.spec.ts` | **齐** | strict `hr` 仅 `/hr/performance-cards`；初始摘要、按需详情、写后强制重读、A→B 迟到响应隔离，无存储/URL 参数/P10D/P10F 回退；`npm run test:e2e:hr-performance-cards` |
| 资质到期提示 P10I | `features/hr-credential-expiry/*`、`router.tsx`、`AppShell.tsx`、`e2e/hr-credential-expiry.spec.ts` | **齐** | strict `hr` 仅 `/hr/credential-expiry`；服务端日期直出、组件实例级在途请求去重、首次严格单次 GET、固定免责声明，无存储/URL 参数/P10D/P10F/P10H 回退；`npm run test:e2e:hr-credential-expiry` |
| 财务个人成本记录 P10J | `features/finance-cost-change-events/*`、`router.tsx`、`AppShell.tsx`、`e2e/finance-cost-change-events.spec.ts` | **齐** | strict `finance` 仅 `/finance/cost-changes`；首次严格单次 GET、刷新累计两次，固定动作/错误/限制声明，无写入、存储、业务回退或外网；`npm run test:e2e:finance-cost-change-events` |
| 财务项目成本记录 P10K | `features/finance/*`、`e2e/finance-project-cost-change-events.spec.ts` | **齐** | 既有 `/finance` 选中项目下显式打开/刷新；零自动 P10K GET、项目切换清空和迟到隔离，无 P10J/未知 API/外网或浏览器存储；`npm run test:e2e:finance-project-cost-change-events` |
| 知识库/卡片 | `knowledge-base/**`（useKnowledgeCards、cardsApi、KnowledgeBasePage）、`ChapterEditor`/`InsertCardDialog`；E2E `e2e/knowledge-cards.spec.ts` | **齐** | 图片 Tab 后端化；章节插入卡片；`npm run test:e2e:cards` |
| 查重 | `duplicate-check/pages`、`types.ts` | **齐** | 已接 API |
| 废标 | `rejection-check/pages`、`types.ts` | **齐** | 已接 API |
| 设置 | `settings/hooks`、`pages`、`types` | **齐** | embeddingModel 字段 |
| 创建/首页 | `create`、`home` | **齐** | |
| 导出模板 | `export-format/*` | **齐** | 标题边框与叶子标题左栏控件、实时预览已补齐 |
| 本地解析 | `local-parser` | **齐** | `projectId` 查询参数仅预填，绝不自动回传 |
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
# 当前完整串行基线：537 passed（1 条既有 Starlette/httpx 弃用警告）

cd ..\frontend
npm run lint
npm run build
# 响应矩阵双浏览器 E2E（独立 8010/5174 + biaoshu-e2e.db；首次需 npx playwright install chromium）
npm run test:e2e:matrix
```

**已知 lint 状态**：`npm run lint` **已通过**（**0 errors、0 warnings**）。此前 Hooks 误判（`useApiProjects` / `useApiSettings` → `shouldUseApiProjects` / `shouldUseApiSettings`）与 5 条既有 warnings（`BusinessStepStepper` / `StepStepper` 的 `only-export-components`、`useSiteBackground` 的 `exhaustive-deps`、`ChapterEditor` / `useTechnicalPlanEditors` 的 `no-useless-escape`）已在**独立 lint-only 任务**中清理完毕；`npm run build` 通过 TypeScript 类型检查。

**代理分工**：Grok 负责限定范围内的代码与测试落地；Codex 负责把任务写入 `.agent-collaboration/messages/codex-to-grok.jsonl`、审查 Grok 的 diff、运行验收并回复结果。Grok 接入命令见 `docs/agent-collaboration.md`；运行时消息目录已被 Git 忽略，禁止写入 API Key、令牌或真实密钥。

### 3.1 Grok 直连协作复现（强制按此顺序）

1. **先核验工作区**：只在 `collab/grok-code-codex-review` 工作；先执行 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review`。有未知脏文件不得覆盖、暂存或提交。
2. **只下发一个文件级受限任务**：通过 `tools/agent-collaboration/Send-AgentMessage.ps1` 从 `codex` 写入 `task`，正文必须含目标、精确白名单、禁止改动、接口/安全约束、验收命令，以及“不得 commit/push，完成后只发送 review_request”。消息箱为 Git 忽略运行态，不能写密钥、Cookie、CSRF 或真实人员数据。
3. **后台静默启动 Grok 单次执行**：仅在当前 PowerShell 进程设置本机代理，再以隐藏窗口启动 `C:\Users\Administrator\.grok\bin\grok.exe`；不得弹终端、浏览器或抢占用户前台焦点。Grok 读取最新任务后只实现和自测。精确命令：

```powershell
cd C:\Users\Administrator\biaoshu
$env:HTTP_PROXY = 'http://127.0.0.1:7890'
$env:HTTPS_PROXY = 'http://127.0.0.1:7890'
$env:ALL_PROXY = 'http://127.0.0.1:7890'
$env:NO_PROXY = 'localhost,127.0.0.1'
$stdout = '.agent-collaboration\grok.stdout.log'
$stderr = '.agent-collaboration\grok.stderr.log'
$arguments = '--cwd "C:\Users\Administrator\biaoshu" --single "读取 .agent-collaboration/messages/codex-to-grok.jsonl 中最新一条 Codex 任务，严格按任务执行；完成后仅通过消息箱向 Codex 发送 review_request，不要提交或推送。" --always-approve --disable-web-search --no-subagents --output-format json'
Start-Process -FilePath 'C:\Users\Administrator\.grok\bin\grok.exe' -ArgumentList $arguments -WorkingDirectory 'C:\Users\Administrator\biaoshu' -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
```

4. **等待 review_request，不信口头结论**：读取 `.agent-collaboration/messages/grok-to-codex.jsonl` 或 `Read-AgentMailbox.ps1`；要求其报告精确文件列表、失败先测证据、最终测试、`git diff --check`、风险与未做项。Grok 超时后子进程可能仍继续；先用 `Get-Process grok` 和消息箱确认，不重复下发相同任务。
5. **Codex 独立验收并唯一负责 Git**：核对差异仅在白名单，复跑定向与受影响回归，再按风险决定是否要求返修。仅 Codex 可 `git add`、中文 `git commit` 和带本机代理的 `git push origin collab/grok-code-codex-review`。每个完成包至少分为计划、后端/前端实现、文档闭环提交；不得向 `main` 推送或 force push。

P10D 至 P10G 的实际协作按上述模式完成。P10H 延续该闭环：Codex 先否决把 P10D `performance` 枚举直接当作具体项目业绩，冻结独立 `hr_performance_cards` 契约；Grok 后端首版后，Codex 发现更新模型可显式传入非空字段 `null` 且鉴权断言过宽，遂下发仅限 Schema/测试的返修；修复后独立验收后端定向 14 项与全量 392 项。前端再单独实现，Codex 审查初始摘要、按需详情、写后双重重读、迟到响应隔离、严格门禁和网络/存储白名单，独立通过 lint/build、P10H E2E 10 项及单 worker 串行全量 93 项。

P10I 同样完成两轮受限审查。后端首版整实体加载了契约禁止读取的备注/创建人/时间戳，关注项模型还允许 `valid` 且分类器暴露可变窗口；Codex 限定返修为 7 个必要 SQL 列、三类关注状态和内部固定 90 天，并补真实 SQL 投影与模型拒绝测试，独立通过定向 14 项和全量 406 项。前端首版用“至少 1 次 GET”掩盖 Strict Mode 重复读取，并跨功能触发 P10D；Codex 拒绝后，返修为组件实例级共享在途 Promise、首次严格 1 次 GET、刷新累计 2 次，移除跨功能请求并补齐停用卡空态，独立通过 lint/build、P10I E2E 10 项及单 worker 串行全量 103 项。下一包必须延续相同的“规划—单任务实现—独立审查—返修—验收—文档”闭环。

M3-C 从 M3-B 明确后遗留中选取最近批次即时撤销，Codex 先比较对话框内单批撤销、页面级临时撤销和后端版本历史，冻结最小方案。Grok 严格 3 文件先写失败 E2E，再实现按章最早 before/最终 after 快照与原状态恢复。Codex 独立代码/安全审查确认快照不持久化、点击时重读并校验标题/正文/状态、漂移章不覆盖且无新增 API；定向 M3-B/M3-C 6 项、M3-A 1 项、P10H 10 项及 lint/build 均通过。首轮全量 103/106 的 3 项失败均为纯白页应用未启动，相关定向通过；完整单 worker 重跑 106/106 后才验收提交。下一包继续沿用“规划—单任务实现—独立审查—验收—文档”闭环。

P9D 延续相同闭环：Codex 先审计后端 `imageWarnings` 已同时覆盖技术标与商务标，冻结纯前端五文件边界。Grok 首版后，Codex 拒绝仅靠 `useEffect` 清空导致的新项目首帧旧告警和迟到写入，并要求项目绑定与实例级代次；第二轮又拒绝只等 `route.fulfill` 的假同步测试和“先下载后写告警”的逆序。最终测试等待旧项目下载调用后再断言新项目无污染。Codex 独立通过后端图片专项 14 项、P9D E2E 4 项、lint/build 和单 worker 全量 110 项，发送 `ack=msg_6501bcc367fa4a26ab09cae11a4774fd` 后才提交 `e5adad7`。

P10J 继续采用后端、前端分包。后端首版把 SQL `LIKE` 下划线当作字面量，非法 `fceX...` 可占用固定上限；首轮返修后空后缀和尾随空白仍在 Python 层过滤，仍会挤出合法事件。Codex 两次拒绝后，最终把字面 `fce_`、非空后缀、无首尾空白全部前移到 SQL `LIMIT 50` 前，独立通过定向 16 项、受影响回归 63 项和全量 422 项。前端实现本身通过，但首版 E2E 放行 Google 字体且未知外网不可观测；返修为字体本地空响应、其他外网记录后中止，并补不真实出网的探针。Codex 独立通过 P10J E2E 12 项、lint/build 和全量 122 项，发送 `ack=msg_1120ac97e76346e7bc2b2fb6266e50be` 后才提交 `fce6cb6`。

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

`kind=business`、editor-state 商务字段、`biz_*` 任务（复用技术标 SSE 进度与回退）、export mode=business（含标题段落边框）、revise 结构化写回。P11B 已将商务 workspace 收口为服务端 editor-state 唯一真值：旧 workspace localStorage 忽略保值，真实空态保持空，GET/PUT 失败显式脱敏且 A→B 迟到隔离。

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

### 4.8 P10B/P10C/P10J/P10K 财务报价、成本草案、毛利快照与变更记录

P10B 已实现并推送：后端 `GET /api/finance/business-bids` 与 `GET /api/finance/business-bids/{projectId}` 仅在 `AUTH_MODE=required` 且当前成员角色严格为 `finance` 时开放。接口只投影当前工作空间 `kind=business` 项目的项目摘要、报价分项和备注，响应 `Cache-Control: no-store`；技术标、跨空间和不存在项目统一 `404 project_not_found`。金额只接受有限数值，异常值为 `null` 且不计入合计。

P10C 已实现并推送：同一 `/finance` 门禁下，严格财务成员可通过独立 `cost-draft` / `cost-entries` 端点维护人工成本条目，并看到基于当前报价的毛利快照。金额仅为人民币整数分，前端元输入按字符串转换；写入走既有 CSRF，成功后重新读取服务端草案，审计只写动作与条目 ID。它不新增税务、审批、导出、预算、回款或版本历史。前端不调用通用项目、editor-state、设置或文件接口，不把业务数据、Cookie 或 CSRF 写入浏览器存储；项目切换在对应报价明细就绪前不挂载成本面板。完整契约见 `docs/p10b-finance-business-quote-contract.md` 与 `docs/p10c-finance-cost-draft-contract.md`。

P10J 已完成并推送：计划=`701c946`，后端=`4e662d6`，前端=`fce6cb6`。只允许严格 `finance` 读取本人在当前活动工作空间最近 50 条成功成本条目新增/修改/删除记录。既有审计没有项目、金额、内容、前后快照或失败尝试，因此 API 只返回 action/entryId/occurredAt，页面明确“不是完整财务审计”。无新表、迁移、其他成员投影、筛选、分页、导出或浏览器存储。完整契约见 `docs/p10j-finance-personal-cost-change-events-contract.md`。

P10K 已完成并推送：计划=`2e53007`，后端=`1eaa75e`，前端=`dbf301c`。P10C 成功新增、修改、删除现在会在同一事务写入最小项目事件；严格 `finance` 仅在既有 `/finance` 显式点击后读取选定商务标最近 50 条，并把 actor 映射为 `本人/其他财务成员`。旧历史不回填，响应无金额、内容、成员身份、失败尝试或前后值；项目切换清空且迟到响应隔离。完整契约见 `docs/p10k-finance-project-cost-change-events-contract.md`。

### 4.9 P10D 人员资质素材卡

P10D 已实现并推送：后端 `d8f7cbd` 与前端 `71f065a`。`/api/hr/credential-cards*` 仅向 `AUTH_MODE=required` 的 strict `hr` 当前空间成员开放；required 未登录保持全局 `401 auth_required`，disabled、非 HR 与所有者隐式绕过均为 `403`。列表只返回摘要，选中后才读取详情；创建、编辑、启停走既有 CSRF 并强制重读服务端列表与详情。服务端拒绝额外敏感字段和非 JSON 布尔，审计只记录动作和卡片 ID；浏览器不持久化数据。P10D 卡片本身无删除、附件、联系方式、证件号、项目关联、导出或跨空间搜索；团队快照仅由独立 P10F 提供。完整契约见 `docs/p10d-hr-credential-cards-contract.md`。

### 4.10 P10F 人力项目团队推荐快照

P10F 已完成并推送：计划=`12e067f`，后端=`3dc600a`，前端=`254f8c7`。仅 `AUTH_MODE=required` 下 strict `hr` 可在 `/hr/team-recommendations` 从当前空间有效 P10D 卡摘要中按顺序保存技术标项目团队快照；只提供 HR 项目 `id/name` 选择器、摘要和按需详情，写入走 CSRF，快照不含备注且不会随卡片编辑/停用自动变化。strict `bid_writer` 仅能在当前技术标项目内经用户动作读取最小展示投影；disabled、仅所有者身份、其他角色均不放行。响应均 `no-store`，审计 target 只为 `htr_*`，浏览器不持久化。无人员业绩、证件、附件、AI 推荐、审批、导出、Word 写入或项目内容共享。完整契约见 `docs/p10f-hr-team-recommendation-contract.md`。

### 4.11 P10E 投标人匿名合规预览

P10E 已完成并推送：计划=`26f7e40`，后端=`1b6ccf3`，前端=`37cf835`。`GET /api/bidder/compliance-preview` 只向 `AUTH_MODE=required` 的 strict `bidder` 当前空间成员开放；required 未登录由全局中间件固定 `401 auth_required`，disabled、所有者隐式绕过、`bid_writer`、`finance` 与 `hr` 为 `403 role_forbidden`，非成员空间保持 `403 workspace_forbidden`。服务端只读取 `kind=technical` 项目的既有收敛 `responseMatrix`，仅返回 `dataState` 与总量、覆盖、未覆盖、豁免、覆盖率基点五项汇总；不返回项目数量/ID/名称、工作空间、人员、原文、章节、大纲、备注、文件或财务字段。每次成功读取仅审计固定 action 与 `anonymous_aggregate` target，响应固定 `Cache-Control: no-store`。

前端 `/bidder` 仅 strict `bidder` 可挂载，独立「投标人 / 合规预览」导航下只请求该 GET，并只在 React 内存保存结果；空态不计算覆盖率，失败固定中文脱敏。E2E 覆盖匿名字段、空态、错误脱敏、disabled/所有者/其他角色不请求、网络白名单和浏览器存储。P10E 本身无项目详情、导出、写入、版本、结果跟踪或规则执行；项目统计由独立 P10G 提供。完整契约见 `docs/p10e-bidder-anonymous-compliance-preview-contract.md`。

### 4.12 P10G 投标人项目级合规统计

P10G 已完成并推送：计划=`26b43ea`，后端=`c3cf8b4`，前端=`d5656cc`。仅 `AUTH_MODE=required` 下、当前工作空间内精确 `bidder` 角色可使用；disabled、仅 `is_owner`、`bid_writer`、`finance`、`hr` 均不放行，但真实 `member.role=bidder` 的所有者按其实际角色正常通过。`GET /api/bidder/project-compliance/projects` 只返回当前空间 `kind=technical` 的 `id/name`，不审计；用户选择后 `GET /api/bidder/project-compliance/{projectId}` 仅返回 `dataState` 与总量、覆盖、未覆盖、豁免、覆盖率基点五项统计。空矩阵为 `200`，跨空间、不存在和商务标统一 `404 bidder_project_compliance_not_found`，不回显项目 ID 或细节；两条成功响应均 `no-store`，详情成功读仅审计固定 `bidder_project_compliance_read` 与 `project_compliance`，审计不含项目标识、名称、计数或矩阵。

前端 `/bidder/project-compliance` 仅 strict `bidder` 可挂载，初始只请求选择器，选择后才请求详情；请求序号与项目 ID 双重绑定，项目切换时立即清空旧结果，过时响应不得渲染到新项目。导航把 `/bidder` 收紧为精确匹配，「项目合规」仅匹配其自身路径；不得请求 `/api/bidder/compliance-preview` 回退、`/projects*`、编辑器状态、人力、财务、文件或外网接口，亦不得使用 URL 参数或浏览器存储。P10G 不交付项目详情、矩阵原文、来源、章节、大纲、人员/团队/资质/业绩、附件、财务、写入、导出、版本、结果跟踪或规则执行。完整契约见 `docs/p10g-bidder-project-compliance-contract.md`，实施计划见 `docs/plans/2026-07-14-p10g-bidder-project-compliance-plan.md`。

### 4.13 P10H 人员业绩素材卡

P10H 已完成并推送：计划=`7694843`，后端=`6c76d80`，前端=`4eb8a14`。仅 `AUTH_MODE=required` 下、当前工作空间内精确 `hr` 角色可使用 `/api/hr/performance-cards*`；required 未登录固定 `401 auth_required`，disabled、所有者隐式绕过和其他角色固定 `403 role_forbidden`，非成员空间保持 `403 workspace_forbidden`，跨空间/不存在/伪造卡 ID 统一 `404 hr_performance_not_found`。摘要列表不含 `performanceSummary`、`remark`，详情与成功写入才返回；年份为可空严格整数 1900–2100，启用状态为严格布尔，额外键、非对象、空补丁与显式非法 `null` 固定 `422 invalid_hr_performance`。成功响应均 `no-store`；创建/更新审计只在固定 action/`hpc_*` target 上保留业务脱敏，既有审计基础设施仅从验证会话记录操作者/空间。

前端 `/hr/performance-cards` 复用严格 `RequireHr`，初始只请求摘要，选中后才取详情；创建、编辑与启停成功后重读列表和当前详情，不做乐观更新。请求序号与卡片 ID 双重绑定，A→B 的迟到响应不会覆盖新选择；错误固定中文脱敏，不请求 P10D/P10F、项目、文件、财务、投标人或外网，不写浏览器存储或 URL 参数。P10H 不交付删除、附件、证件校验、联系方式、合同金额、项目关联、团队组装、审批、导出或 Word 写入。完整契约见 `docs/p10h-hr-performance-cards-contract.md`，实施与验收记录见 `docs/plans/2026-07-14-p10h-hr-performance-cards-plan.md`。

### 4.14 P10I 人员资质到期提示

P10I 已完成并推送：计划=`ddc1807`，后端=`d5201e9`，前端=`49daa16`。仅 `AUTH_MODE=required` 下、当前工作空间内精确 `hr` 角色可读取 `GET /api/hr/credential-expiry`；required 未登录保持 `401 auth_required`，disabled、仅所有者和其他角色固定 `403 role_forbidden`，非成员空间保持 `403 workspace_forbidden`。服务以 UTC 自然日和固定 90 天窗口分类当前空间资质卡，SQL 只投影 ID、人员显示名、类别、资质名、等级、有效期和启停状态；有效卡只计数，停用卡只计入排除数，备注、创建人、时间戳与空间不读取、不返回。成功响应 `no-store`，审计仅保留固定 action/target/result 与既有验证会话身份，不记录任何业务值。

前端 `/hr/credential-expiry` 复用严格 `RequireHr`，直接展示服务端日期、窗口、六项计数与三类关注项，不用浏览器时间重算。React Strict Mode 下使用组件实例级在途 Promise 共享首次请求，不跨用户/会话缓存：初次严格 1 次 GET，手动刷新后累计严格 2 次。页面固定声明不验证真实性，不展示 `cardId`，不请求 P10D/P10F/P10H、项目、文件、财务、投标人、未知 API 或外网，不写浏览器存储或 URL 参数。完整契约见 `docs/p10i-hr-credential-expiry-contract.md`，实施与验收记录见 `docs/plans/2026-07-14-p10i-hr-credential-expiry-plan.md`。

### 4.15 M3-C 融合写入最近批次单次撤销

M3-C 已完成并推送：计划=`c63310f`，实现=`b8ff605`。它不改变 `content_fuse` 后端任务、模型输出或 editor-state API；只在 `ContentFuseDialog` 当前实例内，为最近一次至少成功写入一条建议的确认批次保存最小快照。多建议同章保留最早写入前正文/状态和最终写入后正文/状态；下一成功批次覆盖，生成新建议、关闭、刷新或切项目后不保留。

点击“撤销本次写入”时逐章重读当前状态，只有章节仍存在且标题、正文、状态均精确等于写入后快照才恢复写入前正文与原状态；漂移章跳过且不覆盖。恢复继续通过 `replaceChapterBody` 派生预览与字数，并走既有串行防抖 PUT；快照无论全部、部分或零项恢复均一次消费。无新 API、表、依赖、浏览器存储、模块全局缓存、历史栈或通用撤销。完整契约见 `docs/m3c-content-fuse-undo-contract.md`，实施与验收记录见 `docs/plans/2026-07-14-m3c-content-fuse-undo-plan.md`。

### 4.16 M3-D 融合写入持久恢复批次

M3-D 已完成并推送：计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`。确认接口只接受成功 `content_fuse` 任务 ID 和按用户顺序选择的 1–5 个建议 ID；建议正文、action 与 base 只从服务端任务结果取得。服务端锁定当前空间技术标项目后重新校验目标章存在性、标题、正文哈希与码点长度，在同一事务内写章节、服务端快照并裁剪为每项目最近 20 批；整批冲突、超限或异常均零写入。列表仅返回批次 ID、章数、状态和时间；恢复只覆盖 title/body/status 仍精确等于 after 的章，完整、部分或零恢复后都一次消费。

前端复用 `ContentFuseDialog`，确认前零本地正文写和零 editor-state PUT；业务 POST/consume 成功后立即禁止二次提交，再执行唯一一次真实 editor-state 重载和批次列表刷新。`useTechnicalPlanEditors.reloadFromApi` 返回 `Promise<boolean>`：其他既有调用可忽略返回值并保持旧静默语义，M3-D 则据此区分“业务失败”和“业务已完成但刷新失败”。对话框只展示时间、章数、可恢复/已消费及有限 20 批声明；不展示历史正文、标题、来源或任务/批次标识，不使用 URL、浏览器存储、剪贴板、console、下载、轮询、计时器或外网。项目切换/关闭通过实例代次隔离迟到列表、create 和 consume。

后端经三轮受限审查后独立通过专项 34、受影响回归 71、串行全量 487；前端经两轮受限审查消除双 GET 假成功窗口，独立通过持久恢复 5、原子确认 6、M3-A 1、认证/RBAC 11 和单 worker 串行全量 145，lint/build/diff-check 通过。完整契约见 `docs/m3d-content-fuse-persistent-recovery-contract.md`，实施与审查记录见 `docs/plans/2026-07-14-m3d-content-fuse-persistent-recovery-plan.md`。

### 4.17 P9D 导出图片失效引用浏览器提示

P9D 已完成并推送：计划=`4925a51`，实现=`e5adad7`。技术标与商务标成功导出后消费后端现有 `result.imageWarnings`，只保留最多 20 条非空字符串、每条最多 240 个 Unicode 码点，并以 React 纯文本列表展示；非法结构、HTML、URL 和路径均不解析，不新增链接或网络请求。

每次新导出前清空旧告警，状态同时绑定产生它的 `projectId`；实例级代次使旧项目或旧导出迟到结果不能污染当前页面。告警不改变成功状态，当前任务先写告警再继续下载；旧任务迟到仍保持既有下载语义但不写当前告警。无新后端、API、表、依赖、浏览器存储、计时器或模块全局缓存。完整契约见 `docs/p9d-export-image-warning-contract.md`，审查与验收记录见 `docs/plans/2026-07-14-p9d-export-image-warning-plan.md`。

### 4.18 P12A editor-state 手动检查点只读库

P12A 已完成并推送：计划/契约=`bf8ccd6`、后端=`9f53d92`。新增 `editor_state_checkpoints`，只接受空对象 POST，由服务端在项目锁内读取当前技术标或商务标权威 editor-state，抽取精确 13 键并生成 UTF-8 紧凑排序标准 JSON；`stateVersion` 是规范正文 SHA-256 前 32 位，每项目固定最近 20 条。列表和淘汰 SQL 只读取元数据/主键，详情按 `id/workspace_id/project_id` 三重作用域读取并重验键集、字节、摘要、计数与规范形式。所有成功和固定业务错误 `no-store`。

Grok 首版后经两轮受限返修：先修复完整正文批量加载、提交后 `refresh` 假失败、非规范 JSON 放行、损坏元数据异常泄漏和跨项目正文提前加载；再把项目锁、权威读取、序列化、计数、插入、裁剪、提交全部纳入显式回滚域，并拒绝 `NaN/Infinity`。Codex 独立通过专项 29、受影响回归 97、P8C/异步 callback 15、后端串行全量 518 passed。P12A 没有恢复、删除、下载、搜索、自动历史或前端；P12B 必须先冻结 expected current state version、恢复前安全检查点、原子恢复和迟到 autosave 防护。

### 4.19 P12B-A editor-state 全状态版本与可选 CAS 基础

P12B-A 已完成并推送：计划/契约=`0b55c30`、实现=`780cc82`。`editor_state_service` 成为 P12A/P12B 精确 13 键、紧凑 UTF-8 排序 JSON 和 `esv_` 版本的共享权威；GET/PUT 成功响应返回 `stateVersion`，PUT 可选 `expectedStateVersion`。携带 expected 时先取项目数据库写锁，只从同一锁后行计算全状态与矩阵版本；全状态冲突优先并固定只返回 `code/message/currentStateVersion`，任一冲突整包零写、显式回滚。缺 expected 仍为迁移期兼容覆盖，不是最终安全门。

Grok 初版经两轮定点返修：第一次消除锁后重复读取和提交后 `refresh`/GET 假失败；Codex 首次全量发现 12 项回归后，第二次统一 `updatedAt` 提交前后格式，并在持久 JSON 读写边界把存量/新写 `NaN/±Infinity` 收敛为 `null`，同时保持规范哈希和 P12A 直接伪造快照严格 `allow_nan=False`。Codex 独立通过专项 19、内容融合/财务定向 12、原回归 104、后端串行全量 537 passed。P12B-A 没有前端 expected、迟到任务围栏或恢复；下一包只能是 P12B-B。

### 4.20 P12B-B 技术标/商务标前端全状态 CAS

P12B-B 已完成并推送：契约/计划=`0636302`、实现=`473e823`。技术主 hook 和商务 hook 分别维护同项目串行保存链，每次执行读取最新 UI 与服务端 `stateVersion`；guidance 已并入技术主状态，`useProjectGuidance` 只保留反馈历史和 revise；矩阵合并 PUT 进入技术队列并精确只带矩阵、矩阵版本和 expected。GET/PUT 版本格式非法会固定失败或阻断；精确全状态 409 保留本地、停止全部自动保存，只能显式全量 GET 恢复；无真实矩阵明细的普通 409 不再伪造空矩阵冲突。

Grok 首版全量仍有 4 failed、3 did not run，且实现早于新增测试，未获验收；第一次返修更新矩阵/HR 旧测试并修复 409 分流与版本串链证据，第二次返修清除矩阵 E2E 的 `.or(...)` 和宽泛 2xx 断言。Codex 独立通过技术 28、商务 18、矩阵 8、HR 推荐 4、融合确认 6、持久恢复 5、前端全量 201。技术 truth 首轮 1 项纯白页、首个 GET 为零，精确 1 项与整文件 28 项复跑均通过。P12B-B 当时没有给任务/callback/P8C/M3-D 写入加 expected，随后已由 P12B-C 补齐；restore 仍未实现。

### 4.21 P12B-C editor-state 延迟写入围栏

P12B-C 已完成并推送：冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`。C1 为九类任务 writer 捕获创建时权威版本，最终写入锁后 CAS；批量章节只推进自身成功版本，商务 revise 进入原保存队列。C2 把 disabled callback 的 editor-state/任务/项目写入收进单事务，并让 P8C 票据在签发时绑定版本；陈旧或旧空版本票据首次回调零业务写但必须消费。C3 让 M3-D apply/consume 强制 expected、全状态冲突优先于原章节规则，并把两个 POST 收进技术主 `matrixSaveChainRef`。

C3 经两轮返修：首轮移除成功重读后会吞掉下一次真实编辑的残留 autosave skip，并补 PUT 挂起时 apply/consume 零旁路、成功唯一重读、不确定响应阻断；第二轮把 abort/缺失/非法/带空白版本的本地正文保留、零重试、两防抖窗口零 PUT 和零 unhandled 改为逐轮闭环。Codex 独立通过后端专项 62、全量 570、C3 E2E 48、前端全量 212、lint/build 与语法/diff 检查。P12B-C 当时没有实现 restore、恢复按钮、历史浏览、删除或自动检查点；随后 P12B-D 已补齐显式安全恢复，但历史浏览、删除和自动检查点仍未实现。

### 4.22 P12B-D editor-state 检查点安全恢复

P12B-D 已完成并推送：冻结=`613818f`、D1 后端=`551caba`、D2 前端=`0f81dd6`。D1 在一次项目写锁和事务内先比较执行时 expected，再严格读取并验证目标检查点；覆盖当前 13 键前创建恢复前安全检查点，写回复用共享规范映射，重新计算版本必须精确等于目标，最后保护安全记录并裁剪到最近 20 条。陈旧 expected、损坏/超限快照、写回漂移、插入/裁剪/提交异常均显式回滚，不留下部分恢复或安全记录；成功响应提交前构造且 `no-store`。

D2 在技术标和商务标页头后复用折叠面板，只在展开时读取最近 20 条元数据，不请求或渲染 snapshot/ID/version。“保存服务器当前版本”复用现有保存执行器强制即时 PUT 后才 POST 精确 `{}`；恢复要求内联二次确认，进入现有串行保存链并携带执行时最新 expected。成功后阻断旧 UI、作废旧写 epoch，只做一次 editor-state GET 水合完整相关字段；折叠、项目切换、迟到 list/create/restore 和重复点击均由面板会话与项目绑定 token 隔离。

D2 四轮返修依次关闭 forced-create 不确定失败未阻断、商务恢复后误 PUT、宽松响应 shape/固定 sleep；跨项目共享布尔 token 与未入真实 gate 的假迟到证据；禁用按钮 `force:true` 假令牌验证、create 成功体非法版本未全量阻断与技术水合缺项；最后关闭 HTTP `ApiError.code` 冒充内部版本错误。Codex 独立通过 D1 **58/81/599**、D2 **51/63/263 passed**；D2 全量首跑的单次纯白页经精确 1 项与完整 263 项重跑后才验收。最终 Grok 回执=`msg_a37557e7e11543df93d0599bf580ac83`，Codex 确认=`msg_94a365e64f9f424f93d46ffdd2e344d7`。

P12B-D 不是通用版本库：没有自动检查点、每次 autosave 历史、命名/删除/下载/diff/搜索、任意版本时间线、跨项目浏览或多人协作。后续若选择版本历史，必须另立数据保留、配额、权限、审计、并发和隐私契约。

### 4.23 P12C-A/B/C1/C2 editor-state 有限自动修订账本、只读历史与受限恢复

P12C-A 已完成并推送：冻结=`daa8c43`、实现=`226e1c1`。新增独立 `editor_state_revisions` 表，不复用 P12A/P12B-D 的 20 条手动/安全检查点裁剪域；每项目最近 10 条、单条最多 2 MiB，固定 `browser_put/task/revise/callback/local_parser/content_fuse_apply/content_fuse_consume/checkpoint_restore` 八类内部来源。`record_editor_state_transition` 只接受调用方同一事务内的 before/after 权威全状态，按空账本、连续、断链和回退语义追加，相邻版本去重，只 flush，不 commit/rollback/refresh/查询项目/取得第二把锁。

Grok 首轮全量 636 passed / 1 failed，失败来自并列时间戳测试在统一时间后继续 transition，误把随机 ID 稳定排序当作插入顺序；返修后先完成 transition，再统一时间且只验证 `created_at DESC, id DESC`。Codex 随后用失败先测证明缺任一 13 键时 `.get` 补 `None` 会让匹配版本的假状态入账（28 failed / 1 passed），限定返修为 extract 前要求共享权威键集合全部存在，并允许服务端派生额外键。随后补齐跨空间裁剪隔离、DELETE 行 ID 精确断言及合法 32 位夹具 ID。Grok 最终回执=`msg_07cc1dfd882d4117861661b1722ec205`，Codex 确认=`msg_294959d9885c4c50a7a5e77c687037fd`。

Codex 独立通过专项 **67**、P12A/P12B-D 受影响回归 **77**、后端全量 **666 passed**；只有 1 条既有 Starlette/httpx 弃用告警，`py_compile`、精确三文件白名单、工作树与暂存区 diff 检查均通过。A 包当时没有生产调用、API、Schema、前端、列表、详情或恢复能力。

P12C-B-A 已完成并推送：冻结=`fbf93c0`、实现=`acf3139`。公开浏览器 PUT 唯一传服务端字面量 `browser_put`；服务内部来源默认 `None`，来源存在时强制项目写锁，锁后同一 row 构造 before，commit 前同一事务记录 after。Grok failure-first 11 failed / 1 passed，首版 12/107/678；Codex 两轮返修关闭并列时间戳顺序假设、真实跨空间 404、flush 后脱敏 500 与 commit 前 revision 已 flush 证据，独立通过 **14/107/680 passed**。

P12C-B-B1 已完成并推送：冻结=`05864f6`、实现=`5a0d1c0`。五类技术 writer 与四类商务 writer 任务均经私有包装器固定来源 `task`；版本冲突原样进入既有 stale 流程，其他 upsert 内部异常只返回固定中文。批量章节每次实际迁移各记一条修订，章间漂移只保留成功前缀。Grok failure-first 8 failed / 2 passed，首版 10/109；Codex 一次返修关闭内部异常原文泄露、逻辑优先级假绿、宽松增量与空集合来源断言，独立通过 **10/126/690 passed**。

P12C-B-B2 已完成并推送：冻结=`3a30c03`、实现=`5149385`。`business_parse` 与四类结构化商务 revise 的两个真实 upsert 写点固定来源 `revise`；结构解析失败、空 revised、普通技术 revise、陈旧 expected 与 LLM 期间漂移保持本次修订零增量。Grok failure-first 6 failed / 5 passed、最终 11/122；Codex 独立通过 **11/147/701 passed**，并确认真实 ASGI 脱敏 500、recorder/commit 失败双零、commit 前 flush 与来源隔离。B2 交付时已覆盖浏览器 PUT、九类任务与五类商务 revise；当时两类 callback、content-fuse、checkpoint restore 仍待逐包接入，随后个人 callback 已由 C1 完成。

P12C-B-C1 已完成并推送：冻结=`76834f5`、实现=`1d0ce0e`。个人 callback 保存同一次锁后 before，在 parsed Markdown、成功任务和项目步骤写入后，以提交前内存 after 和固定 `callback` 调用无提交原语。Grok failure-first 6 failed / 4 passed、最终 10/150；Codex 一次测试返修关闭通用 500 假绿与直调 service 冒充 P8C 公开路由，独立通过 **10/224/711 passed**。C1 完成时已覆盖浏览器 PUT、九类任务、五类商务 revise 与个人 callback，P8C `local_parser` 留给 C2。

P12C-B-C2 已完成并推送：冻结=`52bbabf`、实现=`82cc82e`。fresh 分支保存同一次锁后 before/行，在正文、任务、项目和审计暂存后以固定 `local_parser` 留史；stale/null 不进入 helper，只提交消费并保持零修订，recorder/commit 失败全域回滚且同票可重用。Grok failure-first 7 failed / 3 passed、初版专项 10；Codex 一次仅测试返修关闭 C1 阶段守卫、`>=1` 与条件 401 假绿，独立通过 **20/272/721 passed**。

P12C-B-D1 已完成并推送：冻结=`e8ffaeb`、实现=`a6a28f6`。融合 apply 保存同一次锁后 before/行，在章节、恢复批次和裁剪暂存后从同一内存行构造 after，以固定 `content_fuse_apply` 在唯一 commit 前留史。Grok failure-first 9 failed / 2 passed、初版 11/184；Codex 一次仅测试返修关闭完整/部分 consume 可能以其他来源误写仍假绿的缝隙，独立通过 **11/285/732 passed**。D1 当时覆盖到 content-fuse apply，consume 与 checkpoint restore 后续分别由 D2/D3 独立接入。

P12C-B-D2 已完成并推送：冻结=`6b83fc1`、实现=`f256f5b`。融合 consume 复用锁后 before/同一状态行，仅在 `restored > 0` 时从提交前内存行构造 after，以固定 `content_fuse_consume` 与章节和批次消费共享原唯一事务；零恢复仍消费批次，但完整 13 键、`updatedAt`、版本与修订身份序列不变。Grok failure-first 11 failed / 13 passed；Codex 两轮仅测试返修关闭宽松集合、跨项目恒真比较、真实跨空间隔离缺失、并发任意 409、零恢复部分字段比较、公开 500 固定表名/路径及外空间完整状态比较假绿，独立通过 **25/299/746 passed**。Grok 最终回执=`msg_a9410ee18ff64338b36b652e6dc7401b`，Codex 确认=`msg_2e23e5e7f9414b52b83569b526592426`。D2 当时已覆盖 content-fuse apply/consume，checkpoint restore 后续由 D3 独立接入。

P12C-B-D3 已完成并推送：冻结=`1d44484`、实现=`b91a7ff`。检查点恢复复用锁后 before 和写回后 after，在目标版本复核后、检查点裁剪与唯一 commit 前，仅对不同规范版本固定记录 `checkpoint_restore`；同内容仍创建安全检查点并更新 `updatedAt`，但修订身份精确不变。Grok failure-first **11 failed / 7 passed**、最终专项 18；Codex 两轮仅测试返修关闭来源隔离同义反复、失败路径只比版本、裁剪失败缺可重试和同内容时间语义弱断言，独立通过 **18/270/764 passed**。Grok 最终回执=`msg_69322b31400844f4aa72bbaed660eb98`，Codex 确认=`msg_1b0bff219b7940eabf665626c1214a2b`。至 D3 时八类既有写入来源已覆盖；C2 后又增加准确恢复来源 `revision_restore`。

P12C-C1 已完成并推送：冻结=`26b504e`、实现=`7023ecd`。列表固定最近 10 条 `id/state_version/snapshot_bytes/source_kind/created_at` 投影，不读取 `snapshot_json`；详情以 revision/workspace/project 三重作用域读取并严格重验规范 JSON、字节和版本。Grok 首版 failure-first **12 failed / 0 passed**、专项 13；Codex 审查真实复现坏时间在 ORM 物化阶段裸 500，返修 failure-first **1 failed / 12 passed** 后关闭真实越界字节、非法来源、坏时间和正文损坏的固定 500/no-store 门。Codex 独立通过 **13/201/777 passed**，Grok 回执=`msg_1ea43d2ffaf94129aa18badfa1da9180`，Codex 确认=`msg_a61704c81ee24b98bda1d56f5a5f7cd9`。C1 交付时尚无恢复或前端；后端恢复随后已由 C2 完成。

P12C-C2 已完成：冻结=`54af600`、范围修订=`2276366`、实现=`0803250`。严格 POST 只接受 `expectedStateVersion`，锁后复用 C1 三重作用域重验目标；共享恢复原语把恢复前安全检查点、13 键写回、准确 `revision_restore` 新时间点与双配额裁剪收进唯一事务，同内容零修订。Grok 首版专项 23；Codex 将迁移失败假证据改成真实 DROP 前异常后得到 **1 failed / 22 passed**，证明 SQLite 回滚后临时表残留；第二轮零行 DML 触发物理事务并收紧固定 500/no-store、来源和计数门。Codex 独立通过 **23/121/800 passed**；Grok 最终回执=`msg_b0389af3509b4e4d93076334060141d8`，Codex 确认=`msg_1dc76dd27beb48ba8c367da1432ea0c6`。其后前端 C3 已完成并独立验收。

### 4.24 路径索引

```text
backend/app/
  api/compliance.py finance.py hr.py bidder.py knowledge.py tasks.py projects.py content_fuse_applications.py editor_state_checkpoints.py editor_state_revisions.py settings.py opportunities.py resources.py templates.py
  services/
    task_service.py parse_engines.py business_task_service.py knowledge_service.py
    embedding_service.py duplicate_service.py rejection_service.py
    export_service.py revise_service.py editor_state_service.py editor_state_checkpoint_service.py editor_state_revision_service.py editor_state_revision_history_service.py
    file_service.py finance_service.py hr_credential_service.py hr_performance_service.py hr_credential_expiry_service.py bidder_compliance_preview_service.py bidder_project_compliance_service.py opportunity_service.py resource_service.py resource_sync_service.py
    template_service.py content_fuse_application_service.py text_similarity.py

frontend/src/features/
  technical-plan/  business-bid/  knowledge-base/  bid-templates/
  editor-state-checkpoints/  duplicate-check/  rejection-check/  settings/  bid-opportunity/  resources/  finance/  hr/  hr-performance/  hr-credential-expiry/  bidder/  bidder-project-compliance/
```

---

## 5. 明确未完成

| 优先级 | 项 | 现状 |
|--------|----|------|
| 核心项目 | 项目列表/详情/创建真值 | P11A 已完成并推送（计划=`70a2dc7`、前端=`b0a86e4`）：技术标/商务标生产入口只认 `/api/projects*`，不再以 mock、`biaoshu.projects.v1` 或本地假 ID 伪装成功 |
| 商务标 | editor-state 真值 | P11B 已完成并推送（计划=`6a3f4fe`、前端=`a99d8d4`）：workspace 只认服务端 editor-state，旧 workspace 键忽略保值，失败固定脱敏并隔离 A→B 迟到；AI 反馈 history 本地键仍为非目标 |
| 技术标 | editor-state 真值 | P11C 已完成并推送（计划/契约=`24b7ba8`、安全细化=`c5b3eec`、前端=`1441509`）：只认服务端 editor-state，旧键忽略保值，失败固定脱敏，required Cookie/CSRF、409/M3-D 与 A→B 挂起保存隔离均有 E2E |
| 导出 | `structure` / `min_heading_left_enabled` | P9A 已实现：叶子标题左侧强调线（`c1ff160`）；整章布局与 `structure` 仍不做，详见 `docs/plans/2026-07-13-p9a-word-layout-plan.md` |
| 业务 | 其他外部标讯数据源 | P9B 已完成唯一的国能 e 招单站受控追踪；其他网站/API/RSS、定时同步和浏览器外网请求仍未接，须另立计划 |
| 技术标 | 通用版本、响应矩阵与解析增强 | M3-D 已交付最近 20 批恢复；P12B 已完成全状态版本、围栏、检查点恢复；P12C-A/B/C 已完成九来源修订、默认最近 10 条列表、按需摘要和双工作区恢复；P12D-A/B 已完成字段摘要与“与当前对比”；P12E-A/B/C 已完成单修订对当前及双历史修订的有界正文差异；P12F-A/B/C/D/E-A 已完成最多 20 条/20 MiB 有界保留、后端游标页、前端手动加载更多、九类来源单选与后端时间范围筛选；仍未接前端日期控件、正文/多选搜索筛选、自动批量比较、完整时间线、删除、跨项目历史、多人协作、解析器自动部署/模型打包、真实模型验收与其他交付增强 |
| 资产 | 卡片化知识/多模板融合 | 阶段 1 模板 + 阶段 2 卡片库（`53e012f`）；阶段 3 M3-A 至 M3-D 均已完成，最新计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387` |
| RAG | 真语义大模型 embedding 调优 | 有本地+可选 API，可继续增强 |
| 财务 | 税务、审批、导出、预算、回款、版本与完整财务审计 | P10B/P10C 已完成报价只读、人工成本草案与毛利快照；P10J 已完成本人记录，P10K 已完成上线后项目记录；旧历史、失败尝试与完整身份审计仍未实现；禁止从报价推算 |
| 团队角色 | 人力附件、真实证件核验、投标人矩阵明细/版本/结果跟踪 | P10D/P10F/P10H/P10I 已交付；P10I 只依据人工日期提示，不属于真伪核验；其余人力和投标人数据域仍需独立契约 |
| 库 | Alembic | 仅 create_all + ALTER |
| 生产 | HTTPS/Key 加密/PG/Docker | 本机身份和成员 RBAC 已有；生产部署能力未做 |

**粗估**：技术标 ~93%；商务 ~80%；合规工具可用；内网多人 ~30%；公网 SaaS ~15%。

---

## 6. 建议下一会话方向

1. 阶段 4 **功能包 8** MVP=`6db1586`、P8B/P8C、**P8D MinerU 助手**（计划=`30d066f`、实现=`e1fe316`）与 **P8E Docling 助手**（计划=`73b1264`、后端=`79b346e`、助手=`e3f9cc4`）均已验收并推送；真实 CLI/模型仍需人工准备，自动部署仍须独立安全契约。
2. 阶段 4 **P9A/P9B/P9C/P9D** 与阶段 5 **P10A/P10B/P10C/P10D/P10F/P10E/P10G/P10H/P10I/P10J/P10K** 均已实现、独立验收并文档闭环。P9C 的真实模型门仍是运行时前置：固定依赖和模型缓存就绪后，用户显式构建索引，再运行固定预检；未通过前继续关键词降级。
3. P8C/P8D/P8E、P10K、M3-D、P11A、P11B 与 P11C 均已完成。P8E 已按顺序完成后端精确 `mineru|docling` 枚举和独立本机助手；继续保持 P8B/P8C/P8D 的策略、票据、回环和正文出域边界。
4. P12B-A/B/C/D、P12C-A/B/C、P12D-A/B、P12E-A/B/C、P12F-A/B/C/D/E-A/E-B/F-A/F-B/G-A/G-B/H/I/J-A/J-B、P12G 与 P12H 已完成；P12H 冻结=`b81546e`、实现=`1ff8839`、闭环=`92486cc`，当前后端/前端有效全量基线 **1217/318 passed**。检查点搜索已选择为 P12I 严格六文件；固定保护裁剪、跨项目版本、完整时间线和多人协作仍需另行审计拆包。

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
| docs/p8e-docling-local-helper-contract.md | P8E 后端来源枚举与本机 Docling 助手冻结契约 |
| docs/plans/2026-07-15-p8e-docling-local-helper-plan.md | P8E 两阶段受限实施与验收计划 |
| docs/p9c-fixed-model-runtime-gate-contract.md | P9C-R1 固定提交、显式准备、严格离线加载与真实预检冻结契约 |
| docs/plans/2026-07-16-p9c-fixed-model-runtime-gate-plan.md | P9C-R1 六文件 failure-first、Grok 自测与 Codex 真实验收计划 |
| docs/p12d-revision-current-diff-summary-contract.md | P12D-A 当前状态与目标修订只读差异摘要契约 |
| docs/plans/2026-07-16-p12d-revision-current-diff-summary-plan.md | P12D-A 四文件 failure-first、零写与 Codex 全量验收计划 |
| docs/p12d-revision-comparison-frontend-contract.md | P12D-B 双工作区前端比较入口、严格解析与迟到隔离契约 |
| docs/plans/2026-07-17-p12d-revision-comparison-frontend-plan.md | P12D-B 三文件 failure-first、状态互斥与串行 E2E 计划 |
| docs/p12f-revision-delete-backend-contract.md | P12F-G-A 单条修订物理删除后端完成契约 |
| docs/plans/2026-07-18-p12f-revision-delete-backend-plan.md | P12F-G-A 五文件修正、返修与独立验收记录 |
| docs/p12f-revision-delete-frontend-contract.md | P12F-G-B 共用前端确认、唯一 DELETE、重载与迟到隔离完成契约 |
| docs/plans/2026-07-18-p12f-revision-delete-frontend-plan.md | P12F-G-B 三文件 failure-first 与串行验收计划 |
| docs/p12f-revision-display-name-contract.md | P12F-H 单条展示名称、六键元数据、PATCH 与迟到隔离完成契约 |
| docs/plans/2026-07-18-p12f-revision-display-name-plan.md | P12F-H 十七文件 failure-first、四轮返修与串行验收记录 |
| docs/p12f-revision-display-name-search-contract.md | P12F-I 展示名称与可见内容联合搜索完成契约 |
| docs/plans/2026-07-18-p12f-revision-display-name-search-plan.md | P12F-I 四文件 failure-first、联合匹配与串行验收记录 |
| docs/p12f-revision-pinning-backend-contract.md | P12F-J-A 固定状态与裁剪保护后端冻结契约 |
| docs/plans/2026-07-19-p12f-revision-pinning-backend-plan.md | P12F-J-A 九文件 failure-first、锁/裁剪/迁移串行验收计划 |
| docs/p12a-editor-state-manual-checkpoints-contract.md | P12A 手动检查点只读库冻结契约 |
| docs/plans/2026-07-15-p12a-editor-state-manual-checkpoints-plan.md | P12A 七文件后端实施与验收计划 |
| docs/p12b-editor-state-version-foundation-contract.md | P12B-A 全状态版本与可选 CAS 冻结契约 |
| docs/plans/2026-07-15-p12b-editor-state-version-foundation-plan.md | P12B-A 五文件后端实施与验收计划 |
| docs/p12b-frontend-editor-state-cas-contract.md | P12B-B 三个浏览器写入者、保存队列与全状态冲突 UX 冻结契约 |
| docs/plans/2026-07-15-p12b-frontend-editor-state-cas-plan.md | P12B-B 七文件前端实施与验收计划 |
| docs/p12b-delayed-editor-state-write-fence-contract.md | P12B-C 任务/revise/callback/P8C/M3-D 延迟写入围栏完成契约 |
| docs/plans/2026-07-15-p12b-delayed-editor-state-write-fence-plan.md | P12B-C 三批实施、审查与真实验收记录 |
| docs/p12b-editor-state-checkpoint-restore-contract.md | P12B-D 恢复前安全检查点、原子 restore 与双工作区入口完成契约 |
| docs/plans/2026-07-15-p12b-editor-state-checkpoint-restore-plan.md | P12B-D D1/D2 实施、四轮前端返修与真实验收记录 |
| docs/p12c-editor-state-revision-ledger-contract.md | P12C-A 独立最近 10 条修订账本、无提交原语与 P12C-B/C 闸门 |
| docs/plans/2026-07-15-p12c-editor-state-revision-ledger-plan.md | P12C-A 三文件实施、审查返修与 67/77/666 验收记录 |
| docs/p12c-browser-put-revision-integration-contract.md | P12C-B-A 浏览器 PUT 同锁同事务修订记录完成契约 |
| docs/plans/2026-07-15-p12c-browser-put-revision-integration-plan.md | P12C-B-A 三文件实施、两轮返修与 14/107/680 验收记录 |
| docs/p12c-task-revise-revision-integration-contract.md | P12C-B-B 任务/revise 调用审计与 B1/B2 完成交付记录 |
| docs/plans/2026-07-15-p12c-task-revision-integration-plan.md | P12C-B-B1 三文件实施、一次返修与 10/126/690 验收记录 |
| docs/plans/2026-07-15-p12c-revise-revision-integration-plan.md | P12C-B-B2 双文件实施与 11/147/701 验收记录 |
| docs/p12c-callback-revision-integration-contract.md | P12C-B-C1/C2 两类 callback 的事务分叉、来源隔离与接入契约 |
| docs/plans/2026-07-15-p12c-personal-callback-revision-integration-plan.md | P12C-B-C1 双文件实施、测试返修与 10/224/711 验收记录 |
| docs/plans/2026-07-16-p12c-local-parser-callback-revision-integration-plan.md | P12C-B-C2 三文件实施、阶段守卫返修与 20/272/721 验收记录 |
| docs/plans/2026-07-16-p12c-content-fuse-apply-revision-integration-plan.md | P12C-B-D1 双文件实施、consume 隔离反假绿返修与 11/285/732 验收记录 |
| docs/p12c-revision-restore-contract.md | P12C-C2 后端受限恢复、九来源迁移与失败三域回滚完成契约 |
| docs/plans/2026-07-16-p12c-revision-restore-plan.md | P12C-C2 迁移红测、两轮受限返修与 23/121/800 验收记录 |
| docs/p12c-revision-history-frontend-contract.md | P12C-C3 双工作区修订历史面板、共享恢复链与迟到隔离完成契约 |
| docs/plans/2026-07-16-p12c-revision-history-frontend-plan.md | P12C-C3 failure-first、多轮反假绿审查与 21/51/46/284 验收记录 |
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

## 11. 当前会话状态（2026-07-18）

- **用户长期目标（必须完整保留）**：持续完成 biaoshu 标书制作者剩余主线任务，按既定路线图完成独立规划、受限实现审查、独立验收、中文文档闭环与协作分支推送；不直接操作 `main`。
- **P11A 已完成并推送**：计划=`70a2dc7`、前端=`b0a86e4`。服务端 `/api/projects*` 已成为技术标/商务标项目列表、详情、创建及查重/废标选择器的唯一真值；旧 `biaoshu.projects.v1` 被忽略且原值不变，失败不再生成本地项目或复活演示项目。
- **P11B 已完成并推送**：计划=`6a3f4fe`、前端=`a99d8d4`。商务 workspace 只认既有 editor-state GET/PUT；旧 workspace 键忽略保值，真实空态保持空，GET/PUT 固定脱敏失败，任务后刷新失败与 A→B 迟到均有显式边界；AI 反馈 history 仍为非目标。Grok 因 402 未发正式审查消息，Codex 依据现有差异完成独立审查，仅修正 E2E 测试时序后验收。
- **P11C 已完成并推送**：契约=`docs/p11c-technical-editor-state-truth-contract.md`，计划=`docs/plans/2026-07-15-p11c-technical-editor-state-truth-plan.md`，前端=`1441509`。技术标 editor-state 只认服务端；旧本地键忽略保值，真实空态不补 mock，失败固定脱敏；普通与合并 PUT 使用同源 Cookie/内存 CSRF；409/M3-D 保持兼容；A 的迟到或挂起保存不污染、阻塞 B；生产演示入口已移除。
- **P8D 已完成并推送**：契约=`docs/p8d-mineru-local-helper-contract.md`，计划=`docs/plans/2026-07-15-p8d-mineru-local-helper-plan.md`，实现=`e1fe316`。纯标准库助手只从交互 TTY 读取 P8C 单次票据，Windows 只认 `mineru.exe`，强制本地离线模型、环境白名单、临时输出和回环无代理/无重定向单次回调；真实 CLI/模型由用户人工准备。
- **P8E 已完成并推送**：契约/计划提交=`73b1264`，P8E-A 后端=`79b346e`，P8E-B 助手=`e3f9cc4`。Docling 46、MinerU 54、后端受影响回归 37、P8C E2E 9、P8B E2E 6 passed；真实 Docling/模型仍未安装、未验收，禁止声称真实模型就绪。
- **Grok 当前状态**：2026-07-19 已通过 `grok login --device-auth` 重新认证，P12H 两文件返修可正常执行。P12H 首轮任务=`msg_282f2c6bd315485f960f1360361403ec`，返修=`msg_732b2095aa73484bbcc56572d5ab4a08`/`msg_64032847642b43008dcfc683c736029a`，最终 review=`msg_92c05eeb3bba4dd1801470646e74084d`，Codex ack=`msg_c7168985bed9415ab1fc44420474d857`；Grok 全程只实现/自测，未暂存、未提交、未推送。
- 当前分支仍为 `collab/grok-code-codex-review`；P12F-H 冻结=`0660145`、范围修订=`0db935b`/`aca68b6`、实现=`b4338ba` 已提交并推送，当前文档闭环提交以实际 HEAD 为准。全部既有基线保留。新会话第一步必须用 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review` 重新核验，不可只信本文静态 SHA。
- 阶段 3 **已完成并推送**：M3-A 只读融合建议、M3-B 差异预览与浏览器确认、M3-C 会话内单批撤销、M3-D 服务端原子确认与最近 20 批持久恢复。
- 阶段 4 **包 5** 已推送：`460097a` 智能建议人工确认 E2E。
- 阶段 4 **包 6** 已推送：`1289c92` 实现响应矩阵源分页调用。
- 阶段 4 **包 7** 已推送：`2c7b3e0` 实现响应矩阵字段级三方合并（base 快照 + 原子字段三方合并 + 冲突显式选择 + 仅矩阵 PUT + field-merge E2E）。
- 阶段 4 **包 8** MVP：**已验收并推送** `6db1586` 实现可插拔解析引擎调度（父提交 `834969e`；`parse_engines` + `_run_parse` 调度；默认 lightweight；测试 fake；非法引擎 failed 不静默回退；当时 MinerU 仅外置 callback、尚未接 Docling）。后续 **P8B** 已完成：计划=`f662674`、后端=`0994cc8`、前端=`80d2579`；脱敏策略接口只回 `light|local|ask`，技术标/商务标每次动作重新读取，`light` 显式任务、`local` 只带项目 ID 回传、`ask` 一次性选择且取消不建任务；不启服务端 MinerU/Docling、不持久化策略。P8D/P8E 又在后续分别补齐本机外置助手，但仍未把解析器嵌入服务端。
- **P8C 本地解析一次性回传票据交付**：计划=`cabe99d`，后端=`af39ff8`，前端=`1cf5576`。required strict `bid_writer` 受会话/CSRF 保护显式签发 10 分钟单项目单次票据，库内只存摘要；唯一公开 POST 使用流式 2 MiB 上限和条件 UPDATE，同事务写解析结果、任务、项目步骤与固定脱敏审计。前端只在组件内存显示当前 origin 的固定 `curl.exe`，disabled 保留旧表单，其他角色零签发。完整契约见 `docs/p8c-local-parser-one-time-callback-ticket-contract.md`。
- **包 9A** 已实现并完成完整独立验收：计划=`57b394a`，实现=`c1ff160`，自动化文档闭环=`6d36365`，WPS 视觉验收闭环=`3dadaf8`。技术标父标题保持普通边框，叶子标题“部署架构/机房节点/售后保障”强化左栏；商务标叶子小节“二、资格响应”强化左栏；均无整章页框。不接 `structure`。
- **包 9B 交付完成**：初始审计=`a1ba88a`；用户指定国能 e 招单站后，依次推送 `45d7214`、`1c46e41`、`6491363`、`229f1d7`、`000b403`、`a7cfcb8`。P9B 不使用未获授权的通用来源；完整固定契约、数据最小化、人工确认、验收和非目标见 `docs/p9b-chnenergy-integration-contract.md`。
- **P9B 国内来源补充审计**：已将全国公共资源交易平台、中国政府采购网、天津/北京开放数据的公开资料写入包 9 总计划。全国平台公开公告页不等于读取 API；中国政府采购网规范是签名发布接口；天津候选虽有截止时间字段但公开页无实际端点且数据元信息陈旧；北京候选需 `userKey` 且无独立截止时间字段。均未满足完整受控读取契约，禁止据此写网页抓取或同步代码。
- **P9B 最终验收**：Codex 独立运行后端全量 230 passed（固定 `PYTHONHASHSEED=0`，仅 1 条既有弃用警告）、前端 lint/build、P9B E2E 1 passed 和 `git diff --check`；并对用户给定公告执行只读核验，正文北京时间截止时间为 `2026-07-29 09:00:00`。无真实数据库写入、无浏览器外网同步。
- **P9C 交付与真实模型门**：P9C 已按纯离线 BAAI/bge-small-zh-v1.5（512 维、CPU）、版本并存与可见关键词降级完成 `cc0d217`、`a0bd84b`、`71c503c`、`585e502` 四个实现提交。正文/查询不得出域，旧 API embeddingModel 与旧哈希均不参与知识库语义检索。固定评测集有 20 条完全合成查询，评测文件的版本、模型、维度和阈值均为硬校验；预检无下载/路径/跳过磁盘参数。本机无模型缓存时，Codex 实测返回 `model_unavailable`/退出码 2；这不是缺陷，未通过真实预检前不得称语义索引就绪。完整契约见 `docs/p9c-offline-semantic-index-contract.md`。
- **P10A 身份/RBAC 交付**：实现提交为 `a025627`（身份会话）、`c60a2d2`（成员管理和权限收口）、`64d32e0`（前端会话、认证模式握手和 CSRF 续发）；两份实施修订文档为 `1a442c0`、`3716e4f`。`required` 使用 HttpOnly 不透明会话、scrypt、成员工作空间校验、最后所有者保护和设置 owner 收口；前端不会持久化口令/Cookie/CSRF，硬刷新用受会话保护的 `/api/auth/csrf` 安全续发。P10B 以独立严格 `finance` 依赖补充报价只读能力，没有放宽 P10A 的默认业务拒绝。
- **P10B 财务报价交付**：计划=`5d99888`，后端=`bc0517c`，前端=`ef1e369`。严格财务角色只能读取当前空间商务标报价白名单投影；无会话在 required 下保持中间件 `401 auth_required`，已登录非财务与 disabled 为 `403 role_forbidden`；技术标、跨空间和不存在项目统一 404。完整契约见 `docs/p10b-finance-business-quote-contract.md`。
- **P10C 财务成本草案交付**：计划=`b662e85`，后端=`6f30084`，前端=`737c7db`。strict `finance` 可维护当前空间商务标人工成本条目，并以整数分读取报价、成本、毛利和毛利基点；金额输入服务端 `StrictInt` 拒绝浮点/字符串/布尔；成功写入仅审计动作和条目 ID；前端不持久化敏感数据，项目切换明细未就绪前不挂载成本面板。无税务、审批、导出、预算、回款、版本或审计查看。完整契约见 `docs/p10c-finance-cost-draft-contract.md`。
- **P10D 人员资质素材卡交付**：计划=`6555998`，后端=`d8f7cbd`，前端=`71f065a`。strict `hr` 仅可管理当前空间的最小人员资质卡；`require_hr` 不因所有者身份隐式放行，列表不返回备注，详情/写入才返回备注，创建/更新需 CSRF，`isActive` 仅接受 JSON `true/false`，跨空间/不存在统一 404，审计只写 action 与 `hcc_*` ID。前端 `/hr` 仅 HR 有入口，选中才取详情，每次创建/编辑/启停后重读列表和详情，不持久化卡片；卡片本身无删除、附件、联系方式、证件号、项目关联、导出或跨空间搜索；团队快照仅由 P10F 独立提供。完整契约见 `docs/p10d-hr-credential-cards-contract.md`。
- **P10F 人力项目团队推荐快照交付**：计划=`12e067f`，后端=`3dc600a`，前端=`254f8c7`。strict `hr` 仅可通过 HR 项目 `id/name` 选择器为当前空间技术标项目维护有序的有效卡摘要快照，写入需 CSRF，`remark` 不复制；strict `bid_writer` 只能在用户点击后读取本项目最小投影。disabled、非相应角色和仅 `is_owner` 均不放行；真实 `member.role=bid_writer` 的所有者按角色正常通过。快照不随来源卡编辑/停用自动变化，所有响应 `no-store`，审计只记录 `htr_*`。前端不预读 HR 详情、不持久化数据，项目切换不会短暂展示旧项目结果；无业绩、证件、附件、AI 推荐、审批、导出或 Word 写入。完整契约见 `docs/p10f-hr-team-recommendation-contract.md`。
- **P10E 投标人匿名合规预览交付**：计划=`26f7e40`，后端=`1b6ccf3`，前端=`37cf835`。`require_bidder` 只允许 required 模式当前空间精确 `bidder`；唯一 `GET /api/bidder/compliance-preview` 使用收敛技术标响应矩阵，返回 `dataState` 与匿名五计数，固定 `no-store`。项目数量/ID/名称、工作空间、原文、来源、章节、大纲、备注、人员与财务字段均不出域；成功读审计只记录 `bidder_compliance_preview_read` 与 `anonymous_aggregate`。前端 `/bidder` 仅投标人可挂载，唯一本机业务请求为该 GET，错误固定中文脱敏且不写浏览器存储。P10E E2E 覆盖匿名投影、空态、错误、角色拒绝、网络白名单和存储边界；P10E 本身无写入、项目详情、版本或结果跟踪，最小项目五计数仅由独立 P10G 提供。完整契约见 `docs/p10e-bidder-anonymous-compliance-preview-contract.md`。
- **P10G 投标人项目级合规统计交付**：计划=`26b43ea`，后端=`c3cf8b4`，前端=`d5656cc`。`require_bidder` 只允许 required 模式当前空间精确 `bidder`；选择器 `GET /api/bidder/project-compliance/projects` 仅返回技术标 `id/name`，不审计；详情 `GET /api/bidder/project-compliance/{projectId}` 仅返回 `dataState` 与五项汇总。disabled、仅所有者、其他角色均拒绝；真实 `member.role=bidder` 的所有者按实际角色通过。跨空间/不存在/商务标固定 `404 bidder_project_compliance_not_found`，不反射路径项目 ID；成功响应 `no-store`，详情审计固定 action/target 且不记录项目标识、计数或矩阵。前端先取选择器再按用户选择取详情，不回退 P10E、不写 URL/浏览器存储，项目切换不会展示旧结果。P10G 不含项目详情、矩阵原文、人员、财务、写入、导出、版本、结果跟踪或规则执行；完整契约见 `docs/p10g-bidder-project-compliance-contract.md`。
- **P10H 人员业绩素材卡交付**：计划=`7694843`，后端=`6c76d80`，前端=`4eb8a14`。`require_hr` 只允许 required 模式当前空间精确 `hr`；摘要列表不含 `performanceSummary`/`remark`，详情按需读取，创建/编辑/启停走 CSRF 且写后强制重读。严格年份、布尔、额外键、空补丁与显式非法 `null` 均有固定 422；跨空间/不存在/伪造 ID 固定 404，不反射 ID。成功响应 `no-store`，审计固定 action/`hpc_*` target 且不记录业务值。前端无 P10D/P10F 回退、无浏览器存储/URL 参数，迟到响应不覆盖新卡。无删除、附件、证件校验、联系方式、合同金额、项目关联、团队组装、审批、导出或 Word 写入；完整契约见 `docs/p10h-hr-performance-cards-contract.md`。
- **P10I 人员资质到期提示交付**：计划=`ddc1807`，后端=`d5201e9`，前端=`49daa16`。唯一 GET 仅向 required 模式当前空间精确 `hr` 开放；服务端 UTC 日期和固定 90 天窗口、必要 SQL 列、有效卡只计数、停用卡只排除、固定 `no-store` 与脱敏审计。前端服务端日期直出，Strict Mode 首次严格单次 GET，刷新后累计两次，无模块全局缓存、P10D/P10F/P10H 回退、浏览器存储或 URL 参数。只做人工日期提示，不是真实证件核验；完整契约见 `docs/p10i-hr-credential-expiry-contract.md`。
- **P10J 财务个人成本变更记录交付**：计划=`701c946`，后端=`4e662d6`，前端=`fce6cb6`。唯一 GET 仅向 required 模式当前空间精确 `finance` 开放，只查询本人最近 50 条成功成本变更；SQL 上限前完成字面前缀、非空后缀和无首尾空白过滤，只投影 action/target/created_at。前端 Strict Mode 首次严格单次 GET、刷新累计两次，不请求报价/草案/项目/其他角色或外网，不写浏览器存储。它不包含项目、金额、内容、前后值、失败尝试或其他成员，不是完整审计；完整契约见 `docs/p10j-finance-personal-cost-change-events-contract.md`。
- **M3-C 融合写入单批撤销交付**：计划=`c63310f`，实现=`b8ff605`。当前融合对话框只保存最近成功批次的最小内存快照；撤销点击时精确校验章节存在性、标题、正文和状态，未漂移才恢复正文与原状态，漂移章跳过。快照一次消费、关闭即失效；无新 API、后端、存储、历史栈或通用撤销。完整契约见 `docs/m3c-content-fuse-undo-contract.md`。
- **M3-D 融合写入持久恢复交付**：计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`。后端以成功任务结果为唯一建议权威，锁内校验 base，同事务写章节/快照/裁剪，最近 20 批且漂移安全一次消费；前端确认前零本地写，POST 成功后唯一真实重载，业务已完成但重载失败有独立固定中文，项目/关闭迟到不污染，不写浏览器存储或外网。完整契约见 `docs/m3d-content-fuse-persistent-recovery-contract.md`。
- **P9D 导出图片失效引用提示交付**：计划=`4925a51`，实现=`e5adad7`。技术标/商务标成功 export 只消费后端 `imageWarnings`，最多 20 条、每条 240 码点，以 React 纯文本显示且继续下载；告警绑定项目并用实例代次隔离迟到响应。两轮审查修复首帧旧告警/迟到污染、E2E 假同步、调用顺序和 lint warning。完整契约见 `docs/p9d-export-image-warning-contract.md`。
- **已验证基线**：P12F-D 后端专项/游标与 C1 回归/全量 **68/48/986 passed**；前端聚焦/history/技术真值/商务真值/checkpoint/全量 **3/37/28/18/51/300 passed**；P13-A 历史基线 **13/72/918 passed**；P9C-R1 专项/语义/知识库完整 **17/21/28**、真实预检 **1.0/0.927295**。后端全量仅 1 条既有 Starlette/httpx 弃用告警。**E2E 共用 SQLite 重置库，所有 Playwright 命令必须串行。**
- **P10J 已完成**：契约=`docs/p10j-finance-personal-cost-change-events-contract.md`，计划=`docs/plans/2026-07-14-p10j-finance-personal-cost-change-events-plan.md`。两轮后端审查和一轮前端测试网络审查均闭环。
- **P8C 已完成**：契约=`docs/p8c-local-parser-one-time-callback-ticket-contract.md`，计划=`docs/plans/2026-07-14-p8c-local-parser-one-time-callback-ticket-plan.md`。两轮后端审查和三轮前端反假绿审查均闭环；它只补 required 模式回传授权，不交付 MinerU/Docling 运行时。
- **P10K 已完成**：计划=`2e53007`、后端=`1eaa75e`、前端=`dbf301c`。最小 `finance_project_cost_change_events` 只记录本包上线后 P10C 成功变更并与业务/审计同事务；项目 GET 只回 action/entryId/actorScope/occurredAt，前端只在 `/finance` 显式点击后读取。后端全量 453、前端全量 140 均通过。
- **M3-D 已完成**：服务端原子确认成功 `content_fuse` 任务中的用户选择，只保留每项目最近 20 批；一次性恢复时仅覆盖 title/body/status 仍精确等于 after 的章节。代码提交与远端一致后才开始本文档闭环。
- **P11A/P11B/P11C 已完成**：P11A 让技术标/商务标列表、详情与创建只认服务端项目；P11B、P11C 分别让商务标和技术标编辑内容只认服务端 editor-state。旧项目键与两类旧 workspace/editor 键均不再作为成功依据；前端全量从 P11A 的 155、P11B 的 166 增至 P11C 的 184。
- **P12A 已完成**：计划/契约=`bf8ccd6`、后端=`9f53d92`。显式服务端检查点精确保存 13 键规范快照，每项目最近 20 条；创建/裁剪同事务、完整失败域显式回滚，列表/淘汰不加载正文，详情作用域和完整性严格校验。两轮返修与 Codex 独立 29/97/15/518 验收闭环；没有恢复、删除、下载、自动历史或前端。
- **P12B-A 已完成并推送**：计划/契约=`0b55c30`、实现=`780cc82`。共享 P12A 同算法 `stateVersion`，可选 `expectedStateVersion` CAS 只用一次锁后行，全状态冲突优先且最小脱敏；两轮返修关闭重复读取、提交后假失败、时间戳漂移和非有限值兼容。Codex 独立 19/12/104/537 验收闭环。缺 expected 仍兼容旧写入，明确不是恢复安全门。
- **P12B-B 已完成并推送**：契约/计划=`0636302`、实现=`473e823`。两个项目保存队列收口技术、商务、guidance 和矩阵合并浏览器写入；全部 PUT 使用最新 expected，全状态冲突保留本地并只允许显式全量重载。两轮返修与 Codex 独立 28/18/8/4/6/5/201 验收闭环；没有实现 P12B-C 写入围栏或 restore。
- **P12B-C 已完成并推送**：冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`。任务/revise、个人 callback、P8C 票据和 M3-D 均绑定权威版本并在最终写入锁后比较；陈旧写零业务落库，P8C 票据例外为消费后 409。M3-D 与技术普通/矩阵 PUT 共用队列，成功唯一重读，不确定结果保守阻断；两轮返修关闭下一编辑丢失和跨导航测试假绿。独立 62/570/48/212 验收闭环；没有实现 restore。
- **P12B-D 已完成并推送**：冻结=`613818f`、D1=`551caba`、D2=`0f81dd6`。后端同事务创建恢复前安全检查点并原子写回严格目标，前端双工作区显式创建/二次确认恢复、执行时 expected、唯一重读与迟到隔离均已闭环；独立 58/81/599 与 51/63/263 验收通过。它没有自动记录每次写入，也不是任意版本库。
- **P12C-A 已完成并推送**：冻结=`daa8c43`、实现=`226e1c1`。独立表保存每项目最近 10 条规范修订，无提交原语覆盖首次、连续、断链、回退和相邻去重；拒绝缺任一 13 键的假状态，正文不进入列表/日志/返回。独立 67/77/666 验收通过。它是账本基础，不单独等于自动历史已交付。
- **P12C-B-A 已完成并推送**：冻结=`fbf93c0`、实现=`acf3139`。仅公开浏览器 PUT 以服务端固定 `browser_put` 来源接入；锁后 before、写后 after 与账本记录共用同一事务，冲突、记录失败和 commit 失败均证明双零写。独立 14/107/680 验收通过；请求体伪造来源无效，其他生产写入者仍未接入。
- **P12C-B-B1 已完成并推送**：冻结=`05864f6`、实现=`5a0d1c0`。九类 writer 每次实际迁移固定记录 `task`；批量章节逐章提交，内部 upsert 异常固定脱敏，CAS 冲突保持 stale。独立 10/126/690 验收通过；export/response_match/content-fuse 未误接。
- **P12C-B-B2 已完成并推送**：冻结=`3a30c03`、实现=`5149385`。五类商务 revise 的两个真实写点固定记录 `revise`；零变化、技术 revise、陈旧 expected、并发漂移与失败原子性均已覆盖。独立 11/147/701 验收通过；无 API、Schema 或前端改动。
- **P12C-B-C1 已完成并推送**：冻结=`76834f5`、实现=`1d0ce0e`。个人 callback 用同一次锁后 before、提交前内存 after 与固定 `callback` 原子留史；固定 500、来源隔离与全域回滚已关闭测试假绿。独立 10/224/711 验收通过；P8C 路径未被提前接入。
- **P12C-B-C2 已完成并推送**：冻结=`52bbabf`、实现=`82cc82e`。fresh P8C 回调以固定 `local_parser` 与票据消费、正文、任务、项目和审计同事务留史；stale/null 只消费零修订，其他失败回滚可重用。独立 20/272/721 验收通过；生产返修期间哈希未变。
- **P12C-B-D1 已完成并推送**：冻结=`e8ffaeb`、实现=`a6a28f6`。融合 apply 以固定 `content_fuse_apply` 与章节、恢复批次和裁剪同事务留史；空账本 before+after、已有基线精确 +1、失败全域回滚和双并发均已证明。Codex 关闭 consume 隔离假绿后独立 11/285/732 验收通过。
- **P12C-B-D2 已完成并推送**：冻结=`6b83fc1`、实现=`f256f5b`。融合完整/部分 consume 以固定 `content_fuse_consume` 原子留史，零恢复只消费且状态/版本/修订全等；跨项目/跨空间、精确并发错误码、失败全回滚和公开 500 脱敏均已证明。Codex 独立 25/299/746 验收通过。
- **P12C-B-D3 已完成并推送**：冻结=`1d44484`、实现=`b91a7ff`。不同版本 checkpoint restore 固定记录 `checkpoint_restore`，同内容零修订；回退新时间点、跨空间、精确并发、完整失败零写与四类失败可重试均已证明。Codex 独立 18/270/764 验收通过。
- **P12C-C1 已完成并推送**：冻结=`26b504e`、实现=`7023ecd`。最近 10 条元数据列表不加载正文，详情按 revision/workspace/project 三重作用域读取并重验规范快照；真实坏时间物化、越界字节、非法来源和正文损坏均固定 500/no-store。Codex 独立 13/201/777 验收通过；无恢复或前端。
- **P12C-C2 已完成**：冻结=`54af600`、范围修订=`2276366`、实现=`0803250`。严格 expected CAS、C1 目标重验、安全检查点、共享写回、准确 `revision_restore`、双配额和旧库迁移已闭环；Codex 独立 23/121/800 验收通过。
- **P12C-C3 已完成**：冻结=`6b9143a`、实现=`5e4f9f6`。默认折叠、严格列表/按需摘要、共享令牌/保存链、执行时 expected、唯一重读和 list/detail/restore 迟到隔离已闭环；多轮反假绿后 Codex 独立 21/51/46/284、lint/build 验收通过。后续 P12D/P12E 已补齐对比，P12F-A 至 G-A 已补齐有限保留、游标加载、来源/时间/可见内容搜索和单条修订删除，P12H 已补齐单条检查点删除；来源多选、日期预设、跨项目历史或多人协作仍未实现。
- **P9C-R1 已完成并推送**：冻结=`cd70ef0`、实现=`b53dcce`。固定依赖、固定 endpoint/revision/10 文件/权重哈希、显式准备唯一联网路径、生产/预检严格离线和跨 cwd 缓存确定性均已闭环；真实制品指纹=`a04f4aa475164fb551464a0320b09c37`，预检 Recall@5=`1.0`、NDCG@5=`0.927295`，后端全量 **817 passed**。首次专项 8 failed；返修红测 11 failed / 6 passed，最终专项 17 passed。测试假制品临时写入已从约 0.54 GiB 收敛到 925 字节。模型缓存被 Git 忽略，不是仓库交付物。
- **P12D-A 已完成并推送**：冻结=`2cc6ee3`、实现=`9445fcc`。首次红测因 fixture 单值查询触发 `MultipleResultsFound` 而无效；生产代码未改时修正 fixture 后有效红测 **14 failed**，最终 Grok/Codex 专项 **14 passed**、受影响回归 **132 passed**、后端全量 **831 passed**。四文件白名单、共享 13 键逐字段规范 JSON、两侧六项摘要、固定错误脱敏、五域零写及 `True`/`1` 反假绿均通过；无前端、正文/值/ID/版本回显、恢复或数据库写入。
- **P12E-A 已冻结并验收**：单条历史修订与当前状态的有界章节正文差异预览，冻结=`5aa205c`、实现=`f9f067e`。只读服务、精确 schema/路由、严格 parser、技术/商务共享面板和三条独立 E2E 均在七文件白名单内；Codex 已完成首轮资源上限返修审查、独立串行验收、中文提交与推送。
- **其余未实现主线**：检查点固定与保护裁剪、检查点搜索/排序、自动批量比较、完整时间线、来源多选、日期预设、名称排序/批量、跨项目历史与多人协作；搜索片段/高亮/游标/缓存；MinerU/Docling 自动安装、模型打包、常驻服务、真实模型样本验收与完整孙进程治理；P9C 真实用户语料调优、其他模型/GPU/在线 embedding/自动更新；Word `structure`/整章布局；除国能 e 招外的合法外部标讯来源；人力附件/真实证件核验；财务税务/审批/导出/预算/回款/版本、失败尝试与完整身份审计；投标人矩阵明细/版本/结果跟踪；Alembic、PostgreSQL、HTTPS、Key 加密、Docker 和公网 SaaS。
- 新任务分工不变：Grok 只负责限定实现与自测，未经 Codex 审查确认不得提交；Codex 负责计划、范围冻结、差异审查、独立测试、验收、中文提交、文档闭环和 GitHub 状态核验。每一包仍按“计划提交 → 实现提交 → 文档闭环提交 → 推送协作分支”执行，禁止合包。
- GitHub 若出现连接重置，可在当前 PowerShell 进程临时配置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY=http://127.0.0.1:7890` 与 `NO_PROXY=localhost,127.0.0.1` 后重试；不得把代理或凭据写入仓库。

**换会话可直接：核验分支、HEAD/上游/远端与工作区状态 → 读本文 §0～§3.1、§4.23、§5、§6、§11、P12I 契约/计划及路线图 → 确认 P12H 冻结=`b81546e`、实现=`1ff8839`、闭环=`92486cc` 已推送且后端/前端有效基线 1217/318 passed → 若 P12I 冻结提交已在远端，则通过消息箱让 Grok 严格六文件 failure-first 实现。禁止让 Grok commit/push、未经契约加入固定保护、排序/分页、跨项目历史、完整时间线或多人协作，或由 Codex 冒充 Grok 完成主实现。**
## P12D-B 完成交接（2026-07-17）

当前协作分支已完成 P12D-B：Grok 任务 `msg_a8258d4b49f44678bf43fe2a2356d583`，首轮 review_request `msg_9394bad10ef34048977ecdc9c9250239`；Grok 未提交/推送，Codex 独立审查、验收、文档闭环并负责本次提交推送。三文件白名单为 API 封装、共享修订面板、修订历史 E2E。

首轮红测真实结果为 2 failed / 21 passed / 1 did not run（串行分组首个入口缺失导致第三条未运行），不符合原计划 3/21，已如实记录，禁止在交接中冒充 3/21。生产实现后的 Codex 独立结果为：历史 24、检查点 51、技术/商务真值 46、前端全量 287 passed；lint/build/diff 均通过。

已实现：按需“与当前对比”按钮；严格四键/六键 parser；13 键有序无重复差异字段；固定中文标签；摘要/比较/恢复互斥；项目、折叠、刷新、摘要、恢复和卸载的 arrived/complete 迟到隔离；无正文、ID、版本、内部键和值泄漏。

P12D-B 本包当时未实现正文 diff 或双历史修订比较；后续 P12E-A/B/C 已补齐这些只读能力。仍未实现自动批量比较、删除、搜索、分页、导出、分享、多人协作、比较缓存和比较结果自动恢复，后续包必须重新规划并冻结。
Codex 验收确认消息箱回执：`msg_733aeffa4a144d7192aa296263055aba`。

## P12E-A 完成交接（2026-07-17）

P12E-A 冻结=`5aa205c`，Grok 实现=`f9f067e`，最终 review_request=`msg_c24f270186a741a09a33781e84b1e762`，Codex 验收确认=`msg_1432aa1aacf944d28b2089dda8f2bb7c`。首轮审查真实发现第 101 个正文差异章仍进入 difflib；返修任务=`msg_f09905515e974049827cd981087884c6`，红测 **1 failed / 1 passed**、修后 **2 passed**。修复后完整值扫描覆盖全部配对，只有前 100 个实际正文差异章进入 difflib，尾章差异仍返回非空有界项。

最终独立验收为：后端专项/受影响回归/全量 **23/27/854 passed**（1 条既有 Starlette/httpx 弃用告警）；history/checkpoint/truth/前端全量 **27/51/46/290 passed**，全部 Playwright 使用单 worker、零重试串行；lint、build、diff-check、七文件白名单和空暂存区通过。实现已由 Codex 以中文提交并推送，工作区闭环后必须再次核对 HEAD 与远端一致。

P12E-A 交付边界是“一条历史修订 ↔ 请求时当前状态”的只读章节正文差异；双历史修订手动比较随后已由 P12E-B/C 完成。完整时间线、正文自动恢复、删除、搜索、分页、导出、分享、跨项目历史和多人协作仍未实现，下一包必须另立契约、计划和白名单。

## P12E-B 完成交接（2026-07-17）

P12E-B 契约=`docs/p12e-revision-pair-body-diff-contract.md`、计划=`docs/plans/2026-07-17-p12e-revision-pair-body-diff-plan.md`，冻结提交=`00ef081`，实现提交=`5a5b08a`。目标是同一工作空间/项目内两条历史修订的只读正文差异；路径为 `/api/projects/{projectId}/editor-state-revisions/{beforeRevisionId}/body-diff/{afterRevisionId}`，响应使用 `beforeChapterCount/afterChapterCount`，不读取当前 editor-state。

Grok 只修改了 `backend/app/api/schemas.py`、`backend/app/api/editor_state_revisions.py`、`backend/app/services/editor_state_revision_body_diff_service.py` 和新建 `backend/tests/test_p12e_revision_pair_body_diff.py`；先写真实红测，再实现双快照服务。禁止前端、分页、搜索、恢复、删除、导出、分享、缓存或多人协作。最终 review_request=`msg_d8a128763e274c3b8eb12c6e1234d456`，Codex 验收回执=`msg_f7bd19cc0dae4834b275823a90c4a6f7`；Grok 未提交/推送。

Failure-first 真实记录：13 项红测中 11 项是新路由尚不存在的 HTTP 404，1 项是同正文双修订夹具因 `stateVersion` 重合导致 before/after ID 相同，1 项是 AST 断言缺少 `compare_revision_bodies`；夹具修正后 pair 专项 13 项通过。不能把 13 项全部表述成路由缺失。

独立验收：P12E-B/P12E-A/P12D-P12C 合并专项 **86 passed**，后端全量 **867 passed**，均仅 1 条既有 Starlette/httpx 弃用告警；三生产文件 `py_compile`、`git diff --check`、精确四文件白名单、空暂存区均通过。P12E-B 本包只包含后端双修订基础，前端选择器随后由 P12E-C 完成；自动批量比较、完整时间线、分页/搜索、恢复/删除/导出/分享、跨项目历史、缓存或多人协作仍未实现。

## P12E-C 完成交接（2026-07-17）

P12E-C 契约=`docs/p12e-revision-pair-frontend-contract.md`、计划=`docs/plans/2026-07-17-p12e-revision-pair-frontend-plan.md`，冻结提交=`8b40bf4`，实现提交=`b6a4375`。技术标与商务标共用同一历史面板：内存选择“差异前/差异后”，选择零请求，比较精确一次 P12E-B GET，并展示严格解析后的有界中文差异。

Grok 只修改 API 封装、共用面板和既有 history E2E 三文件；任务=`msg_70f49042da2e46d5a7d2783ee8f7575f`，最终 review_request=`msg_fa38202aa5d641d5b111d914995d6f4f`，Codex 验收回执=`msg_fd6c844f235644e9b3c4bd597d049d36`。Grok 未提交或推送。

真实 failure-first 为 **3 failed / 0 passed**，首个失败是生产面板没有双修订选择按钮；不是收集、fixture、依赖、白页或服务启动错误。实现后聚焦 **3 passed**。Codex 独立通过 P12E-C 聚焦 **3**、P12E-A/P12D-B/P12C-C3 history 回归 **27**、前端全量 **293 passed (8.2m)**；全部 Playwright 使用 `--workers=1 --retries=0`。lint、build、diff-check、精确三文件和空暂存区均通过。

本包没有实现分页、搜索、自动批量比较、完整时间线、恢复/删除、导出、分享、缓存、跨项目历史、URL/浏览器存储或多人协作。下一包必须先审计剩余主线并重新冻结，不得直接扩大 P12E-C。

## P12F-A 完成交接（2026-07-17）

P12F-A 契约=`docs/p12f-revision-retention-quota-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-retention-quota-plan.md`，冻结=`e713fb3`、实现=`24f4cf2`。选择本包的原因是旧写入事务把每项目修订裁成 10 条，直接做分页只能产生无法访问更老数据的假入口。

交付结果：常规小快照最多保留最近 20 条，同时以项目总 `snapshot_bytes <= 20 MiB` 约束最坏磁盘占用；按 `created_at DESC, id DESC` 保留连续最新前缀，达到任一上限后删除该条及所有更旧行。默认历史 GET 继续只返回最近 10 条，响应 shape 不变。

Grok 严格只修改两个服务和四个既有测试。真实 failure-first 为 **9 failed / 0 passed**，首个业务失败是旧计数常量仍为 10；实现后聚焦 **9 passed**。首轮 review_request=`msg_63b19b98d56645bb98e96e0affd44524`；Codex 要求补强非法元数据失败后的精确零副作用断言，返修 task=`msg_72c9cee33d5446358a29aab701aa5909`、review_request=`msg_7fa5a6f3c971479aa8c2b65f7b37cdaa`。

Codex 独立审查确认裁剪 SELECT 无 `snapshot_json`、先完整校验再删除、连续最新前缀、DELETE workspace/project/id 三重作用域且只 flush。非法元数据失败前后精确比较 `id/state_version/snapshot_bytes/source_kind/created_at`，本项目与旁路项目均零副作用。独立六文件专项/受影响回归/后端全量为 **121/134/871 passed**，仅 1 条既有 Starlette/httpx 弃用告警；验收回执=`msg_4cd3242575cb4c5d865138415e57a028`。

P12F-A 未回填已裁历史，也未实现分页 API、前端加载更多、搜索、删除、命名、固定、导出、分享、跨项目历史或多人协作。下一步只可先审计并独立冻结 P12F-B 游标分页，不得直接沿用本包白名单或顺带扩张功能。

## P12F-B 后端修订游标页完成交接（2026-07-17）

契约=`docs/p12f-revision-cursor-page-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-cursor-page-plan.md`。审计结论是不能修改既有列表成功体，否则会破坏 P12C-C1 的顶层精确 `{items}` 和当前前端最多 10 条 parser；因此新增独立只读 `GET /api/projects/{projectId}/editor-state-revisions/page`，P12F-C 再单独接“加载更多”。

固定页大小 10，查询投影仍为五列并 `LIMIT 11`；游标使用 `esrc1_` 版本前缀和规范无填充 base64url，只携带 UTC 微秒时间位置与合法修订 ID。带游标时使用 `created_at < t OR (created_at = t AND id < id)`，禁止主动/非零 OFFSET、COUNT 和正文投影。SQLite 方言会把单纯 `.limit(11)` 编译成 `LIMIT ? OFFSET ?`，但 OFFSET 绑定恒为 0，源码没有 `.offset(` 调用；这是方言占位行为，不是偏移分页。成功体精确 `items/nextCursor`；非法游标固定 400 `editor_state_revision_cursor_invalid`，所有成功/业务错误 `no-store`。

冻结提交=`4ddd896`，实现提交=`c84a94d`。Grok 原任务=`msg_b044740a30cc4e82ac4c98c4c42731c4`，真实 failure-first 为 **27 failed / 3 passed**：新 `/page` 被动态 `/{revision_id}` 吞为旧 404，且页大小常量尚不存在；实现后首轮专项 **30 passed**，首轮 review_request=`msg_5df53113b2894ea984694c8d21d15601`。

Codex 首轮审查拒绝 Windows `datetime.fromtimestamp` 最大年份平台依赖、编码端可能生成解码器必拒的 pre-1970 游标，以及 lookahead 损坏测试中的恒真 `or`。返修任务=`msg_628cbdef5bf24ac09f4f08d676f79d25`，返修回执=`msg_6a45abaf4cc141d7bcf066c809b7a11f`；最终使用 UTC epoch + `timedelta(microseconds=us)`，编码端严格校验 ID 和时间闭区间，MIN/MAX、MAX+1、pre-1970 第十条和精确零泄漏均有回归。

Codex 独立通过新专项/受影响 7 文件回归/后端串行全量 **34/171/905 passed**，仅 1 条既有 Starlette/httpx 弃用告警；`py_compile`、`git diff --check`、精确四文件和空暂存区均通过。验收回执=`msg_6163277b22da433a8ae672560eeec3b5`。P12F-B 交付时 P12F-C 尚未冻结或实现；随后已完成独立审计，见下一节。

P12F-B 的 Grok 四文件白名单为 `backend/app/services/editor_state_revision_history_service.py`、`backend/app/api/editor_state_revisions.py`、`backend/app/api/schemas.py`、新建 `backend/tests/test_p12f_revision_cursor_page.py`。本包未实现前端加载更多，也未提供客户端 limit/offset/page/total/hasMore；搜索、筛选、删除、命名、固定、导出、分享、跨项目历史、多人协作、历史回填和后台清理均未进入 P12F-B。

## P12F-C 前端加载更多完成交接（2026-07-17）

契约=`docs/p12f-revision-load-more-frontend-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-load-more-frontend-plan.md`。审计确认旧 `{items}` 列表没有 cursor，前端不得本地生成排序位置；因此首次展开、刷新和恢复后历史重载必须改用 P12F-B `/page`，旧封装可以保留但面板不能双请求。

三文件边界固定为 `editorStateRevisionApi.ts`、`EditorStateRevisionPanel.tsx` 和 `editor-state-revision-history.spec.ts`。API 严格解析精确 `items/nextCursor`、每页最多 10、页内 ID 唯一和 `esrc1_` 外壳；面板只手动加载、最多累计 20，失败保留原 items/cursor 可重试，不自动预取第三页。

加载更多需要独立同步单飞门和请求代次；折叠、卸载、项目切换、刷新及恢复重载必须作废迟到 catch/finally。追加项复用现有摘要、对比、正文差异、跨页 pair 和恢复链，禁止修改 workspace/hook/后端。P12F-C 不含无限滚动、搜索、筛选、删除、total/hasMore、页码、跨项目历史或多人协作。

冻结=`bb1ae3e`、实现=`fe99f5a`。Grok 原任务/首轮回执=`msg_878d37c5db1946a59b7dcc70d605a4ea`/`msg_4fde9fc2e6454d00b7ae806f58a5b198`；真实 failure-first **2 failed / 0 passed / 2 did not run**，红测前生产文件未改。Codex 两轮返修关闭空 cursor 退化、只点一次的假双击、宽泛计数、Cookie 漏检、禁止旁路未断言和任意方法 knowledge 宽放行；返修 task/review 分别为 `msg_0dff84f4f11349da87ff8695ff105a36`/`msg_021c43c667e348948dfad51d6c927298`、`msg_8bc571cf0bf544fe8206134e5ec43155`/`msg_319b7051f10f45089a18a1a77beb4d68`。

Codex 独立 P12F-C/history/技术真值/商务真值/checkpoint **4/34/28/18/51 passed**，前端全量 **297 passed（9.6m）**，lint/build/diff-check/精确三文件/空暂存区通过；验收回执=`msg_f83db79a50aa4e3d9e4aa65c9dcc9263`。自然 UI 在加载更多在途时真实禁用刷新/恢复；实现保留防御性重载代次，不用 `force:true` 伪造不可达并发。下一包不得直接沿用本包三文件白名单。

## P13-A 任务 SSE 工作空间鉴权完成交接（2026-07-17）

契约=`docs/p13a-task-sse-workspace-auth-contract.md`、计划=`docs/plans/2026-07-17-p13a-task-sse-workspace-auth-plan.md`，冻结=`e8dfa61`、实现=`1509aa2`。审计确认 required 认证中间件原本仍会拦截无会话请求，真实缺口不是“完全绕过登录”，而是 SSE 路由自行选默认/请求头 workspace，使已登录非 bid_writer、非成员头和 active workspace 语义绕过；流内快照也只按 project/task 读取。

实现以 SSE 专用私有依赖打开一个短 Session，显式复用 `get_workspace_id` 并做连接前任务归属校验，finally 在 StreamingResponse 前关闭，只向生成器传 workspace 字符串。每次快照读取再新开短 Session，按 workspace/project/task 复用 `get_task`；项目/任务消失或越界时返回 SSE error 并关闭。disabled 默认/显式头、原生 EventSource、snapshot/task/heartbeat/terminal、11 分钟超时和 GET 轮询回退均未改。

Grok 原任务/首轮回执=`msg_7b03139e43024424ab5707426d2b02bf`/`msg_ea83529fa69a42c7a91a88ac775f96d3`；真实 failure-first **8 failed / 5 passed**，红测时生产两文件 hash 与冻结 HEAD 一致。Codex 首轮审查只要求测试证据返修：删除鉴权泄漏、角色、workspace 和 task ID 的恒真 `or`，不再跳过 secret marker，并把生成器调用收紧为精确三位置参数且空 kwargs；返修 task/review=`msg_b7cb9c7720a646a0976591d5cc4d3baf`/`msg_367b8a5ef9b54e89875bc16ea3b89974`，生产实现未动。

Codex 独立专项/受影响回归/后端全量 **13/72/918 passed**，仅 1 条既有 Starlette/httpx 弃用告警。第一次全量被 Codex 外层 20 分钟时限终止且无 pytest 失败摘要；子进程退出后以 40 分钟外层干净重跑，最终 **918 passed in 1310.97s**。`py_compile`、diff-check、精确三文件与空暂存区通过；验收回执=`msg_c1023b623e3e40fea59ba798676d451d`。

P13-A 未修改 `deps.py`、中间件、前端、E2E、数据库或任务 schema；未实现 SSE 事件游标、重放、多任务总线、WebSocket、presence、前端工作空间切换 UI、URL token 或审计扩展。下一包必须重新审计、冻结并提交计划，Grok 不得自行沿用三文件白名单。

## P12F-D 修订来源筛选完成交接（2026-07-18）

契约=`docs/p12f-revision-source-filter-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-source-filter-plan.md`，冻结=`a2acdf3`、实现=`587df9a`。本包只扩展既有 `/api/projects/{projectId}/editor-state-revisions/page` 与技术标/商务标共用修订面板：缺少 `sourceKind` 返回全部来源并保持 `esrc1 {i,t}`，显式合法单一来源使用规范紧凑 `esrc2 {i,s,t}`；`esrc2` 与缺失、不匹配或非法显式来源一律固定 `cursor_invalid`，不得从游标反向采纳来源。

后端查询保持 `id/state_version/snapshot_bytes/source_kind/created_at` 五列投影，以 workspace/project/source 及 created_at/id keyset 约束后执行 `LIMIT 11`；前端提供“全部来源”与九类中文来源，筛选切换清旧页并取新第一页，刷新和恢复沿用当前筛选，折叠保留，项目切换重置。筛选分页继续遵守 20 条上限、失败保值、同步单飞及 project/filter/cursor 迟到响应隔离。本包精确六文件，未修改数据库、旧列表响应、修订详情/恢复/差异端点或写入链路。

真实 failure-first 为后端 **38 failed / 17 passed**、前端 **2 failed / 0 passed / 1 did-not-run**。Grok 原任务/首轮 review=`msg_441102447c64467f8bd27a4d0b241d94`/`msg_f1f94a200185467c88f2f07ff626e896`；第一轮返修补强固定错误、SQL/AST、筛选第二页失败重试、恢复在途禁用与 Cookie 非持久化证据，task/review=`msg_308b3e60e72b4cecaeb9853a6ee2f54f`/`msg_61426868c5454cb8b56b7a97362ef34a`；第二轮按冻结契约第 42 行修正非法显式来源的错误优先级并精确证明 keyset 与 `LIMIT 11`，task/review=`msg_025f0d26538147b58e4949d08d459bfa`/`msg_21c4ff084afc4555a992c2fc37bb3b3e`；第三轮清除残留 `assert A or B`，task/review=`msg_23a1993ce6334808b410aaf1e25faa98`/`msg_06291046a6494d508528c01378d85241`。

Codex 独立通过后端 P12F-D/游标与 C1 回归/全量 **68/48/986 passed**，前端聚焦/history/技术真值/商务真值/checkpoint/全量 **3/37/28/18/51/300 passed**；前端全量固定 `--workers=1 --retries=0`，**300 passed（7.5m）**，后端全量 **986 passed（22m37s）**。`lint`、`build`、`py_compile`、diff-check、精确六文件、空暂存区及弱断言扫描均通过；验收回执=`msg_d977b2ead50b4f8292852c9b2de95b08`。Grok 全程未暂存、提交或推送。

本包明确未实现正文全文搜索、日期范围、多来源组合筛选、自动加载、删除、命名、固定、导出、分享、跨项目历史、多人协作、数据库迁移或 SSE 扩展。下一包必须先只读审计现有生产入口与测试真值，重新编写并提交冻结契约/计划，再向 Grok 下发新的精确文件白名单；不得把本包六文件当作后续默认授权。

## P12F-E-A 修订时间范围筛选后端完成交接（2026-07-18）

契约=`docs/p12f-revision-time-range-filter-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-plan.md`。只读审计确认现有 `(workspace_id, project_id, created_at, id)` 索引、五列投影和 `created_at DESC,id DESC LIMIT 11` 可直接承载日期范围，因此 A 包不改模型、Schema、数据库、索引、响应体或前端。

新 query 仅为严格 UTC 毫秒 `createdFrom` 包含下界和 `createdBefore` 排除上界，允许单边；任一时间边界激活后使用规范 `esrc3 {b,f,i,s,t}`，把上下界、可选来源与末条位置共同绑定。V1/V2 在无时间范围时必须完全兼容；第二页缺失、增加、改变或非法范围/来源一律 cursor-invalid，禁止从游标采用条件。独立非法时间范围固定 `editor_state_revision_time_range_invalid`，项目 404 和 P12F-D 的来源优先级保持冻结。

冻结=`af3798a`、实现=`c66b69d`。Grok 只修改路由、history service 和新建后端专项测试三个文件；真实 failure-first **74 failed / 12 passed**，首轮实现专项 **86 passed**、合并回归 **116 passed**。首轮 review 后，Codex 直接复现双空、相等、倒置 V3 范围仍被接受，并指出 SQL 上界断言会被第二页 keyset 的 `< cursor` 假满足；返修仅改 service 与新测试，补齐 `f/b/t` 语义门并拆分首/次页精确 SQL 证据。

Codex 独立直接复现确认双空、相等、倒置、`t<f`、`t>=b` 均固定 cursor-invalid，`t=f` 与 `t=b-1` 正确接受；专项/受影响回归/后端全量 **87/116/1073 passed**，全量耗时 1697.87 秒，仅 1 条既有 Starlette/httpx 弃用告警。`py_compile`、diff-check、AST 弱断言扫描、精确三文件和空暂存区均通过。最终 SHA-256 为路由 `A5B6A9CE4DA528021C88E8A50E6D507B35BDE3AC26D220BA6863EDED69C789FC`、service `89F4254D11E03E5C3E5F3D4F62CA75C8AAE22FC9FCA6CDCB93C03E3C1D8FB1AA`、测试 `8AB11FF9C9230BA4827F27D00CF0DF83BC9CC92C5CB0A019842F4E2A039BE358`。

消息追溯：原任务/首轮 review=`msg_561a10fe93ac42f6b6d23fad0e897682`/`msg_233591eecb8043aa9450246bedab157f`，返修 task/review=`msg_45bd09a547014e49a8951276fb162016`/`msg_1d5bb5b639454405b87c4853f57e90fd`，Codex 验收回执=`msg_0533a4bab32448b0be8d5ec2b0ba1508`。Grok 全程未暂存、提交或推送。

前端日期控件、浏览器本地时区转换、正文搜索、来源多选、命名/固定/删除、跨项目历史、多人协作及 SSE 扩展均不在 A 包。P12F-E-B 已在下一节按独立三文件冻结边界完成交付；A 包后端三文件授权没有沿用到 B 包。

## P12F-E-B 修订时间范围筛选前端完成交接（2026-07-18）

契约=`docs/p12f-revision-time-range-filter-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-frontend-plan.md`。只读审计确认 API 封装目前只接受 `esrc1/esrc2` 和 192 长度，面板已有来源/游标同步 ref、首屏与加载更多代次、刷新/恢复/折叠/项目切换入口，history E2E 已有 37 项及完整 arrived/complete 探针；因此严格只改这三个前端文件，不改后端、CSS、hook、配置或依赖。

交互冻结为两个分钟步长 `datetime-local` 草稿和明确“应用时间/清除时间”。草稿按浏览器本地时区严格解析并字段回验，合法值才 `toISOString()` 为 P12F-E-A 精确 UTC 毫秒；允许单边，双边必须开始早于结束。无效草稿固定中文、零请求并保留当前结果；来源切换、刷新、恢复和加载更多只用已应用 UTC 条件。第二页显式重复来源/时间并原样回传 `esrc3`；折叠保留、项目切换重置，在途控件禁用及迟到 success/catch/finally 隔离均纳入红测。

冻结三文件 SHA-256：API=`E4C5590FD76A754F7589DA5E330F2CF3E4A2F35DE540BB4003869BEC7AC6F5D7`，面板=`7C925E3AA7E71B09EDAB70F674488DA08D3D2BAA5619782E1C8147B42B7E6363`，history E2E=`382C5919A13A815706707109020BF0EE0C9C18EE75CCCADE6158A89743400182`。冻结时要求 Grok 先只改 E2E 形成真实业务红测，再实现两生产文件，且不得暂存、提交或推送；所有 Playwright 均显式 `--workers=1 --retries=0` 串行。

冻结=`a31e50e`、实现=`f9127ec`。真实 failure-first **0 passed / 2 failed / 1 did not run**；Codex 首轮限定 E2E-only 返修五处宽松计数、V3 257 字符真实 parser 路径、第二页完整 query 及同项目重开后的迟到 load-more 验污，生产两文件哈希保持不变。

Codex 独立通过 P12F-E-B/history/技术 truth/商务 truth/checkpoint **3/40/28/18/51 passed**，lint/build/diff-check/精确三文件/空暂存区通过。全量首轮冻结范围外既有 Promise.all 双击竞态为 **294 passed / 1 failed / 8 did not run**；检查点独立 **51/51 passed** 后，不改代码完整复验 **303/303 passed（8.3m）**。任务/首轮回执=`msg_e3d1972aa28d442c92382f67e85003b0`/`msg_c322467045704332a69c55bf9d57ee94`，返修 task/review=`msg_aa86d5c6708c4b6fb7d0c7f7e917c5f2`/`msg_5c2808c3069d424c9714b5e7c7915255`，验收=`msg_489249aa6c264cc8a7125f07179b2d36`。

最终 SHA-256：API=`DD49CC4D53389C3760797CDA8D87536131DAF12671AEF1F642EAADFC09372375`，面板=`1F29D4FB0A9A840B954963CC51D8176DC254E6D4EBFC4C02B4C52C2D0F2546D9`，history E2E=`AB27FE3E1DEB0CD8A3BD8AAF5DDB8CDD0F6DE0D6517CEB1F28B0FDC1B45B23C7`。正文/标题搜索、来源多选、日期预设、自动加载、命名/固定/删除、跨项目历史、多人协作和后端变更均未进入 E-B；其中搜索后端/前端随后已由 P12F-F-A/B 独立交付。

## P12F-F-A 修订可见内容搜索后端完成交接（2026-07-18）

契约=`docs/p12f-revision-content-search-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-content-search-plan.md`。只读审计确认旧 page/list 的五列元数据投影不能承载正文搜索，GET query 又会把敏感关键词写进 URL/访问日志，因此 A 包新增独立 `POST .../editor-state-revisions/search`：请求体携 `query` 与可选来源/时间，成功只回既有五键元数据。

服务只扫描元数据条件下最新 20 条候选，SQL 精确六列并显式加载 `snapshot_json`；先完整校验候选窗全部 13 键快照，再以 NFKC+casefold 连续字面子串搜索严格用户可见字段。技术标只含大纲 title/description、章节 title/preview/body 和 parsedMarkdown；商务标只含资格/目录/报价/承诺的明确可见文本与 parsedMarkdown。ID、版本、来源、状态、模式、矩阵引用、facts/analysis/guidance 等均禁止匹配；响应不含关键词、片段、命中字段、分数、快照、游标或总数。

严格四文件：路由、Schema、history service、新专项测试。冻结=`b2eed7c`，实现=`e6516e8`。真实 failure-first **18 failed / 3 passed**，首个真实业务失败 405；Grok 未暂存、提交或推送。

两轮受限返修分别关闭默认 Pydantic 422 原始 input 泄漏与 11 类反假绿，以及第二轮 8 项 test-only 假绿。任务链：原 task/review=`msg_ab2e31c47bec41cea1800673d62dd866`/`msg_ca71c93c8daf4297901972b7f17b21a6`；首轮返修=`msg_5288187034e54751a8663e1262d6f284`/`msg_82c572a14d2544c88161bbcc58c84e05`；第二轮返修=`msg_c32879f80cc5474f8ef0ae91413a7bd9`/`msg_2188e539e693431cb29b0211afd48e08`；验收=`msg_554d0035e24d437086f3a1d14bbef1ad`。

Codex 独立串行通过专项/受影响回归/后端全量 **23/203/1096 passed**，全量 1658.59 秒；仅 1 条既有 Starlette/httpx 弃用告警。编译、diff-check、AST/弱断言、精确四文件和空暂存区通过。最终哈希：路由=`E56B0BF69A1DD425DFBF3FCD68F210E2664A9D693571E11467C462F10DDFDC08`，Schema=`474680ECEC41BEACACE624A6F154B5951167C1EEC23AEF4D48AAC708CD277221`，service=`8EACFAD08E213B14F8FF3FC5A3DBE93F3F9A17D02BCA282FF79BF8D51C350B2C`，测试=`584441E80D4C22DF4D616DB94E2D70CBBBF849260B5A314666F8C891F1B3995B`。

本包未改前端、旧 GET、游标/分页、数据库/索引/迁移或依赖。P12F-F-B 前端入口随后已由下一节独立完成；命中高亮/片段、自动搜索、缓存、跨项目搜索、来源多选、日期预设、命名/固定/删除、多人协作和 SSE 仍未实现。

## P12F-F-B 修订可见内容搜索前端完成交接（2026-07-18）

契约=`docs/p12f-revision-content-search-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-content-search-frontend-plan.md`。只读审计确认既有 API 已有严格五键/来源/UTC parser，面板已有筛选、刷新、恢复重载与迟到隔离，history E2E 已有双工作区 page 探针；因此只改 API 封装、共用面板和既有 history E2E，不改后端、CSS、hook、配置、依赖或其它测试。

交互冻结为搜索草稿与已应用关键词分离：输入零请求，明确“搜索”或 Enter 才校验并 POST；无静默 trim，NFKC 后 1..64 码点、无首尾空白/C0/C1。搜索与来源/已应用时间组合，成功展示服务端最多 20 条并隐藏加载更多；空态/失败固定中文。刷新、恢复、来源/时间变化和折叠重开保留已应用搜索，项目切换重置；page/search success/catch/finally 必须同时核对 query/source/from/before 和 session。

关键词只可存在于当前输入值、React 内存、调用栈和一次 POST body，不得进入 URL、GET query、固定文案、console、local/session/Cookie、剪贴板或其它请求。实现前冻结三文件 SHA-256：API=`DD49CC4D53389C3760797CDA8D87536131DAF12671AEF1F642EAADFC09372375`，面板=`1F29D4FB0A9A840B954963CC51D8176DC254E6D4EBFC4C02B4C52C2D0F2546D9`，history E2E=`AB27FE3E1DEB0CD8A3BD8AAF5DDB8CDD0F6DE0D6517CEB1F28B0FDC1B45B23C7`。

冻结=`4585388`、实现=`be2fe77`。Grok 先得到真实 **3 failed / 0 passed / 0 did-not-run**，再完成两生产文件；初始 task/review=`msg_3fb9225e60824153ac8b76d6d2c118de`/`msg_c69d1b022cea4d778db1edeee5da5546`。Codex 首轮发现严格坏响应、DEL/C1/astral 码点边界和旧 `catch/finally` 与新 loading 重叠三类假绿，受限 E2E-only 返修 task/review=`msg_76277425992e4369a1476bdcbe9829c1`/`msg_6722f22970184a0981eb07d6d2997951`，验收=`msg_14c421e3a1c2498985c41ed026e84fdf`。

返修真实覆盖顶层 extra、元数据缺键/extra、重复 ID、21 项超限，DEL/C1、64/65 个 astral 码点边界，以及 A 搜索 parser `catch` 与 B page loading 的真实重叠。Codex 独立串行通过聚焦/history/技术 truth/商务 truth/checkpoint/后端专项/前端全量 **3/43/28/18/51/23/306 passed**，lint/build/diff-check/精确三文件/空暂存区/禁区扫描通过。

最终 SHA-256：API=`4EB053C284A6F4059D559842B3A6C5C0AF829BDF08E26A8528E0760B0B02D433`，面板=`524D5AC6D494736492E4A18385DEE74C7F7547129888E322808548A17F8F81FF`，history E2E=`D7BFAE7EDD61747DE790FDC188E9C61959E93529AA1093F514E1B6BBCC7D63BB`。自动搜索/防抖、片段/高亮/分数、搜索历史/缓存、搜索游标/跨项目搜索、来源多选/日期预设、命名/固定/删除、导出/分享、多人协作和 SSE 扩展仍未实现，后续必须另包冻结。

## P12F-G-A 单条修订删除后端完成交接（2026-07-18）

契约=`docs/p12f-revision-delete-backend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-delete-backend-plan.md`，冻结=`c176cb5`、实现=`d2555d4`。单条自动修订没有被其它业务表外键引用，当前 editor-state、手动/安全检查点和后续 transition 均不依赖修订行持续存在，因此实现无需新表、迁移、软删除或配额重算。

冻结入口为无 query/body 的 `DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}`：成功严格空 204/no-store；query/body 固定脱敏 422；项目/跨空间与修订/跨项目固定脱敏 404；数据库故障 rollback 后固定 500。required 继续只允许当前空间 bid_writer 并校验 DELETE CSRF，disabled 保持本机兼容。

最终五文件：修订路由、实体注释、新删除服务、新专项测试，以及仅同步 `test_no_write_routes_on_revision_history` 单一函数的旧 history 守卫。原四文件冻结遗漏了旧守卫对详情 DELETE 的 404/405 要求；Codex 在回归阶段识别后受限增补，不视为一般扩权。服务只投影 `Project.id`，再以 workspace/project/revision 三谓词删除恰好一行并唯一 commit；禁止读取 snapshot/current editor-state/checkpoint，禁止范围删除、补写修订或 commit 后 refresh。

真实 failure-first **10 failed / 3 passed / 0 did-not-run**，首个业务失败 405。首轮并行 restore/auth 结果因共享 SQLite 污染废弃；Codex 受限返修关闭 `rowcount=None` 错映射、实现缺失分支、宽状态、SQL 至少一次、任务空占位、commit/query 恒零计数及 search 子集等假绿。消息追溯：原 task/review=`msg_3eb102c1f38c4c2f8cdec28ccc1b704f`/`msg_cf1b447acfc54ee7a6f6b4d89572082b`，返修 task=`msg_8e2920c76fe54da482a2c27dffa90204`，最终 review=`msg_03d59080b90744459e70d9ae35847f94`。

Grok 与 Codex 分别串行通过专项/history+cursor+search/restore+retention/auth/全量 **14/71/93/39/1110 passed**；Codex 独立全量耗时 1620.30 秒，仅 1 条既有弃用告警。最终哈希：路由=`71E61A18822A4E79BAEEA7A7CB93F0A7612DD02D9F29CC997C484786687EF76D`，实体=`2C19028EBF3292CDE069E5D034E880593D1313185643E0AA827109A8ED96BCDE`，服务=`B4618F603635FCB548DCBD1A9BE87BC071FD45C3A6302F74A4942C61D7E401CC`，专项=`C04D054751BEDF10614138CA1F8CCFE7F160CEDD6C0F4B3C6E9438BEC5044668`，旧守卫=`E71154970CC83212A193D3B5C313AA3C7A9215C7C623B22A4C284E3F2C1A00FE`。

P12F-G-B 前端确认/重载随后已完成，见下一节；多选/批量/软删除/回收站、命名/固定/标签、检查点删除、审计报表、跨项目历史、多人协作和 SSE/WebSocket 仍未授权。

## P12F-G-B 单条修订删除前端完成交接（2026-07-18）

契约=`docs/p12f-revision-delete-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-delete-frontend-plan.md`；冻结=`89b5728`、实现=`bb7c4f4`。严格只修改 `editorStateRevisionApi.ts`、技术/商务共用 `EditorStateRevisionPanel.tsx`、共用 `editor-state-revision-history.spec.ts`；后端、共享 `apiFetch`、两个 workspace hook、数据库、配置和依赖均未修改。

用户链路固定为“删除 → 内联固定文案二次确认 → 精确一次无 query/body DELETE”。取消与确认前均零请求；成功显示固定中文并按已应用状态重载第一批，普通态 GET page、搜索态 POST search；失败不重载并保留原列表。确认/执行期间其它意图和控件真实互斥，project/session/delete generation 同时隔离旧 success/catch/finally。

真实 failure-first 为 **3 failed / 0 passed / 0 did-not-run**，首个业务失败是列表完成加载后缺少删除按钮，两个生产文件保持冻结哈希。首轮实现因旧闭包项目比较、成功后重载失败覆盖、宽松 body/search/列表保值证据被拒绝；第一轮返修后又因测试块残留 OR、可选首项、`Math.min` 和缺少完整四条件搜索失败而被拒绝；第二轮仅改 E2E 并关闭上述缺口。

原任务/首轮审查=`msg_c2434ab3c5cf4f71bc1041f534fb9c00`/`msg_09b30848f82e41a081ef81774b4553a8`；第一轮返修/审查=`msg_c8016f559da6416798db554fce27d974`/`msg_da019ed6142941829215f27e8e4e3d31`；第二轮返修/最终审查=`msg_5ed7a812631a4c22a520c88bf9002356`/`msg_ac681239aff641ab9cac8265bf0fb2bc`；Codex 验收回执=`msg_f51d6e2c60bf450eb2c3a7e3a24d3551`。Codex 独立串行通过 **4/47/51/28/18/310 passed**，lint/build/diff-check/精确三文件/空暂存区/静态禁区门均通过。最终哈希：API=`260589B9D02F8B88E3A8FDF8A19CA9BB7C03B3645D9072A612A5E7B55AF6DDAD`，面板=`DDAF690B6A310171144168ACAD113BF335EAB070D5918F2CA14173497EB1CE37`，history E2E=`6797E9BBA85FEBDD2F603709556DCB85F78CF44C03B638245C2AD28CA6CB60DD`。Playwright 仍必须显式 `--workers=1 --retries=0` 串行。

## P12F-H 单条修订命名冻结交接（2026-07-18）

契约=`docs/p12f-revision-display-name-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-display-name-plan.md`；当前文档提交即冻结。只读审计在固定/置顶、搜索片段/高亮、跨项目历史与多人协作候选中选择最小的单条展示名称：nullable 名称列、独立 PATCH 写服务、list/page/search/detail 六键元数据，以及技术/商务共用面板原位保存/覆盖/清除。

名称为 NFKC 后 1..40 码点、首尾无空白且无控制/双向字符；null 清除。PATCH 只写 workspace/project/revision 三谓词命中的 `display_name`，不加载快照，不生成修订，不改 editor-state/检查点/来源/时间/游标。成功只原位更新当前列表，失败保值；mounted/project/session/name generation/revision 围栏隔离迟到。

初始严格十文件；failure-first 后确认六键元数据会使 history/page/source/time/search/delete 六份既有精确合同测试必然红，因此当前范围修订将它们以机械更新边界加入，最终严格十六文件。八个生产文件、两个新文件和六个既有测试的冻结哈希见契约第 8 节。Grok 已形成真实 ASGI/UI 红测 **30 failed / 0 passed**、**3 failed / 1 passed / 0 did-not-run**；固定/置顶和裁剪保护明确留后，名称不会保护修订免于 P12F-A 裁剪。

## P12F-J-A 修订固定与裁剪保护后端基础（已完成，2026-07-19）

契约=`docs/p12f-revision-pinning-backend-contract.md`、计划=`docs/plans/2026-07-19-p12f-revision-pinning-backend-plan.md`，冻结提交=`2f03b8c`，实现提交=`a7021c4`。Grok 任务=`msg_3ab978a1cecd42e39464c449f585eea2`、严格返修任务=`msg_db5e44f95529408dbbe737d35ff468ed`/续跑=`msg_90fe962903c845cba1da185e0909ae08`，最终 review_request=`msg_88f4752ef1cf4a929c6b194df00d9398`；Grok 未暂存、提交或推送，Codex ack=`msg_c630805296ac48d6941809bbca957b7f`。选择理由是固定/置顶会改变 P12F-A 的连续最新前缀，必须单独先冻结配额和事务边界，不能借 P12F-I 顺手扩围。

本包最终严格九文件：新增 `is_pinned` 非空布尔列与 SQLite 幂等迁移；固定上限 5 条/10 MiB；项目级锁后单条 PATCH `/editor-state-revisions/{revisionId}/pin`；自动裁剪先完整校验，再保留全部固定行和最新非固定前缀。显式单条 DELETE 仍允许删除固定行；list/page/search/detail 继续精确六键，前端与 J-B 的 `isPinned` 元数据/UI 不进入本包。为修复 SQLite Boolean 吞掉非法元数据的缺陷，trim/pin 投影改用 `type_coerce(..., Integer)` 返回原始值，pin 移除 `is_(True)` 过滤并完整校验同项目候选。

真实 failure-first 为 **15 failed / 71 passed**，随后严格坏值 failure-first 为裁剪 **1 failed** 与 pin **1 failed**；首个有效失败均为真实业务逻辑。Grok 最终串行 **16/96/1/1165 passed**，Codex 独立串行同样通过 **16/96/1/1165 passed**；py_compile、diff-check、精确九文件、空暂存区、迁移 DROP 前回滚、execute/flush/commit 回滚和原始坏值零写证据全部通过。实现已由 Codex 以中文提交并推送。P12F-J-B 才能扩展七键响应、前端 API/parser、技术/商务固定入口与 E2E。

## P12F-J-B 修订固定状态七键响应与前端入口（已完成，2026-07-19）

契约=`docs/p12f-revision-pinning-frontend-contract.md`、计划=`docs/plans/2026-07-19-p12f-revision-pinning-frontend-plan.md`，冻结=`f019a4b`，实现=`5ef7abd`，Codex ack=`msg_8399a348aa1543e2b4b61cbdd25b4ac9`。严格十四文件仅包括后端 Schema/路由/history service、前端 API/共用面板/history E2E 和八份精确响应合同测试；ORM、迁移、pin service、裁剪、共享请求层、页面/hook、CSS、依赖和配置均未改动。

后端 list/page/search 统一精确七键，detail 精确八键；四类 SQL 均以 `type_coerce(EditorStateRevisionRow.is_pinned, Integer).label("is_pinned")` 保留 SQLite 原始值，共用校验只接受原生 int 0/1 并输出 bool。page 第 11 条 lookahead、search 未命中候选和 detail 坏值都会使整次读取固定脱敏失败且零写；list/page 仍不读正文，10/10+1/20 候选、倒序、来源/时间、V1/V2/V3 游标、名称或内容联合匹配均不变。

前端 `META_KEYS`/`DETAIL_KEYS` 升为七/八键，pin API 只发一次无 query 的 PATCH，body/响应均精确 `{isPinned:boolean}` 且响应必须等于目标。技术/商务复用同一面板：显示“已固定”，提供固定/取消固定；同步 ref 在 await 前关门，全局单飞且所有操作真实 disabled；成功只原位更新目标，失败保值、零重载/重试；mounted/session/generation/project/revision 五重围栏阻止旧 A success/catch/finally 污染或解锁 B。

Grok 首轮错误地并发运行后端和 Playwright，该轮结果全部作废；后续额度/进程中断，未发送最终 `review_request`。Codex 对保留实现做受限审查，修正刷新未纳入 pin 锁、Hooks 依赖和四处 E2E 确定性问题后，独立串行通过后端专项/全量 **297/1170 passed**，P12F-J-B/history/checkpoint restore/技术 truth/商务 truth **6/61/51/28/18 passed**，lint/build/py_compile/diff-check/十四文件/空暂存/SQL 与泄漏静态门均通过。整仓前端全量沿用上一包 **318 passed** 基线，按用户要求未重复扫描不受影响套件。

核心最终 SHA-256：路由=`AA6B2E82AD47126C4CEBFFF5351B3C394B81DCBF3D49D24B20D782CE9981F147`，Schema=`65A5E879E0201E9FAF22F16A5B2914219BDE3FF386C8106FB4BADA338CBD5BE5`，history service=`5C126E7B18D081231AABF9C4C7A04672DE5472F2632C015667A70F08C101B438`，前端 API=`AB194540B8E0EE564218C9E3820BDBEDEF43E97D477650F806C2F09EE686B279`，面板=`283386B7EAE16DF9643C95D0C8CD255FA80FCD47EED189746DE0C62CD95A104F`，history E2E=`6FCB317644AEC24C38532CAD4338BCD4B7DA4AD0A2AB9CDE332D70744FED3A50`。固定排序/分组、批量固定、数量/容量展示、乐观更新、自动重试、跨项目历史和多人协作仍未实现；检查点命名已选为 P12G 并单独冻结。

## P12G 手动检查点展示名称（已完成并推送，2026-07-19）

契约=`docs/p12g-checkpoint-display-name-contract.md`，计划=`docs/plans/2026-07-19-p12g-checkpoint-display-name-plan.md`，冻结=`9696ec1`，实现=`077e7d4`。严格十二文件包括既有十文件与新增检查点名称服务/后端专项测试；最终 SHA-256 见契约第 11 节。

生产目标：检查点表 nullable `display_name` 与 SQLite 幂等迁移；精确 `PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name`；create/list/detail 七/七/八键；技术/商务共用面板保存、覆盖、清除并成功原位更新。创建请求仍精确 `{}`，手动与安全检查点初始名称均为 null；名称不进入快照、恢复、排序、20 条裁剪或自动修订。

Grok 初始后端 failure-first **37 failed / 25 passed**，首个真实业务失败为 PATCH 404。首轮 review=`msg_1b3e0ffcfc164586a641c4c70669f058`；Codex 审查发现 `.get()` 掩盖缺键、伪同步单飞和 A→B 测试没有真正重叠，四文件返修 task/review=`msg_ef6e51ac93f849a9bf58d4699519da48`/`msg_f472fcf56377451a8c92c5dbc7b69031`。返修先得到后端 **1 failed**、前端 **1 failed / 4 passed / 3 did-not-run**，最终以精确索引、flight token、render 同步 ref、同任务双 DOM click 和 A/B 双 hold 关闭；Grok 未暂存、提交或推送。

Codex 独立串行通过后端聚焦/恢复回归/全量 **62/47/1203 passed**，前端 P12G/checkpoint/history/技术 truth/商务 truth **8/59/61/28/18 passed**；lint、build、py_compile、diff-check、严格十二文件、空暂存区和最终哈希均通过，ack=`msg_cd2908a39cc1438186b0f41d13062443`。整仓前端沿用已验收 **318 passed** 基线，因所有受影响套件已完整通过而未重复扫描不受影响套件。

未实现：创建时命名、自动名称、检查点名称搜索/排序、固定/删除/下载/分享、批量/标签/备注、跨项目检查点、完整时间线、多人协作、审计扩展和 SSE/WebSocket。下一包必须重新审计与冻结。

## P12H 单条检查点删除（已完成并推送，2026-07-19）

契约=`docs/p12h-checkpoint-delete-contract.md`，计划=`docs/plans/2026-07-19-p12h-checkpoint-delete-plan.md`；冻结=`b81546e`、实现=`1ff8839`。严格七文件为后端检查点路由、既有 405 守卫、新删除服务、新专项测试，以及前端 checkpoint API、共用面板和既有 checkpoint E2E；最终哈希见契约第 9 节。

选择依据：检查点行无其它业务表外键引用，单条删除不需要模型、迁移、Schema 或核心恢复链改动；固定会改变最近 20 条裁剪，跨项目历史/多人协作需要更大权限与数据边界。删除成功空 204/no-store，query 与任意非空 body 固定脱敏 422；服务只投影 Project.id 并以 workspace/project/checkpoint 三谓词删除恰好一行，rowcount/事务故障全 rollback。

前端技术标/商务标共用同一删除入口：固定文案内联确认，确认前/取消零请求，独立 flight token 在 await 前真单飞；成功只原位移除且零列表/editor-state 重载，失败保留列表和确认可重试。delete 不依赖 editor-state expected version，但与 toggle/list/create/restore/name/其它 delete 真实互斥；A/B 双 hold 证明旧 success/catch/finally 不污染或解锁 B。

首轮 Grok 在正式回执前因 402 中断，因此 failure-first 数量无可复核记录且未补造。Codex 初审真实得到后端 **43 passed**、前端 **8 passed / 1 failed**，并发现空体弱 OR、假 disabled、泄漏门和恢复确认可被删除抢占；两文件返修后 Grok review=`msg_92c05eeb3bba4dd1801470646e74084d`。Codex 独立串行通过后端 **43/80/1217 passed**，前端 **9/68/61/28/18 passed**，lint/build/py_compile/diff/七文件哈希门通过，ack=`msg_c7168985bed9415ab1fc44420474d857`。整仓前端沿用 **318 passed** 基线，未冒充重跑。

## P12I 检查点名称与可见内容显式搜索（已完成并推送，2026-07-19）

契约=`docs/p12i-checkpoint-search-contract.md`，计划=`docs/plans/2026-07-19-p12i-checkpoint-search-plan.md`；冻结=`86cc1a3`、实现=`8c41bbc`。严格六文件为 checkpoint service/路由、新后端专项，以及前端 checkpoint API、共用面板和既有 checkpoint E2E；最终哈希见契约第 9 节。

选择依据：固定与保护裁剪需要新增列、SQLite 幂等迁移并改变创建/恢复事务；跨项目版本和多人协作会扩大权限、身份与前端会话边界。P12I 只在当前项目既有最多 20 条检查点内显式搜索，不改模型、Schema、迁移、索引、裁剪或七键响应，是可单独验收的最小高价值包。

后端唯一新增 POST search：精确 `{query}`，无 query 参数；一次八列投影、workspace/project、倒序、LIMIT 20，先完整重验全部候选的名称与规范快照，再按 NFKC+casefold 匹配名称或用户可见内容。前端技术标/商务标共用入口，输入零请求、同值零重发、清除一次 GET、active search 下刷新/创建/恢复重发同一 POST；命名/删除继续原位，所有意图真实互斥、同步单飞和 A→B 迟到隔离。

Grok 首轮 review=`msg_58a1a28887534e02bd4497bb12dec3da`。Codex 审查发现失败同词无法重试、active refresh 双飞，以及后端权限/投影/坏行/预算和前端迟到/泄漏证据不足；受限返修任务/review=`msg_69b8bb73702945b3a4f0b3ebd26c942a`/`msg_2a430c560a4d415d881a4fd58911ad9d`，两项返修红测先为 **2 failed**，修后为 **2 passed**。Grok 未暂存、提交或推送。

Codex 独立串行通过后端 **18/123/1235 passed**、前端 **8/76/61/28/18 passed**，lint/build/py_compile/diff/六文件/哈希门通过；验收回执=`msg_608e5dda4d59453b83ab068ce9879fbf`。整仓前端沿用 **318 passed** 基线，未冒充重跑。固定、分页、跨项目、完整时间线、多人协作、presence、SSE/WebSocket 仍未进入本包。

## P12J-A 检查点固定与保护裁剪后端基础（已完成并推送，2026-07-19）

契约=`docs/p12j-checkpoint-pinning-backend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-backend-plan.md`。选择依据：检查点固定会同时改变 SQLite 表结构、手动创建裁剪和恢复前安全检查点保护，必须先于响应/UI 单独冻结；排序/分页、跨项目历史与多人协作需要更大的读取、权限与会话边界，不进入本包。

严格九文件：模型、数据库迁移、checkpoint 核心服务、新 pin 服务、Schema、checkpoint 路由、既有 checkpoint 基线测试、新 P12J 专项、P12H 精确字段清单。冻结=`9f304da`、实现=`8edebd4`；最终 SHA-256 见契约第 10 节。Grok 测试先行且全程未暂存、提交或推送。

核心合同：`is_pinned BOOLEAN NOT NULL DEFAULT 0` + SQLite 0/1 CHECK；每项目最多固定 5 条/10 MiB；PATCH 精确 `{isPinned:boolean}`、≤1024 字节、no-store；项目锁后只投影 `id/snapshot_bytes/is_pinned` 原始整数。裁剪先完整校验，再保留全部固定行、本轮恢复前安全 `protect_id` 和最新普通行至总数 20；显式 P12H DELETE 仍可删除固定行。

P12J-A 当时保持 create/list/search 七键、detail 八键、前端和检查点排序不变；`isPinned` 元数据与技术/商务固定入口后续已由 P12J-B 交付。固定排序/分组、批量、分页/游标、跨项目检查点、完整时间线、多人协作、presence、SSE/WebSocket 继续未实现。

Grok 初始真实 failure-first **16 failed / 3 passed**；首轮专项/受影响回归/后端全量 **19/140/1254 passed**。Codex 审查发现不完整迁移误判最终态、空候选携带保护 ID 静默返回，以及真实 5 固定+15 普通边界/反假绿缺口；返修 task/review=`msg_f9bc9783042748b9bad6125c529081c1`/`msg_3a93a06c7c9b4343813b7069273afd30`，先得到 **2 failed / 0 passed**，修后 **23/140 passed**。

Codex 最终独立串行通过专项/受影响回归/后端全量 **23/140/1258 passed**，全量耗时 **1454.53 秒**，仅 1 条既有 Starlette/httpx 弃用告警；py_compile、diff-check、精确九文件、空暂存区、最终哈希和安全静态门通过，验收确认=`msg_6e53fde20dd14ddd94a0ca03192531c6`。本后端包没有运行或修改 Playwright，前端沿用 **318 passed** 基线；后续 P12J-B 已独立交付，见下一节。

## P12J-B 检查点固定状态八/九键响应与前端入口（已完成并推送，2026-07-19）

契约=`docs/p12j-checkpoint-pinning-frontend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-frontend-plan.md`，代码哈希基线=`262683e`、冻结=`65fe259`、口径澄清=`1471c31`、实现=`7d1d5c9`。最终严格十一文件：Schema、checkpoint 路由/核心服务、共用前端 API/面板/checkpoint E2E，以及五个既有后端测试；最终哈希见契约第 9 节。

后端仅将 create/list/search 七键和 detail 八键扩为含 `isPinned` 的八/九键；list/detail/search 三处用原始 Integer 投影拒绝 SQLite 非法 `2`，create/safety 初始 false。前端仅增加严格八键 parser、精确一键 pin API、固定 badge/按钮、全局同步单飞、全部检查点操作互斥、active search 原位更新与 A→B 五重迟到围栏。

表、迁移、P12J-A pin service/5 条/10 MiB/保护裁剪、名称/删除/恢复/搜索语义、技术/商务页面与 hook、共享请求层均未改。Grok 初始任务/review=`msg_b78f8a9474cd470bbd1507aa141ba6c4`/`msg_b86ca88d69b74be89c556aa83d8fa7ed`，真实 failure-first **6 failed**；Codex 以受限 E2E 返修任务=`msg_0912b706fd844359a335f046eae1f1fc` 补强另一行同拍、旧 A catch/finally 和 active search 多结果顺序证据，验收确认=`msg_98239bfc61c743d1b7b44d7fec15a975`。Grok 未暂存、提交或推送。

Codex 独立串行通过后端 **120/1261 passed**、前端 **6/82/61/28/18 passed**，lint/build/py_compile/diff/严格十一文件/空暂存区/最终哈希通过。Grok 返修自测曾出现一次既有 history 双击元素 detached（**1 failed / 44 passed / 16 did not run**），未改代码重跑及 Codex 独立首轮均 **61 passed**，保留为非阻断稳定性风险。整仓前端 318 基线未重复运行或冒充；固定排序/分组、批量、容量展示、分页/游标、跨项目版本、完整时间线和多人协作仍未实现。

## P12K 检查点固定优先默认列表（已完成并推送，2026-07-20）

契约=`docs/p12k-checkpoint-pinned-first-list-contract.md`，计划=`docs/plans/2026-07-19-p12k-checkpoint-pinned-first-list-plan.md`，代码审计基线=`90cfd58`、契约冻结=`fe0fa08`、启动口径修订=`ff48495`/`6666af6`、实现=`3c3cbf9`。严格两文件：`backend/app/services/editor_state_checkpoint_service.py` 与新建 `backend/tests/test_p12k_checkpoint_pinned_first_list.py`。

唯一生产变化是默认 list ORDER BY 增加原始固定列倒序，形成固定组优先、组内 `created_at DESC,id DESC`。search 继续最新 20 条纯时间/ID 倒序，旧固定第 21 条不得挤入候选；P12J-B 当前列表固定后只原位更新，下一次默认 GET 才重排。表/迁移/API/Schema/pin service/配额/裁剪、所有写路径、前端与 E2E 均冻结。

Grok 初始 task/review=`msg_24d08a0202954060b4c4ab3b0a35942d`/`msg_131b165976c64b2fb05ceb0792122a5c`。真实 failure-first **8 failed / 4 passed**，首个业务失败是旧固定项仍排在新普通项之后；另一个隔离用例最初因测试夹具 `Workspace` 构造 `TypeError` 失败，生产修改前已先修正，未冒充业务红测。首轮实现通过专项/受影响集/后端全量 **12/132/1273 passed**，全量耗时 **1674.75 秒**。

Codex 审查后下发 test-only 返修 task/review=`msg_b1b3d1fb809c4a579ed35dfd9a875615`/`msg_4e2f742d8ac2469fad123e367922f6fa`，补齐 PATCH 不触发默认列表投影以及 list/search ORDER BY 精确序列证据；生产哈希保持不变。Codex 独立串行通过六文件受影响集 **132 passed in 106.74s**，并通过 py_compile、diff-check、严格两文件、空暂存区、SQL/AST 和最终哈希门；遵循分级验收策略，没有重复 Grok 已跑的 1273 条全量。验收确认=`msg_3048a39db0c04969978a7e2dd7ea0c60`。

最终 SHA-256：生产服务=`8C08B546E0DB8FA00FE4D6E15FB93A23650F15FA12C42E23EC100ED6EA7E371E`，专项测试=`49A6FEA0F2C08FF44E9E7CC57FC216A967B03EFCF6DA6ED78624DDC573821591`。Grok 未暂存、提交或推送；前端未修改、未运行 Playwright，沿用 checkpoint **82** 与整仓 **318 passed** 基线。固定分组标题、批量固定、容量展示、分页/游标、跨项目检查点、完整时间线和多人协作仍未实现；下一包尚未冻结。

## P12L 检查点固定名额提示前端（已完成并推送，2026-07-20）

契约=`docs/p12l-checkpoint-pinned-count-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12l-checkpoint-pinned-count-frontend-plan.md`，代码哈希基线=`5258f84`、契约冻结=`4526832`、启动口径=`d21cfb5`、实现=`cc6bf11`。严格两文件最终 SHA-256：面板=`890621124EB953F8A81BF4E5975E75B76F03A6296089FF682C5DE94A5FF187AE`，E2E=`C8961E30831869659FBC37CD806F95D4ACFA608097CEC2C52DFFD4E6DC72055A`。

唯一用户变化是技术标/商务标默认检查点列表在加载完成后显示 `已固定 X 条（最多 5 条）`。数量只从严格解析后的 `items` 以 `isPinned === true` 纯派生；pin/unpin/delete/默认重载随现有状态即时重算，零新增请求。active search 必须隐藏，避免把搜索子集冒充项目总数；5/5 时仍让服务端处理第 6 条 PATCH，不新增本地权限或配额校验。

Grok 第一阶段只改 E2E 得到真实 **4 failed / 1 passed**，面板哈希保持冻结；第二阶段仅改面板。最终 Grok 聚焦/受影响 checkpoint **5/87 passed**，lint/build 通过；Codex 独立聚焦 **5 passed in 16.0s**、lint 和静态门通过，验收=`msg_a685c7123a4f4c9fac68481b99a25cec`。Grok 未暂存、提交或推送，Codex 实现提交=`cc6bf11`。字节容量、分组/重排、搜索固定优先、API/后端、分页、跨项目检查点、完整时间线与多人协作全部未实现。

## P12F-I 修订名称与可见内容联合搜索完成交接（2026-07-19）

契约=`docs/p12f-revision-display-name-search-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-display-name-search-plan.md`；冻结=`060191e`，实现=`008e443`，Codex 验收回执=`msg_d954063f489248babb027b9bb335f666`。Grok 最终 review_request=`msg_82cd1e26df03413389a92604830cdb9c`，未暂存、提交或推送。严格四文件为 history search service、既有内容搜索专项、共用修订面板和 history E2E。

后端固定只扫描最新 20 条候选，先完整校验六键元数据与规范 13 键快照，再以同一 NFKC+casefold 连续字面规则联合匹配非空展示名称和既有可见字段；任一命中即返回，同一修订只返回一次并保持倒序。SQL 七列、`LIMIT 20`、第 21 条不补扫、来源/时间/空间过滤、固定错误和五域零写均未变化；名称命中不能短路坏快照、坏元数据或预算超限。

技术标/商务标共用“名称或内容搜索”入口、活动态、失败和空态固定中文；搜索仍精确一次既有 POST，搜索态无 page/游标，刷新/清除/来源时间组合/删除重载/迟到隔离复用既有语义。名称只作 React 文本；关键词/名称不得进入 URL、存储、Cookie、console、错误或外网。固定/置顶、裁剪保护、片段/高亮/评分/游标/缓存、自动搜索、跨项目历史、检查点命名和多人协作不在本包。

真实 failure-first：后端 **5 failed / 1 passed**、前端 **2 failed / 1 passed**。Codex 独立串行通过后端专项/兼容/全量 **29/247/1146 passed**，前端聚焦/history/checkpoint/技术 truth/商务 truth/全量 **3/55/51/28/18/318 passed**，lint/build/py_compile/diff-check/精确四文件/空暂存区/最终哈希/静态门均通过；仅保留既有 pytest 弃用告警和 build 大 chunk 提示。
