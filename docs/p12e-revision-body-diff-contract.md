# P12E-A 单条修订正文差异预览契约

模块：P12E-A editor-state 单条修订与当前状态的章节正文差异预览  
对接：P12C-C1 修订历史详情、P12D-A 差异摘要、P12D-B 技术/商务共用修订面板。  
状态：2026-07-17 冻结，等待 Grok 按白名单实现；Codex 负责审查、独立验收、文档闭环和提交推送。

## 1. 目标

P12D-B 已能告诉用户“当前状态与某条历史修订是否不同”以及哪些大字段不同，但仍不能看到章节正文到底改了什么。本包新增一个只读、按需、单条历史修订对当前服务端状态的章节正文差异预览，让标书制作者在决定是否恢复前能先查看有界的新增、删除和保留片段。

比较对象固定为：

- 当前工作空间、当前项目；
- 一条由当前修订历史列表取得的 `revisionId`；
- 目标修订快照与请求时服务端当前 editor-state 的 `chapters` 字段。

本包不改变 P12D-A 的四键摘要响应，也不把差异结果写回 editor-state、修订账本、检查点或浏览器存储。

## 2. 后端接口

新增唯一接口：

```text
GET /api/projects/{projectId}/editor-state-revisions/{revisionId}/body-diff
```

请求不得有 body、查询参数、重试、轮询或其他旁路请求。成功和业务错误均带 `Cache-Control: no-store`。

### 2.1 精确响应结构

顶层只允许以下六个键，顺序不作为语义依据：

```json
{
  "sameBody": false,
  "changedChapterCount": 1,
  "currentChapterCount": 2,
  "targetChapterCount": 2,
  "truncated": false,
  "items": [
    {
      "ordinal": 1,
      "kind": "changed",
      "beforeTitle": "总体架构",
      "afterTitle": "总体架构",
      "hunks": [
        {"op": "equal", "text": "第一段\n"},
        {"op": "delete", "text": "旧正文\n"},
        {"op": "insert", "text": "新正文\n"}
      ]
    }
  ]
}
```

`items` 每项只允许 `ordinal/kind/beforeTitle/afterTitle/hunks` 五个键；`kind` 只允许 `added|removed|changed`；`hunks` 每项只允许 `op/text` 两个键，`op` 只允许 `equal|delete|insert`。不得返回 revision ID、state version、chapter ID、项目 ID、来源、时间、原始快照、其他 13 键或异常原文。

### 2.2 比较语义

1. 目标侧必须复用 P12C-C1 的三重作用域和快照完整性校验；当前侧必须读取请求时服务端权威 editor-state，并只抽取规范 13 键中的 `chapters`。
2. 章节只在服务端内部用唯一 `id` 配对；响应不得输出 ID。若历史数据缺少可用唯一 ID，按同一序号配对；重复 ID、非对象章节或无法确定配对时，返回固定差异失败，不猜测配对关系。
3. `sameBody` 仅表示所有配对章节正文及章节集合完全相同；标题变化不单独制造正文差异，但会作为 `beforeTitle/afterTitle` 上下文返回。新增/删除章节分别生成 `added`/`removed` 项。
4. `ordinal` 为响应内稳定的 1 起始展示序号，不是数据库 ID。项目切换、历史顺序变化或重新加载不得让旧请求污染当前面板。
5. 正文按规范化换行后的行序列用标准库差异算法生成 `equal/delete/insert` 片段；不允许用版本号、长度或 Python 直接相等代替正文比较。
6. `sameBody=true` 时 `items` 必须为空、`changedChapterCount` 必须为 0；存在正文差异时 `sameBody=false` 且计数等于 `items.length`。

### 2.3 有界与截断

服务端必须先用完整正文判断是否相同，再对返回文本做有界截断，避免“截断后看起来相同”的假绿。固定上限如下，常量应集中在新服务中并由测试精确断言：

- 最多处理 100 个章节；
- 单个章节参与展示的正文最多 20,000 个 Unicode 码点；
- 单个标题最多 240 个 Unicode 码点；
- 单个章节最多 80 个 hunk；
- 单个 hunk 文本最多 2,000 个 Unicode 码点；
- 整个响应的差异文本最多 120,000 个 Unicode 码点。

超过任一上限时仍按完整值确定 `sameBody` 和变化计数，返回可见片段并将 `truncated=true`。截断只发生在服务端返回值，不改变恢复、版本或任何持久化数据。

## 3. 错误与安全边界

