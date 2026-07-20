# P12M 修订搜索命中来源标签实施计划

> **状态：2026-07-20 已完成。** 冻结=`95b298f`、实现=`cc23542`；首轮严格七文件，受影响回归后由 Codex 明确扩展两份既有测试做 test-only 兼容。Grok 负责测试先行、实现及分级自测，Codex 已完成审查、独立验收、文档和提交。

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

## 执行结果

1. failure-first：P12M 后端 **3 failed / 0 passed**，生产文件仍为冻结哈希；缺失点为成功项无 `matchReasons`。
2. 首轮实现完成 search 专属八键、后端固定原因顺序、前端严格 parser 与技术/商务共用标签。Grok 搜索专项 **33 passed**，P12M/受影响 history E2E **2/6 passed**，lint/build/py_compile 通过。
3. 受影响后端首轮 **265 passed / 2 failed**；失败仅来自 name/pin 两份旧回归仍断言 search 七键。Codex 授权两文件 test-only 扩围后，两条定点各 **1 passed**，P12M 后端 **3 passed / 30 deselected**。
4. Codex 独立串行复验同样得到后端 **1/1/3 passed**、前端 **2/6 passed**，并通过 lint、py_compile、diff/哈希/空暂存/泄漏门；未机械重复后端全量或整仓 318 E2E。
5. 消息追溯：原任务/review=`msg_cd0cc6ff09e94cae98f81d54ded77846`/`msg_30fa964062c745e892d78074e4c283f7`；返修 task/review=`msg_b2f8890512d24f2c8d1dbf43508e373f`/`msg_565054cbdcdf40cb9536d5c21939d3d1`；Codex ack=`msg_935e7f7b28df4a8ab75227d6e124b2f1`。
