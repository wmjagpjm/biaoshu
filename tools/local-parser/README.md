<!--
模块：P8D/P8E 本机外置解析助手与 V1-C 运行时预检说明
用途：说明 MinerU 与 Docling 双助手的人工安装、模型准备、复制即用命令、离线边界、失败排查与残余风险；并说明 V1-C 预检入口。
对接：runtime_preflight.py；mineru_callback_helper.py；docling_callback_helper.py；P8C 一次性回调；docs/p8d / docs/p8e / docs/v1c 契约。
二次开发：不提供自动安装器；不承诺清理解析器自建孙进程；不把票据写入参数/环境/文件/日志；真实模型未验收不得冒充自动化通过。
-->

# 本机外置解析助手（P8D MinerU / P8E Docling）与 V1-C 预检

本目录是**独立本机工具**，不嵌入后端进程。用户在浏览器为目标项目签发 P8C 一次性回传票据后，在本机**交互终端**显式选择源文件（Docling 还需本地模型目录），由助手调用 PATH 中已安装的解析 CLI，再向本机回环后端提交 Markdown。

| 助手 / 入口 | 脚本 | 回调 `source` / 角色 |
|------|------|----------------|
| P8D MinerU | `mineru_callback_helper.py` | `mineru`（默认业务回传） |
| P8E Docling | `docling_callback_helper.py` | `docling`（显式业务回传） |
| V1-C 预检 | `runtime_preflight.py` | **不回调**；仅静态检查或合成样本真值门 |

两条业务路径共用已验收的输入校验、TTY 票据、Origin 归一化、Markdown 有界读取与无代理/无重定向回调原语；**不得**把 Docling 冒充成 MinerU，也不得由浏览器或后端自动拉起本助手。V1 默认解析器为 **MinerU**；Docling 为管理员显式选择的可选路径，V1 不要求安装。

---

## V1-C：本机解析运行时预检（推荐先跑）

在安装/验收真实 CLI 之前或之后，用标准库入口做**诚实预检**。预检**不签发、不读取、不消费**一次性票据，**不访问后端**，**不读取**真实标书、`uploads`、数据库或密钥。

### 模式与成功语义

| 模式 | 行为 | 成功时 |
|------|------|--------|
| `--dry-run` | 仅静态：解析可执行文件类型、Docling artifacts 目录、内存命令形态 | `ok=true`、`diagnosticCode=static_ready`、`runtimeVerified=false`；消息含「尚未运行」 |
| `--synthetic-check` | 在 TEMP 生成含固定锚点的最小 DOCX，离线跑已安装 CLI，校验唯一 Markdown 含锚点 | `ok=true`、`diagnosticCode=synthetic_passed`、`runtimeVerified=true` |

成功**不**表示 OCR/表格/整本标书质量已验收。`dry-run` **绝不**启动子进程、不创建业务样本、不 HTTP。

### 复制即用命令

默认 MinerU 静态检查（本机未安装时预期 `cli_missing`、退出码 2，这是正确生产真值）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --dry-run
```

MinerU 合成样本真值门（须已人工安装 CLI/模型，且仅在现场授权后执行；**Agent 禁止自动跑真实合成门或代装**）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine mineru --synthetic-check
```

可选 Docling（**必须**提供已存在的本地 `--artifacts-path`；MinerU **禁止**带该参数）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine docling --artifacts-path "D:\models\docling" --dry-run
backend\.venv\Scripts\python.exe tools\local-parser\runtime_preflight.py --engine docling --artifacts-path "D:\models\docling" --synthetic-check
```

参数要点：

- `--engine` 仅 `mineru|docling`，默认 `mineru`
- `--dry-run` 与 `--synthetic-check` **互斥且必选其一**
- stdout **仅一个**六键 JSON：`ok`、`engine`、`mode`、`diagnosticCode`、`message`、`runtimeVerified`（无路径、无 argv、无正文、无异常类名）

### 当前机器诚实口径与禁止项

- 若 PATH 中无安全的 `mineru.exe` / `docling.exe`，`dry-run` 应得 `diagnosticCode=cli_missing`（退出码 2）。**不得**把缺 CLI 伪装成通过。
- **禁止** Agent 自动 `pip install`、下载模型、执行 `docling-tools models download` / `mineru-models-download` 或联网安装。
- **禁止**把真实业务文件路径传给预检；预检只处理代码生成的合成 DOCX。
- 现场授权边界：真实 CLI 安装、模型准备、真实 `--synthetic-check`、授权业务样本 E2E 只能由**用户/管理员显式授权**后人工执行。

### 主要诊断码（摘要）

| `diagnosticCode` | 含义 | 退出码 |
|---|---|---:|
| `static_ready` | dry-run 静态通过 | 0 |
| `synthetic_passed` | 合成样本命中锚点 | 0 |
| `argument_invalid` | 参数非法 | 2 |
| `cli_missing` | CLI 缺失或不安全类型 | 2 |
| `artifacts_invalid` | Docling 模型目录非法 | 2 |
| `parser_failed` / `parser_timeout` / `output_invalid` / `sample_marker_missing` | 合成门失败类 | 2 |
| `interrupted` | 用户中断 | 130 |
| `internal_error` | 未预期兜底 | 1 |

---

## 共用前置条件（业务回传助手）

1. **本机后端已启动**
   默认回调 Origin 为 `http://127.0.0.1:8000`。须能访问
   `POST /api/local-parser/callback`（P8C 公开回调；后端已允许精确 `mineru|docling`）。

