<!--
模块：V1-K 静默启动诚实诊断契约
用途：统一本机启动入口、端口归属、就绪探测、有限状态侧车与显式只读诊断。
对接：根启动入口、backend/frontend run-dev、V1-A 受控停机、health 与 V1 本机首日主链。
二次开发：默认启动必须静默；可信内网绑定属于 V1-L，禁止在本包放宽监听面。
-->

# V1-K 静默启动诚实诊断契约

> **状态：已完成并通过 Codex 独立验收。**
> **冻结：** `8f0c137`；测试=`8f0c366`/`1fdea4c`/`997b57c`；实现=`cd4102f`。仅 `collab/grok-code-codex-review`，严禁操作 `main`。

## 1. 问题真值与分包

当前默认入口保持后台静默，但存在三类首日阻断：缺 `.venv`/npm 只返回不可见退出码；任意进程监听 8000/5173 即被误判成功；Hidden 子进程派生成功后不验证 API/页面就绪，早退没有稳定状态。历史根 `Start-Biaoshu-Dev.ps1` 还会弹前台服务窗口、自动开浏览器并 `Read-Host`，与用户已冻结偏好分叉。

A/B 独立审计：A task/review=`msg_1b01ad1d4e6c48e48676e14b64e474dc`/`msg_aceca0b60d704a929008146fcebb0bbf`，B task/review=`msg_ab643054a2ed40c3827c828e886d109e`/`msg_097ce4b0034741db95913e2310275ad8`。设计确认：A question/YES=`msg_b82ef70a60ae49caa62cc637dc0dc8dc`/`msg_1720a1f4bbcc4d7584d897e1a673110c`，B question/YES=`msg_03cd593dc74d4228a6288157b6570072`/`msg_9c903841ef704be7b9a6e7235dfa7be9`。

backend 与 Vite 均固定 `127.0.0.1`，同一内网其它电脑当前不可直接访问。双方确认缺口真实，但放宽监听涉及网卡、allowed hosts、同源会话、CORS、防火墙和安全发布，冻结为后续 **V1-L 可信内网访问**独立包；V1-K 继续只服务本机 loopback。

## 2. 统一入口与模式

新增 `tools/v1-ops/Start-Biaoshu-Dev.ps1` 作为唯一逻辑真源，必须带 UTF-8 BOM。以下五个既有入口只能做薄委托，禁止保留第二套端口、进程或就绪算法：

1. 根 `Start-Biaoshu-Dev.bat`：选择 `all`；
2. 根 `Start-Biaoshu-Dev.ps1`：选择 `all`，移除前台服务窗口、自动开浏览器和 pause；
3. 根 `Start-Biaoshu-UI.bat`：选择 `frontend`；
4. `backend/run-dev.bat`：选择 `backend`；
5. `frontend/run-dev.bat`：选择 `frontend`。

新增根 `Diagnose-Biaoshu-Dev.bat`，仅委托真源 `-DiagnoseOnly`。启动模式可派生缺失服务；诊断模式只读；`-PlanOnly` 只计算计划。任意默认启动路径均必须 Hidden、无浏览器、无 `Read-Host`/pause、无 `cmd /k`、不抢焦点。

## 3. 前置、归属与就绪

### 3.1 前置检查

选择 `all` 时必须先完成两端全部前置检查，再启动任一进程：后端固定要求 `backend/.venv/Scripts/python.exe` 与 `backend/app/main.py`；前端固定要求 npm、`frontend/package.json` 和 `frontend/node_modules`。本包不安装、下载或修复依赖。

### 3.2 端口归属

8000/5173 的 listener 必须按 V1-A Stop 的保守语义判定：后端只接受本仓 `.venv` 下 Python 且命令含 uvicorn 与本仓 backend；前端只接受本仓 frontend 下 node/npm/vite。无法枚举、PID/路径/命令不可信、多监听混合或 foreign 监听均失败；不得因 `LISTENING` 直接报成功。V1-K 不修改、导入或调用 `Stop-Biaoshu-Dev.ps1`，不得终止任何进程。

