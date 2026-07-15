<!--
模块：P12C editor-state 有限自动修订历史契约
用途：冻结独立于手动/安全检查点的最近自动修订账本、事务原语和后续接入边界。
对接：P12A 手动检查点；P12B-A/B/C/D 全状态 CAS、延迟写围栏与安全恢复。
二次开发：本文件不是公开历史 API 或任意版本库；A 包不得接入生产写入者、不得新增前端或声称自动历史已可用。
-->

# P12C editor-state 有限自动修订历史契约

> **状态**：P12C-A 已实现、独立验收并推送（冻结=`daa8c43`，实现=`226e1c1`）。
> **拆包**：P12C-A 独立账本与无提交事务原语（已完成）→ P12C-B 按事务边界逐包接入生产写入者 → P12C-C 受限浏览/恢复另行冻结。

## 1. 只读审计结论

P12A/P12B-D 的 `editor_state_checkpoints` 同时承载用户手动检查点和恢复前安全检查点，固定每项目最近 20 条。若把高频 autosave/任务写入直接追加到该表，手动检查点和安全检查点会在短时间内被淘汰，破坏已验收恢复语义。自动修订必须使用独立表、独立配额和独立服务，禁止复用现有 20 条裁剪域。

当前写入者不只 `upsert_editor_state`：任务/revise、个人 callback、P8C 票据、M3-D apply/consume 和 checkpoint restore 均有各自事务边界。一次把新表和全部写入者合包会重新打开 P12B-C/D 已关闭的锁、CAS、回滚和迟到写风险。因此 A 包只交付内部账本原语，不修改任何生产写入路径；B 包必须在 A 独立验收后另立精确白名单。

## 2. P12C-A 数据模型

新增 `EditorStateRevisionRow`，表名 `editor_state_revisions`，只含：

| 字段 | 契约 |
|---|---|
| `id` | 服务端生成 `esr_` + 32 位小写 hex；主键 |
| `workspace_id` | 当前工作空间外键，级联删除 |
| `project_id` | 当前项目外键，级联删除 |
| `snapshot_json` | `editor_state_service` 权威 13 键规范 JSON；禁止客户端投稿 |
| `state_version` | 从同一规范 JSON 计算的 `esv_` 版本 |
| `snapshot_bytes` | UTF-8 字节数，数据库约束 1～2 MiB |
| `source_kind` | 固定内部来源枚举，见下节；不得存任意字符串 |
| `created_at` | 服务端 UTC 时间；排序与 `id` 共同稳定 |

索引固定覆盖 `(workspace_id, project_id, created_at, id)`。表中不得新增用户/操作者、任务/检查点/票据/批次 ID、项目名称、路径、请求体、异常、备注、标签、IP、Cookie、CSRF、API Key 或其他来源详情。

## 3. 固定来源与配额

`source_kind` 只允许以下内部枚举，为 B 包预留，A 包测试可以使用但不得接入生产调用：

- `browser_put`
- `task`
- `revise`
- `callback`
- `local_parser`
- `content_fuse_apply`
- `content_fuse_consume`
- `checkpoint_restore`

每项目只保留最近 **10** 条自动修订，按 `created_at DESC, id DESC` 稳定裁剪。每条仍受 2 MiB 上限，因此单项目自动修订账本理论上限为 20 MiB；这与 P12A/P12B-D 的 20 条检查点独立。裁剪只能 SELECT 待保留/删除的 `id/state_version`，不得为淘汰加载 `snapshot_json`；DELETE 必须同时限定 workspace/project/id，绝不跨域。

## 4. 无提交事务原语

新增内部函数 `record_editor_state_transition(...)`，只接受调用方已在同一项目写锁/事务内取得的 `before_state`、`after_state` 和固定 `source_kind`。它必须：

1. 只用 `editor_state_service.extract_canonical_snapshot`、`canonical_snapshot_json` 和共享版本算法处理两个状态，禁止复制 13 键、序列化或哈希。
2. 校验两个输入携带的合法 `stateVersion` 与重新计算结果精确一致；非法、缺失、非规范、非有限值、超 2 MiB 或来源非法均抛固定内部错误。
3. 只读取当前项目最新一条 `id/state_version`，不得读取历史正文。
4. 若账本为空或最新版本不等于 before，先追加 before；随后仅在最新版本不等于 after 时追加 after。相邻同版本不得重复；恢复到更早版本时因其与最新不同，仍须形成新时间点。
5. 插入和裁剪只 `flush`，绝不 `commit`、`rollback`、`refresh`、查询项目、取得第二把锁或记录审计；事务成败完全由未来 B 包调用方控制。
6. 返回值只能是本轮新增数量和最终版本等内部最小结果，不返回 snapshot、项目/空间或行 ID。

