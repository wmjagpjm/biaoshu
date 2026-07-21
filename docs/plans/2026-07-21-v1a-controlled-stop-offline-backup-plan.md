# V1-A 受控停机与离线备份基础实施计划

> 状态：已完成、独立验收并提交；实现=`5b4ad39`
> 契约：`docs/v1a-controlled-stop-offline-backup-contract.md`
> 实施基线：`42aaa40`（V1-A 并行接口契约）

## 1. 并行实施顺序

1. Codex 先提交本契约与计划，再从该 V1-A 冻结提交为 Grok A/B 创建全新独立 worktree；不得沿用 P13-I4 脏 worktree。
2. Grok B 只新增 Python 专项测试，在无生产文件的基线上运行真实 failure-first；测试夹具只使用临时假库/文件，禁止读取主仓真实数据。
3. Grok A 只实现两个根 bat、两个 UTF-8 BOM PowerShell 脚本和一个 Python 标准库核心；不得查看或修改 B 的测试文件，不得运行真实备份。
4. 两边各自发送 review_request/result。Codex 独立审查源/目标路径、进程归属、全验证后终止、敏感数据、临时目录原子完成、哈希与 SQLite 完整性；疑似问题先发 question，双方确认后才授权最小返修。
5. Codex 合并后串行运行专项、编码/静态门、临时夹具 smoke、`compileall` 与 diff-check，通过后中文提交、更新交接并推送协作分支。

## 2. 独立运行边界

- A worktree：`C:\Users\Administrator\biaoshu-v1a-grok-a`，只写五个生产入口文件。
- B worktree：`C:\Users\Administrator\biaoshu-v1a-grok-b`，只写一个专项测试文件。
- 两边不得监听或终止 8000/5173，不得读取主仓 `backend/data/biaoshu.db`、`backend/uploads` 或根 `uploads`，不得创建真实备份目录。
- 测试只允许 `tempfile`/临时 PowerShell 夹具；禁止管理员权限、网络、第三方包、sleep 作为完成证据和并发测试。

## 3. 反假绿检查点

- “备份成功”必须由临时假 SQLite 的副本 `integrity_check=ok`、全文件哈希/大小和严格 manifest 同时证明；只看目录存在或脚本 exit 0 不算通过。
- 必须真实篡改源/副本、构造 corrupt DB、占用假监听门或注入归属快照，证明失败路径删除临时目录且无最终目录。
- Stop 测试不得杀真实进程；通过纯判定函数/注入快照/`WhatIf` 证明 foreign listener 导致所有 PID 零终止，不能只检查字符串含 `taskkill`。
- A/B 必须使用契约冻结的 Python 公开测试接口；不得各自发明不兼容函数名。Stop 的快照注入只允许 `WhatIf`，测试必须证明去掉 `WhatIf` 后固定拒绝。
- legacy 根必须以独立逻辑名进入 manifest，不能与 canonical uploads 合并；不存在时不得生成伪目录。
- manifest 与控制台均做敏感字符串门，且任何路径字段只能是规范相对路径。

## 4. 后续拆包

V1-A 通过后，优先冻结 V1-B 离线恢复与回滚演练；随后再评估数据根锚定迁移、启动入口统一和本地解析指引。在线热备、WAL、自动定时与云同步继续后置。

## 5. 实施与独立验收结果（2026-07-21）

- Grok B 在无生产入口基线上完成真实 failure-first：初版 `50 failed / 1 passed`，最终测试版 `59 failed / 1 passed`，首个业务失败始终为根备份入口缺失；没有收集错误、skip 或 xfail。
- Codex 首次组合运行暴露 `45 passed / 6 failed`，确认五项测试误判/注入缺口和五项生产缺陷；后续又发现 wildcard 双栈监听漏检、PowerShell 5.1 中文 stderr 乱码及正路径反假绿。每一轮均先发 `question`，Grok A/B 独立确认存在后才授权最小返修。
- 最终严格六文件：两个根 bat、两个 UTF-8 BOM PowerShell、一个标准库备份核心和一个专项测试；实现提交=`5b4ad39`。
- Codex 在全新临时组合目录及主工作区分别串行通过专项 `60 passed / 0 failed / 0 errors / 0 skipped`；两个 PS1 均为 `EF-BB-BF` 且解析 0 错误，Python `compileall` 与 `git diff --check` 通过。
- 最终生产哈希：Stop bat=`1D1F3B38E9DA9D2E6284B46B21F4E0CF602BF8ED177885BFD1CDADE2B54E42A5`；Backup bat=`27F50F4466EF4131F1CBD80B57A978BEBB93FC109C828062D1EC7373B175781C`；Stop PS1=`699D2084BC9CA97A4E905CACB274B7318EDAFD42DAD13024D2A571FB58D893C8`；Backup PS1=`B735E3E29620F60BE10A34DA2DD773CC6882BB2145F574BDA62B6917613A5383`；Python 核心=`282FE38C55DC1E4C46ACBE499619E7063A6DAF5FDC62EDC27DA0414EE62E40A6`。
- 全部验收仅使用临时假仓、假 SQLite、假 uploads 和注入监听快照；未真实终止服务、未读取或备份主仓真实业务数据、未创建真实业务备份。
