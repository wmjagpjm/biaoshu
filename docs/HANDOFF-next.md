# 新会话交接：biaoshu（当前有效）

> **交接日期**：2026-07-15（P12C-B-A 浏览器 PUT 修订账本原子接入已完成并推送）
> **仓库本地**：`C:\Users\Administrator\biaoshu`
> **GitHub**：https://github.com/wmjagpjm/biaoshu
> **当前工作分支**：`collab/grok-code-codex-review`（协作分支；**勿直接当 main**）
> **协作分支已推送功能基线**：P12C-B-A 冻结=`fbf93c0`、实现=`acf3139`；P12C-A 冻结=`daa8c43`、实现=`226e1c1`；P12B-D 冻结=`613818f`、D1 后端=`551caba`、D2 前端=`0f81dd6`；P12B-C 冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`；P12B-B 契约/计划=`0636302`、实现=`473e823`；P12B-A 计划/契约=`0b55c30`、实现=`780cc82`；P12A 计划/契约=`bf8ccd6`、后端=`9f53d92`；P8E 计划/契约=`73b1264`、后端=`79b346e`、助手=`e3f9cc4`；P8D 计划=`30d066f`、助手=`e1fe316`；P11C 计划/契约=`24b7ba8`、安全细化=`c5b3eec`、前端=`1441509`；P11B 计划=`6a3f4fe`、前端=`a99d8d4`；P11A 计划=`70a2dc7`、前端=`b0a86e4`；M3-D 计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`；P10K 计划=`2e53007`、后端=`1eaa75e`、前端=`dbf301c`；P8C 计划=`cabe99d`、后端=`af39ff8`、前端=`1cf5576`；P10J 计划=`701c946`、后端=`4e662d6`、前端=`fce6cb6`；P9D 计划=`4925a51`、实现=`e5adad7`；M3-C 计划=`c63310f`、实现=`b8ff605`；P10I 计划=`ddc1807`、后端=`d5201e9`、前端=`49daa16`；P10H 计划=`7694843`、后端=`6c76d80`、前端=`4eb8a14`；P8B 计划=`f662674`、后端=`0994cc8`、前端=`80d2579`；P10F 计划=`12e067f`、后端=`3dc600a`、前端=`254f8c7`；P10E 计划=`26f7e40`、后端=`1b6ccf3`、前端=`37cf835`；P10G 计划=`26b43ea`、后端=`c3cf8b4`、前端=`d5656cc`。新会话必须以 `git rev-parse HEAD` 与远端分支一致为准。
> **参考 `origin/main`**：`4847a9d` — docs: 重写换会话交接并强制注释规范专章（非当前工作 HEAD）
> **本地状态**：P12C-B-A 实现=`acf3139` 已推送，本文档闭环提交位于其后。新会话仍须重新核对本地 HEAD、远端 SHA 与工作区。
> **验收基线**：P12C-B-A 专项/受影响回归 **14/107 passed**，后端串行全量 **680 passed**（1 条既有 Starlette/httpx 弃用警告）；P12B-D2 专项/受影响回归 **51/63 passed**，单 worker、零重试前端全量 E2E **263 passed**；`frontend npm run lint` 零问题，`build` 通过（仅既有大包体积提示），生产与测试文件 `py_compile`、白名单与 diff 检查通过。P12C-A 历史基线为 67/77/666，P12B-D1 历史基线为 58/81/599，P12B-C 历史后端/前端全量为 570/212，Docling 46、MinerU 54 继续保留。**所有 Playwright E2E 共用 SQLite 重置库，必须逐条串行运行，禁止并行。**

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu 标书制作者剩余主线任务。仓库 C:\Users\Administrator\biaoshu，GitHub https://github.com/wmjagpjm/biaoshu.git。
工作分支只能是 collab/grok-code-codex-review，禁止直接操作 main；先执行 git status -sb，并核对 HEAD 与 origin/collab/grok-code-codex-review 一致且工作区干净。
完整阅读 docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md、docs/integration-checklist.md。
长期目标：持续完成卡片化知识与素材库、多模板融合与可控 AI 编写、质量与交付闭环；每包必须独立规划、限定实现、Codex 审查与独立验收、中文文档闭环、推送协作分支。
当前进度：P12A、P12B-A/B/C/D、P12C-A、P12C-B-A、P9D、M3-A 至 M3-D、P8B/P8C/P8D/P8E、P9A/P9B/P9C、P10A 至 P10K、P11A/P11B/P11C 均已完成。P12C-B-A 冻结=`fbf93c0`、实现=`acf3139`；后端全量 680、前端全量 263 passed。
当前执行包：P12C-B-A 已让公开浏览器 PUT 以固定 `browser_put` 同锁同事务写入最近 10 条修订账本；没有历史列表/详情/恢复或前端。
下一步：只读审计并冻结 P12C-B-B 任务/revise 接入；必须枚举 task_service、business_task_service 与 revise_service 的调用和 commit/rollback 边界，不得直接给所有 `upsert_editor_state` 调用传来源。两类 callback、content-fuse、checkpoint restore 继续留待后包。
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

