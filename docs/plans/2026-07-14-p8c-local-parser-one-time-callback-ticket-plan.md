<!--
模块：P8C 本地解析一次性回传票据实施计划
用途：把 required 模式外部解析回传拆为后端票据闭环与前端签发展示两个受限任务。
对接：docs/p8c-local-parser-one-time-callback-ticket-contract.md；P8/P8B；Grok-Codex 协作消息箱。
二次开发：Grok 只实现和自测，不得提交推送；Codex 独立审查、验收、中文提交与文档闭环。
-->

# P8C 本地解析一次性回传票据实施计划

> **执行要求**：使用 `executing-plans` 按任务逐项执行；先写失败测试，再做最小实现。

**目标**：让 `AUTH_MODE=required` 下的外部本地解析助手使用短期、单项目、单次票据回传 Markdown，而不接触浏览器会话、CSRF 或长期全局 Token。

**架构**：strict `bid_writer` 通过受会话与 CSRF 保护的项目端点签发随机票据；数据库只保存摘要和绑定元数据。认证中间件只公开一个精确回调路径，回调以原子条件更新消费票据，并在同一事务中写入解析结果、成功任务和项目步骤。前端后续只在用户点击后签发并以内存显示 curl，不自动回调或启动解析器。

**技术栈**：FastAPI、SQLAlchemy、SQLite/PostgreSQL 兼容 SQL、SHA-256、React/TypeScript、Playwright、pytest。

> **状态**：计划已冻结，等待后端受限实现。
> **工作方式**：项目约束要求继续使用唯一协作分支，不新建 worktree；Grok 留下未提交差异，Codex 唯一负责 Git。
> **基线**：HEAD 以本计划提交为准；后端 422 passed，前端单 worker E2E 122 passed。

---

## 1. 任务 1：后端一次性票据与公共回调

### 1.1 文件白名单

仅允许修改或新增：

- `backend/app/models/entities.py`
- `backend/app/models/__init__.py`
- `backend/app/main.py`
- `backend/app/api/auth_middleware.py`
- `backend/app/api/parse_callback.py`
- `backend/app/services/local_parser_ticket_service.py`（新建）
- `backend/tests/test_local_parser_callback_tickets.py`（新建）

禁止修改 `config.py`、`database.py`、`deps.py`、`auth_service.py`、`project_service.py`、`editor_state_service.py`、`task_service.py`、`parse_engines.py`、`parse_service.py`、既有测试、依赖、脚本、前端和文档。不得暂存、commit 或 push。

### 1.2 TDD 步骤

1. 在新测试中建立 required 模式 strict `bid_writer`、其他角色、仅所有者、跨空间项目和公共无会话请求夹具；先证明签发端点与公共回调尚不存在。
2. 运行 `./.venv/Scripts/python.exe -m pytest -q tests/test_local_parser_callback_tickets.py`，记录预期 404/失败测试证据。
3. 新增票据实体：摘要唯一；workspace/project/user 外键和索引；固定时间字段；不得出现 raw token、filename、markdown、IP、User-Agent 字段。在 `models/__init__.py` 与 `main.py` 显式注册。
4. 在新 service 中实现固定常量、随机票据生成/摘要、签发、手工请求体规范化、原子消费与同事务应用。公开函数补中文“模块/用途/对接/二次开发”说明。
5. 在 `parse_callback.py` 保持旧路由不变，新增签发端点和 `/local-parser/callback`；签发从 `request.state` 取 actor，公共回调不读取会话。所有错误使用固定 code/message，不反射输入。
6. 在 `auth_middleware` 增加仅匹配 `POST + /api/local-parser/callback` 的公开判断；不得把该路径加入对所有方法生效的宽泛集合。测试证明 GET/PUT、`/api/local-parser/callback/extra`、旧 `/api/projects/{id}/parse-callback` 和其他项目路径均不公开，既有 health/bootstrap/login 公开语义不回归。
7. 测试签发响应只有 ticket/expiresAt/callbackPath、`no-store`、10 分钟固定窗口、库内仅摘要、审计固定脱敏；query/body 不能改变 TTL 或绑定。
8. 测试公共回调缺票据、坏票据、过期、已消费、项目删除和重放统一 401；长期 `X-Local-Token` 不能替代新票据。
9. 测试 JSON 非对象、额外键、source 非 mineru、非法 filename、空/超长 Markdown、原始 body 超 2 MiB 固定 400/413 且响应不含正文、文件名、票据或敏感标记。
10. 测试成功后只有一次解析结果、一次成功任务、一次项目步骤更新和一次固定审计；响应精确字段且 `no-store`。用 monkeypatch 制造中途异常，证明票据消费与全部业务写入一起回滚。
11. 用同一票据连续或并发竞争，断言受条件 UPDATE 影响行数控制，严格只有一次成功；服务不得仅靠先查后改的 Python 标志判断。
12. 运行定向、受影响回归和 `git diff --check`，完成后仅发送 `review_request`。

