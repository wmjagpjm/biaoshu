# P9C 离线真语义索引实施计划

> **给 Grok：** 严格按任务、TDD 和白名单实施；每项完成后只发送 review_request，不得提交或推送。
> **实施基线：** 本计划由 Codex 按用户授权冻结；不再等待模型或数据出域决定。

**目标：** 用本机离线 BAAI/bge-small-zh-v1.5（512 维）建立可恢复、版本化的知识库语义索引，使用户可见索引状态与关键词降级；旧哈希向量绝不伪装为语义检索结果。

**架构：** 保留现有 kb_chunks 及其历史 embedding_json，不覆盖、不删除。新增“语义索引运行”和“索引分块向量”两张表：重建时先写新的 running 版本，全部成功后才将其切为 active；失败或中断保留上一 active 版本。搜索只读取当前 active 版本的同维向量，并总是返回明确 semanticStatus；无 active 版本时只执行关键词检索且页面明确显示“未构建/关键词降级”。

**技术栈：** FastAPI、SQLAlchemy/SQLite、sentence-transformers、PyTorch CPU、Python 标准库哈希、React/TypeScript、Vite、pytest、Playwright。

## 已冻结决策

1. 仅使用离线 BAAI/bge-small-zh-v1.5、512 维、CPU；不使用 OpenAI 兼容 embedding API，不向外发送知识库正文或查询。
2. 模型制品仅可在用户点击“构建语义索引”后从固定模型标识加载到后端固定缓存目录；浏览器没有模型 URL、Token、缓存路径或供应商输入框。
3. 新索引采用版本并存、成功后切换；每个版本记录 provider、模型标识、模型制品指纹、维度、计数、状态和安全错误码。
4. 当模型未下载、空间不足、构建失败、构建中、索引缺失或版本不匹配时，后端只给出静态中文状态与关键词结果；禁止静默回退到旧哈希向量并声称语义检索可用。
5. 旧工作空间设置的 embeddingModel/API 路径不再参与知识库入库或查询；字段为兼容旧数据保留，不能作为 P9C 外发通道。
6. 首轮评测使用不少于 20 条完全合成、脱敏的中文招投标检索对；真实模型预检要求 Recall@5 不低于 0.80、NDCG@5 不低于 0.70，未达标不得激活新索引。

## 范围与非目标

**范围：**

- 离线模型提供者、版本化索引数据域、受控后台重建、搜索状态契约和知识库页面状态面板；
- 仅固定 BAAI/bge-small-zh-v1.5 的模型缓存、制品指纹与磁盘空间预检；
- pytest 的确定性假模型与不访问网络的浏览器 E2E；
- 明确的真实模型预检脚本和合成评测集。

**非目标：**

- 不支持模型下拉、多模型并行、GPU 专用路径、在线 API、用户自填 URL/Token、远程向量数据库或自动模型更新；
- 不删除历史 embedding_json，不批量修改用户文档，不改变 P9A/P9B、卡片、资源中心或项目生成语义；
- 不把真实模型下载或真实评测放入 pytest/Playwright；自动化测试不得触网；
- 不承诺业务真实语料效果；真实用户数据评测仍须另立脱敏批准流程。

## 全局安全与验收不变量

- 模型标识、缓存目录、最低可用磁盘和维度均由服务端常量/配置固定，所有 HTTP 请求体均不得传入 URL、路径、Token、模型名或维度。
- 只有后台重建可加载模型；搜索请求、应用启动、文档上传/重索引都不能触发模型下载。
- 模型加载和评测脚本不记录正文、查询、密钥、模型原始响应或本机绝对用户路径。
- 每张新表、每条索引、每个向量和每个状态查询都按 workspace 隔离；跨空间索引 ID 返回 404。
- 后端日志/API 只暴露固定错误码：model_unavailable、model_storage_insufficient、index_interrupted、index_failed、index_not_built、index_building。
- 所有新写或大改的模块顶注释必须包含“模块、用途、对接、二次开发”四字段，且均为简体中文。

## 严格文件白名单

### 任务 1：离线提供者与版本化后端索引

- 修改：backend/requirements.txt
- 修改：backend/app/core/config.py
- 修改：backend/app/core/database.py
- 修改：backend/app/main.py
- 修改：backend/app/models/entities.py
- 修改：backend/app/models/__init__.py
- 修改：backend/app/api/schemas.py
- 修改：backend/app/api/knowledge.py
- 修改：backend/app/services/embedding_service.py
- 修改：backend/app/services/knowledge_service.py
- 修改：backend/tests/test_knowledge_rag.py

