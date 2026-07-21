<!--
模块：V1-C 本机解析运行时预检实施计划
用途：把固定诊断、零回调 dry-run、合成 DOCX 真值门拆为 Grok B 测试、Grok A 实现和 Codex 独立验收步骤。
对接：docs/v1c-local-parser-runtime-preflight-contract.md、tools/local-parser、Grok A/B 本地消息路由。
二次开发：严格测试先行和三文件白名单；问题须双方确认后才返修；Grok 不得执行 Git 写入。
-->

# V1-C 本机解析运行时预检实施计划

> **状态：已完成。** 冻结=`6e7aafb`，实现=`21d3213`；Grok 未执行 Git 写入，Codex 已独立验收并推送。
> **执行要求：** 使用 `executing-plans` 逐项执行；任何契约疑点先停止并通过消息箱确认。

**目标：** 在不安装解析器、不读取业务数据、不签票或回调的前提下，为 MinerU 默认路径和 Docling 可选路径提供诚实静态预检与显式合成样本运行门。

**架构：** 新增单一标准库 CLI，复用 P8D/P8E 已验收的可执行文件、argv、离线环境、进程和 Markdown 校验原语。`dry-run` 只做静态检查；`synthetic-check` 在 TEMP 生成含固定锚点的 DOCX 并调用已安装 CLI，始终零回调。测试全部使用注入与假 CLI，真实 CLI 门保持关闭。

**技术栈：** Python 3 标准库（`argparse`、`json`、`tempfile`、`zipfile`、`pathlib`、`unittest`），既有 P8D/P8E 助手原语，PowerShell 串行验收。

---

## 任务 1：冻结真值与工作区

**文件：**

- 阅读：`docs/v1c-local-parser-runtime-preflight-contract.md`
- 阅读：`tools/local-parser/mineru_callback_helper.py`
- 阅读：`tools/local-parser/docling_callback_helper.py`

**步骤：**

1. Codex 提交并推送本契约与计划，记录冻结提交。
2. 从冻结提交创建独立实现 worktree `C:\Users\Administrator\biaoshu-v1c-impl`，分支 `collab/v1c-runtime-preflight-impl`。
3. 核对主仓与实现 worktree 均干净；主仓 HEAD 与远端冻结提交一致。
4. 将 Grok A/B 路由都切到该实现 worktree，但任务严格串行：B 写 failure-first 时 A 不得写文件；B 完成后 A 才实现。
5. 不创建或读取 SQLite 数据库；本包纯本地工具，不需要测试数据库。

## 任务 2：Grok B 只写 failure-first

**文件：**

- 新增：`tools/local-parser/test_runtime_preflight.py`
- 禁止修改其它文件。

**步骤：**

1. 测试先从固定路径加载尚不存在的 `runtime_preflight.py`，不得复制生产逻辑到测试。
2. 写参数矩阵：默认 MinerU、两个模式互斥必选、非法引擎、Docling 缺 artifacts、MinerU 多余 artifacts。
3. 写 dry-run 反副作用测试：注入的进程启动器、票据读取器和回调函数全部为触发即失败哨兵；成功 JSON 仍须 `runtimeVerified=false`。
4. 写 DOCX 生成器测试：ZIP 必含 `[Content_Types].xml`、`word/document.xml`，文档锚点精确一次，不含绝对路径或业务样本。
5. 写两引擎 synthetic-check：假 runner 在输出根写唯一 Markdown；验证固定锚点、离线环境、命令形态、零回调和最终清理。
6. 写失败映射：CLI、artifacts、非零、超时、输出非法、缺锚点、中断、未知异常；断言精确诊断码、退出码和 JSON 六键。
7. 扫描测试源码，禁止 `skip`、`xfail`、宽松 `or`、真实 CLI 存在才断言或读取真实业务路径。
8. 运行专项，记录真实 failure-first passed/failed/did-not-run；生产脚本不存在必须失败。
9. 发送 `review_request`，含命令、数字、失败首节点、唯一文件哈希、反假绿证据；不得暂存、提交或推送。

