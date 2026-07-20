# 前后端联调清单

> 目标：验证 health / 项目 / 设置 / revise / editor-state / 响应矩阵 / 本地标讯库 / 资源中心及受控同步 / **中标内容模板** 已闭环。
> Key **明文**存储与回显（保密机决策）。

## P13-C 当前已载入版本修订来源可见性（已完成）

契约：`docs/p13c-current-revision-source-visibility-contract.md`

计划：`docs/plans/2026-07-20-p13c-current-revision-source-visibility-plan.md`

冻结=`e62ea27`，实现=`6eaa89f`

联调确认：

1. `GET|PUT editor-state` 必出 `currentRevisionSourceKind`，只能为九类固定来源或 `null`；客户端投稿任何来源字段都不能控制结果。
2. 服务端来源查询只投影最新一条 `state_version/source_kind`，workspace/project 作用域，`created_at DESC,id DESC LIMIT 1`；不读 snapshot、不回扫、不写库。
3. 最新版本不匹配、坏来源、空账本与并发漂移显示“来源未知”；不能把旧同版本或其它项目/空间来源拼到当前响应。
4. 技术/商务标题区显示同一套中文来源标签；来源与 P13-B 时间只在合法 `stateVersion` 同响应通过当前会话/写入代次后接受。
5. 409、网络失败、非法版本、旧项目 GET/PUT 不污染；项目切换清空；不增加额外 GET/PUT、轮询、storage、timer。
6. 新字段不进入 13 键哈希、CAS、修订快照、写入/裁剪/恢复；无迁移、actor、用户名、presence 或实时最新承诺。

验收：真实 failure-first 后端 **18 failed**、前端 **5 failed**；Grok 后端/前端 **18/11 passed**，lint/build 通过。Codex test-only 返修后独立后端 P13-C+P12C **32 passed**、全状态 **19 passed**、P13-B/C E2E **11 passed**，lint/py_compile/diff-check 通过。未运行后端全量或整仓 E2E。

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13c_current_revision_source.py tests\test_p12c_browser_put_revisions.py --tb=short
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_full_version.py --tb=short
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-version-freshness.spec.ts --project=chromium --workers=1 --retries=0
npm run lint
```

## P13-B 已载入编辑版本更新时间可见性（已完成）

契约：`docs/p13b-editor-state-version-freshness-contract.md`
计划：`docs/plans/2026-07-20-p13b-editor-state-version-freshness-plan.md`

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-version-freshness.spec.ts --project=chromium --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --project=chromium --workers=1 --retries=0
npm run lint
npm run build
cd C:\Users\Administrator\biaoshu
git diff --check
git diff --name-only 040d644
git diff --cached --name-only
```

验收结果：冻结=`040d644`、实现=`1d4fe0b`。真实 failure-first **6 failed / 0 passed**；Grok P13-B/技术商务真值 **6/46 passed**，lint/build 通过；Codex test-only 返修后独立 P13-B **6 passed（24.7s）**、lint/diff-check 通过。严格六文件；合法无后缀 UTC ISO 固定显示到秒，非法/缺失显示未知；成功 GET/PUT 接受同一响应时间，真实 PUT abort/409/失败/迟到不污染；A→B 隔离旧 GET/PUT；展示功能零额外请求。未运行后端 pytest 或整仓 318 E2E。

## 1. 一键启动

```text
仓库根目录双击：Start-Biaoshu-Dev.bat
```

启动脚本会在后台静默拉起未运行的服务，不等待、不自动打开浏览器；已监听端口会直接返回。启动后直接访问前端地址验证。

Grok-Codex 本地协作：让 Grok 执行 `tools/agent-collaboration/Connect-Grok.ps1` 接入；协议与后续状态消息见 `docs/agent-collaboration.md`。消息目录仅用于本机运行时，不提交 Git。

若访问地址失败：

1. 确认点的是 **`Start-Biaoshu-Dev.bat`**（不是 `.url` 快捷方式）
2. 右键 bat → **以管理员身份运行**（一般不需要）
3. 或用备用脚本：右键 **`Start-Biaoshu-Dev.ps1`** → 使用 PowerShell 运行  
   （若提示禁止脚本：在 PowerShell 执行  
   `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`）
4. 仍失败时在资源管理器地址栏输入 `cmd` 回车，再执行：  
   `cd /d C:\Users\Administrator\biaoshu`  
   `Start-Biaoshu-Dev.bat`  
   再用 `netstat -ano | findstr :8000` 与 `netstat -ano | findstr :5173` 检查监听端口

成功时不会弹出服务窗口或启动器等待窗口；直接访问前端与健康检查地址确认服务状态。

或分别启动：

```powershell
# 后端
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\activate
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 前端
cd C:\Users\Administrator\biaoshu\frontend
npm run dev
```

- 前端：http://127.0.0.1:5173/create  
- 后端探活：http://127.0.0.1:8000/api/health  
- 开发代理：Vite 将 `/api` → `8000`（无需配置 CORS 也可）

## 2. 界面观测

| 检查点 | 期望 |
|--------|------|
| 左侧栏底部 API 状态点 | 绿 = 在线；红 = 离线 |
| 设置页保存 | 刷新后 Key **明文**仍在 |
| 设置「测试模型连通」 | 成功回显模型回复，或明确错误 detail |
| 我的项目 | 数据来源条显示「后端 API」 |
| 工作区标题旁 | 编辑持久化：后端 |

联调纯列表时，可在 `frontend/.env.local`：

```env
VITE_MERGE_MOCK_PROJECTS=false
```

## 3. 冒烟脚本（无外网 LLM）

```powershell
# 先起 uvicorn，再：
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python scripts\smoke_e2e.py
```

期望输出末尾：`OK smoke e2e`。

## 4. 手工验收路径

1. **创建** → 创建技术标项目 → 进入工作区  
2. **刷新**「我的项目」→ 新项目仍在  
3. **分析步**改概述 → 刷新页面 → 概述仍在（editor-state）  
4. **分析步响应矩阵**编辑技术要求/评分点后出现矩阵项 → 勾选章节/大纲并保存备注 → 刷新后仍保持；点击「智能建议」后逐条勾选应用，人工修改过或“不响应”条目不应被覆盖；删除或重生成大纲后，无效引用不计入覆盖率；导出技术标 Word 后应有「六、响应矩阵」表且不展示失效关联或内部 ID
5. **设置**填真实 Key → 测试连通
6. **分析步**反馈面板提交意见 → history 有摘要 → 「修订结果预览」→ 可替换概述
7. **导出模板**启用「标题段落边框与分级底色」和「最小标题左栏」→ 预览仅三级叶子标题显示加粗左边线 → 设为默认后导出 Word，技术标/商务标的 Markdown 叶子标题及大纲叶节点左边框加粗；概述、正文容器、章节标题和父标题保持普通边框，文档无整章页框
8. **技术标**上传 Markdown 后点「轻量解析」→ 最近任务由 `pending/running` 更新到 `success`，预览写入解析结果
9. **商务标**上传文件后运行 `parse`，或配置用户自备 Key 后运行一个 `biz_*` 任务 → 状态可更新并成功回填
10. **正文生成**页点击图片图标上传 PNG/JPEG/GIF → 当前光标位置写入 `biaoshu-image://file_...`；导出 Word 后有图片与可选题注；手工删掉该图片后再次导出，技术标导出页应显示有限纯文本“图片引用无效”告警且仍继续下载，Word 内也有降级段落，不得请求外网；商务标含同类 Markdown 引用时行为一致
11. 临时阻断单任务 `/events` 请求后重新发起任务 → 页面先查询一次任务，再以约 2 秒间隔查询至终态；不得无限重连 SSE
12. **停掉后端** → 状态变红；列表提示本地兜底，不白屏

## 5. 自动化测试

```powershell
cd backend
.\.venv\Scripts\python -m pytest -q

cd ..\frontend
npm run lint
npm run build

# 响应矩阵 E2E（独立 8010/5174 与 biaoshu-e2e.db；勿占用日用端口）
# 含：双 context 409、刷新来源保留映射、智能建议人工确认、来源 80 分页（本机 mock LLM）
# 首次需：npx playwright install chromium
npm run test:e2e:matrix

# 中标内容模板沉淀与复用 E2E
npm run test:e2e:templates

# 知识卡片创建 → 章节插入 → 刷新保持 E2E
npm run test:e2e:cards

# 阶段3 M3-A：模板/卡片只读融合建议 E2E（本地 mock LLM，不写章节）
npm run test:e2e:fuse

# 阶段3 M3-B/M3-D：差异预览、服务端原子确认、base 漂移跳过与失败语义 E2E
npm run test:e2e:fuse-apply

# 阶段3 M3-D：跨刷新最近 20 批、完整/部分/零恢复、一次消费与迟到隔离 E2E
npm run test:e2e:fuse-persistent-recovery

# P9B：国能 e 招计划追踪（隔离 8010/5174、biaoshu-e2e.db、MockTransport；禁止真实外网）
npm run test:e2e:opportunity-watch

# P9C：离线语义索引状态面板（隔离 8010/5174、路由拦截；禁止模型站点请求）
npm run test:e2e:semantic-index

# P10A：本机身份、会话恢复与受限导航（隔离 8010/5174、路由桩；禁止外部业务主机）
npm run test:e2e:auth-rbac

# P10B：财务商务标报价（隔离 8010/5174、路由桩）
npm run test:e2e:finance-role

# P10C：财务成本草案与毛利快照（隔离 8010/5174、路由桩；仅财务专用端点）
npm run test:e2e:finance-cost-draft

# P10D：人力人员资质素材卡（隔离 8010/5174、路由桩；仅 HR 专用端点）
npm run test:e2e:hr-credential-cards

# P10F：人力项目团队推荐快照（隔离 8010/5174、路由桩；HR 与标书制作者最小投影）
npm run test:e2e:hr-team-recommendations

# P10E：投标人匿名合规预览（隔离 8010/5174、路由桩；仅 bidder 专用端点）
npm run test:e2e:bidder-compliance-preview

# P10G：投标人项目级合规统计（隔离 8010/5174、路由桩；仅 bidder 最小项目投影）
npm run test:e2e:bidder-project-compliance

# P10H：人力人员业绩素材卡（隔离 8010/5174、路由桩；仅 HR 专用端点）
npm run test:e2e:hr-performance-cards

# P10I：人力资质到期提示（隔离 8010/5174、路由桩；仅 HR 只读最小投影）
npm run test:e2e:hr-credential-expiry

# P10J：财务个人成本变更记录（隔离 8010/5174、路由桩；仅本人当前空间成功事件）
npm run test:e2e:finance-cost-change-events

# P10K：财务项目成本变更记录（隔离 8010/5174、路由桩；显式读取上线后项目事件）
npm run test:e2e:finance-project-cost-change-events

# P8B：解析策略接线（真实本机 API/任务；禁止服务端 MinerU/Docling）
npm run test:e2e:parse-strategy

# P8C：required 一次性回传票据（路由桩 + 网络/存储/剪贴板反假绿；不启动解析器）
npm run test:e2e:local-parser-callback-ticket

# P9D：技术标/商务标导出图片失效引用浏览器提示（真实本机 export + 受控边界桩）
npm run test:e2e:export-image-warnings

# P11A：核心项目列表/详情/创建服务端单一真值（路由桩 + mock/localStorage 假成功与存储边界反假绿）
npm run test:e2e:core-project-data-truth

# P11B：商务标 editor-state 服务端单一真值（路由桩 + 旧 workspace 保值 + GET/PUT/会话隔离反假绿）
npm run test:e2e:business-editor-state-truth

# P11C：技术标 editor-state 服务端单一真值（真实登录 Cookie/CSRF + 409/M3-D + A→B 挂起保存隔离）
npm run test:e2e:technical-editor-state-truth
```

当前基线：后端串行全量 **800 passed**（1 条既有 Starlette/httpx 弃用警告）；P12C-C2 专项/四文件回归 **23/121 passed**，11 文件 `py_compile`、真实 SQLite 迁移失败回滚、白名单与 diff 检查通过。P12C-C1 历史基线为 13/201/777，P12C-B-D3 为 18/270/764，P12C-B-D2 为 25/299/746，P12C-B-D1 为 11/285/732，P12C-B-C2 为 20/272/721，P12C-B-C1 为 10/224/711，P12C-B-B2 为 11/147/701，P12C-B-B1 为 10/126/690，P12C-B-A 为 14/107/680，P12C-A 为 67/77/666。P12C-C3 前端独立结果为专项 **21 passed**、checkpoint restore **51 passed**、技术/商务 truth **46 passed**、Chromium headless 单 worker 零重试全量 **284 passed**；`lint` / `build` / 七文件白名单 / diff 通过，仅保留既有大 chunk 提示。P12B-D1 历史恢复专项/受影响回归/全量为 58/81/599；P12B-C3 历史后端/前端全量为 570/212；P12A、P8C、M3-D、P10K、P9D 及其他既有专项继续保留。E2E 共用 SQLite 重置脚本，禁止并行启动多个 Playwright 命令，必须逐条串行运行。

P8D/P8E 本机助手独立验收命令（仓库根；不安装或探测真实 MinerU/Docling）：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
```

P8E 当前为 Docling **46 passed**、P8D MinerU **54 passed**；后端 P8E-A/P8C/P8B/解析受影响回归 **37 passed**，P8C E2E **9 passed**、P8B E2E **6 passed**。P8E 当时沿用后端全量 487；P12A 更新为 518，P12B-A 为 537，P12B-C1/C2 依次为 552/562，P12B-C3 已更新为 570；P12B-B/C2/C3 将前端全量依次更新为 201/207/212。真实 Docling/模型未安装、未验收。

P12A 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state_checkpoints.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state.py tests/test_auth_rbac.py tests/test_health_and_projects.py tests/test_content_fuse_applications.py tests/test_content_fuse.py tests/test_bid_templates.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_async_and_callback.py tests/test_local_parser_callback_tickets.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

结果依次为 **29 / 97 / 15 / 518 passed**，均只有 1 条既有 Starlette/httpx 弃用警告。P12A 只提供空对象 POST 创建、元数据列表和单条只读详情；不应出现 restore/PUT/PATCH/DELETE/download/search 伪成功。列表与淘汰 SQL 不得投影 `snapshot_json`，跨项目详情必须在 SQL 中同时限定 `id/workspace_id/project_id`。

P12B-A 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state_full_version.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_content_fuse.py::test_content_fuse_success_readonly_and_base tests/test_content_fuse.py::test_content_fuse_all_sources_invalid_fails_without_write tests/test_content_fuse.py::test_content_fuse_cancel_keeps_editor_state tests/test_finance_role.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_editor_state_checkpoints.py tests/test_editor_state.py tests/test_response_matrix.py tests/test_content_fuse_applications.py tests/test_local_parser_callback_tickets.py tests/test_bid_templates.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

结果依次为 **19 / 12 / 104 / 537 passed**，均只有 1 条既有 Starlette/httpx 弃用警告。CAS 同时带全状态与矩阵版本时必须只有一次项目锁和一次锁后 editor-state 读取；提交成功后不得 `refresh` 或重读；全状态 409 detail 只能含 `code/message/currentStateVersion`。`updatedAt` 提交前后字符串必须稳定；持久 JSON 中的非有限 float 收敛为 `null`，但 P12A 直接伪造非有限规范快照仍必须失败。

P12B-B 独立验收命令（前端；必须逐条串行）：

```powershell
npm run lint
npm run build
npm run test:e2e:technical-editor-state-truth
npm run test:e2e:business-editor-state-truth
npm run test:e2e:matrix
npx playwright test e2e/hr-team-recommendations.spec.ts --workers=1
npm run test:e2e:fuse-apply
npm run test:e2e:fuse-persistent-recovery
npm run test:e2e
```

P12B-B 已实现并推送（契约/计划=`0636302`、实现=`473e823`）。验收已证明技术整包、guidance、矩阵合并和商务整包 PUT 都带最新 `expectedStateVersion`；同项目第二请求在第一响应前严格为 0，且 expected 精确等于第一响应版本；固定全状态 409 保留本地并阻断全部写入，只有显式全量 GET 才恢复。技术/商务 GET 缺失或非法版本、PUT 200 缺失/非法新版本均进入固定阻断；普通 409 无矩阵明细不得伪造空矩阵冲突。独立结果为 **28 / 18 / 8 / 4 / 6 / 5 / 201 passed**；其后 P12B-C 已完成。

P12B-C 独立验收命令（后端与前端分别在各自目录，全部串行）：

```powershell
# 后端 C3/M3-D 专项与全量
.\.venv\Scripts\python.exe -m pytest tests\test_p12b_delayed_writer_fences.py tests\test_content_fuse_applications.py -q
.\.venv\Scripts\python.exe -m py_compile app\api\schemas.py app\api\content_fuse_applications.py app\services\content_fuse_application_service.py
.\.venv\Scripts\python.exe -m pytest -q

# 前端 C3 相关与全量；禁止并行
npm run lint
npm run build
npx playwright test e2e/content-fuse-apply.spec.ts e2e/content-fuse-persistent-recovery.spec.ts e2e/technical-editor-state-truth.spec.ts e2e/p12b-delayed-writer-fences.spec.ts --project=chromium --workers=1 --retries=0
npx playwright test --project=chromium --workers=1 --retries=0
```

P12B-C 已实现并推送（冻结=`b5a9d90`、C1=`0c8fc77`、C2=`f3c05ae`、C3=`59fcd50`）。C1 验证任务/revise 创建时绑定版本、最终锁后 CAS；C2 验证个人 callback 原子零写以及 P8C 陈旧/空版本票据“消费但不写”；C3 验证 M3-D 全状态冲突优先、apply/consume 成功版本与独立算法一致、零恢复版本不变、两个 POST 严格等待普通 PUT并使用其响应版本。网络 abort、成功响应缺失/非法/带空白版本均逐轮证明本地正文保留、零重试、两个防抖窗口零 PUT、零 pageerror/unhandled。最终结果为后端 **62 / 570 passed**、前端 **48 / 212 passed**；其后 P12B-D 已完成。

P12B-D 独立验收命令（后端、前端分别在各自目录；全部串行）：

```powershell
# D1 后端
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoint_restore.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_editor_state_full_version.py tests\test_p12b_delayed_writer_fences.py tests\test_content_fuse_applications.py
.\.venv\Scripts\python.exe -m pytest -q

# D2 前端；禁止并行
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --project=chromium --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts e2e/content-fuse-apply.spec.ts e2e/content-fuse-persistent-recovery.spec.ts e2e/response-matrix-conflict.spec.ts --project=chromium --workers=1 --retries=0
npm run lint
npm run build
npm run test:e2e -- --workers=1 --retries=0
```

P12B-D 已实现并推送（冻结=`613818f`、D1=`551caba`、D2=`0f81dd6`）。D1 在同一项目锁和事务内完成当前 expected CAS、恢复前安全检查点、目标严格重验、共享 13 键写回、版本复核与最近 20 条裁剪，结果 **58 / 81 / 599 passed**。D2 只在面板展开时读最近 20 条元数据，创建先强制即时 PUT 再 POST `{}`，恢复二次确认后携带执行时最新 expected；成功唯一 editor-state GET，迟到 list/create/restore、折叠、项目切换与连点均隔离，ID/version/snapshot 不进入 DOM/存储/URL/console。四轮返修后结果 **51 / 63 / 263 passed**，lint/build/diff 通过；全量首跑单次纯白页后，精确用例 1 passed 且完整重跑 263 passed。联调不得把本包误扩展为自动检查点、每次 autosave 历史、任意版本浏览/回滚、删除、diff 或多人协作。

P12C-A 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_full_version.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\models\entities.py app\services\editor_state_revision_service.py tests\test_editor_state_revisions.py
```

P12C-A 已实现并推送（冻结=`daa8c43`、实现=`226e1c1`）。独立 `editor_state_revisions` 与检查点 20 条域完全分离，每项目最近 10 条；内部 transition 原语验证 before/after 的 13 个权威键与匹配版本，只 flush、不 commit/rollback/refresh/查询项目/加锁。最新与裁剪 SELECT 不加载 `snapshot_json`，DELETE 同时限定 workspace/project/行 ID，跨项目与跨空间旁路行不受影响。Codex 独立结果为 **67 / 77 / 666 passed**，编译、三文件白名单与工作树/暂存 diff 检查通过。A 包没有生产调用、API、Schema、前端、历史列表或恢复入口；联调不得把“表和原语存在”误报成自动历史已可用。P12C-B 必须按不同事务边界逐包接入并证明业务写/历史写同成同败。

