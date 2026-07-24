<!--
模块：V1-Q LAN 停机/备份/恢复发布门契约
用途：冻结 Stop/Backup/Restore 对显式 LAN 监听与本机已分配 IPv4 的 fail-closed 安全面，覆盖 V1-A 回环缩窄措辞。
对接：V1-A 受控停机与离线备份、V1-B 离线恢复、V1-L 可信内网、V1-K 启动诊断。
二次开发：test-only 六文件与 production 两文件严格分离；probe 仅测试注入；禁止真实 8000/5173、真实 PID、真实 DB/uploads。
-->

# V1-Q LAN 停机、备份与恢复发布门契约

> **状态：failure-first test-only（生产未授权）**
> **日期：2026-07-24**
> **工作树：** `C:\Users\Administrator\biaoshu-v1q-lan-stop`
> **分支：** `collab/v1q-lan-stop-gate`
> **冻结 HEAD：** `ac2855800d31de4218a29f1ecc53c63007b6a3be`
> **放行：** question `msg_92e9184d18804c859f4f9b4c3029f5c3`；status `msg_1c29a1c37a214dde9effaa5a2a3cf2f9`（Q1–Q8 全部 YES）
> **task：** `msg_9849d38ce5c04251bac60ab38d0ac857`

## 1. 问题真值与优先级

V1-L 允许前端绑定显式 RFC1918 `LanHost:5173`，后端仍回环 `127.0.0.1:8000`。V1-A 契约与实现将 Stop 初次/复查枚举与 `assert_services_stopped` 安全面缩窄到回环与通配地址集合，导致：

1. LAN-only 5173 可被 Stop 视为“无监听、已停止”（假成功）；
2. 回环后端 + LAN 前端并存时可能只验证/终止后端（部分停机）；
3. `Test-PortListening` 过滤 LAN，且枚举异常回退 `BeginConnect(127.0.0.1)` 可假报释放；
4. `assert_services_stopped` 默认只探 `127.0.0.1`，`_default_port_probe` 将任意 `OSError` 当空闲（fail-open）。

**V1-Q 优先于 V1-A 的回环限定措辞。** 凡 V1-A 写“仅检查 `127.0.0.1:8000/5173`”之处，在 Stop 监听收集/复查与 `probe=None` 默认路径上，一律以本契约为准：必须覆盖回环与本机已分配 IPv4，任一 live 或不确定错误 fail-closed。

## 2. 公开 API 兼容（不可破坏）

冻结名称与签名（参数默认值字面兼容）：

```python
assert_services_stopped(
    host="127.0.0.1",
    ports=(8000, 5173),
    probe=None,
) -> None
```

1. 名称、参数顺序与默认值保持不变；既有显式 `probe=` 注入测试继续兼容。
2. 注入 `probe(host, port)` 时：仍按传入 `host` 与 `ports` 调用，**不得**因 V1-Q 改变注入契约。
3. **`probe=None` 时：`host` 不再缩窄安全检查面。** 实现必须对每个目标端口覆盖：
   - 回环（至少 `127.0.0.1`）；
   - 本机当前已分配的非回环 IPv4（不含 DNS/外部探测构造）；
   - 任一候选 live 或不确定错误（timeout / permission / unreachable / 地址枚举异常等，**除明确 ConnectionRefused**）→ `BackupError` fail-closed。
4. `biaoshu_restore` 直接复用 `biaoshu_backup.assert_services_stopped`；修复 backup 默认路径即可覆盖 backup / restore / recover，无需为该缺口改 restore 公开 API。

## 3. Stop 行为门

### 3.1 监听收集

1. 目标端口仍为 `8000` 与 `5173`。
2. 不得仅以 `{127.0.0.1, 0.0.0.0, ::, ::1}` 过滤而丢弃显式 RFC1918 LAN 监听。
3. 显式 RFC1918 上的本仓 owned 前端必须进入归属集合；WhatIf 须识别为将终止对象。
4. 先全量归属验证，再终止；任一 foreign / 无法确认 → 整次失败、零终止。
5. 回环 owned 后端 + foreign LAN 前端并存时必须拒绝，禁止只停后端。

