<!--
模块：V1-N 远程 MinerU 批量解析实施计划
用途：把契约冻结、failure-first 测试、生产接线与真实烟测拆为可独立授权阶段。
对接：docs/v1n-remote-mineru-api-contract.md；V1-M managed 旁路模式；agent-collaboration。
二次开发：每阶段只动已授权白名单；禁止为变绿偷改 production；Token 轮换前置。
-->

# V1-N 远程 MinerU 批量解析实施计划

> **状态：** 阶段 0–1（契约 + test-only failure-first 反假绿返修）由 Codex 授权本任务；**生产未授权**。
> **工作树：** `C:\Users\Administrator\biaoshu-v1m-m3-b`
> **基线 HEAD：** `6e513c328c3a1e60c6625cfb231c76b56d63e97e`（M3 前端 test-only，禁止改写/暂存）
> **角色：** Grok B 交付文档与红测；Codex 审查/终验/提交；禁止操作 `main` 与 A worktree / M3 文件。

## 0. 约束总表

| 规则 | 要求 |
| --- | --- |
| Git | 禁止 add/commit/push/切分支/stash/reset/checkout（除非 Codex 明文授权） |
| 本任务可写 | 仅 4 个文件（见 §1） |
| 数据 | 禁止读真实 `biaoshu.db`、业务 `uploads`、密钥、Cookie |
| 网络 | 自动测试零外网；模块级 socket/DNS/默认 httpx 熔断 |
| Token | 禁止使用/搜索/复述泄漏 Token；启用前必须轮换；仅 env `BIAOSHU_REMOTE_MINERU_TOKEN` |
| 协议 | V1 本地 source 仅批量预签名路径；禁止自动 fallback 到 `POST /api/v4/extract/task` |

## 1. 本任务白名单（test-only）

| 路径 | 动作 |
| --- | --- |
| `docs/v1n-remote-mineru-api-contract.md` | 契约冻结/返修 |
| `docs/plans/2026-07-23-v1n-remote-mineru-api-plan.md` | 计划冻结/返修 |
| `backend/tests/test_v1n_remote_mineru_client.py` | failure-first 返修 |
| `backend/tests/test_v1n_remote_mineru_parse_task.py` | failure-first 返修 |

**禁止：** 任何 production、既有测试、路线图、交接文档、M3 文件、另一 worktree。

## 2. 后续生产候选白名单（未授权，勿写）

精确候选（实现阶段需 Codex 单独 production 授权）：

1. **NEW** `backend/app/services/remote_mineru_client.py`
   - HTTP 客户端、合成名、data_id、`is_ocr=true`、轮询、ZIP 安全提取、诊断码、`REMOTE_SEMAPHORE`、可注入 transport/sleep/clock。
2. **MODIFY** `backend/app/services/task_service.py`
   - `_run_parse` 增加与 managed 并列的 `remote_mineru` 旁路；**remote 客户端/协议/Token/后缀/client-cap** 写二键 result；共享门/**task 防御性 caps**/CAS/finalizer/取消沿用既有 None/cancelled；零部分写回；**不**改 lightweight/managed 成功路径语义。
3. **MODIFY** `backend/app/core/config.py`
   - `remote_mineru_token` + 唯一 `validation_alias="BIAOSHU_REMOTE_MINERU_TOKEN"`。
4. **MODIFY** `backend/.env.example`
   - 中文说明、默认空、轮换提示；无真实值。

### 2.1 扩围门

若实现时证明必须新增例如 `remote_mineru_parse_service.py` adapter：

1. 发 `question` 说明理由与精确路径；
2. Grok/Codex 双方确认；
3. **等待 Codex 扩围授权** 后才可写入；
4. **禁止**先写后报。

## 3. 阶段划分

### 阶段 0：契约冻结（本任务）

- 写清官方事实 vs 本系统策略：`is_ocr=true` 三键、绝对 HTTPS、不跟随重定向、ZIP 上限与安全门、V1 禁止 extract/task 自动 fallback、`file_urls` 顺序仅为待烟测集成假设。
- **退出标准：** 契约可被测试逐条锚定；无生产代码。

### 阶段 1：failure-first 测试反假绿（本任务）

**顺序：** 先文档，再测试；串行一次合并跑两文件。

#### 1.A client 专项关闭清单（含 C1–C11）

