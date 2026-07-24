<!--
模块：V1-Q LAN 停机/备份/恢复发布门实施计划
用途：failure-first 测试先行，再 production 两文件修复，Codex 独立审查与提交。
对接：docs/v1q-lan-stop-backup-restore-gate-contract.md；V1-A/V1-B/V1-L。
二次开发：每批只执行授权白名单；疑似问题先 question 双确认。
-->

# V1-Q LAN 停机、备份与恢复发布门实施计划

> **执行方式：** Grok B 高耗费 test-only；后续 Grok A / 授权方 production-only；Codex 独立审查、验收、提交。
> **状态：** failure-first test-only 进行中；生产冻结未授权。
> **冻结 HEAD：** `ac2855800d31de4218a29f1ecc53c63007b6a3be`
> **工作树：** `C:\Users\Administrator\biaoshu-v1q-lan-stop` / `collab/v1q-lan-stop-gate`
> **双确认：** Q1–Q8 YES（`msg_92e9184d…` / `msg_1c29a1c3…`）；test task=`msg_9849d38c…`

**目标：** 关闭 V1-L LAN 前端与 V1-A 回环缩窄之间的 Stop/Backup/Restore 假成功与 fail-open 缺口，同时保持 `assert_services_stopped` 名称与参数兼容。

## 任务 1：契约与计划冻结（本批）

1. 新增契约与本计划，明确 V1-Q 优先于 V1-A 回环措辞。
2. 钉死六文件 test-only 与两文件 production 白名单、禁止预填 future hash。

## 任务 2：Grok B failure-first（本批）

在四个现有测试文件中新增最窄红门，并替换固定 Stop SHA：

| 门 | 文件 | 期望（生产未改） |
|----|------|------------------|
| R1 | `test_biaoshu_backup.py` | TEMP/stub + WhatIf：RFC1918 owned LAN 前端须被识别；当前识别 0 → 业务红；终止分支计数 0 |
| R2 | 同上 | owned 回环后端 + foreign LAN 前端：全量归属后拒绝、零终止；当前假成功 → 红 |
| R3 | 同上 | 精确提取 `Test-PortListening`：LAN Listen=busy；非 NoConnection 枚举异常=busy 且 TcpClient/New-Object/BeginConnect=0 |
| R4A | 同上 | 本机非回环 IPv4 高位 bind + `ports=(P,), probe=None` → 须 `BackupError` |
| R4B | 同上 | fake socket：仅全候选 ConnectionRefused 为空闲；其余 OS 类错误 → `BackupError` |
| R5 | `test_biaoshu_restore.py` | restore 与 CUTOVER recover：monkeypatch 一次路由到 `backup.assert_services_stopped(ports=(P,), probe=None)`；live 拒绝；调用计数 1；四树哈希不变 |
| R6 | `test_start_biaoshu_dev.py` / `test_trusted_lan_access.py` | 删除固定 Stop SHA；UTF-8 BOM + PS5.1 ParseFile errors=0 |
| R7 | 自守卫 | 仅扫描新增 V1-Q 方法：禁 skip/提前 return/宽 except/真端口/真 PID/DB/DNS |

验证：串行最窄新用例或完整四文件一次；业务 failed + 零 errors；`py_compile`、PS5.1 parse、`git diff --check`/`status`、生产两文件 SHA 与任务开始一致；无 Git 写。

## 任务 3：Codex 测试审查

1. 排除假红、源码扫描冒充、真实副作用、预填哈希、skip 假绿。
2. 通过后提交 test-only；production 仍冻结。

## 任务 4：production-only（未授权）

仅两文件：

1. `Stop-Biaoshu-Dev.ps1`：LAN 监听收集/复查；枚举异常 fail-closed 且零 TcpClient 回退。
2. `biaoshu_backup.py`：`probe=None` 覆盖回环+本机已分配 IPv4；非 ConnectionRefused 错误 fail-closed。

## 任务 5：串行验收与闭环

先 V1-Q 新门全绿，再回归 V1-A/B/K/L 相关最窄专项；禁止真实 Stop/Backup/Restore 批处理、真实 8000/5173、真实 DB/uploads。文档回写真实数字与消息 ID 后关闭包。

## 非目标

防火墙、Start/Vite 改动、IPv6 LAN、公网、自动提交 main、并发全量 pytest。
