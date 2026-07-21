# P13-I4 项目任务状态安全对账实施计划

> 状态：契约已冻结，等待 Grok A/B failure-first
> 契约：`docs/p13i4-project-task-status-reconciliation-contract.md`
> 基线：`02cc2bd`（I3 文档闭环提交）

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
