# P12F-J-B 修订固定状态七键响应与前端入口实施计划

> **执行者：Grok**：严格十四文件；先形成真实后端七键/坏固定值和前端入口红测，再实现六个生产文件；只自测并通过消息箱请求审查，不暂存、不提交、不推送。
>
> **审查者：Codex**：核对冻结哈希、逐文件受限审查、独立串行验收、中文文档闭环、提交并推送协作分支。
>
> **状态：** 2026-07-19 已完成；冻结=`f019a4b`，实现=`5ef7abd`，Codex 验收回执=`msg_8399a348aa1543e2b4b61cbdd25b4ac9`。

**目标：** 把 P12F-J-A 的权威固定状态接入 list/page/search/detail 七键响应与技术/商务共用面板，提供严格单飞、原位更新、失败保值和迟到隔离的固定/取消固定入口。

## 1. 实施顺序

1. 第一阶段仅修改合同列出的八个后端测试文件和 history E2E：把精确六键期望升级为七键，增加 SQLite 原始 `is_pinned=2`、page lookahead、search 候选、detail、pin 后读取联调证据；E2E 探针显式增加 `isPinned`，新增 P12F-J-B 技术/商务红测。六个生产文件必须保持冻结哈希。
2. 第二阶段修改后端三个生产文件：输出 Schema 增加 `isPinned`；history 四类查询以 `type_coerce(Integer)` 读取原始固定值；共用校验仅接受 0/1 并转换 bool；路由统一映射。不得触碰 P12F-J-A 的表、迁移、pin service、裁剪和 PATCH 语义。
3. 第三阶段修改前端 API：meta/detail 严格七/八键；详情七项逐值一致；新增精确一键 pin PATCH 与响应值等于请求目标校验。
4. 第四阶段修改共用面板：显示“已固定”；单击固定/取消；同步 ref 单飞；全操作互斥；成功只原位更新；失败保值；项目切换/折叠/卸载和 A→B 迟到 success/catch/finally 全围栏。
5. 第五阶段补齐 E2E route 日志与 arrived/complete gate，先跑聚焦，再 history 全文件、两份 editor-state truth、checkpoint、lint/build，最后全量单 worker E2E。

## 2. Grok 自检与回执要求

Grok 的 `review_request` 必须报告：

- 真实 failure-first 命令、通过/失败/未运行数字和首个业务失败；
- 精确十四文件清单、冻结前后 SHA-256、空暂存区；
- 后端四类 SQL 原始固定值投影、0/1 严格校验、坏值零写证据；
- 前端 pin 请求精确 path/query/body/CSRF/响应、零重载和原位更新证据；
- 技术/商务共享入口、双击单飞、全互斥、A→B 双 gate 与旧 finally 隔离；
- 所有专项/回归/全量命令的真实数字、耗时、警告、风险和明确未做项。

禁止只给“测试通过”、截断命令、并行运行、宽松统计或人工声称。若额度或工具中断，应保留工作区并说明最后完成的精确阶段，不得自行提交。

## 3. Codex 受限审查重点

1. 对比冻结哈希和白名单；拒绝 ORM/迁移/pin service/裁剪/共享请求层/CSS/hook/依赖/其它测试的任何修改。
2. 逐处确认 list/page/detail/search 都使用原始 Integer 投影；`is_pinned=2` 不能被 SQLAlchemy Boolean 结果处理器收敛成 `True`；page 第 11 条、search 未命中候选也必须先验证。
3. 确认响应只增加 `isPinned`，排序、游标、候选上限、来源/时间、名称搜索、正文验证、DELETE/restore/name 不变。
4. 确认前端 parser 精确拒绝缺失/extra/非 bool/相反 pin 响应；pin API 无 query/retry/额外 header；错误不反射敏感值。
5. 确认面板的同步单飞门在 await 前关闭，pin 加入所有既有 `exclusiveUiLocked` 分支；成功零 GET/POST search，失败不改 items；旧 A finally 不能解锁 B。
6. 审查 E2E 是否真实经过生产 parser/组件、使用 arrived+complete 双 gate、精确请求增量和不带 `force:true`；静态自检不能替代业务路径。

## 4. 独立验收与交付

Codex 按契约第 7 节逐条串行执行，先聚焦后全量。后端全量基线为 **1165 passed**，前端全量基线为 **318 passed**；新增用例会提高数量，不能以旧数字作为硬编码通过条件。仅在所有命令、diff-check、十四文件、空暂存区、哈希和静态门通过后：

1. 发送明确验收回执给 Grok；
2. 以中文 Commit Message 提交实现并推送 `collab/grok-code-codex-review`；
3. 更新契约、计划、`HANDOFF-next.md`、路线图和联调清单，记录冻结/实现/回执 SHA 与真实数字；
4. 再次提交中文文档闭环并推送；
5. 核对 branch、HEAD、tracking、`ls-remote` 四方一致且工作区干净，然后继续审计下一主线包。

## 5. 交付后边界

J-B 完成只代表固定状态可见、可操作且保护性裁剪闭环；仍不允许把固定理解为置顶排序或检查点。下一包必须重新按路线图价值、依赖和测试成本只读审计，不自动把批量固定、名称排序、检查点命名、跨项目历史或多人协作并入。

## 6. 实际执行与验收结论

1. Grok 完成十四文件实现，但首轮后端与前端并发自测违反串行契约，结果作废；后续进程中断，未发送最终 `review_request`。Codex 对实际工作区做独立逐文件审查，并在原白名单内修正刷新按钮未纳入 pin 锁、Hooks 依赖和 E2E 点击/状态清理/静态正则四类确定性问题。
2. 有效串行结果：后端专项 **297 passed**、全量 **1170 passed / 1 warning**、py_compile 通过；P12F-J-B 定向 **6 passed**、history **61 passed**、checkpoint restore **51 passed**、技术/商务 truth **28/18 passed**；lint 与 build 通过。整仓前端全量不重复执行，沿用上一包 **318 passed** 已验收基线。
3. 静态门确认精确十四文件、空暂存区、diff-check 通过，四处原始整数固定投影，零直接 Boolean 投影、零 history 写事务；响应/parser/PATCH/全局单飞/失败保值/A→B 围栏均有真实业务与静态证据。
