<!--
模块：P12B-B 技术标/商务标前端 editor-state 全状态 CAS 契约
用途：冻结浏览器保存队列、stateVersion 生命周期、全状态冲突 UX 与 guidance 单一写入边界。
对接：P12B-A GET/PUT stateVersion/expectedStateVersion；技术标、商务标、响应矩阵与 M3-D 重载。
二次开发：本包只收口浏览器写入者，不处理后台任务/callback/M3-D 服务端迟到写入，也不实现检查点恢复。
-->

# P12B-B 技术标/商务标前端 editor-state 全状态 CAS 契约

> **状态**：只读审计完成，契约已冻结；等待前端受限实现。
> **工作分支**：`collab/grok-code-codex-review`。
> **前置提交**：P12B-A 计划/契约=`0b55c30`、实现=`780cc82`、闭环=`bf3e86a`；后端串行全量 537 passed，前端单 worker 串行全量 184 passed。

## 1. 审计结论：实际有三个浏览器写入者

1. `useTechnicalPlanEditors`：初始/任务后 GET；800ms 防抖整包 PUT；响应矩阵普通写、三方合并 PUT；已有项目会话隔离和普通保存串行链。
2. `useBusinessBidWorkspace`：初始/任务/修订后 GET；600ms 防抖商务整包 PUT；已有项目会话隔离，但多个同项目 PUT 尚未形成串行链。
3. `useProjectGuidance`：独立 GET/PUT `guidance`，并从 `biaoshu.projectFeedback.{projectId}` 水合本地 guidance。它与技术标主 hook 各自读取和写入同一 editor-state，无法共享同一个全状态版本，必须在本包消除独立网络写入。

技术标响应矩阵合并当前直接调用 PUT，没有进入普通保存 Promise 链；P12B-B 也必须把它与整包保存放入同一项目队列。只给两个主 hook 增加字段而保留 guidance/合并旁路，不构成“每次浏览器 PUT 都带当前 expected”的安全门。

## 2. 服务端版本的唯一来源

- `EditorStateApi` 必须消费服务端 `stateVersion`，格式精确为 `^esv_[0-9a-f]{32}$`；不得根据本地内容、`updatedAt`、矩阵版本或时间生成。
- 当前项目初始 GET 缺失/格式非法时按固定加载失败处理：不挂可编辑成功态、不发送 PUT、不回退 mock/localStorage。
- 每个当前项目会话只在内存保留 `stateVersion` 与冲突状态；禁止写入 localStorage、sessionStorage、URL、Cookie、IndexedDB、clipboard、console 或反馈历史。
- 每次 editor-state PUT 均必须携带执行时内存中的 `expectedStateVersion`；包括技术整包、商务整包、技术 guidance 与响应矩阵合并。
- PUT 200 后只接受同一有效会话响应中的合法 `stateVersion`，再允许队列下一项执行。成功响应缺失/格式非法时视为“可能已写入但客户端版本未知”，立即阻断后续自动保存并要求显式重载；不得拿旧 expected 自动重试。
- 网络/普通 HTTP 失败不得推进版本；错误固定中文脱敏，不记录响应原文。

## 3. 同项目保存队列与迟到响应

技术标与商务标分别维护当前项目的串行保存链：

1. 防抖只负责合并频繁编辑；定时器触发后把保存加入当前项目队列，不得与前一 PUT 并发。
2. 每个队列任务真正开始时读取最新 UI 状态和最新服务端版本，不能在定时器创建时固化旧版本/旧正文。
3. 第一项成功后更新版本，第二项才以新版本发送；同页连续编辑不得因自身前一成功写入产生伪 409。
4. 项目切换立即重置新项目队列、版本、阻断和冲突状态；旧项目挂起请求不得阻塞新项目，也不得在返回后改写新项目版本、错误、冲突或内容。
5. 同项目显式重载/任务后重载递增写入代次并清除未发送定时器；旧代次响应即使迟到也不得重新解除/触发阻断或覆盖新 GET 的版本。若旧请求已到服务端，P12B-A CAS 保证其要么先提交并被 GET 观察，要么以后以陈旧 expected 冲突；客户端不得假装能取消已到服务端的事务。

