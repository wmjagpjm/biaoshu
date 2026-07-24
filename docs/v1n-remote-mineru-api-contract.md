<!--
模块：V1-N 远程 MinerU 批量解析契约
用途：冻结官方本地文件批量上传协议、本系统安全/隐私策略、独立 engine=remote_mineru 旁路与有限诊断码。
对接：https://mineru.net/apiManage/docs；V1-M managed 旁路模式；task_service parse finalizer；Settings Token。
二次开发：禁止把云端 SLA、可取消/可删除、部分写回、原文件名出站或托管 runtime 复用写进实现；生产须 failure-first 后另授权。
-->

# V1-N 远程 MinerU 批量解析契约

> **状态：契约/计划 + test-only failure-first（本任务）；生产实现未授权。**
> **工作树：** `C:\Users\Administrator\biaoshu-v1m-m3-b`，基线 HEAD=`6e513c328c3a1e60c6625cfb231c76b56d63e97e`（M3 前端 test-only，禁止改写）。
> **官方文档：** https://mineru.net/apiManage/docs（精准解析 API · 本地文件批量上传解析）。
> **本任务授权：** 四文件 test-only/契约/计划返修；**不是**生产接线授权。

## 1. 目标与非目标

### 1.1 目标

在**不修改** `parse_engines` 注册表、不复用 managed runtime/manifest/诊断码的前提下，新增独立解析引擎名 **`remote_mineru`**：

1. 由 `task_service._run_parse` 与 `managed` **并列旁路**（`engine` 精确字符串匹配）。
2. 使用 MinerU 官方 **本地文件批量上传** 协议：
   `POST /api/v4/file-urls/batch` → 顺序 `PUT` 每个**绝对 HTTPS** 预签名 URL →
   `GET /api/v4/extract-results/batch/{batch_id}` → 下载**绝对 HTTPS** ZIP → 安全读取每个结果**唯一** `full.md`。
3. 成功路径复用 M2 的 `_validate_parse_sources`、固定分隔符聚合、`_enforce_markdown_caps`、`_parse_finalize_success` **五域单事务**。
4. **失败路径分层**：client ZIP 下载/ZIP 安全/`full.md` 读取解码阶段 cap → `RemoteMineruError(zip_unsafe|output_invalid)` → task 写 remote 二键；runner 正常返回后 task 防御性 `_enforce_markdown_caps` 超限 → 固定中文 `error` + `result is None`；共享输入门/CAS/finalizer/已取消任务沿用既有 task 语义（`result is None` 或 `status=cancelled`）；对外文案仅有限中文；敏感字段零泄漏。

### 1.2 非目标（本任务与 V1 首版均禁止）

| 禁止项 | 说明 |
| --- | --- |
| 注册 `parse_engines` | 不得 `register_engine("remote_mineru")` 或任何 subprocess 进入注册表 |
| 复用 managed runtime | 禁止 manifest、CLI、`managed_ocr_runtime_core`、managed 诊断码 |
| 自动单文件 URL 模式 | `POST /api/v4/extract/task` 是**有效的公网 URL 单文件入口**，但 **V1 的 source 是本机 uploads**，公网 MinerU **无法访问本机/LAN URL**；V1 **禁止**自动 fallback 到该入口。未来仅可对**显式公网 HTTPS source 类型**另开契约 |
| URL 批量模式 | 不用 `POST /api/v4/extract/task/batch` |
| Agent 轻量 API | 不用 `/api/v1/agent/*` |
| HTML 专用模型 | 不用 `MinerU-HTML`；本系统固定 `model_version="vlm"` |
| 回调推送 | 省略 `callback`/`seed`；仅轮询 |
| 云端取消/删除保证 | 官方无公开可靠取消/删除；不得写“云端已删除” |
| 外网自动测试 | 所有自动化测试仅内存 transport；模块级 socket/DNS/默认 httpx 熔断 |
| 真实 Token 落盘 | 禁止使用/复述/搜索此前聊天泄漏 Token；未来仅允许 env `BIAOSHU_REMOTE_MINERU_TOKEN` |

## 2. 官方事实 vs 本系统策略

> 下列「官方」摘自 mineru.net 文档公开说明；「本系统」为本仓库冻结策略。二者不可混写为“官方要求”。

