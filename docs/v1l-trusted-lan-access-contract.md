<!--
模块：V1-L 可信内网访问契约
用途：冻结单机作为可信局域网主机时的监听、鉴权、同源代理、Host、防火墙与回滚边界。
对接：V1-K 启动诊断真源、Vite /api 代理、P10A 会话/RBAC、V1 本机/内网发布主线。
二次开发：默认必须保持回环；禁止把后端或无鉴权前端直接暴露到局域网，更禁止借本包扩成公网部署。
-->

# V1-L 可信内网访问契约

> **状态：生产实现、隔离测试、回归与发布说明已完成并推送。**
> **基线：** `ca7223a`；初始冻结=`2d7dd55`；测试冻结=`ea01c48`；测试夹具修正=`7c9266e`/`b0f197e`；生产实现=`10b5f3e`。仅允许 `collab/grok-code-codex-review`，严禁操作 `main`。
> **审计：** A task/review=`msg_dd6c70f130fa4d0e96f3a495fde42f0f`/`msg_092d74fd43f2402bb6f8a783d65274f3`；B task/review=`msg_d4469f174c324471a76a5269f7697caa`/`msg_7d4576573bd049e68d828ed1e3ea5c74`。
> **双确认：** A question/YES=`msg_9c5880199caa482f88461364fceb1918`/`msg_33d84617361f434e93d6913c2968e15c`；B question/YES=`msg_e424521a7e674153958e331084461021`/`msg_0a67657dd9df443ba1e9eeddfb95409a`。
> **Q2 反假绿范围修订：** B failure-first/review=`msg_8f1542dacb14405292ca4064d9614af0`/`msg_fe023d1222a7465cb9670f07c94054aa`；Codex question/B YES=`msg_100daab91dec454e8a6a6238407f1d71`/`msg_4745e5c01b2046a6bf74d0ec9fa0ec78`。
> **Q3 精确语义：** Codex question/B YES=`msg_c3a68603ba344b46aaa30a0eb52f4969`/`msg_0af2ec045e904cd4b2272a4aeef3d3ca`。
> **最终自动化验收：** V1-L `56 passed / 68 subtests passed`，V1-K `67 passed / 19 subtests passed`，Q8 定点 `5 passed`；前端 lint/build、`py_compile`、两个 PS1 ParseFile/UTF-8 BOM、`git diff --check`、严格白名单、状态七键/单次 Replace 与 TEMP 清理均通过。
> **未验证：** 未启动真实 LAN 服务，未创建或验证真实 Windows 防火墙规则，未使用隔离数据库/uploads 与真实管理员登录，也未从第二台内网设备验证可达性；这些部署验收不得由自动化测试冒充。

## 1. 问题真值

当前 Vite 与 uvicorn 均固定监听 `127.0.0.1`，同一内网其它电脑无法打开系统。`AUTH_MODE` 默认 `disabled`；若只把任一入口改成 `0.0.0.0`，会把无会话保护的项目、知识库、设置、导出和本地解析面暴露给整个可达网段。

A/B 与 Codex 已独立确认缺口真实，且一致否决“前后端双端口跨源”和“直接改 `0.0.0.0`”。V1-L 只交付可信内网最小入口，不改变个人本机默认行为。

## 2. 唯一拓扑

```text
内网浏览器 http://<显式私有 IPv4>:5173
    |
    | 同源页面、Cookie、CSRF、上传、下载与 SSE；业务 URL 仅 /api
    v
Vite：仅绑定用户显式给出的 RFC1918 IPv4:5173
    |
    | /api 代理，changeOrigin=true
    v
FastAPI：始终 127.0.0.1:8000
```

