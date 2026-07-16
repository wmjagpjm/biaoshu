<!--
模块：P9C-R1 固定离线模型运行时门实施计划
用途：把固定制品准备、严格离线生产加载、路径确定性与真实预检拆成可逐步验证的 Grok 任务。
对接：docs/p9c-fixed-model-runtime-gate-contract.md、P9C 既有实现、后端测试与 Codex 真实验收。
二次开发：Grok 不得提交/推送或真实下载模型；任何白名单外改动必须停止并请求 Codex。
-->

# P9C-R1 固定离线模型运行时门实施计划

> **给 Grok：** 必须按本计划逐项 failure-first；只实现、自测并发送 `review_request`，不得提交或推送。
> **完成状态：** 已完成；冻结=`cd70ef0`、实现=`b53dcce`，Codex 独立真实验收通过。

**目标：** 为既有 P9C 增加固定提交、显式准备、严格离线加载和真实制品验收门，使语义运行时可重复且不会在业务请求中隐式联网。

**架构：** 单一操作员 CLI 负责固定官方制品的显式准备与校验；生产加载器和预检只从同一确定缓存读取。版本、文件表、大小、权重哈希和直接依赖固定在服务端代码，HTTP/前端零输入、数据库零变更。

**技术栈：** Python 3.13、pydantic-settings、sentence-transformers、PyTorch CPU、huggingface-hub、pytest、SHA-256。

---

## 1. 文件边界与通用规则

仅允许：

- 修改 `backend/requirements.txt`
- 修改 `backend/app/core/config.py`
- 修改 `backend/app/services/embedding_service.py`
- 修改 `backend/scripts/semantic_model_preflight.py`
- 新建 `backend/scripts/prepare_semantic_model.py`
- 新建 `backend/tests/test_semantic_model_runtime.py`

所有新文件顶必须有“模块、用途、对接、二次开发”四字段中文注释。生产代码不得导入测试模块；测试不得触网、不得安装依赖、不得写应用数据库或真实缓存。Grok 不运行真实 `--download`，真实依赖/模型验收由 Codex 执行。

## 2. 任务 1：先写固定门失败测试

### 步骤 1：创建专项测试

在 `backend/tests/test_semantic_model_runtime.py` 先写以下测试，所有网络和第三方加载均用注入或 `monkeypatch`：

1. `test_relative_cache_dir_is_backend_anchored_across_cwd`：相对 `upload_dir` 在仓库根、`backend/` 和临时 cwd 下解析为同一 `backend/data/semantic-models`；绝对路径语义保持既有契约。
2. `test_runtime_model_contract_is_fixed_and_rejects_override`：模型 ID、512 维与完整 revision 固定；错误环境覆盖在 Settings 校验期失败。
3. `test_runtime_loader_is_revision_pinned_and_strictly_offline`：假 `SentenceTransformer` 精确收到固定 ID、固定 revision、`cache_folder`、`device="cpu"`、`local_files_only=True`、`trust_remote_code=False`，不得出现下载调用。
4. `test_prepare_cli_has_only_explicit_download_switch`：解析器只允许无参数检查或 `--download`；URL、模型、revision、Token、路径、endpoint、代理、跳过空间/哈希均拒绝。
5. `test_prepare_download_uses_fixed_snapshot_and_no_token`：假下载器只收到固定 repo/revision、10 文件白名单、`token=False`；不下载 `pytorch_model.bin`。
6. `test_artifact_manifest_rejects_missing_size_and_hash_mismatch`：缺文件、大小异常、权重 SHA-256 异常均固定 `model_artifact_mismatch`，错误不含绝对路径。
7. `test_failed_download_preserves_existing_valid_snapshot`：已有有效制品时下载异常不删除/改写其文件；固定返回 `model_download_failed`。
8. `test_prepare_and_preflight_do_not_import_database_or_knowledge_service`：模块导入和注入执行路径不接触数据库/知识库。

### 步骤 2：运行红测

在 `backend` 执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_model_runtime.py
```

预期：因 `prepare_semantic_model.py`、固定 revision 或严格离线参数尚不存在而失败；记录精确失败数与首个业务失败，不得修改断言迎合现状。

## 3. 任务 2：固定配置、依赖与缓存根

### 步骤 1：最小配置实现

- 在 `config.py` 定义固定模型 ID、revision、维度与缓存目录名常量；Settings 只接受字面固定值，错误覆盖抛校验异常。
- 修正 `resolve_semantic_model_cache_dir`：相对 `upload_dir` 先锚定 `backend/`，再取其父目录下固定 `data/semantic-models`；绝对 `upload_dir` 保持以其父目录锚定。
- 不增加 HTTP、`.env.example` 或前端输入，不允许任意缓存路径逃逸。

### 步骤 2：固定直接依赖

只把 P9C 三个直接依赖收紧为：

```text
sentence-transformers==5.6.0
torch==2.12.1
huggingface-hub==1.23.0
```

不得顺手锁定或升级 FastAPI、SQLAlchemy、Pillow 等其他依赖。

### 步骤 3：运行专项

重复运行任务 1 命令。预期：配置/路径类测试通过，准备工具相关测试仍因未实现而失败。

## 4. 任务 3：生产加载严格离线

### 步骤 1：修改加载器

`OfflineBgeEmbedder.ensure_loaded_for_rebuild` 必须：

- 继续只允许既有后台重建调用；
- 使用固定模型 ID/revision、固定缓存、CPU；
- 明确传 `local_files_only=True` 与 `trust_remote_code=False`；
- 加载前调用共享制品校验，缺失/损坏映射固定 `model_artifact_mismatch` 或 `model_unavailable`；
- 不打印第三方异常、绝对路径或缓存内容；
- 保留测试注入与既有 512 维/归一化/指纹语义。

### 步骤 2：运行加载器专项与既有回归

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_model_runtime.py -k "loader or contract or cache_dir"
.\.venv\Scripts\python.exe -m pytest -q tests/test_knowledge_rag.py -k "semantic"
```

