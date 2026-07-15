<!--
模块：P8D 本机 MinerU 外置解析助手契约
用途：冻结用户显式选择本地文件、离线调用已安装 MinerU CLI、再用 P8C 一次性票据回传 Markdown 的最小生产链。
对接：P8/P8B 可插拔解析与策略；P8C 一次性回传票据；tools/local-parser/mineru_callback_helper.py。
二次开发：不得把外部解析器塞入后端进程，不得自动安装/下载模型，不得让票据进入参数、环境、文件、日志或非回环网络。
-->

# P8D 本机 MinerU 外置解析助手契约

> **状态**：已完成、独立验收并推送。计划=`30d066f`，实现=`e1fe316`。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收结果**：助手单测 54 passed；后端 P8C/P8B/解析受影响回归 35 passed；前端 lint/build、P8C E2E 9 passed、P8B E2E 6 passed。后端 487/前端全量 184 基线未重跑也未改变。

## 1. 现状与方案选择

现有后端 `parse_engines.py` 明确禁止 subprocess、shell、网络和外部二进制；P8B 的 `local` 只导航到回传页，P8C 只授权外部助手向固定回环后端回传解析后的 Markdown，不提供原文件读取或下载。因此真实解析器必须保持为独立本机进程，不能在 API 请求、任务线程或浏览器中启动。

本包选择一个纯标准库 Python 助手：用户先在浏览器为目标项目显式签发 P8C 单次票据，再在本机终端显式选择原文件；助手只调用 PATH 中已安装的 `mineru`，从临时目录读取唯一 Markdown，最后向固定 P8C 回调提交。它不改后端、前端、数据库或既有 `light/local/ask`。

官方依据：

- MinerU 官方 CLI 使用 `mineru -p <input_path> -o <output_path>`；CPU 兼容路径可用 `-b pipeline`。未指定 `--api-url` 时 CLI 会临时启动本机 `mineru-api`：<https://opendatalab.github.io/MinerU/zh/usage/quick_usage/>。
- 离线本地模型必须显式设置 `MINERU_MODEL_SOURCE=local`；模型下载是独立人工步骤：<https://opendatalab.github.io/MinerU/zh/usage/model_source/>。

拒绝方案：

1. 后端注册 subprocess engine：破坏 P8 冻结边界，外部进程超时、资源和路径风险进入 API 服务；
2. 浏览器上传原文件给外部解析服务：扩大正文出域和网络面；
3. 自动安装 MinerU 或下载模型：依赖体积、许可确认、磁盘和网络不可控；
4. 同包支持 Docling：P8C 当前 `source` 只允许精确 `mineru`，冒用该值会形成错误审计；Docling 必须另立回调枚举和安全契约。

## 2. 唯一用户流程

1. 用户在 `/local-parser?projectId=<当前项目>` 显式签发 P8C 10 分钟单项目单次票据。
2. 用户在本机运行助手并通过 `--input` 显式传入一个本地源文件；助手使用不回显输入的安全提示读取票据。
3. 助手验证本机 MinerU、输入、回环后端 Origin 和运行边界后，在系统临时目录调用一次 MinerU pipeline。
4. MinerU 成功且临时目录中恰有一个合法 Markdown 时，助手向 `<回环 Origin>/api/local-parser/callback` 发送一次请求。
5. P8C 成功消费票据后，助手只输出固定中文成功信息；票据随即不可重放。失败时不自动重试回调，避免不确定状态下重复消费。

助手不读取项目、Cookie、CSRF、浏览器存储、仓库 `.env`、上传目录或数据库；项目绑定只来自票据。

## 3. 输入与可执行文件边界

- `--input` 必须是已存在的单个普通文件，解析绝对路径后仍为文件；拒绝目录、空文件、符号链接和大于 50 MiB 的文件。
- 扩展名只允许 MinerU 官方支持且与当前标书源文件相关的 `.pdf/.png/.jpg/.jpeg/.docx/.pptx/.xlsx`，大小写不敏感；不根据 MIME、客户端正文或 URL 扩展。
- 可执行文件只通过 `shutil.which("mineru")` 从当前受信 PATH 解析；不提供 `--executable`、环境覆盖、任意命令模板或 shell 字符串。
- 固定命令语义为 `mineru -p <绝对输入文件> -o <系统临时目录> -b pipeline`，参数数组调用且 `shell=False`；不得追加 `--api-url`、远程模型、插件或用户自定义额外参数。
- 助手不得执行 `pip/uv/conda`、`mineru-models-download`、浏览器、PowerShell、批处理或任何安装/下载命令。未安装 CLI/模型时固定失败并提示人工阅读官方文档。

## 4. 离线、进程与临时文件治理