1. 浏览器只能访问 Vite 单入口，禁止直连 `:8000`。
2. 后端、后端 health、OpenAPI、`/docs`、`/redoc` 与公开解析回调继续只在本机回环可达。
3. 前端业务请求必须保持相对 `/api`；`VITE_API_BASE_URL` 未设置时允许前端既有 `/api` 回退，显式存在但为空、纯空白或非精确 `/api` 时启动失败。
4. Vite proxy target 必须精确回环 `http://127.0.0.1:8000`，禁止环境变量把代理指向外部主机。
5. 同源拓扑不扩 `CORS_ORIGINS`，不改 Cookie `HttpOnly`、`SameSite=Strict`、`Path=/api`，不新增浏览器跨源兼容分支。

## 3. 显式 opt-in 与输入校验

默认继续等价于：

```powershell
.\Start-Biaoshu-Dev.ps1
```

内网模式只能显式提供：

```powershell
.\Start-Biaoshu-Dev.ps1 -ListenProfile lan -LanHost 192.168.1.20
```

参数契约：

1. `ListenProfile` 只允许 `loopback|lan`，默认 `loopback`；大小写不敏感，去首尾空白后判定。
2. `lan` 必须同时提供一个字面 RFC1918 IPv4：`10/8`、`172.16/12` 或 `192.168/16`。
3. 拒绝空值、主机名、IPv6、IPv4-mapped IPv6、`0.0.0.0`、`127/8`、链路本地、组播、CGNAT、公网地址、端口、路径、URL、前后空白残留和额外未知参数；没有网卡前缀信息时不得声称能推断子网广播地址。
4. `loopback` 携带 `LanHost`、`lan` 缺 `LanHost`、重复参数或冲突 profile 均固定失败，不得启动任一新进程。
5. 不自动枚举并信任全部网卡，不把动态探测地址写回仓库或 `.env`。

固定新增失败 code 精确包括：`listen_profile_invalid`、`lan_host_required`、`lan_host_invalid`、`lan_backend_auth_unverified`、`lan_admin_not_bootstrapped`、`lan_api_base_invalid`。`lan_auth_required` 没有区别于 `lan_backend_auth_unverified` 的独立可达语义，禁止保留死 code。中文诊断必须固定、有限、无原始异常。

错误优先级冻结为：未知、重复或冲突 profile/参数=`listen_profile_invalid`；LAN 缺 host=`lan_host_required`；host 非法=`lan_host_invalid`；8000 foreign/mixed=`backend_port_foreign`；frontend-only 没有可证明 required 的 owned 后端=`lan_backend_auth_unverified`；required 已证明但管理员未 bootstrap=`lan_admin_not_bootstrapped`；LAN API base 显式空白或非精确 `/api`=`lan_api_base_invalid`。测试不得接受多选 code 集合。

## 4. 鉴权与既有进程门

1. `lan` 模式必须让新启动后端进程以 `AUTH_MODE=required` 运行；`disabled` 与 LAN 前端监听不得同时生效。
2. 启动前必须通过回环 `GET /api/auth/bootstrap-status` 证明运行后端返回精确布尔 `authRequired=true` 且 `bootstrapped=true`。只有 health 成功不足以证明鉴权和可登录性。
3. 8000 已有本仓 owned 后端时，只有 health ready、`authRequired=true` 且 `bootstrapped=true` 才允许继续启动或复用 LAN 前端；响应缺失、非法、超时或 authRequired=false 固定 `lan_backend_auth_unverified`，仅 bootstrapped=false 固定 `lan_admin_not_bootstrapped`。
4. 8000 为 foreign/mixed listener 时沿用 V1-K 失败语义，不启动 LAN 前端。
5. 新启动后端在 required 握手未成功前，禁止启动 LAN 前端，避免短暂暴露无证明入口。
6. V1-L 不自动创建管理员、不接收或保存口令。发布说明必须先引导运维者用既有本机脚本完成管理员 bootstrap，再开放 5173。
7. 登录后继续依赖 P10A Cookie、CSRF、workspace 成员与 RBAC；不得新增共享口令、URL token 或 localStorage token。

