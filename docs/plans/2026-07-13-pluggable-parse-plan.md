<!--
模块：阶段4包8可插拔解析调度方案
用途：锁定 engine 契约、lightweight 默认、测试 fake 边界、安全规则与真实 MinerU/Docling 部署决策。
对接：backend parse_engines / task_service._run_parse；外置 parse-callback；HANDOFF / 路线图。
二次开发：禁止静默回退引擎；禁止默认 requirements 安装 MinerU/Docling；parseStrategy 未接线勿假称已支持。
-->

# 可插拔解析调度（阶段 4 功能包 8 MVP）

> **状态（2026-07-13）**：**已验收并推送**（SHA=`6db1586`，提交标题「实现可插拔解析引擎调度」）。
> **基线父提交**：`834969e`（包 7 文档状态同步后协作分支干净点）。
> **包 7 已推送**：`2c7b3e0`（实现响应矩阵字段级三方合并）。
> **分支**：`collab/grok-code-codex-review`。
> **明确未做**：内嵌真实 MinerU / Docling；改 `parseStrategy` 接线；改 callback 默认 token 策略；包 9。

## 1. 现状

| 能力 | 状态 |
|------|------|
| 轻量解析 `parse_service.parse_file_to_markdown` | 已有；txt/md/docx/pdf 本机提取 |
| 异步 parse 任务 | 已有；成功写 `editor-state.parsedMarkdown` |
| 外置 MinerU 回传 `POST .../parse-callback` | 已有；可选 `X-Local-Token` |
| 可插拔引擎注册/调度 | **已完成并推送**（`6db1586`） |
| 真实 MinerU/Docling 内嵌或 subprocess | **不做**（部署决策，见 §5） |
| `settings.parseStrategy=local/ask` 驱动内嵌引擎 | **未接线**（设置项仍存在，但不选择引擎） |

## 2. engine 契约（冻结）

| 项 | 规则 |
|----|------|
| 默认引擎 | `lightweight`（payload 缺省 / null / 空白字符串） |
| payload 字段 | `engine`：仅**非空字符串**可作为名称 |
| 非法类型 | bool / 数字 / 对象等 → 任务 `failed`，错误含「解析引擎不可用」 |
| 未注册名称 | 任务 `failed`，错误含「解析引擎不可用」；**禁止**静默回退 lightweight |
| 成功 result | 保留 `parsedMarkdown`（摘要）、`chars`、`filename`，新增 **`engine`** |
| 全文权威 | 仅 `editor-state.parsedMarkdown`；失败时**不覆盖**已有全文 |
| 生产注册表 | 默认**仅** `lightweight`；无 fake |
| 测试 fake | 仅经 `parse_engines.register_engine` / monkeypatch 注入；测后 `reset_registry` |
| callback | 语义不变；仍可独立覆写 `parsedMarkdown`；result 兼容既有 `source` 字段 |

## 3. 模块边界

```
task_service._run_parse
  → parse_engines.resolve_engine_name(payload.engine)
  → parse_engines.parse_with_engine(name, path, filename)
       → LightweightParseEngine → parse_service.parse_file_to_markdown
       → (测试) FakeParseEngine → 固定 fixture markdown
  → 成功才写 ProjectEditorStateRow.parsed_markdown + task.result.engine
```

**禁止**在 `parse_engines` 中：`subprocess` / `Popen` / `os.system` / `requests` / 任意路径穿越 / shell。

## 4. 测试矩阵

| 场景 | 期望 |
|------|------|
| 无 payload / 空白 engine | success，`result.engine=lightweight`，parsedMarkdown 与旧行为一致 |
| 显式 `engine=lightweight` | 同上 |
| 测试注册 fake + `engine=fake` | success，fixture 全文写入 editor-state，`result.engine=fake` |
| `engine=docling` 等未注册 | failed，错误含「解析引擎不可用」，旧 parsedMarkdown 不变，fake 不被调用 |
| `engine=1` / `true` | failed，同上 |
| callback 默认空 token | 无 Header 仍 200（**部署风险**：任意本机可达方可能回传**） |
| callback 配置 token | 错误/缺失 Header → 401；正确 Token → 200 写全文 |

## 5. 真实 MinerU / Docling 部署决策（本包不实现）

| 选项 | 结论 |
|------|------|
| 默认 `requirements.txt` 安装 | **否**（体积、二进制、许可与本机日用冲突） |
| 进程内嵌调用 | **否**（安全面与超时难控） |
| 外置 CLI/服务 + `parse-callback` | **推荐**后续独立 task：固定可执行路径白名单、workspace/project 绑定、token 强制 |
| Docling | **未接**；若引入须新 engine 名 + 独立安全审查，不得 silently alias 到 lightweight |
| `parseStrategy` | 设置 UI 的 local/ask **当前不驱动**本调度器；接线须另开 task，避免假称已支持 |

## 6. Token 默认空的风险（记录）

- `LOCAL_PARSER_TOKEN` / `settings.local_parser_token` **默认为空** → callback **不校验** `X-Local-Token`。
- 保密机本机回环可接受；多用户/局域网暴露时必须配置非空 token（本包仅加测试，不改默认）。

## 7. 已合入实现文件（SHA=`6db1586`）

- `backend/app/services/parse_engines.py`（新）
- `backend/app/services/task_service.py`
- `backend/tests/test_parse_export.py`
- `backend/tests/test_async_and_callback.py`
- `backend/tests/test_parse_engines.py`（新）
- 文档闭环另批仅改：本文件、`docs/plans/2026-07-12-bid-writer-roadmap.md`、`docs/HANDOFF-next.md`、`docs/integration-checklist.md`

## 8. 验收命令

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q tests/test_parse_engines.py tests/test_parse_export.py tests/test_async_and_callback.py

cd ..\frontend
npm run lint
npm run build

cd ..
git diff --check
# 静态：parse_engines.py 无 subprocess/Popen/os.system/requests；requirements 无 mineru/docling
```

## 9. 明确不做

- 安装/内嵌真实 MinerU、Docling
- 修改 `requirements.txt`、`parse_callback.py`、`config.py`、前端、DB/API 路由
- 强制默认 token 非空、任意新 markdown 体积上限（属部署决策）
- 包 9 交付增强、多角色
- 本实现提交已验收并推送；文档闭环未获 Codex `ack` 前仍不得 commit / push
