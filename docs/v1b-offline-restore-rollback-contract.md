# V1-B 离线恢复与回滚演练契约

> 状态：已实现并通过 Codex 独立验收，代码提交=`20a4a60`
> 日期：2026-07-21
> 实施基线：`6382342`（V1-A 代码与文档闭环）
> 契约冻结：`40d1852`
> 目标版本：V1 本机/可信内网可实际使用版
> 前置实现：V1-A=`5b4ad39`

## 1. 目标与诚实语义

V1-B 为 Windows 本机日用增加显式离线恢复入口。用户先通过 V1-A 受控停机，再选择一个受支持的完整备份；恢复工具在任何业务路径变更前完成格式、数据兼容、文件哈希和 SQLite 完整性校验，自动创建恢复前备份点，随后用同卷 staging、持久 journal 和可逆根切换完成恢复。

“恢复成功”只表示以下事实同时成立：

1. 备份格式和独立数据兼容版本均精确匹配；
2. 数据库及所有纳入快照的数据根与备份权威内容一致；
3. 未纳入备份的语义模型缓存保持原样；
4. 恢复后的数据库 `PRAGMA integrity_check=ok`；
5. journal 已进入提交态，且恢复前备份点可作为普通 v2 备份再次恢复；
6. 任何提交前故障均已自动恢复为操作前状态，不能留下“半新半旧”并声称成功。

本包不是文件合并、在线热恢复、跨版本迁移或最近备份自动选择器。

## 2. 只读审计与问题确认

只读架构审计：Grok A task=`msg_2cd78be675a64a879feece150036437c`、review_request=`msg_af63265fc30b48bb8e6473af1a2ba3c4`。

只读测试审计：Grok B task=`msg_b31d92a1ef2e4222b818a5ec1a8c1e09`、review_request=`msg_64563cdc769b420dbb2aa2710d1cd40b`。

Codex 独立发现并向双方确认两项设计风险：

1. `biaoshu-offline-backup-v1` 只是 manifest 格式版本，不能证明 SQLite 和文件布局与当前应用兼容；`git_head` 可空且受纯文档提交影响，也不能充当兼容闸。
2. v1 只记录文件，没有根状态。缺少可选根时无法区分不存在、空目录和未纳入；恢复时保留 live 会形成旧数据库与新文件树混合态。

Grok A question=`msg_f7167c352d0d433c8ecf14c705b5883a`、确认=`msg_0423e8b43c5846948c4da28ce0192635`；Grok B question=`msg_83ff1392f5e64c769d83811fd2b14ef0`、确认=`msg_058bfb59096643bf9831e91779424a46`。双方均独立确认风险存在，并同意本契约的 v2、独立数据兼容版本和根状态方向；确认阶段零文件修改、零 Git 写入、零真实业务数据读取。

V1-A 验收明确未创建真实业务备份，因此不存在必须自动覆盖恢复的存量 v1 业务包。v1 历史实现和验收文档完整保留，但 V1-B 固定拒绝用 v1 自动覆盖 live。

## 3. 可恢复备份 v2

V1-B 把新写出的备份升级为：

- `BACKUP_SCHEMA_VERSION = "biaoshu-offline-backup-v2"`：仅表示备份包和 manifest 格式；
- `DATA_COMPATIBILITY_VERSION = "biaoshu-data-v1"`：表示当前冻结的日用 SQLite 语义和文件根布局；
- `git_head`：只作审计信息，不参与恢复放行；
- v1 或缺少兼容标识的包：固定拒绝自动恢复，不提供 `--force`。

`manifest.json` 顶层必须精确六键：

```json
{
  "schema_version": "biaoshu-offline-backup-v2",
  "data_compatibility_version": "biaoshu-data-v1",
  "created_at_utc": "2026-07-21T00:00:00Z",
  "git_head": null,
  "roots": {},
  "files": []
}
```

