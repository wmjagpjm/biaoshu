<!--
模块：V1-D lightweight 文档结构质量契约
用途：冻结 DOCX 标题/段落/表格原始块顺序与合成 PDF 内容质量门，补齐本机日用解析的结构缺口。
对接：backend/app/services/parse_service.py、parse_engines LightweightParseEngine、parse/analyze/outline 任务链。
二次开发：禁止加入 OCR、MinerU/Docling、外网、真实标书、新依赖/API/数据库；复杂合并单元格与版式还原另包。
-->

# V1-D lightweight 文档结构质量契约

> **状态：冻结前草案。** 冻结提交后才允许在独立 worktree 写测试或实现。
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **基线：** `ca741ed`；V1-C 已完成，本机仍无真实 MinerU/Docling CLI。

## 1. 问题真值

当前 `parse_service.parse_file_to_markdown` 的 DOCX 分支只遍历 `doc.paragraphs`：

- 普通段落文本和相对顺序保留；
- Heading 样式被降为普通文本；
- `w:tbl` 不进入 `doc.paragraphs`，表格及全部单元格文本完全丢失；
- 表前/表后段落仍存在，但表格从语义顺序中蒸发。

Codex 与 Grok A/B 使用系统 TEMP 合成样本独立确认：普通 DOCX 段落通过，表格锚点失败；文字型 PDF 按页通过，无文本 PDF 返回 MinerU 提示。现有解析测试主要覆盖 Markdown 与 fake engine，没有真实 DOCX/PDF 结构质量门。

## 2. 本包目标

1. DOCX 按 `document.element.body` 的真实子节点顺序处理段落与表格；
2. Heading 1–6 映射为对应 Markdown 标题；
3. 普通表格转换为稳定的 GFM pipe table，保留空单元格、管道符和单元格换行；
4. 保持 txt/md/markdown、PDF、未知类型和解析任务协议不变；
5. 用完全合成 TEMP DOCX/PDF 建立 failure-first 和回归门。

## 3. DOCX 输出契约

### 3.1 头部与块顺序

输出继续以精确头部开始：

```text
# 解析结果：<original_name>
```

正文块只能按 `w:body` 子节点原顺序输出：

- `w:p`：段落；
- `w:tbl`：表格；
- 其它节点忽略。

禁止先遍历全部段落、再把表格追加文末。非空块之间固定两个换行；空段落继续忽略。

### 3.2 标题与普通段落

- 样式名精确匹配 `Heading 1` 至 `Heading 6` 时，输出 `#` 至 `######`、一个空格和 strip 后文本。
- 其它样式，包括列表、自定义标题、未知或缺失样式，保持普通文本块；本包不恢复列表符号或层级。
- 空文本段落不输出。

### 3.3 普通表格

每个非空表格输出 GFM pipe table：

```markdown
| H1 | H2 |
| --- | --- |
| A1 | A2 |
```

规则：

1. 第一行固定作为表头；紧随一行与列数相同的 `---` 分隔行。
2. 各行以最大列数右侧补空，空单元格保留列位，稳定表现为 `|  |`。
3. 单元格文本 strip 后，把任意 CR/LF 和连续空白折叠为单个 ASCII 空格。
4. 单元格中的 `|` 精确转义为 `\|`。
5. 行顺序、列顺序和全部非空单元格文本必须保留。
6. 无可见行的表格输出固定 `（空表）`，不得抛异常。

### 3.4 明确降级

复杂合并单元格不做 rowspan/colspan 语义还原。本包接受 python-docx 的展平视图，可能重复合并文本；测试不得把普通表格通过伪装成合并单元格支持。嵌套表格、图片、图表、SmartArt、文本框、页眉页脚、脚注、分页和自定义编号均不处理。

## 4. PDF 质量门

PDF 生产代码保持冻结：

- 继续使用 `pypdf.PdfReader`；
- 每页按顺序输出 `## 第 N 页`；
- 有文字页输出 `extract_text().strip()`；
- 无文字或提取异常页输出精确诊断 `（本页未提取到文本，可能是扫描件，请用本地 MinerU）`。

本包只新增合成两页文字 PDF 顺序门和两页 blank PDF 诊断门。禁止 OCR、Tesseract、PDF 表格/坐标/版式还原、图片提取、模型或外置 CLI。

## 5. 协议与兼容边界

- `LightweightParseEngine`、注册表、engine 名、任务 payload/result、editor-state、analyze/outline 截断与写事务全部不改。
- `parseStrategy=light|local|ask` 与 P8B/P8C/P8D/P8E/V1-C 不改。
- `python-docx` 与 `pypdf` 已在 `backend/requirements.txt`；不得新增或升级依赖。
- 不改文件上传、知识库、前端、API、数据库、模型、迁移或启动脚本。
- 不读取真实业务文件、`biaoshu.db`、uploads、密钥或 Cookie。

## 6. 严格文件白名单

冻结阶段：

1. `docs/v1d-lightweight-document-structure-quality-contract.md`；
2. `docs/plans/2026-07-22-v1d-lightweight-document-structure-quality-plan.md`。

实现阶段：

1. 修改 `backend/app/services/parse_service.py`；
2. 新增 `backend/tests/test_parse_service_synthetic_quality.py`。

其余文件一律只读。若实现证明必须扩围，先发 question，双方确认并修订冻结文档后才能授权。

## 7. failure-first 与反假绿矩阵

新专项只用系统 TEMP 与合成数据，不启动服务、不访问数据库：

1. DOCX：Heading 1、表前段落、2×2 表、表后段落；断言 Markdown 精确结构与 `heading < before < table < after`。
2. DOCX：空单元格、`V|pipe`、单元格内换行；断言 `|  |`、`V\|pipe` 和折叠后的单空格。
3. DOCX：仅空段落保持既有 `（DOCX 无段落文本）`。
4. PDF：标准库生成两页文字 PDF，断言页标题和 PAGE1/PAGE2 严格顺序，且无扫描提示。
5. PDF：`pypdf.PdfWriter` 生成两页 blank，断言每页标题和诊断精确出现两次。
6. TXT/MD 代表样本保持原头部与正文。
7. TEMP 成功与异常路径均清理。

测试必须：

- 断言完整表格结构与全部锚点，不得只做片段存在；
- 用 index 严格不等式证明块/页顺序；
- 禁止 `skip`、`xfail`、`importorskip`、宽松 `or`、真实依赖存在才跑；
- 禁止 fake engine、上传 `.md` 冒充 DOCX/PDF 质量；
- failure-first 只新增测试文件，预期 DOCX 两组失败、PDF/TXT/MD 组通过；如实际数字不同须如实报告。

## 8. 串行验收

使用主仓既有 venv，先把工作目录设为实现 worktree 的 `backend`：

```powershell
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_parse_service_synthetic_quality.py --tb=short
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_parse_engines.py tests\test_parse_export.py --tb=short
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m py_compile app\services\parse_service.py
git -C .. diff --check
```

禁止 pytest xdist/并发分组、后端全量、前端 E2E、真实 CLI/模型或业务样本。

## 9. 完成定义与下一步

严格两文件实现、新专项与受影响回归、编译/diff/哈希/空暂存全部通过；Codex 独立审查后中文提交推送并闭环五份生产文档。完成后进入标书内容制作与 Word 整章导出，不扩到 OCR 或 V2/V3。
