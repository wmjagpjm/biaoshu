# P9C 运行时模型就绪状态修正计划

> **状态：** Codex 在 P9C 任务 2 独立审查中发现并冻结；仅修正状态契约，不扩展模型、网络或数据范围。
> **基线：** `a0bd84b`（P9C 任务 1 与任务 2 均已提交推送）。

**目标：** 服务重启后，若版本化索引仍为 `active`、但离线 BGE 模型尚未在内存就绪，状态 API 和页面必须明确显示 `model_unavailable` 与关键词降级；不得把持久化索引状态误称为可立即语义检索。

**问题证据：** 当前搜索路径在模型未就绪时已返回 `model_unavailable` 且不会加载/下载模型；但 `GET /api/knowledge/semantic-index` 仅返回数据库中的 `active`，知识库面板会显示“已就绪”。两者不一致，违背 P9C 的可见降级决策。

## 不变量

1. `active` 仍表示已成功构建的版本化索引，不能因进程重启或模型未加载被改写为 `failed`。
2. 状态 API 只做 `OfflineBgeEmbedder.is_ready()` 的只读判断；不得在状态查询或浏览器轮询中加载模型、下载权重、读取正文或触网。
3. 模型未就绪时，响应保留 `status=active` 与索引 ID，但临时输出固定 `errorCode=model_unavailable`；不写回数据库、不记录路径/异常原文。
4. 前端必须优先按 `errorCode=model_unavailable` 显示“模型不可用”和关键词降级，即使 `status=active`；按钮显示“重试构建”。
5. 仅使用固定 BAAI/bge-small-zh-v1.5 与 512 维，禁止模型 URL、Token、路径、供应商或 localStorage 配置。

## 严格文件白名单

- 修改：`backend/app/services/knowledge_service.py`
- 修改：`backend/tests/test_knowledge_rag.py`
- 修改：`frontend/src/features/knowledge-base/types.ts`
- 修改：`frontend/e2e/semantic-index.spec.ts`

## 实施与验收

1. 先写后端失败测试：假模型构建成功为 `active` 后卸载/清除注入，`GET /semantic-index` 仍返回原索引 ID 和 `active`，但 `errorCode=model_unavailable`；断言没有调用模型加载。
2. 最小修改状态汇总：只在读模型返回值覆盖临时错误码，不修改数据库行，也不改变搜索的无触网语义。
3. 先写浏览器失败用例：模拟 `active + model_unavailable`，断言面板显示“模型不可用”、关键词降级和“重试构建”，不显示“已就绪”。
4. Grok 只实现、自测、发送 `review_request`；Codex 审查后独立运行后端定向与全量回归、前端 lint/build、语义索引 E2E 与 cards E2E，再单独提交推送。

## 非目标

- 不在状态查询中从磁盘加载模型；显式点击“重试构建”仍是唯一允许加载模型的入口。
- 不改变索引迁移、向量表、评测集、模型依赖、缓存位置或任何 P9A/P9B 行为。
- 不伪造真实模型预检结果；该事项仍留在 P9C 任务 3。