### 3.3 就绪探测

- 后端：固定回环 `GET http://127.0.0.1:8000/api/health`，只有 HTTP 200、JSON `status="ok"` 且 `dbOk=true` 才 ready；
- 前端：固定回环 `GET http://127.0.0.1:5173/create`，只有有限成功 HTTP 状态才 ready；
- 已归属且 ready 为幂等成功；已归属但未 ready 固定失败，不重复启动；
- 无 listener 时才允许 Hidden 派生对应进程并做有界轮询；超时或早退固定失败；
- `all` 中一端启动后另一端未就绪，不自动停止已启动进程，状态必须诚实记录部分结果，整体失败。

启动命令继续使用当前 loopback、端口和开发命令；不得改成 `0.0.0.0`、安装服务、开防火墙或自动开页面。

## 4. 状态侧车与显式诊断

每次 start/diagnose/plan 必须原子覆盖 Git 已忽略的 `tmp/dev-start-status.json`。写临时文件后同目录替换，禁止半 JSON。固定顶层七键：

1. `schemaVersion`：精确整数 `1`；
2. `updatedAtUtc`：严格 UTC `Z` 时间；
3. `mode`：`start|diagnose|plan`；
4. `component`：`all|backend|frontend`；
5. `overall`：`ready|already_running|failed|plan`；
6. `code`：契约固定枚举；
7. `services`：精确 `backend/frontend` 两键，各自精确 `{state,code}`。

服务 `state` 只允许 `not_selected|planned|missing|foreign|not_ready|ready|already_running`。顶层与服务 code 必须来自固定枚举，至少覆盖 `ready`、`already_running`、`venv_missing`、`backend_entry_missing`、`npm_missing`、`frontend_package_missing`、`frontend_deps_missing`、`listener_unavailable`、`backend_port_foreign`、`frontend_port_foreign`、`backend_not_ready`、`frontend_not_ready`、`snapshot_invalid`、`status_write_failed`。

状态与诊断禁止包含 PID、绝对路径、用户名、command line、argv、异常原文、stdout/stderr、Key、Cookie、CSRF、数据库路径/内容、uploads、标书正文或第三方输出。不得新增原始日志。状态写入失败时不得启动进程；显式诊断只显示固定中文映射与稳定退出码，不回显路径或异常，不启动/停止进程、不打开浏览器、不 pause。

## 5. 测试注入与零副作用

测试只允许系统 TEMP 假仓。真源可接受严格 listener/probe/process 快照，但快照参数只能与 `-PlanOnly` 或 `-DiagnoseOnly` 同时使用；生产 start 模式投稿快照必须固定失败。快照必须精确 schema、拒绝额外键、重复/非法端口、非整数 PID、相对/异常路径、换行与超限命令、非法布尔/状态。

测试必须证明：零真实 `Start-Process`、零 Stop/taskkill、零端口 bind、零 live HTTP、零真实 DB/uploads、零浏览器、零联网；TEMP 状态文件清理后根不存在。禁止用 README/脚本文本扫描代替行为测试，禁止 skip/xfail、宽泛 `or`、固定 sleep 或条件假绿。

## 6. 严格文件白名单

测试阶段唯一可写：

1. `tools/v1-ops/test_start_biaoshu_dev.py`。

生产阶段唯一可写：

2. `tools/v1-ops/Start-Biaoshu-Dev.ps1`；
3. `Diagnose-Biaoshu-Dev.bat`；
4. `Start-Biaoshu-Dev.bat`；
5. `Start-Biaoshu-Dev.ps1`；
6. `Start-Biaoshu-UI.bat`；
7. `backend/run-dev.bat`；
8. `frontend/run-dev.bat`。