| 维度 | 官方事实 | 本系统策略（冻结） |
| --- | --- | --- |
| 批量上传入口 | `POST https://mineru.net/api/v4/file-urls/batch`，Header `Authorization: Bearer <token>` + `Content-Type: application/json` | 同固定 host/path；**基址不可被 payload/env 覆盖** |
| 申请 body | 可含 `files[{name,data_id,is_ocr,page_ranges}]`、`model_version` 等 | **仅** `files` + `model_version="vlm"`；`files[]` **精确三键** `name`/`data_id`/`is_ocr`；扫描件策略固定 **`is_ocr=true`（布尔 True）**；省略 `callback`/`seed`/`extra_formats`/`language`/`enable_*`/`page_ranges` |
| 上传 | 对返回 `file_urls[i]` 顺序 `PUT` 本地文件；**无须设置 Content-Type**；上传后系统自动提交解析 | **仅接受公网 HTTPS 目标**：scheme=`https`、hostname 非空、**无 userinfo/fragment**、端口仅默认或 **443**；每次外部请求**紧前**经可注入 `resolve_addresses_fn` 验证全部解析地址为 **global public**（拒绝回环/私网/link-local/metadata/IPv6 私网与混合含非公网）；**全列表静态预检**失败（`http`/相对/协议相对/空 host/非法端口/userinfo/fragment/首检非公网）→ `api_response_invalid` 且 **整批零 PUT**；**每 URL 紧前 JIT resolve** 失败（rebinding 变私网）→ **仅当前 URL 零 PUT**，已成功 PUT **不可撤销**（见 §4.4:183-184）；PUT **不设 Content-Type**；**不**额外调用提交接口；首版 **仅接受 HTTP 200**（3xx 一律失败且 **不跟随 Location**） |
| 顺序对应 | 官方示例按序 PUT | **集成假设（待真实烟测确认，不冒充官方保证）**：`file_urls[i]` 与申请 `files[i]`/本地 sources 顺序一一对应；实现按序 PUT，但对账主路径仍以 `data_id` 为准 |
| 轮询 | `GET /api/v4/extract-results/batch/{batch_id}`，Bearer；状态含 `waiting-file\|pending\|running\|converting\|done\|failed` | 状态集合精确同上；**未知状态=协议失败**；任一 `failed`=整批失败；**全部 `done` 才下载**；禁止部分写回；3xx → `api_request_failed` 且零跟随 |
| 轮询节奏 | 文档示例常见 interval≈3s；**无公开 SLA** | 本系统策略：**3 秒间隔 / 30 分钟总墙钟**（含信号量等待）；`sleep`/`clock` 可注入；**不得声称官方 SLA** |
| 结果 ZIP | `full_zip_url` 下载后含 `full.md` 等 | **仅公网 HTTPS**（同 PUT URL 门：无 userinfo/fragment、443/默认端口、可注入 resolver 全地址 global public）；非法形态 → `api_response_invalid` 且 **零 ZIP GET**；3xx → `zip_download_failed` 且零跟随；下载须 **stream 累计** `MAX_ZIP_BYTES`；**压缩单块前门（V1）**：ZIP GET 须显式 `Accept-Encoding: identity` 并拒绝非 identity/`Content-Encoding`，**或** `iter_raw` 且每块只接受 `remaining+1`（禁止 `iter_bytes` 透明解压后先完整 `extend` 再判 cap）；超 cap 后不得继续读 canary；**安全**查找唯一 basename=`full.md`；读取/解码/聚合过程中同步执行单文件与累计 **2MiB UTF-8 / 1,000,000 码点** cap（禁止 content/read 后再限）；UTF-8 **严格**解码；**空白 full.md → `output_invalid`**；非 UTF-8 → **`output_invalid`（唯一码，禁止二选一）** |
| `data_id` | 可选业务标识（≤128，字母数字._-） | **每文件必填**；随机源冻结为单一 **`uuid.uuid4().hex`**（32 小写 hex）；**仅用于结果对账**；缺键/空结果/本地缺失/重复/未知均 `api_response_invalid` 且零 ZIP GET |
| `file_name` | 结果项可含 `file_name` | **禁止只信 file_name 或结果顺序**；必须 `data_id` 完整一一对账 |
| 文件名出站 | 官方示例可用真实文件名 | **必须**合成 `source-001.<后缀>`…；禁止原文件名/路径/项目名/正文进入 JSON |
| 支持类型 | PDF/图片/Office/HTML 等 | **允许后缀精确 14 项**：`.pdf .png .jpg .jpeg .jp2 .webp .gif .bmp .doc .docx .ppt .pptx .xls .xlsx`（大小写归一）。**`.html` 与 `.txt/.md/.markdown` 及未知后缀**在任何 HTTP 前固定拒绝 |
| 批大小 | 官方每批最多 50、上传链接 24 小时 | 本系统复用既有 source 数量上限（可更低，如 10）；不扩大官方批上限 |
| 取消 | 无公开“取消云端任务必成功”保证 | 本地取消只停止等待；editor/task **零部分写回**；不得声称云端已删除 |
| 重定向 | 未强制 | **任何重定向均失败**；`follow_redirects=False`；Location **零跟随** |
| 代理/环境 | 未强制 | 生产 `trust_env=False`；TLS `verify` **不得关闭** |
| 敏感头 | PUT 用预签名 URL | **POST/GET 轮询带 Bearer**；**预签名 PUT 与 ZIP GET 不带 Bearer/Cookie** |

### 2.1 云端数据不可撤销风险（必须向运维/用户文档复述）

1. 文件一旦 PUT 到预签名 URL，即进入 MinerU 云侧处理链路；**本系统无法保证删除、撤回或覆盖云端副本**。
2. 本地 `cancel` 仅终止本进程等待与落库；云端可能继续解析并保留 ZIP/中间产物直至其自有策略过期。
3. 合成文件名与随机 `data_id` 降低业务元数据出站风险，**不消除**文件二进制内容出站与云侧留存风险。
4. 若合规要求“数据不得出域”，**不得启用** `remote_mineru`，应使用 `lightweight` / `managed` / 人工 `local`。

### 2.2 Token 轮换前置（生产启用前强制）