`roots` 必须精确包含六个键：`db`、`uploads`、`knowledge`、`knowledge_cards`、`legacy_uploads`、`semantic_models`。每个值必须精确三键：

```json
{
  "state": "present",
  "file_count": 1,
  "total_bytes": 123
}
```

根状态固定为：

- `present`：存在至少一个纳入文件；`file_count >= 1`，`total_bytes` 与 `files` 聚合精确相等；
- `empty`：源目录存在但没有纳入文件；计数均为 0；恢复后该根存在且为空；
- `absent`：源路径不存在；计数均为 0；恢复后该根不存在；
- `not_included`：仅允许 `semantic_models` 使用，表示备份时未启用模型缓存选项；恢复时唯一允许保留 live 原样。

`db` 只能是 `present`，且必须只有 `db/biaoshu.db` 一项。其它 canonical 根只能为 `present|empty|absent`；`semantic_models` 可额外为 `not_included`。状态、文件条目、计数、字节数任一不一致即固定失败。

`files` 每项仍精确四键：`logical_root`、`relative_path`、`size_bytes`、`sha256`。相对路径必须是规范 POSIX 相对路径；拒绝空值、`.`、`..`、反斜杠、盘符、UNC、重复项、前后空白、NUL、控制字符和大小写碰撞。大小只能是非 bool 的非负整数，SHA-256 只能是 64 位小写十六进制。

备份目录除 `manifest.json` 和 `files` 声明的普通文件/必要目录外不得有未知文件、符号链接、junction 或其它 reparse point。manifest 和输出继续执行敏感字段门。

## 4. 固定数据根与默认布局

恢复只支持当前 V1 默认日用布局，目标绝对锚定仓库根：

| 逻辑根 | live 目标 | 恢复动作 |
| --- | --- | --- |
| `db` | `backend/data/biaoshu.db` | 必须替换 |
| `uploads` | `backend/uploads` | 按状态替换、置空或移除 |
| `knowledge` | `backend/data/knowledge` | 按状态替换、置空或移除 |
| `knowledge_cards` | `backend/data/knowledge_cards` | 按状态替换、置空或移除 |
| `legacy_uploads` | 仓库根 `uploads` | 独立替换、置空或移除，禁止混入 canonical uploads |
| `semantic_models` | `backend/data/semantic-models` | `not_included` 保留；其它状态按快照处理 |

恢复不得依赖进程 cwd。环境变量或 `backend/.env` 中存在非默认 `DATABASE_URL`/`UPLOAD_DIR` 时必须固定拒绝；只允许读取和比较这两个键，禁止打印、记录或复制其值，也不得读取其它密钥值。`.env`、其它 `backend/data` 文件、`.venv`、`node_modules`、Git、日志和消息箱均不在恢复目标内，保持原样。

可恢复 v2 备份不得静默跳过 canonical 数据根内的未知、被排除或非普通文件；遇到此类文件应让备份失败，避免恢复前备份点无法保护随后会被整根替换的内容。

## 5. 显式入口与停机门

新增入口：

- `Restore-Biaoshu.bat <备份目录>`；
- `Restore-Biaoshu.ps1 -BackupDir <绝对目录>`；
- Python 标准库核心 `tools/v1-ops/biaoshu_restore.py`。

入口要求：

1. 不提供默认“最近备份”，备份目录必须显式给出且位于仓库外；
2. PowerShell 在任何写入前要求用户输入精确中文 `恢复`，其它输入均取消且零写入；
3. Restore 不自动调用 Stop，不自动终止任何进程；
4. 必须复用 V1-A 的 `assert_services_stopped` 语义，8000/5173 任一监听即失败；
5. Python CLI 的明确应用开关仅供 PS 包装在用户确认后传入；不提供跳过端口、版本、哈希、SQLite、journal、回滚或路径检查的参数；
6. 根 bat 只转发固定 PS1；PS1 必须 UTF-8 BOM、兼容 Windows PowerShell 5.1 和含空格/中文路径。