技术标普通整包仍不发送 `parsedMarkdown`，避免浏览器回写任务/回调正文；商务标仍只发送 parsedMarkdown 与四类 business 字段。新增 expected 不得扩大正文包。

## 4. 全状态冲突与矩阵冲突必须分流

全状态冲突只认 `status=409` 且 `detail.code=editor_state_version_conflict`：

- 固定显示“编辑内容已被其他操作更新，已停止自动保存。重新载入远端内容将替换当前未保存修改。”，不得展示服务端 message、版本串、正文、项目 ID、路径或异常。
- 保留当前本地内容供用户查看/复制，但阻断该项目所有 editor-state PUT；继续编辑也不得自动重试。
- 只提供显式“重新载入远端内容”动作；成功 GET 原子替换当前全状态、接受新版本、清冲突并恢复保存。不得采用 `currentStateVersion` 直接续写，因为客户端尚未加载与该版本对应的完整正文。
- 重载失败保持阻断与本地内容，显示固定加载失败；不得回退旧服务端版本或强制覆盖。
- 不提供“仍然覆盖”“忽略冲突”“自动合并整态”或循环重试。

技术标现有响应矩阵 409 兼容继续保留，但必须先识别全状态 code：

- 有全状态 code 时绝不构造矩阵远端空数组或三方合并预览，只走上述整态冲突。
- 无全状态 code 且 detail 精确含矩阵正文/版本时，继续既有矩阵冲突、字段级选择和显式应用合并。
- 矩阵合并 PUT 进入同一技术保存队列，同时携带 `expectedStateVersion`、`responseMatrixVersion` 和 `responseMatrix`；成功同时更新两个版本。
- 全状态阻断期间，“重新载入远端矩阵”或旧合并按钮都不能解除阻断或发 PUT。真实服务端因全状态版本包含矩阵，远端矩阵变化通常先触发全状态冲突；不得伪造仍可只合并矩阵的成功路径。

## 5. guidance 收口

- 技术标主编辑态纳入 `guidance`，初始/重载 GET 与 outline/chapters/facts/analysis/matrix 同源水合。
- `updateGuidance` 必须更新技术标主 hook 内存状态，并由同一 800ms 队列、同一 expected 保存；不得再有独立 guidance GET/PUT。
- `useProjectGuidance` 仅负责反馈历史与 revise 调用，并接收当前服务端权威 guidance 供 revise payload 使用；不得从 localStorage guidance 水合成功内容。
- `biaoshu.projectFeedback.{projectId}` 继续只承载既有反馈 history 语义；更新 history 时可保留旧对象中无关字段，但旧 guidance 永远不参与页面水合、expected、冲突恢复或 API 成功判定。不得迁移、删除或把版本写入该键。

## 6. 任务、修订与 M3-D 重载

- 技术任务、商务任务/修订以及 M3-D 原子确认/恢复成功后，继续只通过既有单次 editor-state GET 重载；成功响应必须同步接受新的 `stateVersion`。
- 重载水合必须跳过下一次防抖保存，禁止把刚加载的远端状态原样 PUT 回去。
- 业务 POST 成功但重载失败的既有语义不反转；仍显示“业务已完成但刷新失败”或固定加载失败，不把 POST 谎报为失败。
- 在途旧 PUT 的回调受写入代次隔离，不能在任务重载后写回旧版本或错误。
- P12B-B 不改变任务、个人 callback、P8C 票据或 M3-D 后端写入；这些写入者仍可能在本包之后改变状态，浏览器下一次 CAS 会检测，但真正的迟到写入拒绝属于 P12B-C。

## 7. 安全、存储和兼容边界

