<!--
模块：V1-L 可信内网访问实施计划
用途：按测试先行、生产受限、Codex 独立审查和真实发布边界交付局域网单入口。
对接：V1-L 契约、V1-K 启动诊断、P10A 认证、A/B 独立 worktree 与主协作分支。
二次开发：每批只执行本计划授权项；任何白名单、监听面或测试真实性问题必须先 question 双确认。
-->

# V1-L 可信内网访问实施计划

> **执行方式：** 使用 `executing-plans`；Grok 承担高耗费测试/实现，Codex 独立审查、验收、提交与推送。
> **状态：** 生产实现、隔离测试、回归、发布说明与推送均已完成；真实 LAN 环境验收尚未执行。
> **基线：** `ca7223a`；初始冻结=`2d7dd55`；测试冻结=`ea01c48`；测试夹具修正=`7c9266e`/`b0f197e`；生产实现=`10b5f3e`。A/B 审计、Q1 九项决策、Q2 七项反假绿修订与 Q3 bootstrap/API base 精确语义均已双确认。
> **自动化验收：** Q8 定点 `5 passed`；V1-L `56 passed / 68 subtests passed`；V1-K `67 passed / 19 subtests passed`；前端 lint/build、`py_compile`、PS1 ParseFile/UTF-8 BOM、diff、白名单、七键/单次 Replace 与 TEMP 清理均通过。
> **保留未验证项：** 真实 LAN 服务、真实防火墙、隔离数据库/uploads、真实管理员登录和第二台内网设备可达性均未运行，不得假绿。

**目标：** 保持默认本机回环的同时，允许运维者显式选择一个 RFC1918 IPv4，只暴露 Vite 5173，并以 required 会话、同源 `/api` 代理和手工最小防火墙规则供 5–6 人可信内网使用。

### 任务 1：冻结与隔离（已完成）

1. 提交 V1-L 契约、计划、交接、路线图和联调清单，只推送协作分支。
2. 将 `biaoshu-v1l-a`、`biaoshu-v1l-b` 快进到冻结提交，保持各自干净分支和独立 TEMP；禁止操作 `main`。
3. 固定消息链、白名单、非目标、零真实端口/防火墙测试边界和 V1-K 回归门。

### 任务 2：Grok B failure-first（已完成）

1. B 只写契约 §9 的两个测试文件；先报告生产未改时的真实业务红。
2. 用 TEMP 假仓、严格 listener/probe/process/auth 快照验证参数、LanHost listener/探针、required 握手、正向启动顺序、幂等、状态隐私与零副作用。
3. Vite 配置必须成功模块加载并结构化读取；LAN 外部 proxy 覆盖必须仍得到精确回环 target，不接受“任意加载失败也通过”、README、正则包含或只看命令字符串。
4. 禁止 skip/xfail、宽泛 `or`、条件跳过、真实 `Start-Process`、live HTTP、防火墙、浏览器、数据库或联网。
5. 完成后只发 `review_request`，报告首红、passed/failed/did-not-run、文件哈希、TEMP 清理和未运行项；不提交。

### 任务 3：Codex 测试审查与冻结（已完成）

1. 逐项排除假红、源码扫描冒充行为、快照旁路、真实副作用、状态第八键、秘密/IP 泄漏和 V1-K 原子门弱化。
2. 疑似问题先发 `question`；B 明确 YES 后才授权 test-only 返修。
3. Codex 独立复跑新 failure-first 与 V1-K 精确回归，通过后提交并推送测试。
4. A worktree 只在测试提交后快进，禁止提前看半成品生产修改。

### 任务 4：Grok A production-only（已完成）

1. A 只写契约 §9 的五个生产/说明文件，禁止改测试。
2. 真源实现 profile/IPv4 严格解析、后端回环、required 子进程、authRequired+bootstrapped 握手、LanHost listener/探针和“后端先证明、前端后暴露”。
3. Vite 实现精确 bind、有限 allowedHosts 与固定回环 proxy；`BIAOSHU_LISTEN_PROFILE`/`BIAOSHU_LAN_HOST` 只作短生命周期子进程桥接并及时恢复，默认 loopback 行为不变。
4. `.env.example` 与 README 说明管理员 bootstrap、同源地址、防火墙 Private/LocalSubnet/5173 和精确回滚；不得包含口令、密钥或真实人员数据。
5. 完成后只发 `review_request`，报告 diff、测试、Parse/BOM、lint/build、风险与未运行项；不提交。

### 任务 5：Codex 独立审查与问题闭环（已完成）

1. 核对严格白名单、默认 loopback、未知参数拒绝、RFC1918 算术、环境变量生命周期和 Start-Process 启动顺序。
2. 确认 LAN 模式在 `authRequired=true` 前不存在 5173 暴露窗口，既有 disabled/foreign 后端均 fail-closed。
3. 确认 proxy/API base/allowedHosts 无通配、CORS/Cookie/frontend src/backend 业务零改动，状态仍七键且单次原子替换。
4. 任何疑似缺陷必须走 `question → 双方确认 YES → 最小 task → review_request`；Codex 不直接替 Grok 修生产代码。

### 任务 6：串行验收（自动化已完成，真实环境未验证）

按风险从小到大串行运行：

```powershell
cd C:\Users\Administrator\biaoshu\tools\v1-ops
..\..\backend\.venv\Scripts\python.exe -m pytest -q test_trusted_lan_access.py
..\..\backend\.venv\Scripts\python.exe -m pytest -q test_start_biaoshu_dev.py
..\..\backend\.venv\Scripts\python.exe -m py_compile test_trusted_lan_access.py test_start_biaoshu_dev.py

cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build

cd C:\Users\Administrator\biaoshu
git diff --check
```

另做两个 PS1 ParseFile/UTF-8 BOM、生产/测试白名单、Stop 哈希、V1-K 状态七键/单 Replace 与空暂存区门。禁止并发 pytest、重复全量、真实防火墙、默认数据库/uploads 和联网安装。

真实烟测只在静态/隔离门全绿后执行：使用明确私有 IPv4、隔离数据库/uploads、临时账号，Hidden 串行启动；验证本机经 LAN URL 登录、Cookie/CSRF、GET/POST、SSE、上传与下载代表链。第二台设备验证需要真实内网客户端；缺少时必须列为未验证，不得假绿。

### 任务 7：提交、推送与闭环（已完成）

1. Codex 按“测试 → 生产 → 文档闭环”分层中文提交并推送协作分支，严禁 force push 或操作 `main`。
2. 更新契约、计划、README、交接、路线图、联调清单，记录真实测试数字、消息 ID、提交与未运行项。
3. 只有生产实现、独立验收和发布说明全部通过后，才把 V1-L 标为已完成并重新核算 V1 完成度；文档冻结和审计不得计作功能完成。
4. 下一包从 V1 剩余真实阻断中重新只读审计；OCR/真实解析器部署、最终版式、在线热备和 V2/V3 继续独立分包。
