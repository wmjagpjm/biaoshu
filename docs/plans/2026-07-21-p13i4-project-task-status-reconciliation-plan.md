# P13-I4 项目任务状态安全对账实施计划

> 状态：已完成实现、双方确认返修、Codex 独立审查与最终验收
> 契约：`docs/p13i4-project-task-status-reconciliation-contract.md`
> 基线：`02cc2bd`（I3 文档闭环提交）
> 提交：冻结=`9d2cc27`、后端=`2ccfd0f`、前端=`ef6fe54`、注释修正=`7554d5d`

## 1. 并行实施顺序

1. Grok A 在独立 worktree 只新增后端安全状态投影、路由、Schema 和专项测试；先运行真实 failure-first，再串行自测后发送 review_request/result，不做 Git 写入。
2. Grok B 在另一独立 worktree 只扩展 I3 面板回调、`useProjectPipeline` 当前任务单飞对账、技术/商务薄接线和新 Playwright 专项；使用独立前端/后端端口和独立数据库，不修改 A 的文件。
3. Codex 等待两边结果后，在主工作区分别检查白名单、差异、静态禁区、敏感字段和反假绿证据；疑似问题先发 question，双方确认后才发最小返修 task。
4. Codex 合并并串行运行后端 I4 专项、受影响 P13-I1/I2/P13-A 代表回归、前端 I4/H3/freshness、lint、build、diff-check；通过后更新五份交接文档、中文提交并推送协作分支。

## 2. 独立运行边界

- Grok A 数据库目录：`C:\Users\Administrator\biaoshu-p13i4-grok-a\backend\data`。
- Grok B 若启动 E2E，使用后端 `8012`、前端 `5176`，数据库 `sqlite:///./data/biaoshu-p13i4-grok-b-e2e.db`；不得占用主工作区端口或数据库。
- 所有 pytest/Playwright 串行，禁止 xdist、并发分组、sleep 作为完成证据、skip/xfail 和整仓重复测试。

## 3. 反假绿检查点

- 后端测试必须通过真实 HTTP 路由断言精确三键和敏感字段缺失，不能只测 `task_service` 函数；跨 workspace 必须是真实第二 workspace，不得用同空间第二项目冒充。
- 前端必须由 route mock 实际投递合法/非法 task-event，并断言请求顺序、请求次数和请求 URL；只断言 callback 或 EventSource 创建不算通过。
- 重复事件必须用不同合法 progress 对照，证明单飞和项目代次守卫真实生效；失败响应必须断言固定文案和没有旁路请求。
- 成功对账后必须证明 message/result/error 保留原值，editor-state 与正文 API 请求数为零。

## 4. 实施偏差与双方确认

- 双方确认并修复同 eventId 完成后重放再次 GET、A 请求挂起切换 B 时可能并发、迟到 `running` 响应覆盖 SSE 终态、eventId Set 无上限增长等真实问题。
- 测试补强了 result/error 真实页面状态、status 500 固定文案与旁路计数、无淘汰 mutation、后端非法 body 精确 422 合同，删除了恒真断言。
- 为保持 200 条 FIFO 去重与终态判定为纯逻辑，双方确认唯一扩展生产白名单：`frontend/src/features/technical-plan/hooks/projectTaskStatus.ts`。
- Codex 拒绝并撤回生产 `window` 测试探针和动态 API import；最终下载行为仍使用原有同步 `window.open`，未扩大隐私面或旧流水线变更面。
- “一次”最终解释为每个合法新事件触发一次只读确认且所有请求在途单飞，不是任务生命周期最多一次；不采用 active progress `max()`，因为冻结契约未定义单调进度。
- 闭环注释审计又发现技术/商务页面仍写“任务事件提示不进入 useProjectPipeline”。question=`msg_23c3424d6b154f43af2921b09fdac9a1`，Grok 确认=`msg_e918277a10164ad5adcc6a829708d7c0`；双方确认后才授权两文件纯注释 task=`msg_e9993fc7aa49409f80764846f87ba16a`，review_request=`msg_86824ed8031e4673a6a59f881ae47777`，Codex 提交=`7554d5d`。

## 5. 最终验收

- 主工作区后端 I4 + I1 + I2 + P13-A：**81 passed**。
- 主工作区前端 I4 + I3 + H3 + freshness：**45 passed**。
- `npm run lint`、`npm run build`、Python `compileall`、严格文件边界与 `git diff --check`：通过。
- 所有 Playwright 均固定 `--workers=1 --retries=0`；后端最终数据库目录为 `backend/data/codex-p13i4-final`。
- 未运行后端全量或整仓 **318 E2E**。I4 不提供任务结果自动展示、正文自动刷新、通知、评论审批、协同光标、WebSocket、强制锁、多人任务列表或历史时间线；下一包必须重新审计和冻结。