P12C-B-A 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_browser_put_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_response_matrix.py tests\test_editor_state.py
.\.venv\Scripts\python.exe -m pytest -q
```

P12C-B-A 已实现并推送（冻结=`fbf93c0`、实现=`acf3139`）。公开浏览器 PUT 唯一传服务端字面量 `browser_put`，请求体额外来源键被忽略；服务默认来源为 `None`，不会改变其他调用者。来源存在时先取得项目写锁，锁后构造 before，写后构造 after，并在唯一 commit 前同事务记录。空账本、连续、相邻去重、断链、回退、矩阵版本、省略字段保留、真实跨空间 404、冲突、记录 flush 失败和 commit 失败均已覆盖。Codex 独立结果为 **14 / 107 / 680 passed**；本包没有接入 task/revise、callback、content-fuse 或 checkpoint restore，也没有新增历史列表、详情、恢复、Schema 或前端。

P12C-B-B1 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_task_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_task_cancel.py tests\test_task_sse.py tests\test_settings_and_revise.py tests\test_business_bid_mvp.py
.\.venv\Scripts\python.exe -m pytest -q
```

P12C-B-B1 已实现并推送（冻结=`05864f6`、实现=`5a0d1c0`）。九类 writer 任务每次真实 editor-state upsert 固定记录 `task`；批量章节逐章迁移、逐章修订与成功前缀语义不变。两个私有包装器保留版本冲突的固定 stale 流程，并把其他 upsert 内部异常收敛为固定中文任务错误，禁止 SQL、路径、表名、异常类型、正文或版本进入 REST/SSE。Codex 独立结果为 **10 / 126 / 690 passed**；该实现提交没有接入当时尚待后包的商务 revise、callback、content-fuse apply/consume、checkpoint restore、历史 API 或前端。

P12C-B-B2 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revise_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_settings_and_revise.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_business_bid_mvp.py tests\test_async_and_callback.py tests\test_local_parser_callback_tickets.py tests\test_content_fuse_applications.py tests\test_editor_state_checkpoint_restore.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\services\revise_service.py tests\test_p12c_revise_revisions.py
```

P12C-B-B2 已实现并推送（冻结=`3a30c03`、实现=`5149385`）。商务 `business_parse` 与四类结构化 revise 的真实 editor-state 迁移固定记录 `revise`；结构解析失败、空 revised、普通技术 revise、陈旧 expected 与 LLM 期间漂移不伪造本次修订。recorder/commit 失败均由真实 ASGI 脱敏 500 返回并证明 editor-state/revision 双零；外部并发浏览器修订按来源和精确版本排除。Codex 独立结果为 **11 / 147 / 701 passed**；在 B2 交付时个人 callback、P8C 一次性本地解析 callback、content-fuse apply/consume、checkpoint restore、历史 API 与前端均未实现，后续个人 callback 已由 C1 单独交付。

P12C-B-C1 独立验收命令（后端；全部串行）：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_personal_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12b_delayed_writer_fences.py tests\test_async_and_callback.py tests\test_local_parser_callback_tickets.py tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_editor_state.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_auth_rbac.py tests\test_task_cancel.py tests\test_task_sse.py tests\test_business_bid_mvp.py
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app\api\parse_callback.py tests\test_p12c_personal_callback_revisions.py
```

P12C-B-C1 已实现并推送（冻结=`76834f5`、实现=`1d0ce0e`）。个人 callback 用同一次锁后 before 和提交前内存 after，以固定 `callback` 与 parsed Markdown、成功任务、项目步骤共享唯一事务；客户端 source 不能控制内部来源。缺/坏 expected、Token 失败、陈旧 409 均零修订；recorder/commit 失败固定 JSON 500 且 editor-state/任务/项目/revision 全域回滚。P8C 隔离通过真实公开 HTTP 路由证明 C1 未提前接入。Codex 独立结果为 **10 / 224 / 711 passed**；C1 交付时 P8C `local_parser`、content-fuse apply/consume、checkpoint restore、历史 API 与前端均未实现，随后 P8C 已由 C2 单独交付。

P12C-B-C2 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_local_parser_callback_revisions.py tests\test_p12c_personal_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_local_parser_callback_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_local_parser_callback_tickets.py tests\test_async_and_callback.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_editor_state_full_version.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_checkpoints.py tests\test_editor_state.py tests\test_parse_engines.py tests\test_parse_strategy_read.py tests\test_parse_export.py
.\.venv\Scripts\python.exe -m pytest -q
```

P12C-B-C2 已实现并推送（冻结=`52bbabf`、实现=`82cc82e`）。P8C fresh 回调用同一次锁后 before/行和固定 `local_parser`，与票据消费、parsed Markdown、成功任务、项目步骤及成功审计共享原唯一事务；stale/null 继续只提交票据消费且零修订，recorder/commit 失败全域回滚并允许同票重用。旧 C1 阶段守卫已收紧为 P8C 精确一条 `local_parser` 且零 `callback`，无效/缺失/过期/重放 401 必须固定 JSON。Codex 独立结果为 **20 / 272 / 721 passed**；C2 交付时 content-fuse apply/consume、checkpoint restore、历史 API 与前端仍未实现，随后 apply 已由 D1 单独交付。

P12C-B-D1 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_apply_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_apply_revisions.py tests\test_content_fuse_applications.py tests\test_content_fuse.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_checkpoints.py tests\test_editor_state.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12c_local_parser_callback_revisions.py
.\.venv\Scripts\python.exe -m pytest -q
```

P12C-B-D1 已实现并推送（冻结=`e8ffaeb`、实现=`a6a28f6`）。融合 apply 以同一次锁后 before/行、提交前内存 after 和固定 `content_fuse_apply`，与章节、恢复批次和裁剪共享原唯一事务；browser_put 基线后一至五条建议同批精确 +1，空账本精确 before+after。recorder/trim/commit 失败全域回滚，双并发恰好一胜一 409；完整/部分/零 consume 的修订身份序列前后完全不变，证明 D1 未误接。Codex 独立结果为 **11 / 285 / 732 passed**；D1 交付时 consume、checkpoint restore、历史 API 与前端均未实现，随后 consume 已由 D2 单独交付。

P12C-B-D2 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_apply_revisions.py tests\test_p12c_content_fuse_consume_revisions.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_content_fuse_consume_revisions.py tests\test_p12c_content_fuse_apply_revisions.py tests\test_content_fuse_applications.py tests\test_content_fuse.py tests\test_p12b_delayed_writer_fences.py tests\test_editor_state_revisions.py tests\test_editor_state_full_version.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_checkpoints.py tests\test_editor_state.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12c_local_parser_callback_revisions.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\services\content_fuse_application_service.py tests\test_p12c_content_fuse_apply_revisions.py tests\test_p12c_content_fuse_consume_revisions.py
```

P12C-B-D2 已实现并推送（冻结=`6b83fc1`、实现=`f256f5b`）。完整/部分恢复在原唯一事务内精确记录一次固定 `content_fuse_consume`；零恢复只消费批次，完整 editor-state、版本和修订身份序列不变。两轮测试返修关闭宽松集合、跨项目恒真比较、真实跨空间隔离缺失、并发任意 409、零恢复部分字段比较及 500 表名/路径泄漏门；完整/零恢复双并发分别固定版本冲突/已消费错误码。Codex 独立结果为 **25 / 299 / 746 passed**；D2 交付时 checkpoint restore、历史 API/前端、删除、diff、搜索与多人协作仍未实现，随后 checkpoint restore 已由 D3 单独交付。

P12C-B-D3 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_checkpoint_restore_revisions.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_checkpoints.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_full_version.py tests\test_editor_state_revisions.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12c_local_parser_callback_revisions.py tests\test_p12c_content_fuse_apply_revisions.py tests\test_p12c_content_fuse_consume_revisions.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_p12b_delayed_writer_fences.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\services\editor_state_checkpoint_service.py tests\test_p12c_checkpoint_restore_revisions.py
```

P12C-B-D3 已实现并推送（冻结=`1d44484`、实现=`b91a7ff`）。不同版本恢复在原唯一事务内固定记录 `checkpoint_restore`；空账本 before+after、已有基线精确 +1、回到旧版本形成新时间点，同内容只创建安全检查点并更新 `updatedAt`、零修订。两轮 test-only 返修收紧来源隔离、完整失败状态零写、两个裁剪失败原目标可重试与公开 500 脱敏。Codex 独立结果为 **18 / 270 / 764 passed**；历史 API/前端、删除、diff、搜索、跨项目历史、任意修订恢复与多人协作仍未实现。

P12C-C1 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_editor_state_checkpoints.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_full_version.py tests\test_auth_rbac.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
.\.venv\Scripts\python.exe -m py_compile app\services\editor_state_revision_history_service.py app\api\editor_state_revisions.py app\api\schemas.py app\main.py tests\test_p12c_revision_history_read.py
```

P12C-C1 已实现并推送（冻结=`26b504e`、实现=`7023ecd`）。列表只投影最近 10 条五列元数据，详情三重作用域按需读取并严格重验规范快照；所有成功与业务错误 `no-store`。Codex 首次审查真实复现坏 `created_at` 裸 500，返修后越界字节、非法来源、坏时间和正文损坏均以真实 SQLite+HTTP 固定脱敏。独立结果为 **13 / 201 / 777 passed**；C1 交付时 C2 restore 与前端尚未实现，后端恢复随后已由 C2 完成。

P12C-C2 独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_restore.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_restore.py tests\test_p12c_revision_history_read.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_editor_state_revisions.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
```

P12C-C2 已实现（冻结=`54af600`、范围修订=`2276366`、实现=`0803250`）。POST restore 严格 expected CAS 后复用 C1 三重作用域目标重验，以准确 `revision_restore` 与恢复前安全检查点、共享 13 键写回、双配额裁剪共享唯一事务；同内容只创建安全点并更新时间，零修订。Codex 首轮真实故障注入得到 **1 failed / 22 passed**，证明旧 SQLite 迁移失败会残留临时表；CREATE 前零行 DML 触发物理事务后，旧 DDL/八列逐值/索引/FK/旧 CHECK 完整且临时表不存在。独立结果为 **23 / 121 / 800 passed**；前端入口随后已由 C3 完成。

P12C-C3 独立验收命令（前端；必须逐条串行）：

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
npx playwright test --workers=1 --retries=0
```

P12C-C3 已实现（冻结=`6b9143a`、实现=`5e4f9f6`）。默认折叠零请求，展开只取最近 10 条元数据，详情严格校验后只保留六项有界摘要；revision ID/version/正文不进入可见 DOM、URL、存储或日志。恢复与检查点共用令牌和既有保存链，确认前零 POST、执行时使用最新 expected、成功唯一 editor-state GET；list/detail/restore 迟到以项目会话和详情操作代次隔离。多轮测试返修用真实检查点 create、双项目双 restore 与 `listCompleteLog/detailCompleteLog` 关闭互斥、旧 finally、迟到及 arrived 冒充 fulfill 假绿。Codex 独立结果为 **21 / 51 / 46 / 284 passed**，lint/build/diff/七文件白名单通过；后端沿用 **800 passed**。当时尚无当前状态差异 API；后续 P12D-A/B 已补齐字段摘要及前端入口，P12E-A/B/C 已补齐单修订对当前与双历史修订正文差异，P12F-A/B/C 已补齐最多 20 条/20 MiB 有限保留、后端游标页和前端手动加载更多。删除、搜索/筛选、跨项目历史和多人协作仍未实现。

P9C-R1 固定离线模型运行时门独立验收命令（后端；模型只允许显式准备一次，其他命令严格离线）：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py --download
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py
$env:HF_HUB_OFFLINE="1"
$env:TRANSFORMERS_OFFLINE="1"
backend\.venv\Scripts\python.exe backend\scripts\semantic_model_preflight.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_semantic_model_runtime.py --basetemp=C:\Temp\p9c-r1
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_knowledge_rag.py -k semantic
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_knowledge_rag.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests --basetemp=C:\Temp\bf-p9c
```

P9C-R1 已实现并推送（冻结=`cd70ef0`、实现=`b53dcce`）。固定制品为 revision `26478543676740eb665f803ca07f3f7f478857c8`、10 文件、96,378,176 字节、`artifactFingerprint=a04f4aa475164fb551464a0320b09c37`，权重 SHA-256 与契约一致；真实离线预检为 **Recall@5=1.0 / NDCG@5=0.927295**。Codex 独立结果为专项 **17 passed**、语义 **21 passed / 7 deselected**、知识库完整 **28 passed**、后端全量 **817 passed**；`pip check`、`py_compile`、diff 和六文件白名单通过。模型缓存位于被忽略的 `backend/data/semantic-models`，不是 Git 交付物；不得在 CI 或日常测试中重复下载。

P12D-A 修订与当前状态差异摘要独立验收命令（后端）：

```powershell
cd C:\Users\Administrator\biaoshu
backend\.venv\Scripts\python.exe -m py_compile backend\app\api\schemas.py backend\app\api\editor_state_revisions.py backend\app\services\editor_state_revision_comparison_service.py backend\tests\test_p12d_revision_current_comparison.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_p12d_revision_current_comparison.py --tb=line --basetemp=C:\Temp\p12d-a-special
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_p12c_revision_history_read.py backend\tests\test_p12c_revision_restore.py backend\tests\test_editor_state_revisions.py backend\tests\test_editor_state_checkpoints.py --tb=line --basetemp=C:\Temp\p12d-a-reg
backend\.venv\Scripts\python.exe -m pytest -q backend\tests --basetemp=C:\Temp\bf-p12d-a
git diff --check
```

P12D-A 已实现并推送（冻结=`2cc6ee3`、实现=`9445fcc`）。GET comparison 组合当前权威 13 键与目标修订重验，逐字段规范 JSON 比较；成功只返回 `sameState/changedFields/currentSummary/targetSummary`，两侧摘要各固定六项，不返回正文、字段值、ID 或版本。有效 failure-first 为 **14 failed**；Codex 独立专项 **14 passed**、P12C C1/C2/账本/检查点回归 **132 passed**、后端全量 **831 passed**，1 条既有弃用告警；`py_compile`、diff、四文件白名单、五域零写和 `True`/`1` 反假绿通过。P12D-A 无前端变化，前端入口须由 P12D-B 串行验收。

## 6. 已接 API 一览

| 方法 | 路径 |
|------|------|
| GET | `/api/health` |
| GET | `/api/projects/{projectId}/editor-state-revisions`（最近 10 条元数据；不读取正文） |
| GET | `/api/projects/{projectId}/editor-state-revisions/{revisionId}`（按需详情；三重作用域） |
| GET | `/api/projects/{projectId}/editor-state-revisions/{revisionId}/comparison`（与当前权威 13 键只读比较；仅字段名和两侧六项摘要） |
| POST | `/api/projects/{projectId}/editor-state-revisions/{revisionId}/restore`（执行时 expected；安全检查点后受限恢复） |
| GET | `/api/auth/bootstrap-status`（公开；`bootstrapped`、`authRequired`） |
| POST | `/api/auth/login`（公开；设置 HttpOnly 会话 Cookie） |
| POST | `/api/auth/logout`（当前会话 + CSRF） |
| GET | `/api/auth/me`（当前会话；仅脱敏身份） |
| GET | `/api/auth/csrf`（当前会话；轮换 CSRF，`no-store`） |
| PUT | `/api/auth/active-workspace`（当前会话 + CSRF） |
| GET/POST/PATCH/DELETE | `/api/auth/members*`（仅工作空间所有者） |
| GET | `/api/finance/business-bids`（仅 strict `finance`；当前 workspace 商务标报价摘要；`no-store`） |
| GET | `/api/finance/business-bids/{projectId}`（仅 strict `finance`；白名单报价分项；技术标/跨空间/不存在统一 404；`no-store`） |
| GET | `/api/finance/business-bids/{projectId}/cost-draft`（仅 strict `finance`；成本草案与毛利快照；`no-store`） |
| POST | `/api/finance/business-bids/{projectId}/cost-entries`（仅 strict `finance` + CSRF；正整数分成本条目） |
| PATCH/DELETE | `/api/finance/business-bids/{projectId}/cost-entries/{entryId}`（仅 strict `finance` + CSRF；跨项目统一 404） |
| GET | `/api/finance/cost-change-events`（仅 strict `finance`；本人当前空间最近 50 条成功成本变更；`no-store`） |
| GET | `/api/finance/business-bids/{projectId}/cost-change-events`（仅 strict `finance`；当前空间商务标上线后最近 50 条项目事件；`no-store`） |
| GET | `/api/hr/credential-cards`（仅 strict `hr`；当前空间摘要；不含备注；`no-store`） |
| GET | `/api/hr/credential-cards/{cardId}`（仅 strict `hr`；跨空间/不存在统一 `404 hr_credential_not_found`；`no-store`） |
| POST | `/api/hr/credential-cards`（仅 strict `hr` + CSRF；字段白名单与严格 JSON 布尔） |
| PATCH | `/api/hr/credential-cards/{cardId}`（仅 strict `hr` + CSRF；更新/启停；无 DELETE） |
| GET | `/api/hr/team-recommendations/projects`（仅 strict `hr`；当前空间技术标 `id/name` 选择器；`no-store`） |
| GET | `/api/hr/team-recommendations`（仅 strict `hr`；推荐摘要；`no-store`） |
| GET/PUT | `/api/hr/team-recommendations/{projectId}`（仅 strict `hr`；详情/有序快照写入，PUT 需 CSRF） |
| GET/POST | `/api/hr/performance-cards`（仅 strict `hr`；摘要列表/创建，POST 需 CSRF；`no-store`） |
| GET/PATCH | `/api/hr/performance-cards/{cardId}`（仅 strict `hr`；按需详情/编辑启停，PATCH 需 CSRF；跨空间/不存在统一 404） |
| GET | `/api/hr/credential-expiry`（仅 strict `hr`；服务端 UTC 日期、固定 90 天计数与最小关注项；`no-store`） |
| GET | `/api/bidder/compliance-preview`（仅 strict `bidder`；当前空间技术标响应矩阵匿名汇总；`no-store`） |
| GET | `/api/bidder/project-compliance/projects`（仅 strict `bidder`；当前空间技术标 `id/name` 选择器；`no-store`） |
| GET | `/api/bidder/project-compliance/{projectId}`（仅 strict `bidder`；单项目五项统计投影；跨空间/商务标/不存在统一 404；`no-store`） |
| GET/POST | `/api/projects` |
| GET/PATCH/DELETE | `/api/projects/{id}` |
| POST | `/api/projects/{id}/parse-callback-ticket`（仅 required strict `bid_writer` + CSRF；签发 10 分钟单项目单次票据；`no-store`） |
| POST | `/api/local-parser/callback`（唯一精确公开回调；仅 `X-Local-Parse-Ticket`；2 MiB 流式上限；`no-store`） |
| GET | `/api/projects/{projectId}/team-recommendation`（仅 strict `bid_writer`；当前空间技术标的最小展示投影；`no-store`） |
| GET | `/api/projects/{id}/tasks/{taskId}/events`（SSE） |
| GET/PUT | `/api/projects/{id}/editor-state`（含 responseMatrix） |
| POST | `/api/projects/{id}/content-fuse-applications`（M3-D；只接 taskId/suggestionIds；章节与恢复快照原子写入；`no-store`） |
| GET | `/api/projects/{id}/content-fuse-applications`（M3-D；最近 20 批最小投影；`no-store`） |
| POST | `/api/projects/{id}/content-fuse-applications/{batchId}/consume`（M3-D；未漂移章节一次性恢复并消费；`no-store`） |
| GET/POST | `/api/projects/{id}/files`（仅招标源文件） |
| GET/POST | `/api/projects/{id}/images`（仅项目正文图片） |
| GET | `/api/projects/{id}/images/{fileId}`（受控预览） |
| GET/PUT | `/api/settings` |
| GET | `/api/settings/parse-strategy`（标书制作者工作空间语义；只返回 `parseStrategy`；`no-store`） |
| POST | `/api/llm/test` |
| POST | `/api/projects/{id}/artifacts/{artifactId}/revise` |
| GET/POST | `/api/opportunities` |
| GET/PATCH/DELETE | `/api/opportunities/{id}` |
| POST | `/api/opportunities/{id}/projects` |
| POST | `/api/opportunities/import`（本机 CSV/JSON 整批导入） |
| GET/POST | `/api/resources` |
| GET/PATCH/DELETE | `/api/resources/{id}` |
| POST | `/api/resources/{id}/view` |
| GET | `/api/resources/sync-sources`（仅同步状态，不含地址/公钥/错误原文） |
| POST | `/api/templates/from-project`（技术标沉淀中标内容模板） |
| GET | `/api/templates`（可选 q/status；列表摘要无完整 snapshot） |
| GET/DELETE | `/api/templates/{id}`（详情含完整 snapshot） |
| POST | `/api/templates/{id}/projects`（从模板新建技术标草稿） |
| GET | `/api/cards`（可选 q/type/status；列表摘要无正文/base64） |
| POST | `/api/cards`（手工创建文本卡） |
| POST | `/api/cards/upload-image`（PNG/JPEG/GIF 图片卡） |
| POST | `/api/cards/from-chunk` / `/api/cards/from-project-image` |
| GET/PATCH/DELETE | `/api/cards/{id}` |
| GET | `/api/cards/{id}/content`（图片卡二进制） |
| POST | `/api/projects/{id}/insert-card`（返回 Markdown；图片复制为项目 role=image） |
| POST | `/api/projects/{id}/tasks` type=`content_fuse`（M3-A：模板/卡片只读融合建议；仅 result_json；禁止写 editor-state） |
| GET | `/api/knowledge/semantic-index`（当前工作空间的脱敏离线语义索引状态） |
| GET | `/api/knowledge/semantic-index/{indexId}`（当前工作空间索引详情；跨空间 404） |
| POST | `/api/knowledge/semantic-index/rebuild`（无请求体；仅显式构建时后台加载固定离线模型） |