| 节点 | 反假绿要求 |
| --- | --- |
| 夹具 | 嵌套路径先建父目录，或平面动态 canary；禁止 FileNotFoundError 冒充业务红 |
| 熔断 | autouse 全局 socket/DNS/默认 httpx 熔断；忽略 transport 注入必须立即失败且零外网 |
| POST body | `files[]` 精确三键 + `is_ocr is True`；`model_version="vlm"`；禁止 extract/task 自动 fallback |
| 非法 URL / SSRF | 公网 HTTPS only；无 userinfo/fragment；443/默认端口（`:443` 规范化等价）；PUT/ZIP 对称；可注入 resolver 覆盖 IPv4/IPv6 回环、私网、link-local、metadata、IP literal、混合；全列表预检失败=零 PUT；JIT rebinding 只伤当前 URL |
| Client 默认 | 真实 run 拦截构造：`verify=True`、`trust_env=False`、`follow_redirects=False`、零代理；禁止 getattr 恒真/空壳类 |
| 3xx | POST/PUT/轮询/ZIP 分别 → `api_request_failed`/`upload_failed`/`api_request_failed`/`zip_download_failed`；Location 零跟随 |
| ZIP 安全 | Windows drive/UNC；symlink/FIFO/device；加密须 patch local+central 并 reread `flag_bits&1` |
| 上限行为 | stream/iter_bytes：恰好 limit 后再 +1 overflow 即停，不得读 canary；成员 file_size 总和/成员数；full.md 可控 read seam 字节/码点 cap 分别证 |
| data_id | 缺键/空结果/本地缺失/重复/未知 → 精确失败 + 零 ZIP GET；随机源冻结 `uuid.uuid4().hex`；seam 预定值证明 |
| 多文件 | 精确分隔符全文；每非终态精确一次 sleep；全部 done 前零 ZIP；done+running 混态零 ZIP |
| full.md | 空白与非 UTF-8 → 唯一 `output_invalid` |
| deadline/取消 | 单一 monotonic deadline；每阶段 request.extensions timeout≤remaining；唯一 `poll_budget_exceeded`；POST 后/两 PUT 间/ZIP 前/两 ZIP 间取消 |
| 信号量 | 真实 BoundedSemaphore(1)；等待期取消/总 deadline；未取得锁不得 release；over-release 合约 |
| TOCTOU | PUT 紧前 no-follow/reparse/identity/size；同句柄上传；同尺寸漂移零 PUT |
| 单文件大小 | 真实逻辑尺寸 200_000_001 拒 / 200_000_000 接受；managed 200MiB 不变 |
| 错误类 | V1：HTTP 200+code!=0 → 统一 `api_upstream_error`；禁止臆测 40101 等；不透传官方 code/message |
| Cookie | Set-Cookie 后后续零 Cookie jar |
| 隐私 | 线程内临时 Filter（不改全局 level）；旁路线程 sentinel 仍可见；成功/POST/PUT/poll/ZIP/cloud code!=0/state=failed 扫 caplog+异常链 |

#### 1.B task 专项关闭清单（含 T1–T12）

| 节点 | 反假绿要求 |
| --- | --- |
| 隔离 | track upload_root 并清理；`engine.url.database` 绝对路径比对；前后 `cache_clear`；清空 Token/manifest；socket/HTTP 熔断 |
| Settings | 真实 `Settings(_env_file=None)`；仅 `BIAOSHU_REMOTE_MINERU_TOKEN` alias；禁止手工字段赋值替代 env |
| 分流 | 封锁 `get_engine`/`resolve_engine_name`/managed；remote runner 精确一次 |
| RemoteSource | filename=DB.filename（非 original_name）；path 精确 resolve 且 is_relative_to TEMP；expected_size==no-follow stat；source order |
| 后缀 | 14 允许 + 大小写 + 未知/拒绝；拒绝在 HTTP/runner 前 |
| 共享门 | 11 文件；200MiB 用 Path.stat seam 命中总量 cap；size mismatch；runner=0 |
| 路径 seam | leaf 替换 + parent reparse → fixed error、result None、runner=0 |
| 多文件 | 同 `created_at` 下 id ASC；精确分隔符全文（禁宽 or） |
| caps | client ZIP/full.md cap → 二键；task 防御性 caps → runner=1、result None、固定错误、五域零写；组合门必证 |
| 取消 | API 真取消 → 最终精确 `cancelled`；client interrupted 仅未取消时 failed 二键 |
| 失败形态 | remote 协议/Token/后缀/**client-cap** → 二键；共享门/**task防御性caps**/CAS/finalizer → result None；取消 → cancelled |
| finalizer | patch **`task_service.update_project`**；H1/H3 四故障点含 final commit；commit=False；成功 commit=1；全点隐私扫描 |
| CAS | ver0/ver1 非空字符串且不同；payload `_expectedStateVersion`==ver0；spy `expected_state_version`；result is None |
| 消息表 | 独立冻结 code→中文；不得用 production 自证 |
| 隐私 | 真实 API/DB/task-events(200)/短 SSE/caplog；禁止人造 canary 拼入 blob 冲淡 |
| 自守卫 | AST：skip/xfail/skipif/importorskip、test_* 提前 return、BoolOp Or、assert True、`except Exception: pass` |
| 回归 | lightweight success 节点；managed 未配置失败；managed configured fake-runtime success；remote runner=0 |

