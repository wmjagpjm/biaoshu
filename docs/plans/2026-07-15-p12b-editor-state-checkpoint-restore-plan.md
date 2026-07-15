<!--
模块：P12B-D editor-state 检查点安全恢复实施计划
用途：把后端原子恢复与双工作区显式入口拆成两个 Grok 受限实现包。
对接：docs/p12b-editor-state-checkpoint-restore-contract.md；P12A；P12B-A/B/C；Grok-Codex 消息箱。
二次开发：Grok 只实现和自测，不得提交推送；Codex 独立审查、验收、中文文档闭环与协作分支推送。
-->

# P12B-D editor-state 检查点安全恢复实施计划

> **状态**：契约已冻结，D1/D2 均未实现。
> **顺序**：冻结提交推送 → D1 后端 → Codex 审查/验收/提交 → D2 前端 → Codex 审查/验收/提交 → 文档闭环。

## 1. 只读审计结论

P12A 的创建服务自行 commit，不能作为“恢复前安全检查点”的嵌套调用；editor-state 13 键还跨越多个 ORM 列和规范化规则，恢复不能在 checkpoint 服务里复制写回算法。P12B-C 已把所有已知延迟写入收进锁后 CAS，双工作区也已有串行链、执行时 expected、阻断和单次重载原语，因此 D 包只需补齐原子 restore 和显式 UI，不重新实现 A/B/C。

恢复跨后端事务、两套前端 hook 和破坏性确认 UI，单包风险过大，固定拆成 D1/D2。未通过 D1 独立验收前禁止开始 D2。

## 2. D1 Grok 后端任务

白名单精确五文件：

1. `backend/app/api/schemas.py`
2. `backend/app/api/editor_state_checkpoints.py`
3. `backend/app/services/editor_state_checkpoint_service.py`
4. `backend/app/services/editor_state_service.py`
5. `backend/tests/test_editor_state_checkpoint_restore.py`（新增）

实施顺序：

1. 先新增恢复专项测试，记录真实 failure-first；覆盖请求 Schema、权限、CAS、完整恢复、安全检查点、损坏快照、20 条裁剪、异常回滚和并发。
2. 在 schemas 增加只接 camelCase expected 的严格请求模型，以及最小恢复响应模型。
3. 在 editor_state_service 增加一个无锁、无查询、无 commit 的已持锁规范快照写回原语；复用既有序列化、analysis/business/matrix 规范化和 `_state_from_row`，禁止暴露第二套哈希。
4. 在 checkpoint 服务编排一次锁后 CAS、目标三重作用域读取/严格验证、当前安全快照插入、目标写回、结果版本复核、保护安全记录的最近 20 条裁剪和一次 commit。
5. 新增 POST restore 路由，复用既有 workspace/CSRF/角色语义、固定 409 与 checkpoint 错误、全响应 `no-store`。
6. 运行专项、P12A/P12B-A/C 受影响回归、后端串行全量、语法与 diff 检查；只发 `review_request`，不得 `git add/commit/push`。

禁止改白名单外文件，禁止前端、实体/迁移、自动检查点、删除/下载/详情扩展、客户端 snapshot、force、重试或文档。

## 3. D1 Codex 审查重点

1. 是否误用自行 commit 的 P12A create/upsert；锁、目标校验、安全插入、写回、裁剪、commit 是否确实一个回滚域。
2. 当前状态是否在锁后抽取；target 是否以 id/workspace/project 三重 SQL 限定并复用严格验证。
3. 写回是否完整覆盖 13 键且只在共享 service 映射；写回后版本是否重新计算并等于目标版本。
4. 同内容恢复是否仍建安全检查点；安全检查点是否可能被并列时间戳裁掉；目标最旧时是否仍保持最多 20 条。
5. 409 是否在任何插入前发生；损坏/超限/异常/commit 失败是否 editor-state 和检查点双零写。
6. 响应是否提交前构造、提交后零 refresh/GET；固定错误是否无正文/ID/SQL/异常泄漏。

