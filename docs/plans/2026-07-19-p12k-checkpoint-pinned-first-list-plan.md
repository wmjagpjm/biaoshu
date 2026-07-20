# P12K 检查点固定优先默认列表实施计划

> **状态：2026-07-20 已完成并推送。** 代码审计基线=`90cfd58`，契约冻结=`fe0fa08`，启动口径修订=`ff48495`/`6666af6`，实现=`3c3cbf9`；生产服务最终 SHA-256=`8C08B546E0DB8FA00FE4D6E15FB93A23650F15FA12C42E23EC100ED6EA7E371E`。执行者为 Grok，审查/验收/文档/提交/推送者为 Codex。
>
> **严格两文件**：一个现有 checkpoint service + 一个新 P12K 后端专项测试。先只写测试取得真实 failure-first，再改生产；pytest 逐条串行；不得暂存、提交或推送。

**目标：** 默认检查点 GET 列表改为固定优先、组内时间/ID 倒序；搜索候选与顺序、前端当前列表原位更新及所有写入/裁剪合同保持不变。

**权威契约：** `docs/p12k-checkpoint-pinned-first-list-contract.md`。

## 任务 1：核验冻结基线

1. 核对分支、启动 HEAD 与最新上游一致、工作区干净，且历史包含契约冻结提交 `fe0fa08`；复算生产服务哈希。`90cfd58` 只表示生产代码审计来源，`fe0fa08` 只表示契约冻结提交，二者都不得被误作必须回退到的启动 HEAD。
2. 完整阅读 P12I、P12J-A/B 契约和 service/list/search/pin/trim 相关测试；确认没有其它 pytest/Grok 并发写仓库或重置共享 SQLite。
3. 任何脏文件、哈希或范围偏差先发送 `status/question`。

## 任务 2：新增真实 failure-first 专项

1. 只新建 P12K 测试，覆盖混合固定、组内时间/ID、pin/unpin 下一次 GET、create 普通项、坏值零写与空间隔离。
2. 用 21 条真实种子证明 search 仍取最新 20 且纯时间/ID 倒序；用 AST/SQL 分别锁定 list 三项和 search 两项排序。
3. 串行运行 P12K 专项，记录精确 failed/passed 和首个业务失败；生产服务哈希必须保持冻结。

## 任务 3：最小实现默认列表排序

1. 只在 `list_editor_state_checkpoints` 的 ORDER BY 首项加入 `type_coerce(...is_pinned, Integer).desc()`，保留后两项和 LIMIT 20。
2. 同步该函数中文四字段/用途说明；禁止抽象通用排序器、Python 排序或触碰 search。
3. 不修改任何 API、Schema、模型、迁移、写服务、前端或既有测试。

## 任务 4：串行自测与审查请求

1. Grok 按契约第 6 节逐条运行专项、六文件受影响集、后端全量和 py_compile，不得并行；Codex 独立复跑受影响集与静态门，不重复 Grok 已完成的全量。
2. 执行 diff-check、两文件白名单、空暂存区、最终哈希、AST/SQL/写调用/弱断言扫描。
3. 只向 Codex 发送一个完整 `review_request`；不得 Git add/commit/push。

## 完成标准

- 默认列表固定优先且组内稳定倒序有真实 HTTP/SQLite/SQL 证据；
- 搜索最新 20 候选与纯时间顺序未被改变；
- P12J-A/B 固定、配额、裁剪、八/九键和前端原位更新无回退；
- 严格两文件、Grok 零 Git 写操作；Codex 独立串行验收后才允许中文提交与推送。

## 实施结果

1. failure-first 为 **8 failed / 4 passed**，首个真实业务失败是较旧固定项仍落在较新普通项之后；另有一个隔离测试夹具 `TypeError`，在生产实现前先修复，未冒充业务失败。
2. Grok 初始 task/review=`msg_24d08a0202954060b4c4ab3b0a35942d`/`msg_131b165976c64b2fb05ceb0792122a5c`，test-only 返修 task/review=`msg_b1b3d1fb809c4a579ed35dfd9a875615`/`msg_4e2f742d8ac2469fad123e367922f6fa`。生产实现始终保持最小一项 ORDER BY 变更。
3. Grok 最终串行通过专项 **12**、受影响集 **132**、后端全量 **1273 passed in 1674.75s**；Codex 独立串行通过受影响集 **132 passed in 106.74s**，并完成编译、diff、白名单、空暂存区、哈希与 SQL/AST 审查。验收确认=`msg_3048a39db0c04969978a7e2dd7ea0c60`。
4. 实现提交=`3c3cbf9`。前端未修改、未运行 Playwright，沿用 checkpoint **82** 与整仓 **318 passed** 基线；下一包尚未冻结，须重新只读审计。