1. Token **唯一来源**：环境变量 / `.env` 的 **`BIAOSHU_REMOTE_MINERU_TOKEN`**。
2. Settings 字段使用 **唯一 `validation_alias="BIAOSHU_REMOTE_MINERU_TOKEN"`**，且全局 `populate_by_name=False`：禁止 `Settings(remote_mineru_token=...)` 字段名回退构造。
3. 空白或缺失：任务在 **零 HTTP** 下固定失败，`diagnosticCode=token_unconfigured`。
4. **此前协作聊天中若出现过 Token 明文，一律视为已泄漏**：启用前必须在 MinerU API 管理页 **轮换/作废旧 Token**，新值仅写入本机受控环境，**禁止**写入 Git、测试夹具、日志、issue、信箱。
5. 本仓库测试 **禁止**使用真实 Token 形态或用户给出的历史值；仅允许明显合成假值（如 `test-token-not-real`）。

## 3. 架构冻结

```text
浏览器 payload.engine = "remote_mineru"
        |
        v
task_service._run_parse
  ├─ lightweight → parse_engines（既有）
  ├─ managed     → managed_parse_runtime_service（既有，仓外 CLI）
  └─ remote_mineru → remote_mineru_client（本契约，云 API）  [并列旁路]
        |
        | 1) 共享输入门（数量/大小/no-follow 等，零 HTTP）
        | 2) 后缀门（零 HTTP）
        | 3) Token 门（零 HTTP）
        | 4) BoundedSemaphore(1) + 墙钟/取消（等待期亦受约束）
        | 5) POST file-urls/batch
        | 6) 顺序 PUT（绝对 HTTPS）
        | 7) 轮询 extract-results/batch/{batch_id}
        | 8) 全 done → 按 data_id 对账 → 下 ZIP → 安全 full.md
        | 9) 分隔符合并；client 阶段 cap→RemoteMineruError 二键；task 防御性 caps→None
        v
_parse_finalize_success（五域单事务）或 remote 客户端/协议/Token/后缀/client-cap 固定二键 result；共享门/task防御性caps/CAS/finalizer/取消沿用既有 None/cancelled
```

### 3.1 独立性规则

1. **精确引擎名**字符串：`remote_mineru`（去首尾空白后全等；大小写敏感）。
2. **不得**出现在 `parse_engines.list_registered_engines()`。
3. **不得** import 或调用 managed runtime/core/manifest 路径逻辑。
4. **不得**复用 managed 诊断码集合；remote 使用 §6 有限集。
5. lightweight / managed 既有分支语义 **零回退**。
6. **禁止**将 `POST /api/v4/extract/task` 作为 V1 本地 source 的自动 fallback。

### 3.2 进程内并发

- 模块级 `REMOTE_SEMAPHORE = threading.BoundedSemaphore(1)`（名称冻结）。
- 获取信号量的**等待期**与**持有期**均受 **取消检查** 与 **总墙钟 30 分钟** 约束。
- 未取得锁 **不得** `release`；等待期取消/超时 **全程零 HTTP**。
- 不声称跨进程互斥。

## 4. HTTP 与客户端行为

### 4.1 固定端点

| 步骤 | 方法 | URL | 认证 |
| --- | --- | --- | --- |
| 申请上传链接 | POST | `https://mineru.net/api/v4/file-urls/batch` | `Authorization: Bearer <token>` |
| 上传文件 | PUT | 响应 `file_urls[i]`（须绝对 HTTPS） | **无** Bearer / Cookie；**无** Content-Type |
| 查询批次 | GET | `https://mineru.net/api/v4/extract-results/batch/{batch_id}` | Bearer |
| 下载结果 | GET | 结果项 `full_zip_url`（须绝对 HTTPS） | **无** Bearer / Cookie |

### 4.2 httpx 生产默认（行为可测，禁止仅常量自证）

- 真实 `run_remote_mineru_parse` 内构造的 `httpx.Client` 必须可被测试拦截构造参数证明：
  - `verify=True`（**显式**；禁止依赖公开属性 getattr 恒真）
  - `trust_env=False`
  - `follow_redirects=False`
  - **零代理**（不传 proxy / 不经 trust_env 代理）
- V1 最小生产可实现性门：`每请求重新校验 URL+解析` + `trust_env=false` + `redirects=false`。
- **残余风险如实记录**：在标准 httpx 下**不能**绝对消除权威 DNS 在“校验”与“连接”之间的 rebinding 残余；**不得**假称连接级 pinning / 强制 extra 空壳 Client 子类。
- 支持构造注入 `transport`（测试用 `MockTransport`）；**生产若忽略注入 transport，测试必须立即失败且绝不出网**
- 支持注入 `sleep_fn`、`clock_fn`、`resolve_addresses_fn`（测试禁止真实 DNS/等待）
- 调用期日志隐私：在**当前 remote 调用线程**临时安装中间记录 `logging.Filter` 抑制 `httpx`/`httpcore` URL/头泄漏；`finally` **只移除本次 filter**；**禁止**永久改写全局 logger level；旁路线程日志须仍可见

### 4.3 请求 body 精确形状（POST）

```json
{
  "files": [
    {"name": "source-001.pdf", "data_id": "<32-hex>", "is_ocr": true},
    {"name": "source-002.docx", "data_id": "<32-hex>", "is_ocr": true}
  ],
  "model_version": "vlm"
}
```

- `name`：`source-{序号:03d}{归一后缀}`，序号从 1 起，与 ASC 源文件顺序一致。
- `data_id`：每文件独立随机；批次内唯一；仅 [0-9a-f] 32 字符或等价 UUID hex（无连字符）。
- `is_ocr`：必须为 JSON 布尔 `true` / Python `True`（扫描件策略固定开启 OCR）。
- JSON 中 **禁止**出现：原文件名、绝对/相对路径、项目 id/名、workspace、正文片段、Token。

