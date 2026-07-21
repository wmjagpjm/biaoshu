# P13-I3 项目任务事件前端提示实施计划

> 状态：契约已冻结，等待 Grok B failure-first
> 契约：`docs/p13i3-project-task-event-frontend-contract.md`
> 实现分支：`collab/p13i3-grok-worktree`

## 1. 实施顺序

1. 只新增 `frontend/e2e/project-task-event-update.spec.ts`，生产三文件未改时先运行真实 failure-first，记录首个业务失败和未运行项。
2. 在 `ProjectTaskEventPanel.tsx` 实现认证/角色门控、原生 EventSource、结构化重复键检测、四类命名事件解析、固定安全展示、代次隔离和 close 生命周期。
3. 在技术标与商务标工作区只做薄挂载，分别使用固定 testId；不得改变现有任务管线和编辑状态刷新。
4. Grok B 串行运行 I3 专项、H3/freshness 代表专项、lint、build、diff-check，并发送 `review_request` 与 `result`；不得 Git 写入。
5. Codex 在主工作区独立检查 B 分支差异、白名单、静态禁区和测试反假绿；疑似问题先发只读 question，双方确认后才允许新的返修 task。
6. Codex 最终在主分支串行验收，精确暂存四文件及冻结文档，中文提交并推送；更新交接、路线图和联调清单。

## 2. 本地验证边界

Grok B 的相对 SQLite 数据库位于 `C:\Users\Administrator\biaoshu-p13i3-grok\backend\data`。若需启动 E2E 服务，不得与主工作区共用正在运行的端口；使用后端 `8011`、前端 `5175`，数据库 `sqlite:///./data/biaoshu-p13i3-e2e.db`。若现有 Playwright 配置硬编码端口，先报告 question，不得擅自修改配置文件或扩大白名单。

## 3. 反假绿门

- SSE 每个事件必须由 route mock 实际投递，断言展示文本、关闭次数和无额外请求；禁止只断言 EventSource 对象已创建。
- 结构化重复键用合法值覆盖的对照用例证明不是 JSON.parse 折叠假绿。
- A→B 场景必须先捕获旧 EventSource，再发送 A 迟到帧，断言 B 无旧提示。
- 控制帧和网络错误必须不展示后端原文；禁止用非零请求数代替“无任务详情请求”。
- 所有测试串行，禁止 sleep 作为完成证据、skip/xfail、源码字符串冒充行为。
