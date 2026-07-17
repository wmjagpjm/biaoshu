# P12E-B 双修订正文差异后端契约

模块：P12E-B editor-state 任意两条同项目历史修订的章节正文差异只读基础
对接：P12E-A 单条修订对当前状态正文差异、P12C-C1 修订历史详情。
状态：2026-07-17 已完成；冻结=`00ef081`、实现=`5a5b08a`，Codex 已完成受限审查、独立验收、中文闭环和提交推送。

## 1. 目标与边界

P12E-A 只支持“一条历史修订 ↔ 请求时当前状态”。P12E-B 在后端增加“两条同项目历史修订之间”的只读正文差异基础，为后续前端双修订选择器提供稳定、脱敏、有界的接口。本包不改 P12E-A 的现有路由和响应，不改变任何写入链。

本包只比较同一 `workspace_id`、同一 `project_id` 下的两条 `revisionId`：

- `beforeRevisionId`：路径中的前一条修订，作为差异前正文；
- `afterRevisionId`：路径中的后一条修订，作为差异后正文。

本包不实现前端按钮、修订选择器、分页、搜索、任意项目历史、正文恢复、删除、导出、分享、缓存或多人协作。后续前端包必须重新冻结契约，不得直接把两个 ID 写入 URL 状态或浏览器存储。

## 2. 后端接口

新增唯一接口：

```text
GET /api/projects/{projectId}/editor-state-revisions/{beforeRevisionId}/body-diff/{afterRevisionId}
```

请求不得有 body、查询参数、重试、轮询或旁路请求；成功和业务错误均带 `Cache-Control: no-store`。两个修订都必须通过 P12C-C1 的 workspace/project/id 三重作用域和快照完整性重验；任一不存在/跨项目/跨工作空间按既有 404 脱敏错误返回，任一损坏按既有 500 脱敏错误返回。

### 2.1 精确响应结构

顶层只允许以下六个键：

```json
{
  "sameBody": false,
  "changedChapterCount": 1,
  "beforeChapterCount": 2,
  "afterChapterCount": 2,
  "truncated": false,
  "items": []
}
```

`items` 每项只允许 `ordinal/kind/beforeTitle/afterTitle/hunks` 五个键；`hunks` 每项只允许 `op/text` 两个键。枚举、码点预算、序号、计数一致性与 P12E-A 相同：`kind` 为 `added|removed|changed`，`op` 为 `equal|delete|insert`，`sameBody=true` 当且仅当 `items=[]`，`changedChapterCount === items.length`。

`beforeChapterCount` 和 `afterChapterCount` 分别对应两个路径修订的 `chapters` 数量；不得返回 revision ID、state version、chapter ID、项目 ID、来源、时间、原始快照、其他 13 键或异常原文。

## 3. 比较语义

1. 两条修订均复用 `get_editor_state_revision`，禁止只取元数据、绕过三重作用域或读取当前 editor-state。
2. 只抽取两个已校验快照中的 `chapters`；内部优先用两侧唯一非空字符串 `id` 配对，任一侧缺少可用唯一 ID 时按同一序号配对，重复 ID、非对象章节或无法确定配对固定失败，不猜测关系。
3. `before` 正文与 `after` 正文先以规范化换行后的完整值判断，再对展示输入应用预算；标题变化不单独制造正文差异，新增/删除章节分别生成 `added`/`removed` 项。
4. 复用 P12E-A 的标准库行差异和有界引擎；最多前 100 个实际正文差异章节进入 difflib，完整值扫描仍覆盖所有配对，防止后段差异假绿。

## 4. 有界与安全

沿用 P12E-A 固定上限：最多 100 个差异章节、单章展示正文 20,000 Unicode 码点、标题 240 码点、单章 80 个 hunk、单 hunk 2,000 码点、全响应差异文本 120,000 码点。超过上限仍由完整值决定 `sameBody`，并返回可见片段与 `truncated=true`。

服务全程只读，禁止 `add/delete/flush/commit/rollback/refresh`、写锁、审计、检查点、修订写入、HTTP 或文件写入；未预期异常固定为 `editor_state_revision_body_diff_failed`，不得泄漏 SQL、路径、异常类型、正文或 ID。

## 5. 实现白名单

Grok 只允许修改以下四个文件，不得 `git add/commit/push`：

1. `backend/app/api/schemas.py`
2. `backend/app/api/editor_state_revisions.py`
3. `backend/app/services/editor_state_revision_body_diff_service.py`
4. `backend/tests/test_p12e_revision_pair_body_diff.py`（新建）

禁止新增依赖、迁移、实体字段、前端文件、其他 E2E、CSS、浏览器存储、URL 状态、缓存、分页、搜索、恢复或任意项目历史 API。

## 6. Failure-first 与验收门

先只增加真实后端红测，证明生产路由不存在/响应不满足契约，再实现最小代码；不得用导入错误、fixture 错误、缺依赖或未启动服务冒充红测。至少覆盖：双修订 changed/added/removed、同修订一致、跨项目/跨工作空间双 ID 404、任一快照损坏固定 500、无 query/body/no-store、完整值尾章反假绿、100 章 difflib 上限和五域零写。

Grok 完成后只发送 `review_request`，报告真实红/绿数字、四文件白名单、零写证据和未做边界。Codex 随后独立运行 P12E-B 专项、P12E-A 专项、P12D/P12C 受影响回归、后端全量、`py_compile`、`git diff --check` 和白名单检查；所有命令后台静默，E2E 如后续包接入必须单 worker、零重试串行。

## 7. 交付记录

Grok 最终 review_request=`msg_d8a128763e274c3b8eb12c6e1234d456`，Codex 验收回执=`msg_f7bd19cc0dae4834b275823a90c4a6f7`。Failure-first 共 13 项失败，其中 11 项为新路由尚不存在的 HTTP 404，1 项为同正文双修订夹具 `stateVersion` 重合导致 before/after ID 相同，1 项为 AST 断言缺少 `compare_revision_bodies`；夹具修正并完成实现后 pair 专项 13 项通过。

Codex 独立通过 P12E-B/P12E-A/P12D-P12C **13/23/50 passed**，合并专项 **86 passed**，后端全量 **867 passed**；均只有 1 条既有 Starlette/httpx 弃用告警。三生产文件 `py_compile`、`git diff --check`、精确四文件白名单、空暂存区通过。实现已由 Codex 以中文提交 `5a5b08a` 并推送协作分支。

本契约不授权前端双修订选择器、分页、搜索、恢复、删除、导出、分享、缓存、跨项目历史、自动批量比较或多人协作；后续能力必须重新冻结。