正向启动事件顺序必须可验证且精确为：后端 owned 或 Hidden 启动完成 → 回环 health ready → 回环 `GET /api/auth/bootstrap-status` 返回 HTTP 200、精确两键且 `authRequired`/`bootstrapped` 均为真 → 唯一一次 LAN 前端 Hidden 启动。禁止把“始终拒绝 LAN 前端”当安全成功，也禁止在鉴权和 bootstrap 证明前短暂监听 5173。若本轮刚启动的 required 后端尚未 bootstrap，允许它保持回环运行，运维者本机执行既有 bootstrap 后重试 LAN 启动。

## 5. Vite Host 与绑定边界

1. 默认 profile 继续 `host=127.0.0.1`、`port=5173`、`strictPort=true`。
2. LAN profile 的实际 bind 必须精确等于 `LanHost`，禁止 `0.0.0.0` 与 `::`。
3. `server.allowedHosts` 必须是有限精确值，只允许所选 IPv4、`127.0.0.1`、`localhost`；如未来支持主机名，必须另行显式参数与测试，当前不实现。
4. 禁止 `allowedHosts=true`、点前缀通配、任意域名、自动枚举全部网卡或信任环境中的不受控 Host。
5. Vite 仍只代理 `/api`；不得代理 `/docs`、`/redoc`、`/openapi.json` 或任意根路径到后端。
6. 后端保持回环且 proxy `changeOrigin=true`，本包不新增 `TrustedHostMiddleware`；未来若后端监听面改变，必须另包重审。
7. LAN listener 枚举必须对 5173 使用精确 `LanHost`，后端 8000 仍使用 `127.0.0.1`；不能继续只枚举回环，也不能把其它接口 listener 当 owned。
8. LAN 前端就绪探针必须精确 `http://<LanHost>:5173/create`。已 owned+ready 时必须 `already_running` 且零重复启动；owned 但未 ready 继续固定失败。

## 6. V1-K 兼容与诊断

1. `tmp/dev-start-status.json` 继续固定七个顶层键、两个服务子对象与同目录单次原子替换；不得增加第八键，也不得塞入 IP、URL、PID、路径、argv 或异常原文。
2. 允许为 LAN 失败扩充有限 code 枚举，但不得改变既有 code、state、退出码与 loopback 行为。
3. 后端探针始终走 `127.0.0.1:8000`；LAN 前端探针可从显式 `LanHost` 确定，但诊断输出不得回显完整 URL 或接口信息。
4. `-PlanOnly`/`-DiagnoseOnly` 必须零 `Start-Process`、零真实端口 bind、零防火墙、零浏览器、零停止；测试注入只允许这两种模式。
5. 默认五入口、Hidden、无浏览器、无 pause、V1-A Stop 归属与状态原子性全部保持。
6. `BIAOSHU_LISTEN_PROFILE` 与 `BIAOSHU_LAN_HOST` 是启动脚本向 Vite 子进程传递 profile/host 的唯一环境桥接；只在派生子进程前短暂设置，调用后恢复原值，不写真实 `.env`，也不得继承为后端业务配置。
7. `AuthSnapshotJson` 只允许 `-PlanOnly`/`-DiagnoseOnly`，start 模式携带时与其它快照一样 `snapshot_invalid`。快照必须是精确 `{port,httpStatus,authRequired,bootstrapped}` 对象：port 精确 8000、HTTP 状态为严格整数、两个布尔字段为严格布尔，拒绝额外/缺失键与非对象。

## 7. Windows 防火墙与发布说明

生产代码、启动脚本和测试禁止调用 `New-NetFirewallRule`、`Set-NetFirewallRule`、`netsh advfirewall` 或其它系统策略写入。README/运维说明只给管理员手工步骤：

1. 规则只允许 TCP 5173；不得开放 8000。
2. Profile 只允许 `Private`，RemoteAddress 只允许 `LocalSubnet`。
3. 规则名必须固定且文档给出精确查询、创建、删除命令；创建前先确认当前网络配置文件为 Private。
4. 回滚顺序为停止服务、删除固定规则、恢复 loopback 启动；不得删除非本产品规则。
5. 防火墙是否生效必须由运维者在另一台可信内网设备验证，代码测试不得声称已真实改防火墙。

