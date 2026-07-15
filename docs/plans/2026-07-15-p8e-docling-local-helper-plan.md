<!--
模块：P8E 本机 Docling 外置解析助手实施计划
用途：把冻结契约拆成后端来源枚举与本机助手两个可独立审查、验收和提交的受限任务。
对接：docs/p8e-docling-local-helper-contract.md；Grok-Codex 消息箱；P8C/P8D 回归。
二次开发：严格测试先行；Grok 不得提交推送；P8E-A 未验收前不得开始 P8E-B。
-->

# P8E 本机 Docling 外置解析助手实施计划

> **执行要求**：使用 executing-plans 按任务顺序执行；实现由 Grok 完成，Codex 逐包审查、独立验收并唯一负责 Git。

**目标**：在不把 Docling 放进后端进程、不自动安装或联网解析的前提下，让 P8C 一次性回调精确接收 `source=docling`，并提供受限本机离线 Docling 助手。

**架构**：P8E-A 只扩 P8C 的固定来源枚举与测试；P8E-B 新增 Docling 专属 CLI，复用 P8D 已验收的输入、票据、Markdown 和回调原语，只参数化内部来源字段。两个实现包顺序派发、分别审查和提交。

**技术栈**：Python 标准库、FastAPI、SQLAlchemy、pytest、unittest、Playwright Chromium headless 单 worker。

---

## 0. 执行前门禁

1. 在仓库根运行 `git status -sb`、`git rev-parse HEAD`、`git rev-parse origin/collab/grok-code-codex-review`；必须位于 `collab/grok-code-codex-review`、本地与远端一致、工作区干净。
2. 完整阅读 `docs/p8e-docling-local-helper-contract.md`、P8C/P8D 契约、`docs/HANDOFF-next.md` §3.1 和本计划。
3. 本机未安装 Docling 是预期事实；禁止安装、下载、联网探测或用真实模型作为自动化前置。
4. 所有 PowerShell 和子进程后台静默；Playwright 只可 Chromium headless、`workers=1`、逐条串行。

## 1. P8E-A：后端固定来源枚举

### 1.1 精确文件

- 修改：`backend/app/services/local_parser_ticket_service.py`
- 修改测试：`backend/tests/test_local_parser_callback_tickets.py`

### 1.2 测试先行

1. 把既有“`docling` 非法”样例替换为 `unknown-parser`，保持未知来源 400 的原意。
2. 新增表驱动测试：精确 `mineru` 与 `docling` 允许；`Docling`、` docling`、`docling `、`docling-extra`、空值、非字符串与未知来源固定 400 且不反射输入。
3. 新增真实事务测试：先用非法来源请求同一票据并得到 400，再用该票据提交 `source=docling` 成功，第三次重放 401；证明 normalize 先于消费且成功仅一次。
4. 成功后读取 editor-state、parse task 与审计：标题/任务结果来源是 `docling`；响应仍仅 `ok/chars/taskId`；审计仍为固定 `one_time_ticket` 且不含来源、项目、文件名、正文、字符数或票据。
5. 运行失败证据：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest tests\test_local_parser_callback_tickets.py -q
```

预期：新 `docling` 成功测试因当前硬编码只允许 `mineru` 而失败；未知来源和既有 P8C 测试继续成立。

### 1.3 最小实现

1. 在 service 常量区新增不可变固定来源集合 `{"mineru", "docling"}`。
2. `normalize_callback_body()` 只把 `source_raw != "mineru"` 改为集合成员校验；不得 strip/lower，也不得修改其他字段、错误、票据或事务逻辑。
3. 文件顶注释更新为 MinerU/Docling 来源枚举，但保持“不启动解析器”的二次开发约束。
4. 重新运行上方专项，预期全部通过。
5. 运行受影响回归：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest tests\test_local_parser_callback_tickets.py tests\test_async_and_callback.py tests\test_parse_engines.py tests\test_parse_strategy_read.py -q
```

预期：全部通过；只允许既有 Starlette/httpx 弃用警告。

6. 运行 `git diff --check`，向 Codex 发送 `review_request`，报告精确两文件、失败先测、最终结果、风险与未做项；不得 `git add/commit/push`。

### 1.4 Codex 审查与提交门

1. 确认 diff 只有两文件，固定集合精确、没有字符串归一化、没有票据表/路由/旧回调扩散。
2. 复跑专项和受影响回归；检查非法来源不消费票据、Docling 成功真实入库、审计不扩字段。
3. 需要返修时只下发定点任务。通过后由 Codex 精确暂存、运行 `git diff --cached --check`、中文提交并推送协作分支。
4. P8E-A 未推送前，禁止派发 P8E-B。

## 2. P8E-B：本机 Docling 外置助手

### 2.1 精确文件

