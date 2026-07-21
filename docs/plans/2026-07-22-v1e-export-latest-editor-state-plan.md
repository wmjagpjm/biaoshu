<!--
模块：V1-E 导出前最新编辑态落盘实施计划
用途：把技术/商务导出竞态拆为 Grok B failure-first、Grok A 最小前端实现和 Codex 独立验收。
对接：V1-E 契约、两个 editor-state hooks、两个工作区页面、共用 E2E。
二次开发：最终严格六文件；测试先行；疑似问题双方确认后才返修；Grok 禁止 Git 写入。
-->

# V1-E 导出前最新编辑态落盘实施计划

> **执行要求：** 使用 `executing-plans`；所有 Playwright 串行、单 worker、零重试。
> **完成状态：** 冻结=`2f3beb1`，实现=`2a1b1ec`，已独立验收并推送 `collab/grok-code-codex-review`。

**目标：** Word export 任务创建前，确定性保存或等待当前项目最新 editor-state；失败时保守不导出旧内容。

**架构：** 两个现有 hook 在各自保存链内提供导出准备门；两个页面只消费三态结果，不接触 PUT body；后端与 export payload 不变。新 E2E 以请求屏障证明 `PUT success < export POST`。

## 任务 1：冻结与独立 worktree（已完成）

1. Codex 提交推送契约/计划，记录冻结提交。
2. 从冻结点创建 `C:\Users\Administrator\biaoshu-v1e-export-flush-impl`，分支 `collab/v1e-export-flush-impl`。
3. Grok A/B 路由切到新 worktree；B 写 failure-first 时 A 不写生产。
4. 复用既有 E2E 临时数据库和端口；禁止两个代理并行启动 Playwright/pytest。

## 任务 2：Grok B failure-first（已完成）

**唯一可写：** `frontend/e2e/export-latest-editor-state.spec.ts`。

1. 建立技术/商务真实页面夹具和请求记录器，所有数据为合成唯一锚点。
2. 先覆盖 pending timer 与已在途保存的 `PUT < export`；当前生产必须真实失败。
3. 覆盖无修改零 PUT、409/失败零 export、快速双击单飞和项目切换迟到隔离。
4. 禁止固定时间等待、源码读取、hook 私有调用、真实下载弹窗、外网或浏览器存储。
5. 运行新专项并发送 review_request，报告真实 failed/passed/did-not-run、首红、端口/进程清理和 SHA-256。

## 任务 3：Codex 审查红测（已完成）

1. 核对首红来自 export 先于保存，而非页面未加载、登录失败或路由桩错误。
2. 核对技术/商务均触达真实编辑控件与导出按钮，PUT body 含唯一新锚点。
3. 核对无改动、失败、双击和切项目门不依赖测试顺序或固定 sleep。
4. 疑似问题先 question；Grok B 确认后才授权 test-only 返修。
5. 合格后冻结测试哈希，向 Grok A 下发 production-only 任务。

## 任务 4：Grok A 最小实现（已完成）

**唯一可写：** 契约 §6 的四个生产文件；冻结 E2E 只读。

1. 两个 hook 分别复用现有即时 PUT 与保存链，实现同语义三态导出门。
2. 保存链记录最近可判定结果；pending timer 只追加一次即时 PUT，无 timer 时只等待链且零额外 PUT。
3. 两个页面增加项目绑定单飞准备态；只有 `ready` 调现有 export。
4. 失败/冲突/stale 保守返回，不改后端、不复制 PUT body、不把正文放进任务 payload。
5. 串行运行新专项、图片告警、必要 truth、lint/build/diff，发送 review_request；不得 Git 写入。

## 任务 5：Codex 独立验收（已完成）

1. 核对最终严格六文件、测试哈希与冻结/确认链一致、暂存区为空。
2. 静态追踪 pending timer、保存链、最近状态、项目 session/epoch 和页面操作令牌。
3. 独立复跑契约 §8 分级命令；核对无新增请求、payload、存储或敏感输出。
4. 发现问题按 question→confirm→task→review_request 闭环。

## 任务 6：提交与文档闭环（已完成）

1. Codex 中文实现提交：`实现：保证Word导出使用最新编辑内容`。
2. 只推送 `collab/grok-code-codex-review`，核对本地与远端一致。
3. 更新契约、计划、HANDOFF-next、路线图和联调清单，写入真实数字、消息链与哈希。
4. 中文文档提交：`文档：闭环V1E导出前最新编辑态落盘`，推送后主工作区干净。

## 任务 7：继续 V1 主线（待下一独立包）

V1-E 后按证据比较稳健下载、人读文件名、多章内容质量门与任务结果正文安全刷新；`structure` 跨页设计、OCR、V2/V3 继续后置。

## 执行记录

1. 原始 14 项 failure-first 为 **11 failed / 3 passed**；双方确认 A1-A3 与测试强度后扩为 18 项。
2. 首版 A3 测试因 timer 尚未入链形成假红；双方确认后使用 `page.clock.fastForward(801/601)`，生产修复前真实结果为 **14 passed / 4 failed**。
3. A1-A3 生产返修后，新专项 **18/18 passed**。P9D 旧测试要求跨项目迟到任务仍下载，与 V1-E 更晚、更严格的零下载契约冲突；双方确认后只扩围 `export-image-warnings.spec.ts`，最终 **4/4 passed**。
4. Codex 独立技术/商务 truth **46/46 passed**，lint/build/diff-check 通过；实现提交 `2a1b1ec` 已快进并推送协作分支。
5. 最终严格六文件及 SHA-256 以 `docs/v1e-export-latest-editor-state-contract.md` §8.3 为准。
