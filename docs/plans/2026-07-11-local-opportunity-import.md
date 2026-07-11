# 本地标讯离线导入实施计划

> **协作约定**：Codex 负责逐项实现、浏览器联调与回归；Grok 在接口边界和最终差异两个节点进行只读反方审查。本计划不创建 Git 提交，除非用户明确要求。

**目标：** 让个人自托管用户可将本机 CSV 或 JSON 标讯清单批量导入当前工作空间，无需外网、API Key 或手工逐条录入。

**架构：** 导入文件仅在请求生命周期内解析，应用不写入上传目录或持久化原始文件。服务层先规范化并校验整批数据，任一非法行时回滚为零写入；通过后在单次事务内插入。可选 `sourceKey` 持久化为工作空间内来源键，重复导入时跳过已有键；未提供来源键的行按新记录处理。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、Python 标准库 `csv/json`、React、TypeScript、pytest、Vite。

---

## 冻结的 v1 契约

- 路由：`POST /api/opportunities/import`，multipart 字段固定为 `file`；仅接受 `.csv`、`.json`，不保存原始文件。
- 输入上限：默认 2 MiB、2,000 行，由 `MAX_OPPORTUNITY_IMPORT_BYTES`、`MAX_OPPORTUNITY_IMPORT_ROWS` 配置。
- JSON 可为数组，或包含 `items`/`opportunities` 数组的对象；CSV 需首行表头，UTF-8 或 UTF-8 BOM。
- 接受中英文表头/键：`title/标题`、`buyer/采购人`、`region/地区`、`budgetLabel/budget_label/预算`、`deadline/截止日期`、`tags/标签`、`summary/摘要`、`sourceLabel/source_label/来源`、`sourceKey/source_key/来源键`。
- `deadline` 必须为 `YYYY-MM-DD`；标题非空；CSV 标签按逗号、中文逗号、分号或竖线拆分；JSON 标签必须为字符串数组或字符串。
- 整批出现格式、字段或日期错误时返回 422 和行号明细，**一条也不写入**。合法导入返回 201 与 `inserted/skipped/total`。
- `sourceKey` 仅是用户提供的本地不透明去重键；同 workspace 重复键跳过。不得写入外部 URL、抓取状态、密钥或附件。
- 所有记录归属当前 `X-Workspace-Id`；状态仍由既有服务端 `deadline` 计算。外网抓取、RSS、资源导入、部分成功、导入预览、模板下载和后台任务均不在本轮。

## 任务 1：先写失败测试

**文件：**

- 修改：`backend/tests/test_opportunities.py`

1. 用 JSON multipart 导入两行，断言 201、列表可见、截止状态由服务端计算。
2. 同一 `sourceKey` 再次导入，断言第二次 `inserted=0/skipped=1`，没有重复记录。
3. 用中文表头 CSV 导入，断言标签拆分、预算和来源映射正确。
4. 传入“一行合法、一行截止日非法”的文件，断言 422 含行号且列表仍为空。
5. 创建第二 workspace，断言其列表看不到默认 workspace 导入记录；超限/扩展名非法返回 400。

## 任务 2：实现模型、配置、服务与路由

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/core/database.py`
- 修改：`backend/app/core/config.py`
- 修改：`backend/.env.example`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/services/opportunity_service.py`
- 修改：`backend/app/api/opportunities.py`

1. 为 `BidOpportunityRow` 增加可空 `source_key`，以 `(workspace_id, source_key)` 唯一约束和 SQLite 兼容唯一索引保障有键导入幂等；空键允许多条记录。
2. 增加导入配置、响应 Schema 与校验异常；所有新公开函数补中文用途、对接和必要的二次开发说明。
3. 使用 Python 标准库解析 CSV/JSON；按冻结映射生成统一 payload，文件与行上限在写库前检查。
4. 批量预校验所有行，再查同 workspace 的既有来源键；发生任意错误回滚，成功时单次提交并返回计数。
5. 路由层只读取 multipart、映射 400/422/409，不将文件写入磁盘，也不直接读写 ORM。

## 任务 3：添加标讯页导入入口

**文件：**

- 修改：`frontend/src/features/bid-opportunity/types.ts`
- 修改：`frontend/src/features/bid-opportunity/hooks/useOpportunities.ts`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunityPage.tsx`
- 修改：`frontend/src/features/bid-opportunity/pages/BidOpportunity.css`

1. 在 Hook 中用 `FormData` 调用导入 API；成功后刷新列表并返回服务端统计，失败时沿用既有错误条。
2. 在标题栏新增图标加文字的“导入标讯”命令，打开紧凑文件弹层；文件选择仅接受 CSV/JSON。
3. 导入成功后在弹层显示 `inserted/skipped/total`；失败保留文件选择和服务端错误，不伪造局部成功。
4. 不改变现有筛选、编辑、删除、立项信息架构；卡片与弹层圆角不超过 8px。

## 任务 4：复审、联调与交接

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`
- 更新：本文件执行记录

1. 运行专项和全量 `pytest -q`、`npm run build`、`git diff --check`。
2. 浏览器手动导入 UTF-8 CSV，确认列表、筛选、立项和重复导入跳过；清理仅由本轮创建的临时记录。
3. 提供实体、解析服务、路由、Hook 与页面差异给 Grok，重点审查路径/编码、整批原子性、来源键隔离、超限、外网残留和前端误报。
4. 更新接口表、手工验收、验收基线、注释齐备表和未完成边界；不提交密钥、数据库、导入样本或用户文件。

## 实施边界

- 本轮不做外网抓取、RSS、URL 白名单、同步 Token、资源中心导入、附件、导入任务队列、导入预览、模板下载、部分成功或软删除。
- 本轮不修改 `structure` / `min_heading_left_enabled` 的冻结语义；标题段落描边与分级底色已完成。
- 本轮不引入 Alembic、PG、登录或多用户鉴权；`X-Workspace-Id` 继续仅作为个人版开发期工作空间选择。
- 本轮不创建 Git 提交；后续若需提交，提交信息必须使用简体中文。

## 执行记录（2026-07-11）

- 已完成 `source_key` 工作空间唯一约束和 SQLite 兼容索引、导入上限配置、CSV/JSON 内存生命周期解析、整批预校验、原子写入与来源键幂等跳过。
- 已完成标讯页导入弹层、FormData 请求、成功统计和弹层内逐行错误显示；删除或创建导入临时文件均未发生。
- 已覆盖 JSON 幂等、中文与空格表头 CSV、服务端状态、跨 workspace 同来源键、非法日期零写入、扩展名/体积/编码/行数上限和文件内重复来源键。
- Grok 两轮审查：首轮发现“失败后保留旧成功统计”P1，已在提交前清空结果修复；最终复审结论为无 P0/P1。其余 P2（框架请求生命周期临时缓冲、409 并发冲突回归）已记录，不扩大到外部同步或任务队列。
- 最终验证：`backend\\.venv\\Scripts\\python.exe -m pytest -q` 为 **89 passed**；`frontend npm run build` 通过（仅既有单包体积警告）；`git diff --check` 通过。针对离线导入模块检索 `requests.`、`httpx.`、`fetch(` 和 `http(s)`，未发现外部网络调用。