## 6. 恢复事务

默认恢复前备份根沿用仓库同级 `biaoshu-backups`；恢复工作根固定为仓库同级 `biaoshu-restore-work`。二者必须位于仓库外、无 reparse，工作根必须与 live 仓库同卷。所有 journal 路径只记录逻辑名和相对工作名，不记录绝对路径。

固定顺序：

1. **PRECHECK**：验证仓库、默认布局、服务停止、备份 v2/兼容版本、严格 manifest、根状态、物理文件集合、大小/哈希、备份 DB 完整性、源/目标/祖先 reparse、工作卷和空间；此阶段零 live 写入。
2. **PRE_BACKUP**：调用升级后的 `create_offline_backup` 为当前 live 创建完整 v2 恢复前备份；若目标备份会处理 semantic 根，恢复前备份也必须纳入 semantic。失败则零 live 写入。
3. **LOCK/JOURNAL**：用独占创建获得单实例锁，原子写入 `biaoshu-offline-restore-journal-v1` journal；已有有效未完成 journal 时先自动回滚，损坏 journal 时固定拒绝并保留现场。
4. **STAGE**：把备份权威内容复制到同卷工作根 staging；逐文件重新核对大小/哈希，DB 再做完整性检查。`empty` 创建空 staging 根，`absent` 不创建新根，`not_included` 跳过。
5. **CUTOVER**：按固定顺序 `db → uploads → knowledge → knowledge_cards → legacy_uploads → semantic_models`。每根先把现有 live 同卷改名到 hold，再按状态安装 staging 或保持缺失；每个危险动作前写 intent，动作后写 result，journal 使用临时文件、fsync 和原子替换。
6. **VERIFY**：对最终 live 重新核对完整文件集合、大小/哈希、根状态、无 reparse 和 DB 完整性；`not_included` 根必须与操作前字节/文件集合一致。
7. **COMMIT**：先持久写入提交态，再清理 hold/staging；恢复前 v2 备份永久保留，用户需要回滚时把它作为普通备份再次执行 Restore。

任何提交前异常都必须进入 **ROLLING_BACK**，按 journal 逆序恢复所有 hold。回滚证据必须是全根文件集合、大小/哈希和 DB 完整性回到操作前状态，不得只看异常或目录存在。

提交后清理失败不得回滚已验证的新数据。journal 保持 `COMMITTED_CLEANUP_PENDING` 并返回固定非零“恢复完成但清理未完成”；下次调用只能先完成清理。清理完成后删除锁和工作目录；恢复前备份不删除。

## 7. 崩溃重入与 fail-closed

- 无 journal：可开始新恢复；
- `PRECHECK/PRE_BACKUP/STAGE`：清理 staging 后回到未变更 live；
- `CUTOVER/VERIFY/ROLLING_BACK`：依据每根 intent/result 与 live/hold/stage 实际存在性自动判定并回滚；
- `COMMITTED/CLEANUP_PENDING`：只清理 hold/stage，不把已提交新数据回滚；
- journal 非法、根状态无法唯一判定、hold 缺失或回滚自身失败：固定失败、保留全部现场、阻止任何新恢复；不提供危险 clear/force 参数。

测试故障注入只能通过 Python 函数参数/内部 hook 进入，不能暴露给 bat/PS/生产 CLI。

## 8. Python 公开接口

V1-B 冻结：

### `biaoshu_backup.py`

- `BACKUP_SCHEMA_VERSION = "biaoshu-offline-backup-v2"`
- `DATA_COMPATIBILITY_VERSION = "biaoshu-data-v1"`
- 保留 V1-A 已冻结的 `BackupError`、`build_source_plan`、`assert_services_stopped`、`create_offline_backup`、`main` 名称和参数兼容；新写出的包改为严格 v2。

### `biaoshu_restore.py`