### 1.D 摘要：caps / CAS / finalizer / 取消 / result

| 项 | 规则 |
| --- | --- |
| caps | **分层**：client ZIP/`full.md` cap→`RemoteMineruError`→task 二键；task 防御性 `_enforce_markdown_caps`→None |
| CAS | 字符串 stateVersion；创建绑定；冲突固定文案 + result None |
| finalizer | 四写点故障全回滚；success event=0；隐私全表面 |
| 取消 | cancelled vs failed+interrupted 互斥 |
| result | remote 二键=客户端/协议/Token/后缀/**client-cap**；共享门/task防御性caps/CAS/finalizer/取消=None/cancelled |
| data_id | 单一随机源 `uuid.uuid4().hex` |

#### 1.C 验收命令（test-only / Codex 终验，全部串行一次）

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

**退出标准：** py_compile 通过；helper 定向门通过；`git diff --check` 对 tracked 无错误；变更文件集合 **精确等于** 四白名单；Codex 侧 collect-only + 两文件合并 failure-first **可收集**且存在真实 **failed**（业务红）；发送 **一个** `review_request`。
状态保持：**production 未授权**；真实外网 / Token **did-not-run**。

### 阶段 2：Codex 审查测试（本任务后）

1. 排除假绿：常量自指、条件成功、复制 production、真实 sleep、外网、真实 Token。
2. 疑似问题 → `question` → 双方确认 → 授权 test-only 返修。
3. 独立复跑两专项；通过后才可授权 production。

### 阶段 3：production 实现（**另授权**）

建议串行顺序：

1. `config.py` + `.env.example`（Token 字段 only）
2. `remote_mineru_client.py`（纯客户端 + ZIP；单测先绿 client）
3. `task_service.py` 旁路（再绿 task 专项）
4. 回归：`test_v1m_managed_parse_m2.py` 子集、`test_parse_engines.py`、关键 task/CAS
5. `py_compile` 生产文件；`git diff --check`；白名单审查

### 阶段 4：真实烟测（**另授权，默认 did-not-run**）

前置：轮换 Token；仅本机 env 注入；非敏感合成 PDF；接受云端不可撤销风险。
**不得**把真实 Token、预签名 URL、batch_id 写入 Git 或协作信箱。
确认 `file_urls` 与源顺序对应的集成假设是否成立。

## 4. 建议生产 API 面（供测试锚定，非本任务实现）

```text
app.services.remote_mineru_client
  ENGINE_NAME = "remote_mineru"
  API_BASE_URL = "https://mineru.net"
  PATH_FILE_URLS_BATCH = "/api/v4/file-urls/batch"
  PATH_EXTRACT_RESULTS_BATCH = "/api/v4/extract-results/batch"  # + /{batch_id}
  ALLOWED_SOURCE_SUFFIXES = frozenset({14 后缀})
  MAX_ZIP_BYTES / MAX_ZIP_MEMBERS / MAX_ZIP_UNCOMPRESSED_BYTES
  POLL_INTERVAL_SEC = 3
  POLL_BUDGET_SEC = 1800
  REMOTE_SEMAPHORE: BoundedSemaphore(1)

  class RemoteMineruError(Exception)  # .diagnostic_code / .message；str==message==message_for_code(code)
  class RemoteSource  # frozen: path, filename, expected_size
  class RemoteParseOutput  # frozen: markdown, file_count, chars

  def message_for_code(code: str) -> str
  REMOTE_MAX_SINGLE_SOURCE_BYTES = 200_000_000
  MAX_MD_CODEPOINTS / MAX_MD_UTF8_BYTES

  def run_remote_mineru_parse(
        sources,
        *,
        token: str,
        cancel_check,
        transport=None,
        sleep_fn=None,
        clock_fn=None,
        resolve_addresses_fn=None,  # 可注入；禁止测试真实 DNS
  ) -> RemoteParseOutput
```

`task_service._run_parse`：

```text
raw_engine.strip() == "remote_mineru" → 旁路
  共享输入门 → 后缀门 → token 门
  构造 RemoteSource 列表 → run_remote_mineru_parse
  捕获 RemoteMineruError → 二键 failed + message_for_code
  成功 → caps + _parse_finalize_success(engine="remote_mineru")
```

## 5. 未运行项（本任务必须声明）

| 项 | 状态 |
| --- | --- |
| 生产模块实现 | 未运行 / 未授权 |
| 真实 MinerU 外网烟测 | did-not-run |
| Token 轮换操作 | 人工前置，自动化不执行 |
| managed/lightweight 全量回归 | 本任务不跑全量（避免超时）；专项内定点回归 |
| 前端策略接线 | 非本包范围 |
| `file_urls` 顺序集成假设 | 待真实烟测确认 |