### 2.3 功能注释齐备表（交接审计 · 2026-07-15）

图例：**齐** = 文件顶含模块+用途+对接（核心服务另有二次开发）；**部分** = 有用途但缺对接/二次开发或仅部分文件；**弱/无** = 缺文件顶或仅零星行内注释。

#### 后端 `backend/app`

| 功能域 | 关键路径 | 文件顶注释 | 说明 |
|--------|----------|------------|------|
| 应用入口 | `main.py` | **齐** | 含二次开发 |
| 项目 CRUD | `services/project_service.py`、`api/projects.py` | **齐** | kind/business 与 editor-state responseMatrix 映射已写清 |
| 任务引擎 | `services/task_service.py`、`api/tasks.py` | **齐** | 取消、biz 分发、RAG 注入、SSE 短 Session；含 `content_fuse` |
| 模板/卡片融合 M3-A | `services/fuse_context_service.py`、`services/task_service.py`（content_fuse）、`tests/test_content_fuse.py` | **齐** | 只读建议；禁写 editor-state；跨 workspace 不泄漏；sourceRefs 含服务端 title；裁剪后 *Used |
| 融合写入持久恢复 M3-D | `models/entities.py`、`api/content_fuse_applications.py`、`services/content_fuse_application_service.py`、`api/schemas.py`、`tests/test_content_fuse_applications.py` | **齐** | 任务结果权威、锁内 base 校验、原子写入/快照/裁剪、最近 20 批、漂移安全一次消费；后端专项 34 passed |
| 商务任务 | `services/business_task_service.py` | **齐** | qualify/toc/quote/commit |
| 编辑态 | `services/editor_state_service.py` | **齐** | business_json、response_matrix_json 规范化与死引用收敛；P12B-A 共享 13 键版本、一次锁后 CAS、提交前响应和非有限值兼容 |
| 编辑态手动检查点 P12A | `models/entities.py`、`api/editor_state_checkpoints.py`、`services/editor_state_checkpoint_service.py`、`api/schemas.py`、`tests/test_editor_state_checkpoints.py` | **齐** | 服务端权威 13 键标准 JSON、最近 20 条、最小 SQL、完整显式回滚、只读详情；专项 29、后端全量 518 passed |
| 编辑态全状态版本 P12B-A | `api/projects.py`、`api/schemas.py`、`services/editor_state_service.py`、`services/editor_state_checkpoint_service.py`、`tests/test_editor_state_full_version.py` | **齐** | GET/PUT `stateVersion`、可选 expected CAS、固定最小 409、矩阵冲突优先级；专项 19、后端全量 537 passed |
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

### 4.23 P12C-A/B-A editor-state 有限自动修订账本

P12C-A 已完成并推送：冻结=`daa8c43`、实现=`226e1c1`。新增独立 `editor_state_revisions` 表，不复用 P12A/P12B-D 的 20 条手动/安全检查点裁剪域；每项目最近 10 条、单条最多 2 MiB，固定 `browser_put/task/revise/callback/local_parser/content_fuse_apply/content_fuse_consume/checkpoint_restore` 八类内部来源。`record_editor_state_transition` 只接受调用方同一事务内的 before/after 权威全状态，按空账本、连续、断链和回退语义追加，相邻版本去重，只 flush，不 commit/rollback/refresh/查询项目/取得第二把锁。

Grok 首轮全量 636 passed / 1 failed，失败来自并列时间戳测试在统一时间后继续 transition，误把随机 ID 稳定排序当作插入顺序；返修后先完成 transition，再统一时间且只验证 `created_at DESC, id DESC`。Codex 随后用失败先测证明缺任一 13 键时 `.get` 补 `None` 会让匹配版本的假状态入账（28 failed / 1 passed），限定返修为 extract 前要求共享权威键集合全部存在，并允许服务端派生额外键。随后补齐跨空间裁剪隔离、DELETE 行 ID 精确断言及合法 32 位夹具 ID。Grok 最终回执=`msg_07cc1dfd882d4117861661b1722ec205`，Codex 确认=`msg_294959d9885c4c50a7a5e77c687037fd`。

