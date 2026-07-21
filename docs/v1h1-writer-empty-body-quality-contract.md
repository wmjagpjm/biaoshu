<!--
模块：V1-H1 章节生成空白正文质量契约
用途：阻止模型空白输出被写入编辑态并被任务谎报为章节生成成功。
对接：chapter/chapters 任务、章节编辑态 CAS、V1-H1 后端专项测试。
二次开发：只拦截 strip 后为空的模型输出；禁止最小字数门、全书原子回滚或导出协议扩围。
-->

# V1-H1 章节生成空白正文质量契约

> **状态：已冻结，待 failure-first、生产实现和 Codex 独立验收。**
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** `775875d3a634344a3ecbbf6acacfc5d32e6f3ac5`；V1-A 至 V1-G 已完成并推送。

## 1. 问题真值

`backend/app/services/task_service.py::_generate_one_chapter_body()` 当前直接返回 `llm_service.chat_completion()` 的 `result.content`。模型返回 `""` 或仅空白字符时：

1. 单章任务仍把空正文写入 editor-state，标记 `status="needs_review"`、`wordCount=0`，并以 `chars=0` 成功结束；
2. 多章任务仍把该章计入 `generated`，继续推进逐章 CAS，最终可谎报“已生成 N 章”；
3. 后续技术标导出会把空章写成“（本章暂无正文）”并成功生成 Word；
4. 现有测试没有证明模型空白输出必须失败，也没有证明失败章不得被改成 `needs_review`。

Grok B 只读确认=`msg_6a454f4b90aa42a1aba2838171dd9cf8`，Grok A 只读确认=`msg_428291552f784462864bd947aafda9d5`；Codex 已独立复核 `task_service.py:1599-1868` 与 `export_service.py:1553-1593`。

## 2. 方案裁定

1. **采用中央空白门。** `_generate_one_chapter_body()` 在取得模型结果后，以 `str(content or "").strip()` 判断有效性；为空时抛出固定中文 `ValueError("模型未返回有效章节正文，请重试")`。
2. **保留原始有效正文。** 仅用 `strip()` 判空，不裁剪或改写有效 Markdown，不改变引用、温度、超时、提示词或 `targetWords`。
3. **禁止粗暴字数门。** `"无。"`、`"见附件一。"` 等合法短章必须继续成功；本包不依据字数、目标字数比例、章节状态或文风评分阻断。
4. **保持逐章提交语义。** 多章任务前一章已成功写入、后一章返回空白时，任务失败但前一章保留；不得改成全书事务或回滚成功章。
5. **导出提醒另包。** 历史空章、手工清空和部分生成残留由 V1-H2 的 `contentWarnings` 处理；H1 不修改 export、前端或图片告警。

## 3. 冻结行为

### 3.1 单章任务

- 模型输出 `""`、空格、制表符、换行或其组合时，任务最终必须为 `failed`。
- 任务固定 `message="任务失败"`，固定安全错误为“模型未返回有效章节正文，请重试”；不得回显模型原始响应、路径、密钥或堆栈。
- 目标章及整个 editor-state 不得因该空白输出发生写入；不得产生 `needs_review`、`wordCount=0` 或成功提示。
- 非空正文继续沿用既有 CAS、项目步进、引用和任务 result 语义。

### 3.2 多章任务

- 第一目标章即返回空白时，任务失败且零章节写入。
- 第 N 章返回空白时，前 N-1 个成功章按既有逐章 CAS 保留；第 N 章及后续章保持生成前状态，任务不得 success，也不得把空白章计入 `generated`。
- 失败路径不得绕过 `_require_payload_expected_version()`、不得重读当前版本后强行覆盖外部编辑。
- 取消、版本冲突和普通上游异常语义保持不变。

## 4. 严格文件白名单

生产：

1. `backend/app/services/task_service.py`。

测试：

2. 新增 `backend/tests/test_v1h1_writer_empty_body_quality.py`。

冻结与闭环文档可修改本契约、对应计划、`docs/HANDOFF-next.md`、路线图和联调清单。禁止修改 `llm_service.py`、editor-state 服务、导出服务、前端、数据库、迁移、依赖、pytest 配置或其它测试。确需扩围必须先由 Codex 发 question，Grok 只读确认后再修订契约并授权。

## 5. failure-first 与反假绿矩阵

专项只使用 pytest 临时数据库与合成章节，不读真实 `biaoshu.db`、uploads、密钥或联网模型：

1. 单章模型返回 `""`：任务应 failed，固定错误，editor-state 版本及目标章完整不变；当前生产应真实失败。
2. 单章模型返回 `" \n\t "`：与空串同义；当前生产应真实失败。
3. 多章首章返回空白：任务 failed，所有章及版本不变，后续生成函数不得继续调用；当前生产应真实失败。
4. 多章第一章返回有效锚点、第二章返回空白：任务 failed；第一章已写并只推进一次版本，第二章仍 pending/空，第三章未调用；当前生产应真实失败。
5. 单章返回 `"无。"`：任务 success、正文精确保留、`chars=2`，证明没有最小字数误杀；当前生产应通过。
6. 单章返回带首尾空白的有效 Markdown：任务 success 且正文原样保留，证明判空不等于裁剪；当前生产应通过。

测试必须精确记录生成函数调用序列与 editor-state 前后快照；禁止 `skip/xfail/importorskip`、宽泛 `or`、空列表循环、源码字符串扫描、真实 LLM、sleep、外网和条件假绿。预期生产未改时 **4 failed / 2 passed**；实际结果必须如实报告，失败必须落在任务终态或编辑态断言，不得以 fixture、数据库、导入或环境错误冒充红测。

## 6. 分级验收

严格串行、禁止 xdist：

```powershell
cd C:\Users\Administrator\biaoshu-v1h1-writer-empty-impl\backend
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_v1h1_writer_empty_body_quality.py --tb=short
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_p12c_task_revisions.py tests\test_knowledge_rag.py --tb=short
git -C .. diff --check
```

Codex 独立验收必须核对严格两文件、冻结测试哈希未变、暂存区为空、失败错误固定脱敏、合法短章不误杀、逐章 CAS 语义不变。禁止后端全量、并发 pytest、真实服务、真实业务库、uploads、密钥或外网。

## 7. 非目标

本包不处理历史空章、手工清空、导出硬阻断、正文完整性 UI、极短章评分、文风与事实质量、自动补写、全书原子回滚、商务标、V2 多人协作或 V3 SaaS。V1-H2 将单独解决“空章可导出但不得静默交付”。

