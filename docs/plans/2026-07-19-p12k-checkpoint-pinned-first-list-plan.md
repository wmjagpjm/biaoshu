# P12K 检查点固定优先默认列表实施计划

> **状态：2026-07-19 已冻结待实现。** 冻结基线=`90cfd58`；生产服务 SHA-256=`20A0FBACFE20DF4D6FE0157B2DF6F41436EDAC5B298F6D2174803E7A66CF4DC3`。执行者为 Grok，审查/验收/文档/提交/推送者为 Codex。
>
> **严格两文件**：一个现有 checkpoint service + 一个新 P12K 后端专项测试。先只写测试取得真实 failure-first，再改生产；pytest 逐条串行；不得暂存、提交或推送。

**目标：** 默认检查点 GET 列表改为固定优先、组内时间/ID 倒序；搜索候选与顺序、前端当前列表原位更新及所有写入/裁剪合同保持不变。

**权威契约：** `docs/p12k-checkpoint-pinned-first-list-contract.md`。

## 任务 1：核验冻结基线

1. 核对分支、HEAD=`90cfd58`、上游一致和工作区干净；复算生产服务哈希。
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

1. 按契约第 6 节逐条运行专项、六文件受影响集、后端全量和 py_compile，不得并行。
2. 执行 diff-check、两文件白名单、空暂存区、最终哈希、AST/SQL/写调用/弱断言扫描。
3. 只向 Codex 发送一个完整 `review_request`；不得 Git add/commit/push。

## 完成标准

- 默认列表固定优先且组内稳定倒序有真实 HTTP/SQLite/SQL 证据；
- 搜索最新 20 候选与纯时间顺序未被改变；
- P12J-A/B 固定、配额、裁剪、八/九键和前端原位更新无回退；
- 严格两文件、Grok 零 Git 写操作；Codex 独立串行验收后才允许中文提交与推送。
