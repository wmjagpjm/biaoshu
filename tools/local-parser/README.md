<!--
模块：P8D/P8E 本机外置解析助手、V1-C 预检与 V1-M 管理式 OCR runtime 预检说明
用途：说明 MinerU/Docling 人工安装、模型准备、复制即用命令、离线边界、失败排查；V1-C 六键预检；V1-M 仓外 manifest 与四层证据。
对接：runtime_preflight.py；managed_runtime_preflight.py；mineru_callback_helper.py；docling_callback_helper.py；P8C 回调；docs/p8d / p8e / v1c / v1m 契约。
二次开发：不提供自动安装器；不承诺清理解析器自建孙进程；不把票据写入参数/环境/文件/日志；假 runner 命中不得冒充真实 OCR 通过。
-->

# 本机外置解析助手（P8D MinerU / P8E Docling）、V1-C 与 V1-M 预检

本目录是**独立本机工具**，不嵌入后端进程。用户在浏览器为目标项目签发 P8C 一次性回传票据后，在本机**交互终端**显式选择源文件（Docling 还需本地模型目录），由助手调用 PATH 中已安装的解析 CLI，再向本机回环后端提交 Markdown。

| 助手 / 入口 | 脚本 | 回调 `source` / 角色 |
|------|------|----------------|
| P8D MinerU | `mineru_callback_helper.py` | `mineru`（默认业务回传） |
| P8E Docling | `docling_callback_helper.py` | `docling`（显式业务回传） |
| V1-C 预检 | `runtime_preflight.py` | **不回调**；PATH 静态检查或合成 DOCX 真值门 |
| V1-M 管理式 OCR 预检 | `managed_runtime_preflight.py` | **不回调**；仓外 manifest + image-only PDF 门 |

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

## V1-M：管理式本机 OCR runtime 预检（M1）

V1-M 在 **仓外专用 MinerU runtime** 上建立可判定门，与 V1-C（PATH `which`）互补。M1 **不**接线后端 `engine=managed`、不安装 CLI/模型、不读业务文件。

### 四层证据（验收必须分开列，禁止合并分母）

| 层级 | 含义 | M1 默认 |
|------|------|--------|
| `automated` | 参数、manifest、fixture、JSON、边界单测 | 可绿 |
| `fake-runtime` | 假 exe / mock Popen，只证明包装安全 | 可绿 |
| `real-runtime` | 真实 CLI + 模型 | **did-not-run**（未授权安装前） |
| `quality` | ASCII / 中文 image-only PDF 真实质量 | **did-not-run**（同上） |

**禁止**：把 `fake-runtime` 的 Markdown 锚点命中写成「真实 OCR 已通过」；禁止 skip/xfail 掩盖未安装。

### 仓外 manifest（不得提交 Git）

位于管理员选择的 runtime 根，精确五键 JSON 示例：

```json
{
  "schemaVersion": 1,
  "engine": "mineru",
  "cliRelativePath": "venv/Scripts/mineru.exe",
  "modelMarkerRelativePath": "models/.biaoshu-ready",
  "requiredFreeBytes": 1
}
```

规则摘要：

- 仅相对路径，且必须解析在 runtime 根内；拒绝绝对路径、UNC、URL、`..`、symlink/reparse 逃逸。
- Windows CLI 必须是普通非 reparse `.exe`；模型 marker 为根内普通小文件（≤64KiB）。
- `requiredFreeBytes` 为严格正整数，预检检查 **manifest 所在目标卷** 可用空间。
- 不从 PATH、API、浏览器、`.env` 接受任意 executable。

### 复制即用

静态就绪（本机无真实 runtime 时预期 `cli_missing` / `runtime_manifest_invalid` 等非零，属正确未安装真值）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\managed_runtime_preflight.py `
  --manifest "D:\biaoshu-mineru-runtime\runtime-manifest.json" `
  --dry-run `
  --quality-profile ascii
```

合成 image-only PDF OCR 门（仍只证明包装 + 假/真 CLI 输出标记；**不**等于业务自动解析完成）：

