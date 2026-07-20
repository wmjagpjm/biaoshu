# P13-G1 完成态操作级交接：项目章节编辑意图租约后端

> 日期：2026-07-20
> 当前状态：**已实现、双确认返修、Codex 独立验收并推送**
> 审计基线：`f0325d0593b0b8c6fc291ee08f646cffe74164fe`
> 契约冻结：`a0b7c48bd82c3f177f2fbc5ee0c274ef07e6da6f`
> 功能实现：`015ab37`（`功能：交付P13G1章节编辑意图租约后端`）
> 分支：仅 `collab/grok-code-codex-review`，禁止操作 `main`
> 契约：`docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md`
> 计划：`docs/plans/2026-07-20-p13g1-project-chapter-edit-intent-lease-backend-plan.md`

## 1. 新会话复制即用

```text
继续 biaoshu 剩余主线，从 P13-G2 项目章节编辑意图前端提示的只读审计开始。仓库 C:\Users\Administrator\biaoshu，只能使用 collab/grok-code-codex-review，禁止操作 main。

先读 docs/HANDOFF-p13g1-in-progress.md、docs/p13g1-project-chapter-edit-intent-lease-backend-contract.md、docs/plans/2026-07-20-p13g1-project-chapter-edit-intent-lease-backend-plan.md、docs/HANDOFF-p13f2-in-progress.md、docs/HANDOFF-next.md、docs/plans/2026-07-12-bid-writer-roadmap.md、docs/integration-checklist.md。

先核对 git status -sb、本地 HEAD、origin/collab/grok-code-codex-review 与 GitHub 实际分支一致。严禁 pull/reset/checkout/stash/rebase/clean、操作 main、git add .、并发 pytest 或沿用 P13-F2 白名单。

P13-G1 已以提交 015ab37 完成，只做后端“章节编辑意图租约”，不是硬锁。P13-G2 尚未冻结，先审计技术标选章、编辑器生命周期、P13-F2 clientId/串行器复用边界，不得直接实现。

后续仍由 Grok 承担高耗费实现与自测，Codex 独立审查；疑似问题必须先只读双确认，确认后才另发返修 task。Grok 不得暂存、提交、推送或写文档。
```

## 2. Git 与文件真值

P13-G1 功能提交后本地与远端分支均为：

```text
015ab37
```

严格白名单审计基线：

| 文件 | SHA-256 / 状态 |
|---|---|
| `backend/app/models/entities.py` | `FE935EEE0DED226A694F2CD61A0BE21239AB7EEB432CE3E0D800A1B4F0A0142A` |
| `backend/app/models/__init__.py` | `ADDDDDAE18A2DEC1CFBF67F382113DFF17E92E170FA8BD1CFA55C7D6E2F63F4B` |
| `backend/app/api/schemas.py` | `1ECC15036BB89F6ABC225A30FB88CED8A467B64C039C31EDB718C29AFB2BEFA9` |
| `backend/app/main.py` | `BFD98A36230B9D9CAFA566BDF327480777F737375379C3B22395A963A04A99BA` |
| `backend/app/services/project_chapter_edit_lease_service.py` | 不存在 |
| `backend/app/api/project_chapter_edit_leases.py` | 不存在 |
| `backend/tests/test_p13g1_project_chapter_edit_lease.py` | 不存在 |

最终七文件 SHA-256：

| 文件 | SHA-256 |
|---|---|
| `backend/app/models/entities.py` | `BA601387E7061BAD1077D972D5A470E95C72EBA3E17BDE84BE173779B0F85010` |
| `backend/app/models/__init__.py` | `F5BED56153BC6D8C1F499FA4EBF77955C64C25FAA49532E95A4AC24838447A84` |
| `backend/app/api/schemas.py` | `28BD4DFB11DD5E7448BAD250860BE0C38079358F6D01D411139C66838D575B30` |
| `backend/app/services/project_chapter_edit_lease_service.py` | `8949D55BA846D0028E76E1BBF63D34ED80970AC688C1B2D8ABB9DCECC25B7860` |
| `backend/app/api/project_chapter_edit_leases.py` | `F52DABBCEE4EB8EFF40F02B92E56BAB58CB79FEAC90C02654D5AC497DE5B5CC8` |
| `backend/app/main.py` | `ED385E5BC020BCB7540ABFF45D690EB4C4C8D65A0CBAAE00323199C671EE1CD8` |
| `backend/tests/test_p13g1_project_chapter_edit_lease.py` | `865D6235CC5C8E7670DC6EDC02D9D7AE85FBA82E629B719608B139CA21C1457E` |

## 3. 冻结结论

- 现有技术标章节不是实体表，只存在 `ProjectEditorStateRow.chapters_json`，Schema 允许 list/dict/null；章节 ID 可能由前端或模型生成。
- 现有 PUT 是 13 键整包写，虽然有 `expectedStateVersion` CAS，但没有 clientId、章节差异或锁令牌。
- 因此 P13-G1 只能做 advisory intent lease；若改称强制锁，会对旧客户端和任务写链作出虚假承诺。
- 选择 heartbeat/leave 两端点、单章节单持有者、45 秒 TTL、15 秒建议续租、每用户项目最多 8 个活动章节。
- heartbeat 锁后精确验证当前技术标章节唯一命中；leave 允许章节已删除后清理。
- 冲突只返回重新校验的安全 holder username，不返回任何内部 ID、digest、正文、标题或时间细节。

## 4. 协作与验收状态

- 初始 task=`msg_0c9d11a1bdf946c9b8f2f85b68152774`。
- 有效 failure-first status=`msg_7e89c95cb9e143aab17fe46d92a1a9a0`：`42 failed / 3 passed`；恢复重复 status=`msg_c818b81805b54255895e7d9e50248a28` 不作为纯红测证据。
- 首轮 review=`msg_5a97ada55378441fa1ed223cf9f74bef`：专项 `45 passed`，P13-F1/认证/editor-state `41/8/1 passed`。
- Codex question=`msg_cec182e52c6c4775b99ef33eef0cbf60`，Grok 只读确认=`msg_7d6862739de5449082c65350b4536deb`，六项均确认存在。
- 双确认后返修 task/review=`msg_2e591638e1b94f559cdab1ea3e57c0d6`/`msg_2a7689d2a917465fb0c6f3de486d379a`；Grok 聚焦/专项 `17/53 passed`。
- Codex result=`msg_18dc76c33b9f47d0a72d754e7578682c`；独立专项/P13-F1/认证/editor-state `53/41/8/1 passed`。
- 六文件 `py_compile`、diff-check、严格七文件、空暂存与哈希门均通过；未运行后端全量、Playwright、前端或 xdist。

Grok OAuth 可用；命令行需显式继承本机 Clash `HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890`，否则直连 `cli-chat-proxy.grok.com` 可能超时。启动新任务前仍须确认没有同包 Grok 进程。

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
