# P13-B 已载入编辑版本更新时间可见性契约

模块：P13-B 技术标/商务标协作可见性快速第一版
用途：复用 editor-state 既有权威 `updatedAt`，让协作者在工作区标题区看到当前已载入服务端版本的更新时间，并在成功保存或显式重载后同步更新。
对接：`useTechnicalPlanEditors`、`TechnicalPlanWorkspace`、`useBusinessBidWorkspace`、`BusinessBidWorkspace`、`GET|PUT /api/projects/{id}/editor-state`。
状态：2026-07-20 已冻结，待 Grok 按六文件白名单 failure-first 实现。

## 1. 审计结论与选型

现有 editor-state 已具备全状态 `stateVersion` CAS、技术标/商务标 409 阻断、保留本地内容和显式重载，不能把旧冲突功能重新包装成多人协作。

精确显示“最后由谁更新”需要持久化操作者，并正确覆盖浏览器 PUT、AI 任务、个人回调、单次票据回调、融合写入/恢复、检查点恢复和修订恢复等多条写链；若只给浏览器 PUT 加用户字段，自动写入后会错误地把旧用户显示为最新操作者。该能力必须另包完成模型、SQLite 幂等迁移、全部写入口和脱敏身份投影，不进入本快速版。

P13-B 只使用服务端已经返回的 `updatedAt`。它是真实协作可见性，不新增数据、不猜操作者，也不声称在线状态。

## 2. 用户可见合同

技术标和商务标工作区标题区各显示一行：

```text
当前已载入版本：2026-07-20 12:34:56 UTC
```

固定规则：

- `updatedAt` 必须是服务端既有 UTC 无后缀 ISO 时间；前端只接受精确 `YYYY-MM-DDTHH:mm:ss`，允许其后出现 1–6 位小数；
- 合法值固定显示到秒并追加 `UTC`，不得按浏览器时区重新解释、不得依赖 `toLocaleString`；
- 缺失、null、空白、非法日期结构或非字符串时显示 `当前已载入版本：更新时间未知`；
- 技术标测试标识固定为 `technical-editor-version-freshness`；商务标固定为 `business-editor-version-freshness`；
- 文案必须说“当前已载入版本”，不得说“远端最新”“最后由某人编辑”“实时同步”或“在线成员”。

## 3. 状态更新与隔离

两个 Hook 各维护当前项目会话的 `versionUpdatedAt: string | null`，并遵守既有项目/会话/写入代次围栏：

1. 切换项目或初始化新会话时立即清空，禁止短暂显示旧项目时间；
2. 初始 GET 成功且 `stateVersion` 合法后，接受同一响应的 `updatedAt`；
3. 普通防抖 PUT、强制即时 PUT、矩阵合并 PUT 等只有在响应仍属于当前项目/会话、`stateVersion` 合法且成功被接受时，才接受同一响应的 `updatedAt`；
4. 检查点/修订/融合/AI 等既有成功后显式 GET 路径，在 GET 被当前会话接受时同步更新时间；
5. 409、网络失败、非法/缺失 `stateVersion`、迟到 A 项目响应、已作废写入代次均不得改变 B 项目或当前已载入版本的时间；
6. 冲突阻断期间保留本地正文和已载入版本时间；显式重载成功后才切换到新时间，重载失败继续保留旧时间；
7. `updatedAt` 只用于展示，禁止替代 `stateVersion`、参与 CAS、保存队列、矩阵版本、缓存键或本地持久化。

## 4. 共享展示组件

新增一个无副作用共享组件，职责仅为：

- 接收 `updatedAt: string | null` 与固定 `data-testid`；
- 以纯函数严格格式化服务端 UTC 时间；
- 非法值使用固定未知文案；
- 不发请求、不设定时器、不读取 storage/Cookie/URL、不持有项目状态。

技术标和商务标页面只负责把各自 Hook 的 `versionUpdatedAt` 传给该组件，不复制两套时间解析逻辑。

## 5. 六文件白名单

Grok 只允许修改：

1. `frontend/src/features/editor-state-collaboration/EditorStateVersionFreshness.tsx`（新建）
2. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
4. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
5. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
6. `frontend/e2e/editor-state-version-freshness.spec.ts`（新建）

禁止修改后端、模型、数据库/迁移、API schema、共享请求层、CSS、依赖、配置、其它 E2E 或文档；不得 `git add/commit/push`。

## 6. Failure-first 与专项验收

Grok 必须先只新建 E2E，在五个生产文件与冻结提交一致时运行真实业务红测。至少证明旧页面缺少两个固定测试标识；不得用错误 URL、收集失败、语法错误、fixture、超时或未启动服务冒充红测。

最终专项至少覆盖：

- 技术标和商务标初始 GET 合法时间按 UTC 固定格式显示；
- 缺失、null、空白、带时区后缀、日期越界或任意字符串均显示未知，不抛 unhandled/console error；
- 成功 PUT 后无需额外 GET 即更新到 PUT 响应时间，且 editor-state 请求数量没有因展示功能增加；
- PUT 409/网络失败保留当前已载入时间；显式重载成功后更新，失败继续保留；
- A→B 切换时立即清空，迟到 A GET/PUT 的 success/catch/finally 不污染 B；
- 页面不出现“在线”“最后由”“实时”等未实现承诺；
- `npm run lint`、`npm run build`、`git diff --check`、精确六文件、空暂存区通过。

Grok 只运行 P13-B 专项和必要的技术/商务真值聚焦，不运行整仓 E2E。Codex 独立复核 P13-B 专项，并按实际差异决定是否补一个受影响聚焦；本纯前端展示包不重复后端 pytest 或整仓 318 E2E。

## 7. 明确未做

本包不做操作者姓名/账号/用户 ID、self/other、在线成员、presence、心跳、轮询、SSE/WebSocket、协同光标、字段锁、评论、审批、审计事件、通知、跨项目时间线、数据库迁移、API 字段扩展、时区偏好或相对时间自动刷新。它只展示当前客户端已成功接受的服务端版本时间，不保证远端在显示后没有继续变化。