### 4.4 响应与对账

1. POST 成功：HTTP 200 且 `code==0`，`data.batch_id` 非空字符串，`data.file_urls` 为 list 且 **len == 源文件数**；每项为合法绝对 HTTPS。
2. 任一条件不满足 → `api_response_invalid`（畸形/对账/非法 URL）或按 HTTP/网络归类 `api_request_failed`。
3. PUT：仅 **200** 成功；3xx/`!=200` → `upload_failed`；部分失败亦 `upload_failed`；已发请求不得假装未上传。
4. GET 轮询：`code==0`；`extract_result` 为 list；3xx → `api_request_failed`。
5. 对账：
   - 为每个本地 `data_id` 找到 **恰好一条** 结果项；
   - 结果中出现未知 `data_id`、重复 `data_id`、缺失项、空结果、结果项缺 `data_id` 键 → `api_response_invalid`；
   - **禁止**仅按 list 下标或 `file_name` 匹配作为主对账。
6. 状态机：
   - 集合内非终态（`waiting-file|pending|running|converting`）→ 继续轮询；
   - 任一 `failed` → 立即 `remote_parse_failed`（不下载其余 ZIP）；
   - 全部 `done` → 进入 ZIP 阶段；
   - 未知 `state` → `api_response_invalid`。
7. 超时：单一 **monotonic deadline** 覆盖 semaphore 等待、全部 HTTP、sleep、stream；每请求 `request.extensions["timeout"]` 的 connect/read/write/pool 均 **非空且 ≤ 当时 remaining**；超过 30 分钟墙钟 → 唯一 `poll_budget_exceeded`。同步 DNS/HTTP **无法** Python 强占式打断合作式等待；禁止双 sleep 硬切断。
8. 取消：每外部动作前检查 `cancel_check()`；client 内部抛 `interrupted`。任务接线层：若 API 任务**已被取消**则最终精确 **`cancelled`**（沿用既有 task 语义）；否则才 `failed`+`interrupted` 二键。**禁止** or 双放行。
9. PUT 紧前：no-follow/reparse/identity/`expected_size` 再校验，并从**同一已验证句柄**上传；Windows/POSIX 测试 seam 可实现（不要求本机不存在的 `O_NOFOLLOW`）。
10. remote 单文件固定 **`<= 200_000_000` bytes**（官方十进制 200MB）；本地 managed **200MiB** 门不改。
11. URL 门（PUT 与 ZIP 对称）：scheme=`https`、hostname 非空、无 userinfo/fragment、端口仅默认或 **443**（显式 `:443` 与缺省端口规范化等价）；可注入 resolver 全地址须 global public。非法形态 → `api_response_invalid`。
12. URL 解析时机：
    - **全列表静态预检**失败（任一 URL 形态/首检解析非公网）→ **整批零 PUT**；
    - **每外部请求紧前 JIT resolve** 失败（rebinding 变私网）→ **仅当前 URL 失败**，已成功 PUT 不得假装未上传；双文件第二 URL rebinding → 第一 PUT 精确一次、第二 PUT 零。

### 4.5 ZIP 安全（禁止 `extractall`）

建议冻结常量（实现与测试双端一致）：

| 常量 | 值 |
| --- | --- |
| `MAX_ZIP_BYTES` | 256 MiB（下载字节上限，须流式计数行为门） |
| `MAX_ZIP_MEMBERS` | 4096（成员数行为门） |
| `MAX_ZIP_UNCOMPRESSED_BYTES` | 512 MiB（成员 `file_size` 声明解压总量行为门） |

结构安全拒绝条件 **1-4**（命中任一 → **`zip_unsafe`**；坏下载另码）。**5-8 使用各自专码，不并入 `zip_unsafe`**：

1. 绝对路径成员、`..` 穿越、反斜杠路径穿越 → **`zip_unsafe`**；
2. **Windows drive**（如 `C:/...`）与 **UNC**（如 `//server/share/...` 或 `\\server\share\...`）→ **`zip_unsafe`**；
3. 符号链接 / FIFO / device 等特殊项（zipfile 可识别的 external_attr/类型）→ **`zip_unsafe`**；
4. 加密项（flag）、坏 ZIP、成员数超限、声明 uncompressed 总量超限、下载字节超限 → **`zip_unsafe`**；
5. 不存在 basename=`full.md` → **`zip_full_md_missing`**（非 zip_unsafe）；
6. 多个 basename=`full.md` → **`zip_full_md_ambiguous`**（非 zip_unsafe）；
7. `full.md` 非 UTF-8 严格可解 → **`output_invalid`**（非 zip_unsafe）；
8. `full.md` 解码后空白（仅空白/空串）→ **`output_invalid`**（非 zip_unsafe）。

**允许**：嵌套目录下唯一 `full.md`（例如 `subdir/full.md`），按 basename 匹配。

下载 ZIP：仅 200；3xx/非 200/网络失败 → `zip_download_failed`（**不跟随** Location）。

### 4.6 聚合与落库（复用 M2）

1. 多文件 Markdown 顺序 = **parse 专用 ASC**（`created_at ASC, id ASC`）源顺序；之间插入精确
   `\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n`
   （`_SOURCE_SEPARATOR`）。
