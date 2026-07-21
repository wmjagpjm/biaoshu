<!--
模块：V1-H2 技术标导出正文完整性提醒实施计划
用途：按冻结、后端/前端 failure-first、最小实现、独立验收和文档闭环拆分 H2。
对接：H2 契约、Grok A/B 消息箱、独立 worktree、串行 pytest 与 Playwright。
二次开发：严格六文件；测试先行；疑似问题双确认后才返修。
-->

# V1-H2 技术标导出正文完整性提醒实施计划

> **执行代理要求：** 必须使用 `executing-plans`，逐项执行并在每个审查点核对真实证据。

**目标：** 技术标包含历史、手工或部分生成空章时仍可导出审阅草稿，但任务和浏览器必须给出固定、有限、脱敏的正文完整性提醒。

**架构：** DOCX 组装在读取权威 chapters 时把固定提醒写入调用方提供的列表，export task 以独立 `contentWarnings` 返回；技术标页面复用现有导出所有权代次，以独立纯文本组件展示。图片告警、保存门和 Blob 下载链不改。

**技术栈：** FastAPI/SQLAlchemy、python-docx、React/TypeScript、pytest、Playwright Chromium。

---

### 任务 1：冻结与创建 worktree

1. 只提交 H2 契约与本计划，中文提交并推送协作分支。
2. 从冻结提交创建 `C:\Users\Administrator\biaoshu-v1h2-export-content-warnings-impl`、分支 `collab/v1h2-export-content-warnings-impl`。
3. 重启 A/B 自动路由到新 worktree；用户交互式 Grok 不结束。

### 任务 2：Grok B 后端 failure-first

**唯一可写：** `backend/tests/test_v1h2_export_content_warnings.py`。

1. 实现契约 §6 后端五组真值，使用临时数据库/TEMP 和合成锚点。
2. 精确断言 result 字段、Word 内容、隐私禁词、商务隔离和合法短章对照。
3. 只运行新专项，记录真实首红、哈希、diff-check、空暂存并回信。

### 任务 3：Codex 审查后端红测

1. 逐行排除 fixture/路径/字段缺失假红，独立串行复跑。
2. 确认无章节、空白计数、合法短章、pending 非空、商务模式均独立覆盖。
3. 疑似缺口先 question；双方确认后才授权 test-only 返修。
4. 冻结后端测试哈希。

### 任务 4：Grok B 前端 failure-first

**唯一可写：** `frontend/e2e/export-content-warnings.spec.ts`；后端冻结测试只读。

1. 实现契约 §6 前端四组真值，真实点击技术标导出并受控 route/download。
2. 精确证明提醒先于下载、图片/正文语义分离、恶意收敛、干净重导出清空和 A→B 迟到隔离。
3. 单 worker、零重试运行新专项，报告真实首红与哈希。

### 任务 5：Codex 审查前端红测

1. 排除固定 sleep、宽泛计数、私有函数或源码扫描假绿。
2. 核对 A→B 在 B 就绪后才释放 A success，旧任务零提醒/零下载。
3. 独立串行复跑并冻结前端测试哈希；问题仍走双方确认门。

### 任务 6：Grok A 最小生产实现

**可写：** `backend/app/services/export_service.py`、`backend/app/services/task_service.py`、`frontend/src/shared/components/ExportContentWarnings.tsx`、`frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`。

1. `build_docx_bytes()` 接受可选 `content_warnings`，仅技术标按冻结两句规则追加最多一条；Word 行为保持。
2. `_run_export()` 传入列表，并始终在 result 返回 `contentWarnings`；商务为空。
3. 新组件防御性收敛 20 条/240 码点并纯文本展示。
4. 技术页使用现有 export generation/项目/准备令牌门，清空、接纳与渲染独立正文提醒。
5. 先跑两个新专项，再按契约串行回归、lint/build；冻结测试只读，完成后回信。

### 任务 7：Codex 独立验收与提交

1. 核对严格六文件、两测试哈希、空暂存与隐私边界。
2. 串行运行契约 §7 全部定向命令，禁止机械全量。
3. 发现生产问题先 question，确认后才授权 production-only 返修。
4. Codex 中文提交实现，快进到协作分支并只推送该分支。

### 任务 8：文档闭环与下一主线

更新 H2 契约/计划、交接、路线图和联调清单，记录真实红绿、消息链、哈希和未运行项；中文提交并推送。随后重新审计 V1 最终标书可交付性的下一高价值断点，不抢跑 V2/V3。