**命令：**

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_runtime_preflight.py" -v
git diff --check
git status --short
```

## 任务 3：Codex 审查 failure-first

1. 逐测核对测试确实观察公开行为和副作用，而非只检查源码字符串。
2. 核对失败来自生产能力缺失，不是导入路径、语法、夹具或平台误差。
3. 核对测试只改一个白名单文件且暂存区为空。
4. 若发现疑似假绿或契约偏差，先发 `[GROK-B] question`；双方确认后才下发最小 test-only 返修。
5. failure-first 合格后，冻结测试 SHA-256 并向 Grok A 下发实现任务。

## 任务 4：Grok A 最小实现

**文件：**

- 新增：`tools/local-parser/runtime_preflight.py`
- 修改：`tools/local-parser/README.md`
- 只读：`tools/local-parser/test_runtime_preflight.py`
- 禁止修改其它文件。

**步骤：**

1. 新脚本先写中文四字段模块注释；公开类/函数补用途与对接。
2. 定义固定 `PreflightError`、诊断码映射与六键 JSON；所有未知异常固定 `internal_error`。
3. 实现静默参数解析；不回显原参数值。
4. 实现 engine 适配：复用对应助手的 resolve、command、env、run 和 Markdown 原语；不得复制或放宽安全规则。
5. 实现内存命令形态校验和 dry-run；确保零 TEMP 输入、零进程、零票据、零 HTTP。
6. 用标准库 `zipfile` 在 TEMP 生成最小 DOCX，正文含精确锚点；不提交二进制 fixture。
7. 实现 synthetic-check：仅 TEMP 合成输入、固定离线运行、唯一 Markdown、锚点门和完整清理；不得调用回调原语。
8. README 增加 V1-C 命令、结果解释、当前缺 CLI 的诚实口径、禁止 Agent 自动安装以及现场授权清单。
9. 运行新专项至绿，再串行运行 MinerU 54 与 Docling 46 回归、编译和 diff-check。
10. 发送 `review_request`，列精确三文件、哈希、failure-first 引用、每组数字、当前机器未运行真实 CLI 的事实和未做项；不得暂存、提交或推送。

## 任务 5：Codex 独立静态审查

1. 精确核对三文件白名单、空暂存区和测试文件哈希未被 Grok A 改动。
2. 追踪两个模式的所有调用路径，证明 dry-run 不可能到达 `Popen`、TEMP 样本、getpass 或 HTTP。
3. 追踪 synthetic-check，证明输入只能由生成器产生、输出只能在 TEMP、没有 ticket/origin/callback 参数。
4. 审查所有异常映射，禁止绝对路径、命令、环境值、Markdown、第三方 stderr 或异常类名进入 JSON。
5. 审查 Docling artifacts 与 MinerU 参数互斥，禁止忽略非法参数。
6. 扫描自动安装、下载、联网、代理继承、skip/xfail、宽松断言和真实数据路径。
7. 疑似问题只发 `question` 给对应 Grok；双方确认后再下发严格白名单返修。

## 任务 6：Codex 独立串行验收

按顺序执行：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m py_compile tools\local-parser\runtime_preflight.py
git diff --check
git status --short
```

再在当前未安装 MinerU 的机器执行静态生产探针并捕获退出码：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --dry-run
```

预期唯一正确结果：退出码 2、`ok=false`、`diagnosticCode=cli_missing`、无绝对路径。不得运行 `--synthetic-check`，不得安装或下载真实 CLI/模型。

## 任务 7：合入、提交与文档闭环

1. 由 Codex 把实现 worktree 的严格三文件复制回主仓；禁止 `git add .`。
2. 复核 SHA-256、`git diff --check`、空暂存边界后精确暂存三文件。
3. 使用中文提交信息：`实现：完成V1C本机解析运行时预检`。
4. 仅推送 `origin collab/grok-code-codex-review`，核对本地 HEAD 与远端一致。
5. 更新本契约、计划、`docs/HANDOFF-next.md`、路线图和联调清单，写明真实计数、消息 ID、提交、当前缺 CLI 和未运行真实合成门。
6. 使用中文文档提交信息：`文档：闭环V1C本机解析运行时预检`，推送并再次核对工作区为空。
7. 停止 V1-C 路由或把其 worktree 固定为只读审计证据；不得把旧白名单沿用到下一包。

## 任务 8：进入下一 V1 包

下一包重新只读审计并冻结，不直接改 V1-C：

1. lightweight DOCX 表格与标题结构；
2. 合成 PDF/DOCX 内容完整性和失败诊断；
3. 标书制作主流程与 Word 整章导出。

真实 MinerU/Docling 安装、模型下载和授权业务样本 E2E 等用户醒后另行明确授权。

## 实际完成记录

1. Grok B 原始 failure-first 为 `0 passed / 1 import error / 24 did-not-run`；B1-B7 测试缺口全部按双确认门关闭，最终测试文件 26 项。
2. Grok A 首轮实现后新专项/MinerU/Docling 为 `25/54/46 passed`；Codex 独立得到相同数字并发现 A1 空白包围 engine 被 `.strip()` 放行。
3. A1 先由 Grok B 增加真实红测：既有 25 项通过、两个 subTest 失败；再由 Grok A production-only 收紧。Codex 最终独立新专项 `26 passed`。
4. `py_compile`、diff-check、严格三文件与哈希门通过；实现提交=`21d3213`，已推送 `collab/grok-code-codex-review`。
5. 当前本机 `mineru --dry-run` 诚实返回 `cli_missing`/2；真实合成门、CLI/模型安装下载和业务样本 E2E 均未执行。
