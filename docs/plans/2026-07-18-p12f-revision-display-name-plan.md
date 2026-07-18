# P12F-H 单条修订命名实施计划

> **执行者：Grok**：初始严格十文件，范围修订后严格十六文件；先只写后端专项和 P12F-H history E2E 形成真实业务红测，再实现后端与技术/商务共用前端，并机械同步六份既有后端元数据合同测试；只自测并通过消息箱请求审查，不暂存、不提交、不推送。
>
> **状态：** 2026-07-18 初始冻结=`0660145`；failure-first 已完成，当前范围修订把六份必要既有后端精确合同测试加入最终十六文件边界。

**目标：** 为有限自动修订提供可选展示名称，闭合严格单条 PATCH、六键元数据、保存/覆盖/清除、失败保值和跨项目迟到隔离。

**架构：** revision 行增加 nullable `display_name`；独立 name service 只投影项目 ID并三谓词 UPDATE；history 只增加名称投影而不改变游标/搜索；共用面板原位更新名称，不重载列表。

## 1. 基线与 failure-first

1. 核对分支、远端、工作区、初始冻结/范围修订提交和十六文件哈希；完整阅读 H 契约及 P12F-A/F/G、history/restore/数据库迁移契约。
2. 第一阶段只新建 `backend/tests/test_p12f_revision_name.py` 并修改 history E2E 的 P12F-H 测试/探针；八个生产文件哈希保持冻结值。
3. 后端红测必须通过真实 ASGI 请求得到新路由 404；前端红测必须进入页面后因六键名称/命名入口缺失失败。记录 failed/passed/did-not-run 与首个业务失败；导入、收集、语法、服务、登录或 serial 跳过不算红测。

## 2. 后端数据与迁移

1. ORM 增加 nullable `display_name VARCHAR(160)`，新修订默认 null；不改来源、索引或裁剪。
2. 在九来源 CHECK 迁移成功后幂等加列，避免旧八来源表重建遗漏新列；旧库行数、八原字段和全部索引不变。
3. 新 name service 完成规范化、项目只投影、三谓词单列 UPDATE、rowcount 防御、唯一 commit/rollback 和固定脱敏错误；禁止读取快照或整实体。

## 3. 后端 API 与读取

1. schema 增加严格请求/响应；路由手工安全读取 JSON 对象，query/缺失/extra/非法值固定 422，PATCH 继续统一 CSRF/角色/workspace。
2. 成功 200 只回 `displayName`；项目/修订 404 和 500 固定脱敏，所有响应 no-store。
3. history list/page/search/detail 投影及 meta schema 精确增加 nullable `displayName`；坏名称整次 corrupt。page cursor、排序、筛选、LIMIT 11、search 候选与匹配集合保持不变。
4. 删除、恢复、裁剪与 recorder 只通过 nullable 列自然兼容；禁止为了名称加载或复制快照。

六键响应同时要求机械同步六份既有精确合同测试：history read、cursor page、source filter、time range、content search、delete。只准调整 `_META_KEYS`、search 七列投影和 delete 只读投影守卫，不得放宽原断言。

## 4. 共用前端

1. API meta/type/parser 扩为严格六键；新增 PATCH 封装和严格一键响应校验。
2. 面板增加单条内联命名：输入/取消零请求，保存或清除精确一次请求；成功原位更新，失败保留，不重载 page/search。
3. 命名确认/在途与现有七类意图及所有非命名控件互斥；mounted/project/session/name generation/revision 四重围栏隔离迟到 success/catch/finally。
4. 用户名称仅 React 文本渲染；不进 URL、存储、Cookie、console、错误、后端 detail 或 HTML 注入。

## 5. 反假绿审查

1. 后端精确检查 request/response、query/body、CSRF/角色/空间、SQL 投影/UPDATE、rowcount、commit/rollback、零旁路和迁移顺序。
2. E2E 探针区分 PATCH arrived/complete，支持 ok/hold/HTTP error；精确断言保存/覆盖/清除/失败保值、零重载与 A→B 迟到重叠。
3. 六键 route 夹具必须覆盖所有既有 history 用例，不得以 `|| null`、可选字段或宽 parser 掩盖缺失 `displayName`。
4. 扫描 OR 假绿、可选首项、`Math.min`、条件断言、宽计数、固定 sleep、skip、`force:true`、fallback、ID/名称/快照/CSRF 泄漏和超范围文件。

## 6. 串行验收与交付

按契约第 9 节逐条串行运行后端专项、受影响回归、后端全量、P12F-H、history、checkpoint、技术/商务 truth、lint、build；Grok 完成后发送 `review_request`，报告红测、精确十六文件、结果、事务/迁移/六键/PATCH/迟到/零旁路证据、哈希、风险和未做项。

Codex 进行受限差异审查，必要时仅在十六文件内下发最小返修；独立重跑全部门和前端全量，通过后才中文提交、推送并更新契约、路线图、交接和联调文档。

## 7. 未做

固定/置顶、裁剪保护、批量命名、标签/备注、名称搜索/排序、检查点命名、跨项目历史、多人协作、SSE、审计、索引、缓存和通用 metadata 框架均不进入本包。
