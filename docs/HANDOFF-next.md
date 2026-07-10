# 新会话交接：biaoshu 当前状态与下一步

> **交接日期**：2026-07-10  
> **仓库本地**：`C:\Users\Administrator\biaoshu`  
> **GitHub**：https://github.com/wmjagpjm/biaoshu  
> **分支**：`main`  
> **远程**：商务标 MVP 已 push；本会话完善 **revise 写回 + 空态**  
> **验收基线**：`pytest` **38+ passed**；`frontend npm run build` 通过  

---

## 0. 新会话第一句（复制即用）

```text
继续 biaoshu。仓库 C:\Users\Administrator\biaoshu，请严格按 docs/HANDOFF-next.md 执行。
先 git status 看未提交改动。对话/注释/Commit Message 用简体中文；
遵守 docs/CONTRIBUTING.md 注释四字段（模块/用途/对接/二次开发）。
用户自备 API Key，禁止把密钥写进仓库。
启动：仓库根 Start-Biaoshu-Dev.bat；或 backend/run-dev.bat + frontend/run-dev.bat。
```

---

## 1. 产品定位（锁定，勿擅自改）

| 项 | 决策 |
|----|------|
| 形态 | **Web 自托管**；非 Electron |
| 账号 | 个人版一账号 ≈ 一 `workspace`（默认 `ws_local`） |
| Key | 用户自备；**保密机允许明文存/回显**（勿擅自改加密） |
| 参考 | C 端 OpenBidKit **只参考交互，勿抄 AGPL 源码** |
| 语言 | 对话 / **代码注释** / Commit Message = **简体中文** |

---

## 2. 注释与代码规范

完整约定见 **`docs/CONTRIBUTING.md`**。文件顶与公开 API 四字段：**模块 / 用途 / 对接 / 二次开发**。

---

## 3. 启动与联调

| 项 | 说明 |
|----|------|
| 一键双启 | 仓库根 `Start-Biaoshu-Dev.bat` |
| 前端 | http://127.0.0.1:5173 |
| 后端 | http://127.0.0.1:8000/api/health |
| 清单 | `docs/integration-checklist.md`（含 §7 知识库、§8 商务标） |

---

## 4. 已完成能力

### 4.1 技术标主链路

项目 CRUD、设置 Key、上传/解析、分析、大纲/章节、任务异步轮询、**取消**、大纲 revise 写回、editor-state、知识库 RAG 简版、Word 导出（编号/列表/表格）。

### 4.2 商务标 MVP（本会话）

| 项 | 说明 |
|----|------|
| 项目 | `Project.kind` = `technical` \| `business`；`linked_project_id` 可选 |
| 列表 | `GET /projects?kind=business`；技术标列表 `kind=technical` |
| 状态 | `editor-state.business_json` → API `businessQualify/Toc/Quote/Commit` |
| 任务 | `biz_qualify` / `biz_toc` / `biz_quote` / `biz_commit`；`export` + `mode=business` |
| 前端 | 列表/工作区接 API；`useProjectPipeline` 上传/解析/生成/取消/导出 |
| 测试 | `backend/tests/test_business_bid_mvp.py` |
| revise 写回 | `business_qualify|toc|quote|commit` 解析 JSON 写 editor-state；`business_parse` 写解析文 |
| 空态 | API 空数组不回填演示 mock |
| 查重 | `POST .../duplicate-check`；kb/self/history |
| 废标 | `POST .../rejection-check`；analysis + 关键词规则 |

### 4.3 关键路径

```text
backend/app/
  services/business_task_service.py   # 商务任务 + Markdown 组装
  services/editor_state_service.py    # business_json
  services/task_service.py            # 分发 biz_*
  services/export_service.py          # mode=business
  models/entities.py                  # kind / business_json

frontend/src/features/business-bid/
  hooks/useBusinessBidWorkspace.ts
  pages/BusinessBidPage.tsx
  pages/BusinessBidWorkspace.tsx
```

---

## 5. 明确未完成

| 优先级 | 项 | 现状 |
|--------|----|------|
| 业务 | 标讯 / 资源中心 | mock |
| 业务 | 查重 / 废标 | **已接 API**（字符 n-gram；非向量） |
| RAG | 向量 embedding | 未做 |
| 导出 | 标题边框、图片 | 未做 |
| 体验 | SSE 推送 | 仍 1s 轮询 |
| 库 | Alembic | 仅 create_all + ALTER |
| 生产 | 登录/多用户/HTTPS/Key 加密/PG/Docker | 未做 |
**粗估**：技术标 ~90%；商务标主路径 ~80%；内网多人 ~30%。

---

## 6. 建议下一会话方向

1. 向量检索（复用 text_similarity 预筛）  
2. 导出边框/图片  
3. SSE 替代轮询  
4. 标讯 / 资源中心后端化  

---

## 7. 验证命令

```powershell
cd C:\Users\Administrator\biaoshu\backend
.\.venv\Scripts\python -m pytest -q
# 期望：43+ passed

cd ..\frontend
npm run build

git status -sb
```

---

## 8. Git 注意

- 禁止提交：`.env`、真实 Key、`*.db`、`uploads/`、`data/`、`node_modules/`、`.venv/`  
- 旧 SQLite 缺列时 `ensure_schema_columns()` 会加 `kind` / `business_json` 等  

---

## 9. 安全

- 用户自备 API Key；勿把密钥写进仓库  
- 知识库与商务生成均要求勿编造招标未出现的硬指标  

---

## 10. 旧文档关系

| 文档 | 状态 |
|------|------|
| **docs/HANDOFF-next.md** | **当前有效交接** |
| docs/integration-checklist.md | 联调操作 |
| docs/CONTRIBUTING.md | 注释强制规范 |
