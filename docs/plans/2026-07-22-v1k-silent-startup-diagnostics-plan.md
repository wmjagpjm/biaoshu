<!--
模块：V1-K 静默启动诚实诊断实施计划
用途：按 B 测试先行、Codex 审查、A 生产实现和独立串行验收交付本机启动诊断。
对接：V1-K 契约、A/B 独立 worktree、V1-A 停机测试与主协作分支。
二次开发：脚本必须保持默认静默；V1-L 内网绑定和外部安装严格后置。
-->

# V1-K 静默启动诚实诊断实施计划

> **执行代理要求：** 使用 `executing-plans`；测试强度、状态 schema、进程副作用或白名单问题必须先双方确认。

**目标：** 五个启动入口委托唯一真源，端口归属和就绪结果诚实可诊断，默认仍无窗口、浏览器或 pause。

**基线：** `94ff7bb`。冻结后新建 A/B 独立 worktree 和分支；B 只写测试，A 只写七个生产脚本，Codex 独占 Git 与文档闭环。

### 任务 1：冻结与隔离

1. 提交本契约、计划、交接、路线图和联调清单，只推送协作分支。
2. 从冻结提交创建干净 A/B worktree；测试数据库与 TEMP 独立，路由保持后台静默。
3. 核对主仓、本地远端一致，严禁 `main`、真实服务和 PID 12456。

### 任务 2：Grok B failure-first

唯一可写 `tools/v1-ops/test_start_biaoshu_dev.py`。只用 TEMP 假仓、严格快照和 plan/diagnose 模式；生产未改时得到真实业务红，报告首红、passed/failed/did-not-run、TEMP 清理、哈希、diff-check 和空暂存区。禁止生产或 Git 写入。

### 任务 3：Codex 审查测试

逐行排除真实端口/进程/HTTP/DB、源码扫描、空循环、宽断言、快照旁路、状态敏感字段、未验证原子覆盖和五入口漏测。问题先 question，B 只读 YES 后才发 test-only 返修。合格后 Codex 提交测试并转入 A。

### 任务 4：Grok A production-only

唯一可写契约 §6 的七个生产脚本。实现唯一 UTF-8 BOM 真源、五入口薄委托、状态侧车、owned/foreign、固定回环就绪、Hidden 启动与显式只读诊断。不得修改冻结测试、Stop、业务代码、host/CORS/端口或依赖。

### 任务 5：Codex 独立验收

1. 核对严格八文件、测试哈希、PS1 BOM、bat 薄委托和空暂存区。
2. 审查所有 live 与注入分支的 fail-closed、零终止、零敏感、原子状态和稳定退出码。
3. 串行运行新专项、V1-A 代表回归、PowerShell parse/BOM、py_compile 与 diff-check；禁止真实启动、全量和并发。
4. 生产问题继续走 `question → Grok YES → task → review_request`。

### 任务 6：提交、推送与闭环

1. Codex 提交测试和生产，快进主协作分支并推送，不操作 `main`。
2. 修正 README 陈旧启动/后端口径，更新契约/计划/交接/路线图/联调清单，记录真实数字、消息、哈希和未运行项。
3. 下一包只读审计 V1-L 可信内网访问；不得未经安全冻结直接把 host 改为 `0.0.0.0`。
