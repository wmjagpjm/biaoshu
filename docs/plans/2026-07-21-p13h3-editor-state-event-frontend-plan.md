# P13-H3 编辑状态事件前端版本提示实施计划

> 状态：契约冻结，待实现
> 契约：`docs/p13h3-editor-state-event-frontend-contract.md`
> 分支：`collab/grok-code-codex-review`

## 1. 实施顺序

1. Grok 先在生产三文件未改时运行真实 failure-first，新增专项只验证 H3 行为；保存失败输出和测试范围，不修改文件以外内容。
2. 新建共享 `EditorStateEventUpdatePanel.tsx`，先补中文四字段文件注释，再实现严格 parser、认证角色门控、EventSource 生命周期、项目代次隔离、固定提示和单次用户刷新。
3. 技术标传入 `editors.stateVersion`（或现有等价当前版本字段）、`editors.reloadFromApi({ blocking: true })`；商务标传入 workspace 当前 stateVersion 与 `refreshFromApi()`。
4. 新增 `editor-state-event-update.spec.ts`，通过 `text/event-stream` route mock 验证真实 EventSource 事件、关闭、迟到和刷新计数；不得用源码字符串或非零请求数冒充行为。
5. Grok 仅运行专项与受影响 freshness 代表用例、lint/build，并发送 review_request；不得暂存、提交、推送或写交接文档。
6. Codex 检查 diff、白名单、parser 与状态代次，发现疑似问题先发只读 question；双方明确确认后才发送修复授权。确认前不得返修。
7. 最终串行验收通过后由 Codex 更新交接、路线图、联调清单，使用中文提交信息并推送。

## 2. 反假绿门

- 每个 EventSource 流必须由 route mock 实际发出帧，并断言 `withCredentials`、精确 URL、关闭次数和展示文本。
- 刷新按钮必须用真实页面回调计数；确认前零 editor-state GET，确认后精确一次。
- A→B 场景保持 A 流门控并发送迟到帧，断言 B 无旧提示、无旧刷新。
- 非法 JSON、错误字段、未知 event、控制帧和网络错误都必须断言固定不可用文本，且不出现后端 detail、ID 或正文。
- 所有测试串行；禁止 `sleep` 作为完成证据、禁止并发 Playwright、禁止整仓重复测试。

## 3. 预期验证命令

```powershell
cd C:\Users\Administrator\biaoshu\frontend
npm run test:e2e -- e2e/editor-state-event-update.spec.ts --workers=1 --retries=0
npm run test:e2e -- e2e/editor-state-version-freshness.spec.ts --workers=1 --retries=0
npm run lint
npm run build
cd ..
git diff --check
git diff --cached --name-only
```

不运行后端全量、整仓 E2E、xdist 或并发 pytest；只有出现共享认证/编辑器接口回归证据时才扩大范围。

