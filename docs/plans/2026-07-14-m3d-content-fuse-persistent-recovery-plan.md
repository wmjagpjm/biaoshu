<!--
模块：M3-D 融合写入持久恢复批次实施计划
用途：把服务端原子确认与有限一次性恢复拆成可独立审查的后端、前端受限任务。
对接：docs/m3d-content-fuse-persistent-recovery-contract.md；docs/HANDOFF-next.md；Grok-Codex 协作消息箱。
二次开发：必须遵守文件白名单；Grok 只实现和自测，Codex 独立审查、验收、提交与推送。
-->

# M3-D 融合写入持久恢复批次实施计划

> **状态**：后端、前端、独立验收、推送与中文文档闭环均已完成。
> **工作分支**：`collab/grok-code-codex-review`。
> **交付提交**：计划=`d326c7d`、后端=`6a5f61f`、前端=`b89a387`。
> **验收结果**：后端专项 34 / 回归 71 / 全量 487 passed；前端定向 23 / 全量 145 passed，lint/build/diff-check 通过。
> **执行顺序**：计划提交并推送 → 后端实现/审查/验收/提交 → 前端实现/审查/验收/提交 → 中文文档闭环。

## 1. 决策与不变量

现有浏览器防抖 PUT 与会话内撤销不能产生可信持久历史。M3-D 不补发客户端回执，也不拦截所有 editor-state 写入；只为已成功的 M3-A `content_fuse` 任务增加服务端原子应用、最多 20 批恢复快照和一次性漂移安全恢复。

不变量：任务结果是建议正文唯一权威；客户端只选任务和建议 ID；当前章节 base 必须在锁内重新验证；确认要么整批成功要么零写；恢复只覆盖仍精确等于 after 的章；一次尝试后消费；不扩成通用版本库。

## 2. 任务 1：后端受限实现

仅允许修改或新增：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/main.py`
- `backend/app/api/schemas.py`
- `backend/app/api/content_fuse_applications.py`（新建）
- `backend/app/services/content_fuse_application_service.py`（新建）
- `backend/tests/test_content_fuse_applications.py`（新建）

不得修改 `database.py`、`deps.py`、认证/CSRF 中间件、现有 `task_service`/`fuse_context_service`/`editor_state_service`、既有测试、依赖、脚本、前端或其他角色文件。

实现要求：

1. 先写失败测试；新实体严格按契约字段、CHECK、外键和复合索引，snapshot 只由服务端生成。
2. 新路由复用 `get_workspace_id`，支持 disabled 个人版与 required strict `bid_writer`；所有响应 `no-store`，错误 code/message 固定且不反射 ID、正文或异常。
3. 确认接口只接 `taskId/suggestionIds`；拒绝额外键、空值、重复、超过 5、同目标章多建议和客户端正文/base/action 等伪造字段。
4. 服务锁定当前 workspace/project 技术标后，重新读取同项目成功 `content_fuse` 任务和 editor-state；从任务结果规范化建议，以与前端完全一致的 SHA-1/码点长度、action、preview、wordCount、status 规则构造 after。
5. 任一选择不存在、无正文、base 漂移、目标缺失、零变化、快照超过 2 MiB或事务步骤失败，整批回滚；章节写入、批次创建和裁剪至 20 条同一 commit。
6. 列表固定 20、稳定倒序和最小字段。恢复锁内逐章精确对比 after，允许部分/零恢复，但章节修改和批次消费同事务；已消费和并发重复固定 409。
7. 测试必须真实检查数据库约束/索引、SQL/事务结果和并发结论；禁止 `or True`、捕获后忽略、宽泛 `in`、仅检查非空或通过应用层对象假装数据库约束。
8. 新文件和公开 API 补齐中文“模块 / 用途 / 对接 / 二次开发”注释；完成后只发送 `review_request`，报告原任务 ID、精确七文件、失败先测、锁与事务证据、快照边界、定向/回归测试、`git diff --check`、风险与未做项，不 commit/push。

Codex 审查重点：是否仍信任客户端正文；是否在锁前校验后直接写；是否调用会自行 commit 的旧 upsert 破坏原子性；失败是否留下半批；旧/商务/跨空间任务是否泄露；恢复是否覆盖漂移；裁剪是否误删其他项目；并发是否双写/双恢复。

## 3. 任务 2：前端受限实现

后端独立验收提交后才派发。仅允许修改或新增：

- `frontend/package.json`
- `frontend/src/features/technical-plan/lib/contentFuseApplications.ts`（新建）
- `frontend/src/features/technical-plan/components/ContentFuseDialog.tsx`
- `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
- `frontend/src/features/technical-plan/pages/TechnicalPlan.css`
- `frontend/e2e/content-fuse-apply.spec.ts`
- `frontend/e2e/content-fuse-persistent-recovery.spec.ts`（新建）

不得修改共享 API/认证、router/AppShell、`useTechnicalPlanEditors`、`contentFuse.ts`、M3-A worker、Playwright 配置、依赖、后端、其他工作台或角色文件。若现有 `reloadFromApi` 接口不足，必须先发 `question`，不得擅自扩白名单。实际第二轮返修由 Codex 明确放行 `useTechnicalPlanEditors.ts`，因此最终前端累计八文件；除此之外白名单未扩散。

