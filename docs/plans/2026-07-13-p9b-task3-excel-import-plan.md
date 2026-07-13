# P9B 任务 3：国能 e 招计划表受控导入实施计划

> **给 Grok：** 必须逐项完成本计划；每个测试先证明失败，再实现最小代码。完成后只提交 `review_request`，不得自行提交或推送。

**目标：** 用户可上传本机 `.xlsx` 招标计划表，服务端仅在内存中校验并原子导入当前工作空间的追踪计划，且不改变既有 CSV/JSON 本地标讯导入契约。

**架构：** 在任务 2 已建立的 `bid_watch_plans` 数据域上增加独立导入服务和 `/api/opportunity-watch/plans/import` 路由。服务通过 `openpyxl` 从内存字节读取工作簿，前十行定位中文表头，先收集整批错误、再在单一事务内依据服务端指纹写入；上传字节、桌面路径和工作簿对象均不持久化、不返回。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Pydantic、`openpyxl`、pytest、FastAPI `TestClient`。

---

## 1. 已冻结的范围与裁定

- 仅接受本机上传的 `.xlsx`；路由不接收 URL、Cookie、站点名、搜索条件、Token 或文件路径。
- 扫描第 1 至第 10 个 Excel 行定位表头；支持 `招标计划名称`（必填）、`招标人`、`范围`、`计划工期`、`预计发布公告时间`、`备注`。
- 完全空白行跳过；若某行含任意其它字段但 `招标计划名称` 为空，则返回包含 Excel 实际行号的校验错误，并使整批零写入。该裁定同时满足总契约的空行跳过和任务 3 对缺失计划名的失败用例。
- 重复导入或同一文件内重复的“清洗后计划名 + 招标人 + 范围”只计入 `skipped`，不得产生重复计划；跨工作空间允许各自导入。
- 文件上限固定为 2 MiB、计划行数固定为 120；上限由 `Settings` 控制，浏览器不可覆盖。
- 本阶段只做 Excel 导入；不做同步、HTTP 客户端、后台任务、公告详情读取、人工接受、前端、真实外网请求或自动立项。

## 2. 严格文件白名单

- 修改：`backend/requirements.txt`
- 修改：`backend/app/services/opportunity_watch_service.py`
- 新建：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

不得修改实体、数据库初始化、既有标讯服务或路由、前端、PowerShell、文档以外的任何文件。不得安装依赖、提交或推送；依赖安装、独立审查、验收、提交与推送由 Codex 负责。

## 3. 实施任务

### 任务 1：冻结依赖与上传响应契约

**文件：**

- 修改：`backend/requirements.txt`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 在 `requirements.txt` 仅新增 `openpyxl>=3.1.5`；不升级或整理其它依赖。
2. 写失败测试，构造内存工作簿并上传到尚不存在的路由，固定成功响应只含 `inserted`、`skipped`、`total`：

```python
response = client.post(
    "/api/opportunity-watch/plans/import",
    files={"file": ("plans.xlsx", workbook_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
)
assert response.status_code == 201
assert response.json() == {"inserted": 2, "skipped": 0, "total": 2}
```

3. 运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunity_watch.py -k "plan_import"
```

预期：因路由和响应模型尚未实现而失败。

4. 新增四字段齐全的导入响应模型；模型只暴露 `inserted`、`skipped`、`total`，不得添加上传文件名、路径、工作簿内容或任意远端字段。

### 任务 2：实现内存解析、校验与单事务幂等写入

**文件：**

- 修改：`backend/app/services/opportunity_watch_service.py`
- 修改：`backend/tests/test_opportunity_watch.py`

**步骤：**

1. 用 `openpyxl.Workbook` 写失败用例：前两行为说明、第三行为中文表头、后续两条有效计划时可导入；第二次导入返回 `inserted=0`、`skipped=2`。
2. 写失败用例：缺少 `招标计划名称` 表头；非空数据行缺计划名；超过 120 条计划；超过 2 MiB；同批重复计划；跨工作空间导入同一计划。断言任意校验错误均返回可定位行号并使当前工作空间零写入。
3. 在服务文件实现以下最小私有边界：

```python
def import_watch_plans_from_xlsx(
    db: Session,
    workspace_id: str,
    *,
    filename: str,
    content: bytes,
    max_rows: int,
) -> dict[str, int]:
    ...