预期：全部通过；既有 fake 模型不加载真实依赖、不触网。

## 5. 任务 4：显式准备工具与共享制品校验

### 步骤 1：新建准备脚本

`prepare_semantic_model.py` 必须：

- 无参数只读检查；只有 `--download` 可联网；其他参数由 argparse 拒绝；
- 固定 repo/revision、10 个文件与精确大小；`model.safetensors` 再校验固定 SHA-256；
- 下载调用固定 `token=False`，禁止远程代码和重复 `pytorch_model.bin`；
- 下载前检查 5 GiB，失败不删除已有有效快照；
- 输出单一有界 JSON：`ok/errorCode/modelId/revision/artifactFingerprint/fileCount/totalBytes`，不含绝对路径、第三方正文或异常原文；
- 不导入数据库、知识库服务，不读取用户文件。

固定模型/revision 常量放在 `config.py`，纯制品表与校验函数放在 `embedding_service.py`；准备脚本和预检只导入这些无副作用接口。生产服务不得反向导入 CLI 脚本，禁止循环导入或在 import 时执行 CLI/网络。

### 步骤 2：收紧真实预检

`semantic_model_preflight.py` 复用固定 revision 与制品校验；`SentenceTransformer` 同样传 `local_files_only=True`、固定 revision、CPU、`trust_remote_code=False`。缓存缺失/损坏的提示改为运行显式准备命令，但 CLI 仍无下载、路径、评测文件或跳过参数。

### 步骤 3：运行专项至全绿

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_semantic_model_runtime.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_knowledge_rag.py -k "semantic"
```

## 6. 任务 5：Grok 自测与交接

从仓库根依次运行：

```powershell
backend\.venv\Scripts\python.exe -m py_compile backend\app\core\config.py backend\app\services\embedding_service.py backend\scripts\semantic_model_preflight.py backend\scripts\prepare_semantic_model.py backend\tests\test_semantic_model_runtime.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_semantic_model_runtime.py
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_knowledge_rag.py -k "semantic"
backend\.venv\Scripts\python.exe -m pytest -q backend\tests\test_knowledge_rag.py
git diff --check
```

再核对 `git diff --name-only` 恰好为六文件白名单。不得真实安装依赖或下载模型；缺依赖环境下无参数准备检查与预检应受控退出，不能崩溃或触网。

Grok 最终只发送 `review_request`，正文包含：failure-first 精确结果、最终各命令结果、六文件清单、未触网证据、风险与未做项。不得 `git add`、commit 或 push。

## 7. Codex 独立验收

Codex 先做代码/测试反假绿审查，再复跑 Grok 命令和后端串行全量。自动化通过后，才在当前后端 venv 以 `--no-cache-dir` 安装三个固定直接依赖，并依次执行：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py --download
backend\.venv\Scripts\python.exe backend\scripts\prepare_semantic_model.py
backend\.venv\Scripts\python.exe backend\scripts\semantic_model_preflight.py
```

真实通过标准：固定 10 文件/96,378,176 字节/权重 SHA-256 正确；预检退出 0，输出真实 artifact fingerprint、Recall@5≥0.80、NDCG@5≥0.70；C 盘仍高于 5 GiB；工作区无模型/缓存/数据库产物。任一门失败不得声称 P9C 真实运行时就绪。

## 8. 后续边界

本包完成后，只能声称“固定模型运行时和合成集真实预检已就绪”。真实用户语料评测、排序调优、GPU、其他模型、自动更新、模型打包/安装器、在线 embedding 与浏览器配置仍须另立任务。

## 9. 实施闭环

1. Grok 首版经 Codex 受限审查发现 endpoint 可被环境漂移、5 GiB 门可覆盖、准备脚本导入数据库链、有效缓存仍触网、依赖错误合并及测试大文件假绿等问题；返修任务=`msg_fe78f5e4db5b4365a69d5ea86f6e766d`。
2. Grok 返修红测为 **11 failed / 6 passed**，最终专项/语义回归/知识库完整回归为 **17 / 21（另 7 deselected）/28 passed**；最终回执=`msg_60f7048c744c4267aadcfd59fb0aa08c`。
3. Codex 独立安装固定依赖并校验官方 torch 轮子 SHA-256，显式下载固定制品后复核 10 文件、总字节、权重哈希、缓存内链接和制品指纹；无参数准备与严格离线生产加载均通过。
4. 真实预检为 `Recall@5=1.0`、`NDCG@5=0.927295`；后端全量 **817 passed**，`py_compile`、`pip check`、`git diff --check`、六文件白名单与缓存忽略门全部通过。
5. 实现已由 Codex 以中文提交 `b53dcce` 推送；验收确认=`msg_05b4ca2b4d084e928b70cbbc759bc33a`。模型缓存只保留在本机，不属于 Git 交付物。