- `RESTORE_SCHEMA_VERSION = "biaoshu-offline-backup-v2"`
- `RESTORE_DATA_COMPATIBILITY_VERSION = "biaoshu-data-v1"`
- `RESTORE_JOURNAL_SCHEMA_VERSION = "biaoshu-offline-restore-journal-v1"`
- `RestoreError`
- `load_and_validate_backup(backup_dir)`
- `build_restore_plan(repo_root, validated_backup)`
- `recover_incomplete_restore(repo_root, work_root=None, service_probe=None, fault_injector=None)`
- `restore_offline_backup(repo_root, backup_dir, pre_restore_destination_root=None, work_root=None, service_probe=None, now=None, git_head=None, fault_injector=None)`
- `main(argv=None)`

`service_probe/now/git_head/fault_injector` 仅供临时假仓测试；根入口不得转发这些注入项。公开返回值可以包含最终恢复前备份 `Path` 和固定逻辑根摘要，但不得包含文件正文、密钥或 journal 内部绝对路径。

## 9. 严格文件白名单

### Grok A：生产

1. `Restore-Biaoshu.bat`（新建）
2. `tools/v1-ops/Restore-Biaoshu.ps1`（新建，UTF-8 BOM）
3. `tools/v1-ops/biaoshu_restore.py`（新建）
4. `tools/v1-ops/biaoshu_backup.py`（仅升级可恢复 v2 manifest/根状态/兼容常量，并保持 V1-A 接口）

### Grok B：测试

1. `tools/v1-ops/test_biaoshu_backup.py`（只把既有 60 项升级为 v2 契约并增加 v2 反假绿）
2. `tools/v1-ops/test_biaoshu_restore.py`（新建）

禁止修改 `backend/app`、前端、依赖、配置、数据库模式、启动/停机入口、V1-A 两个 bat/两个 PS 包装、真实数据目录和其它测试。Grok 不得暂存、提交或推送。

## 10. failure-first 与独立验收

Grok B 先在冻结基线的新独立 worktree 编写两文件测试并串行运行。真实首失败必须是备份仍输出 v1、恢复入口缺失或行为缺失；不得用收集错误、环境错误、skip/xfail、真实业务路径缺失或并发污染冒充。

测试全部使用 `tempfile` 假仓、假 SQLite、假上传树、假 `.env` 和注入服务探针。覆盖至少：

- v2 六键、独立兼容版本、六根四态、状态/files 聚合、v1/未知版本固定拒绝；
- 未知物理文件、重复/逃逸/大小写碰撞、同大小篡改、损坏 SQLite；
- 非默认数据路径、busy 8000/5173、备份/live/work 根及祖先 junction；
- 恢复前备份失败零 live 变化；
- 每个根切换前后故障、逆序回滚、回滚自身失败、损坏 journal、崩溃重入；
- `present/empty/absent/not_included` 精确终态，legacy/canonical 隔离；
- 成功后 DB 完整性和全根哈希地图，失败后操作前全根哈希地图；
- PowerShell 5.1 BOM、中文 stderr、空格/中文路径、精确确认、正路径严格 exit 0；
- 假仓 `backup → 污染 → restore → 再用 pre-restore backup 回滚` 完整往返。

Codex 只组合运行 V1-A 升级专项、V1-B 专项、PowerShell 编码/解析、Python `compileall`、严格文件边界和 `git diff --check`；不运行后端全量、前端或整仓 E2E，不真实停机、备份或恢复主仓数据。

## 11. 隐私与敏感资产

备份、恢复前备份、hold/staging 和 SQLite 都可能包含 API Key、账号哈希、会话摘要与投标文件，必须视为敏感资产。禁止进入 Git、日志、消息箱、Grok 提示、公开同步目录或测试输出。

控制台成功输出只允许固定中文、恢复前备份最终目录和逻辑根计数；失败只输出固定原因首行。journal 不记录用户名、主机名、绝对业务路径、文件名列表、SHA 列表或正文。

## 12. 未交付边界

