# P13-C 当前已载入版本修订来源可见性实施计划

> 契约：`docs/p13c-current-revision-source-visibility-contract.md`  
> 实现者：Grok  
> 审查、独立验收、文档、提交与推送：Codex

## 1. 后端

1. 在修订账本服务增加只读 helper：输入 workspace/project/响应 `stateVersion`，只查最新一条 `state_version/source_kind`；严格匹配后返回九类来源，否则 `None`。
2. 在 `EditorStateOut` 增加必出、可空、九类 `Literal` 字段 `currentRevisionSourceKind`。
3. GET/PUT editor-state 构造响应时调用 helper；保持现有项目校验、CAS、矩阵冲突和 commit 边界。
4. 新建 P13-C 后端测试，先证明字段/逻辑缺失，再覆盖九源、断链、坏值、零写、作用域、PUT 与最小 SQL。

## 2. 前端

1. 从既有修订 API 导出来源合法性校验，继续复用唯一来源集合与中文标签。
2. 技术/商务 hook 增加当前来源内存态，与 P13-B 时间使用同一“合法 stateVersion 已接受”门及同一迟到隔离；切项目一并清空。
3. 扩展共用 freshness 组件展示第二行来源；两页面只传入 hook 值，不新增业务分支。
4. 扩充既有 P13-B E2E 为 P13-B/P13-C 聚焦套件，先红后绿；验证双页面、九源/坏值、GET/PUT/矩阵/重载、切项目与失败迟到，不增加网络请求。

## 3. 受限验收命令

Grok 默认只运行：

```powershell
cd C:\Users\Administrator\biaoshu\backend
python -m pytest tests/test_p13c_current_revision_source.py -q

cd C:\Users\Administrator\biaoshu\frontend
npx playwright test e2e/editor-state-version-freshness.spec.ts --workers=1 --retries=0
npm run lint
npm run build
```

若直接受影响旧测试失败，只把准确失败文件与原因发给 Codex，由 Codex 决定 test-only 扩围；禁止先跑后端全量或整仓 E2E。

## 4. 完成条件

- 契约全部行为有真实测试证据。
- 生产/测试文件严格位于白名单，中文注释四字段同步更新。
- 不含迁移、新请求、轮询、storage、actor 或远端实时宣称。
- Grok 发送 `review_request`，附红测、绿测、受影响测试、lint/build、文件清单和已知限制。
- Codex 独立验收后提交实现；再更新三份中文文档，提交并推送。
