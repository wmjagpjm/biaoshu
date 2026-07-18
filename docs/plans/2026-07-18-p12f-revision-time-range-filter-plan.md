# P12F-E-A 修订时间范围筛选后端实施计划

> **执行者：Grok**：严格三文件，先形成真实后端业务红测再实现；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成并推送。冻结=`af3798a`、实现=`c66b69d`，Codex 独立后端全量 **1073 passed**。

**目标：** 为既有修订键集页增加严格 UTC 包含下界/排除上界，并以 `esrc3` 把时间范围、可选来源和分页位置共同绑定，同时保持 V1/V2 完全兼容。

**技术栈：** FastAPI、SQLAlchemy、SQLite 测试库、pytest。

## 1. 基线与真实红测

1. 核验分支、HEAD/远端和干净工作区，完整阅读契约、P12F-B/D 服务、路由及三组既有回归。
2. 只新增 `backend/tests/test_p12f_revision_time_range_filter.py`；记录两个生产文件 SHA-256，确认红测阶段未变。
3. 运行新专项，必须得到由 query 被忽略、缺少 V3 游标造成的业务失败；记录精确 passed/failed 和首个业务失败。

## 2. 严格时间范围与 V3 游标

1. 路由只新增可选字符串别名 `createdFrom`、`createdBefore`，校验全部委托 history service；响应模型和旧列表不变。
2. service 新增严格 24 字符 UTC 毫秒解析、包含下界/排除上界、固定 time-range-invalid；保持项目 404、V2/V3 绑定错误和来源校验的冻结优先级。
3. 保留 `esrc1 {i,t}` 与 `esrc2 {i,s,t}`；新增 `esrc3 {b,f,i,s,t}` 的规范编解码和条件全等绑定，V3 长度上限 256，禁止从游标采用筛选条件。
4. 五列 SQL 在 workspace/project 后组合 source/from/before/keyset 谓词，继续排序和 `LIMIT 11`；完整校验 lookahead，维持 no-store、损坏脱敏及五域零写。

## 3. 审查与验收

1. Grok 依次运行新专项、P12F-D/B/C1 合并回归、`py_compile`、diff-check、精确三文件和空暂存区，随后通过既有消息箱发送 review_request；不得提交。
2. Codex 检查错误优先级、时间规范、微秒/毫秒边界、V1/V2 兼容、V3 正反绑定、SQL 谓词/投影/limit、lookahead 和测试反假绿；只允许三文件内最小返修。
3. Codex 独立重跑专项、受影响回归和后端全量；验收后以中文提交实现、推送协作分支，再更新契约/计划/主交接/路线图/联调清单形成独立文档闭环。

## 4. 后续边界

P12F-E-A 只交付后端基础。前端日期控件、浏览器时区转换和交互迟到隔离由 P12F-E-B 另包；来源多选、正文搜索、命名/固定/删除、跨项目历史、多人协作及 SSE 扩展继续不得混入。

## 5. 实施与验收记录

1. 冻结提交 `af3798a` 后，Grok 只新增专项测试形成 **74 failed / 12 passed** 的真实业务红测；首轮实现专项 **86 passed**、P12F-D/B/C1 合并回归 **116 passed**。
2. Codex 首轮审查直接复现 V3 双空、相等、倒置范围可被编解码器接受；同时识别第二页 keyset 的 `created_at < cursor` 可让 SQL 上界断言假绿，并要求精确拆分首/次页证据。
3. Grok 返修只改 service 与新测试，统一增加 V3 时间语义门：至少一个边界、双边严格 `f<b`、末条位置满足 `t>=f` 且 `t<b`。专项增为 **87 passed**，受影响回归保持 **116 passed**。
4. Codex 独立复现非法/合法边界，运行专项 **87 passed**、受影响回归 **116 passed**、后端全量 **1073 passed**；编译、diff-check、AST 弱断言扫描、三文件白名单和空暂存区全部通过。
5. 验收回执 `msg_0533a4bab32448b0be8d5ec2b0ba1508` 后，由 Codex 提交实现 `c66b69d` 并推送。下一步只能先只读审计 P12F-E-B 前端日期控件，不得直接沿用 A 包白名单。
