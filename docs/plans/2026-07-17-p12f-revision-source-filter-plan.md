# P12F-D 修订历史来源筛选实施计划

> **执行者：Grok**：严格六文件，先形成后端与前端真实业务红测再实现；Codex 负责独立规划、受限审查、独立验收、中文文档闭环和协作分支推送。
>
> **状态：** 2026-07-18 已完成并推送；冻结=`a2acdf3`、实现=`587df9a`。

**目标：** 在技术标与商务标共用修订面板中按九类权威来源筛选完整有界时间线，并用版本化游标严格绑定筛选条件。

**技术栈：** FastAPI、SQLAlchemy、SQLite 测试库、React 19、TypeScript 6、Playwright Chromium headless。

## 1. 基线与红测

1. 核对分支、HEAD/远端和干净工作区，阅读契约、P12F-B/C 实现与测试。
2. 只新增 `test_p12f_revision_source_filter.py`，并只修改 history E2E 增加 P12F-D 探针/场景；确认四个生产文件哈希未变。
3. 后端专项应因 `/page` 仍忽略 `sourceKind`、无 `esrc2` 绑定而失败；前端聚焦应因无筛选器而失败。记录精确 passed/failed/did-not-run 数字和首个业务失败。

## 2. 后端筛选与游标

1. 路由只新增可选别名 `sourceKind`，严格校验交给 history service；响应模型和旧列表不变。
2. service 复用权威来源枚举，合法筛选进入五列 `LIMIT 11` 查询谓词；无筛选 SQL/结果保持兼容。
3. 保留 `esrc1 {i,t}`；新增 `esrc2 {i,s,t}`，实现规范编解码、来源一致性校验和固定脱敏错误。
4. 维持项目/空间隔离、lookahead 全量校验、no-store、只读零写和现有 corrupt 语义。

## 3. 前端 API 与面板

1. API 封装接受可选来源筛选，精确构造无参数、仅 sourceKind、仅 cursor、sourceKind+cursor 四种 GET；parser 只扩展接受合法 `esrc2_` 外壳。
2. 面板增加“全部来源”及九类中文选项；筛选切换清理旧意图并加载新第一页，错误不得显示旧来源结果。
3. 刷新/恢复沿用当前筛选；折叠保留、项目切换重置；筛选分页继续 20 条上限、失败保值和同步单飞。
4. 用请求代次和同步 ref 绑定 project/filter/cursor，覆盖切换、折叠、刷新、恢复和卸载后的迟到 success/catch/finally。

## 4. 验收与反假绿

1. 后端覆盖全枚举、边界分页、两版游标绑定、非法输入、跨域、SQL 投影/谓词/limit、损坏与五域零写。
2. 前端覆盖技术/商务、请求精确性、筛选分页、失败保值、交互禁用、筛选生命周期和 arrived+complete 迟到隔离。
3. Grok 串行运行规定定向与受影响回归、lint/build/diff-check，发送 review_request；不得提交。
4. Codex 检查测试断言是否存在恒真、宽泛计数、泄漏跳过或 route 旁路；必要时只下发六文件内受限返修。
5. Codex 独立执行聚焦、受影响回归和后端/前端全量，随后中文提交实现、更新主交接/路线图/联调清单/契约计划并推送。

## 5. 未做

不做全文搜索、日期/多选筛选、删除、命名、固定、导出、分享、自动加载、跨项目历史、多人协作、数据库迁移或 SSE 扩展。

## 6. 实际执行与验收

1. Failure-first 实际为后端 **38 failed / 17 passed**、前端 **2 failed / 0 passed / 1 did-not-run**；首个失败分别是后端仍返回混排全集、前端不存在筛选器，不是收集、fixture、浏览器或语法假红。
2. Grok 首版实现后经三轮最小返修：先补强后端精确错误/SQL/AST 与前端失败保值、恢复在途、Cookie 证据；再按冻结契约第 42 行修正 `esrc2` 携非法筛选的 cursor-invalid 优先级并精确证明 `LIMIT 11`；最后清除残留 `assert A or B`，全文件禁止模式扫描零命中。
3. Codex 独立通过后端 **68/48/986 passed**；前端 **3/37/28/18/51/300 passed**。前端全量为单 worker、零重试 **300 passed（7.5m）**；后端全量 **986 passed（22m37s）**。
4. `lint`、`build`、`py_compile`、diff-check、六文件白名单、空暂存区、弱断言扫描均通过；验收回执=`msg_d977b2ead50b4f8292852c9b2de95b08`。
5. 实现由 Codex 以中文提交 `587df9a 功能：完成P12FD修订来源筛选` 并推送 `collab/grok-code-codex-review`；Grok 全程未暂存、提交或推送。
