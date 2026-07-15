<!--
模块：P12B-C editor-state 延迟写入围栏实施计划
用途：把后台任务、商务修订、解析回调与 M3-D 的版本围栏拆成三个可独立审查的小批次。
对接：docs/p12b-delayed-editor-state-write-fence-contract.md；HANDOFF；Grok-Codex 消息箱。
二次开发：三批全部验收前不得开始 P12B-D；Grok 只实现和测试，Codex 独占审查、提交与推送。
-->

# P12B-C editor-state 延迟写入围栏实施计划

> **状态**：计划已冻结，待依次执行 C1、C2、C3。

## 1. 顺序与停线条件

1. **C1 任务/revise**：先抽取共用锁后版本校验原语；任务创建捕获内部版本，九类 writer 最终 CAS；商务 revise 进入商务保存队列并返回新版本。
2. **C2 callback**：在 C1 共用原语上完成个人 callback 原子 CAS；给 P8C 票据加签发版本并实现“陈旧回调零写但消费票据”。
3. **C3 M3-D**：apply/consume 请求强制 expected；后端全状态校验优先；技术主队列串行外部写并处理成功后重读/不确定结果阻断。
4. 每批必须先由 Grok 提交 `review_request`，Codex 审查和独立定向测试通过后才进入下一批。跨批失败不得用扩大白名单、跳过用例或修改旧断言掩盖。
5. 三批均完成后才跑后端与前端全量、更新中文闭环文档并提交；P12B-D 仍需另行冻结契约。

## 2. C1 执行步骤

1. 新增专项后端/前端失败测试，证明旧任务迟到覆盖、revise 无 expected 与队列旁路。
2. 在 `editor_state_service` 提供不提交的锁后 CAS 公共原语，P12B-A `upsert_editor_state` 复用或保持逐字等价的一处实现。
3. 任务创建仅为九类 writer 覆盖内部基准版本；worker 传递 expected；parse 去直接 ORM；批量章节按自己的成功响应推进。
4. 精确捕获 `EditorStateVersionConflict`，写固定脱敏 failed 任务；不得把 current version 放入 error/result。
5. revise schema 对写入 stage 强制版本，service 最终 CAS 并返回版本；API 映射固定 409。
6. 商务 hook 把 revise 放入 `saveChainRef`，在执行时读取最新 expected；成功/冲突/不确定结果执行契约中的阻断与单次重读。
7. Grok 运行专项、task/revise/editor-state 回归、lint/build 和新增 E2E，只发回执。

## 3. C2 执行步骤

1. 先写个人 callback 与 P8C stale-ticket 失败测试，包括票据消费、原子零写和旧空版本行。
2. 模型/SQLite 轻量升级只新增 `expected_state_version`；检查旧库幂等迁移和新表精确列集。
3. 签发时由服务端捕获版本；公共回调复用同一锁后校验，冲突单独提交消费，其他异常完整回滚。
4. 个人 callback 改为强制 expected 的单事务直写；成功返回新版本，陈旧固定 409。
5. disabled 页面提交前 GET 服务端版本并投稿；更新 curl 示例和 E2E，禁止版本持久化/日志。
6. Grok 运行 callback/P8C/P8D/P8E 助手受影响回归和新增 E2E，只发回执。

## 4. C3 执行步骤

1. 先写 M3-D “章节未漂移但其他 13 键漂移”与前端队列旁路失败测试。
2. apply/consume schema 强制 camelCase expected，API 映射 P12B-A 固定冲突；服务层在既有锁后先全状态、后章节规则。
3. 成功响应包含服务端新版本；保持批次快照、最近 20、一次消费、部分/零恢复原语义。
4. 技术主 hook 增加最小版本化外部写 runner，与普通/矩阵 PUT 共用 `matrixSaveChainRef`；M3-D 对话框不得再旁路。
5. runner 对成功后未重读、不确定 POST、409、非法版本全部保守阻断；唯一重读成功才恢复自动保存。
6. Grok 运行 M3-D 后端、技术 truth、矩阵和融合 E2E、lint/build，只发回执。

## 5. Codex 独立审查重点

1. 版本是否在“操作开始”绑定、在“最终写入”锁后比较；是否有 worker 开始时重捕获、冲突后拿新版本重试等假围栏。
2. 任务内部版本是否绝不出 API/SSE；批量章节是否只推进自己的成功版本，外部版本不能被吞掉。
3. revise、callback、P8C、M3-D 是否把状态写与伴随业务写放在正确原子域；冲突/异常 rollback 或票据消费例外是否精确。
4. P8C 冲突是否真的消费；中途异常是否仍可按旧契约回滚；旧空版本票据是否绝不写。
5. M3-D 是否先全状态再章节校验；是否错误删除原有 base/after 漂移安全。
6. 前端外部写是否进入现有串行链；网络“请求可能成功但响应丢失”是否阻断旧 UI 自动保存。
7. 错误是否固定、脱敏；是否泄露正文、版本、票据、任务 payload、路径或异常原文。

## 6. 独立验收命令

后端命令在 `C:\Users\Administrator\biaoshu\backend` 依次串行执行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_p12b_delayed_writer_fences.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_async_and_callback.py tests/test_local_parser_callback_tickets.py tests/test_content_fuse_applications.py tests/test_editor_state_full_version.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

前端命令在 `C:\Users\Administrator\biaoshu\frontend` 依次串行执行：

```powershell
npm run lint
npm run build
npx playwright test e2e/p12b-delayed-writer-fences.spec.ts --project=chromium --workers=1
npx playwright test e2e/local-parser-callback-ticket.spec.ts e2e/content-fuse-apply.spec.ts e2e/content-fuse-persistent-recovery.spec.ts --project=chromium --workers=1
npx playwright test --project=chromium --workers=1
```

若实现中发现契约遗漏真实受影响文件，Grok 必须先回报并由 Codex 授权修正命令/白名单，不得自行扩文件。所有 Playwright 必须 headless、workers=1，且任何时刻只运行一个进程；共享 `backend/data/biaoshu-e2e.db` 禁止并发。根目录另行执行 `git diff --check`、白名单审计和暂存后 `git diff --cached --check`。

## 7. 提交与文档闭环

计划/契约先由 Codex 中文提交并推送。C1、C2、C3 可各自形成中文实现提交，但只有全部独立验收通过后，才更新 `docs/HANDOFF-next.md`、`docs/integration-checklist.md`、路线图与本计划的真实测试计数，并由 Codex推送。Grok 不得 commit/push。