### 任务 2：知识库状态面板与本地 E2E

- 修改：frontend/src/features/knowledge-base/types.ts
- 修改：frontend/src/features/knowledge-base/hooks/useKnowledgeBase.ts
- 修改：frontend/src/features/knowledge-base/pages/KnowledgeBasePage.tsx
- 修改：frontend/src/features/knowledge-base/pages/KnowledgeBase.css
- 新建：frontend/e2e/semantic-index.spec.ts
- 修改：frontend/package.json

### 任务 3：合成评测与交付文档

- 新建：backend/tests/fixtures/p9c_semantic_eval.json
- 新建：backend/scripts/semantic_model_preflight.py
- 修改：backend/tests/test_knowledge_rag.py
- 修改：docs/plans/2026-07-13-package-9-delivery-enhancement-plan.md
- 修改：docs/plans/2026-07-14-p9c-semantic-retrieval-decision-gate.md
- 修改：docs/HANDOFF-next.md
- 修改：docs/integration-checklist.md
- 新建：docs/p9c-offline-semantic-index-contract.md

任何不在本计划列出的改动，特别是模型 URL、前端模型设置、密钥、用户文件、全局代理或外部服务，均须暂停并由 Codex 重新规划。

## 任务 1：离线提供者、版本化索引和搜索契约

### 1.1 先写失败测试

在 backend/tests/test_knowledge_rag.py 新增并先运行以下类别的测试：

1. 相同输入经测试注入的离线提供者两次生成，输出维度为 512 且稳定；生产提供者未加载模型时必须返回 model_unavailable，不得触网。
2. 创建 active 旧索引后，新的索引构建失败，旧索引仍为 active 且其向量不被删除。
3. 两个工作空间分别构建索引，不能查询或读取对方索引；跨空间索引状态查询返回 404。
4. 没有 active 索引时，知识库搜索仍返回关键词命中，并给出 index_not_built/关键词降级状态；不得读取 legacy embedding_json 计算 vectorScore。
5. 有 active 索引时，只使用相同 indexId/维度的向量；维度不符、building、failed 和 interrupted 均不产生语义分数。
6. POST 重建仅创建 queued 运行；并发 queued/running 返回 409；启动时将残留 queued/running 收敛为 index_interrupted，保留 active 版本。
7. API 响应/数据库序列化中没有 apiKey、外部 URL、用户缓存路径、正文或供应方原始错误。

定向命令：backend\\.venv\\Scripts\\python.exe -m pytest -q tests/test_knowledge_rag.py -k "semantic or embedding or search"。

### 1.2 最小实现

1. 在实体中新增 SemanticEmbeddingIndexRow 与 SemanticChunkEmbeddingRow；前者保存状态、模型/制品指纹、维度、计数和固定错误码，后者仅保存 indexId、chunkId、workspaceId 和向量 JSON。建立工作空间/状态、indexId/chunkId 和唯一约束，且所有外键均在 ORM 与 SQLite 中成立。
2. 在 main 和 models 包注册新表；数据库启动期只补新表或必要索引，并将遗留 queued/running 索引标记为 failed/index_interrupted，不做旧块迁移。
3. 在 config 中固定模型 ID、512 维、后端 data 下的模型缓存目录和 5 GiB 最低可用空间；不得从 API、前端或工作空间设置读取这些值。
4. 在 embedding_service 实现 OfflineBgeEmbedder：只在受控后台重建中按固定模型 ID 加载；生成归一化 512 维向量并计算制品指纹。提供测试注入接口，使 pytest 能用确定性 512 维假模型且不导入/下载真实模型。
5. 停止把旧 embeddingModel/API 调用作为知识库向量来源。保留旧字段和 legacy embedding_json 只为数据兼容；搜索无 active P9C 索引时只做关键词排序。
6. 在 knowledge_service 实现创建、执行、查询和中断收敛：先写 running 索引及其全部新向量，验证计数/维度/指纹后单事务切 active 并 supersede 旧 active；任何异常仅将新索引置 failed，旧 active 保留。
7. 在知识库路由添加 GET /semantic-index、GET /semantic-index/{index_id}、POST /semantic-index/rebuild；重建由 BackgroundTasks 执行，无请求体。搜索响应追加 semanticStatus、semanticIndexId 和 vectorScore（仅语义 ready 时），现有字段保持兼容。
8. 运行定向测试至通过，再运行 backend\\.venv\\Scripts\\python.exe -m pytest -q tests/test_knowledge_rag.py。

### 1.3 Grok 自测与交接

