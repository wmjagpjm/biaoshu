# P12E-A 单条修订正文差异预览实施计划

对接：`docs/p12e-revision-body-diff-contract.md`、P12C-C1 修订历史、P12D-A/B 当前状态差异摘要与前端入口。  
执行方式：Codex 规划与受限审查；Grok 只按白名单实现和自测；Codex 独立验收、中文文档闭环、提交并推送。

> **完成状态（2026-07-17）**：冻结=`5aa205c`，实现=`f9f067e`；后端专项/受影响回归/全量为 **23/27/854 passed**，前端 history/checkpoint/truth/全量为 **27/51/46/290 passed**，E2E 均为单 worker、零重试串行执行。

## 1. 开工基线

- 分支必须是 `collab/grok-code-codex-review`。
- HEAD、远端和工作区必须一致且干净；发现未知脏文件先发 `question`，不得覆盖。
- 当前基线提交：P12D-B 闭环 `c7cf67f`。
- 所有命令在 Windows 后台静默执行；E2E 共享 SQLite，严禁并行 Playwright。

## 2. 实施顺序

1. 先新增后端真实 SQLite 专项与前端三条独立 E2E 断言，生产入口保持不改并运行 failure-first。
2. 新建只读正文差异服务，复用 C1 目标校验和 editor-state 13 键权威读取；实现唯一配对、完整值判等、标准库行差异和固定预算。
3. 在 schemas/路由挂载唯一 `body-diff` GET，所有成功/业务错误 `no-store`，固定脱敏失败。
4. 在现有 API 封装增加严格正文差异 parser；组件增加按需按钮、互斥状态、代次隔离、迟到 arrived/complete 证据和有界中文渲染。
5. 逐条运行契约要求的专项、P12D-A/C1/C2/检查点受影响回归、三组前端 truth、lint/build 和 diff 检查；未提交任何文件。

## 3. 受限审查重点

- 后端不得用 Python `==`、版本号、长度或摘要替代正文逐值比较；不得因为截断而误报相同。
- 章节配对必须在服务端完成且不泄漏 ID；重复/脏 ID 必须固定失败，不能按猜测顺序吞掉错误。
- 只读服务不得打开写事务、锁、审计或 HTTP；失败不得泄漏 SQL、路径、异常类型和正文。
- parser 必须拒绝顶层、item、hunk 的额外键、缺键、未知枚举、负数、超大字符串、乱序/重复 hunk 及计数不一致。
- 前端比较按钮不得自动触发；summary、comparison、body-diff、restore 互斥；必须同时有 arrived 和 complete 真实完成证据，不能只看 route 命中。
- 迟到请求的旧 `catch/finally` 不得覆盖新项目、新修订或折叠后的状态；不能用固定 sleep、`.or(...)` 或宽泛 2xx 断言制造假绿。

## 4. 完成条件

- Grok 只发送 `review_request`，精确报告红测、最终命令、七文件白名单、截断和零写证据，不提交不推送。
- Codex 逐行审查服务、schema、路由、parser、组件和 E2E；发现假绿或越界只通过消息箱退回同一任务的定点返修。
- 独立后端专项、受影响回归、前端专项/真值、lint/build、前端单 worker 零重试全量和 `git diff --check` 全部通过。
- Codex 更新 `docs/HANDOFF-next.md`、`docs/integration-checklist.md`、路线图和本计划的完成状态，记录真实红测偏差，不把 `did not run` 冒充失败/通过。
- Codex 使用中文提交信息提交并推送，核对 HEAD、远端 SHA 和干净工作区；P12E-A 未实现边界继续保留。

## 5. 留给后续包的边界

本计划完成后，正文差异仍只针对“一条历史修订 ↔ 当前状态”，不等于任意历史两两比较、完整版本时间线、正文恢复、修订删除、分页搜索或多人协作。后续包必须重新只读审计、排序和冻结，不能沿用本包白名单顺手扩大。

## 6. 实际审查与验收记录

1. Grok 在额度中断前留下半成品，恢复后补齐 20,000 码点在 difflib 前截断、三条独立 E2E、严格 parser 与 arrived/complete 迟到隔离；首个完整审查请求为 `msg_7409f0e20158437f99ed689e148c7028`。
2. Codex 首轮受限审查用独立探针证明 101 个正文差异章会调用 `_diff_lines` **101 次**，违反最多处理 100 个章节的资源边界。返修任务=`msg_f09905515e974049827cd981087884c6`；Grok 真实红测为 **1 failed / 1 passed**，修后 **2 passed**，最终审查请求=`msg_c24f270186a741a09a33781e84b1e762`。
3. 返修后完整值扫描仍覆盖展示上限后的章节；只有前 100 个实际正文差异章进入 difflib。Codex 探针得到 `diff_calls=100`、`raw_items=100`、`any_diff=true`、`body_truncated=true`，同时覆盖“前 100 章相同、第 101 章才不同”仍返回 `sameBody=false` 与非空有界项。
4. Codex 独立通过后端专项 **23 passed**、P12D/P12C 受影响回归 **27 passed**、后端串行全量 **854 passed**（1 条既有 Starlette/httpx 弃用告警，1198.62 秒）。
5. 前端严格串行通过 history **27**、checkpoint **51**、技术/商务 truth **46**、全量 **290 passed**（8.3 分钟）；全部使用 `--workers=1 --retries=0`。`npm run lint`、`npm run build`、`git diff --check`、精确七文件白名单与空暂存区均通过；build 只有既有 chunk 大小提示。
6. Codex 验收确认=`msg_1432aa1aacf944d28b2089dda8f2bb7c`。实现以中文提交 `f9f067e` 推送；未扩入任意历史两两比较、正文自动恢复、删除、搜索、分页、导出、分享或多人协作。
