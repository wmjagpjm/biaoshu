<!--
模块：响应矩阵双浏览器 E2E 方案与实施记录
用途：说明依赖、启动/隔离、用例步骤、分层与 CI 预算。
对接：task msg_4aeed563 / msg_33f9d4fc / msg_28b5b564；分支 collab/grok-code-codex-review
二次开发：状态：409 主路径与刷新来源 E2E 已实现；智能建议人工确认 E2E 另立项。
-->

# 响应矩阵双浏览器上下文 E2E — 最小方案

> **状态（2026-07-12）**：409 主路径与「刷新来源保留人工映射」已实现（`npm run test:e2e:matrix` 两 spec）；智能建议人工确认浏览器 E2E 仍未做。

### 当前实现状态（以代码为准，勿把下文历史方案当现状）

| 项 | 实际配置 |
|----|----------|
| 依赖 | `frontend` 已装 `@playwright/test`；仅 chromium |
| scripts | `test:e2e` / `test:e2e:matrix`（conflict + refresh-sources） |
| 后端 | `8010`，`DATABASE_URL=sqlite:///./data/biaoshu-e2e.db`，`DEFAULT_WORKSPACE_ID=ws_e2e`；启动前跑 `backend/scripts/e2e_reset_db.py` |
| 前端 | `5174`；**同源** `/api`，经 `VITE_API_PROXY_TARGET=http://127.0.0.1:8010`（**不是** 浏览器侧固定 `VITE_API_BASE_URL=http://127.0.0.1:8010/api`） |
| webServer | `reuseExistingServer: false`（前后端均强制新进程，避免复用日用端口/错误库） |
| workers / sleep | `workers: 1`；禁止固定 `sleep` 作同步 |
| 主 spec | `frontend/e2e/response-matrix-conflict.spec.ts`（双 context 409） |
| 扩展 spec | `frontend/e2e/response-matrix-refresh-sources.spec.ts`（刷新来源保留人工映射；API 改 analysis 后收敛） |

下文 §0–§7 中标注为「实施前方案 / 历史记录」的段落保留规划痕迹，**仅作决策背景**；运维与二次开发以本表与 `frontend/playwright.config.ts`、`frontend/vite.config.ts` 为准。

## 0. 审计结论（实施前仓库快照 · 历史记录）

| 项 | 现状 |
|----|------|
| Playwright / Puppeteer / Cypress | **无** |
| Vitest / Jest / Testing Library | **无**（`frontend/package.json` 仅 oxlint + tsc + vite） |
| 浏览器驱动 / CI 浏览器步骤 | **无** |
| 可复用启动脚本 | `Start-Biaoshu-Dev.bat` → `backend/run-dev.bat`（uvicorn `:8000`）+ `frontend/run-dev.bat`（vite `:5173`）；静默、端口已监听则跳过 |
| Vite 代理 | `/api` → `http://127.0.0.1:8000`（`frontend/vite.config.ts`） |
| 后端测试库隔离 | `backend/tests/conftest.py`：`DATABASE_URL=sqlite:///./data/biaoshu-pytest.db`，每测 `drop_all/create_all` + seed `ws_local` |
| 日用库 | 默认 `backend/data` 下业务库（与 pytest 文件分离，但 **dev 与 E2E 若共用默认 URL 会污染**） |
| 矩阵冲突 UI 可选择器 | 冲突容器 `role="alert"` + class `response-matrix__conflict`；按钮文案「重新载入远端矩阵」；「刷新来源」「智能建议」 |
| 后端并发/版本 | 已有 `test_response_matrix_concurrent_versioned_puts_one_wins` 等 API 级覆盖（非浏览器） |

**结论**：做双上下文 UI E2E **必须新增** Playwright（或等价）；当前无前端测试框架可复用。

---

## 1. 建议依赖 / scripts / 预计文件（实施前方案 / 历史记录）

> 以下「确认后再装 / 确认后改」表述为立项时写法；**依赖与 scripts 现已落地**，见文首「当前实现状态」。

### 依赖（devDependencies，版本可按确认时最新稳定钉死）

