<!--
模块：P9C-R1 固定离线模型运行时门契约
用途：冻结固定模型版本、显式制品准备、严格离线加载、缓存路径与真实预检边界。
对接：P9C 语义索引、embedding_service、semantic_model_preflight、运行时准备脚本与总路线图。
二次开发：不得加入任意模型/URL/Token/路径输入，不得把模型或缓存提交 Git，不得用假模型冒充真实就绪。
-->

# P9C-R1 固定离线模型运行时门契约

> **状态**：已完成全局只读审计并冻结，待 Grok failure-first、受限实现与 Codex 独立真实验收。
> **基线**：P9C 后端=`cc0d217`、前端=`a0bd84b`、运行时降级=`71c503c`、合成评测/预检=`585e502`；当前后端/前端全量基线 **800/284 passed**。

## 1. 审计结论与选包理由

当前 P9C 已有固定模型标识、512 维版本化索引、关键词降级、状态面板、合成评测和真实预检，但本机固定缓存为空，后端虚拟环境缺少 `sentence-transformers`、`torch`、`transformers`，真实预检稳定返回 `model_unavailable`/退出码 2。现有生产加载还未固定模型提交，且 `SentenceTransformer(...)` 未强制 `local_files_only=True`；用户点击重建时可能由第三方库隐式联网。默认相对 `upload_dir` 也会让缓存根随启动目录改变，与“固定缓存”文档不一致。

候选比较：

| 候选 | 用户价值 | 外部依赖 | 独立验收 | 结论 |
|---|---:|---:|---:|---|
| P9C 固定模型运行时门 | 高：让已有语义索引首次获得真实模型证据 | 中：固定 PyPI 运行时与单一公开模型 | 高：已有预检、合成集和索引测试 | **本包选择** |
| MinerU/Docling 自动部署 | 高 | 高：两个 CLI、模型、孙进程与多格式样本 | 中低 | 后续拆包 |
| Word `structure`/整章布局 | 中高 | 低 | 低：缺效果图、跨页与 WPS 视觉语义 | 未冻结 |
| 新合法外部标讯源 | 中高 | 高：来源授权、接口、签名或凭据 | 中 | 先做来源契约 |
| 多人协作/完整版本治理 | 高 | 高：身份、并发、审计和迁移面广 | 低 | 不作为下一最小包 |

## 2. 固定模型与制品契约

官方来源仅允许 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)，MIT 许可、24M 参数、512 维、CPU 首轮运行。固定不可漂移值：

| 项目 | 固定值 |
|---|---|
| 模型 ID | `BAAI/bge-small-zh-v1.5` |
| Hugging Face 提交 | `26478543676740eb665f803ca07f3f7f478857c8` |
| 权重 | `model.safetensors`，95,827,648 字节 |
| 权重 SHA-256 | `354763b9b1357bc9c44f62c6be2276321081ed2567773608c0d0785b61d5a026` |
| 固定必需文件总量 | 96,378,176 字节（约 91.91 MiB） |
| 固定维度 | 512 |
| 设备 | CPU |
| 最低剩余空间 | 5 GiB，准备、加载与预检均不可绕过 |

必需文件只有 `1_Pooling/config.json`、`config.json`、`config_sentence_transformers.json`、`model.safetensors`、`modules.json`、`sentence_bert_config.json`、`special_tokens_map.json`、`tokenizer.json`、`tokenizer_config.json`、`vocab.txt`。不下载重复的 `pytorch_model.bin`、README 或任意额外文件；缓存、依赖轮子和模型制品均不得进入 Git。

运行时顶层依赖固定为 `sentence-transformers==5.6.0`、`torch==2.12.1`、`huggingface-hub==1.23.0`。本包只固定这三个直接依赖，不生成全项目锁文件，不升级其他业务依赖。

## 3. 数据流与联网边界