## 8. 并发与非目标

V1-L 只承诺固定 5–6 人、可信内网、低并发编辑。SQLite 锁等待仍是已知边界；WAL、在线热备、PostgreSQL、数据根迁移另包处理。

本包不做 IPv6、HTTPS/证书、公网 SaaS、反向代理产品化、Docker/K8s、OAuth/OIDC/LDAP/MFA、协同光标、评论审批、强制锁、自动防火墙、自动安装依赖或真实网卡选择 UI。

## 9. 严格文件白名单

Failure-first 阶段唯一可写：

1. `tools/v1-ops/test_trusted_lan_access.py`（新）；
2. `tools/v1-ops/test_start_biaoshu_dev.py`（仅兼容新增参数/code 所必需的最小调整）。

生产阶段唯一可写：

3. `tools/v1-ops/Start-Biaoshu-Dev.ps1`；
4. `frontend/vite.config.ts`；
5. `backend/.env.example`（只补 AUTH_MODE/内网说明，不写口令）；
6. `README.md`；
7. `backend/README.md`（删除内网场景直连 8000 的误导，只保留本机开发说明）。

闭环文档：本契约、实施计划、`HANDOFF-next.md`、路线图、联调清单。根薄委托和 Stop 默认不得修改；如测试证明根委托未透传现有 `$args/%*` 才能先 question 双确认，不得自行扩围。

禁止修改 backend 业务代码、认证/Cookie/CORS、frontend `src`、数据库、依赖锁、端口、备份恢复、真实 `.env`、上传或标书数据。

## 10. Failure-first 与验收门

生产未改时，新专项必须因 LAN 参数、私有 IPv4 校验、required 握手、Vite 动态绑定与 Host 白名单缺失而业务红；收集、编码、依赖、PowerShell 或环境失败不算红。

至少覆盖：

1. loopback 默认参数和 V1-K 七键/原子/Hidden 全部不回归；
2. profile/host 缺失、重复、未知、通配、IPv6、非 RFC1918 与 URL 注入全部 fail-closed；
3. LAN 后端参数始终回环，前端 bind 精确私有 IPv4；
4. 新后端继承 required；正向测试必须证明 health → 精确 auth URL/方法/两键均为真 → frontend start 的事件顺序，握手 false/缺键/额外键/非布尔/错误 port/失败/超时均不启动前端；bootstrapped=false 必须精确 `lan_admin_not_bootstrapped`；
5. owned required 后端可复用，foreign/mixed 继续失败；
6. API base 非相对 `/api`、proxy target 非回环与 allowedHosts 过宽全部拒绝；LAN 配置必须成功结构化加载并忽略外部 `VITE_API_PROXY_TARGET`，loopback/E2E 继续允许既有显式 8010 覆盖；
7. Vite 配置以实际模块加载结果验证，不得仅用 README 或字符串包含假证明；
8. LAN 5173 listener 必须按精确 LanHost 枚举并用 LanHost URL 探测；owned+ready 幂等零启动，错误地址、foreign 或 not-ready 不得冒充；
9. 状态仍精确七键、有限 code、无 IP/URL/环境/秘密，写入仍单次原子替换；
10. 测试零真实服务、端口、HTTP、数据库/uploads、防火墙、浏览器与联网；TEMP 根清理；
11. 反假绿自检必须拒绝测试方法中的多 code 断言、条件成功 `return` 和捕获任意加载异常即通过；
12. PowerShell 5.1 ParseFile/UTF-8 BOM、TypeScript build/lint、`py_compile`、`git diff --check` 和严格白名单通过。

最终真实烟测必须另用隔离数据库/uploads、临时账号与明确私有 IPv4，串行且 Hidden；不得接触用户真实标书或密钥。若没有第二台内网设备，必须诚实记录“远端设备可达性未验证”，不得用本机请求冒充远端验收。
