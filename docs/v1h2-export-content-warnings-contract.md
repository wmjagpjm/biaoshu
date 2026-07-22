<!--
模块：V1-H2 技术标导出正文完整性提醒契约
用途：让历史、手工或部分生成残留的空章在 Word 导出成功时可见，不再静默交付。
对接：技术标 DOCX 组装、export 任务 result、技术标导出页与 H2 后端/前端专项。
二次开发：提醒非阻断；不得复用 imageWarnings、回显章节内容/标识或增加字数硬门。
-->

# V1-H2 技术标导出正文完整性提醒契约

> **状态：已完成、独立验收并推送。**
> **分支：** 仅 `collab/grok-code-codex-review`，严禁操作 `main`。
> **冻结：** `73a76c6`；**实现：** `0047a46`；V1-A 至 V1-H2 已完成并推送。

## 1. 问题真值

V1-H1 已阻止新的模型空白输出谎报生成成功，但不能消除历史空章、用户手工清空或多章任务中途失败后保留的空章。当前技术标导出：

1. `build_docx_bytes()` 对空正文写入“（本章暂无正文）”，仍生成 Word；chapters 为空时甚至不输出“四、正文”；
2. `_run_export()` 仍把项目置为 `exported`，任务 result 只有文件定位、大小、模式与 `imageWarnings`；
3. 技术标页面只展示图片降级告警，用户可能把“导出成功”误解为正文已完整；
4. 现有测试未证明空章必须产生正文完整性提醒，也未证明合法短章和有正文的 pending 章不得误报。

Grok B/A 只读审计分别为 `msg_6a454f4b90aa42a1aba2838171dd9cf8`、`msg_428291552f784462864bd947aafda9d5`；Codex 拆包裁定=`msg_fea93b71c9f14b9d9660a578c294292a`。

## 2. 产品与协议裁定

1. **允许导出并提醒。** H2 不硬阻断草稿导出，不改变 export success、文件落盘、项目 `exported` 或下载语义。
2. **新增独立字段。** export result 必须始终包含 `contentWarnings: string[]`；不得把正文告警塞入 `imageWarnings`，商务标固定为空数组。
3. **只按正文判空。** `str(body or "").strip()==""` 才算空章；不依据 `status`、`wordCount`、`targetWords` 或比例判断。合法短章和有正文的 pending 章零告警。
4. **固定有限脱敏。** 技术标无有效章节时只返回一条固定提醒；存在空章时只返回一条含空章数量的固定提醒。禁止标题、章节 ID、正文、文件名、路径、模型错误或项目/用户信息。
5. **Word 保持现状。** 空章仍写“（本章暂无正文）”，无章节仍不输出正文区；H2 只补任务结果和浏览器可发现性。

## 3. 固定后端语义

- 有至少一个有效章节字典且 N 个正文为空时：

  `正文存在 N 个空章节，导出的 Word 已保留空章占位，请补充后再定稿。`

- chapters 缺失、非列表、空列表或没有有效章节字典时：

  `当前没有可导出的正文章节，导出的 Word 不包含正文部分，请补充后再定稿。`

- 全部有效章节正文非空时：`contentWarnings=[]`。
- 商务标不扫描技术章节，固定 `contentWarnings=[]`。
- 同一导出最多一条正文提醒；未来扩展仍须遵守前端最多 20 条、每条最多 240 Unicode 码点的防御性收敛。

## 4. 前端展示与所有权

- 新建独立 `ExportContentWarnings`，标题固定“正文完整性提醒”；只以 React 文本节点展示，不解析 HTML/Markdown/URL，不提供链接。
- 只接入技术标导出页；商务标本包不新增正文提醒 UI。
- 每次新导出启动时同步清空旧正文提醒；成功且当前项目、准备令牌、告警 generation 均匹配时，先设置图片与正文提醒，再继续既有 Blob 下载。
- A 导出迟到 success、A→B、A→B→A 或同项目旧导出均不得污染当前页面；下载失败不应抹掉已接受的正文提醒。
- 非数组、非字符串、空白项、超 20 条或超 240 码点的恶意任务结果必须安全收敛；不得进入 HTML、storage、URL、console 或网络。

## 5. 严格文件白名单

后端生产/测试：

1. `backend/app/services/export_service.py`；
2. `backend/app/services/task_service.py`；
3. 新增 `backend/tests/test_v1h2_export_content_warnings.py`。

前端生产/测试：

4. 新增 `frontend/src/shared/components/ExportContentWarnings.tsx`；
5. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`；
6. 新增 `frontend/e2e/export-content-warnings.spec.ts`。

禁止修改商务标页面、`ExportImageWarnings.tsx`、V1-E 保存门、V1-F 下载 helper、pipeline、API schema、数据库、迁移、依赖、配置或其它测试。扩围必须先走 Codex question 与 Grok 只读确认。

## 6. failure-first 与反假绿矩阵

后端使用 pytest 临时数据库、TEMP 导出根和合成章节：

1. 两章一实一空：export success、Word 同时含有效锚点与空章占位，`contentWarnings` 精确为 N=1 固定句。
2. 空串与混合空白共两章：N=2；告警不得含标题、ID、正文、路径或项目名。
3. chapters 空/缺失：固定“没有可导出正文章节”提醒，Word 无“四、正文”。
4. 合法短章“无。”及 `status=pending` 的非空章：零提醒，Word 正常生成。
5. 商务标 export：`contentWarnings=[]`，既有商务正文与下载不变。

前端使用真实技术标页面、受控任务/下载 route：

6. 成功 export 的合法正文提醒先展示再发生一次下载；图片提醒可同时存在且语义分离。
7. 恶意/过量/超长 `contentWarnings` 收敛为最多 20 条、每条 240 码点的纯文本，零 HTML 注入。
8. 下一次干净导出在任务启动时清空旧提醒，成功后保持空态。
9. A→B 迟到 export success 零正文提醒、零旧下载，B 后续导出正常。

禁止 `skip/xfail/importorskip`、宽泛 `or`、源码扫描、真实业务数据、固定 sleep、条件假绿或把图片组件文本冒充正文提醒。生产未改时后端与前端新增行为必须真实失败；实际红绿数字如实记录。

## 7. 分级验收

严格串行：

```powershell
cd C:\Users\Administrator\biaoshu-v1h2-export-content-warnings-impl\backend
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_v1h2_export_content_warnings.py --tb=short
C:\Users\Administrator\biaoshu\backend\.venv\Scripts\python.exe -m pytest -q tests\test_parse_export.py tests\test_project_images.py tests\test_export_download_filename.py --tb=short