### 1.3 后端验收命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python.exe -m pytest -q tests/test_local_parser_callback_tickets.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_async_and_callback.py tests/test_parse_engines.py tests/test_auth_rbac.py
cd ..
git diff --check
```

Codex 审查重点：公开路径是否过宽；是否保存/日志输出原始票据；签发是否接受客户端 workspace/user/TTL；required 角色和 CSRF 是否绕过；消费是否真正原子且同事务；Pydantic/框架错误是否回显 Markdown；长期 Token 是否成为公共回退；是否偷偷安装或启动解析器。

## 2. 任务 2：前端签发与内存 curl 展示

后端验收提交并推送后才派发。仅允许修改或新增：

- `frontend/package.json`
- `frontend/src/features/local-parser/pages/LocalParserPage.tsx`
- `frontend/e2e/local-parser-callback-ticket.spec.ts`（新建）

禁止修改路由、`useAuthSession`、共享 API/认证层、P8B 解析策略、技术标/商务标工作区、Playwright 配置、依赖/锁文件、后端和文档。

### 2.1 TDD 步骤

1. 新建 E2E，先证明 required strict `bid_writer` 页面没有一次性票据入口；记录失败证据。
2. 在页面复用 `useAuthSession` 的 `authRequired`/`canAccessBusiness`，只在 required strict `bid_writer` 下显示“生成一次性回传票据”；disabled 显示个人兼容说明并保留旧表单。
3. 用户点击后严格一次 POST `/projects/{projectId}/parse-callback-ticket`；无自动签发、轮询、计时器或项目 ID 变化触发。成功响应只在组件 state 保存，重新签发先清空旧值。
4. 展示固定 `/api/local-parser/callback`、`X-Local-Parse-Ticket` 和 Windows curl；不得把票据放入 URL、日志、剪贴板 API、localStorage、sessionStorage、IndexedDB 或模块全局缓存。
5. 签发错误固定中文，不拼接服务端 detail/code/path/项目 ID/票据。页面不自动调用公共 callback、不访问外网、不启动本地进程。
6. E2E 覆盖：首次零签发、点击严格一次、响应展示、刷新后票据消失；disabled 零签发且旧回传仍可用；非 bid_writer 受限且零签发；网络白名单和所有浏览器存储为空。
7. 新增 `test:e2e:local-parser-callback-ticket` 脚本，只运行该 spec；依次运行定向 E2E、P8B 回归、lint、build 与 `git diff --check`，完成后只发 `review_request`。

### 2.2 前端验收命令

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run test:e2e:local-parser-callback-ticket
npm run test:e2e:parse-strategy
npm run lint
npm run build
cd ..
git diff --check
```

所有 Playwright 命令必须等待前一个完成，禁止并行；始终 headless、单 worker、后台静默，不启动可见浏览器或前台窗口。

## 3. Codex 独立验收与提交顺序

1. 审查后端精确七文件、表字段、公开路径、权限/CSRF、票据摘要、原子消费、事务与固定错误；运行 P8C 定向、解析/认证回归和后端串行全量，形成独立中文后端提交并推送。
2. 派发前端三文件任务；审查显式签发、内存秘密、disabled 兼容、网络/存储边界，运行定向/P8B E2E、lint/build 和单 worker 全量 E2E，形成独立中文前端提交并推送。
3. 更新本计划、契约、路线图、联调清单、P8 总计划和 HANDOFF，形成独立中文文档闭环提交并推送。

## 4. Grok review_request 必报项

后端必须报告：原任务 ID、失败先测证据、精确七文件、表字段、随机强度/摘要/TTL、精确公开路径、权限与 CSRF、原子 SQL 和事务证据、固定错误脱敏、定向/回归结果、`git diff --check`、风险与未做项。前端必须报告：原任务 ID、失败先测、精确三文件、请求次数、票据生命周期、disabled/角色边界、网络/存储断言、定向/P8B E2E、lint/build/diff-check。两包均不得 commit/push。