2. 结果 ZIP 打乱时仍按本地源 ASC 聚合；ZIP GET 仅按精确 `full_zip_url` 计数，**禁止**把轮询 URL 误计为 ZIP。
3. **caps 分层**：
   - **client**：ZIP 下载/ZIP 安全/`full.md` 读取解码阶段超限 → `RemoteMineruError(zip_unsafe|output_invalid)` → task 写 remote 二键；
   - **task 防御性**：runner 正常返回后 `_enforce_markdown_caps`（1,000,000 码点 / 2 MiB UTF-8）超限 → 固定中文 `error` + `result is None`（禁止 remote 二键冒充）。
4. 成功：`_parse_finalize_success`；result 精确
   `{"engine":"remote_mineru","fileCount":N,"chars":M}`。
5. **remote 客户端/协议/Token/后缀/client-cap 类**失败：`{"engine":"remote_mineru","diagnosticCode":"<code>"}` + 固定中文 `error`（`message_for_code` 单点）。共享输入门/task防御性caps/CAS/finalizer 失败沿用既有 task 语义（`result is None` + 固定 `error` 文案）；API 已取消 → `status=cancelled`。
6. CAS / `expectedStateVersion` / finalizer 故障语义与 M2 **不回退**；finalizer 写点 `commit=False`，成功路径最终 `commit` 精确一次。

## 5. 隐私与日志红线

以下字段 **不得** 进入：应用日志、`logger.exception` 可观测消息、异常 `str`/`message`（对外）、task API 的 `error`/`result`/`message`、SSE snapshot、`task_to_dict`、任务事件账本、数据库 task 业务字段：

- Token 原文或可逆片段
- `Authorization` 头值
- `batch_id`
- `data_id`（含 POST 生成的真实 data_id）
- 预签名 URL / `full_zip_url`
- 云端 `code`/`msg`/`trace_id`/`err_msg` 原文
- 本地绝对路径（含 TEMP）、原文件名、正文片段

日志异常只允许 **固定中文** 或 **固定诊断码**（禁止拼接第三方原文）。
每个 `RemoteMineruError` 的 `message` 必须 **精确等于** `message_for_code(code)`；未知码折叠 `internal_error`。

## 6. 有限诊断码

| diagnosticCode | 固定中文（`message_for_code` 单点） | 典型触发 |
| --- | --- | --- |
| `token_unconfigured` | Token 未配置 | 空白/缺失 Token，零 HTTP |
| `source_type_unsupported` | 源文件类型不受支持 | 后缀拒绝，零 HTTP |
| `api_request_failed` | 远程接口请求失败 | HTTP 非 200/3xx/网络/超时（申请/轮询）；retryable |
| `api_response_invalid` | 远程接口响应无效 | 畸形 JSON、数量不匹配、未知状态、data_id 对账失败、非法 PUT/ZIP URL |
| `api_auth_failed` | 远程鉴权失败 | **保留码位**；V1 **不**根据无官方证据的猜测数字 code 细分映射（见下行） |
| `api_quota_exceeded` | 远程配额不足 | **保留码位**；V1 不臆测官方数字 code |
| `api_busy` | 远程服务繁忙 | **保留码位**；retryable；V1 不臆测官方数字 code |
| `api_input_rejected` | 远程拒绝输入 | **保留码位**；V1 不臆测官方数字 code |
| `api_upstream_error` | 远程上游错误 | **V1：HTTP 200 且 `code!=0` 一律折叠为本码**（不透传官方 code/msg/trace；禁止 40101/40201/… 臆测映射） |
| `source_size_exceeded` | 源文件超过远程单文件上限 | 单文件 > 200_000_000 bytes，零 HTTP |
| `source_identity_mismatch` | 源文件身份校验失败 | PUT 紧前 size/identity/no-follow 不一致 |
| `upload_failed` | 文件上传失败 | PUT 非 200/3xx/部分失败 |
| `poll_budget_exceeded` | 轮询超时 | 超过 30 分钟墙钟（含等待信号量） |
| `remote_parse_failed` | 远程解析失败 | 任一项 state=failed |
| `zip_download_failed` | 结果包下载失败 | ZIP GET 失败/3xx |
| `zip_unsafe` | 结果包不安全 | 穿越/drive/UNC/symlink/FIFO/加密/超限/坏 ZIP 等 |
| `zip_full_md_missing` | 缺少 full.md | 零个 basename full.md |
| `zip_full_md_ambiguous` | full.md 不唯一 | 多个 basename full.md |
| `output_invalid` | 输出无效 | 非 UTF-8 / 空白 full.md / caps 前非法聚合 |
| `interrupted` | 操作已中断 | 取消（含信号量等待期） |
| `internal_error` | 内部错误 | 未分类异常（脱敏） |

- 云 `code/msg/trace_id/err_msg` **不透传**；**V1** 在 HTTP 200 下 `code!=0` **统一** `api_upstream_error`（禁止无官方证据的细粒度数字映射）；畸形 JSON/结构才用 `api_response_invalid`。HTTP 非 200/网络仍按申请/轮询 → `api_request_failed` 等。
- 未知内部 code / 普通异常一律折叠为 `internal_error`。
- **失败 result 形态（任务层）**：
  - **remote 客户端/协议/Token/后缀/client ZIP·full.md cap** 错误 → 安全二键 `engine/diagnosticCode`；
  - 共享输入门 / **task 防御性 caps** / CAS / finalizer → `result is None` + 固定中文 `error`（**禁止** remote 二键冒充）；
  - API 已取消 → `status=cancelled`（**禁止**写 remote 失败二键）。

