<!--
模块：V1-M 管理式本机 OCR 自动解析契约
用途：冻结扫描 PDF 真实性、专用解析运行时、项目多文件解析、原子写回与前端策略边界。
对接：P8B/P8C/P8D/P8E、V1-C、任务/editor-state/revision/event、V1 本机/内网发布主线。
二次开发：必须按 M1→M2→M3→M4 分包；禁止把假 CLI、环境未就绪或单文件绿测冒充真实自动解析完成。
-->

# V1-M 管理式本机 OCR 自动解析契约

> **状态：M1 与 M2 automated/fake-runtime 已完成、独立验收并推送；M3/M4 未开始。M4 真实 runtime/quality 仍为 did-not-run。**
> **基线：** 契约=`9ed3a06`；M1 测试=`3be6d19`/`3b8e42e`/`61dbe38`、生产=`a7d640b`；M2 测试=`df85ac7`、生产=`86d5206`；只允许 `collab/grok-code-codex-review`，严禁操作 `main`。
> **A 审计：** task=`msg_b32147f62fba43229ea58abbe6903340`，review=`msg_e978430b73074df49d57cd7b6acd1e50`。
> **B 审计：** task=`msg_e2bdf5e1122d4ba7b4dfe59c61d3c7a5`，review=`msg_6d7d74ca5b964ca9987779080ead3d67`。
> **Q1 双确认：** A question/YES=`msg_d9e3c5b69e34444196173bf1b59c27ac`/`msg_91b3078bdf0c432ba05b8b584f448d79`；B question/YES=`msg_35addd2168654b7fb0cc800d39c966f1`/`msg_71b72f88d8c8482092081bbb6cb416e7`。
> **M1 验收：** Codex 独立串行 managed/MinerU/V1-C/Docling=`29/56/26/46 passed`；真实 Windows junction no-follow 探针通过，TEMP left=0。真实 CLI、模型、real-runtime 与 quality 均 did-not-run。

## 1. 当前真值

系统已有三条代码链，但第三条尚未完成前端接线与真实 runtime 验收：

1. `light`：浏览器创建 `engine=lightweight` 的 parse 任务，后端从项目 uploads 按 ASC 读取全部 source，使用 `pypdf/python-docx` 聚合并以单事务写入 editor-state。
2. `local`：浏览器只导航 `/local-parser`；用户在宿主机终端显式运行 MinerU/Docling 外置助手，用一次性票据或 disabled 兼容回调写回 Markdown。
3. `managed`：后端已有独立 adapter、共享 pure core、受控多文件任务和五域单事务 finalizer；它不进入 `parse_engines` 注册表。M3 前端尚未创建该任务，M4 CLI/模型与真实质量尚未验收。
4. 生产 `parse_engines` 注册表仍只有 `lightweight`；`local|ask` 不启动解析器，MinerU/Docling CLI 与模型均未安装。
5. 本机为 Windows 11、31.8 GB RAM、RTX 5060 8 GB；后端 Python 3.13.3，当前只有 CPU 版 torch。C 盘约 40 GB 可用，D 盘约 154 GB 可用，但任何盘符都不得写成产品常量。

V1-C 的合成 DOCX 锚点位于 OpenXML 文本层，只证明已安装 CLI 能读取最小 DOCX；它不证明扫描 PDF、图像 OCR、中文、多页顺序或真实模型质量。

## 2. M2 开工前生产缺口（现已关闭）

### 2.1 多文件静默漏解析

M2 开工前，`file_service.list_files()` 按 `created_at.desc()` 返回新到旧，`task_service._run_parse()` 却只解析 `files[0]`。创建页允许上传多个源文件，因此当时只解析最新一份，其余文件不进入 `parsedMarkdown` 或 task result。M2 已用 parse 专用 ASC 查询与固定分隔符关闭该缺口，对外 GET 排序不变。

这属于 V1 自动解析必须关闭的缺口；现已由 `86d5206` 关闭，不得以单文件 OCR 绿测替代当前多文件验收。

### 2.2 parse 成功不是单事务（已关闭）

M2 开工前成功路径依次执行：

1. `editor_state_service.upsert_editor_state()` 提交 editor-state；
2. `project_service.update_project()` 提交项目状态；
3. `_set_task(... success ...)` 提交任务和事件。

