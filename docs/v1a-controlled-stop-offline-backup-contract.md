# V1-A 受控停机与离线备份基础契约

> 状态：只读审计后冻结，等待 Grok A/B failure-first
> 日期：2026-07-21
> 前置：V1/V2/V3 版本分层=`2ba8983`；P13-I4 已闭环
> 目标版本：V1 本机/可信内网可实际使用版
> 只读审计：任务结果=`msg_239b4262f141457bba42606a742ce580`/`msg_97af3590842d4847889a286eaf14fd0c`；运维与交付=`msg_19b4d1a3af0c438b88ea6bae14d85a08`/`msg_a5d03289d1be42b99ac04595fd06660e`

## 1. 目标与诚实语义

为 Windows 本机日用提供两个显式入口：受控停止本仓库开发服务，以及在服务停止后创建离线备份目录。备份用于保护当前真实业务数据，不是编辑态检查点、Git 提交、在线热备、跨版本迁移或云同步。

完整备份必须包含当前日用 SQLite、项目上传/图片/导出树、知识索引与知识卡片文件；现存仓库根 `uploads` 作为历史数据根单独保留，禁止静默忽略或与 `backend/uploads` 混合。语义模型缓存默认不备份，可由显式参数加入。`.env` 默认不备份，备份中的 SQLite 可能包含产品允许明文保存的 API Key、账号哈希和会话摘要，因此整个备份目录必须视为敏感资产，禁止进入 Git、日志、消息箱或公开同步目录。

## 2. 受控停机

- 根入口 `Stop-Biaoshu-Dev.bat` 只调用 UTF-8 BOM PowerShell 实现；默认检查 `127.0.0.1:8000` 与 `127.0.0.1:5173`。
- 停机前必须一次性收集两个端口的监听 PID 并验证归属。后端只允许本仓库 `backend/.venv` Python/uvicorn 进程；前端只允许命令行指向本仓库 `frontend` 的 Vite/Node 进程。
- 任一端口由无法确认归属的进程监听时，整次操作固定失败且零终止；不得按端口盲杀，不得影响 8010/8012/5174/5176 等测试端口或其它应用。
- 全部归属通过后才逐个终止进程树，并在有界时间内复查两个端口均已释放。无监听视为幂等成功；失败只显示固定中文原因，不输出完整命令行、环境变量或用户目录之外的敏感参数。
- 提供 `-WhatIf`/等价只读模式，必须走同一归属判定但不终止进程。
- 为专项测试提供 `-ListenerSnapshotJson <临时文件>`，只允许与 `-WhatIf` 同时使用；JSON 严格为 `port/pid/executablePath/commandLine` 四键记录数组。该入口只替代监听快照来源，仍走生产归属判定；未带 `-WhatIf`、额外/缺失键、重复 PID、非法端口/PID/路径或非数组均固定失败且零终止。不得接受快照正文作为待执行命令。

## 3. 离线备份

- 根入口 `Backup-Biaoshu.bat` 只调用 UTF-8 BOM PowerShell 包装；核心复制、SQLite 校验、JSON 清单和 SHA-256 使用 Python 标准库实现。
- `Backup-Biaoshu.bat [目标根]` 与 `Backup-Biaoshu.ps1 -DestinationRoot <目录>` 使用同一参数语义；未传目标根时固定使用仓库同级 `biaoshu-backups`，最终备份目录仍在仓库外。控制台成功输出只允许最终目录与固定敏感提示，不枚举源文件或 manifest 内容。
- 备份前精确检查 8000/5173 均未监听；任一仍监听时固定失败、零创建最终备份。不得自动停服务，停机与备份保持两次显式用户动作。
- 默认源固定锚定仓库绝对路径，不读取进程 cwd：
  1. `backend/data/biaoshu.db`（必需且只允许该日用库）；
  2. `backend/uploads`；
  3. `backend/data/knowledge`；
  4. `backend/data/knowledge_cards`；
  5. 仓库根非空 `uploads`，在备份中保存为独立 legacy 根；
  6. `backend/data/semantic-models` 仅在显式 `-IncludeSemanticModels` 时加入。
