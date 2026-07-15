<!--
模块：P8E 本机 Docling 外置解析助手契约
用途：冻结 P8C 回调来源枚举扩展与 Docling 本机离线助手的接口、安全、进程和验收边界。
对接：local_parser_ticket_service；tools/local-parser；P8C 一次性回调；Docling 官方 CLI。
二次开发：禁止后端内嵌 Docling、自动安装/下载、远程转换、外部插件、票据持久化或把 Docling 冒充 MinerU。
-->

# P8E 本机 Docling 外置解析助手契约

> **状态**：P8E-A/P8E-B 已实现、独立验收并推送；真实 Docling/离线模型仍未安装和验收。
> **工作分支**：`collab/grok-code-codex-review`。
> **交付基线**：P8D 计划=`30d066f`、实现=`e1fe316`、文档闭环=`38b9318`；P8E 计划/契约=`73b1264`、后端=`79b346e`、本机助手=`e3f9cc4`。后端全量 487、前端全量 E2E 184 为沿用基线，不冒充本包重跑。
> **本机事实**：当前 PATH 中未发现 `docling` 或 `docling-tools`；本包只能用假 CLI/假 HTTP 自动化验收，禁止声称真实模型已就绪。

## 1. 现状与方案选择

P8C 的唯一公开回调 `POST /api/local-parser/callback` 已具备 2 MiB 流式正文上限、10 分钟单项目单次票据、条件 UPDATE 原子消费、固定脱敏错误和同事务写入。当前唯一阻塞点是 `normalize_callback_body()` 把 `source` 精确限定为 `mineru`。票据表不保存解析器类型，来源只进入解析结果标题、成功任务消息和任务结果；审计继续只记录固定 `one_time_ticket`，因此无需迁移表或新增公开路径。

比较三种方案：

1. **后端直接启动 Docling**：违反包 8/P8B/P8C 的“服务端不启动外部解析器”边界，扩大网络、模型、进程和资源风险，拒绝。
2. **复制 P8D 全部安全与回调代码**：实现独立但会复制已两轮返修的票据、Origin、响应和 Markdown 防线，后续容易安全漂移，拒绝。
3. **独立 Docling CLI + 复用 P8D 已验收通用原语**：后端只扩固定来源枚举；Docling 助手只新增 CLI 专属逻辑，共享回调增加内部受控 `source` 参数且默认保持 `mineru`。选择此方案。

## 2. 交付拆分与总体数据流

P8E 必须分两个顺序实现包，禁止合并派发：

1. **P8E-A 后端来源枚举**：先让公开回调精确接受 `mineru|docling`，保持 P8C 其余鉴权、票据、事务和响应不变；Codex 独立验收、提交并推送后再进入下一包。
2. **P8E-B 本机 Docling 助手**：用户显式选择单文件和本地模型目录，在交互 TTY 粘贴 P8C 票据；助手以固定参数调用本机既有 Docling，仅从临时目录读取唯一 Markdown，再以 `source=docling` 向现有回环回调提交一次。

数据流固定为：浏览器显式签票 → 票据仅驻留助手进程内存 → 本机 Docling 离线转换 → 临时目录唯一 Markdown → 无代理/无重定向单次回调 → P8C 原子消费和同事务写入。浏览器不启动进程、不上传原文件，后端不读取本机路径。

## 3. P8E-A 后端来源枚举边界

- 在 `backend/app/services/local_parser_ticket_service.py` 定义不可变固定集合，只允许精确小写 `mineru` 与 `docling`；不得接受大小写折叠、首尾空白清洗、前缀、后缀、任意字符串或客户端扩展枚举。
- `markdown` 与 `filename` 的现有规范化、2 MiB/1,000,000 码点/255 码点上限和固定错误完全保持；非法来源仍返回 `400 local_parser_callback_bad_request`，不得反射来源值。
- 请求体必须先规范化再消费票据。反假绿测试须证明非法来源不会消耗票据，同一票据随后可用精确 `docling` 成功一次。
- `docling` 成功路径沿用现有 editor-state、项目步骤、成功 parse task 和固定审计事务；任务 `result.source` 与标题来源精确为 `docling`。成功响应仍只能有 `ok/chars/taskId`，审计不得新增来源、项目、文件名、正文、字符数或票据字段。
- 不修改 `LocalParserCallbackTicketRow`、公开路径、认证中间件、签发响应、TTL、角色、CSRF、Cookie、旧 `X-Local-Token` 或个人兼容 `/api/projects/{id}/parse-callback`。旧路径允许的历史 source 语义不在本包收紧。
- `source` 只是持票助手自报的任务来源标签，不是解析器身份的密码学证明；不得据此授予权限、跳过校验或形成审计身份。

## 4. P8E-B Docling CLI 与本地模型边界

### 4.1 参数、输入与可执行文件

