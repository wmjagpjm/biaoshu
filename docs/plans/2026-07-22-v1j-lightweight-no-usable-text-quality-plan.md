<!--
模块：V1-J lightweight 无有效正文质量门实施计划
用途：按测试先行、双确认、独立实现和串行验收交付统一空文档失败语义。
对接：V1-J 契约、A/B 独立 worktree、V1-D 合成测试、项目 parse 与知识库索引。
二次开发：B 只写测试，A 只写生产；Codex 负责冻结、审查、Git 与最终闭环。
-->

# V1-J lightweight 无有效正文质量门实施计划

> **执行代理要求：** 使用 `executing-plans`；任何测试强度、生产语义或白名单问题必须先双确认。

**目标：** 所有 V1-I 支持的 lightweight 文档在没有真实正文时统一 failed，不写伪 editor-state、不放行 LLM；知识库显示固定中文并清除旧 chunk。

**基线：** `54eb128`。A=`C:\Users\Administrator\biaoshu-v1-next-audit-a`，B=`C:\Users\Administrator\biaoshu-v1-next-audit-b`；冻结提交后两树均快进到冻结基线。

### 任务 1：冻结文档

1. 提交契约、本计划、交接、路线图和联调清单，只推送 `collab/grok-code-codex-review`。
2. 核对本地/远端一致与空工作区，严禁 `main`。
3. 记录 A/B 审计、统一范围确认和知识库扩围确认完整消息链。

### 任务 2：Grok B failure-first

唯一可写：

- `backend/tests/test_v1j_lightweight_no_usable_text_quality.py`；
- `backend/tests/test_parse_service_synthetic_quality.py`。

按契约 §6 先写合成格式、项目任务和知识库矩阵；生产未改时串行运行两文件，报告真实 failed/passed、首个业务红点、TEMP/数据库/旧 chunk 证据、两文件 SHA-256、diff-check 和空暂存。禁止生产或 Git 写入。

### 任务 3：Codex 审查并转移测试

逐行排除最终 Markdown 字符串扫描、只看异常非空、只把 chunk_count 置 0、未验证项目完整指纹、真实文件/数据库、宽断言和条件假绿。疑似问题先 question，B 只读确认后才下发 test-only 返修。合格后由 Codex 提交两测试并转入 A。

### 任务 4：Grok A 最小生产实现

唯一可写：

- `backend/app/services/parse_service.py`；
- `backend/app/services/knowledge_service.py`。

在解析结构层实现 `NoUsableTextError` 与各格式真实正文判据；知识库只新增该异常专门分支并物理清旧 chunk。不得改任务层、引擎层、前端或依赖。串行运行契约 §7，发送 review_request，不得 Git 写入。

### 任务 5：Codex 独立审查与验收

1. 核对严格四文件、冻结测试哈希、空暂存和合成根清理。
2. 审查段落/单元格/页文本判据、混合 PDF、空表、异常类型/固定文案、项目零副作用和知识库旧 chunk 物理删除。
3. 独立串行运行新专项、V1-D、解析引擎/任务/知识库代表回归、py_compile 与 diff-check；禁止全量和并发。
4. 问题继续走 `question → Grok YES → task → review_request`。

### 任务 6：提交、推送与闭环

1. Codex 提交两生产文件，快进主协作分支并推送；不得合并 `main`。
2. 更新契约/计划、`HANDOFF-next.md`、路线图和联调清单，记录真实红绿、消息、四文件哈希和未运行项。
3. 再只读审计下一 V1 断点；启动诊断只能在保持默认静默的前提下另包设计。