- 运行 git diff --check；
- 仅发送 review_request，说明白名单、测试命令、未触网证据和任何依赖安装结果；
- 不提交、不推送。

## 任务 2：知识库语义索引状态面板和浏览器 E2E

### 2.1 先写失败 E2E

新建 frontend/e2e/semantic-index.spec.ts，使用 Playwright 本地路由拦截知识库语义索引 API，不启动真实模型，不请求外网。用例必须覆盖：

1. 页面显示“离线语义索引（本机）”、当前模型固定为 BAAI/bge-small-zh-v1.5、未构建时的关键词降级文案和“构建语义索引”按钮。
2. 点击构建后页面进入构建中、按钮禁用；轮询终态后显示已就绪、512 维和完成统计。
3. model_unavailable/failed 状态显示固定中文说明与“重试构建”，不显示远端错误、路径或 Token。
4. 页面没有模型 URL、Token、模型名称或缓存路径输入；现有上传、文件夹、卡片和图片入口仍可用。
5. 浏览器仅请求 /api 和本地 Vite 资源，不访问模型站点或任何外部 host。

定向命令：cd frontend && npm run test:e2e:semantic-index。

### 2.2 最小实现

1. 在类型与 Hook 中增加语义索引读模型、刷新、重建和受控轮询；API 失败不写 localStorage 伪造语义就绪状态。
2. 在 KnowledgeBasePage 增加紧凑状态面板，显示固定模型名、状态、计数、最后成功时间、关键词降级原因和构建/重试按钮；local 回退模式禁用操作并明确提示。
3. 在 CSS 增加小范围响应式样式；不改变文档筛选、上传、卡片或图片原有布局语义。
4. package.json 仅增加 test:e2e:semantic-index 脚本。
5. 运行 npm run lint、npm run build、npm run test:e2e:semantic-index 和既有 npm run test:e2e:cards。

### 2.3 Grok 自测与交接

同任务 1：仅发送 review_request，不提交、不推送；报告 E2E 是否完全本地。

## 任务 3：合成评测、真实模型预检和文档闭环

### 3.1 先写失败测试与合成评测集

1. 新建不少于 20 条合成中文招投标检索对；每条只包含虚构的项目、查询、候选分块 ID 和人工相关等级，禁止客户名称、真实投标文件、密钥和 URL。
2. 新增评测函数测试：计算 Recall@5、NDCG@5，缺失候选、重复 ID、维度不符和模型不可用均给受控失败而非虚假通过。
3. 新建显式预检脚本，只接受本计划固定模型和后端固定缓存目录；先检查 5 GiB 可用空间，再加载模型、输出制品指纹和合成指标。脚本不得读取应用知识库或写数据库，默认不得自动下载；模型缺失时给出中文准备说明和非零退出码。

### 3.2 验收与交付

1. Codex 在受控环境中单独运行真实模型预检；只有本地模型已明确可用时才记录指标。未安装模型不伪造通过，保留为可见部署前置。
2. Grok 运行定向/全量后端测试、前端 lint/build、两项知识库 E2E 和 git diff --check。
3. Codex 审查无外部正文/API 调用、无前端配置入口、无旧哈希伪语义、无破坏性迁移、无跨工作空间读取及无敏感日志。
4. Codex 更新 P9C 契约、总计划、交接和联调清单，单独提交中文验收文档并推送协作分支。

## 总验收矩阵

| 维度 | 通过标准 |
|---|---|
| 离线边界 | 生产服务没有外部 embedding API 调用；浏览器无模型站点请求；真实模型仅在显式重建/预检加载。 |
| 迁移恢复 | 新版本失败/中断不破坏 old active；成功后才切换 active；旧 embedding_json 未被覆盖。 |
| 隔离与状态 | 所有索引/向量/状态按 workspace 隔离；跨空间 404；每种降级有固定中文状态。 |
| 向量正确性 | active 索引向量均 512 维、已归一化、制品指纹匹配；旧/错维向量不参与余弦。 |
| 评测 | 合成集不少于 20 条，真实本地模型预检 Recall@5≥0.80、NDCG@5≥0.70；未达到不得激活新索引。 |
| 回归 | 后端全量 pytest、前端 lint/build、semantic-index E2E、knowledge-cards E2E、git diff --check 全部通过。 |

## 未完成项

- 本计划完成后仍不支持外部 embedding API、模型可选、GPU 专用优化、真实用户语料评测或自动模型更新。
- 若真实本地模型缺失或预检不达标，索引维持未构建/关键词降级状态；不得以测试假模型替代真实验收。
- 任何后续模型、数据出域或缓存位置变更，必须新建计划、重新评测并单独审查。