## 4. D1 独立验收命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoint_restore.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_editor_state_full_version.py tests\test_p12b_delayed_writer_fences.py tests\test_content_fuse_applications.py
.\.venv\Scripts\python.exe -m pytest -q
```

另跑被修改生产文件 `py_compile`、仓库根 `git diff --check`、白名单核对与暂存后 `git diff --cached --check`。全部 PowerShell 后台静默，不打开浏览器或可见终端。

## 5. D2 Grok 前端任务

白名单精确九文件：

1. `frontend/src/features/editor-state-checkpoints/editorStateCheckpointApi.ts`（新增）
2. `frontend/src/features/editor-state-checkpoints/EditorStateCheckpointPanel.tsx`（新增）
3. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
4. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
5. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
6. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
7. `frontend/e2e/editor-state-checkpoint-restore.spec.ts`（新增）
8. `frontend/e2e/technical-editor-state-truth.spec.ts`（仅路由桩/断言机械对齐）
9. `frontend/e2e/business-editor-state-truth.spec.ts`（仅路由桩/断言机械对齐）

实施顺序：

1. 先写技术/商务逐模式 E2E，证明确认前零 restore、PUT→create/restore 严格顺序、expected 串链、唯一 editor-state GET、不确定响应阻断与迟到隔离。
2. 新增最小 API 模块，仅封装元数据 list、空对象 create、expected restore；不封装详情正文。
3. 把两 hook 的即时保存复用既有普通保存执行器；创建检查点先强制保存最新 UI，再空对象 POST 并核对版本。禁止复制一套状态 body 或绕过矩阵/全状态冲突规则。
4. 技术 hook 复用并泛化既有 `runVersionedExternalWrite` 注释/用途；商务 hook 增加同等 restore 队列语义。恢复成功清 timer、作废旧 epoch、唯一 GET；不确定失败固定阻断且零重试。
5. 新增共用折叠面板，元数据最小展示、内联二次确认、固定中文状态；两页面只负责接入 hook 回调。
6. 运行新 E2E、双 truth、P12B-C3/M3-D/矩阵受影响回归、单 worker 串行全量、lint/build/diff；只发 review_request，不得提交推送。

禁止新增 CSS/依赖、修改后端/共享 API 基础设施/M3-D、读取详情 snapshot、显示 ID/version、浏览器持久化、console、轮询、自动历史或外网。

## 6. D2 Codex 审查重点

1. 创建是否真的先强制保存最新 UI，而不是只等尚未入队的 600ms timer；技术矩阵/body 是否复用既有保存原语。
2. create/restore 是否与普通 PUT 同链；expected 是否执行时读取；恢复前旧 timer/队列/飞行中回调是否被 epoch 隔离。
3. 业务 POST 成功与唯一 GET 失败是否正确区分；是否出现成功后重试或旧本地带新版本 PUT。
4. 409/abort/非法响应是否保留本地并阻断；项目 A→B、折叠和双击是否污染或重复请求。
5. 面板是否只读元数据、二次确认、固定脱敏文案；是否泄漏 checkpointId/stateVersion/正文到 DOM、URL、存储、console。

## 7. D2 独立验收命令

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run test:e2e -- --workers=1 e2e/editor-state-checkpoint-restore.spec.ts
npm run test:e2e -- --workers=1 e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts e2e/content-fuse-apply.spec.ts e2e/content-fuse-persistent-recovery.spec.ts e2e/response-matrix-conflict.spec.ts
npm run lint
npm run build
npm run test:e2e -- --workers=1
```

Chromium 必须 headless、`workers=1`、`retries=0`，共享 SQLite 下禁止并行。另跑仓库根 diff/白名单/暂存检查。

## 8. 提交与闭环

D1、D2 各自验收通过后由 Codex 单独中文提交并推送协作分支。最后更新本契约/计划、`docs/HANDOFF-next.md`、`docs/integration-checklist.md` 和路线图，写入真实提交、Grok 消息 ID、返修原因、专项/全量数字和遗留边界。P12B-D 完成不代表自动全写入历史、任意版本浏览/回滚或多人协作完成，长期目标继续 active。