```text
操作员显式 --download
  → 固定 huggingface.co / 固定模型 / 固定提交 / 固定文件表
  → 固定缓存目录的第三方缓存事务
  → 文件名、大小、提交和 safetensors SHA-256 校验
  → 无正文、查询、数据库或工作空间访问

用户显式“构建语义索引”
  → 生产加载器 local_files_only=True + 固定 revision + trust_remote_code=False
  → 本地模型编码
  → 既有 P9C 新版本构建、校验、成功后切 active
```

`prepare_semantic_model.py` 无参数时只读检查；只有字面 `--download` 才可联网。CLI 不提供模型、revision、URL、endpoint、Token、缓存路径、代理、跳过空间、跳过哈希、评测文件或数据库参数。下载明确 `token=False`，正文、查询、Cookie、API Key 和工作空间数据永不进入请求；可继承操作系统代理完成 HTTPS 连接，但不得把代理写入仓库或输出。

生产 `OfflineBgeEmbedder`、状态查询、搜索、应用启动、上传、重索引和真实预检全部禁止下载。准备失败保留既有有效缓存；加载或校验失败只给固定错误码，不泄露绝对路径、第三方异常或响应正文。

## 4. 路径、状态与错误契约

- 相对 `upload_dir` 必须锚定 `backend/`，从仓库根、`backend/` 或其他工作目录运行时得到同一 `backend/data/semantic-models`；绝对 `upload_dir` 仍以其父目录的 `data/semantic-models` 为准。
- 准备工具与预检不导入知识库服务、不打开数据库、不读取 `kb_chunks`、查询或用户文件。
- 未安装依赖固定为 `deps_missing`；缓存缺失为 `model_unavailable`；空间不足为 `model_storage_insufficient`；提交/文件/大小/哈希不符统一为 `model_artifact_mismatch`；联网失败为 `model_download_failed`。
- API 与前端契约保持不变，不新增运行时准备 API，不在浏览器展示缓存路径、提交、哈希或下载按钮。
- 已有 active 索引但进程尚未加载模型时继续返回 `active + model_unavailable`，不得改写数据库或显示“已就绪”。

## 5. 严格实现白名单

Grok 只允许修改以下 6 个文件：

1. `backend/requirements.txt`
2. `backend/app/core/config.py`
3. `backend/app/services/embedding_service.py`
4. `backend/scripts/semantic_model_preflight.py`
5. `backend/scripts/prepare_semantic_model.py`（新建）
6. `backend/tests/test_semantic_model_runtime.py`（新建）

禁止修改 API、Schema、数据库实体/迁移、知识库服务、前端、E2E、现有评测集、P8D/P8E、启动脚本或任何用户数据。文档、提交与推送仍由 Codex 负责。

## 6. 验收门

自动化门：

- failure-first 必须在生产代码未改时因“准备脚本/固定 revision/严格离线参数不存在”失败；不得用语法错误或环境缺包充当红测。
- 新专项覆盖固定提交、文件表、大小/哈希、无任意 CLI 参数、无 Token、同缓存路径、严格离线加载、损坏/缺失/下载失败保留旧缓存及固定脱敏错误。
- P9C 语义索引受影响回归、后端串行全量、`py_compile`、六文件白名单与 `git diff --check` 全绿。

Codex 真实门：

1. 用 `--no-cache-dir` 安装三个固定直接依赖，避免再次扩大 C 盘 pip 缓存；
2. 先运行无参数检查得到真实未就绪，再显式运行一次 `--download`；
3. 下载后无参数检查必须验证固定提交、10 个必需文件、总量和 safetensors SHA-256；
4. 真实预检必须输出固定制品指纹、20 条合成评测的 Recall@5≥0.80、NDCG@5≥0.70，退出码 0；
5. 预检不得读取/写入应用数据库或用户知识库，真实指标不得用注入假模型替代。

## 7. 非目标

不实现真实用户语料评测、排序权重调优、其他模型、GPU、在线 embedding、自动更新、后台静默下载、模型打包进 Git/安装包、服务端下载 API、前端下载按钮、MinerU/Docling、Word 版式、外部标讯或多人协作。真实预检通过只证明固定合成集与运行时就绪，不宣称真实业务检索质量已经完成。