## 6.1 P10D 人员资质素材卡

1. 以严格 `hr` 登录，打开 `/hr`；侧栏仅显示「人力 / 人员资质」，不得显示财务或标书制作者业务入口。
2. 初始列表仅请求 `GET /api/hr/credential-cards`，摘要不显示 `remark`；点选卡片后才请求详情并显示备注。
3. 新建、编辑和启停必须携带内存 CSRF；每次成功后均重新 GET 列表与当前详情，不使用乐观更新或浏览器存储。
4. `owner` 的隐式绕过、`bid_writer`、`finance`、`bidder` 与 disabled 均没有人力入口；直达 `/hr` 只显示受限页，且不应发 HR API 请求。
5. 不得出现证件号、手机号、住址、附件、URL、创建人或工作空间字段；P10D 卡片本身无 DELETE、导出、项目关联或跨空间搜索；项目团队快照由下节 P10F 独立受限提供。

## 6.2 P10F 人力项目团队推荐快照

1. 以严格 `hr` 登录，打开 `/hr/team-recommendations`；初始只能请求 HR 技术标 `id/name` 选择器和资质卡**摘要**，选择项目后才可请求推荐详情，绝不请求卡片备注。
2. 保存只发送有序 `memberCardIds`，仅 `isActive=true` 的当前空间卡可加入；成功后必须重读推荐摘要和详情。已保存快照即使来源卡后续编辑或停用也不得自动改变，停用成员须由 HR 明确移除后再保存。
3. `disabled`、非 HR、所有者隐式绕过和跨空间均不得获得 HR 入口或发出 HR API 请求；写入须携带内存 CSRF，错误不得回显输入、卡 ID 或服务端 detail。
4. 以严格 `bid_writer` 打开技术标项目时，仅用户点击「查看团队推荐」后才可请求单项目投影；`ready` 只显示顺序、协作显示名和资质摘要，`empty` 明确未推荐。仅有 `is_owner` 不是授权；成员角色本身为 `bid_writer` 才可按角色通过。
5. 标书制作者展示不得请求 `/hr/*`、完整项目、编辑态、文件、财务或外网；两侧均不得写入浏览器存储、导出、自动匹配、Word 写入、人员业绩、证件或附件。

## 6.3 P10E 投标人匿名合规预览

1. 以严格 `bidder` 登录，打开 `/bidder`；侧栏仅显示「投标人 / 合规预览」，不得显示标书制作者、财务或人力入口。
2. 页面仅请求 `GET /api/bidder/compliance-preview`，并只展示总条目、已覆盖、未覆盖、已豁免和服务端给出的覆盖率基点；`empty` 时覆盖率显示「暂无可计算覆盖率」。
3. 响应和页面不得出现项目数量、项目 ID/名称、工作空间 ID、人员、原文、`sourceKey`、章节/大纲、备注、文件、报价或成本；页面须声明不是评审结论或投标结果。
4. `owner` 的隐式绕过、`bid_writer`、`finance`、`hr` 与 disabled 均没有投标人入口；直达 `/bidder` 只显示受限页，且不应发投标人预览 API 请求。
5. 浏览器不得写入 `localStorage` 或 `sessionStorage`；除认证、健康检查和本接口外，不能请求项目、编辑态、设置、文件、财务、人力或外网端点。完整边界见 `docs/p10e-bidder-anonymous-compliance-preview-contract.md`。

## 6.4 P10G 投标人项目级合规统计

1. 以严格 `bidder` 登录，打开 `/bidder/project-compliance`；初始只请求 `GET /api/bidder/project-compliance/projects`，选择器只显示当前空间技术标名称，不能请求 P10E 聚合、项目详情或编辑态。
2. 选择项目后才请求 `GET /api/bidder/project-compliance/{projectId}`；页面只显示总条目、已覆盖、未覆盖、已豁免和覆盖率。空矩阵为 `200 empty` 与全零统计，不显示项目字段、矩阵行、章节、源文、人员或财务。
3. 切换项目时旧统计须立即消失，延迟响应不得污染新选择；错误固定中文且不回显 ID、路径、后端 detail 或敏感标记。
4. `disabled`、仅所有者、`bid_writer`、`finance`、`hr` 直达该路由均为受限页且不发 P10G API；当前成员角色本身为 `bidder` 的所有者按角色正常通过。
5. 不得写入浏览器存储或 URL 查询参数；不得请求 `/projects*`、`/editor-state`、`/hr/*`、`/finance/*`、文件、设置、P10E 聚合或外网。完整边界见 `docs/p10g-bidder-project-compliance-contract.md`。

## 6.5 P10H 人员业绩素材卡

1. 以严格 `hr` 登录，打开 `/hr/performance-cards`；侧栏「人力 / 人员业绩」激活且「人员资质」不误激活，非 HR、disabled 与仅所有者均无入口，直达只显示受限页且零 P10H API。
2. 初始只请求 `GET /api/hr/performance-cards`，列表只显示人员、项目、角色、年份、状态和时间，不得含 `performanceSummary` 或 `remark`；点选后才请求单卡详情。
3. 新建、编辑与启停须携带内存 CSRF；成功后必须重新读取列表和当前详情，不使用乐观更新。完成年份仅允许空值或 1900–2100 整数，非法输入在前端预检后不得发写请求。
4. 快速点选 A→B 时，A 的迟到详情不得覆盖 B；错误只显示固定中文，不得回显后端 detail、路径 ID、输入内容或敏感标记。
5. 不得请求 P10D/P10F、项目、编辑态、文件、财务、投标人或外网接口，不得写入 `localStorage`、`sessionStorage` 或 URL 参数；无 DELETE、附件、导出、项目关联、自动匹配或团队写入。

## 6.6 P10I 人员资质到期提示

1. 以严格 `hr` 登录，打开 `/hr/credential-expiry`；侧栏「人力 / 到期提示」精确激活，「人员资质」不误激活。非 HR、disabled 与仅所有者直达只显示受限页，并且零 P10I API。
2. 页面首次挂载严格只请求一次 `GET /api/hr/credential-expiry`；点击刷新后累计严格两次。React Strict Mode 不得造成重复读取或重复审计，不得用模块全局缓存跨用户或会话共享结果。
3. 页面直接显示服务端 `asOfDate`、固定 `windowDays=90`、六项计数和三类关注项；不得用 `Date.now()` 重算。有效卡只计数，停用卡只计入排除数，无启用卡时明确“停用卡已排除”。
4. 页面须显示“仅依据人工录入的有效期日期生成，不验证证书真实性、持证状态、适用范围或监管结论”；不得展示 `cardId`、备注、创建人、工作空间、时间戳、证件号、附件、路径或外链。
5. 不得请求 P10D/P10F/P10H、项目、编辑态、文件、财务、投标人、未知 API 或外网，不得写入 `localStorage`、`sessionStorage` 或 URL 参数；错误固定中文且不回显后端 detail。完整边界见 `docs/p10i-hr-credential-expiry-contract.md`。

## 6.7 P10J 财务个人成本变更记录

1. 以严格 `finance` 登录，打开 `/finance/cost-changes`；侧栏「财务 / 我的成本记录」精确激活，「财务报价」不误激活。非财务、disabled 与仅所有者直达只显示受限页，并且零 P10J API。
2. 页面首次挂载严格只请求一次 `GET /api/finance/cost-change-events`；点击刷新后累计严格两次。页面只展示固定中文动作、完整 `entryId` 与安全时间，不根据时间或条目 ID 推导项目、金额或内容。
3. 页面须声明“只记录当前账户在当前工作空间成功的成本条目新增、修改、删除；不是完整财务审计，不能还原项目、金额、内容、变更前后值或失败尝试”。空数组和非数组均为空态，错误固定中文且不得回显后端 detail、路径或敏感标记。
4. 不得请求报价、成本草案、项目、编辑态、设置、文件、人力、投标人、未知 API 或外网；不得写入 `localStorage`、`sessionStorage` 或 URL 参数。完整边界见 `docs/p10j-finance-personal-cost-change-events-contract.md`。

## 6.8 P10K 财务项目成本变更记录

1. 以严格 `finance` 登录并打开既有 `/finance`；选中且加载完成一个商务标项目后，成本草案下出现“项目成本记录”，但挂载和切项目都不得自动请求 P10K GET。
2. 点击“查看项目记录”后精确请求一次 `GET /api/finance/business-bids/{编码后的项目 ID}/cost-change-events`，刷新后累计两次。页面只显示固定动作、完整 `entryId`、`本人/其他财务成员` 和安全时间。
3. 打开记录后通过既有 P10C 新增成本条目，P10K 请求数必须保持不变；只有手动刷新才看到新事件。项目切换须立即关闭并清空旧记录，迟到响应不得覆盖新项目。
4. 页面须声明只记录 P10K 上线后的成功操作，不含旧历史、金额、内容、成员身份、失败尝试或完整审计。错误固定中文，不回显后端 detail、路径、项目 ID 或原始异常。
5. 不得请求 P10J、通用项目/editor-state/settings/files、其他角色、未知 API 或外网；不得写入 local/session storage、IndexedDB、Cookie、剪贴板或控制台。完整边界见 `docs/p10k-finance-project-cost-change-events-contract.md`。

## 6.9 P8B 解析策略接线

1. 设置页保存 `light`、`local` 或 `ask` 后，技术标和商务标解析动作都重新请求 `GET /api/settings/parse-strategy`；响应只能有 `parseStrategy` 且带 `Cache-Control: no-store`，不得回显完整设置或 Key。
2. `light` 创建既有 `parse` 任务，任务 payload 固定 `engine=lightweight`；成功后继续按既有路径刷新解析预览或商务编辑态。
3. `local` 不创建任务，进入 `/local-parser?projectId=<当前项目>`，页面仅预填项目 ID；MinerU 仍由保密机本地运行，用户粘贴 Markdown 后才调用既有 callback。
4. `ask` 每次显示一次性选择框；取消不建任务、不回写默认策略。商务标上传、整段重解析和反馈重生成均按同一规则处理。
5. 策略读取失败只显示「暂时无法读取解析策略，请稍后重试」，不得回显后端详情或静默降级；浏览器不得使用 `localStorage`/`sessionStorage` 持久化或决定策略。

## 6.10 P8C 本地解析一次性回传票据

1. required 模式以 strict `bid_writer` 打开 `/local-parser?projectId=<当前项目>`；挂载、改项目 ID 和刷新均不得自动签发。只有显式点击“生成一次性回传票据”才发送一次无 body、带既有 CSRF 的签发 POST。
2. 页面只在当前组件内存显示票据、固定 `/api/local-parser/callback`、`X-Local-Parse-Ticket` 和当前站点绝对 `curl.exe`；刷新后立即丢失。不得使用响应 callbackPath 构造 URL，不得写 localStorage、sessionStorage、IndexedDB、URL、控制台或剪贴板。
3. 外部助手只可向精确公共路径提交 `source=mineru` 的受限 JSON；缺失/错误/过期/重放票据统一 401，正文超过 2 MiB 固定 413。成功只返回 `ok/chars/taskId`，同一票据并发只能一次成功。
4. disabled 显示“无需一次性票据”并保留旧 `X-Local-Token` + Markdown 手工表单；finance/hr/bidder/仅 owner 非制作者不挂载页面且零签发。页面不自动调用公共 callback，不启动 MinerU/Docling，不请求外网。
5. 完整契约见 `docs/p8c-local-parser-one-time-callback-ticket-contract.md`；后端=`af39ff8`，前端=`1cf5576`。

## 6.11 M3-C 融合写入最近批次单次撤销

1. 进入技术标编写步，打开「模板/卡片融合」，生成建议并勾选至少两个目标章确认写入；对话框出现“撤销本次写入”，章节正文变为建议内容、状态变为待审。
2. 未做其他编辑时点击撤销，应显示“已撤销 2 章，跳过 0 章”；按钮立即消失，原正文和原状态恢复，等待防抖保存后刷新仍保持。
3. 再次写入后，在遮罩下手工修改其中一章正文，再点击撤销；只恢复未漂移章，手工章保持现值，汇总显示恢复/跳过各自数量。标题或状态漂移同样不得覆盖。
4. 关闭对话框再打开不得出现旧撤销按钮；生成新建议清空旧快照，下一成功批次只替换最近批次。无成功写入、全跳过或已消费快照不得建立/复活撤销入口。
5. 撤销不发新业务 API，只沿用 editor-state PUT；不得写 `localStorage`、`sessionStorage`、IndexedDB、URL 或模块全局缓存，不得影响响应矩阵、大纲、分析、其他项目或其他用户。完整边界见 `docs/m3c-content-fuse-undo-contract.md`。

## 6.12 M3-D 融合写入持久恢复批次

1. 进入技术标编写步，打开「模板/卡片融合」，生成建议后默认不勾选。勾选 1–5 条且同目标章最多一条，点击确认只发送一次 `POST /content-fuse-applications`，body 键和值精确为真实 `taskId/suggestionIds`；确认前和在途不得新增 editor-state PUT。
2. 服务端成功后前端只执行一次真实 editor-state GET，随后读取批次列表。若该唯一 GET 失败，应显示「融合已写入，但刷新失败，请关闭后重新打开」，服务端批次已存在且同一对话框不能二次 create；业务 POST 409/500 则显示「融合确认失败，请刷新后重试」，正文和批次均不变化。
3. 关闭再打开对话框，最近批次仍显示时间、章数和「可恢复」，并固定声明「最多保留最近 20 批，不是完整版本历史」；页面不得展示任务、批次、建议、章节或来源 ID，不得请求历史正文、模板/卡片详情或外网。
4. 点击恢复必须二次确认。完整未漂移时恢复全部；手工改变一章的标题/正文/状态时只恢复其他章；全部漂移时恢复 0 章。三种结果都只允许一次 consume，并变为「已消费」、不再显示恢复按钮。
5. consume 成功后唯一 editor-state GET 失败时显示「恢复已完成，但刷新失败，请关闭后重新打开」，服务端批次已 consumed 且不得二次 consume。项目 A→B、关闭后迟到列表/create/consume 均不得重开对话框、刷新错误项目、显示旧消息或追加 editor/list GET。
6. 浏览器业务请求须符合 method+精确路径白名单；主动未知 `/api`、伪项目路径和外网探针必须被可观测阻断。M3-D 不新增 localStorage 键，sessionStorage/IndexedDB/Cookie 精确空，剪贴板读写为 0，页面/console/存储不含秘密串或项目/task/suggestion/batch ID。完整边界见 `docs/m3d-content-fuse-persistent-recovery-contract.md`。

## 6.13 P11A 核心项目真实数据收口

1. 后端返回真实技术标/商务标项目时，列表只显示对应 `kind` 的服务端项目；返回 `200 []` 时显示真实空态，不得补 `mockProjects`、`mockBusinessProjects` 或旧 localStorage 项目。
2. 预置旧 `biaoshu.projects.v1` 后刷新列表、触发列表 500、直达演示 ID，旧项目都不得出现；旧键和值必须保持精确不变，不得新增 v2/cache/其他项目元数据键，也不得上传旧值。
3. 技术标新建页、创建方案页、商务标入口各自模拟 POST 失败：按钮在途禁用，页面保持原 URL/表单/列表，显示固定「项目创建失败，请稍后重试」，不得生成 `proj_*`、导航假工作区或写 pending。再次点击只能新增一次真实 POST。
4. 创建成功只使用 POST 响应的真实 projectId 导航；创建方案有文件时，`biaoshu.pendingProjectFiles` 只能在成功后写入，键集和值精确为真实 projectId 与页面文件名。P11A 当时未改 editor-state；后续 P11B/P11C 已分别移除商务标 workspace 与技术标 editor-state 的本地/mock 成功依据。
5. 商务标详情 404/失败显示「未找到项目」而不是复活演示卡；技术标详情可回真实列表。查重/废标项目列表失败时选择器为空、固定中文且无未处理 Promise。
6. P11A E2E 主动阻断未知 `/api`、`/api/projects` 前缀未知端点与外网；应用层 console error/warning 精确空，local/session/IndexedDB/Cookie/clipboard 按场景精确收敛。完整契约见 `docs/p11a-core-project-data-truth-contract.md`，计划=`70a2dc7`，前端=`b0a86e4`。

## 6.14 P11B 商务标编辑态真实数据收口

1. 商务标工作区首次加载、显式重试、任务/修订后刷新只认当前项目 `GET /api/projects/{id}/editor-state`；服务端空字段保持空，不得补 `bb_*` 演示资格、目录、报价或承诺内容。
2. 预置 `biaoshu.businessBid.workspace.{projectId}` 后，页面不得读取、写入、删除、迁移或上传它，键和值必须精确不变，也不得新增 v2/cache/其他 workspace 别名。`biaoshu.businessBid.feedback.{projectId}` 仅保留 AI 反馈历史既有语义，不参与水合或成功判定。
3. GET 失败、401、404 均显示固定「商务标工作区加载失败，请稍后重试」卡片，只提供重试和返回列表；编辑控件、旧内容和 PUT 均为零。重试每次只增加一次当前项目 GET，成功后才挂工作区。
4. 初始 GET 成功后，编辑按既有 600 ms 防抖发送精确商务字段 PUT；失败只显示固定「商务标工作区保存失败，请稍后重试」，不得回显 detail/code/路径/项目 ID。再次编辑可新增一次 PUT，成功清错。
5. 任务成功后的唯一 editor-state 刷新失败时，任务成功事实不反转，但旧内容立即退出并进入同一加载失败态；项目 A→B 时，A 的迟到 GET、PUT 成功/失败与定时器不得污染 B。
6. P11B E2E 使用 method+精确路径白名单，主动阻断未知 API 和外网，并核对 local/session/IndexedDB/Cookie/clipboard/console 边界。完整契约见 `docs/p11b-business-editor-state-truth-contract.md`，计划=`6a3f4fe`，前端=`a99d8d4`。

## 6.15 P11C 技术标编辑态真实数据收口

1. 技术标工作区首次加载、显式重试和任务后刷新只认当前项目 editor-state GET；服务端真实内容精确呈现，analysis/outline/facts/chapters/parsedMarkdown 全空时保持真实空态，不补 mock。
2. 预置 `biaoshu.technicalPlan.editors.{projectId}` 后不得读取、写入、删除、迁移或上传，旧键和值精确不变；生产页面不存在填入演示分析、伪事实抽取、示例目录和固定伪日志入口。
3. GET 500/401/404 均显示固定加载失败卡、零工作区和零 PUT；每次重试只增加一次 GET。PUT 500/401/403 显示固定保存失败，再次编辑只新增一次 PUT，成功清错且不泄漏 detail/code/路径/项目 ID。
4. required 场景必须先经登录页真实 `POST /api/auth/login`，由浏览器同源携带 HttpOnly Cookie；登录响应内存 CSRF 精确用于普通防抖 PUT 和应用矩阵合并 PUT，Token、Cookie 与正文不落浏览器存储或 console。
5. 409 继续进入固定中文响应矩阵三方合并，不显示服务端原文；应用合并仍只写 `responseMatrix` 与 `responseMatrixVersion`。M3-D 业务成功但刷新失败时不重复业务请求，关闭对话框后进入 P11C 加载失败卡。
6. SPA A→B 时，A 的项目对象、初始化 GET、任务后刷新、普通 PUT 成功/失败/409 均不得污染 B；即使 A PUT 挂起，B 的 GET 和合法保存也不得被同一保存链阻塞。
7. P11C E2E 使用 method+精确路径白名单，主动阻断未知 API 和外网，并核对 local/session/IndexedDB/Cookie/clipboard/console 边界。完整契约见 `docs/p11c-technical-editor-state-truth-contract.md`，计划/契约=`24b7ba8`，安全细化=`c5b3eec`，前端=`1441509`。

## 6.16 P8D 本机 MinerU 外置解析助手

