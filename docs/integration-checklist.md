# 前后端联调清单

> 目标：验证 health / 项目 / 设置 / revise / editor-state / 响应矩阵 / 本地标讯库 / 资源中心及受控同步 / **中标内容模板** 已闭环。
> Key **明文**存储与回显（保密机决策）。

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

当前基线：后端串行全量 **537 passed**（1 条既有 Starlette/httpx 弃用警告）；P12B-A 专项 **19 passed**、内容融合三项加财务整文件 **12 passed**、P12A/editor-state/矩阵/融合确认/callback/模板回归 **104 passed**。P12A 历史专项 **29 passed**、P8C/异步 callback **15 passed** 继续保留。前端 `lint` / `build` 通过（仅既有大包体积提示），技术 editor-state truth **28 passed**、商务 editor-state truth **18 passed**、P11A **10 passed**、认证/RBAC **11 passed**、解析策略 **6 passed**、响应矩阵 **8 passed**、HR 推荐 **4 passed**、融合确认 **6 passed**、持久恢复 **5 passed**、模板复用 **1 passed**，Chromium headless、单 worker 串行全量 E2E **201 passed**。M3-D、P10K、P8C、P9D 及其他既有专项继续保留。E2E 共用 SQLite 重置脚本，禁止并行启动多个 Playwright 命令，必须逐条串行运行。

P8D/P8E 本机助手独立验收命令（仓库根；不安装或探测真实 MinerU/Docling）：

```powershell
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_docling_callback_helper.py" -v
backend\.venv\Scripts\python.exe -m unittest discover -s tools\local-parser -p "test_mineru_callback_helper.py" -v
```

P8E 当前为 Docling **46 passed**、P8D MinerU **54 passed**；后端 P8E-A/P8C/P8B/解析受影响回归 **37 passed**，P8C E2E **9 passed**、P8B E2E **6 passed**。P8E 当时沿用后端全量 487；P12A 曾更新为 518，P12B-A 已将当前后端全量更新为 537，P12B-B 已将前端全量更新为 201。真实 Docling/模型未安装、未验收。

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

P12B-B 已实现并推送（契约/计划=`0636302`、实现=`473e823`）。验收已证明技术整包、guidance、矩阵合并和商务整包 PUT 都带最新 `expectedStateVersion`；同项目第二请求在第一响应前严格为 0，且 expected 精确等于第一响应版本；固定全状态 409 保留本地并阻断全部写入，只有显式全量 GET 才恢复。技术/商务 GET 缺失或非法版本、PUT 200 缺失/非法新版本均进入固定阻断；普通 409 无矩阵明细不得伪造空矩阵冲突。独立结果为 **28 / 18 / 8 / 4 / 6 / 5 / 201 passed**；下一包 P12B-C 尚未实现。

## 6. 已接 API 一览

| 方法 | 路径 |
|------|------|
| GET | `/api/health` |
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

任务默认异步：`POST /tasks` 立即返回，前端优先订阅 `GET /tasks/{id}/events` 的 `snapshot` / `task` / `heartbeat`；流不可用时立即 GET 一次，再以 2 秒间隔轮询。SSE v1 仅承诺默认工作空间。
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
5. 在已准备好固定模型缓存的受控环境，执行 `backend/.venv/Scripts/python.exe backend/scripts/semantic_model_preflight.py`：先检查 5 GiB 磁盘，仅读固定合成集；真实指标必须达到 Recall@5≥0.80、NDCG@5≥0.70。当前无模型缓存时应受控返回 `model_unavailable` 与退出码 2，不得下载、安装依赖或伪造通过。

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

Celery、MinerU/Docling 自动安装、模型打包、常驻服务、真实模型样本验收与完整孙进程治理、P9B 以外的外部标讯数据源、P9C 的其他模型/GPU/在线 embedding/真实用户语料评测与自动模型更新、M3-D 以外的通用版本历史/任意历史浏览回滚/多人协作、商务 AI 反馈历史服务端化、P10K 以外的财务税务/审批/导出/预算/回款/版本与失败尝试/完整身份审计、P10I 以外的人力附件与真实证件核验、P10G 以外的投标人矩阵明细/版本/结果跟踪与其他合规数据域、SSE 事件游标/多工作空间鉴权、标题整章布局语义。

**响应矩阵相关（已接 vs 未扩）：** 多端冲突的版本写保护、409 与双浏览器上下文 E2E 主路径已接；「刷新来源」保留人工映射 E2E 已接；**智能建议人工确认后应用** E2E 已接；**来源超过 80 分页** 已推送（`1289c92`）；**字段级三方合并** MVP + E2E 已推送（`2c7b3e0`，`response-matrix-field-merge.spec.ts`）。仍未接：Word 失效引用在浏览器层的扩展（导出逻辑以后端单测为准）；包 9 交付增强。

**解析相关（包 8 MVP + P8B + P8C + P8D + P8E）：** 可插拔调度 `parse_engines` + 默认 `lightweight` + 任务 `result.engine` 已推送（`6db1586`）；P8B 已把工作空间 `light/local/ask` 接到技术标和商务标解析入口；P8C 已提供 10 分钟单项目单次回传票据；P8D/P8E 已提供只调用本机既有 `mineru.exe`/`docling.exe` 的离线、回环、受限标准库助手（`e1fe316`/`e3f9cc4`）。旧个人 callback 与可选长期 Token 仍兼容；真实 CLI/模型需人工安装准备，自动部署仍未接。

## 15. 知识库 RAG 简版

1. 打开「知识库」→ 上传 md/txt/docx/pdf → 状态「已就绪」、分块数 > 0  
2. 浏览器或 curl：`GET http://127.0.0.1:8000/api/knowledge/search?q=关键词` 有 items  
3. 技术标生成大纲/章节时，任务 result 可含 `kbCitations`（有相关文档时）  
4. 无文档时生成行为与此前一致
