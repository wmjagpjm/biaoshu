# P13-G1 在途操作级交接：项目章节编辑意图租约后端

> 日期：2026-07-20
> 当前状态：**只读审计与设计已完成，等待本版本冻结提交推送后下发 Grok**
> 审计基线：`f0325d0593b0b8c6fc291ee08f646cffe74164fe`
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md`
> 计划：`docs/plans/2026-07-20-p13g1-project-chapter-edit-intent-lease-backend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu P13-G1 项目章节编辑意图租约后端。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13g1-in-progress.md、docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md、docs/plans/2026-07-20-p13g1-project-chapter-edit-intent-lease-backend-plan.md、docs/HANDOFF-p13f2-in-progress.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 与 GitHub 实际分支一致。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add .、并发 pytest 或沿用 P13-F2 白名单。

P13-G1 只做后端“章节编辑意图租约”，不是硬锁。严格七文件：entities、模型导出、schemas、新 service、新 API、main、新专项测试。不得修改 editor-state PUT、P13-F1/F2、认证、前端、依赖、配置、已有测试或文档。

Grok 先只写新测试做真实 failure-first，再实现并串行自测。Codex 疑似问题必须先让 Grok 只读确认；双方确认存在后才发新返修 task。Grok 不得暂存、提交、推送或写文档。
```

## 2. Git 与文件真值

审计时仓库干净，本地、远端引用与 GitHub 实际分支均为：

```text
f0325d0593b0b8c6fc291ee08f646cffe74164fe
```

严格白名单基线：

| 文件 | SHA-256 / 状态 |
|---|---|
| `backend/app/models/entities.py` | `FE935EEE0DED226A694F2CD61A0BE21239AB7EEB432CE3E0D800A1B4F0A0142A` |
| `backend/app/models/__init__.py` | `ADDDDDAE18A2DEC1CFBF67F382113DFF17E92E170FA8BD1CFA55C7D6E2F63F4B` |
| `backend/app/api/schemas.py` | `1ECC15036BB89F6ABC225A30FB88CED8A467B64C039C31EDB718C29AFB2BEFA9` |
| `backend/app/main.py` | `BFD98A36230B9D9CAFA566BDF327480777F737375379C3B22395A963A04A99BA` |
| `backend/app/services/project_chapter_edit_lease_service.py` | 不存在 |
| `backend/app/api/project_chapter_edit_leases.py` | 不存在 |
| `backend/tests/test_p13g1_project_chapter_edit_lease.py` | 不存在 |

## 3. 冻结结论

- 现有技术标章节不是实体表，只存在 `ProjectEditorStateRow.chapters_json`，Schema 允许 list/dict/null；章节 ID 可能由前端或模型生成。
- 现有 PUT 是 13 键整包写，虽然有 `expectedStateVersion` CAS，但没有 clientId、章节差异或锁令牌。
- 因此 P13-G1 只能做 advisory intent lease；若改称强制锁，会对旧客户端和任务写链作出虚假承诺。
- 选择 heartbeat/leave 两端点、单章节单持有者、45 秒 TTL、15 秒建议续租、每用户项目最多 8 个活动章节。
- heartbeat 锁后精确验证当前技术标章节唯一命中；leave 允许章节已删除后清理。
- 冲突只返回重新校验的安全 holder username，不返回任何内部 ID、digest、正文、标题或时间细节。

## 4. 协作状态

当前尚未发送 P13-G1 task。必须先提交并推送本次冻结文档，再让 task 引用实际冻结提交 SHA、七文件白名单和上述基线哈希。

Grok OAuth 已于本会话重新认证成功，默认模型 `grok-4.5`。启动前必须确认没有同一 P13-G1 Grok 进程，禁止重复进程。

## 5. 审查重点

1. 项目级数据库锁必须在项目/章节判断、过期清理、计数、冲突判断之前；`now` 必须锁后采样。
2. 单章节唯一键与项目锁共同保证并发抢占恰一赢家；不得用进程锁或 GIL 冒充数据库并发。
3. `chapterId` 只精确匹配当前 `chapters_json` 字典项的原生字符串 ID；重复目标拒绝，不 trim/回退标题。
4. 同用户不同 client 仍冲突；same user+same digest 才续期。
5. holder 用户/成员/角色/安全用户名每次冲突前重新校验；失效 holder 可接管。
6. 当前 actor 用户名也须安全；不安全 actor 固定 403、零租约。clientId 只在请求瞬时内存和 SHA-256 摘要；请求校验与错误不得回显原始值。
7. leave 不因章节删除失去清理能力，但必须五维精确删除。
8. 测试必须真实 HTTP/DB/线程/故障施压，不接受源码、宽状态、预插最终结果或空集合假绿。

## 6. 串行测试边界

Grok：新专项、P13-F1、认证/项目/editor-state 代表节点、py_compile、diff-check。Codex 独立复跑新专项和必要代表回归。

明确不跑：后端全量、任何 Playwright、前端 lint/build、pytest-xdist 或多个并发 pytest。线程并发只允许在单个专项用例内部，并为每个线程创建独立 client/session。

## 7. 禁止事项

- 禁止把本包写成强制锁或声称已阻止 editor-state 覆盖。
- 禁止修改现有 PUT、任务、回调、P13-F1/F2、认证、前端、依赖、配置或已有测试。
- 禁止新增 GET/list、SSE/WebSocket、广播、游标、历史、审计、通知、光标、选区或自动合并。
- 禁止 Grok Git 写操作、文档写入、清理测试产物或越权修复。
- 禁止 Codex 未经双确认直接代写主实现；只有双方确认问题后才可另发返修授权。