| 包 | 建议版本策略 | 用途 |
|----|----------------|------|
| `@playwright/test` | `^1.49` 或确认时 LTS | 双 browser context、断言、webServer |
| 浏览器 | `npx playwright install chromium`（仅 chromium，控体积） | headless CI |

**不建议** 同时上 Vitest：本任务是跨页真实 HTTP + 双上下文，Playwright 足够；组件单测另立项。

### npm scripts（`frontend/package.json`，实施前拟定 · 现已落地）

```json
"test:e2e": "playwright test",
"test:e2e:matrix": "playwright test e2e/response-matrix-conflict.spec.ts"
```

### 预计新增文件（实施前清单 · 历史记录）

```text
frontend/playwright.config.ts
frontend/e2e/response-matrix-conflict.spec.ts
frontend/e2e/fixtures/matrix-project.ts   # 可选：API 建项目/写矩阵种子
backend/scripts/e2e_prepare_db.py         # 可选：独立 SQLite 路径 + 建表 seed
# 实际落地为 backend/scripts/e2e_reset_db.py（非 e2e_prepare_db.py）
```

**不改** 业务源码（除非 E2E 发现缺 `data-testid`；优先用现有 `role`/`文案`/`aria-label`，确认后再加最小 testid）。

---

## 2. 启动 / 等待 / SQLite 与 workspace 隔离

### 推荐架构

**实施前草案（历史记录，含已废弃的直连 API Base URL 与可复用 server 设想）：**

```
Playwright webServer[]:
  1) 后端：DATABASE_URL=sqlite:///./data/biaoshu-e2e.db \
           DEFAULT_WORKSPACE_ID=ws_e2e \
           SEED_SAMPLE_OPPORTUNITIES=false \
           uvicorn app.main:app --host 127.0.0.1 --port 8010
  2) 前端：VITE_API_BASE_URL=http://127.0.0.1:8010/api \   # 已废弃，见下方实际
           vite --port 5174 --strictPort
  # 草案曾写 reuseExistingServer: !process.env.CI — 已废弃
```

**当前实际实现（以 `playwright.config.ts` 为准）：**

```
Playwright webServer[]:
  1) 后端：e2e_reset_db.py && uvicorn ... --port 8010
           DATABASE_URL=sqlite:///./data/biaoshu-e2e.db
           DEFAULT_WORKSPACE_ID=ws_e2e
           reuseExistingServer: false
  2) 前端：vite --host 127.0.0.1 --port 5174 --strictPort
           VITE_API_PROXY_TARGET=http://127.0.0.1:8010
           （浏览器仍走同源 /api，由 Vite proxy 转发；非 VITE_API_BASE_URL 直连 8010）
           reuseExistingServer: false
```

要点：

- **端口与日用 8000/5173 错开**（8010/5174），避免撞上开发进程。
- **独立 SQLite 文件** `biaoshu-e2e.db`，禁止指向 `biaoshu-pytest.db` 或默认个人库。
- 启动前由 `e2e_reset_db.py` 做 `drop_all + create_all + ensure_default_workspace`（非每个 spec beforeEach 另起一套，除非后续再扩）。
- **workspace**：固定 `ws_e2e`；两 context 同 workspace 同 project_id。
- **等待**：Playwright `webServer.url` + **`reuseExistingServer: false`**（强制新进程）；页面用 `expect(locator).toBeVisible()` / `toPass` / 可观察 response，**禁止 `sleep` 硬等**。矩阵保存防抖 800ms → 用 `expect.poll` 等网络空闲或等 version 相关 UI/接口响应。

### 种子数据（优先 API，不点满 UI）

1. `POST /api/projects` 建技术标项目。
2. `PUT /api/projects/{id}/editor-state` 写入含 1～2 行的 `responseMatrix` + 读回 `responseMatrixVersion`。
3. 两 context 打开 `/technical-plan/{id}/analysis`（或实际承载 `ResponseMatrixPanel` 的路由）。

---

## 3. 双 context 核心用例步骤与断言

**文件**：`e2e/response-matrix-conflict.spec.ts`
**标题**：`响应矩阵：双浏览器上下文 409 与显式载入`

