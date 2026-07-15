<!--
模块：P8D 本机 MinerU 外置解析助手说明
用途：说明人工安装 MinerU/模型、P8C 签票、复制即用命令、离线边界、失败排查与已知风险。
对接：tools/local-parser/mineru_callback_helper.py；docs/p8d-mineru-local-helper-contract.md；P8C 回调。
二次开发：不提供自动安装器；不承诺清理 MinerU 自建孙进程；不把票据写入参数/环境/文件/日志。
-->

# 本机 MinerU 外置解析助手（P8D）

本目录是**独立本机工具**，不嵌入后端进程。用户在浏览器为目标项目签发 P8C 一次性回传票据后，在本机**交互终端**显式选择源文件，由助手调用 PATH 中已安装的 MinerU，再向本机回环后端提交解析后的 Markdown。

## 前置条件（须人工完成）

1. **安装 MinerU CLI（Windows 必须是 `mineru.exe`）**
   按官方文档在本机安装，确保终端中可被 PATH 解析。
   - **Windows**：助手在 `shutil.which("mineru")` 之后**只接受**解析结果为 `.exe` 的普通文件；
     **拒绝** `mineru.cmd` / `.bat` / `.com` / 无后缀包装（即便 `shell=False`，批处理仍可能经命令解释器，存在参数注入风险）。
   - **POSIX**：只接受可执行的普通非符号链接文件。
   参考：<https://opendatalab.github.io/MinerU/zh/usage/quick_usage/>

2. **准备本地模型（离线）**
   本助手对子进程强制 `MINERU_MODEL_SOURCE=local`、`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，**不会**代为下载模型。
   请按官方「模型来源」说明事先完成下载与配置：
   <https://opendatalab.github.io/MinerU/zh/usage/model_source/>

3. **磁盘与硬件**
   CPU/内存/磁盘需求以 MinerU 官方文档为准；本助手不评估、不预留资源。

4. **本机后端已启动**
   默认回调 Origin 为 `http://127.0.0.1:8000`。须能访问
   `POST /api/local-parser/callback`（P8C 公开回调）。

5. **在浏览器签发票据**
   打开 `/local-parser?projectId=<当前项目>`，以有权限的用户签发 **10 分钟、单项目、单次** 票据。
   票据形态与后端 `secrets.token_urlsafe(32)` 一致：**恰好 43 个** ASCII URL-safe 字符 `[A-Za-z0-9_-]`。
   票据只显示一次，请在**交互式终端**粘贴到助手提示中（输入不回显）。
   **管道/重定向 stdin（非 TTY）会被拒绝**，且不会调用 `getpass` 回退读管道。

## 复制即用

必须在交互式终端中运行（PowerShell/CMD 窗口、本地 SSH 伪终端等），**不要** `echo ticket | python ...`：

```powershell
# 使用仓库内后端 venv 的 Python 即可（仅标准库，无额外 pip 依赖）
backend\.venv\Scripts\python.exe tools\local-parser\mineru_callback_helper.py --input "D:\标书\某文件.pdf"
```

可选指定回环 Origin（仅 `http`/`https` + `127.0.0.1` / `localhost` / `::1`，端口须合法 1–65535）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\mineru_callback_helper.py `
  --input "D:\标书\某文件.pdf" `
  --backend-origin "http://127.0.0.1:8000"