- 新增纯标准库 `tools/local-parser/docling_callback_helper.py`；CLI 只接受必填 `--input`、必填 `--artifacts-path` 和可选 `--backend-origin`。票据不得进入 argv、环境、文件、URL、剪贴板或标准输入管道。
- `--input` 复用 P8D 边界：单个已存在普通非符号链接文件，非空且不超过 50 MiB；扩展名只允许 `.pdf/.png/.jpg/.jpeg/.docx/.pptx/.xlsx`，拒绝目录、URL、双扩展伪装和其他格式。
- `--artifacts-path` 必须是已存在普通非符号链接目录，解析后仍为目录；不得接受 URL、文件或不存在路径。助手不检查、复制、下载或修改模型内容；路径只作为 Docling 官方 `--artifacts-path` 参数。
- 只用 `shutil.which("docling")`。Windows 只接受普通非符号链接 `docling.exe`，拒绝 `.cmd/.bat/.com` 与无后缀；POSIX 只接受普通非符号链接且可执行文件。不得接受用户自定义 executable、解释器或额外参数。

### 4.2 固定命令与离线环境

固定参数数组使用当前官方 CLI 的 `docling convert [OPTIONS] source`，不经 shell。除绝对输入、绝对模型目录、绝对临时输出目录和按扩展名固定映射的 `--from` 值外，参数不得由用户控制：

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