| 步骤 | Context A | Context B | 断言 |
|------|-----------|-----------|------|
| 0 | 打开项目矩阵区 | 打开同一项目矩阵区 | 双方可见同一 `sourceText` 初值 |
| 1 | 修改 notes 或 status 为「A-保存」 | — | 本地输入值为 A |
| 2 | 触发保存（失焦或等防抖 PUT 200） | — | `expect` 到 PUT 200；可选读 GET version 变新 |
| 3 | — | 在**不刷新**前提下改 notes 为「B-本地」并触发保存 | 出现冲突条：`getByRole('alert')` 含「矩阵保存冲突」/「其他终端」；**输入框仍为「B-本地」**（未被远端 A 静默替换） |
| 4 | — | 点击「重新载入远端矩阵」 | 冲突条消失；矩阵展示 **A 的远端内容**（notes/status = A） |
| 5 | — | 再改并保存 | PUT 200；GET 与 B 最终一致；无 alert |

可选稳健性：

- B 在步骤 3 用 `page.route` **不必**模拟 409；必须打真实后端。
- 串行保存：同页快速连改只验证「最终一次 200 + 无误 409」，可作第二 soft case。

---

## 4. 其它覆盖分层（避免 E2E 膨胀）

| 场景 | 推荐层 | 理由 |
|------|--------|------|
| 空矩阵稳定 version / 概述不改 version / 旧客户端无 version | **已有** `test_response_matrix.py` | API 足够 |
| 双 Session 并发一成一败 | **已有** concurrent 单测 | 比 E2E 稳 |
| 刷新来源（merge analysis → matrix） | **已接 E2E** `response-matrix-refresh-sources.spec.ts` | 多源人工映射保留；API 改 analysis 后删/增行并 GET 持久化 |
| 智能建议须人工确认 | **仍未做浏览器 E2E**；任务 API 单测已覆盖 `response_match` 不写 editor-state | 后端已断言建议不入库；后续 E2E 只断言：出现待确认区、未点应用前映射不变（禁真实 Key） |
| Word 导出失效引用 | **保持后端** `test_technical_export_includes_reconciled_response_matrix` | 不在浏览器下解析 docx |

**P1 范围（当前）**：**§3 双 context 冲突主路径**与**刷新来源保留人工映射**已合入 `test:e2e:matrix`；智能建议人工确认 / Word 导出浏览器层仍后续。

---

## 5. CI / 本机时间预算 / 无 GUI

| 环境 | 方式 | 预算（估） |
|------|------|------------|
| 本机 Windows | `npx playwright test --project=chromium`；可 headed 调试 | 冷启动前后端 + 浏览器 ≈ **45–90s**；热复用 webServer ≈ **20–40s** |
| CI（无 GUI） | `CI=1`，`npx playwright install --with-deps chromium`，`headless: true` | job +10–20s 装浏览器；单文件 spec **≤ 2 min** 超时 |
| 并行 | 单 worker 先（SQLite 文件锁）；勿多 worker 共一 e2e.db | — |

`playwright.config.ts` 建议：

- `fullyParallel: false`（首版）
- `workers: 1`
- `retries: CI ? 1 : 0`
- `timeout: 60_000`
- 禁止 fixed `waitForTimeout` 作为同步手段

---

## 6. 实施顺序（实施前清单 · 历史记录）

> 下列步骤在立项时「待 Codex ack 后」执行；**1–3 已完成**。保留供对照，勿再当作待办。

1. 钉依赖版本 + `playwright.config.ts` + e2e 库脚本。✅
2. 实现 §3 单 spec，绿。✅
3. 文档：`docs/integration-checklist.md` 补一条 E2E 命令。✅
4. **不**默认并入 `npm run build`；CI 可选 job。
5. 再议是否加 `data-testid`。

---

## 7. 风险与非目标

- 个人版无登录：E2E 不测鉴权。
- LLM 智能建议依赖 Key：E2E 用 mock 任务结果或跳过。
- 不把 pytest 库与 e2e 库混用。
- 不在本方案阶段修改 `editor_state_service` 业务逻辑。