```powershell
backend\.venv\Scripts\python.exe tools\local-parser\managed_runtime_preflight.py `
  --manifest "D:\biaoshu-mineru-runtime\runtime-manifest.json" `
  --ocr-check `
  --quality-profile ascii
```

- stdout **仅一个**九键 JSON：`ok`、`status`、`engine`、`mode`、`diagnosticCode`、`message`、`runtimeVerified`、`didNotRunRealRuntime`、`qualityProfile`
- `status`：`ready|passed|not_ready|failed`；`mode` **永远只能** `dry-run|ocr-check`（缺模式/无法判定固定 `dry-run` fallback，仍 `argument_invalid`）
- `qualityProfile`：`ascii|windows-zh`
- `ascii`：两页 ASCII 锚点 image-only PDF；Markdown 须 `P1→P2` 顺序命中
- `windows-zh`：非 Windows 或缺少 `simhei.ttf|msyh.ttc` → `quality_precondition_failed` + `didNotRunRealRuntime=true`，不计 passed；字体就绪时用系统 truetype 绘制两页中文短句（封面验收短句甲/正文验收短句乙）及各自 ASCII 伴随锚点；成功 Markdown 须按「中文P1→ASCII P1→中文P2→ASCII P2」全部命中；仅 ASCII 命中固定 `ocr_marker_missing`，不得 `ocr_passed`；假 runner 成功不得宣称真实中文质量
- manifest 相对路径在 resolve 前逐组件拒绝同根 symlink/reparse（含 leaf alias、父目录 alias、Scripts reparse）→ `runtime_manifest_invalid`；真缺失仍分别 `cli_missing`/`model_missing`
- **禁止** `--executable`、联网安装、把 runtime/模型放进仓库或 uploads

### 未安装真值（当前机器诚实口径）

若尚未准备仓外 manifest / CLI / 模型：

- dry-run 应得非零与固定中文诊断（如 `cli_missing`、`model_missing`、`runtime_manifest_invalid`）
- **不得**把缺 CLI 伪装成 `static_ready` / `ocr_passed`
- 自动化验收应显式记录：`real-runtime did-not-run`、`quality did-not-run`

### 管理员 M4 / 回滚边界

- **M4 真实验收**（安装兼容 Python/MinerU/模型、写 manifest、跑真实 ASCII/中文扫描 PDF、隔离项目 managed 任务）必须由用户/管理员**单独明确授权**；Agent 与 M1–M3 代码任务**禁止**代装或联网下载。
- 回滚：删除或移走仓外 runtime 根与 manifest 即可回到「未安装」；业务库与 uploads 不因 M1 预检改变。
- M1 完成 ≠ 扫描 PDF 业务自动解析可用；须 M2/M3 接线且 M4 真实门通过后才可上调 V1 OCR 完成口径。

### 主要诊断码（V1-M 摘要）

| `diagnosticCode` | 含义 | 退出码 |
|---|---|---:|
| `static_ready` | dry-run 静态通过 | 0 |
| `ocr_passed` | ocr-check 标记命中（含 fake-runtime） | 0 |
| `argument_invalid` | 参数非法 | 2 |
| `runtime_manifest_invalid` | 清单键/类型/路径形态非法 | 2 |
| `cli_missing` / `model_missing` / `disk_insufficient` | 未就绪类 | 2 |
| `quality_precondition_failed` | 中文 profile 前置失败 | 2 |
| `parser_failed` / `parser_timeout` / `output_invalid` / `ocr_marker_missing` | 运行/输出门失败 | 2 |
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

子进程环境：只读系统变量有限继承 + **全部可写根（HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP/TMPDIR 及 HF/Torch/XDG/Matplotlib/pycache 缓存）绑定到本次 output 临时根** + `MINERU_MODEL_SOURCE=local` + `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1`；剥离代理、API Key、Cookie、CSRF 与票据。`Popen` 固定 `cwd=<output 临时根>`、`shell=False`。
固定命令：`mineru -p <绝对文件> -o <临时目录> -b pipeline`。

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
