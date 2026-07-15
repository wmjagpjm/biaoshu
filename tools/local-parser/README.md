<!--
模块：P8D/P8E 本机外置解析助手说明
用途：说明 MinerU 与 Docling 双助手的人工安装、模型准备、复制即用命令、离线边界、失败排查与残余风险。
对接：mineru_callback_helper.py；docling_callback_helper.py；P8C 一次性回调；docs/p8d / docs/p8e 契约。
二次开发：不提供自动安装器；不承诺清理解析器自建孙进程；不把票据写入参数/环境/文件/日志；真实模型未验收不得冒充自动化通过。
-->

# 本机外置解析助手（P8D MinerU / P8E Docling）

本目录是**独立本机工具**，不嵌入后端进程。用户在浏览器为目标项目签发 P8C 一次性回传票据后，在本机**交互终端**显式选择源文件（Docling 还需本地模型目录），由助手调用 PATH 中已安装的解析 CLI，再向本机回环后端提交 Markdown。

| 助手 | 脚本 | 回调 `source` |
|------|------|----------------|
| P8D MinerU | `mineru_callback_helper.py` | `mineru`（默认） |
| P8E Docling | `docling_callback_helper.py` | `docling`（显式） |

两条路径共用已验收的输入校验、TTY 票据、Origin 归一化、Markdown 有界读取与无代理/无重定向回调原语；**不得**把 Docling 冒充成 MinerU，也不得由浏览器或后端自动拉起本助手。

---

## 共用前置条件

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

- 不自动安装/升级 MinerU 或 Docling，不在助手内执行 `pip` / `docling-tools models download` / `mineru-models-download`
- 不把解析器注册为后端 `parse_engines`，不由浏览器 `parseStrategy=local` 自动拉起
- 不支持 GPU/VLM/ASR、远程服务、URL/HTML/音视频等额外格式、外部插件、自定义 OCR/线程/超时
- 不读取浏览器 Cookie/CSRF、仓库 `.env`、数据库或上传目录
- 不接受管道票据，不把 Windows 批处理包装当作安全可执行文件
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
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
```

## 相关文档

- P8D 契约：`docs/p8d-mineru-local-helper-contract.md`
- P8E 契约：`docs/p8e-docling-local-helper-contract.md`
- P8E 计划：`docs/plans/2026-07-15-p8e-docling-local-helper-plan.md`
