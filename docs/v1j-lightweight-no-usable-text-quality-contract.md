<!--
模块：V1-J lightweight 无有效正文解析质量门契约
用途：阻止空 TXT/MD/DOCX、空表 DOCX 与全空 PDF 被标为解析成功并进入分析链。
对接：parse_service、knowledge_service、项目 parse 任务、知识库索引与 V1-D 合成质量门。
二次开发：只允许严格四文件；禁止字符串剥离 Markdown 冒充结构判定，禁止 OCR/模型/依赖扩围。
-->

# V1-J lightweight 无有效正文解析质量门契约

> **状态：已完成、独立验收并推送。**
> **基线：** `54eb128e72d286b8f711c6fda7eca41d14de1d9e`；冻结=`1e33f1b`，测试=`3917c3a`，实现=`f9562a5`；仅 `collab/grok-code-codex-review`，严禁操作 `main`。

## 1. 问题真值与优先级

V1-I 已让真实文件进入项目，但默认 lightweight 解析仍把结构占位当正文：

1. TXT/MD/markdown 的正文全空白时，仍返回非空“解析结果”标题；
2. DOCX 没有非空段落或单元格时，仍返回“（DOCX 无段落文本）”或空表 Markdown；
3. PDF 所有页面均无提取文本或零页面时，仍返回页标题、扫描提示或“（PDF 无页面）”；
4. `_run_parse` 把以上字符串写入 editor-state，推进项目步进并标 success；`analyze/outline` 只检查字符串非空，因此会继续调用模型；
5. 知识库共用同一解析器，若直接抛新异常，现有宽泛分支会把英文异常类名显示给用户，失败重建还可能保留旧 chunk。

双路只读审计：A task/review=`msg_9663fb0ef51645d49506d9d0a5e2167a`/`msg_65d6d32db8b34ddab3d782d441cc3e21`，B task/review=`msg_d6a35463076d48de935502811228e7aa`/`msg_4ae54a8daf5b4f80898804cff6d36c66`。统一范围确认：A question/YES=`msg_029145240aa94d81b9077fd729fa8cb5`/`msg_06eb09050d894514b61741f45055e952`，B question/YES=`msg_7b5c850f85f74aed82ea3178939a42c9`/`msg_9324c2effe00486ca53723aa7edaddfd`。知识库边界确认：A question/YES=`msg_c8a617cb9021431d8942a30e3ca914d1`/`msg_81ba67351d834a3f83efa11ee495ac1d`，B question/YES=`msg_42acea811dc04b5091ab0698fb9b1e21`/`msg_caf4c9016679402c922d006da12c16e1`。

启动失败诊断也真实存在，但默认前台控制台/pause 与用户已冻结的后台静默、不弹终端/浏览器、不抢焦点偏好冲突。本包只解决会写入伪正文并放行 LLM 的协议级假成功；启动诊断后置到日志、状态页或显式诊断命令的独立产品设计。

## 2. 统一产品语义

### 2.1 有效正文定义

- TXT/MD/markdown：解码后 `strip()` 至少一个字符才算有效；仅空白为无有效正文。
- DOCX：至少一个段落文本或表格单元格文本 `strip()` 非空才算有效。仅空段落、仅空表、仅图片/未支持对象均为无有效正文。
- PDF：至少一页 `extract_text()` 后 `strip()` 非空才算有效。零页或全部页面无文本均为无有效正文。
- 未知扩展名的既有按文本降级不在本包改变范围；后续若要收紧必须另包。

### 2.2 成功与失败

1. 全空 TXT/MD/markdown/DOCX/PDF 必须抛 `parse_service.NoUsableTextError`，固定中文错误精确为：

   `未提取到可用正文，请检查文件是否为空；扫描版 PDF 请使用本地 MinerU`

