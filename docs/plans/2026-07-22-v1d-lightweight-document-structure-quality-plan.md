<!--
模块：V1-D lightweight 文档结构质量实施计划
用途：把 DOCX 块顺序/标题/普通表格和合成 PDF 回归拆为 Grok B failure-first、Grok A 实现和 Codex 验收。
对接：docs/v1d-lightweight-document-structure-quality-contract.md、parse_service.py、Grok A/B 本地路由。
二次开发：严格两文件白名单；测试先行；问题须双方确认后返修；Grok 禁止 Git 写入。
-->

# V1-D lightweight 文档结构质量实施计划

> **执行要求：** 使用 `executing-plans` 逐项执行；任何范围或测试疑点先通过消息箱确认。

**目标：** 用现有 python-docx/pypdf 修复 lightweight DOCX 表格丢失与标题降级，并用合成 DOCX/PDF 锁定结构质量。

**架构：** `parse_service.py` 的 DOCX 分支改为按 Word body 子节点顺序生成 Markdown；PDF 生产路径不改。新专项在 TEMP 生成二进制样本，直接调用纯函数，不经过数据库、API 或服务。

**技术栈：** Python 3、python-docx、pypdf、pytest、标准库 tempfile/zip/PDF 字节构造。

---

## 任务 1：冻结与独立实现 worktree

1. Codex 提交推送契约/计划，记录冻结提交。
2. 从冻结提交创建 `C:\Users\Administrator\biaoshu-v1d-impl`，分支 `collab/v1d-lightweight-impl`。
3. A/B 路由切到新 worktree；B failure-first 期间 A 不写文件。
4. 本包不需要测试数据库，不创建服务端口或 SQLite。

## 任务 2：Grok B 只写 failure-first

**唯一可写：** `backend/tests/test_parse_service_synthetic_quality.py`。

1. 先写中文四字段模块注释和集中 ASCII 锚点。
2. 用 python-docx 生成 Heading 1→表前段→2×2 普通表→表后段；断言精确 Markdown 和块顺序。
3. 生成空单元格/管道符/换行表；断言稳定转义与列位。
4. 生成空 DOCX、TXT、MD 回归。
5. 用标准库生成可被 pypdf 提取的两页文字 PDF；不得依赖 reportlab。
6. 用 PdfWriter 生成两页 blank PDF；断言精确扫描提示两次。
7. 所有 TEMP 根显式跟踪并在测试后不存在；扫描测试源禁止跳过/预期失败/宽松断言。
8. 运行新专项并发送真实 failure-first review_request；不得修改生产、暂存、提交或推送。

## 任务 3：Codex 审查红测

1. 核对首红来自表格/Heading 能力缺失，不是 PDF 生成器、导入路径或夹具错误。
2. 核对 PDF/TXT/MD 现有能力真实通过，DOCX 两组真实失败。
3. 核对表格断言包含结构、全部锚点、顺序和转义；TEMP 清理不允许空列表假绿。
4. 疑似问题先 question；双方确认后才授权 test-only 返修。
5. 合格后冻结测试 SHA-256，向 A 下发生产任务。

## 任务 4：Grok A 最小生产实现

**唯一可写：** `backend/app/services/parse_service.py`。测试文件严格只读。

1. 更新文件顶与公开函数中文注释，说明 DOCX 顺序和复杂版式边界。
2. 添加内部段落渲染：Heading 1–6 → Markdown 标题，其它非空文本保持普通块。
3. 添加内部单元格规范化：strip、空白折叠、pipe 转义。
4. 添加内部普通表格渲染：第一行表头、分隔行、最大列数补空、空表固定占位。
5. 用 python-docx 的 Paragraph/Table 包装 `document.element.body` 子节点并按顺序输出。
6. 保持 txt/md/pdf/unknown 与头部原样；不改 API、引擎或依赖。
7. 串行运行新专项、parse_engines+parse_export 回归、py_compile、diff-check。
8. 发送 review_request，列两文件哈希、真实数字、风险和未做项；不执行 Git 写入。

## 任务 5：Codex 独立审查与验收

1. 核对严格两文件、测试哈希未被 A 修改、暂存区为空。
2. 静态追踪 body 子节点顺序；拒绝 paragraphs 后追加 tables。
3. 审查表格列数、空格、pipe 和空表，不接受合并单元格能力的虚假声明。
4. 复跑契约 §8 命令；核对无数据库、网络、服务和真实样本副作用。
5. 发现问题按 question→confirm→task→review_request 双确认闭环。

## 任务 6：提交、推送与文档闭环

1. Codex 精确暂存两文件，中文提交：`实现：完成V1D轻量文档结构质量门`。
2. 只推送 `collab/grok-code-codex-review` 并核对本地=远端。
3. 更新契约、计划、HANDOFF-next、路线图、联调清单，写入真实数字、消息链、提交与哈希。
4. 中文文档提交：`文档：闭环V1D轻量文档结构质量门`，推送后保持主工作区为空。
5. V1-D worktree 只保留证据；下一包不得沿用白名单。

## 任务 7：继续 V1 产品主线

V1-D 后优先审计标书制作主流程当前断点和 Word 整章导出；OCR、真实外置解析器安装、跨项目协作与公网 SaaS 继续后置。