本包不提供：v1 强制恢复、跨数据兼容版本迁移、自动停机、在线热备/恢复、WAL、选择性文件/项目恢复、压缩/加密、定时/增量/云备份、备份浏览 UI、数据根迁移、非默认数据库路径、网络共享恢复、自动删除恢复前备份或任何跳过校验的逃生开关。

## 13. 实现与验收记录

代码提交=`20a4a60`，严格只包含本契约第 9 节六文件。Grok B 在冻结基线 `40d1852` 上先完成真实 failure-first：备份专项 `65` 项中 `56 passed / 9 failed`，首个业务失败为缺少 `DATA_COMPATIBILITY_VERSION`；恢复专项 `42` 项中 `1 passed / 41 failed`，首个业务失败为恢复入口不存在。两组均为 0 收集错误、0 skipped、0 xfail，全部使用 TEMP 假仓。

Codex 审查期间累计关闭生产问题 A1-A15 与测试问题 B1-B12。所有疑似问题均先发 `question`，由对应 Grok 独立确认存在后才另发最小返修 `task`。后段关键确认链包括：A10-A12=`msg_ed016c5d46da4ed3ba24330caea27a79`/`msg_26f642e1c01e46128880404fab2b6256`，A13=`msg_954cb283f42a4957ae918a8ea582e7c1`/`msg_da70087247b14b579110d4177afcd3ae`，A14=`msg_32f3946d54424466a0e3db1d7729ef03`/`msg_264fb5ca434f421f97a861faf9ce1dcf`，A15=`msg_e9910bb425954c83ae6aae9cd874e9a7`/`msg_9ef96c7b8efd4474b488b8e403ec2066`，B12=`msg_e6a87ae72b404428bea9b5cfd1c76a8c`/`msg_32599cb8bc2a449d8f8b5f91948bd1d8`。最终关闭了 stale lock 竞态、锁/工作根清理假成功、journal 路径穿越与相位校验、公共异常泄漏、回滚全图/SQLite 证据、hold/result 崩溃重入、已恢复根误删、`cutover_before_hold` 假 A3、过宽短路误删唯一 live，以及同秒备份名造成的测试假绿。

Codex 主仓串行验收结果：备份 `65 passed / 0 failed / 0 errors / 0 skipped`，恢复 `81 passed / 0 failed / 0 errors / 0 skipped`；四个 Python 文件无写入编译通过，Restore PS1 为 `EF-BB-BF` 且 Parser 0 错误，`git diff --check` 通过。Codex 另以独立断言验证 A13 result 前重入、A14 自然 `cutover_before_hold` 故障全图回滚和 A15 不一致终态 fail-closed，三项均通过。

最终 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `tools/v1-ops/biaoshu_backup.py` | `27AF069EACDCB336CDFA54782138CFDA01A2ED53FFFF388753C5B64BD57B1D53` |
| `tools/v1-ops/test_biaoshu_backup.py` | `2854DCF5202AE5DD1D94BA57AB573F8272025AEC5F8E33B9D0451B2CBFC0511A` |
| `Restore-Biaoshu.bat` | `C962CFBD9234138EC9320BC299ECD0EA986756EF5B2398335E6537757491226D` |
| `tools/v1-ops/Restore-Biaoshu.ps1` | `B1179C625674F7F822CE47D8593FF00698109D358AD722E8A938A4E34A02D7A0` |
| `tools/v1-ops/biaoshu_restore.py` | `893074F9B2D88141FC2C952274F7256E4A3F6DF184DB3F7D0520D6D253704AF6` |
| `tools/v1-ops/test_biaoshu_restore.py` | `BC6B8EBA2490E45A4BF06536337690C263C5E514722B2D3C3986066DBA350420` |

本轮未真实停机、备份或恢复主仓业务数据，未运行后端全量、前端、Playwright 或整仓 E2E。下一包转入 V1 自动解析生产可部署性与真实 TEMP 样本审计；V2/V3 继续后置。