## 6.1 契约摘要总表（caps / CAS / finalizer / 取消）

| 主题 | 冻结规则 |
| --- | --- |
| caps | **client**：ZIP 流式/`full.md` 读取超限 → `zip_unsafe`/`output_invalid` 二键；**task 防御性** `_enforce_markdown_caps` 超限 → `result is None` |
| CAS | 创建时写入 `payload_json._expectedStateVersion`（非空字符串）；finalizer `expected_state_version` 原样；冲突 → `ERR_TASK_BASE_CHANGED` + `result is None` |
| finalizer | upsert/project/`_set_task(success)` 均 `commit=False`；成功唯一 `commit`；任一点失败五域回滚 + `result is None` + success event=0 |
| 取消 | API 已取消 → `cancelled`；client `interrupted` 且未取消 → `failed`+二键 `interrupted`；禁止 or 双放行 |
| 日志 | 线程内临时 Filter；finally 只摘 filter；level 原值；旁路线程可见 |

## 7. 配置

| 项 | 规则 |
| --- | --- |
| env | 仅 `BIAOSHU_REMOTE_MINERU_TOKEN` |
| Settings 字段名 | `remote_mineru_token: str = Field(default="", validation_alias="BIAOSHU_REMOTE_MINERU_TOKEN")` |
| populate_by_name | `False`（全局已是） |
| `.env.example` | 中文注释说明用途、轮换要求、禁止提交真实值；默认空 |
| API 基址 | 代码常量，拒绝配置覆盖 |

## 8. 测试契约（failure-first）

### 8.1 文件与范围

仅允许本任务四文件：

1. `docs/v1n-remote-mineru-api-contract.md`（本文）
2. `docs/plans/2026-07-23-v1n-remote-mineru-api-plan.md`
3. `backend/tests/test_v1n_remote_mineru_client.py`
4. `backend/tests/test_v1n_remote_mineru_parse_task.py`

### 8.2 强制规则

- **懒导入** production；缺失时 **可收集** 且 **业务红**；禁止 collection error / skip / xfail / monkeypatch 假实现冒充绿。
- 自动测试 **零外网**：autouse 全局 socket/DNS/默认 httpx 熔断；仅 MockTransport / 内存 ZIP。
- **禁止**源码子串/hasattr/签名常量/恒真集合/`assert ... or True`/条件 return/`except Exception: pass` 替代行为证据。
- client：POST 三键+`is_ocr is True`、非法 URL、3xx 分诊、ZIP 安全全门、上限行为、对账、信号量等待期、动态 canary。
- task：绝对测试库路径比对、Settings alias 隔离、分流行为证据、14 后缀、共享输入门、ASC 聚合、caps、真取消映射、finalizer H1/H3 缩编、隐私 canary、AST 自守卫。
- 测试文件 **不得复制** production finalizer/ZIP 实现；不得修改既有 M2 文件。

### 8.3 验收命令（test-only / Codex 终验，全部串行一次）

```powershell
cd C:\Users\Administrator\biaoshu-v1m-m3-b\backend
# 1) py_compile
.\.venv\Scripts\python.exe -m py_compile tests\test_v1n_remote_mineru_client.py tests\test_v1n_remote_mineru_parse_task.py
# 2) 正常 conftest helper 定向门（固定集合；若新增独立 fd-reuse helper 须同步纳入 -k）
.\.venv\Scripts\python.exe -m pytest tests\test_v1n_remote_mineru_client.py tests\test_v1n_remote_mineru_parse_task.py -k "ast_self_guard or sparse_and_transport_helper_self_proof or read_guard_helper_self_proof or worker_cleanup_helper_self_proof or fd_reuse_helper_self_proof" -q --tb=short
# 3) collect-only
.\.venv\Scripts\python.exe -m pytest tests\test_v1n_remote_mineru_client.py tests\test_v1n_remote_mineru_parse_task.py --collect-only -q
# 4) 两文件合并 failure-first
.\.venv\Scripts\python.exe -m pytest tests\test_v1n_remote_mineru_client.py tests\test_v1n_remote_mineru_parse_task.py -q --tb=short
cd ..
git status --short
git diff --check
```

**whitespace / cached check 说明：**

- `git diff --check` **只检查 tracked 工作区**，不能证明当前**未跟踪**四文件无 whitespace 问题。
- Codex 最终：先核对精确四白名单文件后 **暂存**，再执行 `git diff --cached --check`；失败禁止提交。
- Grok 本轮 **禁止暂存**，故 `review_request` 只能声明 `git diff --cached --check` **did-not-run**，不得假称通过。

生产未实现时：收集成功、断言 **业务红**（failed>0）；**不得**为变绿改 production。
状态保持：**production 未授权**；真实外网 / Token **did-not-run**。

### 8.4 V1 发布高风险门（Q1–Q7，不可后置）