后两步失败时可能出现正文已变化但任务显示 failed 的假失败。M2 已用 parse 专用 finalizer 把 `parsedMarkdown + revision(source=task) + project step/status + task success + task event` 放入一次提交；取消或任一步失败整包回滚，唯一 commit 后不再 refresh。

### 2.3 外部错误可能泄漏（已关闭）

通用任务异常会把 `str(exc)` 写入 `task.error`，完整任务 API/SSE 又会返回该字段。M2 managed 边界已只信任有限 code，并用 core 固定映射重新生成中文，禁止绝对路径、argv、第三方 stdout/stderr、异常类名、模型目录或正文进入任务响应。

### 2.4 MinerU 可写根隔离（M1 已关闭）

M1 failure-first 已证明旧 P8D MinerU 助手继承父进程可写根且未设置 `cwd`。生产提交 `a7d640b` 已把全部可写目录和 `cwd` 绑定单次 output/TEMP 根，同时保留 V1-C 零参兼容；代理、API Key、Cookie、CSRF 与票据不继承。真实解析器孙进程完整回收仍只作残余风险，不得声称百分百治理。

## 3. 方案选择

### 3.1 拒绝：污染后端 venv

禁止把 MinerU/Docling 安装进 `backend/.venv`，也禁止在 `parse_engines.py` 内直接加入任意 subprocess engine。解析器依赖、模型、GPU/CPU 运行时、缓存和 30 分钟级进程不得与 FastAPI 依赖生命周期绑定。

### 3.2 采用：独立专用 runtime + 后端受控任务

唯一目标拓扑：

```text
浏览器（light|managed|local|ask）
    |
    | managed -> 既有受鉴权/CSRF/RBAC 的 parse task
    v
FastAPI task worker
    |
    | 只读当前 workspace/project 的受权 source 文件
    | 固定 manifest、固定 mineru.exe、固定 argv/env/cwd、全局并发 1
    v
仓外专用 MinerU runtime（模型与缓存均在管理员选择的独立卷）
    |
    | 有界唯一 Markdown；无 HTTP、无票据、无正文出域
    v
parse 专用单事务 finalizer -> editor-state / revision / project / task / event
```

现有 P8C/P8D/P8E 人工回调链继续作为 `local` fallback；管理式自动路径不签发、不消费也不模拟一次性票据。

### 3.3 V1 唯一首选

V1 管理式 runtime 只实现 **MinerU**。Docling 继续保留现有人工助手和 V1-C 预检，但不进入 M1-M3 自动接线。Windows + Python 3.13 + RTX 5060 的真实兼容版本、模型体积和 OCR 质量当前均未验证，禁止在文档中补造版本号或容量。

## 4. 分包与完成定义

### M1：OCR 真值与专用 runtime 预检

只交付：

1. 两页 image-only 合成扫描 PDF；必跑 portable profile 仅用 Pillow 内置 ASCII 位图字体，不依赖系统字体或网络。
2. `pypdf.extract_text()` 必须逐页为空且不得含锚点，证明锚点只存在于像素，不在 PDF 文本层。
3. Windows 中文质量 profile 仅在管理员显式真实验收时读取 `simhei.ttf|msyh.ttc`；字体缺失固定 `quality_precondition_failed` 和 did-not-run，不能 skip/pass。
4. 新管理式 preflight 从管理员提供的仓外 manifest 解析固定 MinerU CLI、模型就绪标记与目标卷 `requiredFreeBytes`；不从 PATH、API、浏览器或 `.env` 接受任意 executable。
5. 修复 MinerU 全部可写目录与 cwd 隔离；假 CLI 只证明包装安全，不证明 OCR。

M1 完成不代表业务自动接线、CLI/模型已安装或真实 OCR 已通过。

**M1 完成记录：** 两页 ASCII 与 Windows 中文 image-only fixture、严格 manifest、固定九键 JSON、同根 symlink/junction no-follow、MinerU env/cwd 隔离均已通过 automated/fake-runtime 验收；提交 `a7d640b` 已推送。real-runtime/quality 因 M4 未授权而 did-not-run，V1 约 94% 口径不变。

### M2：后端调度、多文件与原子写回

只交付：