实现要求：

1. 对话框保留 M3-A 生成与 M3-B 默认不勾选差异预览；成功任务须保存 task ID，但不得把任务结果或 ID写入浏览器存储。
2. 确认写入只 POST `taskId/suggestionIds`；点击后不得先调用 `onReplaceChapterBody` 或触发 editor-state PUT。服务成功后由父级 `reloadFromApi` 强制重读，再刷新批次；失败时本地正文不变。
3. 选择层阻止同目标章多建议，显示固定中文；不能靠隐藏按钮代替服务端校验。
4. 对话框打开按当前项目取最近批次；显示时间、章数、状态和有限 20 批声明，不展示历史正文/标题/来源。可恢复批次必须二次确认，成功后重读 editor-state 与列表并显示恢复/跳过计数。
5. 关闭/切项目立即清空批次、确认态、错误和在途代次；迟到 GET/POST 不得刷新错误项目或重新打开 UI。失败固定中文，不回显 detail、路径、项目/task/batch ID 或原始异常。
6. E2E 必须让新 API 可观测并默认拒绝未知业务/外网；断言确认前零正文写、POST body 精确、成功强制 GET、刷新后批次存在、完整/部分/零恢复、消费一次、项目切换迟到隔离、local/session/IndexedDB/Cookie/clipboard/console 零泄漏。不得用宽泛路由返回成功、吞异常、无条件真值或只断言非空。
7. 完成后只发送 `review_request`，报告原任务 ID、精确七文件、失败先测、定向与 M3-A/B/C/认证回归、lint/build/diff-check、网络/存储边界，不 commit/push。

## 4. 独立验收与提交顺序

Codex 依次完成：

1. 审查后端七文件、数据库约束/索引、锁、任务权威、原子确认、20 批裁剪和漂移恢复；运行 M3-D 定向、M3-A/editor-state/响应矩阵/认证回归及后端串行全量，中文提交并推送。
2. 再派发前端，审查七文件、零提前本地写、请求精确性、重读顺序、迟到隔离和反假绿；运行 lint、build、M3-D/M3-A/M3-B/M3-C/认证定向及单 worker 全量 E2E，中文提交并推送。
3. 更新契约、计划、路线图、联调清单和 HANDOFF，形成独立中文文档闭环提交并推送。

建议验证命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_content_fuse_applications.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_content_fuse.py tests/test_editor_state.py tests/test_response_matrix.py tests/test_auth_rbac.py
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run lint
npm run build
npm run test:e2e:fuse-persistent-recovery
npm run test:e2e:fuse-apply
npm run test:e2e:fuse
npm run test:e2e:auth-rbac
npm run test:e2e
```

所有 Playwright 命令必须等待前一个完成，禁止并行。所有 PowerShell 与 Grok 子进程后台静默运行，不启动可见窗口、浏览器或前台应用。

## 5. Grok review_request 必报项

后端必须报告原任务 ID、失败先测、精确七文件、表字段/约束/索引、锁与同事务证据、任务结果权威、base/Unicode/派生规则、20 批裁剪、完整/部分/零恢复、并发结论、定向和回归、`git diff --check`、风险与未做项。前端必须报告原任务 ID、失败先测、精确七文件、零提前本地写、请求 body/次数、成功重读、刷新后恢复、消费与迟到隔离、未知 API/外网阻断、浏览器存储与敏感信息检查、定向 E2E、lint/build/diff-check。两包均不得 commit/push。

## 6. 实际审查、验收与提交记录

1. 计划以 `d326c7d 文档：冻结M3D融合写入持久恢复计划` 独立提交并推送。
2. 后端由 Grok 实现，Codex 连续三轮退回并限定修复范围，最终七文件通过任务权威、数据库约束/索引、锁与事务、2 MiB、20 批、漂移恢复和并发审查。Codex 独立运行专项 34 passed、受影响回归 71 passed、串行全量 487 passed，提交并推送 `6a5f61f 实现M3D融合写入持久恢复后端`。
3. 前端由 Grok 实现。首轮审查收紧 POST 成功后重读失败语义、body 精确值、严格请求顺序、未知 API/外网阻断、完整存储基线、完整/部分/零恢复与迟到隔离。第二轮审查发现“探测 GET + 实际重载 GET”仍可能假成功，遂明确放行 Hook，把真实重载改为单次 `Promise<boolean>` 并在 E2E 锁死精确一次 GET。
4. Codex 独立运行 lint、build、持久恢复 5 passed、原子确认 6 passed、M3-A 1 passed、认证/RBAC 11 passed，以及单 worker 串行全量 E2E 145 passed；`git diff --check` 通过。前端以 `b89a387 实现M3D融合写入持久恢复前端` 提交并推送。
5. Grok 全程未执行 git add/commit/push；所有中文提交、代理推送、远端一致性核验和本文档闭环由 Codex 完成。所有 PowerShell、Grok 和 Playwright 进程保持后台静默；E2E 未并行。