```

运行后会出现**不回显**的票据提示，粘贴 P8C 票据并回车。

成功时仅打印：

```text
本地解析回传成功
```

失败时仅打印固定中文原因（不含票据、taskId、源绝对路径、Markdown 或服务端 detail）。

## 行为边界（摘要）

| 项目 | 行为 |
|------|------|
| 可执行文件 | 仅 `shutil.which("mineru")`；Windows 仅 `.exe`；不可指定任意路径或远程 API |
| 命令 | 固定 `mineru -p <绝对文件> -o <临时目录> -b pipeline`，`shell=False` |
| 输入 | 单文件；扩展名 `.pdf/.png/.jpg/.jpeg/.docx/.pptx/.xlsx`；非空且 ≤ 50 MiB；拒绝符号链接与目录 |
| 票据 | **仅**交互 TTY + `getpass`；固定 43 字符 URL-safe；禁止命令行参数、环境变量、管道、配置文件、剪贴板 |
| 回调 | 固定拼 `/api/local-parser/callback`；入口内再次归一化 Origin 并校验票据；无代理、无重定向、**只请求一次**、失败不重试 |
| 响应体 | 成功响应最多读取 64 KiB；超限固定「回传响应无效」；HTTPError/非 2xx **不**整包 `read()` |
| 环境 | 子进程不继承完整父环境；白名单系统变量 + 强制离线变量；剥离代理与 API Key |
| 输出 | 仅系统临时目录；无论成功/失败/超时/中断均清理；stdout/stderr 丢弃 |
| Markdown | 临时根内恰好一个合法 `.md`；**读取前**校验普通非 symlink、仍在临时根、文件字节 1～2 MiB，再二进制有界读取（禁止无界 `read_text`）；输出树目录项+文件项合计 ≤ 4096，第二个 `.md` 立即失败；正文 1～1,000,000 码点；最终 JSON UTF-8 body 再次 ≤ 2 MiB |

## 明确不做

- 不自动安装/升级 MinerU，不执行 `pip`/`mineru-models-download`
- 不支持 Docling、多文件批处理、远程 MinerU API、Docker/WSL 编排
- 不读取浏览器 Cookie/CSRF、仓库 `.env`、数据库或上传目录
- 不把本工具注册为后端 `parse_engines` 引擎，也不由 `parseStrategy=local` 自动拉起
- 不接受管道票据，不把 Windows 批处理包装当作安全可执行文件

## 失败排查

1. **未找到 MinerU 命令**
   检查 PATH；Windows 确认解析到的是 **`mineru.exe`**，而不是 `mineru.cmd`。
   在同一终端执行官方 CLI 自检；按文档安装 CLI 与模型。

2. **输入文件无效**
   确认是单文件、扩展名在白名单、非空、不超过 50 MiB、不是快捷方式/符号链接。

3. **回传票据无效**
   常见原因：非交互终端、票据长度/字符集不符、票据过期或已使用、误用管道传入。
   请在交互终端重新签发并粘贴，**不要** `echo`/`type` 管道喂入。

4. **MinerU 解析失败 / 超时**
   默认最长 30 分钟。查看本机 MinerU 自身是否能对同一文件跑通；助手不回显 MinerU 原始日志。模型未就绪时会失败。

5. **未找到合法的唯一 Markdown 输出**
   MinerU 未产出恰好一个 `.md`，或 `.md` 在读取前已超文件字节上限（2 MiB）、输出树条目超限（目录项+文件项合计 4096）、正文为空/码点过大、或最终 JSON body 超限。可先在命令行单独验证 MinerU 输出结构。

6. **后端地址无效**
   Origin 必须是回环地址，合法端口 1–65535，且不能带路径、查询、fragment 或用户名密码。
   非法端口（如 65536）、缺括号 IPv6 等均报此错误，而不是「回传失败」。

7. **回传失败 / 回传响应无效**
   常见原因：后端未启动、端口不对、响应不是 `{ok:true, chars, taskId}`、响应体超过 64 KiB。
   请重新在页面签发票据，**不要**让助手自动重试（避免不确定消费）。

## 临时文件与安全

- 助手使用 `tempfile.TemporaryDirectory`，正常退出路径会删除临时根。
- 强制结束操作系统进程（如任务管理器直接杀 Python）时，极端情况下临时目录可能残留，可手动清理系统 Temp。
- 票据只存在于当前进程内存与 HTTPS/HTTP 请求头；不得写入日志、截图或共享终端回放。
- 回调响应有固定读取上限，避免恶意/异常超大响应拖垮本机内存。

## 已知风险：MinerU 孙进程

未指定 `--api-url` 时，官方 CLI 可能临时拉起本机 `mineru-api` 等**孙进程**。
本助手会在超时/中断时终止**直接子进程**并清理临时目录，但**无法证明**一定能回收 MinerU 自行拉起的全部孙进程。若本机出现残留进程或端口占用，请按 MinerU 官方说明与系统工具人工处理。这不属于本助手的自动安装或服务常驻治理范围。

## 自测（开发者）

使用假 MinerU / 假 HTTP，不要求安装真实 MinerU：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_*.py" -v
```

## 相关文档

- 契约：`docs/p8d-mineru-local-helper-contract.md`
- 实施计划：`docs/plans/2026-07-15-p8d-mineru-local-helper-plan.md`