1. 新增 pure `managed_ocr_runtime_core` 作为 manifest/path/no-follow/ready/单文件 runner 的唯一真源；M1 CLI 与后端 adapter 共用。禁止复制、加载整份 CLI、环境指定模块路径或新增中间 worker CLI。
2. `engine=managed` 进入独立 runtime service；`lightweight` 继续走 `parse_engines`，禁止在该注册表执行 subprocess。manifest locator 只允许服务端 path-only 配置；客户端 payload/query/header 零路径，未配置固定 not-ready，禁止降级 light。
3. 每任务全量重读 manifest/CLI/marker/disk，每文件启动前再做廉价 no-follow recheck；禁止正向 ready 长缓存。任一中途变化整任务失败、零部分提交。
4. parse 专用查询按 `created_at ASC, id ASC` 返回全部 source；对外 GET `/files` 保持 desc。`lightweight` 与 `managed` 共享聚合；单文件正文原样，多文件之间只插入 `\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n`，不写文件名、路径或 ID。
5. V1 单次任务最多 10 个文件；数据库声明大小与实际普通 no-follow 文件必须一致，合计最多 200 MiB。source leaf/父目录 symlink/junction/reparse/path traversal 或大小不一致均零 parser。
6. 每文件顺序处理；合计 Markdown 同时不得超过 1,000,000 Unicode 码点与 2 MiB UTF-8。任一文件或合计失败整任务零写。
7. 管理式 runtime 使用进程内 `BoundedSemaphore(1)`；每文件最多 30 分钟、任务总时限 120 分钟；锁等待和子进程检查间隔不高于 1 秒，取消/超时 terminate→kill 直接子进程。跨进程并发与 Windows 孙进程是明确残余风险。
8. parse 私有 finalizer 复用既有 CAS/规范化逻辑：editor-state upsert、project update 与 task update 默认 `commit=True`，仅 finalizer 使用 `commit=False`/flush，随后唯一 commit。成功包同时写 parsedMarkdown、`source_kind=task` 修订、项目状态/步骤、task success/result 与 task event；任一步异常先 rollback，再写固定 failed。
9. `lightweight|managed` 成功 result 精确三键 `engine/fileCount/chars`。managed 失败 result 精确二键 `engine/diagnosticCode`，code 仅允许 M1 有限子集；task.error 使用固定中文。Markdown、文件名、路径、argv、stdout/stderr、异常类和第三方原文不得进入 GET/SSE。

M2 自动测试使用 TEMP、假 runtime、独立 SQLite/uploads；不得安装/调用真实 CLI。

**M2 完成记录（2026-07-22）：** test-only=`df85ac7`，production-only=`86d5206`。Grok A 精确 M2/managed=`47/34 passed`；Codex 独立串行 M2/managed/task-revision-security/settings/B1-B2=`47/34/65/3/2 passed`，八个 Python 生产文件 `py_compile`、九文件白名单、`git diff --check`、唯一 `BIAOSHU_MANAGED_OCR_MANIFEST` 环境探针与 TEMP left=0 均通过。Codex 与 Grok 逐项确认并关闭锁等待总时限、取消 rollback、source parser 前 no-follow/size 重检、commit 后 refresh 假失败、异常文案 canonicalize 和 Pydantic 字段名 env 回退。真实 CLI、模型、Popen 业务调用、real-runtime 与 quality 均 did-not-run；因此 M2 仅证明后端自动接线与假 runtime 安全边界，不证明真实 OCR 可用，V1 仍约 94%。

### M3：前端策略接线

解析策略扩为精确 `light|managed|local|ask`：

- `light`：既有轻量任务；
- `managed`：创建 `engine=managed` 的既有 parse task；
- `local`：保持人工回传页，零任务；
- `ask`：明确三选一，不回写默认策略。

禁止把 `local` 偷换为自动 runtime。设置页与解析按钮必须区分“轻量解析”“本机自动 OCR”“人工本地回传”；不得显示绝对路径、模型目录或命令。管理式 runtime 未就绪时固定提示并保留人工回传入口，不得静默降级 light。

### M4：管理员安装与真实烟测

只有用户/管理员明确授权后才允许：