- 禁止打包 `biaoshu-e2e.db`、`biaoshu-pytest*.db`、`codex-*` 测试目录、`.env`、`.venv`、`node_modules`、日志、消息箱、Git 元数据或其它未知 `data/*.db`。
- 先在目标根创建同卷临时目录，完成复制、源/副本文件 SHA-256、大小核对和副本数据库 `PRAGMA integrity_check=ok` 后，再原子改名为 `biaoshu-backup-<UTC>`。任一步失败必须删除本轮临时目录，不得留下看似成功的最终目录。
- 目标根必须在仓库外，且不能位于任一源目录内；拒绝符号链接、junction/reparse point、路径逃逸、已存在最终目录和非普通文件。源文件在复制期间变化必须失败，不得把不一致快照标记为成功。
- `manifest.json` 使用固定版本 `biaoshu-offline-backup-v1`，只记录 UTC 时间、Git HEAD（可空）、逻辑根、相对路径、字节数和 SHA-256；不得记录绝对路径、主机名、用户名、API Key、Cookie、票据或文件正文。
- Python 核心冻结公开测试接口：`BACKUP_SCHEMA_VERSION`、`BackupError`、`build_source_plan(repo_root, include_semantic_models=False)`、`assert_services_stopped(host="127.0.0.1", ports=(8000, 5173), probe=None)`、`create_offline_backup(repo_root, destination_root, include_semantic_models=False, now=None, git_head=None, service_probe=None)` 与 `main(argv=None)`。`probe/now/git_head` 只用于纯测试注入，不得暴露为 root bat 的危险绕过参数；CLI 不提供跳过端口、完整性、哈希或源变化检查的选项。

## 4. 严格实现白名单

### Grok A：生产入口

1. `Stop-Biaoshu-Dev.bat`（新建）
2. `Backup-Biaoshu.bat`（新建）
3. `tools/v1-ops/Stop-Biaoshu-Dev.ps1`（新建，UTF-8 BOM）
4. `tools/v1-ops/Backup-Biaoshu.ps1`（新建，UTF-8 BOM）
5. `tools/v1-ops/biaoshu_backup.py`（新建）

### Grok B：独立专项测试

1. `tools/v1-ops/test_biaoshu_backup.py`（新建）

禁止修改现有启动脚本、`backend/app`、前端、依赖、配置、数据库模式、运行时 journal_mode、已有数据目录和既有测试。Grok 不得暂存、提交或推送。

## 5. failure-first 与验收

- Grok B 先在独立 worktree 写测试并证明生产入口缺失；真实首失败必须是脚本/模块不存在或行为缺失，不得以环境、编码、收集失败或 skip/xfail 代替。
- 测试使用临时假仓库、假 SQLite 和假文件树；禁止读取、复制、哈希或打印主仓真实 `biaoshu.db`、uploads、Key 或用户文件。
- 覆盖：精确源白名单、legacy 根分离、模型缓存开关、测试库排除、仓库外目标门、端口占用拒绝、完整性失败、复制中变化、哈希/大小、manifest 严格字段、临时目录清理、已存在目标、symlink/reparse 拒绝、无绝对路径/密钥文本。
- PowerShell/批处理覆盖：两个 PS1 必须 UTF-8 BOM；根 bat 只转发到固定脚本；Stop 的归属判定先全验证后终止、foreign listener 全局零副作用、幂等与 `WhatIf` 均有证据。不得真实终止当前机器进程。
- Codex 合并后只运行新专项、静态/编码门、临时夹具手工 smoke、Python `compileall` 和 `git diff --check`；不运行后端全量、整仓 E2E 或真实业务数据备份。

## 6. 未交付边界

本包不提供恢复覆盖、备份浏览 UI、在线热备、定时备份、增量备份、压缩/加密容器、云同步、WAL、数据库/上传根迁移、跨版本 Schema 迁移、自动安装依赖或自动导出密钥。V1 恢复与回滚演练必须在本包通过后另立契约。