1. 先按 MinerU 官方文档人工安装 `mineru.exe` 和本地模型；助手绝不执行 pip、模型下载、远程 API、浏览器或后端内嵌进程。没有真实运行时也可完整运行 54 项假进程/假 HTTP 单测。
2. 在 P8C 页面显式签发 10 分钟单项目单次票据，再从交互 TTY 运行 `tools/local-parser/mineru_callback_helper.py --input <本地单文件>`；非 TTY 管道、非 43 字符 URL-safe 票据、命令行/环境/文件票据均拒绝。
3. Windows 只接受 PATH 中普通 `mineru.exe`，拒绝 `.cmd/.bat/.com`；命令固定 pipeline、`shell=False`，子进程只继承系统环境白名单并强制本地离线模型，代理/API Key/票据不继承。
4. 输入只允许单个非符号链接 PDF/图片/DOCX/PPTX/XLSX，非空且不超过 50 MiB；输出只在系统临时目录，树上限 4096 项、唯一 Markdown、读取前/有界读取/码点/JSON 四重上限，失败零回调并清理。
5. 回调只允许 `http|https` 回环 Origin，固定 `/api/local-parser/callback`，无代理、无重定向、一次请求零重试；成功/错误响应均有读取上限，页面和终端不回显票据、绝对路径、正文、taskId 或 detail。
6. 完整契约见 `docs/p8d-mineru-local-helper-contract.md`，计划=`30d066f`，实现=`e1fe316`。自动安装、真实模型样本验收、常驻服务和 MinerU 孙进程百分百回收仍未交付。

## 6.17 P8E 本机 Docling 外置解析助手

1. P8C 公共回调只精确接受 `mineru|docling`；非法大小写、空白、前后缀和未知来源在消费票据前固定失败，合法 `docling` 进入同一事务并保留固定审计脱敏。
2. Docling 助手仅接受 `--input`、`--artifacts-path` 和可选回环 Origin；Windows 只认 PATH 中普通非符号链接 `docling.exe`，固定 `docling convert` 参数且禁止远程服务、外部插件和用户附加参数。
3. 用户须在助手外人工安装 CLI、下载离线模型并传入已存在普通非符号链接模型目录；助手不安装、不下载、不探测模型，不把假 CLI 测试冒充真实模型就绪。
4. 子进程 `cwd`、HOME/USERPROFILE、APPDATA、TEMP、XDG/HF/Torch/Matplotlib/Python 缓存等 14 个可写目录全部绑定单次 `biaoshu-docling-*` 临时根；代理、Docling service/API、Token、业务配置和票据不继承，退出后统一清理。
5. 输出继续复用 P8D 的树、唯一 Markdown、有界读取、码点、JSON、无代理/无重定向单回调和固定脱敏边界；`source=docling` 必须显式进入 body，MinerU 默认仍为 `source=mineru`。
6. 完整契约=`docs/p8e-docling-local-helper-contract.md`，计划=`73b1264`，后端=`79b346e`，助手=`e3f9cc4`；独立验收 Docling 46、MinerU 54、后端 37、P8C E2E 9、P8B E2E 6 passed。

## 7. 本机日用主链路（目标 A 加强版）

| 步骤 | 操作 |
|------|------|
| 上传 | document 步选择 PDF/DOCX/TXT |
| 解析 | `light` 时「轻量解析」（**异步任务**，顶部进度条）；`ask` 时先选本次方式 |
| 本地 MinerU | `local` 或询问选择本地后进入带项目 ID 的 `/local-parser`，粘贴 Markdown 回传，或 `POST .../parse-callback` |
| 分析 | 「AI 招标分析」→ 结构化概述/技术要求/废标风险/评分点（可编辑），并生成可手工维护的响应矩阵 |
| 响应矩阵 | 在分析步把技术要求/评分点映射到大纲节点和章节；可调用用户已配置模型生成待确认建议，逐条应用后才保存；删除大纲/章节后无效引用不计入覆盖；技术标 Word 导出包含收敛后的矩阵表 |
| 导出样式 | 模板设置「设为默认」同步到后端，导出 Word 应用字体、标题编号、标题段落边框与分级底色；启用最小标题左栏时仅大纲/Markdown 叶子标题加粗左边框 |
| 大纲 | 「AI 生成大纲」 |
| 正文 | 「AI 生成本章」或 **「生成全部空章节」** |
| 正文图片 | 正文工具栏图标上传 PNG/JPEG/GIF，写入项目内 `biaoshu-image://file_...` 引用 |
| 导出 | 「生成并下载 Word」（含封面、项目内正文图片及无效引用 warning） |

任务默认异步：`POST /tasks` 立即返回，前端优先订阅 `GET /tasks/{id}/events` 的 `snapshot` / `task` / `heartbeat`；流不可用时立即 GET 一次，再以 2 秒间隔轮询。P13-A 已完成：required 模式无自定义头时使用会话活动工作空间，显式头只能选择成员空间且仅 bid_writer 可读；disabled 继续兼容默认空间/显式头。连接前 Session 在流开始前关闭，每帧短 Session 重新校验 workspace/project/task。
测试可用：`POST /tasks?sync=true`。

## 8. 商务标六步（MVP）

| 步骤 | 操作 |
|------|------|
| 列表 | `/business-bid` → `GET /api/projects?kind=business` |
| 新建 | 「从招标文件开始」→ `POST /projects` `kind=business` |
| 解析 | 上传文件 → 任务 `parse` → editor-state `parsedMarkdown` |
| 资格 | 「生成资格草稿」→ `biz_qualify` → `businessQualify` |
| 目录/报价/承诺 | `biz_toc` / `biz_quote` / `biz_commit` |
| 导出 | 「生成并下载 Word」→ `export` `payload.mode=business` |
| 反馈修订 | 各步 AiFeedback 提交后，表格/解析文应变化（后端写 editor-state） |

手改字段防抖写回 `PUT .../editor-state`。技术标列表应带 `kind=technical` 以免混入。  
新建真实项目不应出现演示假资格行（空数组保持空）。

## 9. 查重 / 废标（合规）

1. 技术标有章节正文；知识库有 ready 文档  
2. `/duplicate-check` 选项目 → 开始查重 → 有命中可左右对照  
3. `/rejection-check` 选项目 → 运行检查 → 见 analysis 风险或规则命中  

## 10. 知识库混合检索

1. 上传文档后可在「知识库」看到“离线语义索引（本机）”状态面板；模型名固定为 `BAAI/bge-small-zh-v1.5`、维度固定为 512，页面没有模型 URL、Token、模型名或缓存路径输入。
2. 未构建、模型不可用、磁盘不足、构建中、失败或中断时，页面必须显示固定中文降级原因；搜索仍可返回关键词命中，`vectorScore=0`，不得把历史哈希向量称为语义结果。
3. 点击“构建语义索引”仅提交无请求体的本机 API；同空间构建中按钮禁用。浏览器网络请求只能前往本机 `/api`，不得访问模型站点。
4. 构建完成后，`GET /api/knowledge/search?q=关键词` 的响应包含 `semanticStatus`；只有 `ready` 时才允许非零语义 `vectorScore`。跨工作空间索引详情必须返回 404。
5. 当前受控本机已按 P9C-R1 显式准备固定模型；执行 `backend/.venv/Scripts/python.exe backend/scripts/semantic_model_preflight.py` 会先检查 5 GiB 磁盘并仅读固定合成集，独立实测 Recall@5=`1.0`、NDCG@5=`0.927295`。其他机器缓存缺失时仍应受控返回 `model_unavailable` 与退出码 2，不得由生产请求、预检或测试隐式下载、安装依赖或伪造通过。

## 11. 本地标讯库

1. 打开 `/bid-opportunity`；空工作空间显示空态，使用“新增标讯”录入标题、截止日期等字段。
2. 新增一个截止日超过 7 天的标讯，按关键字、地区和状态筛选后仍可找到；修改后刷新页面，字段应保持。
3. 从未截止标讯创建技术方案项目，应跳转项目正文页；删除原标讯后，项目仍存在但 `sourceOpportunityId` 为空。
4. 将标讯截止日改为昨天后，“创建技术方案项目”不可用。
5. 仅演示环境需要初始数据时，在 `backend/.env` 设置 `SEED_SAMPLE_OPPORTUNITIES=true` 后重启；默认不得自动写入示例记录。
6. 点击“导入标讯”选择 UTF-8 CSV 或 JSON；合法记录导入后出现在列表。重复 `sourceKey` 应统计为跳过；任一行日期/标题非法时弹层显示行号且列表不新增记录。
7. 导入仅接受本机 CSV/JSON（默认上限 2 MiB、2,000 行）；不得填写外部 URL、Token 或附件路径。
8. P9B：在同页“国能 e 招计划追踪”上传本机 `.xlsx`；页面应显示“需人工确认；不会自动创建项目”。同步期间按钮禁用，结束后显示计划数、运行状态、命中和北京时间。
9. P9B：仅 `resolved` 命中显示“加入本地标讯”；`needs_review` 不显示该操作。接受后本地列表出现一条标讯，重复接受不重复创建；公告外链具有新窗口和 `noreferrer` 属性。
10. P9B：浏览器网络请求只能前往本机 `/api`；E2E 必须使用隔离数据库和 MockTransport，不得以真实国能网络作为测试依赖。完整边界见 `docs/p9b-chnenergy-integration-contract.md`。

## 12. 中标内容模板

1. 打开技术标项目（大纲与章节非空）→ 点击「沉淀为模板」→ 填写名称/可选标签 → 确认。
2. 打开侧栏「中标模板」`/bid-templates` → 列表可见；搜索标题/标签可用。
3. 点击「从模板新建」→ 进入新项目大纲步；刷新后大纲/章节仍在；修改新项目不改变模板快照。
4. 删除源项目后，模板仍可打开且可继续新建；删除模板不影响任何项目。
5. 与「导出模板」页相互独立，勿混用术语。

## 13. 资源中心

1. 打开 `/resources`，默认显示六条“系统精选”资源；其 `workspaceId` 为 `null`，页面没有编辑或删除按钮。
2. 新增一条资源并填写标题、正文 Markdown、标签和色调；保存后刷新页面，资源应保持并显示“我的资源”。
3. 点击用户资源打开详情，浏览量应增加一次；刷新后数量仍保持，且资源排序不因浏览而变化。
4. 编辑、删除自己的资源应成功；系统资源的 `PATCH` 和 `DELETE` 应返回 403。
5. 资源正文只按文本展示；不配置也不应存在 `VITE_RESOURCES_URL`、浏览器远程 fetch 或 mock 回退。
6. 默认不设 `RESOURCE_SYNC_SOURCES` 时，在 `backend` 执行 `.\.venv\Scripts\python.exe scripts\sync_resources.py`，应提示未配置来源且不发生网络请求。
7. 使用 [资源同步清单协议](resource-sync-manifest.md) 配置测试发布方后运行命令；新资源应以只读“系统精选”出现，重复同版本清单不重复创建。`GET /api/resources/sync-sources` 只返回名称、状态和计数，不返回 URL、公钥或远端错误。

## 14. 仍未接（后续）

Celery、MinerU/Docling 自动安装、模型打包、常驻服务、真实模型样本验收与完整孙进程治理、P9B 以外的外部标讯数据源、P9C 的其他模型/GPU/在线 embedding/真实用户语料评测与自动模型更新、修订历史删除/搜索/跨项目历史/多人协作、商务 AI 反馈历史服务端化、P10K 以外的财务税务/审批/导出/预算/回款/版本与失败尝试/完整身份审计、P10I 以外的人力附件与真实证件核验、P10G 以外的投标人矩阵明细/版本/结果跟踪与其他合规数据域、SSE 事件重放/多任务总线/前端工作空间切换 UI、标题整章布局语义。P13-H1 已交付项目级事件游标后端；修订游标页/手动加载更多已由 P12F-B/C 完成；SSE 工作空间鉴权已由 P13-A 完成，但事件重放与上述 UI 扩展仍未接。

**响应矩阵相关（已接 vs 未扩）：** 多端冲突的版本写保护、409 与双浏览器上下文 E2E 主路径已接；「刷新来源」保留人工映射 E2E 已接；**智能建议人工确认后应用** E2E 已接；**来源超过 80 分页** 已推送（`1289c92`）；**字段级三方合并** MVP + E2E 已推送（`2c7b3e0`，`response-matrix-field-merge.spec.ts`）。仍未接：Word 失效引用在浏览器层的扩展（导出逻辑以后端单测为准）；包 9 交付增强。

**解析相关（包 8 MVP + P8B + P8C + P8D + P8E）：** 可插拔调度 `parse_engines` + 默认 `lightweight` + 任务 `result.engine` 已推送（`6db1586`）；P8B 已把工作空间 `light/local/ask` 接到技术标和商务标解析入口；P8C 已提供 10 分钟单项目单次回传票据；P8D/P8E 已提供只调用本机既有 `mineru.exe`/`docling.exe` 的离线、回环、受限标准库助手（`e1fe316`/`e3f9cc4`）。旧个人 callback 与可选长期 Token 仍兼容；真实 CLI/模型需人工安装准备，自动部署仍未接。

## 15. 知识库 RAG 简版

1. 打开「知识库」→ 上传 md/txt/docx/pdf → 状态「已就绪」、分块数 > 0  
2. 浏览器或 curl：`GET http://127.0.0.1:8000/api/knowledge/search?q=关键词` 有 items  
3. 技术标生成大纲/章节时，任务 result 可含 `kbCitations`（有相关文档时）  
4. 无文档时生成行为与此前一致
## P12D-B 前端修订对比入口（已完成）

P12D-B 在 P12D-A 只读 comparison API 之上完成技术标/商务标共用“与当前对比”入口。Grok 只修改三个白名单文件并返回 review_request，Codex 独立审查和提交；首轮 failure-first 实际为 **2 failed / 21 passed / 1 did not run**，原因是技术标串行分组首个比较入口失败后后续一条未运行，已按实际日志记录，未将其伪写为 3/21。

独立验收必须逐条串行、\`--workers=1 --retries=0\`：

1. \`npx --no-install playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0\` → **24 passed**；
2. \`npx --no-install playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0\` → **51 passed**；
3. \`npx --no-install playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0\` → **46 passed**；
4. \`npx --no-install playwright test --workers=1 --retries=0\` → **287 passed**；
5. \`npm run lint\`、\`npm run build\` → 通过；build 仅有既有 chunk 大小警告。

比较请求是单次 GET，无 body、查询参数、重试、轮询、详情 GET、restore POST、editor-state GET/PUT、检查点或外网旁路；UI 只显示固定中文字段标签和两侧六项摘要。单条修订对当前状态正文差异已由 P12E-A 接入，双历史修订正文差异已由 P12E-B/C 接入；自动批量比较、完整时间线、删除、搜索、分页、导出、分享和多人协作仍未接入。

## P12E-A 单条修订正文差异预览（已完成）

P12E-A 冻结=`5aa205c`、实现=`f9f067e`。后端新增唯一只读 `GET /api/projects/{projectId}/editor-state-revisions/{revisionId}/body-diff`；成功体精确六键，章节项/片段精确五键/二键，不返回 revision、版本、chapter ID、路径或原始快照。完整正文先判等，最多前 100 个实际正文差异章进入 difflib；展示正文 20,000 码点、标题 240、80 hunks/章、2,000 码点/hunk、全响应 120,000 码点，任一截断固定 `truncated=true`。

Codex 首轮探针真实复现 101 个变化章产生 **101** 次 `_diff_lines`，返修红测为 **1 failed / 1 passed**，修后 **2 passed**；尾章完整值反假绿同时证明“前 100 章相同、第 101 章才不同”仍为 `sameBody=false` 且有可见项。独立验收：

1. 后端 P12E 专项 **23 passed**，P12D/P12C 受影响回归 **27 passed**；
2. 后端串行全量 **854 passed**，仅 1 条既有 Starlette/httpx 弃用告警；
3. `editor-state-revision-history` / checkpoint / technical+business truth 严格串行 **27/51/46 passed**；
4. `npx --no-install playwright test --workers=1 --retries=0` → **290 passed (8.3m)**；
5. `npm run lint`、`npm run build`、`git diff --check`、精确七文件与暂存区检查均通过。

技术标与商务标共用“查看正文差异”，只在点击时请求一次；摘要、当前对比、正文差异、恢复确认四意图互斥，项目/修订/折叠/刷新/恢复和组件卸载均隔离 arrived/complete 迟到结果。P12E-B/C 后续已接入双历史修订手动选择比较；仍未接正文自动恢复、自动批量比较、完整时间线、删除、搜索、分页、导出、分享和多人协作。

## P12E-B 双修订正文差异后端基础（已完成）

契约=`docs/p12e-revision-pair-body-diff-contract.md`、计划=`docs/plans/2026-07-17-p12e-revision-pair-body-diff-plan.md`，冻结=`00ef081`、实现=`5a5b08a`。目标是同一 workspace/project 两条历史修订的只读比较：`GET /api/projects/{projectId}/editor-state-revisions/{beforeRevisionId}/body-diff/{afterRevisionId}`。响应六键为 `sameBody/changedChapterCount/beforeChapterCount/afterChapterCount/truncated/items`，复用 P12E-A 的完整值扫描和有界 difflib 引擎。

Grok 实现白名单仅四文件：两个 schema/路由文件、正文差异服务和新后端专项测试；先真实 failure-first，再报告 `review_request`，不得提交/推送。最终 review_request=`msg_d8a128763e274c3b8eb12c6e1234d456`，Codex 验收回执=`msg_f7bd19cc0dae4834b275823a90c4a6f7`。

Failure-first 真实分解：13 项红测中 11 项为新路由不存在的 HTTP 404，1 项为同正文双修订夹具 `stateVersion` 重合导致 before/after ID 相同，1 项为 AST 断言缺少 `compare_revision_bodies`；夹具修正后 pair 专项 13 passed。独立串行验收：P12E-B/P12E-A/P12D-P12C **13/23/50 passed**，后端全量 **867 passed**，均仅 1 条既有 Starlette/httpx 弃用告警；合并专项 **86 passed**。`py_compile`、`git diff --check`、精确四文件与空暂存区均通过。

本包只交付后端双修订基础；前端双修订选择器随后已由 P12E-C 完成。分页、搜索、恢复、删除、导出、分享、缓存、跨项目历史、自动批量比较和多人协作仍未实现，后续包必须重新冻结契约与白名单。

## P12E-C 双修订正文差异前端选择与展示（已完成）

契约=`docs/p12e-revision-pair-frontend-contract.md`、计划=`docs/plans/2026-07-17-p12e-revision-pair-frontend-plan.md`。目标是在技术标/商务标共用修订面板中以内存选择两条不同历史修订，调用 P12E-B 唯一 pair GET 并展示有界结果。

冻结=`8b40bf4`、实现=`b6a4375`。Grok 白名单仅三个文件：`frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`、`frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`、`frontend/e2e/editor-state-revision-history.spec.ts`；没有修改后端、CSS、其它组件、依赖、路由、存储或 URL。选择不发请求；比较精确一次 GET、无 query/body，并关闭摘要/当前对比/单修订正文差异/恢复等旁路。

Failure-first 为真实 **3 failed / 0 passed**，首个失败是双修订选择按钮尚不存在；实现后 Grok 聚焦 **3 passed**。最终 review_request=`msg_fa38202aa5d641d5b111d914995d6f4f`，Grok 未提交/推送。

Codex 独立验收：P12E-C 聚焦 **3 passed**；P12E-A/P12D-B/P12C-C3 受影响 history 回归 **27 passed**；前端全量 **293 passed (8.2m)**，全部 `--workers=1 --retries=0`。`npm run lint`、`npm run build`、`git diff --check`、精确三文件与空暂存区均通过。仍未实现分页、搜索、自动批量比较、完整时间线、恢复/删除、导出、分享、缓存、跨项目历史、URL/浏览器存储和多人协作。

## P12F-A 修订有限保留扩容与总字节配额（已完成）

契约=`docs/p12f-revision-retention-quota-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-retention-quota-plan.md`，冻结=`e713fb3`、实现=`24f4cf2`。写入保留已改为最多 20 条且项目总快照最多 20 MiB；默认列表仍固定最近 10 条，既有 GET shape、顺序、详情、恢复和对比语义不变。

Grok 白名单仅两个服务和四个既有后端测试。真实 failure-first **9 failed / 0 passed**，首个业务失败为旧计数常量仍是 10；实现后聚焦 **9 passed**。Codex 首轮审查要求补强非法元数据失败后的精确零副作用测试，最终按契约序比较 `id/state_version/snapshot_bytes/source_kind/created_at`，不读取正文。

独立验收命令（后端；全部串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_p12c_revision_history_read.py tests\test_p12c_browser_put_revisions.py tests\test_p12c_revision_restore.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_task_revisions.py tests\test_p12c_revise_revisions.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_p12c_content_fuse_apply_revisions.py tests\test_p12c_content_fuse_consume_revisions.py tests\test_p12c_local_parser_callback_revisions.py tests\test_p12c_personal_callback_revisions.py tests\test_p12d_revision_current_comparison.py tests\test_p12e_revision_body_diff.py tests\test_p12e_revision_pair_body_diff.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q --tb=line
```

