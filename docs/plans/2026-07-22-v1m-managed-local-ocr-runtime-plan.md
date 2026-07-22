<!--
模块：V1-M 管理式本机 OCR 自动解析实施计划
用途：把 OCR 真值、专用 runtime、后端多文件原子接线、前端策略与真实安装拆为可独立验收的四段。
对接：docs/v1m-managed-local-ocr-runtime-contract.md、P8B/P8C/P8D/P8E、V1-C、V1 路线图。
二次开发：每阶段只执行已冻结白名单；默认批量举证、Grok 确认并一次修完、Codex 单次终验；高风险问题保留 question/YES/单独授权。
-->

# V1-M 管理式本机 OCR 自动解析实施计划

> **执行要求：** Grok 承担限定实现和高耗费精确测试，Codex 负责批量举证、独立审查、一次串行终验、提交与推送；协作细则见 `docs/agent-collaboration.md`。
> **状态：** M1 已完成并推送；M2 两路只读审计与十项批量确认已完成，决策和精确白名单已冻结，待 failure-first。M3/M4 未开始。
> **基线：** 当前生产=`a7d640b`；仅协作分支，严禁 `main`。

**目标：** 让扫描 PDF 在本机专用 MinerU runtime 中受控解析，并最终接入既有项目任务/editor-state，同时保留轻量与人工回传路径。

**架构：** M1 先用 image-only PDF 和仓外 manifest 建立真实可判定的 runtime 门，并修复 MinerU 可写根；M2 再让后端按项目权限顺序解析全部 source 文件并单事务落库；M3 增加 `managed` 前端策略；M4 最后由管理员显式安装并运行真实烟测。

**技术栈：** Python 标准库、Pillow、pypdf、SQLAlchemy、FastAPI、React/TypeScript、PowerShell；真实 MinerU 与模型保持仓外独立运行时。

## M1 完成记录（2026-07-22）

- 提交：契约=`9ed3a06`；测试=`3be6d19`/`3b8e42e`/`61dbe38`；生产=`a7d640b`。
- Codex 独立串行：managed **29 passed**、MinerU **56 passed**、V1-C runtime_preflight **26 passed**、Docling **46 passed**。
- 真实 Windows junction 只读探针：follow `stat=0x10`、no-follow `lstat=0x2416`，生产 reparse 检测为 true；TEMP left=0。
- automated/fake-runtime 已通过；真实 CLI/模型、real-runtime/quality 均 did-not-run。M1 不改变 V1 约 94% 口径。
- M2 起采用简化流程：普通问题批量确认并一次修完，只由 Codex 做一次最终相关回归；权限、事务、隐私、数据损坏和真实安全边界仍保留分段授权。

---

## 阶段 0：冻结与隔离

1. Codex 提交契约、计划、交接、路线图和联调清单并推送 `collab/grok-code-codex-review`。
2. `biaoshu-v1m-a`、`biaoshu-v1m-b` 快进到冻结提交；两者分别使用独立 TEMP，后续后端测试必须设置独立 SQLite/uploads。
3. 固定消息链、M1 五文件边界、禁止安装/联网/真实 parser 和禁止操作 `main`。

预期：主仓与远端一致，两个 worktree 干净且 HEAD 相同。

## 阶段 1：M1 Grok B failure-first

### 任务 1：建立管理式 preflight 红门

**文件：**

- 新增：`tools/local-parser/test_managed_runtime_preflight.py`
- 禁止生产文件改动。

步骤：

1. 用 TEMP 生成合法/非法 manifest，覆盖精确五键、严格类型、相对路径、穿越、绝对/UNC/URL、symlink/reparse、CLI 类型、model marker 和 `requiredFreeBytes` 目标卷检查。
2. 测试模块不得在收集阶段顶层导入尚不存在的生产模块；缺入口必须表现为可计数的业务断言失败，其他独立门仍能执行，禁止重复 V1-C 的 import error/大量 did-not-run。
3. 用 Pillow 内置 ASCII 位图字体生成两页 image-only PDF；用 pypdf 断言每页文本层为空，PDF 内不得出现锚点明文字节。
4. 冻结新 CLI 的精确九键 JSON、状态/code/退出码和固定中文脱敏边界。
5. 假 runner 覆盖 dry-run、ASCII OCR 命中/缺失/逆序、零/多 Markdown、非零、超时、中断、TEMP 清理、env/argv/cwd 和 stdout/stderr 隔离。
6. Windows 中文 profile 只验证前置分流；字体缺失必须 did-not-run，不把 fake 输出当中文质量。
7. 运行：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_managed_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m py_compile tools\local-parser\test_managed_runtime_preflight.py
git diff --check
```

预期：生产入口不存在导致真实业务红；收集/语法/依赖失败不算 failure-first。

### 任务 2：冻结 MinerU env/cwd 红门

**文件：**

- 修改：`tools/local-parser/test_mineru_callback_helper.py`
- 禁止修改 helper 生产代码。

步骤：

1. 预置父环境中的 HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP/TMPDIR、代理、Key 与票据哨兵。
2. 精确要求子进程全部可写根等于本次 output/TEMP 根，且 `cwd` 精确等于该根。
3. 要求系统只读变量可有限继承，代理/Key/票据不得继承。
4. 单独运行新增节点，再运行 MinerU 助手全专项；生产未改时新增节点必须业务红。
5. 只发送 `review_request`，报告初红、passed/failed/did-not-run、SHA-256、TEMP 清理和未运行项；不得提交。

## 阶段 2：Codex 审查测试

1. 排除测试读取自身锚点后伪造 OCR、PDF 仍含文本层、按真实 CLI 是否存在分支、skip/xfail、宽泛 code、条件 return、捕获任意异常即通过。
2. 排除 manifest 字符串扫描代替路径行为、磁盘 monkeypatch 不验证目标卷、Windows 中文字体缺失被计为 passed。
3. 排除 env 测试只看构造字典却不验证真实 Popen kwargs/cwd。
4. 疑似问题先发 `question`；B 明确 YES 后才授权 test-only 返修。
5. Codex 独立复跑精确新专项与 MinerU 回归；绿后提交并推送测试，A worktree 才允许快进。

## 阶段 3：M1 Grok A production-only

**严格文件：**

- 新增：`tools/local-parser/managed_runtime_preflight.py`
- 修改：`tools/local-parser/mineru_callback_helper.py`
- 修改：`tools/local-parser/README.md`

步骤：

1. 实现 manifest 严格解析、runtime 根内路径解析、CLI/model marker/目标卷磁盘门和固定九键 JSON。
2. 实现 portable 两页 ASCII image-only PDF 与可选 Windows 中文 profile；生成器只处理合成内容。
3. 复用既有 MinerU 固定 argv、唯一 Markdown、有界读取、超时/中断/清理原语；不签票、不 HTTP、不读业务输入。
4. 收紧 `build_mineru_env(runtime_dir, source_env)`，所有可写根固定到本次 runtime_dir；`run_mineru_process` 传精确 `cwd=output_dir`。
5. 保持现有 P8D CLI 用户流程、回调、票据和输出语义不变；只增强隔离。
6. 更新 README，明确四层报告、manifest、未安装真值、管理员 M4 边界与回滚。
7. 只发送 `review_request`，不得提交或推送。

## 阶段 4：M1 串行验收

按顺序运行：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_managed_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_runtime_preflight.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m py_compile tools\local-parser\managed_runtime_preflight.py tools\local-parser\mineru_callback_helper.py
git diff --check
```

