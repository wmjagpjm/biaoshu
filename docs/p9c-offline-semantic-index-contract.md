# P9C 离线语义索引契约与验收记录

> **状态：** 实现、自动化验收与文档闭环已完成；真实模型预检待本机运行时准备完成后执行。
> **适用范围：** 仅适用于知识库的本机离线 BAAI/bge-small-zh-v1.5（512 维、CPU）语义索引；不授权在线向量 API、模型切换、GPU 路径或真实用户语料评测。

## 1. 目标、范围与非目标

**目标：** 在不覆盖历史 `kb_chunks.embedding_json` 的前提下，为每个工作空间建立可恢复、版本化、可观察降级的离线语义索引。未存在可用活动索引时，知识库继续返回关键词结果，但不得把历史哈希向量描述为语义检索。

**已实现范围：**

- 固定模型标识 `BAAI/bge-small-zh-v1.5`、512 维、CPU、服务端固定缓存目录与 5 GiB 最低可用空间；
- 语义索引运行与分块向量两张隔离表；新版本先构建，成功后才切换为 `active`，失败或中断保留旧活动版本；
- 仅在用户点击“构建语义索引”后由后台重建路径加载模型；搜索、应用启动、上传和重索引不会加载或下载模型；
- 状态读取、单索引读取与无请求体重建 API，以及前端状态面板、关键词降级文案和本地 Mock E2E；
- 20 条完全合成的中文招投标评测查询、固定数据契约、Recall@5/NDCG@5 预检脚本与受控失败码。

**明确非目标：**

- 不提供模型 URL、Token、缓存路径、模型名、维度或供应方的前端/HTTP 输入；
- 不向外发送知识库正文或查询，不调用 OpenAI 兼容 embedding API，不使用远程向量库；
- 不删除或迁移旧 `embedding_json`，不声称其为语义向量；
- 不在 pytest、Playwright 或预检脚本中自动下载模型、安装依赖、读写知识库或数据库；
- 不以合成集结果宣称真实业务语料效果。

## 2. 索引、状态与隔离契约

| 项目 | 固定契约 |
|---|---|
| 模型 | `BAAI/bge-small-zh-v1.5`，`offline_bge`，512 维，CPU。 |
| 缓存与空间 | 缓存目录由服务端 `upload_dir` 推导的固定 `semantic-models` 子目录；重建和预检均先要求至少 5 GiB 可用空间。 |
| 版本切换 | 新运行写入 `queued`/`running`；全部向量、维度、计数和制品指纹验证成功后才切为 `active`，旧活动版本再转为历史状态。 |
| 失败恢复 | `model_unavailable`、空间不足、构建失败或中断只影响新运行；旧 `active` 不删除。启动期将遗留 `queued`/`running` 收敛为 `index_interrupted`。 |
| 工作空间 | 索引运行、分块向量和状态查询均以 `workspaceId` 过滤；跨空间索引 ID 返回 404。 |
| 搜索 | 只读取当前 `active`、同索引 ID、同维度的 P9C 向量；否则 `vectorScore=0` 且仅关键词排序。绝不读取历史 `embedding_json` 计算语义分数。 |
| 可见状态 | `model_unavailable`、`model_storage_insufficient`、`index_interrupted`、`index_failed`、`index_not_built`、`index_building` 与 `ready`；页面使用固定中文文案，不回显路径、正文、密钥或供应方原始错误。 |

## 3. HTTP 与前端契约

| 方法 | 路径 | 约束 |
|---|---|---|
| GET | `/api/knowledge/semantic-index` | 只返回当前工作空间的脱敏状态；不含缓存绝对路径、正文、密钥或模型 URL。 |
| GET | `/api/knowledge/semantic-index/{indexId}` | 只读取当前工作空间；跨空间或不存在均为 404。 |
| POST | `/api/knowledge/semantic-index/rebuild` | 无请求体，返回 202；同空间已有 `queued`/`running` 时为 409；后台任务是唯一可加载模型的生产路径。 |
| GET | `/api/knowledge/search` | 始终返回 `semanticStatus`；无可用索引时保留关键词结果，`vectorScore` 为 0。 |

- 知识库页始终展示固定模型名和 512 维；即使服务端返回脏模型名或维度，前端也不会展示为有效配置。
- 本地演示回退时，语义索引面板显示不可构建，构建按钮禁用；不向 localStorage 写入伪造的语义就绪状态。
- 浏览器只请求本机 Vite 资源与 `/api`，不得访问模型站点或其他外部主机。

## 4. 合成评测与真实预检契约

- 固定评测文件：`backend/tests/fixtures/p9c_semantic_eval.json`；仅含 20 条虚构中文招投标查询、候选 ID、候选文本和 0 至 3 的人工相关度。
- 每个查询至少有一个 `relevance >= 1` 的候选；候选顺序已打散，不能通过原始位置取得高分。
- 评测文件必须显式满足：`schemaVersion=1`、固定模型标识、`dimension=512`、`Recall@5 >= 0.80`、`NDCG@5 >= 0.70`。缺字段、错值或降低阈值均受控失败。
- 预检命令为 `backend/.venv/Scripts/python.exe backend/scripts/semantic_model_preflight.py`。它没有下载、外部评测路径或跳过磁盘检查参数；只读取固定合成集，先检查 5 GiB 空间，再以 `local_files_only=True` 加载本地缓存。
- 真实预检成功时才记录制品指纹、20 条合成评测的 Recall@5/NDCG@5；任一指标不足即失败，不能作为激活索引的证明。

## 5. 已交付提交与独立验收

| 提交 | 内容 |
|---|---|
| `cc0d217` | P9C 离线提供者、版本化索引、搜索状态与后端测试。 |
| `a0bd84b` | 语义索引状态面板和本地浏览器 E2E。 |
| `71c503c` | 已有活动索引但模型未就绪时的只读关键词降级状态。 |
| `585e502` | 合成评测集、固定预检脚本、评测契约与负向测试。 |

Codex 独立验收结果：

- 后端全量按五组以 `PYTHONHASHSEED=0` 运行，共 **251 passed**，仅 1 条既有 Starlette/httpx 弃用警告；
- P9C 语义索引 E2E **9 passed**，知识卡片 E2E **1 passed**；均为本机隔离后端与路由拦截，不访问模型站点；
- `npm run lint` 通过（0 错误、0 警告）；`npm run build` 通过，仅保留既有大包体积提示；
- `git diff --check` 通过；静态审查确认没有下载开关、跳过磁盘检查、外部评测路径、在线 embedding 调用、前端模型配置入口或旧哈希伪语义路径。

## 6. 真实模型前置条件与后续操作

本机当前未安装可供预检加载的模型缓存；Codex 独立执行预检得到 `errorCode=model_unavailable` 与退出码 2，这是预期的真实状态，不是验收通过。未下载模型、未安装依赖、未写入知识库或数据库，也未记录虚假的 Recall@5/NDCG@5。

后续只有在受控运行时已安装 `backend/requirements.txt` 所列离线运行依赖，且用户在知识库页面明确点击“构建语义索引”完成固定模型加载后，才可再次运行预检。只有预检实际达到 Recall@5≥0.80、NDCG@5≥0.70，且索引构建成功，才可把该工作空间记为真实语义索引已就绪；在此之前页面和搜索必须保持“未构建/模型不可用/关键词降级”的可见状态。
