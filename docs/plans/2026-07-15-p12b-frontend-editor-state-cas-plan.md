<!--
模块：P12B-B 前端 editor-state 全状态 CAS 实施计划
用途：把三个浏览器写入者、两个项目队列、冲突 UX 与两份 E2E 收敛为七文件受限任务。
对接：docs/p12b-frontend-editor-state-cas-contract.md；P12B-A；P11B/P11C；矩阵与 M3-D 回归。
二次开发：Grok 只实现/自测；Codex 负责审查、独立串行 E2E、中文提交和文档闭环。
-->

# P12B-B 前端 editor-state 全状态 CAS 实施计划

> **状态**：只读审计完成，契约已冻结；等待前端受限实现。
> **前置提交**：P12B-A 计划/契约=`0b55c30`、实现=`780cc82`、闭环=`bf3e86a`。

## 1. 实施顺序

1. 先扩展 P11C/P11B 两份 route 状态机，使 GET 返回合法 `esv_`，PUT 精确比较 expected、成功生成下一版本，并补缺版本、挂起串行、全状态 409、显式重载、迟到隔离和存储边界红测。
2. 技术 hook 增加严格版本 ref/state、全状态阻断/冲突、写入代次；普通整包与矩阵合并共用一条项目队列，每次执行读取最新 state/expected。
3. 把 guidance 纳入技术主状态/主 PUT；`useProjectGuidance` 删除 editor-state GET/PUT 和本地 guidance 水合，只保留 history/revise，并由页面传入权威 guidance。
4. 技术页面新增固定冲突提示和显式全量重载按钮；不得复用矩阵“重新载入远端矩阵”解除全状态阻断。
5. 商务 hook 增加严格版本、串行链、写入代次与全状态阻断；商务页面新增相同固定冲突/显式重载 UX。
6. 修正两份既有 E2E 的合法版本和精确请求体期望，完整复跑 P11B/P11C、矩阵、M3-D、lint/build 与全量串行 E2E。
7. 完成后仅发 `review_request`，不得提交或推送。

## 2. Codex 审查重点

1. 是否所有生产 editor-state PUT 都携带当前服务端 expected，尤其 guidance 和矩阵合并旁路。
2. 商务/技术是否真串行，下一请求是否在前一 200 后才读取新版本与最新 UI 状态。
3. 是否先按固定 code 分流全状态冲突，阻断全部写入且只允许显式全量 GET；是否错误地把全状态 409 当矩阵冲突。
4. 成功响应缺版本、重载与在途旧响应是否有代次保护；项目 A 的挂起链是否与 B 解耦。
5. guidance 是否只剩一份服务端权威状态；旧 localStorage guidance 是否完全不参与水合/CAS。
6. 是否保持 M3-D 唯一重载、业务成功不反转、矩阵三方合并和现有正文包边界。
7. 是否偷改后端、共享 API、组件算法、其他测试/配置，或新增 restore/强制覆盖/外网/持久版本。

## 3. 独立验收（严格串行）

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run lint
npm run build
npm run test:e2e:technical-editor-state-truth
npm run test:e2e:business-editor-state-truth
npm run test:e2e:matrix
npm run test:e2e:fuse-apply
npm run test:e2e:fuse-persistent-recovery
npm run test:e2e
```

Playwright 共用 `backend/data/biaoshu-e2e.db` 重置流程，所有命令必须 Chromium headless、workers=1、逐条串行；禁止并行运行两个 E2E。仓库根运行 `git diff --check`，暂存后再运行 `git diff --cached --check`。

## 4. 提交与后续

契约/计划由 Codex 先中文提交并推送；Grok 七文件实现经审查与独立验收后，由 Codex 单独提交前端，再更新路线图、联调和主交接。P12B-B 后只能进入 P12B-C 延迟写入围栏，禁止跳到检查点恢复；长期目标保持 active。