> 本节为 **V1 发布门**：双方已确认 YES（Codex `msg_d5699a2489e84998b8c274c70c55b85c` / Grok `msg_2b226ff3bc534ba1b3413ccd3c51ba52`；全局 `_raise` 回归 `msg_4f0cf83b325c4738921c090ecd4858c0` / `msg_c781a179da2640588329fbdbede66230`）。**禁止**把「同尺寸内容稳定」或下列任一门后置到 V1 之后。测试节点关键字：`v1n_release_gate`。
>
> **Q6/Q3/Q7 反假绿返修确认链（TEST-Q8）：** Codex question `msg_342a4905f7ad45d696d9d49600385a28` → Grok B Q1–Q4 全 YES `msg_7f6614c9ddfe41fc91aa797806003655` → 授权 task `msg_ec9c5957154a44fcb6c941d362d63c5f`。仅 test/docs；四 production 逐字节冻结。

| 门 | 冻结要求 | 失败码（典型） |
| --- | --- | --- |
| **Q1 异常链** | 至少覆盖 `_json_or_invalid`、上传读 `OSError`、`full.md` `UnicodeDecodeError` 正文 marker；公开 `RemoteMineruError` 的 `__cause__`/`__context__`/args/traceback **可达图零 marker**；`diagnosticCode` 与固定中文不变。`_raise` 无论是否处于 active except 均须真正断链（推荐内部 `from None` 后显式清空 context/cause 再 bare raise）；网络路径继续 except 外折叠。 | 既有阶段码（如 `api_response_invalid` / `upload_failed` / `output_invalid`） |
| **Q2 源稳定** | 上传流累计字节 **精确等于** `expected_size`（增长/缩短必红）；PUT **`Content-Length=expected_size`**；Windows `CreateFileW` **禁止 `FILE_SHARE_WRITE`**，并用行为门证明**持有上传句柄时同尺寸改写不能成功**（禁止只断常量）。同尺寸内容稳定为 V1 发布门，不得后置。 | `source_identity_mismatch` / `upload_failed` |
| **Q3 响应 OOM** | POST / PUT / poll **均** `stream=True` 有界读；cap 冻结：`MAX_HTTP_JSON_RESPONSE_BYTES=1_048_576`（POST/poll）、`MAX_HTTP_PUT_RESPONSE_BYTES=65_536`（PUT 丢弃）；超 cap 后 **不得继续读 canary**；保持阶段错误码；合法小响应仍通。ZIP 流式 cap 既有门不回退。**ZIP 压缩单块前门（TEST-Q3-ADD）**：ZIP GET 须显式 `Accept-Encoding: identity` 并拒绝非 identity `Content-Encoding`，**或** `iter_raw` 且每块只接受 `remaining+1`（禁止 `iter_bytes` 透明解压 + 先完整 `extend` 再判 cap）；超大单块后 canary 不可再读。**策略 B 反假绿**：`as_bytes_lens` 与 `buffer_full_lens` 任一出现 `> remaining+1` 完整物化 → 否决 `partial_ok`（禁止 `bytes(chunk)` 整段物化后再切片冒充有界）。 | POST→`api_response_invalid`；PUT→`upload_failed`；poll→`api_response_invalid` 或 `api_request_failed`；ZIP 超限→`zip_unsafe`；编码策略失败→`zip_download_failed`/`zip_unsafe` |
| **Q4 deadline** | PUT 在 JIT resolve 与 `_open_verified_fd` **之后**重新 `_require_remaining`，`timeout=_timeout_for(新 remaining)`；禁止使用 resolve/open 前的旧 rem。 | `poll_budget_exceeded`（耗尽时） |
| **Q5 父目录 reparse** | 以**最终已打开句柄路径**对可信 upload root 做边界校验；可注入 seam **`_v1n_final_path_for_fd(fd)->str`**；`run_remote_mineru_parse(..., trusted_upload_root=...)` 关键字冻结。final-path 越界 → **零 PUT** + `source_identity_mismatch`。禁止仅重复 Path 字符串检查冒充关闭 TOCTOU。RemoteSource 字段集 V1 可保持三字段；根边界经入口 kwarg + seam，**本任务不改 production**。 | `source_identity_mismatch` |
| **Q6 ZIP EOCD** | 在 `ZipFile` **构造前**解析 EOCD / ZIP64 声明 entries；声明成员数 **> 4096** → `zip_unsafe` 且 **ZipFile 构造次数=0**；合法小 ZIP 与坏 ZIP 语义不弱化。**反假绿夹具**：超限必须用**真实一致** 4097 空成员 central directory/EOCD（ZIP64 路径保留真实 4097 CD + ZIP64 EOCD/locator + 经典 0xFFFF sentinel）；禁止仅改 EOCD 声明而真实 CD 仍 1 项；夹具在 ZipFile spy **前**构造或每次 run 前 constructs 清零；合法小 ZIP 须在 run 前清零后证明 production 路径 `constructs > 0`。禁止仅靠 `PK\x01\x02` 字符串计数冒充前门。与 Q3 追加共享：ZIP 3xx→`zip_download_failed`、坏 ZIP→`zip_unsafe` 语义不回退。 | `zip_unsafe` / `zip_download_failed` |
| **Q7 RuntimeError 折叠** | 主路径普通 `RuntimeError(marker)`（经可注入 seam，如 `_synthetic_name`）必须折叠为公开 `RemoteMineruError` + **`internal_error`** 固定中文；`__cause__`/`__context__` 为 None；异常可达图 **零 marker / Token**。**反假绿**：注入 `boom_hits` 精确 **=1**；HTTP hits 精确 **=0**（折叠发生在任何网络请求之前）。网络熔断须用非 `RuntimeError` 基类（如 `_V1NNetFuseError(BaseException)`），**禁止** `except RuntimeError: raise` 把业务异常自缚透传。 | `internal_error` |