1. 在仓外独立目录准备兼容 Python、MinerU CLI 和本地模型；优先使用空间充足的卷，但不硬编码 D 盘。
2. 生成精确 manifest，并按目标卷校验 `requiredFreeBytes`。
3. 串行运行 static、合成 DOCX、ASCII 扫描 PDF、Windows 中文扫描 PDF 真实门。
4. 使用隔离数据库/uploads 与临时账号验证上传两份扫描 PDF、managed 任务、取消/失败/成功、确定性合并和 editor-state 刷新。

缺 CLI/模型时必须得到 `status=not_ready`、固定 code、非零退出和 `didNotRunRealRuntime=true`；不得计入 passed。M4 未执行前，V1-M 只能声明“自动接线代码已完成，真实 runtime 未验收”。

## 5. M1 manifest 与输出契约

manifest 位于仓外 runtime 根，JSON 结构示例如下；示例中的 `requiredFreeBytes` 只展示严格整数类型，不是发布容量：

```json
{
  "schemaVersion": 1,
  "engine": "mineru",
  "cliRelativePath": "venv/Scripts/mineru.exe",
  "modelMarkerRelativePath": "models/.biaoshu-ready",
  "requiredFreeBytes": 1
}
```

规则：

1. 顶层必须精确五键；类型严格，拒绝 bool 冒充整数、额外键、绝对路径、`..`、反向/正向目录穿越、URL、UNC 和符号链接/reparse 绕出 runtime 根。
2. Windows CLI 必须是普通非符号链接 `.exe`；模型 marker 必须是根内普通小文件。
3. `requiredFreeBytes` 必须是大于零的严格整数，来自实际部署 bundle/manifest，不由代码猜测模型需要 20 GB 或其它假精确值；预检检查 manifest 所在目标卷。
4. manifest、runtime、模型和缓存不得提交 Git，也不得放入 uploads、backend venv、仓库 TEMP 或备份业务根。

新 preflight 输出精确九键：

`ok/status/engine/mode/diagnosticCode/message/runtimeVerified/didNotRunRealRuntime/qualityProfile`。

`status` 只允许 `ready|passed|not_ready|failed`；`mode` 只允许 `dry-run|ocr-check`；`qualityProfile` 只允许 `ascii|windows-zh`。至少冻结：

- `static_ready`：静态就绪、未运行 parser；
- `ocr_passed`：真实 CLI 处理 image-only PDF 并命中全部锚点；
- `runtime_manifest_invalid`；
- `cli_missing`；
- `model_missing`；
- `disk_insufficient`；
- `quality_precondition_failed`；
- `parser_failed`；
- `parser_timeout`；
- `output_invalid`；
- `ocr_marker_missing`；
- `interrupted`；
- `internal_error`。

所有消息固定中文，不回显原始值、路径、命令、正文、字体、异常类名或第三方日志。

## 6. OCR fixture 反假绿

必跑 ASCII fixture：

1. 两页图像，锚点分别为 `BIAOSHU_OCR_P1_V1` 与 `BIAOSHU_OCR_P2_V1`；两页顺序必须保持。
2. 锚点先绘制到像素图，再将图像嵌入 PDF；禁止把字符串写进 PDF metadata、文本对象、文件名或 parser 提示。
3. 测试源码可以定义期望锚点，但 fixture 生成后必须由 `pypdf` 证明页面文本层为空；假 runner 不能读取测试常量后直接回填冒充真实 OCR。
4. 真实成功要求唯一 Markdown 同时含 P1、P2 且顺序正确。

Windows 中文 profile：

1. 只读系统字体候选，像素内容使用两页不同中文短句和 ASCII 伴随锚点。
2. 缺字体或非 Windows 为 did-not-run，不计 passed；不得下载字体。
3. 中文未精确命中只能 `ocr_marker_missing` 或后续明确的 partial 报告，不能把 ASCII 命中写成中文质量通过。

旋转、空页、表格、整本真实标书和版面复原不进 M1；它们必须在真实 runtime 基线通过后另做质量增强。

## 7. 权限、隐私与进程边界

