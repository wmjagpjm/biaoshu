# P12M 修订搜索命中来源标签实施计划

> **状态：2026-07-20 已冻结待实现。** 启动 HEAD=`37a4461`；严格七文件，Grok 负责测试先行、实现及分级自测，Codex 负责审查、独立验收、文档、提交和推送。

**目标：** 当前项目修订搜索成功项新增精确 `matchReasons`，按固定顺序说明命中名称、可见内容或两者；技术标/商务标共用面板显示固定中文标签，零正文/关键词泄漏。

**权威契约：** `docs/p12m-revision-search-match-reasons-contract.md`。

## 任务 1：核验冻结基线

1. 核对分支、HEAD/上游=`37a4461`、工作区干净、无其它 pytest/Playwright/Grok 任务；复算第 6 节七文件哈希。
2. 阅读 P12F-F-A/B、P12F-H/I/J-B 契约、search service/route/schema、前端 parser/panel/history E2E；确认搜索候选、排序、预算、错误和一次 POST 不变。
3. 任一脏文件、哈希、并发进程或范围偏差先发 `status/question`。

## 任务 2：failure-first

1. 第一阶段只改既有 backend search 测试与 history E2E；生产五文件哈希保持冻结。
2. 用真实 HTTP/SQLite/浏览器证明名称、可见内容、双命中、非法响应和标签缺失的真实失败，不把路由/收集错误算业务红测。
3. 逐条运行 P12M 后端专项与前端聚焦，记录精确 failed/passed/did-not-run。

## 任务 3：最小后端响应实现

1. service 在现有完整校验后从 `name_match`/`snapshot_match` 生成固定原因数组；禁止额外扫描、短路、正文或关键词返回。
2. schemas/route 为 search 使用精确八键专属模型；list/page/detail 继续旧键集。
3. 保留 `{items}`、20 条、时间/来源过滤、no-store、零写和脱敏错误。

## 任务 4：最小前端接入

1. API 增加搜索专属类型与严格八键 parser；普通 list/page parser 不接受原因键。
2. 面板只在搜索态显示固定中文标签，技术/商务共用；禁止高亮、片段、query/body/ID/内部值回显。
3. 保留搜索一次 POST、顺序、失败保值、刷新/清除/切项目迟到隔离和零额外请求。

## 任务 5：分级串行自测与回执

1. Grok 逐条运行后端专项/受影响回归、P12M history 聚焦/受影响 E2E、lint/build/py_compile；禁止后端全量与整仓 318 重复。
2. 运行 diff-check、精确七文件、空暂存区、最终哈希、SQL/AST/弱断言/泄漏/新增请求静态门。
3. 只发一个 `review_request`，不得 Git add/commit/push。

## 完成标准

- 搜索项原因数组严格、稳定、无泄漏，名称/内容/双命中有真实证据；
- 现有候选窗口、排序、过滤、坏值零写和一次请求合同无回退；
- 技术/商务共用固定中文标签，严格七文件，Grok 零 Git 写操作；Codex 独立分级验收后才提交。