另验收：严格五文件、TEMP 清理、无真实 HTTP/票据/DB/uploads、无网络、无 runtime/model 文件进入 Git。当前机器不创建真实 manifest，不运行 OCR 真门；必须记录 `real-runtime/quality did-not-run`。

## 阶段 5：M1 提交与闭环

1. Codex 按“test-only → production → docs”分层中文提交并推送。
2. 记录精确测试数字、消息 ID、哈希、未运行项和本机仍未就绪真值。
3. M1 完成度不改变 V1 的 94% 粗估；只有 M2/M3/M4 全部完成后重新核算。

## 阶段 6：M2 后端包（决策与白名单已冻结）

只读审计：B=`msg_cc5b585777684a5ebfa90cffcb62186c`，A runtime 接口=`msg_348e0dc4ca974923843628fb0ff71a3a`；十项批量确认 question/YES=`msg_bd91defe184241b0ad99440301906e49`/`msg_ec9609ec6c40462983c938fb14dbbaaa`。D1-D10 全部 YES，无替代项。

冻结执行顺序：

1. Grok B 仅在四个 test-only 文件建立 failure-first：共享 pure core、服务端 path-only locator、每任务/每文件 ready、ASC 全 source、10/200MiB、双输出上限、固定分隔符、no-follow、成功/失败 result 键、单事务 rollback、并发/超时/取消和全域脱敏。
2. Codex 只核对真实红、反假绿与白名单，提交测试；不重复后端全量。
3. Grok A 只在九个 production 文件抽取 `managed_ocr_runtime_core`、实现 managed service、parse 聚合与 finalizer；`parse_engines`/API/Schema/前端/迁移保持冻结。
4. runtime core 由 M1 CLI 与 backend adapter 共用；backend 只按仓库根固定相对路径加载 pure core。manifest 只通过 `BIAOSHU_MANAGED_OCR_MANIFEST` path-only 服务端设置定位；客户端零路径，未配置固定失败且不降级。
5. parse 专用 ASC 查询与 GET desc 分离；单文件正文原样，多文件只用 `\n\n<!-- BIAOSHU_SOURCE_SEPARATOR -->\n\n`。成功 result 精确 `engine/fileCount/chars`，managed 失败 result 精确 `engine/diagnosticCode`。
6. finalizer 通过默认兼容的 `commit=False`/flush 复用 editor-state CAS、project update 与 task event，唯一 commit；任何失败先 rollback。进程内并发 1、每文件 30 分钟、任务 120 分钟、取消检查不高于 1 秒；跨进程与孙进程风险如实保留。
7. 独立 SQLite/uploads/TEMP；禁止真实 CLI、模型、业务文件、HTTP 端口和联网。
8. Grok 只跑精确测试；Codex 最后一次串行运行 M2 专项、parse_engines、parse_export 与 task/revision/security 代表回归，再做编译、diff、白名单和 TEMP 门。普通夹具/逻辑问题批量修，高风险新问题才重新 question。

## 阶段 7：M3 前端包（M2 后重新冻结）

1. 后端与前端策略精确扩为 `light|managed|local|ask`；非法值 fail-closed。
2. `managed` 创建 `engine=managed` 任务并沿用 V1-G 项目/任务代次成功水合。
3. `local` 保持人工回传零任务；`ask` 三选一且取消零副作用。
4. runtime 未就绪显示固定中文并提供人工回传入口；禁止静默 light。
5. lint/build 和受影响 E2E 串行，`--workers=1 --retries=0`。

## 阶段 8：M4 真实安装与发布验收

M4 必须单独取得用户/管理员明确授权。先确认目标卷、Python/CLI/模型官方兼容版本、下载体积、可用磁盘和回滚，再执行任何联网或安装。

验收顺序：manifest static → 既有 synthetic DOCX → ASCII image-only PDF → Windows 中文 image-only PDF → 隔离项目两文件 managed parse → 取消/超时/重启 → editor-state/task/event 一致性。无第二步及以后真实证据时不得声称 V1 OCR 已可用。
