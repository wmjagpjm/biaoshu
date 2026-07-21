# P13-H3 编辑状态事件前端版本提示实施计划

> 状态：实现与独立验收完成；未注册命名事件边界已记录，协议扩展另包处理
> 契约：`docs/p13h3-editor-state-event-frontend-contract.md`
> 分支：`collab/grok-code-codex-review`

## 1. 实施顺序

1. Grok 先在生产三文件未改时运行真实 failure-first，新增专项只验证 H3 行为；保存失败输出和测试范围，不修改文件以外内容。
2. 新建共享 `EditorStateEventUpdatePanel.tsx`，先补中文四字段文件注释，再实现严格 parser、认证角色门控、EventSource 生命周期、项目代次隔离、固定提示和单次用户刷新。
3. 两个 Hook 各增加 `currentStateVersion` React 状态并导出：所有合法版本接受路径与 ref 同步更新；项目切换、非法版本和既有清空路径同步置 null；禁止改变请求、保存链或冲突行为。技术标传入 `editors.currentStateVersion`、`editors.reloadFromApi({ blocking: true })`；商务标传入 `currentStateVersion` 与 `refreshFromApi()`。
4. 新增 `editor-state-event-update.spec.ts`，通过 `text/event-stream` route mock 验证真实 EventSource 事件、关闭、迟到和刷新计数；不得用源码字符串或非零请求数冒充行为。
5. Grok 仅运行专项与受影响 freshness 代表用例、lint/build，并发送 review_request；不得暂存、提交、推送或写交接文档。
6. Codex 检查 diff、白名单、parser 与状态代次，发现疑似问题先发只读 question；双方明确确认后才发送修复授权。确认前不得返修。
7. 最终串行验收通过后由 Codex 更新交接、路线图、联调清单，使用中文提交信息并推送。

## 2. 反假绿门

- 每个 EventSource 流必须由 route mock 实际发出帧，并断言 `withCredentials`、精确 URL、关闭次数和展示文本。
- 刷新按钮必须用真实页面回调计数；确认前零 editor-state GET，确认后精确一次。
- A→B 场景保持 A 流门控并发送迟到帧，断言 B 无旧提示、无旧刷新。
- 非法 JSON、重复键、错误字段、默认 `message`、控制帧和网络错误都必须断言固定不可用文本，且不出现后端 detail、ID 或正文；未注册命名事件另以真实 SSE 稳定窗口记录原生不可观测边界，不冒充已修复。
- 所有测试串行；禁止 `sleep` 作为完成证据、禁止并发 Playwright、禁止整仓重复测试。

## 3. failure-first 与范围修订记录

生产未改时专项真实结果为 **1 passed / 1 failed / 5 did not run**，首个业务失败是技术标就绪后 EventSource 流数量仍为 0；status=`msg_1e4734a045024eed91aaf13a58ef705e`。随后 Codex 与 Grok 分别确认两个 Hook 无公开当前版本，原四文件白名单无法满足等值判断；question=`msg_6889315838a447a4be37811772f2a174`，confirmation=`msg_baac83f66c214b279eb8192527beab0d`。本修订仅扩入两个 Hook，不放宽其它边界。

## 4. 预期验证命令

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

## 5. 完成回执

第一轮 Grok 实现回执=`msg_52e843e975874aafad57b902885a3112`，Codex 发现五项缺口后经 `msg_cb44e9eb820044219411705642779060` 双确认，返修回执=`msg_e9809e17435c494589e7cf1f13b8262a`。第二轮重复键与两页面迟到旗标经 `msg_4b1db4d34b6744ec9185a53a1af8bd6e`、`msg_ac39ea4388364d70b3dd7eb8f2510852` 双确认，返修回执=`msg_898315bea44b4cfca1435744b0cd920f`。Codex 独立串行结果为 H3 `15 passed`、freshness `17 passed`、lint/build/diff-check 全通过；功能提交=`40aacc7`。E 仅保留原生 EventSource 不可观测边界，协议扩展不得并入本包。