2. `NoUsableTextError` 必须继承 `ValueError`，不得带路径、文件内容、异常原文、堆栈、类名或第三方输出。
3. 项目 parse 依赖既有异常链标 failed；异常发生在 `_run_parse` CAS/upsert、`update_project` 和 success 之前，必须保留既有 parsedMarkdown/stateVersion/project status/step，task result 为空。
4. PDF 混合页只要至少一页有真实文字就继续 success；无文字页仍按 V1-D 保留精确 SCAN_HINT。正文加空表的 DOCX 继续 success 并保留空表块。
5. 不新增 success+warning 半绿状态，不改 task schema、engine 名、payload/result、前端按钮或 analyze/outline 协议。

## 3. 解析层设计边界

质量判断必须在 `parse_service` 各格式完成结构提取、尚未 return Markdown 时进行：

- 禁止在 `task_service` 对最终 Markdown 去标题、页标题或占位文案做字符串扫描；
- DOCX 判据必须来自真实段落/单元格文本，不得把 GFM 分隔行或“（空表）”当正文；
- PDF 判据必须来自逐页原始提取结果，不得把 `## 第 N 页` 或 SCAN_HINT 当正文；
- `parse_engines` 继续透传解析器异常，不新增质量元数据或旁路。

V1-D 的“全空 DOCX/PDF 返回占位 Markdown”旧测试语义由本包明确取代；混合页顺序、真实文字页、标题、表格和空页诊断仍保持。

## 4. 知识库一致性

`knowledge_service.index_document()` 必须只对 `NoUsableTextError` 新增专门失败分支：

1. 删除该文档现有全部 `KbChunkRow`，不得只把 `chunk_count` 改 0 留下可检索旧块；可复用 `_replace_chunks(..., [])`；
2. `status="failed"`、`status_message` 精确等于固定中文 `str(exc)`、`chunk_count=0`；
3. 保留文档数据库行与磁盘文件，允许用户重新索引或删除；
4. 其它异常分支、成功分块、embedding、知识库 API/前端和 schema 完全不变；
5. 有正文文档继续 ready、`status_message=None` 且 chunk 数大于 0。

## 5. 严格文件白名单

生产：

1. `backend/app/services/parse_service.py`；
2. `backend/app/services/knowledge_service.py`。

测试：

3. 新增 `backend/tests/test_v1j_lightweight_no_usable_text_quality.py`；
4. `backend/tests/test_parse_service_synthetic_quality.py`，只做 V1-D 冲突期望的 test-only 更新与必要混合/空表反假绿补强。

禁止修改 `task_service.py`、`parse_engines.py`、前端、API/schema、实体、数据库/迁移、依赖、配置、上传、LLM、MinerU/Docling 助手、启动脚本或 V1-I。任何扩围必须先 Codex question、Grok 只读确认，再由新 task 授权。

## 6. Failure-first 与反假绿矩阵

新专项和 V1-D 测试只使用系统 TEMP 合成数据与 pytest 临时数据库/上传根：

1. 空字节、空白、CR/LF/TAB 的 `.txt/.md/.markdown` 均抛精确 `NoUsableTextError`；BOM/零宽字符是否算正文必须在 failure-first 如实记录，未经确认不得擅自扩 Unicode 规则。
2. 空 DOCX、仅空段落 DOCX、仅空表 DOCX 均抛精确错误；正文+空表继续 success，正文和“（空表）”顺序保持。
3. 两页 blank PDF 与零页 PDF 均抛精确错误；混合 PDF 至少一个真实文字锚点时 success，空页 SCAN_HINT 数量和顺序精确。
4. 有正文 TXT/MD/DOCX/PDF 继续 success，文件名只进入既有 Markdown 标题，不误判固定错误文案。
5. 项目 parse 对每类代表空文均 failed：task error 固定、result 为空；editor-state 完整指纹与 project status/step 前后相等，零 analyze/outline/LLM 调用。
6. 知识库新空文上传后保留 doc/file、failed、固定纯中文、零 chunk；已有 ready 文档改为空文重索引时旧 chunk 必须物理删除。有正文知识库文档仍 ready 且可检索。
7. 错误不得包含 `NoUsableTextError`、`ValueError`、绝对路径、用户名、字节、正文、SQL、Cookie、CSRF、密钥或堆栈。