- 修改共享回调：`tools/local-parser/mineru_callback_helper.py`
- 新增：`tools/local-parser/docling_callback_helper.py`
- 新增测试：`tools/local-parser/test_docling_callback_helper.py`
- 修改说明：`tools/local-parser/README.md`

### 2.2 共享回调失败先测

1. 在 Docling 新测试中直接调用共享 body/回调构造，要求显式 `source="docling"` 产生精确 JSON；保持 MinerU 两参数默认仍为 `source="mineru"`。
2. 测试非法内部 source 在构造 Request 前固定失败，不能发网；这不是客户端可控枚举。
3. 运行：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
```

预期：因共享函数尚不接受 source 或 Docling 模块不存在而失败。

### 2.3 Docling CLI 失败先测

按契约 §4 和 §6.2 建立假 `docling.exe`/POSIX 可执行文件与假回环 HTTP。至少逐项测试：

1. Windows 只认 `.exe`，POSIX 只认普通非符号链接可执行文件；拒绝批处理、符号链接、不存在命令。
2. 输入与 `--artifacts-path` 的普通文件/目录、符号链接、空/50 MiB/扩展名边界。
3. `.pdf/.png/.jpg/.jpeg/.docx/.pptx/.xlsx` 精确映射到 `pdf/image/docx/pptx/xlsx`。
4. 完整 argv 精确等于契约固定数组，`convert` 而非 `convert-remote`，无额外参数；`shell=False`，三个标准流为 DEVNULL。
5. 子环境只保留固定系统白名单并强制 HF/Transformers offline；代理、Docling service/API/artifacts 环境、Token/API Key/票据哨兵均不存在。
6. 成功生成唯一 Markdown并只回调一次，body 精确 `source=docling`；非零/启动失败/超时/中断固定失败、终止直接子进程并清理临时根。
7. 非 TTY 固定票据错误且进程/回调均为零；stdout/stderr 不含票据、输入/模型绝对路径、正文、taskId、detail 或假 CLI 原始错误。
8. 不使用真实 Docling、不访问真实外网、不用 `read_text()`/无界 `read_bytes()`、不复制 P8D 已验收的 HTTP 实现。

### 2.4 最小实现

1. 在 `mineru_callback_helper.py` 只参数化 `build_callback_body()` 与 `post_callback()` 的内部 source，默认 `mineru`；固定允许 `mineru|docling`，入口内防御校验。P8D 现有调用无需修改行为。
2. 新增 Docling CLI：复用 P8D 的 `HelperError`、输入/Origin/filename/票据、环境白名单、终止、Markdown 读取与回调函数；不得复制整套安全原语。
3. 实现模型目录校验、`docling.exe`/POSIX 可执行文件解析、扩展名到 `--from` 映射、固定命令数组、环境剥离、30 分钟超时和临时目录流水线。
4. README 改为 P8D/P8E 双助手说明：分别列人工安装/模型准备、命令、离线边界、固定错误、无真实验收和孙进程/资源/网络残余风险；不得提供自动安装命令的执行器。
5. 运行 Docling 专项，预期全部通过；再运行 P8D 原 54 项：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
```

6. 运行 `git diff --check`，向 Codex 发送 `review_request`，报告精确四文件、失败先测、每组测试、风险与未做项；不得 `git add/commit/push`。

### 2.5 Codex 审查与独立验收

1. 先做白名单与安全审查：命令注入、URL/远程服务、插件、环境秘密、票据来源、响应无界读取、输出树/Markdown 无界读取、临时目录、超时/中断和固定错误。
2. 检查假 CLI 必须真实观察 argv/env/Popen，不能用 mock 掩盖 Windows shell 或来源字段；检查同一测试不得真实联网。
3. 独立运行 P8D、P8E 两套 unittest 和 P8E-A/P8C 后端受影响回归。
4. 后台静默、逐条串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:local-parser-callback-ticket -- --workers=1
npm run test:e2e:parse-strategy -- --workers=1
```

5. 需要返修时逐轮仅下发精确文件与问题；通过后由 Codex 精确暂存、`git diff --cached --check`、中文提交并推送。

## 3. 文档闭环

P8E-A 与 P8E-B 均推送后，Codex 更新：

- `docs/p8e-docling-local-helper-contract.md`
- 本计划
- `docs/plans/2026-07-12-bid-writer-roadmap.md`
- `docs/integration-checklist.md`
- `docs/HANDOFF-next.md`
- `tools/local-parser/README.md`（应随 P8E-B 实现提交完成）

文档必须区分“假 CLI 自动化通过”与“真实 Docling/模型未安装、未验收”，记录精确提交、测试数、继承的全量基线、残余风险和下一主线包。Codex 中文提交并推送后，再核对本地 HEAD、远端 SHA 和干净工作区；长期目标保持 active。