Codex 独立结果为六文件专项/受影响回归/后端全量 **121/134/871 passed**，均仅 1 条既有 Starlette/httpx 弃用告警；`py_compile`、`git diff --check`、精确六文件和空暂存区通过。消息追溯：首轮 review_request=`msg_63b19b98d56645bb98e96e0affd44524`，返修 task/review_request=`msg_72c9cee33d5446358a29aab701aa5909`/`msg_7fa5a6f3c971479aa8c2b65f7b37cdaa`，Codex 验收回执=`msg_4cd3242575cb4c5d865138415e57a028`。

本包未新增 API/schema/模型/迁移/前端，未回填已裁历史，也未实现游标分页、加载更多、搜索、删除、命名、固定、导出、分享、跨项目历史或多人协作。P12F-B 现在可以另行审计和冻结，但不得直接沿用本包白名单。

## P12F-B 后端修订游标页（已完成）

契约=`docs/p12f-revision-cursor-page-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-cursor-page-plan.md`。新增独立：

```text
GET /api/projects/{projectId}/editor-state-revisions/page
GET /api/projects/{projectId}/editor-state-revisions/page?cursor={opaqueCursor}
```

成功体精确 `items/nextCursor`，固定每页 10 条；查询只投影五列、`LIMIT 11`，按 `created_at DESC,id DESC` 键集分页。游标为 `esrc1_` 版本化规范 base64url，只含 UTC 微秒时间位置和修订 ID；非法固定 400 `editor_state_revision_cursor_invalid`。旧 `/editor-state-revisions` 顶层仅 `{items}`、最多 10 条及未知查询参数兼容语义必须完全不变。

冻结=`4ddd896`，实现=`c84a94d`。Grok 真实 failure-first **27 failed / 3 passed**：新静态页当时被动态 revision ID 路由吞掉；实现首轮专项 **30 passed**。Codex 审查后下发一次两文件返修，关闭 Windows `fromtimestamp` 最大年份风险、编码端 pre-1970 不可用游标以及 lookahead 恒真断言。最终覆盖 0/1/10/11/20、MIN/MAX/MAX+1、并列时间稳定、不重不漏、重复确定、非法游标矩阵、跨域、lookahead corrupt、五列/LIMIT 11、五域零写和旧列表回归。

SQLite 方言会把 `.limit(11)` 编译成 `LIMIT ? OFFSET ?`，但 OFFSET 绑定恒为 0；源码无 `.offset(`、无非零/主动偏移分页，也无 COUNT。Codex 独立新专项/受影响回归/后端全量为 **34/171/905 passed**，仅 1 条既有 Starlette/httpx 弃用告警；`py_compile`、diff-check、精确四文件和空暂存区通过。消息追溯：原任务/review_request=`msg_b044740a30cc4e82ac4c98c4c42731c4`/`msg_5df53113b2894ea984694c8d21d15601`，返修 task/review_request=`msg_628cbdef5bf24ac09f4f08d676f79d25`/`msg_6a45abaf4cc141d7bcf066c809b7a11f`，Codex 验收回执=`msg_6163277b22da433a8ae672560eeec3b5`。P12F-C 随后已独立冻结，见下节。

## P12F-C 前端修订加载更多（已完成）

前端首次展开、刷新和恢复后历史重载必须调用 P12F-B `/editor-state-revisions/page`，不能从旧 `{items}` 列表生成游标，也不能同时请求新旧列表。页响应严格精确 `items/nextCursor`，每页最多 10；游标只校验长度、`esrc1_` 前缀和 base64url 外壳，禁止解码或本地生成。

仅 `nextCursor` 非空时显示手动“加载更多”。成功按顺序追加且累计最多 20，跨页 ID 不得重复；失败保留原 items/cursor 和当前详情/比较意图，固定错误后允许同 cursor 重试。按钮需要同步单飞门；折叠、卸载、项目切换、刷新和恢复重载必须用独立代次隔离迟到分页，旧 finally 不得清新状态。

冻结=`bb1ae3e`、实现=`fe99f5a`。严格三文件为 `editorStateRevisionApi.ts`、`EditorStateRevisionPanel.tsx`、`editor-state-revision-history.spec.ts`；后端、workspace、hook、共享 `apiFetch` 与其它测试均未改。真实 failure-first **2 failed / 0 passed / 2 did not run**，生产文件在红测前未改；实现后聚焦/完整 history **4/34 passed**。

Codex 两轮审查分别关闭空 cursor 退化、假双击、宽泛计数、Cookie 漏检、禁止旁路未断言，以及任意方法 `/knowledge` 宽放行。最终双击在同一 JS 任务真实触发两次 DOM click，gate 前后当前 cursor 请求精确 1；知识侧栏只精确允许既有 `GET /api/knowledge/folders`。自然 UI 在 load-more 在途时真实禁用刷新/恢复；会话重载仍以独立代次防御性作废旧请求，不用 `force:true` 制造不可达并发。

Codex 独立 P12F-C/history/技术真值/商务真值/checkpoint 为 **4/34/28/18/51 passed**，前端全量 **297 passed（9.6m）**，lint/build/diff-check/精确三文件/空暂存区通过；build 仅既有大 chunk 警告。消息追溯：原任务/首轮回执=`msg_878d37c5db1946a59b7dcc70d605a4ea`/`msg_4fde9fc2e6454d00b7ae806f58a5b198`，返修 1=`msg_0dff84f4f11349da87ff8695ff105a36`/`msg_021c43c667e348948dfad51d6c927298`，返修 2=`msg_8bc571cf0bf544fe8206134e5ec43155`/`msg_319b7051f10f45089a18a1a77beb4d68`，Codex 验收回执=`msg_f83db79a50aa4e3d9e4aa65c9dcc9263`。

无限滚动、自动预取、搜索/筛选/删除、total/hasMore、页码、跨项目历史、多人协作和后端修改仍未进入 P12F-C；其中单一来源筛选已由 P12F-D 独立审计冻结，见下节。

## P12F-D 修订来源筛选（已完成）

契约=`docs/p12f-revision-source-filter-contract.md`、计划=`docs/plans/2026-07-17-p12f-revision-source-filter-plan.md`。只扩展既有 `/editor-state-revisions/page` 和技术/商务共用面板：无筛选继续 `esrc1 {i,t}`，精确单来源筛选使用 `sourceKind` 与绑定该来源的 `esrc2 {i,s,t}`；两版游标或来源不匹配固定 400，不得静默漏项。

后端继续五列投影、`LIMIT 11`、双键键集分页、no-store 和五域零写；前端只展示“全部来源”及九类固定中文标签，筛选分页仍手动加载且最多 20 条。刷新/恢复保留当前筛选，折叠保留、项目切换重置，旧筛选请求需 arrived+complete 迟到隔离。严格六文件，Grok 先形成后端/前端真实业务红测；Codex 审查、独立全量验收和提交推送。

冻结=`a2acdf3`、实现=`587df9a`。真实 failure-first 为后端 **38 failed / 17 passed**、前端 **2 failed / 0 passed / 1 did-not-run**。三轮审查关闭弱断言/SQL/AST 与前端失败保值和恢复在途证据、Cookie 漏检、`esrc2`+非法筛选错误优先级、精确 `LIMIT 11`/键集结构及最后一个 `assert A or B`。

Codex 独立后端专项/旧游标-C1 回归/全量 **68/48/986 passed**；前端 P12F-D/history/技术 truth/商务 truth/checkpoint/全量 **3/37/28/18/51/300 passed**。所有 Playwright 使用 `--workers=1 --retries=0` 串行；lint/build/py_compile/diff-check/精确六文件/空暂存区/弱断言零命中均通过。原任务/首轮回执=`msg_441102447c64467f8bd27a4d0b241d94`/`msg_f1f94a200185467c88f2f07ff626e896`，三轮返修 task/review=`msg_308b3e60e72b4cecaeb9853a6ee2f54f`/`msg_61426868c5454cb8b56b7a97362ef34a`、`msg_025f0d26538147b58e4949d08d459bfa`/`msg_21c4ff084afc4555a992c2fc37bb3b3e`、`msg_23a1993ce6334808b410aaf1e25faa98`/`msg_06291046a6494d508528c01378d85241`；验收=`msg_d977b2ead50b4f8292852c9b2de95b08`。

正文/标题搜索、日期或多来源组合、删除、命名、固定、自动加载、跨项目历史和多人协作仍不在 P12F-D。

## P12F-E-A 修订时间范围筛选后端（已完成）

契约=`docs/p12f-revision-time-range-filter-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-plan.md`。只扩展既有 `/editor-state-revisions/page`：`createdFrom` 为严格 UTC 毫秒包含下界，`createdBefore` 为严格 UTC 毫秒排除上界；任一边界存在时使用绑定上下界、可选来源和末条位置的规范 `esrc3 {b,f,i,s,t}`。无时间范围的 `esrc1/esrc2` 必须完全兼容。

冻结=`af3798a`、实现=`c66b69d`。Grok 严格三文件，真实 failure-first **74 failed / 12 passed**；Codex 首轮审查关闭 V3 双空/相等/倒置范围和末条位置越界可被接受，以及 SQL 上界断言被第二页 keyset 假满足的问题。返修后专项/受影响回归 **87/116 passed**。

Codex 独立直接复现非法/合法 V3 语义，并通过专项/受影响回归/后端全量 **87/116/1073 passed**；仅 1 条既有 Starlette/httpx 弃用告警。`py_compile`、diff-check、AST 弱断言扫描、精确三文件和空暂存区均通过。原任务/首轮 review=`msg_561a10fe93ac42f6b6d23fad0e897682`/`msg_233591eecb8043aa9450246bedab157f`，返修 task/review=`msg_45bd09a547014e49a8951276fb162016`/`msg_1d5bb5b639454405b87c4853f57e90fd`，验收=`msg_0533a4bab32448b0be8d5ec2b0ba1508`。

前端日期控件、浏览器本地时区转换、正文/标题搜索、来源多选、命名、固定、删除、自动加载、跨项目历史和多人协作不在 P12F-E-A。

## P12F-E-B 修订时间范围筛选前端（已完成）

契约=`docs/p12f-revision-time-range-filter-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-time-range-filter-frontend-plan.md`。只改 API 封装、技术/商务共用修订面板和既有 history E2E 三文件；后端保持 P12F-E-A `createdFrom/createdBefore/esrc3` 合同不变。

交互采用两个 `datetime-local` 草稿与明确“应用时间/清除时间”：按浏览器本地时区严格转换为 UTC 毫秒，允许单边，双边必须开始早于结束；无效草稿零请求并保留当前结果。来源切换、刷新、恢复和加载更多只读取已应用 UTC 条件；第二页显式重复来源/时间并原样回传 `esrc3`。折叠保留、项目切换重置，列表/加载更多/恢复在途全部时间控件真实禁用。

冻结=`a31e50e`、实现=`f9127ec`。真实 failure-first **0 passed / 2 failed / 1 did not run**；首个业务失败为时间控件不存在。Codex 首轮仅要求 E2E 返修：五处宽松计数改为精确基线增量、V3 257 字符真实进入 parser、第二页完整 query 精确比较、迟到 load-more 在同项目重开后验污；生产两文件哈希保持不变。

Codex 独立 P12F-E-B/history/技术 truth/商务 truth/checkpoint 为 **3/40/28/18/51 passed**，lint/build/diff-check/精确三文件/空暂存区通过。前端全量首轮在冻结范围外既有“双击确认恢复”测试发生第二个 click 偶发超时，真实结果 **294 passed / 1 failed / 8 did not run**；检查点独立 **51/51 passed**，不改代码、仍 `--workers=1 --retries=0` 完整复验 **303/303 passed（8.3m）**。消息追溯：任务/首轮回执=`msg_e3d1972aa28d442c92382f67e85003b0`/`msg_c322467045704332a69c55bf9d57ee94`，返修 task/review=`msg_aa86d5c6708c4b6fb7d0c7f7e917c5f2`/`msg_5c2808c3069d424c9714b5e7c7915255`，验收=`msg_489249aa6c264cc8a7125f07179b2d36`。

正文/标题搜索、来源多选、日期预设、自动加载、命名/固定/删除、跨项目历史、多人协作和后端变更均不在本包。其中搜索后端/前端随后已由 P12F-F-A/B 独立交付。

## P12F-F-A 修订可见内容搜索后端（已完成）

契约=`docs/p12f-revision-content-search-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-content-search-plan.md`。新增独立 `POST /api/projects/{projectId}/editor-state-revisions/search`，请求体精确携带必填 `query` 与可选 `sourceKind/createdFrom/createdBefore`；required 模式走既有 Cookie+CSRF。搜索词不得进入 URL、响应、错误、审计、日志、Cookie、文件或浏览器存储。

服务只查询元数据条件下最新 20 条候选，精确投影五键元数据加 `snapshot_json` 六列、双键倒序、`LIMIT 20`；全部候选先过既有 13 键/规范 JSON/版本/字节/来源/时间校验，再以 NFKC+casefold 连续字面匹配明确用户可见字段。禁止递归收集所有字符串、SQL LIKE/JSON/FTS、OFFSET/COUNT、全实体、N+1 详情、当前编辑态和第 21 条补扫。

成功只返回 `{items}` 和既有五键元数据，最多 20 条；不返回关键词、片段、命中字段、分数、快照、游标或总数。旧 list/page/detail 和未知 GET `search/q` 完全兼容。严格四文件：路由、Schema、history service、新专项测试；生产冻结哈希见契约。

冻结=`b2eed7c`、实现=`e6516e8`。真实 failure-first **18 failed / 3 passed**，首个业务失败 405。第一轮返修关闭默认 422 原始 input 泄漏、`businessQuote` 容器对象预算、真实禁止字段/SQL/CSRF/任务域等 11 类假绿；第二轮 test-only 关闭 8 项残余假绿。任务/回执和最终哈希见契约第 8 节。

Codex 独立串行专项/受影响回归/后端全量 **23/203/1096 passed**，全量 1658.59 秒；仅 1 条既有 Starlette/httpx 弃用告警。`py_compile`、直接 `git diff --check`、AST/弱断言、精确四文件、空暂存区通过；验收回执=`msg_554d0035e24d437086f3a1d14bbef1ad`。

联调时必须确认：合法搜索精确 200/五键；缺失/额外键固定脱敏 422；项目/来源/时间/关键词错误优先级不变；候选坏行整次失败；第 21 条不扫描；required Cookie+CSRF、角色和安全审计保持既有语义；旧 GET `search/q` 仍忽略。P12F-F-B 前端、游标/片段、数据库/索引/迁移、来源多选、日期预设、命名/固定/删除、跨项目搜索和多人协作不在 A 包。

## P12F-F-B 修订可见内容搜索前端（已完成）

契约=`docs/p12f-revision-content-search-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-content-search-frontend-plan.md`。严格只改 API 封装、技术/商务共用修订面板和既有 history E2E 三文件；P12F-F-A 后端 `e6516e8`、CSS、hook、配置、依赖和其它测试保持不变。

联调入口必须是显式“内容搜索”输入 + “搜索/清除搜索”：输入零请求，不静默 trim；合法动作单次 POST 且 URL 无 query，body 精确 query/sourceKind/createdFrom/createdBefore。搜索态结果最多 20、无游标/加载更多，空态和失败固定脱敏；清除恢复当前来源/时间 page 第一页。

来源/时间变化、刷新、恢复成功或重载失败、折叠重开均保持已应用关键词并重新 POST；项目切换清空并回到无搜索 page。page/search 迟到结果必须同时校验 session/query/source/from/before，旧 success/catch/finally 不污染新列表或 loading。

关键词只可留在输入值、组件内存和 POST body；URL、GET query、固定文案、页面其它文本、local/session/Cookie、console、剪贴板和其它请求均不得包含。三个新增 E2E 必须以精确请求增量、arrived/complete gate 和技术/商务共享真值验收；所有 Playwright 显式 `--workers=1 --retries=0`。

冻结=`4585388`、实现=`be2fe77`。真实 failure-first **3 failed / 0 passed / 0 did-not-run**，首个失败为搜索输入不存在。初始 task/review=`msg_3fb9225e60824153ac8b76d6d2c118de`/`msg_c69d1b022cea4d778db1edeee5da5546`；受限 E2E-only 返修 task/review=`msg_76277425992e4369a1476bdcbe9829c1`/`msg_6722f22970184a0981eb07d6d2997951`，验收=`msg_14c421e3a1c2498985c41ed026e84fdf`。

返修必须保留真实证据：顶层 extra、元数据缺键/extra、重复 ID、21 项超限均进入严格 parser；DEL/C1、64 个 astral 码点合法与 65 个非法；A 搜索 parser `catch` 与 B page loading 真实重叠，旧 `finally` 不得清新 loading。Codex 独立串行通过聚焦/history/技术 truth/商务 truth/checkpoint/后端专项/前端全量 **3/43/28/18/51/23/306 passed**，lint/build/diff-check/精确三文件/空暂存区/禁区扫描通过。

最终 SHA-256：API=`4EB053C284A6F4059D559842B3A6C5C0AF829BDF08E26A8528E0760B0B02D433`，面板=`524D5AC6D494736492E4A18385DEE74C7F7547129888E322808548A17F8F81FF`，history E2E=`D7BFAE7EDD61747DE790FDC188E9C61959E93529AA1093F514E1B6BBCC7D63BB`。自动搜索/防抖、片段/高亮/分数、搜索历史/缓存、搜索游标/跨项目搜索、来源多选/日期预设、命名/固定/删除、导出/分享、多人协作和 SSE 扩展仍须另包。

## P12F-G-A 单条修订删除后端（已完成并推送）

契约=`docs/p12f-revision-delete-backend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-delete-backend-plan.md`，冻结=`c176cb5`、实现=`d2555d4`。唯一入口为无 query/body 的 `DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}`；required 模式必须有效 bid_writer Session + 当前工作空间成员 + CSRF，disabled 保持本机兼容。

联调必须确认：成功严格空 204/no-store；query/body 固定脱敏 422；项目/跨空间与修订/跨项目分别固定脱敏 404；数据库执行/flush/commit 故障 rollback 后固定 500。服务先只投影 Project.id，再以 workspace/project/id 三谓词删除恰好一行，禁止读取 snapshot/current editor-state/checkpoint 或范围删除。

删除后目标不再出现于 list/page/search，detail/comparison/body-diff/restore 固定 404；其它修订顺序、当前 editor-state、矩阵、检查点和五域状态不变，后续真实 transition 仅按既有 P12F-A 配额自然占位。前端确认/重载、多选/批量/软删除/回收站、命名/固定、检查点删除、跨项目历史和多人协作不在 A 包。

真实 failure-first **10 failed / 3 passed / 0 did-not-run**，首个业务失败为 405。首轮并行 restore/auth 污染共享 SQLite，相关结果废弃；原四文件冻结还遗漏了与新 DELETE 必然冲突的旧 history 写路由守卫，最终仅把该守卫单一函数纳入第五受限文件。Codex 返修关闭 `rowcount=None` 错映射、宽状态/条件分支、SQL“至少一次”、任务空占位、事务恒零计数和搜索子集等假绿。