- 禁止 `convert-remote`、URL source、`--headers`、service URL/API Key、VLM/ASR 远程模型、外部插件、调试/画像、用户自定义 OCR engine、额外输出格式和任意附加参数。
- 子进程环境复用 P8D 固定系统白名单，并强制 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`。不得继承 HTTP(S)/ALL proxy、`DOCLING_SERVICE_URL`、`DOCLING_SERVICE_API_KEY`、`DOCLING_ARTIFACTS_PATH`、HF Token、云/API Key、票据或其他业务配置。
- 官方文档说明首次使用默认可能自动下载模型；本助手必须在离线环境且显式本地 artifacts 下运行，模型缺失时固定失败。安装 Docling 和运行 `docling-tools models download` 只能由用户在助手之外人工完成。

### 4.3 进程、输出与回调

- `subprocess.Popen` 必须使用参数数组、`shell=False`、`stdin/stdout/stderr=DEVNULL`。父进程硬超时 30 分钟；超时、Ctrl+C 或异常先 terminate、短等后 kill。不得打印或保存 Docling 原始输出。
- 输出仅写 `TemporaryDirectory(prefix="biaoshu-docling-")`，所有路径下都 finally 清理；不得写仓库、源文件目录、桌面或固定缓存。
- 复用 P8D 已验收的输出树上限 4096、唯一非符号链接 `.md`、临时根内约束、读取前 2 MiB、`2 MiB + 1` 有界二进制读取、严格 UTF-8、1–1,000,000 码点和最终 JSON 2 MiB 上限。
- 复用 P8D 的 TTY-only 43 字符票据、回环 Origin、无代理、无重定向、64 KiB 响应上限、非 2xx 不读正文、一次请求零重试和固定成功字段校验；Docling 调用必须显式构造 `source=docling`，MinerU 默认行为继续构造 `source=mineru`。
- 成功只打印固定中文，不打印票据、绝对路径、模型目录、Markdown、taskId、服务端 detail 或 Docling 原始错误。任何失败固定中文、非零退出且零自动重试。

## 5. 精确文件白名单

### P8E-A 后端任务

- `backend/app/services/local_parser_ticket_service.py`
- `backend/tests/test_local_parser_callback_tickets.py`

### P8E-B 工具任务

- `tools/local-parser/mineru_callback_helper.py`
- `tools/local-parser/docling_callback_helper.py`（新增）
- `tools/local-parser/test_docling_callback_helper.py`（新增）
- `tools/local-parser/README.md`

除上述分包白名单外，Grok 不得修改后端其他文件、前端、依赖/锁文件、数据库模型/迁移、启动脚本、P8/P8C/P8D 文档或 Git 状态；不得 commit/push。Codex 负责审查、独立测试、中文提交和推送。

## 6. 反假绿验收

### 6.1 后端

- 先把原“docling 非法”测试改为未知来源，再新增精确枚举测试；`docling` 必须真实进入成功事务，不能只测常量或 mock normalize。
- 至少覆盖 `mineru/docling` 成功；`Docling`、首尾空白、前后缀、空值、非字符串和未知来源固定 400；非法来源不消费票据；同票据随后精确 `docling` 成功；第二次重放统一 401。
- 核对任务 `result.source=docling`、parsed Markdown 来源标题、成功响应键集、审计固定字段和敏感值不落审计；P8C 原子消费、回滚、正文上限和旧回调回归继续通过。

### 6.2 Docling 助手

- 用完全假的 `docling.exe`/POSIX 可执行文件和回环假 HTTP，不安装 Docling、不读取公网。假进程必须真实记录 argv、cwd 和环境，不能只 mock 最终 Markdown。
- 覆盖可执行文件后缀与符号链接、输入/模型目录校验、七类扩展到五种 `--from` 的精确映射、固定 `convert` 参数顺序、`shell=False`、stdin/stdout/stderr 丢弃、环境白名单与离线变量、远程/插件/API/代理变量剥离。
- 覆盖成功、非零退出、启动失败、超时、Ctrl+C、临时目录清理、唯一 Markdown/输出树/有界读取复用，以及 `source=docling` 的精确 Header/body；不得把默认 `mineru` 假装成 Docling 成功证据。
- 覆盖非 TTY 在读取票据前固定失败且不启动进程/不回调，票据/路径/模型目录/正文/taskId/detail/原始错误不出现在 stdout/stderr/临时文件。
- P8D 原 54 项单测必须保持通过，证明共享回调参数化未破坏 MinerU；两个测试文件不得访问真实外网或要求真实模型。

### 6.3 Codex 独立回归

Codex 至少独立运行：P8E-A/P8C 后端专项；P8C/P8B/解析引擎受影响回归；P8D 与 P8E 工具单测；前端 lint/build；P8C E2E 9 项和 P8B 解析策略 E2E 6 项。Playwright 继续 Chromium headless、`workers=1`、逐条串行。实现暂存后必须运行 `git diff --cached --check`。

## 7. 明确非目标与残余风险

- 不安装、升级、下载、打包或探测真实 Docling/模型；不修改依赖；不新增 Docker/WSL/常驻服务/队列；不运行 `docling-tools models download`。
- 不把 Docling 注册为后端 `parse_engines`，不在浏览器启动进程，不自动签票/续票/重试，不批处理，不上传源文件，不新增前端解析器选择器。
- 不支持 GPU/VLM/ASR、远程服务、URL/HTML/音视频/邮件等额外格式、外部插件、自定义 OCR/线程/超时/命令参数。
- 标准库助手不能形成操作系统级网络沙箱、内存/CPU 硬配额，也不能证明完整回收 Docling 自建的全部孙进程；这些风险必须写入 README，后续若做生产部署须另立契约。
- 本契约针对 2026-07-15 官方 CLI 的 `docling convert` 参数。旧版或未来不兼容版本应固定失败并由用户人工升级，不得添加静默降级命令或 shell 兼容层。

## 8. 官方依据

- Docling CLI 参考：<https://docling-project.github.io/docling/reference/cli/>
- Docling 离线模型准备：<https://docling-project.github.io/docling/usage/advanced_options/>
- Docling 支持格式：<https://docling-project.github.io/docling/usage/supported_formats/>

## 9. 实施、审查与验收记录

1. **P8E-A 后端**：Grok 在精确两文件边界内把公开回调来源扩为不可变精确枚举 `mineru|docling`。Codex 首轮拒绝“审计至少一条并取最后一条”和字符数子串这类宽松断言，返修后独立通过专项 12 项、P8C/P8B/解析受影响回归 37 项；提交=`79b346e`。
2. **P8E-B 首版**：Grok 完成四文件助手和 38 项自动化，但首轮未保留真实失败先测命令证据，不能补写为已发生。Codex 代码审查发现子进程继承仓库工作目录、绝对模型目录可绕过二次校验、通过修改共享模块全局常量实现终止等待，以及静态反作弊正则未启用多行模式；首轮实现不予验收。
3. **第一轮返修**：先补测试后真实出现 43 项中 17 个失败，再修复为显式临时 `cwd`、流水线无条件模型目录校验、直接复用无全局修改的终止函数、精确 `--from` 枚举和可验证的多行反作弊探针。
4. **第二轮返修**：Codex 继续发现 `HOME/USERPROFILE/TEMP` 等仍可能把第三方缓存写入固定用户目录。返修把 14 个可写缓存、配置和临时环境变量全部绑定到本次 `biaoshu-docling-*` 临时根，退出后统一清理；第二轮先测真实出现 6 个用例、30 个子断言失败和 1 个错误，再修复通过。
5. **Codex 独立验收**：Docling 假 CLI/假 HTTP **46 passed**，P8D MinerU **54 passed**，后端受影响回归 **37 passed**（1 条既有 Starlette/httpx 警告），前端 lint/build 通过（build 仅既有大包体积提示），P8C E2E **9 passed**、P8B 解析策略 E2E **6 passed**；两组 Playwright 均为 Chromium headless、`workers=1`、严格串行。实现暂存区 whitespace 检查通过；助手提交=`e3f9cc4`。
6. **真实运行边界**：当前 PATH 未验收 `docling`/`docling-tools`，也未准备或运行真实离线模型。自动化只证明固定 argv、环境、进程、输出、回调和脱敏边界，不代表真实模型可用、解析质量达标或操作系统级网络/资源隔离完备。
