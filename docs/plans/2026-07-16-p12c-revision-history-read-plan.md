<!--
模块：P12C-C1 editor-state 修订历史只读接口实施计划
用途：落实后端列表/详情的五文件白名单、failure-first 顺序与独立验收。
对接：p12c-revision-history-read-contract.md；P12C-A/B；main 路由组装。
二次开发：只读 API 与写恢复拆包；列表 SQL 禁止加载 snapshot_json。
-->

# P12C-C1 editor-state 修订历史只读接口实施计划

> **状态**：已冻结，待 Grok failure-first、实现与自测。
> **基线**：D3 实现=`b91a7ff`、闭环=`d07012b`；后端/前端串行全量 **764/263 passed**。

## 1. 交付目标

交付当前项目最近 10 条自动修订的只读元数据列表与单条按需详情。列表不读取正文，详情严格重验规范 13 键快照；两个端点沿用当前 workspace/bid_writer 权限与固定 `no-store`。不实现恢复和前端。

## 2. 实施顺序

1. 在精确五文件边界内先新增真实 HTTP/SQLite 专项，保持生产未改运行 failure-first；
2. 新增独立 history read service，先实现项目最小投影、列表五列投影和详情六列三重作用域查询；
3. 在 `api/schemas.py` 增加精确列表/详情响应模型，在新路由完成固定错误与 `no-store` 映射；
4. 只在 `main.py` 导入并挂载新 router；禁止顺手改模型、writer 或前端；
5. 串行运行专项、检查点/修订/editor-state/auth 回归和后端全量，再做五文件 `py_compile`、diff、暂存区与白名单检查；完成后仅发送 `review_request`。

## 3. Grok 最低自测

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests\test_p12c_revision_history_read.py --tb=line
.\.venv\Scripts\python.exe -m pytest -q tests\test_editor_state_revisions.py tests\test_p12c_checkpoint_restore_revisions.py tests\test_editor_state_checkpoints.py tests\test_editor_state_checkpoint_restore.py tests\test_editor_state_full_version.py tests\test_auth_rbac.py --tb=line
.\.venv\Scripts\python.exe -m py_compile app\services\editor_state_revision_history_service.py app\api\editor_state_revisions.py app\api\schemas.py app\main.py tests\test_p12c_revision_history_read.py
```

## 4. Codex 验收门

Codex 独立审查 SQL 投影、三重作用域、损坏收敛、完整只读零写、鉴权和来源/正文出域；拒绝用 ORM 整行加载列表、响应 shape 代替 SQL 捕获、跨项目冒充跨空间或只比行数。专项与扩大回归通过后运行后端串行全量；前端无改动，沿用单 worker、零重试 **263 passed** 基线。

## 5. 后续拆包

C1 闭环后再审计 C2 受限 revision restore；C2 必须决定是否抽取共享快照验证/写回原语，并重新证明 expectedStateVersion、恢复前安全检查点、revision 新时间点与失败全回滚。前端列表/详情/恢复入口继续后置，禁止与 C1 合包。