1. 浏览器只提交策略/任务，不提交宿主机路径、manifest、命令、模型目录、额外 argv 或 executable。
2. 后端输入只来自 `workspace_id + project_id + role=source` 的数据库行和服务端 `stored_name`；必须拒绝 symlink/reparse/path traversal。
3. managed task 继续受 required Cookie、CSRF、活动 workspace 和 strict `bid_writer` 边界；disabled 保持个人版兼容。
4. runtime 环境只保留必要系统变量；全部可写 HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP/TMPDIR/HF/Torch/Python 缓存绑定单次 TEMP，强制离线并剥离代理、API Key、Cookie、CSRF、票据和业务配置。
5. 固定 argv、`shell=False`、固定 cwd；stdout/stderr 丢弃或仅进入不对外的有界诊断映射，禁止仓库日志保存正文。
6. 成功、失败、超时、取消和中断均清理单次 TEMP。Windows 孙进程完整回收若无法证明，必须作为残余风险记录，不得声称百分百治理。
7. M1-M3 禁止联网安装、模型下载、真实业务文件、真实数据库/uploads、防火墙或浏览器自动安装。

## 8. 严格文件白名单

### M1 failure-first

只允许：

1. 新增 `tools/local-parser/test_managed_runtime_preflight.py`；
2. 修改 `tools/local-parser/test_mineru_callback_helper.py`，仅增加 env/cwd 失败门。

### M1 production

只允许：

1. 新增 `tools/local-parser/managed_runtime_preflight.py`；
2. 修改 `tools/local-parser/mineru_callback_helper.py`；
3. 修改 `tools/local-parser/README.md`。

禁止修改后端、前端、依赖、V1-C `runtime_preflight.py`、Docling helper、数据库、启动脚本、真实 `.env` 或 Git 忽略规则。

### M2 failure-first

只允许：

1. 新增 `backend/tests/test_v1m_managed_parse_m2.py`；
2. 修改 `backend/tests/test_parse_engines.py`；
3. 修改 `backend/tests/test_parse_export.py`；
4. 修改 `tools/local-parser/test_managed_runtime_preflight.py`，仅锁共享 core 抽取后对外行为不变。

任何其它既有 task/revision 测试若出现真实契约冲突，必须由 Codex 先列出精确文件名后才可扩围。

### M2 production

只允许：

1. 新增 `tools/local-parser/managed_ocr_runtime_core.py`；
2. 修改 `tools/local-parser/managed_runtime_preflight.py`；
3. 新增 `backend/app/services/managed_parse_runtime_service.py`；
4. 修改 `backend/app/services/task_service.py`；
5. 修改 `backend/app/services/file_service.py`；
6. 修改 `backend/app/services/editor_state_service.py`；
7. 修改 `backend/app/services/project_service.py`；
8. 修改 `backend/app/core/config.py`；
9. 修改 `backend/.env.example`，只说明 manifest path，不得提供 executable/argv/model override。

`mineru_callback_helper.py` 默认禁止修改；只有 failure-first 证明 service/core 无法实现不高于 1 秒取消轮询时才重新确认。`parse_engines.py`、API/Schema、前端、数据库迁移、新 revision source、真实 `.env`、任意路径 API、服务端安装器和公网解析均禁止。

### M3

M3 必须在开工前重新冻结精确文件表；不得沿用 M2 白名单扩写前端。

闭环文档为本契约、实施计划、`HANDOFF-next.md`、路线图和联调清单。

## 9. 测试分层

验收报告必须分开列出：

1. `automated`：参数、manifest、fixture、JSON、边界；
2. `fake-runtime`：假 exe/假 Popen，只证明包装安全；
3. `real-runtime`：真实 CLI+模型，默认 did-not-run；
4. `quality`：ASCII/中文 image-only PDF 质量，默认 did-not-run。

禁止合并 passed 分母，禁止 skip/xfail，禁止按机器是否安装 CLI 决定普通单测断言，禁止用 fake runtime 的 Markdown 命中宣称真实 OCR 通过。

## 10. 非目标

V1-M 不做在线解析服务、远程 API、Docker/WSL、浏览器启动本机程序、自动联网安装、GPU/VLM 调优、Docling 自动接线、全文版面复原、OCR 置信度 UI、批量后台队列、PostgreSQL/Celery、协同光标、评论审批、强制锁或公网 SaaS。

## 11. V1-M 完成定义

只有 M1-M3 代码、独立验收、中文提交和文档闭环全部推送，且 M4 真实 runtime/双扫描 PDF/隔离业务链实际通过，才能把“扫描 PDF 自动解析”计为真实可用并重新核算 V1 完成度。M1 文档、假 CLI 或当前 `cli_missing` 均不得上调 94% 口径。
