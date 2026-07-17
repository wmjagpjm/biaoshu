# P12F-A 修订有限保留扩容与总字节配额契约

模块：P12F-A editor-state 自动修订账本有限保留策略
用途：在不提高单项目最坏磁盘占用的前提下，为后续游标分页保留超过最近 10 条的小型修订。
对接：`editor_state_revision_service` 写入同事务裁剪；P12C-C1 默认最近 10 条只读列表。
状态：2026-07-17 冻结，等待 Grok 按六文件白名单实现；Codex 负责受限审查、独立验收、中文闭环和提交推送。

## 1. 背景与目标

当前自动修订账本固定每项目最多 10 条，且默认列表同样最多 10 条。只增加分页 API 无法读取已经被写入事务裁掉的历史，因此必须先建立明确、有限、磁盘有界的保留基础。

P12F-A 只做后端保留策略：

- 常规小快照按时间最多保留最近 **20 条**；
- 同一 workspace/project 的修订 `snapshot_bytes` 总额最多 **20 MiB**；
- 两个上限取先到者，始终保留按 `created_at DESC, id DESC` 排序的连续最新前缀；
- P12C-C1 既有默认列表仍精确只返回最近 **10 条**，响应 shape、路由、详情、恢复、对比和正文差异均不变。

因为单条既有上限是 2 MiB，总字节配额仍为 20 MiB，所以单项目最坏快照存储不高于原先 10×2 MiB；本包只让常见的小快照在同一最坏上限内保留更多时间点。

## 2. 固定常量与裁剪算法

生产常量必须精确为：

```text
MAX_REVISIONS_PER_PROJECT = 20
MAX_REVISION_BYTES_PER_PROJECT = 20 * 1024 * 1024
MAX_REVISIONS_LIST = 10
```

其中 `MAX_REVISIONS_LIST` 必须在只读历史服务中与写入保留上限解耦，禁止继续引用 `MAX_REVISIONS_PER_PROJECT`。

`_trim_revisions` 必须：

1. 只按 workspace/project 查询 `id/state_version/snapshot_bytes`，禁止加载 `snapshot_json`；
2. 以 `created_at DESC, id DESC` 排序，先完整物化并校验每行 `snapshot_bytes` 是非布尔正整数且不超过既有单条 2 MiB 上限；
3. 从最新向旧累计条数和字节；达到 20 条或加入下一条会超过 20 MiB 时，该条及所有更旧行全部进入删除集合，禁止跳过大行后保留更旧小行造成历史空洞；
4. 校验完成前不得删除；任一元数据非法必须抛既有固定 `editor_state_revision_invalid`，由调用方同一事务整体回滚；
5. DELETE 必须同时限定 workspace_id、project_id 和 ID 集合，只 `flush`，禁止 `commit/rollback/refresh`、锁、审计或跨域删除；
6. 最新单条最大 2 MiB，必然可落在 20 MiB 总额内；不得出现裁成零条的特殊旁路。

## 3. 兼容与安全边界

- 既有 `GET /api/projects/{projectId}/editor-state-revisions` 仍只返回顶层 `items`，最多 10 条，顺序和五列元数据不变；不得增加 query、cursor、total、hasMore 或正文。
- 既有详情、restore、comparison、单修订 body-diff、双修订 body-diff 路由和语义不变。
- 不恢复此前已裁掉的历史，不回填旧数据，不新增表/列/索引/迁移/依赖。
- 不新增前端、分页按钮、搜索、删除、命名、固定、导出、分享、缓存、跨项目历史或多人协作。
- 保留策略仅在后续成功写入所处事务中生效；不得启动后台清理、定时任务或全库扫描。

## 4. 六文件白名单

Grok 只允许修改：

1. `backend/app/services/editor_state_revision_service.py`
2. `backend/app/services/editor_state_revision_history_service.py`
3. `backend/tests/test_editor_state_revisions.py`
4. `backend/tests/test_p12c_revision_history_read.py`
5. `backend/tests/test_p12c_browser_put_revisions.py`
6. `backend/tests/test_p12c_revision_restore.py`

禁止修改 API schema/路由、模型、数据库、前端、E2E、依赖、配置或文档；不得新增文件；不得 `git add/commit/push`。

## 5. Failure-first 与验收门

Grok 必须先只改测试形成真实红测，再改两个生产服务。红测至少证明：

- 生产计数上限仍为 10，不能保留第 11～20 条小修订；
- 历史服务仍把默认列表上限绑定写入保留上限，无法同时满足“保留 20、默认只列 10”；
- 新总字节配额常量/连续最新前缀算法尚不存在。

最终测试必须覆盖：

- 20 条小修订保留、写入第 21 条裁最旧；默认 GET 始终只列最近 10 条；
- 通过 monkeypatch 缩小总字节配额，用真实行元数据证明按连续最新前缀裁剪、不会跳洞；生产 20 MiB 常量另作精确断言；
- SELECT 不含 `snapshot_json`，DELETE 三重作用域，另一项目/空间完全不受影响；
- 非法 `snapshot_bytes` 在任何删除前固定失败，调用方回滚后五域不变；
- browser PUT、revision restore、相邻去重、断链补点和最近顺序回归；
- `py_compile`、`git diff --check`、精确六文件、暂存区为空。

Grok 完成后只发送 `review_request`，如实报告红/绿数字、命令、资源上限证据、六文件清单与未做边界。Codex 独立运行专项、P12C 受影响回归和后端全量后才可提交。