冻结/闭环文档：本契约、实施计划、`README.md`、`HANDOFF-next.md`、路线图与联调清单。README 只在实现验收后修正“后端占位/mock/localStorage/只教 npm install”等陈旧口径，不进入 failure-first 行为断言。

禁止修改 `tools/v1-ops/Stop-Biaoshu-Dev.ps1`、Stop/备份/恢复、backend app、frontend src/vite config、`.env`、依赖、数据库、端口、host/CORS/Cookie、V1-J 或其它测试。

## 7. Failure-first 与验收

生产未改时，新专项必须因真源/诊断入口不存在、旧入口未委托和行为缺失而业务红；收集、编码、fixture、环境或 PowerShell 不可用不算红。至少覆盖：五入口委托；PS1 BOM；全部前置缺失；owned/foreign/mixed listener；已就绪/未就绪/启动计划；all 前置失败零部分启动；状态七键/枚举/原子覆盖/固定中文/退出码；快照拒绝；敏感字段零出口；diagnose/plan 零副作用。

最终严格串行：

```powershell
cd C:\Users\Administrator\biaoshu-v1k-test\tools\v1-ops
..\..\backend\.venv\Scripts\python.exe -m pytest -q test_start_biaoshu_dev.py
..\..\backend\.venv\Scripts\python.exe -m pytest -q test_biaoshu_backup.py
..\..\backend\.venv\Scripts\python.exe -m py_compile test_start_biaoshu_dev.py
git -C ..\.. diff --check
```

V1-A 回归只运行停机/启动入口相关代表用例，若测试文件不支持稳定筛选则由冻结计划列精确命令；禁止机械运行恢复大文件、后端全量、前端 E2E、真实服务/端口/health、真实数据或联网安装。

## 8. 非目标

- 不实现可信内网监听；V1-L 另包冻结 bind host、allowed hosts、同源会话、CORS、防火墙与发布说明；
- 不自动创建 venv、pip/npm install、OCR/MinerU/Docling、模型或 Key；
- 不做原始运行日志、服务管理器、Windows 服务、托盘、自动浏览器、生产构建或 Docker；
- 不修改业务主链、停机、备份恢复、Word 版式或 V2/V3 协作。

## 9. 完成与验收记录

Grok B 的最终 failure-first 在生产未实现时为 **44 failed / 14 passed / 19 subtests passed**，Codex 独立复跑一致；测试提交后由 Grok A 完成严格七生产脚本。Codex 首轮生产专项为 **58 passed / 19 subtests passed**，V1-A 备份代表回归为 **65 passed**。

Codex 随后发现终稿存在时先 `Remove-Item` 再 `Move-Item` 会产生缺失窗口。Q4 双确认=`msg_5483fd64109140a7af9ae3e20878766b`/`msg_3fa7662c10e04a64b270c26dd9ae93e4`；Q5 又关闭无关 `File.Replace` 参数可冒充主证据的反假绿，确认=`msg_2e358f4d40714674aea11ce31ae22d32`/`msg_684d8970035942a79cf9182ad8e357a2`。Q6 确认 Windows PowerShell 5.1 不应以“先 `$null` 失败、再重试”作为正常路径，确认=`msg_6f08df9955854233ac81597279f7d000`/`msg_0fdd4d0545904abfb407e765a3b2e04d`。

最终实现只有一处 `[System.IO.File]::Replace($tempPath, $StatusFinal, [NullString]::Value)`，无终稿时才 `Move-Item`，不删除旧终稿。Codex 最终严格串行 V1-K/V1-A 回归为 **67 passed / 19 subtests passed**、**65 passed**；`py_compile`、两个 PS1 ParseFile/UTF-8 BOM、diff-check、严格七生产文件、冻结 Stop 哈希和空暂存区均通过。未运行真实启动/停止、真实端口/HTTP、真实数据库/uploads、后端全量、前端 E2E 或联网安装。