2. **在浏览器签发票据**
   打开 `/local-parser?projectId=<当前项目>`，以有权限的用户签发 **10 分钟、单项目、单次** 票据。
   票据形态与后端 `secrets.token_urlsafe(32)` 一致：**恰好 43 个** ASCII URL-safe 字符 `[A-Za-z0-9_-]`。
   票据只显示一次，请在**交互式终端**粘贴到助手提示中（输入不回显）。
   **管道/重定向 stdin（非 TTY）会被拒绝**，且不会调用 `getpass` 回退读管道。

3. **输入文件（两助手相同）**
   单文件；扩展名 `.pdf/.png/.jpg/.jpeg/.docx/.pptx/.xlsx`；非空且 ≤ 50 MiB；拒绝符号链接与目录。

---

## P8D：MinerU 助手

### 人工安装与模型准备

1. 按官方文档在本机安装 MinerU CLI（Windows 必须是 `mineru.exe`）：
   <https://opendatalab.github.io/MinerU/zh/usage/quick_usage/>
   - **Windows**：`shutil.which("mineru")` 后**只接受** `.exe` 普通非符号链接文件；拒绝 `.cmd/.bat/.com`/无后缀。
   - **POSIX**：只接受可执行的普通非符号链接文件。

2. 按官方「模型来源」事先完成下载与配置（助手强制本地离线，不会代下）：
   <https://opendatalab.github.io/MinerU/zh/usage/model_source/>

### 复制即用

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\mineru_callback_helper.py --input "D:\标书\某文件.pdf"
```

可选 Origin：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\mineru_callback_helper.py `
  --input "D:\标书\某文件.pdf" `
  --backend-origin "http://127.0.0.1:8000"
```

子进程环境：白名单系统变量 + `MINERU_MODEL_SOURCE=local` + `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`；剥离代理与 API Key。
固定命令：`mineru -p <绝对文件> -o <临时目录> -b pipeline`（`shell=False`）。

---

## P8E：Docling 助手

### 人工安装与离线模型准备（助手外完成）

1. **安装 Docling CLI**（Windows 必须是 `docling.exe`）
   官方 CLI 参考：
   <https://docling-project.github.io/docling/reference/cli/>
   - 助手只用 `shutil.which("docling")`。
   - **Windows**：只接受普通非符号链接 `docling.exe`；拒绝 `.cmd/.bat/.com`/无后缀。
   - **POSIX**：只接受普通非符号链接可执行文件。
   - **不得**向助手传入自定义 executable、解释器或额外 CLI 参数。

2. **准备本地模型目录（离线）**
   官方离线/高级选项说明：
   <https://docling-project.github.io/docling/usage/advanced_options/>
   支持格式：
   <https://docling-project.github.io/docling/usage/supported_formats/>
   - 用户在助手之外人工完成安装与 `docling-tools models download`（或等价官方离线准备）。
   - 将模型目录作为 **`--artifacts-path`** 传入；必须是**已存在**的普通非符号链接目录。
   - 助手**不检查、不复制、不下载、不修改**模型内容；模型缺失时 Docling 会失败，助手固定报「Docling 解析失败」。
   - 子进程强制 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，不继承 proxy、`DOCLING_SERVICE_URL`/`API_KEY`、`DOCLING_ARTIFACTS_PATH`、HF token、业务 API key 或票据。

### 复制即用

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\docling_callback_helper.py `
  --input "D:\标书\某文件.pdf" `
  --artifacts-path "D:\models\docling"
```

可选 Origin：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\docling_callback_helper.py `
  --input "D:\标书\某文件.pdf" `
  --artifacts-path "D:\models\docling" `
  --backend-origin "http://127.0.0.1:8000"
```

固定命令（顺序契约，不经 shell）：

```text
docling convert
  --from <pdf|image|docx|pptx|xlsx>
  --to md
  --image-export-mode placeholder
  --pipeline standard
  --artifacts-path <绝对本地模型目录>
  --no-enable-remote-services
  --no-allow-external-plugins
  --abort-on-error
  --document-timeout 1800
  --num-threads 1
  --device cpu
  --output <绝对临时目录>
  <绝对输入文件>