- MinerU 子进程不得复制整个父环境，只可从固定白名单保留 `PATH/SystemRoot/WINDIR/USERPROFILE/HOME/APPDATA/LOCALAPPDATA/TEMP/TMP/TMPDIR/LANG/LC_ALL` 中实际存在的值，再强制加入 `MINERU_MODEL_SOURCE=local`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。不得继承任何代理、API Key、票据、回调 Header 或其他业务配置。
- 固定最长运行 30 分钟；超时、Ctrl+C 或助手异常必须终止当前 MinerU 进程，等待短暂退出后必要时强制结束。不得后台遗留助手自身；无法证明的孙进程治理风险必须在 README 如实写明。
- MinerU stdout/stderr 固定丢弃，不写内存、磁盘或控制台；助手成功输出和固定失败信息不得回显 MinerU 原始输出、源文件绝对路径、Markdown、票据或服务端 detail。
- 输出只写 `tempfile.TemporaryDirectory`；无论成功、失败、超时或中断都清理。不得在仓库、源文件目录、用户桌面或固定 data/cache 目录写助手产物。
- 输出树目录项与文件项合计最多 4096；递归找到的 `.md` 必须恰好一个、为普通非符号链接文件、位于临时根内。读取前先校验文件字节不超过 2 MiB，再以 `2 MiB + 1` 二进制有界读取；去首尾空白后长度 1–1,000,000 Unicode 码点，最终 JSON UTF-8 body 仍不超过 P8C 的 2 MiB。零个、多个、越界或空 Markdown 均固定失败且不回调。

## 5. 票据与回调边界

- 票据只能通过 `getpass` 安全提示读入当前进程内存；禁止命令行参数、环境变量、stdin 管道模式、配置文件、临时文件、日志或剪贴板。
- 后端仅允许 `--backend-origin`，默认 `http://127.0.0.1:8000`；只接受 `http|https`、主机精确为 `127.0.0.1|localhost|::1`、显式或默认合法端口，且不得含用户名、密码、路径、查询或 fragment。
- 回调路径由助手固定拼接 `/api/local-parser/callback`，用户不能覆盖。请求必须绕过代理，禁止重定向；任何 3xx、非 2xx、超时、TLS 或 JSON 异常均固定失败。
- Header 精确包含 `Content-Type: application/json` 与 `X-Local-Parse-Ticket: <内存票据>`；JSON 精确为 `markdown/source/filename`，其中 `source="mineru"`、`filename` 只用输入文件 basename，且 basename 必须满足 P8C 的 1–255 码点及 CR/LF/NUL/斜杠禁令。
- 只允许一次回调尝试，不自动重试。成功响应必须为 JSON 对象且 `ok=true`、`chars` 为非负整数、`taskId` 为非空字符串；助手不打印 taskId。
- 自定义无重定向、无代理 opener 只能连接已验证的回环 Origin；测试必须证明票据和正文不会被 30x 转发到其他路径或主机。

## 6. 明确非目标

- 不安装、打包、升级或下载 MinerU/模型，不承诺任意机器已具备运行条件；
- 不接 Docling、远程 MinerU API、Docker/WSL2、GPU/VLM、多文件/目录批处理或服务常驻治理；
- 不改后端 P8/P8C、回调 schema、票据 TTL、认证中间件、数据库、任务或 editor-state；
- 不改前端签发页，不自动取得票据、项目 ID 或源文件，不监听浏览器；
- 不保存解析历史、临时产物、日志、配置、票据或失败重试队列；
- 不把本助手注册成 `parse_engines` 的 `mineru` engine，不让 `parseStrategy=local` 自动启动进程。

## 7. 验收底线

自动化必须用完全假的 `mineru` 进程和回环假 HTTP，不要求安装 MinerU、模型或访问网络。至少覆盖：PATH 缺失；输入扩展/目录/符号链接/空/50 MiB 上下界；命令数组与 `shell=False`；离线环境和代理剥离；成功/失败/超时/中断清理；Markdown 零个/多个/空/越界；票据不进 argv/env/文件/输出；Origin 白名单、固定路径、无代理、拒绝重定向；请求 Header/body 精确；服务端错误脱敏；一次回调零重试；成功固定中文。

Codex 还须独立回归 P8C 后端票据专项、P8B/P8C 前端 E2E、lint/build，并运行 `git diff --check`。所有 PowerShell 与测试进程后台静默；Playwright 继续 Chromium headless、单 worker、逐条串行。

## 8. 交付与审查结论

- Grok 首版严格保持三文件边界，但 Codex 首轮审查发现 `getpass` 非 TTY 管道降级、Windows `.cmd` 的 shell 假绿、HTTP 响应无界读取和非法端口错误分类；返修后再发现 Markdown 在码点校验前无界读取，遂进行第二轮定点返修。
- 最终助手只接受交互 TTY 中 43 字符 P8C 票据；Windows 只认 `mineru.exe`，POSIX 只认普通非符号链接可执行文件；回调响应上限 64 KiB，非 2xx 不读错误正文；Markdown 与输出树均在读取前有硬上限。
- Codex 独立通过助手单测 54 项、后端受影响回归 35 项、P8C E2E 9 项、P8B E2E 6 项以及 lint/build；暂存区 `git diff --cached --check` 通过。
- 实现提交 `e1fe316` 已推送协作分支。真实 MinerU CLI/本地模型仍需用户按官方文档人工安装准备；未用真实模型样本验收，Docling、安装器、常驻服务和孙进程完整治理仍是后续独立事项。