- 继续使用同源 Cookie 与内存 CSRF；禁止读取 `document.cookie`，禁止把 Cookie/CSRF/版本写入任何存储或日志。
- 错误和冲突固定中文，不拼接后端 detail/message、网络异常、正文、项目 ID 或版本。
- Strict Mode、项目 A→B、挂起 GET/PUT、迟到 200/409/失败均不得造成跨项目污染或新项目队头阻塞。
- 保持 P11B/P11C 旧 workspace/editors 键忽略且原值不变；真实空态仍为空，不复活 demo/mock。
- 不新增轮询、定时刷新、模块全局项目缓存、BroadcastChannel、WebSocket、浏览器锁或外网请求。
- 本包不统一 editor-state `no-store`，不修改后端 API、Schema、CSRF、权限或矩阵 409 结构。

## 8. 精确七文件白名单

Grok 只允许修改：

1. `frontend/src/features/technical-plan/hooks/useTechnicalPlanEditors.ts`
2. `frontend/src/features/technical-plan/hooks/useProjectGuidance.ts`
3. `frontend/src/features/technical-plan/pages/TechnicalPlanWorkspace.tsx`
4. `frontend/src/features/business-bid/hooks/useBusinessBidWorkspace.ts`
5. `frontend/src/features/business-bid/pages/BusinessBidWorkspace.tsx`
6. `frontend/e2e/technical-editor-state-truth.spec.ts`
7. `frontend/e2e/business-editor-state-truth.spec.ts`

禁止修改后端、共享 HTTP 客户端、类型公共文件、响应矩阵组件/算法、M3-D 组件、package.json/lock、Playwright 配置、其他 E2E、依赖、脚本或文档；不得 commit/push。

## 9. 反假绿验收

两份既有 E2E 必须先补真实 failure-first 场景，并至少覆盖：

- 技术/商务初始 GET 的合法版本被保存；缺失/非法版本固定加载失败且零 PUT。
- 技术整包、guidance、矩阵合并与商务整包的每个 PUT 都带执行时最新 expected；PUT 200 的新版本严格串给下一请求。
- 同项目连续编辑用真实挂起路由证明第二 PUT 在第一响应前为 0；第一成功后第二请求正文为最新状态且 expected 为第一响应版本。
- 固定全状态 409 保留本地、阻断所有后续 PUT、无自动重试、无矩阵伪冲突；显式重载恰一次 GET 后才替换远端、更新版本并恢复保存。
- 显式重载失败保持阻断；PUT 200 但缺非法 stateVersion 同样阻断并要求重载。
- 技术既有矩阵 409/三方合并继续可用；合并 PUT 精确只有 `responseMatrix/responseMatrixVersion/expectedStateVersion`，不带 analysis/outline/chapters/facts/guidance。
- guidance 编辑不产生独立 GET/PUT；只进入技术主队列，正文包含 guidance+expected；反馈历史仍只留既有本地语义，版本不落盘。
- 任务/M3-D 重载接受新版本且不触发水合回写；业务成功但重载失败语义不反转。
- A 挂起 PUT/200/409/失败与任务重载迟到不污染、不阻塞 B；Strict Mode、网络白名单、Cookie/CSRF、旧键保值和存储零版本继续精确断言。

禁止 `waitForTimeout` 伪同步、顺序请求冒充并发、`or`/宽泛状态码、只断言按钮出现、不验证请求正文/次数/顺序、吞异常、把测试桩客户端自报版本当服务端真值，或通过修改既有期望绕过功能缺失。

## 10. 明确非目标与后续

- 不实现 P12A 检查点 restore/history/delete/download/自动检查点或恢复按钮。
- 不给任务、revise、个人 callback、P8C 票据或 M3-D 后端写入加 expected；这是 P12B-C。
- 不新增跨标签页实时协作、字段 CRDT、整态三方合并、强制覆盖或离线编辑队列。
- P12B-B 完成后必须先做 P12B-C 延迟写入围栏，随后 P12B-D 才能开放恢复。