**验收（本切片 test-only）：** 仅 `py_compile` + 聚焦 Q3-ZIP / Q6 / Q7 failure-first **一次**；必须保留真实 **failed**；禁止完整 174 项全量。生产修复另授权。

### 8.5 任务接线发布门（路径 / 取消 / 事务，Q4-TASK）

> 前置双方确认 YES：Codex `msg_8e33e60747a34973b40f17528577e5fb` / Grok `msg_6d28cf5ddb514cab9587b01ff65b4348`。
> 测试节点关键字：**`v1n_task_release_gate_q4`**（仅 `test_v1n_remote_mineru_parse_task.py`）。
> **本切片 test-only**；生产 `task_service` 修复另授权。

| 组 | 冻结要求 | 期望观测 |
| --- | --- | --- |
| **G1 上传根/父链** | 上传根→项目目录→叶 **每一级** reparse/symlink 探测失败或 `OSError` 均 **fail-closed**；`upload_dir` 静态 reparse、`project_dir` 检查后被替换为 junction/reparse、任一层 `lstat`/`is_symlink` `OSError`、`nofollow stat` `OSError`（禁止回退 follow stat）一律拒绝。**runner / 外网 HTTP = 0**；`result is None`；五域零写。 | `status=failed`；固定中文 `error`（叶/父 reparse 等既有文案或等价路径拒绝）；公开表面零敏感 marker |
| **G1 协作契约** | `run_remote_mineru_parse(..., trusted_upload_root=...)` + `_v1n_final_path_for_fd(fd)->str` seam；task 接线必须把 **启动时冻结的可信非 reparse upload 根** 传入 client；最终句柄路径须落在该根下，**禁止**仅重复 `Path.resolve` 字符串边界冒充关闭 TOCTOU。RemoteSource 字段集 V1 可保持三字段。 | 缺参/错根 → 业务红；越界 → 零 PUT（client 门） |
| **G2 cancel refresh** | remote（及 managed 代表门）`cancel_check` 内 `db.refresh(task)` **任意失败** 必须 fail-closed：视为 **interrupted**（`cancel_check() is True` 或等价停止），**禁止** `except: return False` 继续 POST/PUT/poll/ZIP/CLI。公开 `error` 固定中文；合成异常 marker / Token / 路径 **零泄漏**。 | remote：`failed` + 二键 `interrupted`；managed：`failed` + `diagnosticCode=interrupted`；fake runner 在 check 后 **外部动作列表精确空** |
| **G3 cancel/finalizer 仲裁** | 两 **真实 Session** + 可控 **barrier**（禁止真实 `sleep` / 宽 `OR` / `autoflush`/`session.dirty`/`flag_modified` / 仅 monkeypatch 返回值自证）：(a) G3a 窗口=**`_upsert_editor_state_for_task` 首写 helper 调用之前**（捕获 worker Session/目标 project 身份后、调用 real helper 前），cancel 独立 Session **真实 commit** 胜出 → 最终精确 **`cancelled`**、`result is None`、editor/revision/project/success event **零部分写回**；harness 须 try/finally 无条件 release + worker join，失败不污染 teardown；(b) finalizer 已合法提交 **success** 后 cancel **不得覆盖** success（反向门独立；需同事务条件更新/CAS 抢终态）。 | (a) `cancelled` + 五域与成功包隔离；(b) 终态保持 `success` + 正文/event 保留 |

**验收（本切片 test-only）：**

```powershell
cd C:\Users\Administrator\biaoshu-v1n-prod\backend
python -m py_compile tests\test_v1n_remote_mineru_parse_task.py
python -m pytest tests\test_v1n_remote_mineru_parse_task.py -k "v1n_task_release_gate_q4" -q --tb=short
```

必须真实 **failed**；禁止再跑 client 7 门或完整 174；生产未授权。

## 9. 生产候选白名单（仅计划引用，本任务禁止写入）

见计划文档精确列表。候选：

- `backend/app/services/remote_mineru_client.py`（NEW）
- `backend/app/services/task_service.py`（旁路接线）
- `backend/app/core/config.py`（Token 字段）
- `backend/.env.example`（说明）

若事实证明需要额外 adapter 模块：必须 **question → 双方确认 → Codex 扩围授权**，禁止先写。

## 10. 完成定义

| 阶段 | 完成标准 |
| --- | --- |
| 本任务（test-only） | 四文件落地；两专项真实 failure-first 红；py_compile/diff-check/白名单干净；review_request 回传计数与哈希 |
| 后续 production（另授权） | 业务转绿；相关回归不回退；仍无真实外网烟测则标明 did-not-run |
| 真实烟测（另授权） | 管理员提供轮换后 Token；受控小文件；人工确认云侧风险；**不**默认进 CI |

## 11. 残余风险

1. 云端留存与司法/合规不可控。
2. 预签名 URL 主机与 CDN 变动导致证书/网络边界需运维关注。
3. 官方新增状态字或字段时，本系统对未知状态 fail-closed，可能误杀。
4. 进程内信号量不防多 worker 进程并发达限。
5. ZIP 炸弹与 zipfile 实现差异：以声明大小 + 成员数 + 下载上限多层防护，不声称完美。
6. `file_urls` 与源顺序对应仅为集成假设，最终以真实烟测与 `data_id` 对账为准。
