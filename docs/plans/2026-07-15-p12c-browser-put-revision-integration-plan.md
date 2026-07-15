<!--
模块：P12C-B-A 浏览器 PUT 修订账本原子接入实施计划
用途：把首个生产写入者限定为服务、路由和一个独立专项测试文件。
对接：docs/p12c-browser-put-revision-integration-contract.md；P12C-A 账本原语；P12B-A/B 浏览器 CAS。
二次开发：按失败先测、受限实现、独立审查和全量回归推进；禁止 Grok 提交推送或扩大到其他写入者。
-->

# P12C-B-A 浏览器 PUT 修订账本原子接入实施计划

> **状态**：已冻结，尚未实现。
> **顺序**：冻结提交推送 → Grok 三文件失败先测/实现/自测 → Codex 受限安全审查与返修 → 后端独立验收 → 中文提交推送 → 文档闭环。

## 1. 实施目标

只把公开浏览器 `PUT /api/projects/{project_id}/editor-state` 接入 P12C-A 账本，内部来源固定为 `browser_put`。成功写入和 revision 必须复用同一项目锁、同一 Session、同一事务与唯一 commit；任一冲突或 revision 失败必须双零写。其他 `upsert_editor_state` 调用者保持不记录。

## 2. Grok 精确任务

白名单仅三文件：

1. `backend/app/services/editor_state_service.py`
2. `backend/app/api/projects.py`
3. `backend/tests/test_p12c_browser_put_revisions.py`（新增）

实施顺序：

1. 先新增真实 API/SQLite 失败测试，记录尚无 `browser_put` 生产 revision 的失败；不得先改实现再补 failure-first。
2. 给 `upsert_editor_state` 增加默认 `None` 的内部来源参数；来源存在时强制复用现有项目写锁，三个版本分支都得到锁后同一 row 的 `current_state`。
3. 修正锁分支结构：只有真实矩阵版本写才比较矩阵版本；普通无 expected 浏览器 PUT 不能因进入来源锁而假 409。
4. 在 commit 前构造 after 后局部导入 P12C-A recorder 并调用；复用现有 rollback，禁止循环导入、二次读取和第二次 commit。
5. 路由只传字面量 `browser_put`；禁止新增 Schema 字段或读取客户端来源。
6. 补齐首次/连续/去重、三类版本路径、冲突双零、跨域、注入失败回滚、内部调用不记录、来源伪造无效和无额外读取/锁/commit 测试。
7. 运行专项、P12C-A/全状态/矩阵/基础 PUT 受影响回归、后端全量、`py_compile` 与 diff 白名单检查；只通过消息箱发送 `review_request`，不得提交或推送。

## 3. Codex 审查重点

1. 是否把来源默认成 `browser_put`，导致任务/revise/商务任务被误记。
2. 无 expected 浏览器 PUT 是否真正取得现有项目写锁；before 是否来自锁后同一 ORM 行。
3. 为来源加锁后，是否错误执行 `None != current_matrix_version` 并制造假 409。
4. 是否因 P12C-A 顶层依赖产生循环导入；是否复制 13 键、JSON、哈希、插入或裁剪逻辑。
5. recorder 是否严格位于 response 构造之后、唯一 commit 之前；异常是否进入同一 rollback。
6. 是否出现先提交正文后记历史、吞历史异常、二次 GET/db.get/refresh、第二把锁或新 Session。
7. 来源是否只能由路由字面量给出；客户端额外键是否可能伪造来源或进入响应。
8. 测试是否真实证明已 flush 后失败也双零写，并用新 Session 排除同一身份映射假绿。
9. 白名单外是否有 Schema、P12C-A、其他写入者、前端、配置、依赖或文档改动。

## 4. 独立验收命令

后端目录，全部串行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_browser_put_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_response_matrix.py tests\test_editor_state.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\services\editor_state_service.py app\api\projects.py tests\test_p12c_browser_put_revisions.py
```

仓库根额外执行 `git diff --check`、精确三文件白名单核对和暂存后 `git diff --cached --check`。所有 PowerShell、pytest 与 Grok 进程后台静默执行；不得启动浏览器或可见终端。

## 5. 完成条件与下一包

只有专项、受影响回归、后端全量、安全审查、编译、白名单和暂存检查全部通过，Codex 才能中文提交并推送。随后更新 P12C 契约/计划、HANDOFF、路线图和联调清单，明确“目前只自动记录浏览器 PUT”。

下一包只能重新审计任务/revise 的锁和事务边界后再冻结；不得直接传 `task/revise` 来源、接 callback/content-fuse/restore，或开始历史浏览与恢复。