```

禁止：`convert-remote`、URL source、`--headers`、service/API key、VLM/ASR、外部插件、额外输出格式与用户自定义参数。

---

## 共用行为边界（摘要）

| 项目 | 行为 |
|------|------|
| 票据 | **仅**交互 TTY + `getpass`；固定 43 字符 URL-safe；禁止 argv/env/管道/文件/剪贴板 |
| 回调 | 固定拼 `/api/local-parser/callback`；无代理、无重定向、**只请求一次**、失败不重试 |
| 响应体 | 成功响应最多读取 64 KiB；HTTPError/非 2xx **不**整包 `read()` |
| 输出 | 仅系统临时目录（MinerU：`biaoshu-mineru-`；Docling：`biaoshu-docling-`）；成功/失败/超时/中断均清理；stdout/stderr 丢弃 |
| Markdown | 临时根内恰好一个合法 `.md`；读取前校验 + 二进制有界读；输出树条目 ≤ 4096；正文 1～1,000,000 码点；JSON body ≤ 2 MiB |
| 成功输出 | 仅固定中文「本地解析回传成功」；不打印票据、绝对路径、模型目录、Markdown、taskId、detail、解析器原始输出 |

---

## 明确不做

- 不自动安装/升级 MinerU 或 Docling，不在助手/预检内执行 `pip` / `docling-tools models download` / `mineru-models-download`
- 不把解析器注册为后端 `parse_engines`，不由浏览器 `parseStrategy=local` 自动拉起
- 不支持 GPU/VLM/ASR、远程服务、URL/HTML/音视频等额外格式、外部插件、自定义 OCR/线程/超时
- 不读取浏览器 Cookie/CSRF、仓库 `.env`、数据库或上传目录
- 不接受管道票据，不把 Windows 批处理包装当作安全可执行文件
- 预检不签票、不回调、不读取真实标书；`dry-run` 不启动解析器子进程
- **自动化测试仅用假 CLI/假 HTTP**；**真实 Docling/模型未安装、未验收**，不得用假绿冒充生产就绪

---

## 失败排查

1. **未找到 MinerU/Docling 命令**
   检查 PATH；Windows 确认解析到的是 **`.exe`**。

2. **输入文件无效**
   单文件、白名单扩展名、非空、≤ 50 MiB、非符号链接。

3. **模型目录无效**（仅 Docling）
   `--artifacts-path` 须为已存在普通非符号链接目录；禁止 URL、文件或不存在路径。

4. **回传票据无效**
   非交互终端、长度/字符集不符、过期或已使用、管道传入。请在交互终端重新签发并粘贴。

5. **解析失败 / 超时**
   默认最长 30 分钟。模型未就绪、格式不支持或 CLI 非零退出时固定失败；助手不回显原始日志。

6. **未找到合法的唯一 Markdown 输出**
   未产出恰好一个 `.md`，或超字节/码点/输出树上限。

7. **后端地址无效 / 回传失败**
   Origin 仅回环 + 合法端口；响应须为 `{ok:true, chars, taskId}` 且 ≤ 64 KiB。不要自动重试。

---

## 残余风险（必须知晓）

1. **无操作系统级网络沙箱**
   标准库助手通过环境剥离与 CLI 参数尽量离线，但不能证明 Docling/MinerU 孙进程完全无法触网。

2. **无内存/CPU 硬配额**
   超时仅针对直接子进程的父进程等待；不能限制解析器内部资源占用。

3. **孙进程完整治理不能证明**
   超时/中断时会 terminate→短等→kill **直接子进程**并清理临时根，但无法证明回收解析器自建的全部孙进程/端口。残留须人工按官方说明与系统工具处理。

4. **真实模型未验收**
   本目录 unittest 使用假 CLI 与假回环 HTTP，证明 argv/env/来源/边界；**不**表示本机已安装可用 Docling 模型或生产路径已跑通。

5. **CLI 版本敏感**
   Docling 参数按 2026-07-15 官方 `docling convert` 冻结；旧版/未来不兼容版本应固定失败并由用户人工升级，助手不提供静默降级或 shell 兼容层。

---

## 自测（开发者）

使用假 MinerU / 假 Docling / 假 HTTP，**不要求**安装真实解析器或模型：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
```

## 相关文档

- V1-C 契约：`docs/v1c-local-parser-runtime-preflight-contract.md`
- V1-C 计划：`docs/plans/2026-07-21-v1c-local-parser-runtime-preflight-plan.md`
- P8D 契约：`docs/p8d-mineru-local-helper-contract.md`
- P8E 契约：`docs/p8e-docling-local-helper-contract.md`
- P8E 计划：`docs/plans/2026-07-15-p8e-docling-local-helper-plan.md`