### 3.2 复查 `Test-PortListening`

1. 必须观察目标端口的 **全部 Listen**（含显式 LAN IPv4），不得仅回环/通配集合。
2. “明确无监听对象”（No MSFT_NetTCPConnection 等既有语义）→ 未占用。
3. **非**“明确无监听对象”的枚举异常 → 直接按仍占用（busy）fail-closed。
4. 禁止在枚举异常路径上 `New-Object TcpClient` / `BeginConnect` 真实回环探测。

### 3.3 WhatIf 与副作用

1. 专项测试仅允许独立 PowerShell 子进程 + WhatIf + stub `Get-NetTCPConnection`/`Get-CimInstance`。
2. 固定不存在的假 PID；`taskkill` / `Stop-Process` / `Process.Start` 终止分支调用精确 0。
3. 禁止真实 taskkill、真实本机 PID、真实 8000/5173 占用操作。

## 4. Backup / Restore 端口门

1. Backup：`create_offline_backup` 与 CLI 默认路径继续调用 `assert_services_stopped`。
2. Restore / recover（含 CUTOVER 等需服务已停的 phase）：继续调用同一函数。
3. 隔离红门允许：仅绑定**高位随机端口**到**当前明确本机已分配非回环 IPv4**；`ports=(P,)` + `probe=None` 必须拒绝。
4. fake socket 门：除所有候选地址均明确 `ConnectionRefused` 外，timeout / permission / unreachable / 地址枚举异常均必须 `BackupError`。
5. 禁止 DNS、外部地址、真实 8000/5173、固定真实 PID、真实服务、主仓 db/uploads。

## 5. 测试与生产白名单

### test-only（本包 Grok B）

1. `tools/v1-ops/test_biaoshu_backup.py`
2. `tools/v1-ops/test_biaoshu_restore.py`
3. `tools/v1-ops/test_start_biaoshu_dev.py`（仅删固定 Stop SHA，改为 BOM/PS5.1 门）
4. `tools/v1-ops/test_trusted_lan_access.py`（同上）
5. `docs/v1q-lan-stop-backup-restore-gate-contract.md`（本文件）
6. `docs/plans/2026-07-24-v1q-lan-stop-backup-restore-gate-plan.md`

### production-only（后续另任务；本包冻结）

1. `tools/v1-ops/Stop-Biaoshu-Dev.ps1`（UTF-8 BOM）
2. `tools/v1-ops/biaoshu_backup.py`

禁止 test-only 阶段修改 production；禁止预填 future production hash。

### Stop 完整性门（替代固定 SHA）

`test_start_biaoshu_dev.py` 与 `test_trusted_lan_access.py` 必须：

1. 完整删除固定 Stop SHA 常量、相等断言与打印；
2. 断言 UTF-8 BOM（`EF BB BF`）；
3. 真实 `powershell.exe` 5.1 `Parser.ParseFile` errors 精确 0；
4. 行为由 V1-Q Stop 红门证明，不得锁死未来哈希。

## 6. failure-first 与自守卫

1. 生产未改时：新 V1-Q 用例必须业务 failed；零收集/夹具/teardown error；禁止 skip/xfail。
2. 自守卫仅约束**新增 V1-Q 测试方法**：禁止 skip、条件提前 return、宽异常吞掉、真实 8000/5173、真实 PID/数据库/上传目录、DNS 与外部地址。
3. 唯一允许的本机网络副作用：当前已分配非回环 IPv4 上的隔离高位 bind/connect；无候选地址必须硬失败（`AssertionError`），禁止 skip。
4. 不得运行真实 Stop/Backup/Restore 生产入口批处理；不得 Git add/commit/push/stash/reset/checkout。

## 7. 未交付边界

本包不交付：防火墙规则、LAN 启动改动、恢复 UI、跨版本迁移、自动枚举全部网卡写回仓库、IPv6 LAN、公网暴露、生产实现本身（另立 production-only 任务）。