自动化验收（后端，全部逐组串行，禁止 xdist）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12f_revision_delete.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py tests\test_p12f_revision_cursor_page.py tests\test_p12f_revision_content_search.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_restore.py tests\test_editor_state_revisions.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_auth_rbac.py
.\.venv\Scripts\python.exe -m pytest -q
```

Grok 与 Codex 最终均通过 **14/71/93/39/1110 passed**；Codex 独立全量 1620.30 秒，仅 1 条既有 Starlette/httpx 弃用告警。`py_compile`、`git diff --check`、精确五文件、空暂存区与最终 SHA-256 全部通过。P12F-G-B 只可另包实现前端确认、加载态、成功重载、失败保留和迟到隔离。

## P12F-G-B 单条修订删除前端（已完成并推送）

契约=`docs/p12f-revision-delete-frontend-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-delete-frontend-plan.md`。三文件白名单为 API、技术/商务共用面板、history E2E；冻结哈希分别为 `4EB053C...B02D433`、`524D5AC...F81FF`、`D7BFAE7...63BB`。共享 `apiFetch` 已支持 204 与 DELETE CSRF，禁止修改后端、共享请求层或 workspace hook。

联调必须确认：默认、点击删除、取消均零 DELETE；固定内联确认后精确一次 DELETE，query 为空、body 为空、无重试；成功固定中文并重载第一批，普通态 GET page，搜索态保留 query/sourceKind/createdFrom/createdBefore 后 POST search；失败列表、草稿和已应用条件保值且零重载。

确认和执行期间折叠、筛选、搜索、刷新、加载更多及全部行操作真实 disabled；进入确认先清摘要/当前对比/body-diff/pair/restore 意图。A→B 双 gate 必须证明旧 DELETE 和旧重载的 success/catch/finally 不污染 B 或清除 B busy。editor-state GET/PUT、restore、checkpoint create、外网、URL/存储/Cookie/console 泄漏均为零。

冻结=`89b5728`、实现=`bb7c4f4`。真实 failure-first **3 failed / 0 passed / 0 did-not-run**，首个业务失败为列表加载后删除按钮缺失。两轮受限返修关闭旧闭包项目校验、成功后重载失败缺口、宽松 OR/首项/`Math.min` 假绿，并补齐 query+sourceKind+createdFrom+createdBefore 组合失败与恢复。

Codex 独立串行通过聚焦/history/checkpoint/技术 truth/商务 truth/前端全量 **4/47/51/28/18/310 passed**；lint、build、diff-check、精确三文件、空暂存区、最终哈希与静态禁区门通过。所有 Playwright 必须继续显式 `--workers=1 --retries=0` 串行运行，禁止并行命令。

## P12F-H 单条修订命名（已完成并推送）

契约=`docs/p12f-revision-display-name-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-display-name-plan.md`。初始冻结=`0660145`，两次范围修订=`0db935b`/`aca68b6`，实现=`b4338ba`。最终十七文件包括 ORM/SQLite 加列/schema/路由/history/name service/新专项、共用 API/面板/history E2E、history/page/source/time/search/delete 六份机械元数据同步，以及 Codex 回归暴露的真实 SQLite 精确列集合基线。

联调必须确认：存量与新修订默认 `displayName=null`；合法名称保存、覆盖、清除均精确一次 PATCH，响应一键/no-store；非法输入、query、extra、跨空间、角色和 CSRF 固定脱敏。list/page/search/detail 六键一致，名称不改变排序、游标、搜索命中、快照或裁剪。

前端输入/取消零请求，成功原位更新且零 page/search 重载，失败保留原名称；命名与摘要/对比/body-diff/pair/恢复/删除/刷新/筛选/加载更多互斥。A→B hold 必须证明旧 success/catch/finally 不污染或解锁 B；名称仅以 React 文本显示，URL/存储/Cookie/console/错误/外网零泄漏。固定/置顶、裁剪保护、名称搜索、批量和检查点命名不在本包。

真实 failure-first 为后端 **30 failed / 0 passed / 0 errors**、前端 **3 failed / 1 passed**。四轮受限审查关闭精确错误与事务/迁移、严格类型、真实 CSRF、失败保值、迟到围栏、Cookie 泄漏和精确列基线。Codex 后端独立串行 **30/240/132/1140 passed**；前端 **5/52/51/28/18/315 passed**，lint/build/py_compile/diff/十七文件/哈希/静态禁区均通过。首轮全量唯一 P8B 瞬时导航失败为 **314 passed / 1 failed**，独立 **1 passed** 后无代码变更完整复验 **315/315 passed**。Playwright 继续强制 `--workers=1 --retries=0`。

## P12F-I 修订名称与可见内容联合搜索（已完成并推送）

契约=`docs/p12f-revision-display-name-search-contract.md`、计划=`docs/plans/2026-07-18-p12f-revision-display-name-search-plan.md`，冻结=`060191e`，实现=`008e443`。严格四文件为 history search service、既有内容搜索专项、共用修订面板和 history E2E；未改路由、Schema、API 封装、模型、迁移、索引或依赖。

联调必须确认：合法关键词对非空展示名称使用与内容相同的 NFKC+casefold 连续字面匹配；名称或内容任一命中即返回，同一修订只出现一次并保持候选倒序。SQL 仍七列、固定 `LIMIT 20`，第 21 条不补扫；全部候选必须先完整验证，名称已命中也不能掩盖坏快照或坏元数据。

技术标/商务标共用“名称或内容搜索”，请求仍精确一次既有 POST，来源/时间组合、刷新、恢复、删除重载、清除和迟到隔离不变。名称只作 React 文本；关键词/名称不得进入 URL、存储、Cookie、console、错误或外网。固定/置顶、裁剪保护、搜索片段/高亮/评分/游标/缓存、自动搜索与跨项目搜索不在本包。

真实 failure-first 为后端 **5 failed / 1 passed**、前端 **2 failed / 1 passed**。Grok 最终 review_request=`msg_82cd1e26df03413389a92604830cdb9c`，未暂存、提交或推送；Codex 验收回执=`msg_d954063f489248babb027b9bb335f666`。Codex 独立串行通过后端专项/兼容/全量 **29/247/1146 passed**，前端 P12F-I/history/checkpoint/技术 truth/商务 truth/全量 **3/55/51/28/18/318 passed**，lint/build/py_compile/diff-check/精确四文件/空暂存区/最终 SHA-256 和静态禁区均通过。

## P12F-J-A 修订固定与裁剪保护后端基础（已完成）

契约=`docs/p12f-revision-pinning-backend-contract.md`、计划=`docs/plans/2026-07-19-p12f-revision-pinning-backend-plan.md`，冻结=`2f03b8c`，实现=`a7021c4`。Grok review_request=`msg_88f4752ef1cf4a929c6b194df00d9398`，Codex ack=`msg_c630805296ac48d6941809bbca957b7f`。选择该包是因为固定/置顶会改变 P12F-A 的连续最新前缀，必须先独立冻结固定数量/容量、项目级锁、保护性裁剪和失败回滚；名称排序、检查点命名、前端固定入口与七键历史响应均不混入。

最终生产边界：`is_pinned BOOLEAN NOT NULL DEFAULT 0` 与 SQLite 0/1 CHECK 迁移；固定最多 5 条且固定快照总和最多 10 MiB；新增单条 `PATCH /api/projects/{projectId}/editor-state-revisions/{revisionId}/pin`，请求精确 `{isPinned:boolean}`，成功一键 200/no-store，超限 409；裁剪先完整校验所有 `snapshot_bytes/is_pinned`，以 `type_coerce(Integer)` 保留原始坏值并拒绝非法元数据，保留全部固定行与最新非固定前缀，总配额仍为 20 条/20 MiB。显式 DELETE 固定行仍允许，list/page/search/detail 继续精确六键。Grok/Codex 独立串行 **16/96/1/1165 passed**；py_compile、diff-check、九文件边界、空暂存区、迁移失败回滚、坏值零写和无正文投影静态门通过。实现已提交推送，J-B 才能扩展七键、前端 parser/UI 与 E2E。

联调门已通过：Grok 先只写两个后端测试文件形成真实 failure-first，再实现生产六文件；机械字段守卫作为第九文件受限同步。Codex 严格串行跑 pin 专项、核心修订/恢复回归、删除守卫、后端全量、py_compile、SQL/AST/错误脱敏/零写/精确九文件与空暂存区。J-B 才能扩展 `isPinned` 元数据、API parser、技术/商务 UI 和 E2E。

## P12F-J-B 修订固定状态七键响应与前端入口（已完成）

契约=`docs/p12f-revision-pinning-frontend-contract.md`、计划=`docs/plans/2026-07-19-p12f-revision-pinning-frontend-plan.md`；冻结=`f019a4b`，实现=`5ef7abd`，Codex ack=`msg_8399a348aa1543e2b4b61cbdd25b4ac9`。

联调必须逐项确认：

1. list/page/search 每项精确七键，detail 精确七键加 snapshot；`isPinned` 只接受原生布尔。SQLite 原始 `is_pinned=2` 在 list/page 第 11 条/search 未命中候选/detail 中均固定脱敏失败且零写。
2. 四类 history SQL 使用 `type_coerce(..., Integer).label("is_pinned")`；list/page 不读正文，search 仍 20 候选，page 仍 10+1，排序、来源/时间、V1/V2/V3 游标和名称或内容联合匹配不变。
3. 前端固定/取消固定各精确一次 PATCH，URL 无 query，body/成功响应均精确 `{isPinned:boolean}` 且同值；required 模式 Cookie+CSRF 仍由共享请求层提供。
4. 成功只原位更新目标固定标记与按钮，列表顺序、游标、来源、时间、关键词、名称和其它操作意图不被重载或重置；失败固定中文并保留全部状态，零重试。
5. 双击/连续点击单飞；在途时折叠、刷新、筛选/搜索/时间、加载更多及全部行操作真实 disabled；技术/商务共用同一入口。A→B 双 gate 证明旧 success/catch/finally 不污染 B 或解锁 B。
6. revisionId/stateVersion/snapshot/cursor/关键词/名称/后端错误/CSRF 不进入 DOM、URL、存储、Cookie、console 或外网；Playwright 全部显式 `--workers=1 --retries=0` 串行。

本包不含固定排序、批量固定、数量/容量展示、乐观更新、自动重试、检查点命名、收藏/标签、导出/分享、跨项目历史或多人协作。

验收结果：后端专项/全量 **297/1170 passed**（全量仅 1 条既有弃用告警），Python 编译通过；P12F-J-B 定向/history/checkpoint restore/技术 truth/商务 truth **6/61/51/28/18 passed**，lint/build 通过。Grok 并发自测轮已作废；有效结果均由 Codex 单 worker、零重试、逐条串行取得。最终严格十四文件、空暂存区、diff-check、四处原始 Integer 投影和哈希门全部通过。

## P12G 手动检查点展示名称（已完成并推送）

契约=`docs/p12g-checkpoint-display-name-contract.md`，计划=`docs/plans/2026-07-19-p12g-checkpoint-display-name-plan.md`，冻结=`9696ec1`，实现=`077e7d4`。严格十二文件已为检查点表增加 nullable `display_name`、独立单列 PATCH、create/list/detail 七/七/八键元数据，以及技术标/商务标共用面板原位命名入口。

联调必须逐项确认：

1. 旧库幂等迁移、新库建表和迁移失败回滚；存量、手动新建及恢复前安全检查点默认 `displayName=null`。
2. PATCH 无 query、body≤1024 且精确 `{displayName:string|null}`；NFKC 后 1–40 码点，拒绝首尾空白、C0/C1/DEL、U+2028/U+2029 和双向控制字符；成功一键/no-store。
3. 项目只投影 ID；UPDATE 同时限定 workspace/project/checkpoint 且只写 `display_name`；rowcount 仅 0/1 有业务语义，所有故障 rollback，commit 后零查询。
4. create/list/detail 精确七/七/八键；列表仍不读正文，顺序和 20 条裁剪不变；恢复不复制名称、不改变四键恢复响应。
5. 技术/商务保存、覆盖、清除和取消；成功只原位更新，失败保值；双击单飞、全操作互斥、A→B success/catch/finally 迟到隔离。
6. 名称、checkpointId、stateVersion、错误和 CSRF 不进入 URL、存储、Cookie、console 或外网；全部 Playwright 显式 `--workers=1 --retries=0` 串行。

联调门已全部通过。初始后端 failure-first **37 failed / 25 passed**，Codex 四文件受限返修又形成后端 **1 failed** 与前端 **1 failed / 4 passed / 3 did-not-run**，关闭缺键掩盖、假单飞和 A→B 假重叠。Codex 独立串行结果为后端 **62/47/1203 passed**，前端 **8/59/61/28/18 passed**；lint、build、py_compile、diff-check、十二文件、空暂存区与 SHA-256 门均通过。消息追溯：原任务/review=`msg_a30143a9cd0743e5bc20589ccd941759`/`msg_1b3e0ffcfc164586a641c4c70669f058`，返修 task/review=`msg_ef6e51ac93f849a9bf58d4699519da48`/`msg_f472fcf56377451a8c92c5dbc7b69031`，Codex ack=`msg_cd2908a39cc1438186b0f41d13062443`。整仓前端沿用已验收 **318 passed** 基线，未重复扫描不受影响套件。

检查点搜索/排序/固定/删除/下载/分享、创建时命名、跨项目检查点、完整时间线、多人协作、审计扩展和 SSE/WebSocket 不在本包。

## P12H 单条检查点删除（已完成并推送）

契约=`docs/p12h-checkpoint-delete-contract.md`，计划=`docs/plans/2026-07-19-p12h-checkpoint-delete-plan.md`；冻结=`b81546e`、实现=`1ff8839`。严格七文件且未改模型、数据库、Schema、核心检查点/恢复服务、页面/hook、共享请求层或修订历史。

联调必须逐项确认：

1. `DELETE /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}` 无 query/body；成功空 204/no-store；任意非空 body 固定 422 且不反射输入。
2. required 只允许当前活动 workspace 的 bid_writer 并通过 DELETE CSRF；项目/检查点/跨 workspace/project 固定脱敏 404。
3. 服务只 `SELECT Project.id`，DELETE 同时限定 workspace/project/checkpoint；rowcount 仅 0/1 有业务语义，execute/flush/commit 失败 rollback，commit 后零查询。
4. 删除目标后其它检查点完整字段、当前 13 键、自动修订、项目、任务和旁路空间/项目均不变；不创建安全检查点或修订，不改变排序/20 条裁剪。
5. 技术/商务共用固定确认文案；确认前/取消零请求；`disabled=true` 仍可删除；同一任务双击精确一次 DELETE。
6. 成功只原位移除目标、零 list/detail/restore/create/editor-state/revision 重载；失败保留列表与确认可重试。
7. 确认/在途期间 toggle、刷新、创建、恢复、命名和其它删除真实 disabled；A/B 双 hold 证明旧 success/catch/finally 不污染、移除或解锁 B。
8. checkpointId/stateVersion/displayName/错误/CSRF 不进入 DOM、query、存储、Cookie、console、下载、剪贴板或外网；Playwright 固定 `--workers=1 --retries=0` 串行。

联调门已全部通过。首轮 Grok 在正式回执前因 402 中断，failure-first 数量无可复核记录且未补造；Codex 首轮真实前端结果为 **8 passed / 1 failed**，并以代码审查关闭空体弱 OR、假 `disabled=true`、console/原始错误泄漏证据不足和恢复确认可被删除抢占。返修任务=`msg_732b2095aa73484bbcc56572d5ab4a08`/`msg_64032847642b43008dcfc683c736029a`，Grok review=`msg_92c05eeb3bba4dd1801470646e74084d`，Codex ack=`msg_c7168985bed9415ab1fc44420474d857`。

Codex 独立串行通过后端 P12H/受影响回归/全量 **43/80/1217 passed**，前端 P12H/checkpoint/history/技术 truth/商务 truth **9/68/61/28/18 passed**；lint、build、py_compile、diff-check、严格七文件、空暂存区和最终哈希均通过。整仓前端沿用已验收 **318 passed** 基线，本包未重复运行不受影响套件。

批量/软删除、撤销/回收站、自动清理、审计、固定/置顶与保护裁剪、搜索/排序、跨项目检查点、跨客户端互斥、多人协作、presence、SSE/WebSocket 不在本包。

## P12I 检查点名称与可见内容显式搜索（已完成并推送）

契约=`docs/p12i-checkpoint-search-contract.md`，计划=`docs/plans/2026-07-19-p12i-checkpoint-search-plan.md`；冻结=`86cc1a3`、实现=`8c41bbc`。严格六文件且未改模型、数据库、Schema、命名/删除服务、核心 editor-state/修订服务、页面/hook、共享请求层或其它 E2E。

联调必须逐项确认：

1. 唯一 POST search 无 query，body 精确 `{query}`；非法外壳 422、非法关键词 400、项目 404、损坏候选 500，均固定中文/no-store/不反射。
2. 后端八列投影、workspace/project、倒序、LIMIT 20；先完整重验全部候选名称与规范快照，再按 NFKC+casefold 匹配名称或可见内容；不扫第 21 条、不写库。
3. 成功顶层精确 `{items}`，每项仍七键元数据，零 snapshot/片段/评分/ID 扩展；双命中去重且顺序不变。
4. 技术标/商务标共用显式输入；编辑零请求，按钮/Enter 一次 POST，同值零重发，清除一次 GET，不做自动搜索或客户端快照过滤。
5. active search 下刷新/创建/恢复只重发同一 POST；命名成功原位更新、删除成功原位移除，保持 P12G/P12H 既有合同。
6. 搜索与 list/create/restore/name/delete/toggle/确认态真实互斥和 disabled；同任务双触发真单飞，A→B 迟到 success/catch/finally 不污染或解锁 B。
7. 失败保留结果/输入可重试；关键词、名称、ID、版本、快照、原始错误与 CSRF 不进入 URL、存储、Cookie、console、异常、剪贴板、下载或外网。
8. pytest 禁止 xdist/并发分组；Playwright 全部 `--workers=1 --retries=0` 逐条串行，禁止同时启动后端与前端测试。

联调门已全部通过。Grok 首轮 review=`msg_58a1a28887534e02bd4497bb12dec3da`；Codex 审查发现两项生产缺陷和多项反假绿缺口，受限返修 task/review=`msg_69b8bb73702945b3a4f0b3ebd26c942a`/`msg_2a430c560a4d415d881a4fd58911ad9d`。两项真实返修红测先为 **2 failed**，修复后 **2 passed**；Grok 全程未暂存、提交或推送。

Codex 独立串行通过后端 P12I/五文件检查点回归/全量 **18/123/1235 passed**，前端 P12I/checkpoint/history/技术 truth/商务 truth **8/76/61/28/18 passed**；lint、build、py_compile、diff-check、严格六文件、空暂存区与最终哈希均通过。验收回执=`msg_608e5dda4d59453b83ab068ce9879fbf`；整仓前端沿用已验收 **318 passed** 基线，本包未重复运行不受影响套件。

固定/保护裁剪、排序、分页/游标、片段/高亮/评分、自动搜索/缓存、批量、跨项目检查点/搜索、完整时间线、跨客户端互斥、多人协作、presence、SSE/WebSocket 不在 P12I。

## P12J-A 检查点固定与保护裁剪后端基础（已完成并推送）

契约=`docs/p12j-checkpoint-pinning-backend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-backend-plan.md`，冻结=`9f304da`、实现=`8edebd4`。本包严格九文件，只做后端列/迁移/PATCH/配额/裁剪；P12J-B 响应与前端不在本包。

联调验收项（全部通过）：

1. 新库及旧 SQLite 表最终都有 `is_pinned BOOLEAN NOT NULL DEFAULT 0` 与 0/1 CHECK；旧行归零，已有有效 0/1 保留，迁移中途失败完整回滚且不残留临时表。
2. `PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/pin` 无 query，原始 body ≤1024 字节且精确 `{isPinned:boolean}`；成功/错误 no-store、固定脱敏，required `bid_writer`/CSRF 与 disabled 兼容均不回退。
3. 每项目固定上限 5 条/10 MiB；锁后原始 Integer 投影完整校验，候选第 21 行、非法固定值/字节、execute/flush/commit 均固定失败并零写。
4. 手动创建保留全部固定行和新检查点；恢复同时保留全部固定行与本轮安全检查点；并列时间戳/不利 ID 不得淘汰安全点，旁路项目/空间不受影响。
5. 显式 P12H DELETE 仍允许删除固定行；取消固定不立即裁剪，后续创建/恢复才按普通时间顺序处理。
6. create/list/search 继续七键、detail 八键且无 `isPinned`；前端没有新按钮/标签，排序、搜索、命名、恢复、修订历史合同不变。
7. pytest 逐条串行，禁止 xdist/并发分组，且本后端包不运行或修改 Playwright；最终须通过新专项、受影响回归、后端全量、py_compile、diff-check、九文件/哈希/空暂存门。

Grok 初始 failure-first **16 failed / 3 passed**；首轮专项/受影响回归/全量 **19/140/1254 passed**。Codex 审查下发返修 task=`msg_f9bc9783042748b9bad6125c529081c1`，先得到 **2 failed / 0 passed**，修后专项/回归 **23/140 passed**；关闭不完整迁移 no-op、空候选 `protect_id` 和真实 5+15 边界缺口。Codex 最终独立串行通过专项/受影响回归/后端全量 **23/140/1258 passed**，全量 **1454.53 秒**，仅 1 条既有弃用告警；py_compile、diff-check、精确九文件、空暂存区、最终哈希与安全静态门均通过。本包未运行 Playwright，前端沿用 **318 passed** 基线。

## P12J-B 检查点固定状态八/九键响应与前端入口（已完成并推送）

契约=`docs/p12j-checkpoint-pinning-frontend-contract.md`，计划=`docs/plans/2026-07-19-p12j-checkpoint-pinning-frontend-plan.md`，代码哈希基线=`262683e`、冻结=`65fe259`、口径澄清=`1471c31`、实现=`7d1d5c9`；严格十一文件。

联调验收项（全部通过）：

1. create/list/search 元数据精确八键，detail 精确九键；`isPinned` 为原生 boolean，create 与安全检查点初始 false，缺失/额外/数字/字符串/null 均拒绝。
2. list/detail/search 三处原始 Integer 投影；原始 `is_pinned=2` 在 list、detail、search 未命中候选均固定 corrupt、no-store、零写；list 无正文，搜索候选/匹配/顺序不变。
3. 前端 pin URL 无 query，body/响应精确 `{isPinned:boolean}` 且响应等于目标；Cookie/CSRF 由共享请求层处理，零重试、零旁路。
4. 技术/商务共用“固定/取消固定”和“已固定”；全局同步单飞，在途时 list/search/create/restore/name/delete/toggle 与所有确认/取消真实 disabled。
5. 成功只原位更新目标固定状态，active search 的关键词/结果/顺序和名称不变，零重载；失败全部保值并固定中文。
6. mounted/session/generation/project/checkpoint 五重围栏；旧 A success/catch/finally 不污染或解锁 B，新 B 在 A 完成前必须真实 arrived 且保持 gate。
7. checkpointId/stateVersion/snapshot/名称/关键词/原始错误/CSRF 不进入新增 DOM、URL、存储、Cookie、console、剪贴板、下载或外网。
8. pytest 与 Playwright 全部逐条串行；最终通过后端受影响集/全量、P12J-B 定向/checkpoint/history/技术/商务、lint/build/py_compile、十一文件/哈希/空暂存区。整仓前端沿用 318 基线，不重复冒充本包结果。

Grok 初始任务/review=`msg_b78f8a9474cd470bbd1507aa141ba6c4`/`msg_b86ca88d69b74be89c556aa83d8fa7ed`，真实 failure-first **6 failed**，生产哈希保持冻结。Codex 下发单文件 E2E 返修=`msg_0912b706fd844359a335f046eae1f1fc`，补齐另一行同拍、旧 A catch/finally 与 active search 多结果顺序/保值证据；Grok 未执行 Git 写操作。返修回执写本地 JSONL 时因桥接会话占用失败，完整隐藏日志已由 Codex 审计并以 `msg_98239bfc61c743d1b7b44d7fec15a975` 确认验收。

Codex 独立串行通过后端受影响集/全量 **120/1261 passed**，前端 P12J-B/checkpoint/history/技术/商务 **6/82/61/28/18 passed**；py_compile、lint、build、diff-check、严格十一文件、空暂存区、最终哈希与静态门均通过。Grok 返修自测的 history 首轮曾有一次既有双击确认元素 detached：**1 failed / 44 passed / 16 did not run**；未改代码重跑和 Codex 独立首轮均 **61 passed**，记为非阻断稳定性风险。整仓前端沿用 **318 passed** 基线，未冒充本包重跑。

## P12K 检查点固定优先默认列表（已完成）

契约=`docs/p12k-checkpoint-pinned-first-list-contract.md`，计划=`docs/plans/2026-07-19-p12k-checkpoint-pinned-first-list-plan.md`，代码审计基线=`90cfd58`、契约冻结=`fe0fa08`、启动口径修订=`ff48495`/`6666af6`、实现=`3c3cbf9`；严格两文件。

完成后的联调验收项：

1. 默认 GET 列表精确 `is_pinned DESC,created_at DESC,id DESC`；固定组优先，组内时间/ID 稳定倒序，仍最多 20 条八键元数据且 SQL 不读正文。
2. PATCH 固定/取消后，下一次默认 GET 分别上移/回归时间位置；PATCH 不自动 GET，P12J-B 当前列表仍原位更新，前端零修改。
3. search 继续最新 20 条 `created_at DESC,id DESC`；旧固定第 21 条即使命中也不进入候选，多项结果不改为固定优先。
4. 原始非法 `is_pinned=2` 仍固定 corrupt/no-store/五域零写；其它项目/空间不参与列表或排序。
5. 表/迁移/模型/Schema/API/pin service/配额/裁剪/create/detail/search/restore/name/delete/修订/前端/依赖全部冻结。
6. pytest 逐条串行；Grok 通过 P12K 专项/六文件受影响集/后端全量 **12/132/1273 passed**，Codex 独立通过六文件受影响集 **132 passed in 106.74s**，并完成 py_compile、diff-check、精确两文件、空暂存区、最终哈希和 list/search 独立 AST/SQL 门。本包不运行 Playwright，沿用 checkpoint **82** 与整仓 **318 passed** 基线。

真实 failure-first **8 failed / 4 passed**，首个业务失败为旧固定项仍排在新普通项之后；测试夹具 `Workspace` 构造错误在生产修改前先修正，不计作业务红测。Grok 初始 task/review=`msg_24d08a0202954060b4c4ab3b0a35942d`/`msg_131b165976c64b2fb05ceb0792122a5c`，test-only 返修 task/review=`msg_b1b3d1fb809c4a579ed35dfd9a875615`/`msg_4e2f742d8ac2469fad123e367922f6fa`，Codex 验收=`msg_3048a39db0c04969978a7e2dd7ea0c60`。最终哈希：生产服务=`8C08B546E0DB8FA00FE4D6E15FB93A23650F15FA12C42E23EC100ED6EA7E371E`，专项测试=`49A6FEA0F2C08FF44E9E7CC57FC216A967B03EFCF6DA6ED78624DDC573821591`。

后续验收采用分级策略：Grok 默认运行专项/受影响集，Codex 独立复核并按迁移、鉴权、共享状态、跨域范围和回归信号决定是否补一次全量；不再让双方机械重复同一全量。所有动态测试仍必须串行，Playwright 继续 `--workers=1 --retries=0`。

## P12M 修订搜索命中来源标签（已完成）

契约=`docs/p12m-revision-search-match-reasons-contract.md`，计划=`docs/plans/2026-07-20-p12m-revision-search-match-reasons-plan.md`，冻结=`95b298f`、实现=`cc23542`；首轮严格七文件，另有两份获 Codex 明确授权的 test-only 兼容测试。

冻结后的联调验收项：

1. 搜索项精确八键：既有七键 + `matchReasons`；数组只能是非空、无重复、固定顺序 `displayName` 与/或 `visibleContent`。list/page/detail 旧键集不变。
2. 后端真实验证名称、可见内容、双命中、未命中、20 条候选/第 21 条排除、来源/时间过滤、坏候选先校验、非法响应和五域零写；不返回正文/关键词/命中值。
3. 前端搜索 parser 严格拒绝缺失/额外/未知/重复/乱序原因；技术/商务共用面板显示固定中文“命中：名称/可见内容”，不高亮、不显示片段、不新增请求。
4. 搜索一次 POST、顺序、清除/刷新/筛选/项目切换迟到隔离和 URL/storage/Cookie/console 零泄漏保持不变。
5. Grok 串行运行后端搜索专项/受影响回归、P12M history 聚焦/受影响 E2E、lint/build/py_compile；Codex 独立复跑专项与聚焦，不重复后端全量或整仓 318 E2E。

真实 failure-first **3 failed / 0 passed**。Grok 搜索专项 **33 passed**，P12M/既有受影响 history E2E **2/6 passed**，lint/build/py_compile 通过；受影响后端首轮 **265 passed / 2 failed**，两条失败均为旧 name/pin 测试仍断言 search 七键。Codex 授权 test-only 返修后，两条定点各 **1 passed**，P12M 后端 **3 passed / 30 deselected**。

Codex 独立串行通过两条兼容回归各 **1 passed**、P12M 后端 **3 passed / 30 deselected**、P12M/既有受影响 history E2E **2/6 passed**，并通过 lint、py_compile、diff-check、精确九文件、空暂存区、哈希和泄漏门。Grok 未执行 Git 写操作，Codex 验收=`msg_935e7f7b28df4a8ab75227d6e124b2f1`。本包未运行后端全量或整仓 318 E2E。

## P12N 已加载修订固定优先前端（已完成）

契约=`docs/p12n-revision-loaded-pinned-first-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12n-revision-loaded-pinned-first-frontend-plan.md`，冻结=`337b401`、实现=`394639a`；严格两文件。

冻结后的联调验收项：

1. 默认/来源筛选/时间筛选的当前已加载修订以 `isPinned === true` 稳定分组，固定项在前，两组内保持服务端原顺序；禁止原地 sort。
2. pin/unpin 成功即时重排且零额外 GET/search/editor-state；失败保值。加载更多后第二页固定项进入已加载固定组，去重/20 条/游标不变。
3. active search 完全保持服务端顺序和 P12M `matchReasons` 索引；技术/商务共用，全部动作仍按 revisionId 命中。
4. 纯 render 派生，零新 state/effect/ref/API/缓存/存储/定时器；A→B 迟到隔离和全局单飞不变。
5. Grok 只跑 P12N 与分页/固定/搜索受影响 history、lint/build；Codex 独立 P12N 聚焦与静态门。不跑后端、完整 history 或整仓 318 E2E。

真实 failure-first **4 failed / 1 passed**；Grok 串行 P12N/受影响 history **5/12 passed**，lint/build 通过；Codex 独立 P12N **5 passed in 9.4s**、lint、差异/哈希/纯派生门通过。最终 SHA-256：面板=`FEAD15B6CB4043D1E6A96C1BFF9782A3B1F072A28D6619E375D9B5F07A23FF3B`，E2E=`617C7481B55A2F7760A36127E5E5DB8C50E193526206D444F13D56AA6F65698F`。Grok 未 Git 写操作，Codex ack=`msg_77a0632fdf5e4eb5bd21ea9e32205430`。本包未运行完整 history、整仓 318 E2E 或后端 pytest。

明确限制：尚未加载的旧固定修订不会提前进入第一页；服务端 list/page 固定优先、esrc 游标升级、固定分组标题和总数/容量后续独立立项。pin 后进入固定组时按原始 `items` 位置稳定排序，不承诺绝对追加到固定组末尾。

## P12L 检查点固定名额提示前端（已完成）

契约=`docs/p12l-checkpoint-pinned-count-frontend-contract.md`，计划=`docs/plans/2026-07-20-p12l-checkpoint-pinned-count-frontend-plan.md`，代码哈希基线=`5258f84`、契约冻结=`4526832`、启动口径=`d21cfb5`、实现=`cc6bf11`；严格两文件已完成。

冻结后的联调验收项：

1. 技术标/商务标共用面板在默认列表加载完成、非搜索、无错误时显示精确 `已固定 X 条（最多 5 条）`；空成功列表为 0，首次/刷新加载中及错误态隐藏。
2. X 只由严格 items 中 `isPinned === true` 纯派生，合法 0..5；禁止新增 state/effect/API/缓存、truthy 宽判或字节容量推算。
3. pin/unpin 成功随既有原位更新加减；固定项删除成功减一、普通项删除不变；所有失败保留原数量且零 list/search/editor-state 旁路。
4. 5/5 时普通项固定按钮仍可发起精确一次 PATCH，由服务端保持权威；失败后仍 5/5，不在前端复制配额校验。
5. active search 始终隐藏提示，搜索 pin 仍隐藏；清除搜索按既有一次 GET 后才显示默认列表数量。项目切换、折叠、卸载及 A→B 迟到 success/catch/finally 不得污染。
6. checkpointId/stateVersion/snapshot/名称/关键词/原始错误/CSRF 不进入新增 DOM 属性、URL、存储、Cookie、console、剪贴板、下载或外网。
7. Grok 串行运行 P12L 聚焦、一次完整 checkpoint 受影响套件、lint/build；Codex 独立只复跑 P12L 聚焦与静态门。禁止整仓 318 E2E、后端 pytest 和并发 Playwright。

真实 failure-first **4 failed / 1 passed**；Grok 串行通过 P12L 聚焦/完整 checkpoint 受影响套件 **5/87 passed**，lint/build 通过；Codex 独立聚焦 **5 passed in 16.0s**、lint 和静态门通过。最终 SHA-256：面板=`890621124EB953F8A81BF4E5975E75B76F03A6296089FF682C5DE94A5FF187AE`，E2E=`C8961E30831869659FBC37CD806F95D4ACFA608097CEC2C52DFFD4E6DC72055A`。Codex 验收=`msg_a685c7123a4f4c9fac68481b99a25cec`；Grok 未 Git 写操作，Codex 实现提交=`cc6bf11`。前端整仓 318 与后端全量均未因本包重复运行。

## P13-A 任务 SSE 工作空间鉴权（已完成）

冻结=`e8dfa61`，实现=`1509aa2`。required 模式继续由认证中间件负责无会话 401；SSE 路由连接前短 Session 复用统一 `get_workspace_id`，因此 finance/hr/bidder 固定 `role_forbidden`，非成员显式头固定 `workspace_forbidden`，无头原生 EventSource 使用会话 `activeWorkspaceId`。disabled 仍支持默认空间与合法显式头。

连接前 Session 必须在 StreamingResponse 开始前关闭，生成器只捕获已授权 workspace 字符串；每轮 `_read_task_snapshot(workspace_id, project_id, task_id)` 新开短 Session 并再次做三层归属校验。不得为长连接挂 request-scope `get_db`，不得回退默认空间或只按任务主键读取。

自动化验收（后端，串行）：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p13a_task_sse_workspace_auth.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_task_sse.py tests\test_auth_rbac.py tests\test_p12b_delayed_writer_fences.py
.\.venv\Scripts\python.exe -m pytest -q
```

