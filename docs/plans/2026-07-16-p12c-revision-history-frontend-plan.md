<!--
模块：P12C-C3 editor-state 修订历史前端实施计划
用途：落实双工作区列表、摘要、恢复和迟到隔离的七文件 failure-first 顺序。
对接：p12c-revision-history-frontend-contract.md；P12C-C1/C2；P12B-D2。
二次开发：所有 E2E 单 worker 串行；不修改后端、检查点模块、依赖或配置。
-->

# P12C-C3 editor-state 修订历史前端实施计划

> **状态**：已冻结，待 Grok failure-first、实现与自测。
> **基线**：C2 冻结=`54af600`、范围修订=`2276366`、实现=`0803250`、闭环=`f34e3fc`；后端/前端串行全量 **800/263 passed**。

## 1. 交付目标

在技术标和商务标工作区交付共用修订历史折叠面板：展开才读最近 10 条、点击才取详情摘要、二次确认才恢复；恢复进入既有版本化外部写队列，使用执行时最新 expected，成功唯一 editor-state GET，失败保守阻断且零重试。原始快照不进入组件、DOM、URL、存储或日志。

## 2. 实施顺序

1. 仅新增 C3 E2E，先覆盖默认零请求、列表/详情、确认、恢复时序、失败、迟到和数据最小化，保持生产未改运行 failure-first；
2. 新增严格 revision API：精确 list/detail/restore shape、九来源、10 条上限、详情元数据匹配和有界摘要；
3. 新增共用面板：折叠代次、按需摘要、固定中文、二次确认、ID/version/正文不渲染；
4. 技术 hook 复用 `matrixSaveChainRef`、`runVersionedExternalWrite` 和既有操作令牌增加 revision restore；技术页挂载面板；
5. 商务 hook 复用 `saveChainRef`、同 runner/令牌增加 revision restore；商务页挂载面板；
6. 串行运行 C3 专项、checkpoint restore、技术/商务 truth、lint/build 和前端全量，再检查七文件白名单、暂存区与 diff；完成只发送 `review_request`。

## 3. Grok 最低自测

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-revision-history.spec.ts --workers=1 --retries=0
npx playwright test e2e/editor-state-checkpoint-restore.spec.ts --workers=1 --retries=0
npx playwright test e2e/technical-editor-state-truth.spec.ts e2e/business-editor-state-truth.spec.ts --workers=1 --retries=0
npm run lint
npm run build
```

命令必须逐条运行；禁止同时启动两个 Playwright 进程。全量仅在上述定向通过后执行：

```powershell
npx playwright test --workers=1 --retries=0
```

## 4. Codex 验收门

Codex 独立审查：列表/详情/restore 严格 shape；原始 snapshot 只在 API 栈内短暂解析并压缩为有界摘要；ID/version/正文不外泄；技术/商务共用既有操作令牌和保存链；执行时 expected、成功唯一 GET、失败阻断与迟到代次没有旁路。随后独立串行运行专项、受影响回归、lint/build 和前端全量；后端无改动，沿用 **800 passed** 基线。

## 5. 后续边界

C3 闭环后，P12C 最小修订列表/摘要/恢复链完成。删除、diff、搜索、分页、跨项目历史、自动历史、保留期和多人协作仍须重新审计，不得从 C3 直接扩展。
