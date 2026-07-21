# V1-A 受控停机与离线备份基础实施计划

> 状态：契约已冻结，等待 Grok A/B failure-first
> 契约：`docs/v1a-controlled-stop-offline-backup-contract.md`
> 基线：`2ba8983`（P13-I4 闭环与版本分层）

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
- legacy 根必须以独立逻辑名进入 manifest，不能与 canonical uploads 合并；不存在时不得生成伪目录。
- manifest 与控制台均做敏感字符串门，且任何路径字段只能是规范相对路径。

## 4. 后续拆包

V1-A 通过后，优先冻结 V1-B 离线恢复与回滚演练；随后再评估数据根锚定迁移、启动入口统一和本地解析指引。在线热备、WAL、自动定时与云同步继续后置。