```

函数必须检查扩展名、用 `io.BytesIO` 和 `load_workbook(..., read_only=True, data_only=True)` 解析、在前十行定位表头、清洗文本、以确定性哈希生成 `fingerprint`。先完成全量校验，再通过同一数据库事务写入；重复指纹只计跳过；不得保存 `content` 或工作簿对象。
4. 为可定位的整批错误定义任务 3 专用异常，异常正文仅含安全的 `row`、`field`、`message`，不得拼接原始文件、路径、工作表公式或 Python 异常原文。
5. 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunity_watch.py -k "plan_import"
```

预期：计划导入相关测试全部通过，且不访问网络。

### 任务 3：注册独立路由并保留既有标讯导入语义

**文件：**

- 新建：`backend/app/api/opportunity_watch.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/tests/test_opportunity_watch.py`
- 回归：`backend/tests/test_opportunities.py`

**步骤：**

1. 新建具有“模块 / 用途 / 对接 / 二次开发”四字段文件顶注释的路由模块；只定义：

```python
POST /api/opportunity-watch/plans/import
```

路由从依赖注入获得当前 `workspace_id` 和 `Settings`，在读取前后均检查 2 MiB 上限，仅接受 `.xlsx`；成功返回 201。校验异常映射为 422 和结构化安全错误，扩展名或大小错误映射为 400。
2. 在 `main.py` 的既有 `/api` 注册区加入该独立路由；不改变 `/api/opportunities/import` 的路径、路由顺序语义、入参、状态码或异常文案。
3. 写 API 测试验证：成功导入、重复上传、跨工作空间独立、缺表头和错误行整批零写入、`.csv`/`.json`/伪装扩展名拒绝、超大小拒绝。
4. 运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunity_watch.py -k "plan_import"
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunities.py
git diff --check
git status -sb
```

预期：全部通过；`git diff --check` 无空白错误；差异仅在本计划白名单内。

### 任务 4：交付前自检与审查交接

**文件：** 本计划白名单中的全部文件。

**步骤：**

1. 静态确认新导入服务与路由不含 `httpx`、`requests`、`BackgroundTasks`、`subprocess`、浏览器调用、URL/Cookie/Token 入参或持久化字段。
2. 确认所有新增模块、公开函数和公开模型均含真实的简体中文四字段注释；注释不得伪称已实现任务 4 的同步功能。
3. 将下列证据通过消息箱发送 `review_request`：先失败的测试命令与摘要、最终两组定向测试原始结果、`git diff --check`、`git status -sb`、精确文件清单、是否安装依赖与任何未解决问题。
4. 未获 Codex `ack` 前，禁止 `git add`、`git commit`、`git push` 或开始任务 4。

## 4. Codex 独立验收与提交门槛

1. 审查精确 diff：只允许白名单文件；新增依赖仅为 `openpyxl`；不修改任务 2 三表或既有 `bid_opportunities` 契约。
2. 独立运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunity_watch.py -k "plan_import"
.\.venv\Scripts\python.exe -m pytest -q tests/test_opportunities.py
.\.venv\Scripts\python.exe -m pytest -q
```

3. 通过后仅暂存白名单，提交信息固定为：`实现国能计划表受控导入`，普通推送至 `origin/collab/grok-code-codex-review`，再核对 `git status -sb` 与本地/远端 SHA 一致。

## 5. 未完成项

- 任务 4 的固定来源低频同步、任务 5 的人工接受、任务 6 的前端面板均未实现，不能在本任务提前创建接口或占位网络代码。
- P9C 的模型、维度、成本、数据出域、回退和索引迁移决策仍未满足；P9B 完整闭环前不得启动。