Codex 独立结果为 **13/72/918 passed**，仅 1 条既有弃用告警；全量完整重跑耗时 1310.97 秒。真实 failure-first **8 failed / 5 passed**；一轮 test-only 返修删除恒真泄漏断言、secret marker 跳过和宽松三参证据。消息追溯：原任务/review=`msg_7b03139e43024424ab5707426d2b02bf`/`msg_ea83529fa69a42c7a91a88ac775f96d3`，返修 task/review=`msg_b7cb9c7720a646a0976591d5cc4d3baf`/`msg_367b8a5ef9b54e89875bc16ea3b89974`，验收回执=`msg_c1023b623e3e40fea59ba798676d451d`。

本包不包含事件游标/重放、多任务总线、WebSocket、presence、前端工作空间切换 UI、URL token、审计扩展或数据库变更。

## P13-D1 修订操作者可信账本（已完成并推送，2026-07-20）

契约=`docs/p13d1-editor-state-revision-actor-ledger-contract.md`，计划=`docs/plans/2026-07-20-p13d1-editor-state-revision-actor-ledger-plan.md`，历史交接=`docs/HANDOFF-p13d1-in-progress.md`，冻结=`3132684`，实现=`a8982e3`。

联调必须确认：

1. 新库/旧 SQLite 最终都有 `editor_state_revisions.actor_user_id` 与 `project_tasks.actor_user_id`，可空、无 FK、无 actor 新索引、旧行 null、重复迁移幂等。
2. 注入第二个 ALTER 失败后重新连接，两张表均无 actor 残留；迁移函数不自行 commit，外层事务负责回滚。
3. required 只认 `request.state.auth_db_user_id`；disabled、非法状态与客户端 body/query/header/payload 投稿均不能控制 actor；本地票据只认 `issued_by_user_id`。
4. 九类 `browser_put/task/revise/callback/local_parser/content_fuse_apply/content_fuse_consume/checkpoint_restore/revision_restore` 均以命名参数把 actor 传到原事务 recorder。
5. task 创建时把 actor 写入任务行；真实 `_bg_worker` 在 Request/创建 Session 结束后以独立 Session 重载，最终 task 修订 actor 正确。任务 REST/SSE/结果/错误均无 actor。
6. 补账 before actor 固定 null；真实不同 after 才记录 actor；空账本同状态只补一条 null 行；no-op、stale、零恢复、同版本恢复不伪造 actor。
7. recorder/flush/裁剪/commit 或各写链故障保持既有完整业务域回滚；actor 不进入 13 键哈希、公开 editor-state、历史列表/详情、日志或浏览器状态。
8. 19 个生产文件保持 test-only 返修前冻结哈希；`business_task_service.py` 是覆盖四类商务任务的必要扩围，不得回退。

最终证据：

- 首轮 failure-first：**16 failed / 0 passed**；首轮绿测：**16 passed**。
- test-only 返修后 P13-D1 专项：**17 passed**。
- Codex 独立专项+精确 schema：**18 passed**。
- PRAGMA 精确顺序回归：**2 passed**。真实根因是 P13-C 测试在 `PRAGMA=1` 后 `Session.commit()` 归还连接，随后恢复可能换到另一池连接；现已改为同一显式连接完成开启、写入、提交、关闭和精确校验。
- 融合 apply/consume、检查点恢复、修订恢复、本地票据回调代表性真实事务路径：**5 passed**。
- 19 个生产文件冻结哈希逐项一致；py_compile、`git diff --check`、公开 actor 泄漏门与 22 文件实现暂存白名单通过。
- 最终 Grok review=`msg_de747706fcb64a188eef50d77e29d451`；Grok 未执行任何 Git 写操作。
- Codex 中文实现提交=`a8982e3`，已推送 `collab/grok-code-codex-review`。

复验命令：

```powershell
cd C:\Users\Administrator\biaoshu\backend

# PRAGMA 同连接修复后的精确顺序
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_editor_state_revisions.py::test_no_commit_rollback_refresh_project_lock `
  tests\test_p13c_current_revision_source.py::test_corrupt_latest_source_returns_null_no_500 `
  --tb=short

# P13-D1 专项与精确 schema；禁止 xdist/并行分组
.\.venv\Scripts\python.exe -m pytest -q `
  tests\test_p13d1_revision_actor_ledger.py `
  tests\test_editor_state_revisions.py::test_table_columns_constraints_indexes_and_fk_cascade `
  --tb=short