Codex 独立通过专项 **67**、P12A/P12B-D 受影响回归 **77**、后端全量 **666 passed**；只有 1 条既有 Starlette/httpx 弃用告警，`py_compile`、精确三文件白名单、工作树与暂存区 diff 检查均通过。A 包当时没有生产调用、API、Schema、前端、列表、详情或恢复能力。

P12C-B-A 已完成并推送：冻结=`fbf93c0`、实现=`acf3139`。公开浏览器 PUT 唯一传服务端字面量 `browser_put`；服务内部来源默认 `None`，来源存在时强制项目写锁，锁后同一 row 构造 before，commit 前同一事务记录 after。Grok failure-first 11 failed / 1 passed，首版 12/107/678；Codex 两轮返修关闭并列时间戳顺序假设、真实跨空间 404、flush 后脱敏 500 与 commit 前 revision 已 flush 证据，独立通过 **14/107/680 passed**。当前只接浏览器 PUT；任务/revise、两类 callback、content-fuse、checkpoint restore 仍须逐包证明同锁同事务、冲突或失败双零写。

### 4.24 路径索引

```text
backend/app/
  api/compliance.py finance.py hr.py bidder.py knowledge.py tasks.py projects.py content_fuse_applications.py editor_state_checkpoints.py settings.py opportunities.py resources.py templates.py
  services/
    task_service.py parse_engines.py business_task_service.py knowledge_service.py
    embedding_service.py duplicate_service.py rejection_service.py
    export_service.py revise_service.py editor_state_service.py editor_state_checkpoint_service.py editor_state_revision_service.py
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
| 技术标 | 通用版本、响应矩阵与解析增强 | M3-D 已交付服务端原子确认和最近 20 批一次性漂移安全恢复；P12B-A/B/C/D 已完成全状态版本、浏览器 CAS、所有既有延迟写入围栏、恢复前安全检查点、原子 restore 与双工作区显式入口；P12C-A/B-A 已交付独立最近 10 条修订账本、无提交原语和浏览器 PUT 原子记录。响应矩阵已完成来源分页、字段级三方合并和冲突保护；P8B/P8C/P8D/P8E 已接策略、单次票据与本机 MinerU/Docling 助手。仍未接：任务/revise、callback、content-fuse、checkpoint restore 的修订记录，任意历史浏览/回滚、多人协作、解析器自动部署/模型打包、真实模型验收与其他交付增强 |
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
4. P12B-A/B/C/D、P12C-A 与 P12C-B-A 已完成。下一主线固定为 P12C-B-B 任务/revise 原子接入审计；必须先区分 task、business task 与 revise 的真实事务/提交边界，再冻结一个最小包。两类 callback、content-fuse、checkpoint restore 继续拆包，禁止跳到删除、diff、搜索、跨项目历史、任意版本恢复或多人协作。

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

## 11. 当前会话状态（2026-07-15）

- **用户长期目标（必须完整保留）**：持续完成 biaoshu 标书制作者剩余主线任务，按既定路线图完成独立规划、受限实现审查、独立验收、中文文档闭环与协作分支推送；不直接操作 `main`。
- **P11A 已完成并推送**：计划=`70a2dc7`、前端=`b0a86e4`。服务端 `/api/projects*` 已成为技术标/商务标项目列表、详情、创建及查重/废标选择器的唯一真值；旧 `biaoshu.projects.v1` 被忽略且原值不变，失败不再生成本地项目或复活演示项目。
- **P11B 已完成并推送**：计划=`6a3f4fe`、前端=`a99d8d4`。商务 workspace 只认既有 editor-state GET/PUT；旧 workspace 键忽略保值，真实空态保持空，GET/PUT 固定脱敏失败，任务后刷新失败与 A→B 迟到均有显式边界；AI 反馈 history 仍为非目标。Grok 因 402 未发正式审查消息，Codex 依据现有差异完成独立审查，仅修正 E2E 测试时序后验收。
- **P11C 已完成并推送**：契约=`docs/p11c-technical-editor-state-truth-contract.md`，计划=`docs/plans/2026-07-15-p11c-technical-editor-state-truth-plan.md`，前端=`1441509`。技术标 editor-state 只认服务端；旧本地键忽略保值，真实空态不补 mock，失败固定脱敏；普通与合并 PUT 使用同源 Cookie/内存 CSRF；409/M3-D 保持兼容；A 的迟到或挂起保存不污染、阻塞 B；生产演示入口已移除。
- **P8D 已完成并推送**：契约=`docs/p8d-mineru-local-helper-contract.md`，计划=`docs/plans/2026-07-15-p8d-mineru-local-helper-plan.md`，实现=`e1fe316`。纯标准库助手只从交互 TTY 读取 P8C 单次票据，Windows 只认 `mineru.exe`，强制本地离线模型、环境白名单、临时输出和回环无代理/无重定向单次回调；真实 CLI/模型由用户人工准备。
- **P8E 已完成并推送**：契约/计划提交=`73b1264`，P8E-A 后端=`79b346e`，P8E-B 助手=`e3f9cc4`。Docling 46、MinerU 54、后端受影响回归 37、P8C E2E 9、P8B E2E 6 passed；真实 Docling/模型仍未安装、未验收，禁止声称真实模型就绪。
- **Grok 当前状态**：额度可用；P12C-B-A 最终回执=`msg_3f8e00adcaf841b29c69c9d79b9bd5fc`，Codex 确认=`msg_e42acfe061a24efaa036cfea081311a4`；P12C-A 最终回执=`msg_07cc1dfd882d4117861661b1722ec205`，Codex 确认=`msg_294959d9885c4c50a7a5e77c687037fd`。协作方式仍是 Grok 只实现/自测、不提交推送，Codex 独立规划、审查、验收、中文提交与推送；后续不得因额度可用而跳过任务包和审查门。
- 当前分支仍为 `collab/grok-code-codex-review`；P12C-B-A、P12C-A、P12B-D、P12B-C、P12B-B、P12B-A、P12A、P8D、P11C、P11B、P11A、M3-D、P10K、P8C、P10J、P9D、M3-C、P10I、P10H、P10G、P10F、P10E 与 P8B 基线均已推送，本文档闭环提交位于其后。新会话第一步必须用 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review` 重新核验，不可只信本文静态 SHA。
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
- **已验证基线**：P12C-B-A 专项/受影响回归 14/107、后端串行全量 680 passed（只有 1 条既有 Starlette/httpx 弃用警告）；P12C-A 历史基线为 67/77/666；P12B-D2 专项/受影响回归 51/63、单 worker、零重试前端全量 E2E 263 passed；lint 零问题，build 通过（仅既有大 chunk 警告），生产与测试文件 `py_compile`、白名单、工作树与暂存区 diff 检查通过。P12B-D1 历史基线为 58/81/599；D2 全量首跑 239 passed 后单次纯白页，精确 1 项与完整 263 项重跑均通过。P12B-C 历史基线为后端/前端全量 570/212；P8E 独立 Docling 46、MinerU 54 继续保留。**E2E 共用 SQLite 重置库，所有 Playwright 命令必须串行。**
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
- **其余未实现主线**：P12C-B-B 任务/revise 接入、后续两类 callback、content-fuse 与 checkpoint restore 接入，P12C-C 受限历史浏览/恢复及多人协作；MinerU/Docling 自动安装、模型打包、常驻服务、真实模型样本验收与完整孙进程治理；P9C 后续真实语义调优；Word `structure`/整章布局；除国能 e 招外的合法外部标讯来源；人力附件/真实证件核验；财务税务/审批/导出/预算/回款/版本、失败尝试与完整身份审计；投标人矩阵明细/版本/结果跟踪；Alembic、PostgreSQL、HTTPS、Key 加密、Docker 和公网 SaaS 能力。下一包固定先只读审计 P12C-B-B 的 `task/revise` 调用点与事务边界；不得给所有 `upsert_editor_state` 调用统一补来源。
- 新任务分工不变：Grok 只负责限定实现与自测，未经 Codex 审查确认不得提交；Codex 负责计划、范围冻结、差异审查、独立测试、验收、中文提交、文档闭环和 GitHub 状态核验。每一包仍按“计划提交 → 实现提交 → 文档闭环提交 → 推送协作分支”执行，禁止合包。
- GitHub 若出现连接重置，可在当前 PowerShell 进程临时配置 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY=http://127.0.0.1:7890` 与 `NO_PROXY=localhost,127.0.0.1` 后重试；不得把代理或凭据写入仓库。

**换会话可直接：核验分支、HEAD/远端与工作区 → 读本文 §0～§3.1、§4.23、§5、§6、§11、P12C 契约/计划及路线图 → 确认 P12C-B-A 冻结 `fbf93c0`、实现 `acf3139` 已推送且后端/前端全量 680/263 passed → 逐一审计 `task_service.py`、`business_task_service.py`、`revise_service.py` 对 `upsert_editor_state` 的调用及其 commit/rollback、锁和失败边界，再冻结 P12C-B-B 最小包。禁止重新实现 P12C-B-A/P12C-A/P12B-A/B/C/D/P12A、把单一浏览器写入者冒充完整自动历史、跳到浏览/恢复/删除/diff/多人协作、让 Grok commit/push，或由 Codex 冒充 Grok 完成主实现。**