cd ..\frontend
npx playwright test e2e/export-content-warnings.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-image-warnings.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-latest-editor-state.spec.ts --workers=1 --retries=0
npx playwright test e2e/export-robust-download.spec.ts --workers=1 --retries=0
npm run lint
npm run build
git -C .. diff --check
```

禁止并发 pytest/Playwright、后端全量或整仓 318 E2E。E2E 只用 worktree 相对测试库；确认 8010/5174 无监听后才能启动，结束后必须清理。

## 8. 非目标

本包不硬阻断导出、不自动补写、不评估极短章/文风/事实/合规质量、不改变 DOCX 占位或版式、不扩商务标、不改图片告警、下载链、保存门、任务协议通用 schema、数据库、V2 多人协作或 V3 SaaS。

## 9. 完成证据

### 9.1 failure-first 与双确认返修

- 后端原始 failure-first 为 **5 failed / 0 passed**；前端原始 failure-first 为 **4 failed / 0 passed**。
- 前端全页链接断言会误伤应用壳 21 个正常导航链接。Codex question=`msg_b3c4e06c956d4e47b6abd3d2f028495e`，Grok B 确认=`msg_9f179d47009548a39d1f4c09940504a4`，test-only 返修 task/review=`msg_834198ea12004c648c68f39c714cf746`/`msg_12330eb5542143689c4eb433fbb9254f`；最终只检查正文提醒区域零链接、零图片及恶意 HTML 精确文本。
- API 合法接受没有有效章节字典的混合列表，旧实现仍会生成“四、正文”空壳标题。加固红测真实为 **1 failed / 5 passed**；Codex question=`msg_8079e82ce9b94a06969bed56103a6610`，Grok A 确认=`msg_de0a293bf55d40ad8fba3a01a6cec5cc`，test-only task/review=`msg_9790521a4f444ac39130aa0ec686199f`/`msg_7c5f2171e9e14aa793160c3eab7c2cfb`。
- API 回读测试中的宽泛 `or` 经 `msg_f8c6b96bd34c4f0faeffcdd0e07a6f7c`/`msg_aaa1600b2f9a408d9f91042f037bf87f` 双确认后，改为精确断言；返修 task/review=`msg_7d4f4c8a5447459d8f2c5675bc627bab`/`msg_b7dbe4efafbd4d5fbf00d8091fc990f8`。
- 生产仅在双方确认后返修，task/review=`msg_aa3881ec42674ea9b74ea66ecab64681`/`msg_3d9e21df0c4a46ffa87293d7d2f5474e`。

### 9.2 Codex 独立验收

- 后端 H2 专项 **6 passed**，定向回归 **35 passed**。
- 前端 H2 专项、图片告警、导出前最新编辑态、稳健下载分别为 **4/4/18/14 passed**；Playwright 全部单 worker、零重试串行。
- `npm run lint` 为 0 error、4 条既有 warning；`npm run build` 通过，仅既有包体积 warning；`py_compile` 与 `git diff --check` 通过。
- 8010/5174 已清理；严格六文件、空暂存区通过。未运行后端全量或整仓 318 E2E。

### 9.3 实现 worktree 验收哈希

以下为 H2 实现 worktree 在验收时的磁盘 SHA-256。Windows 主工作树启用 `core.autocrlf=true`，新文件在不同 worktree 的 LF/CRLF 检出会改变磁盘哈希；跨工作树核验应以提交 `0047a46` 的 Git blob 和零代码 diff 为准，不得仅因行尾转换判定代码被修改。

```text
backend/app/services/export_service.py
5ECFB427591AFBEAA70BA7AF34664F56D1A7D346F00600B5ABA1929B9FFFE898
backend/app/services/task_service.py
BD1A14975180F2195118BE3FB4AD1E31FB02EFDF0FC3E6B8D68702E0C7B8B4B0
backend/tests/test_v1h2_export_content_warnings.py
B60852171AD333155F2F4C6B542F33A759DB64F95A887EA6CA748612D1157588
frontend/src/shared/components/ExportContentWarnings.tsx
D09FE60E80C13891881385A15A8C8F7BDDFA4B314D9F1C7CFA0A434D7FA1AB1A
frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx
86FDF5CB6B88DF6A552EB8517219624AD4F8A145D4C9DEDA4E41DEDE1F13C5D4
frontend/e2e/export-content-warnings.spec.ts
8FE7725C9E1D543B0E0060401941682EC7DD8397A8AB178EA2A6D083B15168B9
```
