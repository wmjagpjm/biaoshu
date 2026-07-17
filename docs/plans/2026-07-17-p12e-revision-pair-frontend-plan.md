# P12E-C 双修订正文差异前端实施计划

> **执行者：Grok**：按本计划三文件白名单实现；Codex 独立审查、验收、中文文档闭环和提交推送。

**目标：** 在技术标/商务标共用修订面板中以内存选择两条历史修订，调用 P12E-B 并展示有界正文差异。
**架构：** API 层增加 pair 严格 parser 与单一 GET；面板增加双侧选择、独立 generation 和互斥展示；E2E 在现有 route 探针中增加 pair arrived/complete 与技术/商务隔离证明。
**技术栈：** React、TypeScript、Playwright Chromium；E2E 必须 `--workers=1 --retries=0`。

## 1. 开工与白名单核验

1. 核对分支 `collab/grok-code-codex-review`、HEAD/远端一致、工作区干净；读取 P12E-C 契约、P12E-B API 和现有 P12E-A 面板。
2. 只允许三个文件：`editorStateRevisionApi.ts`、`EditorStateRevisionPanel.tsx`、`editor-state-revision-history.spec.ts`。
3. 不得改后端、CSS、其它组件、依赖、路由、存储或 URL。

## 2. 先写真实红测

在现有 E2E 探针中增加 pair route 的 arrived/complete 日志、固定响应表和 gate；先只增加 pair 相关测试并运行，记录入口尚不存在/无请求或结果缺失的真实业务失败。修正 fixture/探针后才能进入实现，不得把白页、TS 收集或依赖错误算作红测。

## 3. API 封装

1. 增加 `EditorStateRevisionPairBodyDiff` 类型与 `parseRevisionPairBodyDiff`；复用已有 item/hunk parser 和预算，严格验证 before/after 章节计数及六键。
2. 增加 `getEditorStateRevisionPairBodyDiff(projectId, beforeRevisionId, afterRevisionId)`；ID 非法或相同固定失败，合法路径双 `encodeURIComponent`，`apiFetch` 不传第二参数。
3. 保持 P12E-A `parseRevisionBodyDiff`、现有 current/target 字段和请求路径完全不变。

## 4. 面板实现

1. 增加 before/after 两侧选择 ID、pair 结果/错误/加载状态和独立 generation；只在内存保存，禁止进入 DOM/URL/存储/日志。
2. 为每个列表项添加固定中文选择按钮和 `data-testid`；同一项不能同时成为两侧；两侧未齐时比较按钮禁用。
3. 比较动作只调用一次 pair GET；结果展示前后计数、变化章节数、正文状态、截断提示和有界 hunk；固定中文错误文案。
4. 把 pair 意图纳入现有摘要/当前比较/body-diff/restore 互斥和项目/折叠/刷新/卸载迟到作废链；A0→A1、项目切换、再次选择不得被旧 finally 清理新状态。

## 5. 受限审查与回归

1. 逐行审查 ID 不泄漏、无旁路请求、严格 parser、双侧选择、generation、互斥和技术/商务共享入口。
2. 串行运行 P12E-C E2E、P12E-A/P12D/P12C 受影响 E2E、前端全量 `--workers=1 --retries=0`、`npm run lint`、`npm run build` 和 `git diff --check`。
3. 检查精确三文件白名单、暂存区为空，不允许 Grok 提交或推送。

## 6. Codex 文档闭环与提交

独立验收通过后，Codex 更新 `docs/HANDOFF-next.md`、路线图、`docs/integration-checklist.md`、P12E-C 契约和本计划，记录真实 failure-first、E2E 全量计数、Grok/Codex 消息 ID与未实现边界；以中文提交信息提交并推送。P12E-C 不交付分页、搜索、恢复、删除、导出、分享、缓存、跨项目历史或多人协作。