A 包不允许任何生产代码调用该函数。B 包接入时，历史记录失败必须与对应 editor-state 业务写同事务回滚，禁止“正文成功但历史失败”或反向部分成功；具体调用点、旧无 expected 写入锁化和每类来源映射必须另行冻结。

## 5. 安全与隐私

自动修订包含完整标书编辑态，属于服务端敏感正文。A 包禁止新增 API、Schema、路由、前端、浏览器存储、URL、console、下载、导出、搜索、日志或审计正文；测试不得使用真实人员、项目、招标文件或密钥。内部错误使用固定 code，不拼接 snapshot、版本、项目、SQL 或异常原文。

项目删除依赖数据库外键级联清理。A 包不提供单条/批量 DELETE，不提供保留期设置，不接受客户端 source，不修改手动检查点列表、恢复裁剪或 D2 面板。

## 6. P12C-A 文件边界

只允许：

1. `backend/app/models/entities.py`
2. `backend/app/services/editor_state_revision_service.py`（新增）
3. `backend/tests/test_editor_state_revisions.py`（新增）

禁止修改 API/Schema、数据库启动补列、既有 editor-state/checkpoint 服务、任务/revise/callback/P8C/M3-D、前端、配置、依赖、锁文件和其他测试。Grok 不得 `git add/commit/push`。

## 7. 反假绿验收

专项必须真实覆盖：新表列/约束/外键/索引；精确来源枚举；首个 transition 写 before+after、同版本只写一条；连续 transition 只追加新 after；账本与 before 不连续时补 before；恢复到旧版本形成新行；相邻同版本去重；第 11 条后稳定只留最近 10 条；并列时间戳按 id 稳定；其他项目/空间零误删；项目删除级联。

还必须覆盖：13 键/版本委托共享算法；缺失/非法/带空白/不匹配版本、非规范值、NaN/Infinity、超限和非法 source 固定失败；任一步异常不 commit，调用方 rollback 后新行精确为零；原语不查询项目、不取得锁、不 refresh；最新读取与裁剪 SQL 投影不含 `snapshot_json`；返回值无 snapshot/ID/项目/空间；P12A/P12B-D 现有检查点数量和内容完全不变。

禁止只测纯函数、mock 掉真实 SQLite、以宽泛异常/状态集合放行、固定 sleep、`or True`、从客户端构造 snapshot、顺序调用冒充并发，或因 A 包存在就声称生产写入已自动留史。

## 8. 非目标与后续闸门

P12C-A 不产生用户可见功能，不接任何生产写入者，不新增历史列表/详情/恢复/删除/diff/搜索/分页，不提供任意版本、分支、合并、命名、标签、发布、审批、跨项目浏览或多人实时协作。

P12C-B 必须先审计所有已知 editor-state 写入事务，证明每个成功写入与 transition 记录同锁同事务，陈旧 CAS/业务失败/历史失败均双零写，再逐包接入。P12C-C 只有在 B 全量闭环后才能冻结最小元数据列表、按需详情和是否复用 P12B-D restore；权限、正文出域、保留配额和恢复竞态必须重新审查。

## 9. P12C-A 实现与验收记录

实现提交 `226e1c1` 新增独立 `editor_state_revisions` 表、固定 8 类内部来源、每项目最近 10 条裁剪和无提交 `record_editor_state_transition`。A 包没有生产调用、API、Schema 或前端入口，不能据此声称自动历史已可用。

Grok 首轮全量为 636 passed / 1 failed，失败来自并列时间戳测试在统一时间后继续 transition，随机 ID 稳定排序与插入顺序不等价；测试改为先完成 transition、再统一时间并只验证 `created_at DESC, id DESC`。Codex 随后发现缺任一权威键时 `extract_canonical_snapshot(...get...)` 会补 `None`，攻击者可携带匹配版本让假全状态入账；失败先测得到 28 failed / 1 passed，返修后要求 13 个权威键全部存在，同时允许 `projectId/updatedAt/responseMatrixVersion` 等服务端派生额外键。跨工作空间裁剪隔离、DELETE 行 ID 精确条件和合法 32 位夹具 ID 亦已补齐。

Codex 独立验收结果：P12C-A 专项 **67 passed**，P12A/P12B-D 受影响回归 **77 passed**，后端串行全量 **666 passed**；均只有 1 条既有 Starlette/httpx 弃用告警。`py_compile`、精确三文件白名单、工作树与暂存区 diff 检查全部通过。