- 项目不存在、跨 workspace、修订不存在或修订损坏，沿用 P12C-C1 固定脱敏错误和 HTTP 状态。
- 其他未预期异常统一为 HTTP 500，错误码 `editor_state_revision_body_diff_failed`，中文消息“修订正文差异生成失败”；不得反射路径、SQL、异常类型、快照内容或 ID。
- 服务全程只读：禁止 `add/delete/flush/commit/rollback/refresh`、写锁、审计、检查点、修订创建/裁剪、HTTP 请求和文件写入。
- 只读取当前工作空间和当前项目；不得搜索正文、跨项目取历史、读取其他 workspace 或使用浏览器本地存储。
- 章节标题和正文是用户主动请求查看的有界内容，除上述字段外不得返回任何内部标识或 13 键原始值。

## 4. 前端行为

在既有双工作区 `EditorStateRevisionPanel` 的每条修订上新增按需按钮“查看正文差异”。

- 展开、列表刷新和项目切换不自动请求正文差异；只有用户点击按钮才发送一次精确 GET。
- “查看摘要”“与当前对比”“查看正文差异”“恢复确认”四种意图互斥；点击任一项必须清理其余结果并作废其在途请求。
- 技术标和商务标共用同一面板、同一 API 封装和同一严格解析器；不修改 workspace/hook、通用 `apiFetch`、后端 editor-state 写入链或样式依赖。
- `sameBody=true` 显示“章节正文无变化”；否则显示每项固定中文操作标签“保留/删除/新增”、前后标题和有界片段。不得显示 `op` 原值、字段键、revision ID、state version、路径、响应原文或错误 detail。
- `truncated=true` 只显示固定提示“差异内容较长，仅显示有界片段”，不得自动加载第二页、扩大上限或轮询。
- 项目切换、折叠、刷新、恢复、列表重载、组件卸载以及摘要/比较互相点击均必须隔离 arrived 与 complete 两类迟到结果；旧 `finally` 不得清除新请求状态。
- 固定失败文案：“正文差异加载失败，请稍后重试”。

## 5. Grok 实现白名单

只允许修改以下七个文件，Grok 不得 `git add/commit/push`：

1. `backend/app/api/schemas.py`
2. `backend/app/api/editor_state_revisions.py`
3. `backend/app/services/editor_state_revision_body_diff_service.py`（新建）
4. `backend/tests/test_p12e_revision_body_diff.py`（新建）
5. `frontend/src/features/editor-state-revisions/editorStateRevisionApi.ts`
6. `frontend/src/features/editor-state-revisions/EditorStateRevisionPanel.tsx`
7. `frontend/e2e/editor-state-revision-history.spec.ts`

禁止新增依赖、迁移、实体字段、其他 API、其他 E2E、CSS、浏览器存储、URL 状态、模块全局缓存、AbortController 作为唯一隔离、任意历史两两比较、正文自动恢复、删除、搜索、分页、导出、分享或多人协作。

## 6. Failure-first 与验收

Grok 必须先只扩 E2E/专项测试，在生产正文差异入口不存在时取得真实业务红测；不得用 ImportError、SyntaxError、fixture 错误、缺依赖或浏览器未启动冒充。新增的至少三条 E2E 必须彼此独立，避免串行 describe 中首条失败导致后续 `did not run`。

最低测试覆盖：

1. 后端：相同正文、正文替换、新增/删除章节、标题变化、换行、完整/超限截断、唯一配对、坏数据、跨项目/workspace、required 角色、no-store、固定 500、五域零写和 AST 禁写。
2. 前端技术标：成功差异、严格六键/五键/二键解析、中文标签、summary/compare/body-diff/restore 互斥、只发一次 GET、无内部标识或正文以外泄漏。
3. 前端技术标迟到：body-diff arrived 与 complete 真实分离，项目切换、折叠、刷新、摘要/比较/恢复均不污染新状态。
4. 前端商务标：共享入口、成功/一致语义、精确一次 GET、正文与存储/网络白名单不旁路。

Codex 独立验收必须逐条串行执行，所有 Playwright 固定 `--workers=1 --retries=0`：

```text
backend\.venv\Scripts\python.exe -m pytest -q backend/tests/test_p12e_revision_body_diff.py
backend\.venv\Scripts\python.exe -m pytest -q backend/tests/test_p12d_revision_current_comparison.py backend/tests/test_p12c_revision_history.py
cd frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
cd ..
git diff --check
```

后端/前端全量回归、真实 SQLite、白名单、无外网和无存储证据由 Codex 独立复核；任何未达到固定契约的红测均不得提交。

## 7. 明确不做

P12E-A 不做正文编辑、恢复、自动应用、任意两条历史比较、历史删除、搜索、分页、跨项目时间线、超出最近 10 条的保留策略、导出、分享、多人协作、实时同步、通用 diff 缓存或后端版本库扩展。上述能力必须另立契约，不能从本包顺手扩展。
