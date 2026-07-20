# P12L 检查点固定名额提示前端实施计划

> **状态：2026-07-20 已完成并推送。** 代码哈希基线=`5258f84`，契约冻结=`4526832`，启动口径修订=`d21cfb5`，实现=`cc6bf11`；严格两文件为共用 checkpoint 面板与既有 checkpoint E2E。Grok 负责测试先行、实现及分级自测，Codex 负责审查、独立聚焦验收、文档、提交和推送。

**目标：** 技术标/商务标默认检查点列表显示 `已固定 X 条（最多 5 条）`；既有 pin/unpin/delete/list 状态变化即时重算，搜索态隐藏，零新增请求和零后端变化。

**权威契约：** `docs/p12l-checkpoint-pinned-count-frontend-contract.md`。

## 任务 1：核验冻结基线

1. 核对分支为 `collab/grok-code-codex-review`，HEAD/上游均为最新冻结提交 `4526832`，工作区干净且无其它 Playwright/pytest/Grok 进程；`5258f84` 只表示两文件哈希审计来源，不得回退。
2. 复算面板/E2E 冻结哈希，阅读 P12J-B/P12K 契约、面板 list/search/pin/delete 状态链与 checkpoint 探针。
3. 任一脏文件、哈希、进程或范围偏差先发送 `status/question`，不得覆盖。

## 任务 2：新增真实 failure-first E2E

1. 第一阶段只修改 E2E，新增 `P12L` 聚焦用例；先确认页面、面板和默认列表真实加载，再因提示缺失失败。
2. 覆盖默认 0/X/5、pin/unpin、固定/普通删除、失败保值、5/5 仍请求、active search 隐藏/清除、技术/商务复用、A→B 迟到隔离与零泄漏。
3. 串行运行 `--grep "P12L"` 并记录精确 failed/passed/did-not-run；复算面板哈希仍为冻结值。

## 任务 3：实现纯派生名额提示

1. 在面板增加唯一展示常量 5，以及严格 `isPinned === true` 的 render 期计数；禁止新增 state/effect/API/缓存。
2. 只在展开、默认态、非 loading、无 listError 时展示稳定 testid 和精确中文；空成功列表为 0/5，搜索态始终隐藏。
3. 保留现有原位 pin 更新、删除移除、重载、disabled、搜索、请求代次和安全文案；禁止本地阻止第 6 条固定请求。

## 任务 4：分级串行自测与回执

1. 逐条运行 P12L 聚焦、一次完整 checkpoint 受影响套件、lint、build；不运行整仓 E2E 或后端 pytest。
2. 执行 `git diff --check`、精确两文件、空暂存区、最终 SHA-256、弱断言/skip/retry/sleep/泄漏/新增请求静态扫描。
3. 只发送一个完整 `review_request`，不得 Git add/commit/push。

## 完成标准

- 默认态名额提示准确、纯派生且技术/商务共用；
- pin/unpin/delete/失败/搜索/项目切换行为有真实浏览器证据且零额外请求；
- 搜索态不把子集冒充全局固定数，5/5 不在前端绕过服务端权威；
- 严格两文件、Grok 零 Git 写操作；Codex 独立聚焦验收后才允许中文提交与推送。

## 实施结果

1. failure-first 为 **4 failed / 1 passed**；页面和默认列表已真实加载，业务红测是提示 testid 缺失，不是路由/白页/服务失败。面板冻结哈希在红测阶段保持不变。
2. Grok 串行通过 P12L 聚焦/完整 checkpoint 受影响套件 **5/87 passed**，lint/build 通过；Codex 独立复跑聚焦 **5 passed in 16.0s**、lint 通过，并完成精确两文件、哈希和静态门。消息追溯：task=`msg_505a1e95046f405b8dff74be0cfaec5a`，review=`msg_9fae0b72abd84e0fbe2e56373a2f3ae0`，ack=`msg_a685c7123a4f4c9fac68481b99a25cec`。
3. 最终提交=`cc6bf11`；最终面板/E2E 哈希=`890621124EB953F8A81BF4E5975E75B76F03A6296089FF682C5DE94A5FF187AE`/`C8961E30831869659FBC37CD806F95D4ACFA608097CEC2C52DFFD4E6DC72055A`。下一包尚未冻结，须重新只读审计。
