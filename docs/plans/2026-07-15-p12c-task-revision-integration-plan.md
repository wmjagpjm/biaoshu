<!--
模块：P12C-B-B1 九类任务修订账本接入实施计划
用途：限定 Grok 三文件失败先测与实现、Codex 审查验收及提交顺序。
对接：P12C-B-B 审计契约、P12B-C1 任务延迟写围栏、P12C-B-A 原子接入模式。
二次开发：本计划只接九类任务；商务 revise、callback、content-fuse、restore 均留待后包。
-->

# P12C-B-B1 九类任务修订账本接入实施计划

> **状态**：已冻结，尚未实现。
> **顺序**：冻结提交推送 → Grok 三文件 failure-first/实现/自测 → Codex 受限审查与返修 → 独立验收 → 中文实现提交推送 → 文档闭环。

## 1. 目标与范围

让九类真实 editor-state writer 任务的每次成功状态迁移，以内部固定来源 `task` 写入 P12C 独立最近 10 条修订账本。复用 B-A 已交付的 upsert 可选来源、项目写锁、锁后 before、写后 after、单 commit 和统一 rollback；不新增事务机制。

唯一生产改动是给 `task_service.py` 的 5 个写点和 `business_task_service.py` 的 4 个写点传 `revision_source_kind="task"`。批量章节保持逐章提交；任务状态、项目步骤与 editor-state 仍按既有多事务顺序执行。

## 2. Grok 实施步骤

1. 新建 `test_p12c_task_revisions.py`，先覆盖真实成功/冲突/flush 失败/commit 失败/逐章语义并运行得到红灯；保存准确失败数和首要原因。
2. 只修改两个生产服务的 9 个 upsert 调用；来源必须是服务端字面量，不从任务 payload、LLM 输出、请求体、任务类型或数据库读取。
3. 运行专项和契约指定受影响回归；报告命令、通过数、警告和完整文件清单。
4. 不提交、不推送；等待 Codex 审查，按精确反馈仅修改白名单文件。

## 3. Codex 审查重点

- 精确 9 个 writer 调用接入，非 writer 与其他生产路径保持不变。
- recorder/commit 失败是否真实回滚状态与 revision；失败后的任务终态是否仍按既有规则落库且不泄密。
- chapters 每次实际 upsert 是否一一对应修订，章间冲突是否只保留成功前缀。
- 浏览器来源仍为 `browser_put`，task 来源不会被客户端或 payload 伪造。
- 测试是否查询真实表、避免随机 ID 顺序假设、避免只 mock/静态字符串假绿。

## 4. 独立验收

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_task_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_task_cancel.py tests\test_task_sse.py tests\test_settings_and_revise.py
.\.venv\Scripts\python.exe -m pytest -q
```

此外编译两个生产文件与新测试，核对 `git diff --name-only` 仅三文件、`git diff --check`、暂存区白名单和工作区无额外产物。后端全量耗时较长，必须后台静默串行运行。

## 5. 完成条件

专项、受影响回归、后端全量、审查、编译、白名单和暂存检查全部通过后，Codex 才能以中文 Commit Message 提交并推送实现。随后更新本契约/计划、P12C 总契约、HANDOFF、路线图和联调清单，明确 B1 只覆盖九类任务；B2 revise 仍未实现。
