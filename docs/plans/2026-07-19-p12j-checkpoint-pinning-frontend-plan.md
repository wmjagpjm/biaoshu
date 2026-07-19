# P12J-B 检查点固定状态响应与前端入口实施计划

> **状态：2026-07-19 已冻结待实现。** 十一份白名单代码的哈希基线=`262683e`，契约冻结提交=`65fe259`；实现启动时以协作分支最新上游 HEAD 为准。实现者为 Grok，审查/验收/文档/提交/推送者为 Codex。
>
> **执行者：Grok**：严格十一文件；先只修改六个测试文件形成真实 failure-first，再改五个生产文件；pytest/Playwright 逐条串行；只通过消息箱请求审查，不暂存、不提交、不推送。

**目标：** 将检查点 create/list/search 七键和 detail 八键升级为含 `isPinned` 的八/九键，增加严格前端 parser、一键固定 API 与技术/商务共用原位固定入口，同时保持 P12J-A 存储/配额/裁剪、搜索、命名、删除、恢复和当前 editor-state 行为不变。

**权威契约：** `docs/p12j-checkpoint-pinning-frontend-contract.md`。

## 任务 1：核验冻结基线

1. 核对分支、当前 HEAD 与上游一致、工作区干净；再核对十一文件哈希等于契约第 5 节。`262683e` 只表示白名单代码哈希来源，不是实现启动时必须停留的当前 HEAD。
2. 完整阅读 P12J-A/B、P12G/H/I、P12F-J-B 契约及十一文件；P12F-J-B 只可参考模式，不得复制修订分页/比较功能。
3. 确认没有其它 pytest/Playwright/Grok 同时写仓库或重置共享 SQLite；异常先发 `status/question`。

## 任务 2：只写真实 failure-first

1. 五个后端测试文件先机械把七/八键升级为八/九键，并在 P12J 专项加入 create false、pin 后 list/search/detail、原始 `is_pinned=2` 的 list/detail/search 未命中候选固定失败与零写。
2. checkpoint E2E 探针先显式增加 `isPinned`，新增 P12J-B 技术/商务红测，覆盖入口缺失、严格 parser 和 pin route；不得改前端生产文件。
3. 逐条串行运行红测，记录精确 failed/passed/did-not-run 与首个业务失败；复算五个生产文件哈希仍等于冻结。

## 任务 3：实现后端八/九键读取

1. Schema 元数据/详情增加必填布尔 `isPinned`；路由 `_meta_out`/detail 用索引显式映射，禁止默认值。
2. create 返回 false；list/detail/search 增加原始 Integer 投影并严格只接受 0/1。detail 改显式投影，禁止 ORM Boolean 吞非法值。
3. 保持 list 无正文、detail/search 既有正文边界、三重作用域、20 条倒序、搜索完整校验与零写；不触碰 P12J-A 写路径。

## 任务 4：实现前端严格 API

1. 元数据类型与精确键升级为八键；保持 create stateVersion 专用错误优先级，新增固定值原生布尔验证。
2. 增加精确一键 pin 响应 parser 和 `setEditorStateCheckpointPin`；请求值与响应值必须相等。
3. 错误固定脱敏，禁止响应原文、ID、路径、固定值或异常消息进入 UI/console/存储。

## 任务 5：实现共用面板固定入口

1. 增加 badge、固定/取消按钮、固定中文状态和全局同步单飞；固定按钮不依赖编辑态 disabled，但与全部检查点操作真实互斥。
2. success 只原位更新目标 `isPinned`；失败全部保值；active search 关键词/结果/顺序不变且零重载。
3. 增加 mounted/session/generation/project/checkpoint 围栏；项目切换/折叠作废旧请求，A success/catch/finally 不污染或解锁 B。

## 任务 6：补齐确定性证据并串行自测

1. 后端证明三处原始投影、坏值未命中候选仍失败、create/safety false、name/delete/restore/search 兼容和五域零写。
2. E2E 精确记录 pin arrived/complete；覆盖技术固定/取消/失败/严格响应/双击/另一行/全部 disabled/active search/A→B，商务复用与零旁路。
3. 按契约第 7 节逐条串行运行；不运行整仓前端 318，不与后端并发；再做 lint/build/py_compile/diff-check/十一文件/哈希/空暂存区/弱断言扫描。

## 任务 7：请求 Codex 审查

1. 只发送一个 `review_request`，内容满足契约第 8 节；不得暂存、提交或推送。
2. Codex 若返修，只改新下发的精确文件和问题；仍先形成对应红测，再串行验证并回执。

## 完成标准

- 后端八/九键与非法固定读取有真实行为证据；
- 前端固定入口、严格 parser、单飞/互斥/原位更新/迟到隔离有技术与商务证据；
- P12J-A 存储、配额、裁剪、PATCH 与其它检查点功能无回退；
- Grok 零 Git 写操作；Codex 独立验收后才允许中文实现提交、文档闭环与协作分支推送。