生产未改时必须真实红；预计新专项约 **6 failed / 4 passed**、V1-D test-only 改写另有约 **2 failed**，实际数字必须如实报告。禁止 `skip/xfail`、源码扫描、宽泛 `or`、固定 sleep、只断言异常非空、只改计数不查旧 chunk 或吞异常假绿。

## 7. 分级验收

严格串行、禁止 xdist：

```powershell
cd C:\Users\Administrator\biaoshu-v1-next-audit-b\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_v1j_lightweight_no_usable_text_quality.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_parse_service_synthetic_quality.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_parse_engines.py tests\test_parse_export.py tests\test_knowledge_rag.py
.\.venv\Scripts\python.exe -m py_compile app\services\parse_service.py app\services\knowledge_service.py tests\test_v1j_lightweight_no_usable_text_quality.py tests\test_parse_service_synthetic_quality.py
git -C .. diff --check
```

不得运行后端全量、前端 E2E、真实 CLI/模型、真实数据库/uploads/知识库或联网安装。Grok B 先在独立 worktree 写 failure-first；Codex 审查并提交 test-only 后转入 A。Grok A 只写两生产文件并串行自测；Codex 最终独立验收、提交、推送和文档闭环。

## 8. 非目标与隐私

- 不做 OCR、自动 MinerU/Docling、图片/文本框识别、复杂版式、语义最小字数或内容正确性评分。
- 不改变已上传源文件、失败文档保留、项目手工上传、导出、备份、协作、V2/V3。
- 启动失败诊断另包设计，默认日用启动继续后台静默、不弹终端/浏览器、不抢焦点。
- 测试不得读取真实业务文件；合成根清理后必须不存在，消息箱不得写文件内容或敏感信息。

## 9. 完成证据

Grok B 原任务/review_request=`msg_f5f61e84acd248b2a529a901dc14edcd`/`msg_2721edbd24b14ea582fadbc06e0c83c5`。生产未改时新专项真实 **6 failed / 4 passed**，V1-D 真实 **2 failed / 8 passed**；Codex 独立复跑数字一致，均为业务红点。

Codex 审查发现完整 editor-state 指纹漏字段、零 analyze/outline 调用未直接设钩。question/确认=`msg_ad190c69b3c5485da46203af2390f29d`/`msg_f022456ba93948ee8e235c71fdb15fa2`，确认后返修 task/review_request=`msg_14773be035df4aab92cf4d352a4def59`/`msg_34573b6f1457404cbee4902074ffa2d2`。最终测试改为完整 GET 响应深拷贝精确比较，并分别锁定 analyze、outline、LLM 零调用。

Grok A 生产 task/review_request=`msg_9bbf84bad2f94718a01a46341bd37eb0`/`msg_adfaa73157f9476ea33a3a9b66703abb`。Grok A 与 Codex 独立结果一致：V1-J **10 passed**、V1-D **10 passed**、解析引擎/导出/知识库代表回归 **38 passed**；`py_compile`、`git diff --check`、严格四文件和空暂存区通过。未运行后端全量、前端 E2E、真实数据库/uploads、真实 CLI/模型或联网安装。

主协作 worktree 最终 SHA-256：`parse_service.py=32C8B254F479B20D578F673AF1BDB83C4792D38E856D0056948ED02F93C77945`，`knowledge_service.py=FE2B47CC361573E27592518C64B851C13838E0E2737CAD10F2AEF2FFDA75AA66`，V1-J 测试=`913F77EF42BCF183769F251B8BE543E862D80717BD3B4642E4C0F434E3A02BD1`，V1-D 测试=`4E98BC35B39831E09CC7E327C4DB2620F860D4DEDD5FEB83FEBD7207BE721142`。对应 Git blob 依次为 `0ef1e7a429e3d3dd8643503ab9874446b8e4a64b`、`b68d5432855f89f5068b1d57d2a954aa77ac859f`、`8780cfc7687f1920fee2d0f315a47d0b34ace4f6`、`5f966c98b8832b7061795d498106132b2937062d`。B 提交前新测试为 LF，A/主仓检出为 CRLF，原始字节 SHA 因此不同；Git blob 和内容差异均一致，测试未被修改。
