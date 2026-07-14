<!--
模块：M3-D 融合写入持久恢复批次契约
用途：冻结技术标已生成融合建议的服务端原子确认、有限持久恢复批次和一次性漂移安全恢复边界。
对接：docs/plans/2026-07-14-m3d-content-fuse-persistent-recovery-plan.md；M3-A content_fuse 任务结果；M3-B 差异确认；M3-C 会话内撤销。
二次开发：本包不是通用版本库或多人协同；不得接受客户端正文作为权威建议，不得绕过任务、项目、工作空间、章节 base 或当前状态校验。
-->

# M3-D 融合写入持久恢复批次契约

> **状态**：方案已冻结，等待后端受限实现；前端必须在后端独立验收提交后另行派发。
> **工作分支**：`collab/grok-code-codex-review`。
> **验收起点**：后端串行全量 453 passed；前端 lint/build 通过、单 worker 串行全量 E2E 140 passed。

## 1. 审计结论与方案选择

M3-B/M3-C 当前在浏览器内逐章调用 `replaceChapterBody`，再由编辑器防抖 PUT 整份 `chapters`；最近批次快照只存在对话框内存。关闭、刷新或切项目后无法恢复，这是已冻结语义。若在现有 PUT 成功后再由浏览器补发历史回执，会出现“正文已写但回执丢失”，也会迫使服务端信任客户端提交的 before/after 正文，不能形成可靠恢复依据。

M3-D 因此选择独立服务端原子应用接口：客户端只提交成功 `content_fuse` 任务 ID 和用户勾选的建议 ID；服务端从任务 `result_json` 重新取得建议，锁定项目后核验当前章节 base，在同一事务内更新章节并写有限恢复快照。恢复也由服务端锁定当前项目，只有标题、正文、状态仍精确等于该批次 after 的章节才恢复 before，漂移章节跳过。拒绝“前端补记回执”和“给通用 editor-state PUT 自动建全量版本”两种方案。

本包只解决融合建议确认后的跨刷新恢复，不替代 M3-A 只读生成，不把所有编辑操作升级为版本库，也不提供历史正文浏览、任意版本回滚或多人合并。

## 2. 最小持久模型与保留规则

新增 `content_fuse_application_batches`，字段只允许：

| 字段 | 规则 |
|---|---|
| `id` | 服务端生成 `cfab_` 不透明主键 |
| `workspace_id` | 当前已验证工作空间，外键级联删除并索引 |
| `project_id` | 当前已验证技术标项目，外键级联删除并索引 |
| `task_id` | 同项目成功 `content_fuse` 任务 ID，仅作服务端追溯，不向列表返回 |
| `snapshot_json` | 服务端生成的批次快照；只含建议 ID 与每章 chapterId/title/beforeBody/beforeStatus/afterBody/afterStatus |
| `state` | 仅 `active|consumed`，数据库 CHECK |
| `created_at` | 服务端 UTC 时间，索引 |
| `consumed_at` | 一次恢复尝试后的服务端 UTC 时间，可空 |

建议复合索引 `(workspace_id, project_id, created_at)`。快照不得保存模板/卡片全文、prompt、模型原始响应、reason、sourceRefs、用户身份、Cookie、CSRF、API Key、工作空间/项目名称或其他 editor-state 字段。单批最多 5 章，序列化快照不得超过 2 MiB；超限整批拒绝且零写入。

每项目只保留最近 20 批。新批次同事务插入后删除更旧批次；页面必须写明这是有限恢复窗口，不是完整历史。项目删除级联清理；不提供 DELETE、导出、保留期配置或跨项目查询。

## 3. 原子确认写入 API

唯一确认接口：`POST /api/projects/{projectId}/content-fuse-applications`，请求只含：

- `taskId`：不透明任务 ID；
- `suggestionIds`：按用户确认顺序排列，1–5 个、非空、去重；同一目标章节最多一个建议。

权限沿用既有 `get_workspace_id`：disabled 保持个人版兼容；required 必须是当前活动工作空间的 `bid_writer`，其他角色与跨空间沿用既有固定拒绝；required 写请求继续由现有中间件校验 CSRF。项目必须是当前空间 `kind=technical`。任务必须属于同一项目、类型精确为 `content_fuse`、状态精确为 `success` 且结果结构合法；不存在、跨项目、跨空间、错误类型或状态统一 `404 content_fuse_task_not_found`，不反射路径或任务 ID。

服务端必须以任务结果为唯一建议正文来源，不接受客户端 title、base、action、proposedMarkdown、before/after 或 sourceRefs。锁定项目后重新读取 editor-state；每个建议必须存在、正文非空、action 为既有规范值，目标章存在，且当前章 title(trim)、正文 SHA-1 前 20 hex 的 `bh_` 哈希、Unicode 码点长度与任务 base 精确一致。任一建议非法、重复目标、base 漂移或应用后无实际变化，整批 `409 content_fuse_apply_conflict`，不得部分写入、不得创建批次。

