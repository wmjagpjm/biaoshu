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
10. **正文生成**页点击图片图标上传 PNG/JPEG/GIF → 当前光标位置写入 `biaoshu-image://file_...`；导出 Word 后有图片与可选题注；手工删掉该图片后再次导出，应出现“图片引用无效”而非请求外网
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

# 阶段3 M3-B：差异预览 + 勾选确认写入 / base 漂移跳过 E2E
npm run test:e2e:fuse-apply

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

# P10E：投标人匿名合规预览（隔离 8010/5174、路由桩；仅 bidder 专用端点）
npm run test:e2e:bidder-compliance-preview
```

当前基线：后端串行全量 **335 passed**（1 条既有 Starlette/httpx 弃用警告，含 P10E 严格投标人隔离测试）；前端 `lint` / `build` 通过（仅既有大包体积提示）及全量 E2E **63 passed**。其中 P10E `test:e2e:bidder-compliance-preview` **8 passed**、P10D `test:e2e:hr-credential-cards` **9 passed**、P10C `finance-cost-draft` **4 passed**、P10B `finance-role` **7 passed**、P10A `auth-rbac` **11 passed**、P9C `semantic-index` **9 passed**、知识卡片 `cards` **1 passed**。P10E 仅向 strict `bidder` 开放工作空间级匿名响应矩阵汇总，`AUTH_MODE=disabled` 不开放；完整契约见 `docs/p10a-local-identity-rbac-contract.md`、`docs/p10b-finance-business-quote-contract.md`、`docs/p10c-finance-cost-draft-contract.md`、`docs/p10d-hr-credential-cards-contract.md` 与 `docs/p10e-bidder-anonymous-compliance-preview-contract.md`。

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
| GET | `/api/hr/credential-cards`（仅 strict `hr`；当前空间摘要；不含备注；`no-store`） |
| GET | `/api/hr/credential-cards/{cardId}`（仅 strict `hr`；跨空间/不存在统一 `404 hr_credential_not_found`；`no-store`） |
| POST | `/api/hr/credential-cards`（仅 strict `hr` + CSRF；字段白名单与严格 JSON 布尔） |
| PATCH | `/api/hr/credential-cards/{cardId}`（仅 strict `hr` + CSRF；更新/启停；无 DELETE） |
| GET | `/api/bidder/compliance-preview`（仅 strict `bidder`；当前空间技术标响应矩阵匿名汇总；`no-store`） |
| GET/POST | `/api/projects` |
| GET/PATCH/DELETE | `/api/projects/{id}` |
| GET | `/api/projects/{id}/tasks/{taskId}/events`（SSE） |
| GET/PUT | `/api/projects/{id}/editor-state`（含 responseMatrix） |
| GET/POST | `/api/projects/{id}/files`（仅招标源文件） |
| GET/POST | `/api/projects/{id}/images`（仅项目正文图片） |
| GET | `/api/projects/{id}/images/{fileId}`（受控预览） |
| GET/PUT | `/api/settings` |
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
5. 不得出现证件号、手机号、住址、附件、URL、创建人或工作空间字段；无 DELETE、导出、项目关联、团队推荐或跨空间搜索。

## 6.2 P10E 投标人匿名合规预览

1. 以严格 `bidder` 登录，打开 `/bidder`；侧栏仅显示「投标人 / 合规预览」，不得显示标书制作者、财务或人力入口。
2. 页面仅请求 `GET /api/bidder/compliance-preview`，并只展示总条目、已覆盖、未覆盖、已豁免和服务端给出的覆盖率基点；`empty` 时覆盖率显示「暂无可计算覆盖率」。
3. 响应和页面不得出现项目数量、项目 ID/名称、工作空间 ID、人员、原文、`sourceKey`、章节/大纲、备注、文件、报价或成本；页面须声明不是评审结论或投标结果。
4. `owner` 的隐式绕过、`bid_writer`、`finance`、`hr` 与 disabled 均没有投标人入口；直达 `/bidder` 只显示受限页，且不应发投标人预览 API 请求。
5. 浏览器不得写入 `localStorage` 或 `sessionStorage`；除认证、健康检查和本接口外，不能请求项目、编辑态、设置、文件、财务、人力或外网端点。完整边界见 `docs/p10e-bidder-anonymous-compliance-preview-contract.md`。

## 7. 本机日用主链路（目标 A 加强版）

| 步骤 | 操作 |
|------|------|
| 上传 | document 步选择 PDF/DOCX/TXT |
| 解析 | 「轻量解析」（**异步任务**，顶部进度条） |
| 本地 MinerU | `/local-parser` 粘贴 Markdown 回传，或 `POST .../parse-callback` |
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

Celery、真 MinerU 安装包、P9B 以外的外部标讯数据源、P9C 的其他模型/GPU/在线 embedding/真实用户语料评测与自动模型更新、P10C 以外的财务税务/审批/导出/预算/回款/版本与审计查看、P10D 以外的人力团队推荐/人员业绩/附件与证件校验、投标人项目级预览/版本/结果跟踪与其他合规数据域、SSE 事件游标/多工作空间鉴权、标题整章布局语义。

**响应矩阵相关（已接 vs 未扩）：** 多端冲突的版本写保护、409 与双浏览器上下文 E2E 主路径已接；「刷新来源」保留人工映射 E2E 已接；**智能建议人工确认后应用** E2E 已接；**来源超过 80 分页** 已推送（`1289c92`）；**字段级三方合并** MVP + E2E 已推送（`2c7b3e0`，`response-matrix-field-merge.spec.ts`）。仍未接：Word 失效引用在浏览器层的扩展（导出逻辑以后端单测为准）；包 9 交付增强。

**解析相关（包 8 MVP）：** 可插拔调度 `parse_engines` + 默认 `lightweight` + 任务 `result.engine` **已验收并推送**（`6db1586` 实现可插拔解析引擎调度）；MinerU **仅**外置 `parse-callback`（默认 token 空=不校验，部署须自配）；Docling 未接；`parseStrategy` 未接线。

## 15. 知识库 RAG 简版

1. 打开「知识库」→ 上传 md/txt/docx/pdf → 状态「已就绪」、分块数 > 0  
2. 浏览器或 curl：`GET http://127.0.0.1:8000/api/knowledge/search?q=关键词` 有 items  
3. 技术标生成大纲/章节时，任务 result 可含 `kbCitations`（有相关文档时）  
4. 无文档时生成行为与此前一致