cd ..
git diff --check
git diff --cached --name-only
```

本包没有前端生产改动，最终未运行 Playwright、lint、build、后端全量或整仓 318 E2E；不得把历史基线冒充本包重跑结果。后续只有出现共享迁移/事务回归证据时才扩大测试。

P13-D1 与 P13-D2 均已由 Codex 中文提交并推送。下一主线包应独立冻结活动工作空间切换 UI 与成员可见性；presence、事件协议和真正实时协作继续拆包。

## P13-D2 当前已载入版本操作者用户名展示（已完成并推送）

契约=`docs/p13d2-current-revision-actor-username-contract.md`，计划=`docs/plans/2026-07-20-p13d2-current-revision-actor-username-plan.md`，冻结=`4b95ab5`，实现=`44c9196`。

联调验收项：

1. `GET|PUT editor-state` 200 必出 `currentRevisionActorUsername`；只在最新修订与响应版本精确匹配、actor 用户和同工作区成员均启用、用户名通过安全文本门时返回原名，否则精确 null。
2. disabled、旧行、补账 before、actor 空、用户/成员缺失或停用、仅其它工作区成员全部未知；活动成员角色变更不抹除归因，未来改名按当前用户名展示，不伪造历史快照。
3. 来源与用户名由同一次 `LIMIT 1` 最新修订查询独立校验；禁止回扫、加载 snapshot/口令/会话、泄漏 actor ID，GET 继续零写。
4. 技术标/商务标标题区分别以 `technical-editor-version-actor`、`business-editor-version-actor` 显示“当前版本操作者”；未知固定“操作者未知”，用户名只作 React 文本。
5. 用户名与时间/来源共用合法版本、session/write epoch 接受门；项目切换同拍清空，旧项目迟到 success/catch/finally 不污染，409/失败保值，合法新版本的坏用户名更新为未知。
6. 外部写只允许既有唯一 editor-state GET 更新元数据；零新增请求、轮询、timer、SSE/WebSocket、存储、URL、Cookie、console 或外网泄漏。
7. pytest 串行，Playwright 固定 `--workers=1 --retries=0`。Grok 跑专项与直接回归，Codex 独立审查验收，不机械重复全量。

验收证据：后端真实 failure-first **26 failed / 0 passed**；前端没有合规 E2E-only red，未补造。Grok 最终串行通过后端 P13-D2+P13-C / freshness **44/17 passed**；Codex 独立串行通过后端核心/受影响回归/freshness **44/15/17 passed**，并通过内容融合 apply、技术/商务检查点 restore、技术修订 restore 外部写唯一 GET **1/2/1 passed**。lint、py_compile、diff-check、严格 9 生产+4 测试白名单、哈希与 actor ID/敏感字段泄漏门全部通过。

消息追溯：初始 task/review=`msg_440d7e3e83fa4be0a2a835f8c22aacfc`/`msg_847dca8b61064346bad66b3cb99c6450`；受限返修 task/review=`msg_33342a63342c40399ab6f19501b6f5fc`/`msg_375ba4a732bf42b8986d21ce2602ba9c`。未运行后端全量、完整受影响 E2E 或整仓 318 E2E；不得冒充本包结果。

## P13-E 活动工作空间切换与成员只读可见性（已完成并推送）

契约=`docs/p13e-active-workspace-switch-member-visibility-contract.md`，计划=`docs/plans/2026-07-20-p13e-active-workspace-switch-member-visibility-plan.md`。

完成后的联调验收项：

1. required 双空间用户在全局业务壳看到真实空间选择器；单空间、当前值或非法 DOM 值不发 PUT，disabled 不显示且零认证空间请求。
2. 合法选择精确一次 `PUT /api/auth/active-workspace`，请求体仅 `{workspaceId}`，使用内存 CSRF；无 query、`X-Workspace-Id`、Cookie 读取、存储或外部主机。
3. 切换成功按新空间角色整页进入 `/create`、`/finance`、`/hr` 或 `/bidder`，旧项目/表单/任务/列表状态全部卸载，新导航与所有者设置入口以新 `/auth/me` 为准。
4. HTTP、网络、解析或坏响应显示固定错误并调用 `/auth/me` 对账；若服务端已切换则按成功处理，未切换则保留原空间，对账失败不得继续显示未经确认的可写业务壳。
5. 设置页 required 模式显示真实活动空间名称、ID、角色与所有者，不再显示“我的工作空间（后端）/`ws_local`”假值；disabled 明确个人版默认空间。
6. 仅当前空间所有者看到“加载成员列表”，用户点击后单飞 GET 一次；列表含停用成员，只显示用户名、中文角色、所有者与启用状态。非所有者/disabled 零请求。
7. 坏成员响应整批失败；userId 不进入可见文本、属性、title、URL、存储、console、剪贴板或外网；`isActive` 不得展示为在线/presence。
8. pytest 串行，Playwright 固定 `--workers=1 --retries=0`。本包只跑 P13-E/完整 auth-rbac E2E、lint/build、三个既有后端定点与 diff-check，不默认后端全量或整仓 E2E。
9. 任一审查返修必须有两条分离记录：Grok 对 Codex 发现的只读独立确认，以及双方确认后 Codex 下发的新修复授权。确认前零代码修改；有分歧时只补只读证据。若误触文件，冻结现场，禁止继续、清理、提交或把局部改动冒充完成返修。

本轮第二次审查双确认记录：只读确认请求=`msg_c1e71b76f13c418f99d6f73fbf778b77`，Grok 确认=`msg_e6f7094596fc4d3db79661611b217f10`；活动空间真值回退/重复 ID 与 `expectCountStable` 伪稳定两项均确认存在。提前返修=`msg_c5e4959b6e9b4276b6ed0f15334ef865` 已中止；新授权/review/result=`msg_f3914a680ccf4b9fbf3b3a099fb3f3cb`/`msg_1bfe78d7492e476d9b7187ad847dbdbd`/`msg_1ab08b68c9e74278ad7b17e537633321`。

验收证据：冻结=`19f0bfe`、实现=`5685441`；真实 failure-first **14 failed / 2 passed**。Grok 最终 P13-E/完整认证 **25/36 passed**，lint/build/diff-check 通过；Codex 独立 **25/36 passed**，lint/diff-check 通过，既有后端三节点此前 **3 passed**。严格六生产加一 E2E，最终哈希与 review_request 一致，暂存边界和测试产物清理通过。未运行后端全量或整仓 E2E。

已知非本包问题：完整认证 E2E 可能打印 disabled 设置页 `workspace_settings.workspace_id` 并发 `UNIQUE` 日志，36 项仍全部通过。不得在本包扩后端修复；另行立项时先执行双确认返修门。

## P13-F1 项目在线租约后端基础（已完成并推送）

契约=`docs/p13f1-project-presence-lease-backend-contract.md`，计划=`docs/plans/2026-07-20-p13f1-project-presence-lease-backend-plan.md`，完成态交接=`docs/HANDOFF-p13f1-in-progress.md`。已通过的联调验收项：

1. required 当前活动 workspace 的 strict bid_writer 对真实同空间项目 heartbeat 成功；disabled、其它角色、停用成员/用户、跨空间/项目和任意 `X-Workspace-Id` 均零租约。
2. 心跳 body 精确 `clientId`；CSRF 必须有效；clientId 不 trim、不落明文，数据库只含摘要。同 client 续租保持一行并把服务端过期时间推进 45 秒。
3. 每用户每项目最多 8 个活动 client；旧 client 在上限仍可续租，新 client 固定 429 且无副作用。相同 client 并发不重复、不 500。
4. 快照重新校验启用用户、同空间启用 bid_writer 成员；同用户多 client 只一项，自身优先，最多 50，超限 `truncated=true`。
5. 成员项只含安全 `username/isSelf`；user/member/lease/client ID/摘要、角色、owner、时间明细、Cookie、CSRF、会话与项目内部字段零出口。
6. leave 只删当前 actor 当前项目当前 client 摘要，幂等 204；不会删其它 client/用户/项目/空间。项目/用户删除级联清理。
7. heartbeat 机会清理过期行；过期成员不返回。所有成功 no-store，数据库错误 rollback 并脱敏。
8. pytest 串行；先新专项，再 auth/projects/P13-A 直接回归与 py_compile/diff-check，不默认后端全量或前端测试。
9. P13-F1 无前端在线 UI、SSE/WebSocket、广播、光标、章节锁、历史或后台清理线程；P13-F2 已在下节独立完成，不沿用本包白名单。

验收证据：初始 task/status/review=`msg_31bba4d10d154daca2acab7d3f6ea1e5`/`msg_aad9a00220a44195965981cfe82dae22`/`msg_b176f13020d5470395f70792f811921b`，初始 failure-first **30 failed / 4 passed**。两轮双确认返修分别关闭四项隐私/并发/反假绿问题和锁前时钟问题；返修红测 **16 failed / 5 passed**、**2 failed**。Grok 最终专项/直接回归 **41/55 passed**；Codex 独立专项/代表回归 **41/3 passed**，`py_compile`、diff、白名单与哈希门通过，result=`msg_5aae77e9c06b436aaa9f46c5747e4648`。实现=`6164d8c` 已推送；未跑后端全量、前端或整仓 E2E。

## P13-F2 项目近期成员前端（已完成并推送）

契约=`docs/p13f2-project-presence-frontend-contract.md`，计划=`docs/plans/2026-07-20-p13f2-project-presence-frontend-plan.md`，完成态交接=`docs/HANDOFF-p13f2-in-progress.md`。以下项目已逐项验收：

1. 仅 required authenticated strict bid_writer、当前 projectId 非空且页面 visible 时 heartbeat；disabled、其它角色和非项目页均零 presence 请求。
2. clientId 由 `crypto.randomUUID()` 在当前文档内存延迟生成，只进入 heartbeat/leave 精确 JSON body；不进存储、Cookie、URL、DOM、日志、剪贴板、下载或外网。
3. React StrictMode 首轮稳定窗口只有一次 heartbeat；heartbeat/leave 文档级串行，成功后 15 秒才续租，慢请求不并发。
4. hidden 清空成员并 leave，visible 立即 heartbeat；pagehide/卸载 best-effort keepalive leave。项目 A→B 同步隐藏 A，leave A 后 heartbeat B，A 迟到结果不污染 B。
5. heartbeat 顶层精确四键，成员精确两键、最多 50、唯一 self、安全 username；extra/缺键/坏类型/坏用户名/多 self 整包固定失败。
6. 技术/商务标题区只显示“近期在此项目”、用户名文本、“（我）”和保守 truncated 文案；不得出现在线、实时、正在编辑、正在输入或最后活跃承诺。
7. HTTP、网络、解析和权限失败不得阻断 editor-state 或导航，只显示固定不可用文案；status/detail/code/URL/projectId/clientId/secret marker 零出口。
8. 严格四生产加一新 E2E，无后端、api.ts、auth、router、editor Hook、CSS、依赖或已有测试扩围。
9. Playwright 固定 `--workers=1 --retries=0`；先真实 failure-first，再新专项、freshness 受影响回归、lint/build/diff-check。禁止整仓 E2E、后端全量或并发测试。
10. Codex 疑似问题先让 Grok 只读确认；双方确认存在后才发送独立返修 task。确认前零代码修改，消息 ID 全量写回闭环文档。

验收证据：冻结=`a5709ed`、功能=`dfa6bc0`。初始 task/failure-first/review=`msg_c4b8c3db2b844373a2d9473e2cada9ab`/`msg_85e70a7b4d6e4a2783eb7d1d3bbf072a`/`msg_d496bf3eb7874f95ab5ff1ca1e109247`，真实 `7 failed / 1 passed`。第一轮八项只读双确认=`msg_1e069e62860443f3b40ff942a71c8a78`/`msg_e09a746f34af4c6cbff56a0e7119e0fd`，返修 task/红测/review=`msg_dce02edef2f64cbb8c869cf8c38fb496`/`msg_ea67dfcc73b740a1b7708c62b6db681b`/`msg_33b51876c9a04a5590e2bfbee366b9b1`，红测 `2 failed / 0 passed`。第二轮双确认/task/review/result=`msg_cb83ccf6fe9844138e83fae417829d13`/`msg_4bc81573d30d4f80ada262a540cb81ba`/`msg_534a0dc70d9e4ff7ae53e9a54d7f7d0b`/`msg_d46854d96aac4c6db75e4348f9012dc3`/`msg_f19ceb09650a4f0584e2d4b1d1985fb4`。

最终结果：Grok 聚焦/完整专项 `3/11 passed`，lint/diff-check 通过；Codex 独立专项/freshness `11/17 passed`，lint/diff-check 通过。build 沿用最终生产改动后的 Grok 成功结果；未运行整仓 318 E2E、后端 pytest 或并发 Playwright。pagehide leave 可能受在途 heartbeat 排队延迟，按串行契约与服务端 45 秒过期兜底接受为残余风险。

## P13-H3 编辑状态事件前端版本提示（契约冻结，待实现）

1. 生产白名单仅为 `EditorStateEventUpdatePanel.tsx`、技术标工作区、商务标工作区；测试仅为 `editor-state-event-update.spec.ts`。
2. 仅 required + authenticated + 活动 `bid_writer` + 当前项目建立 EventSource；URL 精确为 `/api/projects/{projectId}/editor-state-events/stream`，`withCredentials=true`，无 query/header/body。
3. 严格接受 `cursor`、`editor-state`、`cursor-stale`、`unavailable`；非法帧、网络错误和控制帧固定不可用，禁止展示后端原文、ID、项目/空间/actor/client/正文/任务信息。
4. 合法且不同的 `stateVersion` 只显示“检测到远端版本变化，请确认后重新载入”；用户确认前零 editor-state GET，确认后精确一次既有 `reloadFromApi`/`refreshFromApi`，不自动写正文。
5. 项目切换/卸载立即关闭旧连接并清空旧提示；迟到事件和刷新结果不得污染新项目。组件不得轮询、重连计时、写 storage/URL/日志。
6. Playwright 固定 `--workers=1 --retries=0` 串行；验收 H3 专项、freshness 代表、lint、build、diff-check，不跑整仓 E2E 或并发测试。

## P13-G1 项目章节编辑意图租约后端（已完成并推送）

契约=`docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md`，计划=`docs/plans/2026-07-20-p13g1-project-chapter-edit-intent-lease-backend-plan.md`，完成态交接=`docs/HANDOFF-p13g1-in-progress.md`。已逐项验收：

1. 仅 required 活动 workspace strict bid_writer 对同空间 technical 项目的当前真实章节可 heartbeat；disabled、其它角色、owner 绕过、停用、跨空间与业务项目均拒绝。
2. 请求体精确 `clientId/chapterId`；clientId 仅存 SHA-256，chapterId 锁后对 `chapters_json` 原生字符串精确唯一命中，不 trim、不按标题回退。
3. 项目级数据库锁必须先于项目/章节判断、过期清理、计数和冲突判断；服务端 `now` 在锁后采样，过期固定 45 秒，refresh 固定 15 秒。
4. 同 user+client 原行续期；同用户不同 client 与不同用户均冲突；冲突只返回重新校验的安全 holder username。
5. 两用户并发抢同一章节恰一成功一冲突，最终单行、不重复、不 500；每用户项目最多 8 个活动章节，旧租约续期不受新租约上限影响。
6. holder 停用、成员停用/改角色、坏用户名或租约过期后可安全接管；旧身份和所有内部 ID/digest 零出口。
7. leave 只删 workspace/project/chapter/user/client 五维精确行，章节删除后仍可清理；错 client 与重复调用幂等 204。
8. 新表唯一键、两个复合索引、workspace/project/user FK 级联精确；service/flush/commit 任一故障完整 rollback。
9. 任意 `X-Workspace-Id`、CSRF、坏 JSON/extra/缺键/坏类型/非法字符均固定脱敏；成功与业务错误 no-store。
10. 本包不修改 editor-state PUT，也不阻止旧客户端/任务写入；UI 和文档不得把意图租约称为强制锁。
11. 严格七文件，无 P13-F1/F2、认证、配置、依赖、前端或已有测试扩围；pytest 串行，先新专项和代表回归，再 py_compile/diff/哈希门，不默认后端全量或 Playwright。
12. 疑似问题必须先 Codex question/review、Grok 只读确认，双方确认存在后才可另发返修 task。

验收证据：冻结=`a0b7c48`，功能=`015ab37`。初始 task/有效 failure-first/review=`msg_0c9d11a1bdf946c9b8f2f85b68152774`/`msg_7e89c95cb9e143aab17fe46d92a1a9a0`/`msg_5a97ada55378441fa1ed223cf9f74bef`，真实 **42 failed / 3 passed**；恢复会话重复 status=`msg_c818b81805b54255895e7d9e50248a28` 不作为纯红测证据。

Codex 第一轮只读问题/确认/返修 task/review/result=`msg_cec182e52c6c4775b99ef33eef0cbf60`/`msg_7d6862739de5449082c65350b4536deb`/`msg_2e591638e1b94f559cdab1ea3e57c0d6`/`msg_2a7689d2a917465fb0c6f3de486d379a`/`msg_18dc76c33b9f47d0a72d754e7578682c`。六项全部双确认后才返修：4096 字节流式 body 上限、恒真/宽状态清除、真实 SQLite 锁等待 fresh-now、精确表/FK/workspace 级联、malformed/非对象/超限 JSON 公开矩阵。

最终结果：Grok 聚焦/专项 **17/53 passed**；Codex 独立专项/P13-F1/认证/editor-state **53/41/8/1 passed**，六文件 `py_compile`、diff-check、严格七文件、空暂存与 SHA-256 门通过。未运行后端全量、Playwright、前端或 xdist。P13-G1 仍只是 advisory intent lease，不阻止现有 editor-state PUT；P13-G2 前端接入已在后续独立包完成。

## P13-G2 项目章节编辑意图前端提示（已完成并推送）

契约=`docs/p13g2-project-chapter-edit-intent-frontend-contract.md`，计划=`docs/plans/2026-07-20-p13g2-project-chapter-edit-intent-frontend-plan.md`，在途交接=`docs/HANDOFF-p13g2-in-progress.md`。完成后必须逐项验收：

1. 仅 required authenticated strict bid_writer 的技术标 content 步、当前有效章节、visible 且内存 UUID/CSRF 可用时 heartbeat；其它状态零 chapter lease 请求。
2. P13-F2 presence 与 G2 chapter lease 复用同一文档 UUID；G2 不修改 P13-F2，章节租约独立串行。
3. heartbeat/leave body 精确 `clientId/chapterId`；项目 ID 只在编码路径，chapter/client 只在 JSON body。
4. 200 精确两键且 refresh=15；409 顶层/detail 精确、固定 code/message、安全 holderUsername；坏包固定 unavailable。
5. 首跳吸收 StrictMode，完成后 15 秒续租，慢请求不并发；A→B 精确完成 A→leave A→heartbeat B，迟到不污染。
6. hidden 清空并 leave、visible 立即 heartbeat、pagehide keepalive；初始 hidden 零 UUID/heartbeat/leave。
7. 自身、冲突、unavailable 只显示固定保守文案；冲突不禁用标题/正文/任务/图片/卡片或 editor-state PUT。
8. clientId、chapterId、holderUsername、status/code/detail/URL/secret marker 无新增 DOM、属性、存储、console、剪贴板、下载或外网出口。
9. 严格四文件，无后端、P13-F2、共享 api/auth/router、editor Hook、ChapterEditor、CSS、依赖、配置或已有测试扩围。
10. Playwright 固定 `--workers=1 --retries=0`；先新专项，再 P13-F2/freshness/必要技术标真值回归、lint/build/diff/哈希，不默认整仓 318 E2E 或后端 pytest。
11. 疑似问题必须先 Codex question、Grok 只读确认，双方确认存在后才可另发返修 task。

完成证据：冻结=`3a74fbb`，功能=`86abbbf`。failure-first=`msg_b20b7dbe314943ba806fcf62f37d95c9`，真实 **8 failed / 1 passed**；两轮只读双确认=`msg_9fa0bb83f0f348f99eca175567b3983d`、`msg_24da16ad88c94f7585de0a34ef88095d`，确认后才返修。Grok 最终 review=`msg_7a542b4e3d444c13800cc401141a0d90`，专项/聚焦关键序列 **13/7 passed**。

Codex 独立串行通过 P13-G2 专项/P13-F2 presence/freshness **13/11/17 passed**，lint、build、diff-check、严格四文件、八文件 SHA-256 与临时工件清理门通过；build 仅既有 chunk 大小警告。未运行整仓 318 E2E、后端 pytest、xdist 或并发 Playwright。P13-G2 只表示近期处理意图，不是强制锁、实时协作或在线状态。

## P13-H1 editor-state 事件账本与游标后端（已完成，随本记录提交）

契约=`docs/p13h1-editor-state-event-cursor-backend-contract.md`，计划=`docs/plans/2026-07-20-p13h1-editor-state-event-cursor-backend-plan.md`，审计基线=`83c2c4a`，冻结=`da2537a`，实现=`4255823`。严格八文件交付独立 `editor_state_events` 表和统一 transition 真实 after 同事务写入，以及 required strict bid_writer 项目级 GET。

完成证据：failure-first=`msg_ee84a231060941049177cce0f05f501a`，真实 **25 failed / 3 passed**；Grok 初版专项/回归 **28/90 passed**。Codex 只读发现无公开 bootstrap tip、未登录与非 GET 宽状态断言两项问题，经 Grok 确认后才授权最小返修；最终 Grok 与 Codex 独立均为专项/回归 **28/90 passed**，`compileall`、diff-check 和严格八文件哈希门通过。

事件只含不透明 eventId、stateVersion、sourceKind、occurredAt；不含快照、正文、章节、actor/client、任务结果或异常原文。无 `after` 时不回放历史，已有事件返回最新 tip 供后续增量读取；游标 stale 固定脱敏 409，事件上限 200/项目，失败事务零事件。未运行后端全量、前端、整仓 E2E、xdist 或并发 pytest；SSE、Last-Event-ID、前端自动刷新、WebSocket、通知、评论审批和强制锁仍未实现。

## P13-H2 editor-state 事件 SSE 与断线重放（已完成并推送）

契约=`docs/p13h2-editor-state-event-sse-replay-contract.md`，计划=`docs/plans/2026-07-20-p13h2-editor-state-event-sse-replay-plan.md`，审计基线=`7e5e02e`，功能=`c19bf94`。严格三文件：H1 事件服务、H1 路由和新 H2 专项；任务 SSE、main/schema/实体/transition、认证公共层、前端与其它测试保持不变。

完成证据：failure-first=`msg_f51300735be6483a9a2570cff2fc899e`，真实 **14 failed / 1 passed**；Codex 发现 request-scope `get_db` 会贯穿 SSE，question=`msg_681a4b8bc9194df98dab55c36b4aa93b`，Grok 确认=`msg_101ba01ec2cc48868a7f8ad0b8dfad07`，返修授权=`msg_85dcfc97c8424b9f987cc9ba682c071d`。返修后 Codex 独立专项/代表回归 **15/46 passed**，compileall、diff-check、严格三文件哈希门通过。

无 Last-Event-ID 的已有历史只发公开 tip cursor 锚点，不回放旧 editor-state；有 Last-Event-ID 正序重放保留窗口内后续事件；空表首事件不得丢。SSE id 与四键 data.eventId 相等，心跳仅注释；连接前 stale 是 HTTP 409，连接中 stale 是无 id 固定控制帧。连接前与每轮查询均用已关闭的短 Session，stream scope 不再触发 request-scope `get_db`；required/活动 workspace/strict bid_writer、X-Workspace-Id、跨项目和请求语法必须精确。未运行后端全量、前端、整仓 E2E、xdist 或并发 pytest；H3 前端、WebSocket、通知、多任务总线或强制锁仍未实现。