`expand` 以当前正文 + 两个换行 + 建议正文构造 after；`merge|rewrite|merge_suggest` 替换为建议正文。服务端同时派生与现有前端一致的字段：preview 依次去 Markdown 行首标题符、把 ``|>*`_-`` 换为空格、折叠空白、trim 后按 UTF-16 code unit 截前 96；wordCount 为移除空白后的 UTF-16 code unit 数；非空 after 状态为 `needs_review`。章节更新、批次快照、20 批裁剪必须同一事务；任一步失败全部回滚。成功响应只含 `batchId/appliedChapterCount/createdAt`，固定 `Cache-Control: no-store`。

## 4. 有限列表与一次性恢复 API

- `GET /api/projects/{projectId}/content-fuse-applications`：固定最近 20 条，按 `created_at DESC, id DESC`；顶层只含 `items`，每项只含 `batchId/chapterCount/state/createdAt/consumedAt`。不返回 task/suggestion/chapter/模板/卡片 ID、标题、正文、before/after、来源或人员。
- `POST /api/projects/{projectId}/content-fuse-applications/{batchId}/consume`：对一个 `active` 批次执行一次恢复尝试；跨项目/空间/不存在统一 `404 content_fuse_application_not_found`，已消费固定 `409 content_fuse_application_consumed`。

恢复接口锁定项目并重新读取 chapters。逐章只有当前 chapterId 存在且 title、body、status 精确等于快照 after 时才恢复 before，同时重新派生 preview/wordCount；其他章计为 skipped，绝不覆盖漂移内容。允许部分恢复；无论恢复 0、部分或全部，都把批次改为 `consumed` 并写 `consumed_at`，防止重复尝试。章节恢复与消费状态必须同一事务。成功只返回 `restoredChapterCount/skippedChapterCount/consumedAt`，固定 `no-store`。

列表和恢复均只允许当前空间技术标项目；不存在、商务标、跨空间统一 `404 project_not_found`。服务端错误使用固定 code/message，不返回 SQL、异常原文、快照、当前正文或冲突章节详情。

## 5. 前端边界

复用既有技术标 `ContentFuseDialog`，不新增路由或导航。生成建议后，确认写入改为调用原子 POST，成功后强制重读 editor-state 与批次列表；不得再先改本地正文或依赖防抖 PUT 建批次。服务端失败时正文保持不变，页面使用固定中文错误。

对话框打开时按当前项目读取一次有限批次列表，显示时间、章数、`可恢复/已消费` 和“最多保留最近 20 批，不是完整版本历史”。点击某个可恢复批次后明确二次确认，成功后强制重读 editor-state 与列表，显示恢复/跳过计数。项目切换或关闭立即清空旧列表、确认态和错误；迟到响应不得覆盖新项目或已关闭对话框。

状态只在组件内存；禁止 URL 参数、local/session storage、IndexedDB、Cookie、剪贴板、console、下载、轮询、计时器或外网。不得请求模板/卡片详情以展示历史，不得把响应正文、项目/任务/批次 ID写入错误文案。M3-A 生成、卡片插入、响应矩阵与其他编辑功能保持兼容。

## 6. 明确非目标

- 不做所有 editor-state 写入的通用版本历史、时间线、命名版本、分支、比较、审批、发布或多人协同；
- 不浏览、导出或搜索历史正文，不按模板/卡片/人员筛选，不跨项目/空间汇总；
- 不恢复 outline、facts、guidance、analysis、responseMatrix、parsedMarkdown 或商务标字段；
- 不信任客户端正文，不修改 M3-A 模型 prompt/配额/结果，不回填 M3-B/M3-C 旧批次；
- 不引入 Alembic、依赖、后台任务、WebSocket、外网、生产部署或其他角色能力。

## 7. 验收底线

后端至少覆盖：权限/CSRF、技术标/空间/任务隔离、客户端伪造字段拒绝、任务结果权威、选择配额/去重/同章唯一、锁后 base 校验、Unicode 哈希与长度、四 action、preview/wordCount/status、零变化冲突、全批原子回滚、2 MiB 上限、固定 20 批裁剪、列表最小投影/no-store、完整/部分/零恢复、漂移不覆盖、一次消费、并发双确认/双恢复至多一次成功，以及异常回滚。前端至少覆盖原子确认零提前本地写、成功强制重读、刷新后批次仍在、完整/部分/零恢复、消费后禁用、固定错误、项目切换/关闭迟到隔离、最近 20 提示、网络白名单和零浏览器存储；M3-A/M3-B/M3-C 与认证 E2E 必须串行回归。
