<!--
模块：V1-O 知识库文档服务端真值契约
用途：冻结知识库文档/文件夹以服务端响应为唯一真值；消灭 local 成功态、假 ID、旧键污染与敏感透传。
对接：useKnowledgeBase、KnowledgeBasePage、既有 /api/knowledge/* 与 P9C 语义索引面板；V1-O E2E。
二次开发：禁止恢复 local 成功态、fld_/kb_ 客户端假 ID、旧 localStorage 键读写删迁；卡片/图片/后端本包冻结。
-->

# V1-O 知识库文档服务端真值契约

> **状态：契约与 failure-first 测试阶段（生产未授权）。**
> **工作树：** `C:\Users\Administrator\biaoshu-v1m-m3-a`，分支 `collab/v1m-m3-a`。
> **冻结基线 HEAD：** `eb64dc1a2fcd2ffa8bee85668f0b99a9ff6c4ffe`。
> **R1 返修授权：** task=`msg_c089ca8bc9ee4d5d98969944fd31c15b`（Q1–Q12）；**R1 重复 Playwright 收集作废**，不得恢复/续跑中断结果。
> **R2 返修授权：** task=`msg_3a048f3b68f14fd5b395f192dc4f8dcf`（关闭 Q1–Q9，test-only 四文件；禁止本代理跑 Playwright）。
> **R5-FINAL 返修授权：** task=`msg_4ad902ba574145568ea219dd665e5ee2`（Q1–Q10 集中 test-only；Q17 synthetic 冲突 NO 不混入；禁止本代理跑 Playwright）。
> **R6-FIX 返修授权：** task=`msg_0d71b9b59aa9438da2467eb9cd8e37f1`（七项确认后最终集中 test-only；依据 Codex Q `msg_c1bfb70021da43fdad97783b018b07a8` + Grok YES×7 `msg_5b5635c73e5f49539fe41c3db278d57a`；禁止本代理跑 Playwright）。
> **Codex failure-first：** 144 项，`134 failed / 10 passed`，29.9 分钟；其中自守卫两处宽 OR 经 question=`msg_65e1f044a2624033b800a572d60788f0`、YES=`msg_d4aaf61379be4dfda5084f88341e19b9`、task=`msg_e8ba8c78024c4f19aaa5c539fe8c9c61` 修复，聚焦复验 `1 passed / 7.2s`；禁止重复完整 failure-first。
> **本阶段可写：** 本契约、实施计划、`frontend/e2e/knowledge-doc-server-truth.spec.ts`、`frontend/e2e/semantic-index.spec.ts`（仅收紧文档失败语义与注释）。生产 hook/page 另授权。

## 1. 问题真值

当前 `useKnowledgeBase` / `KnowledgeBasePage` 在文档主链上仍保留本地演示成功路径，导致页面可显示“像有文档/可写成功”的假真值：

1. **`source: "api" | "local"` 成功双轨。** folders/docs 任一 GET 失败即 `loadLocal()` + mock seed，UI 显示“（文档当前离线本地演示）”，语义面板“本地演示 · 不可构建”，上传/建夹/移动/删除/重试索引仍可本地改内存并写回 `localStorage`。
2. **旧键 `biaoshu.knowledgeBase.docs.v1` 读写。** API 成功时 `saveLocal` 覆盖；local 模式每次 folders/docs 变更再写；失败路径 `loadLocal` 读旧键并与 mock 混用。
3. **客户端假 ID 与假进度。** local 创建文件夹生成 `fld_${Date.now...}`；local 上传生成 `kb_...${Math.random...}`，`setTimeout(800/900)` 把 `parsing`/`indexing` 伪造成 `ready` 并填 chunks。
4. **失败文案与 statusMessage 透传。** create/upload/move/delete/reindex 与主 refresh catch 使用 `err.message`；页面直接展示服务端 `statusMessage` 原文（可含异常详情），可能把路径、detail、code 打进 DOM。
5. **写后未服务端对账。** move 不校验响应 `moved`；批量 delete 中途失败不停止对账；成功路径直接本地 map，可不经 GET 真值。
6. **无 loading|ready|error 主状态机。** 仅有 `hydrated` + source；缺少加载文案、真实空态、主失败互斥。
7. **refresh 无代次围栏。** 并发 refresh / unmount 后迟到 success/catch/finally 可污染列表与 source。
8. **P9C 语义索引** 已禁止 localStorage 伪就绪，但仍以 `source === "api"` 为门；文档主失败被旧测写成“进入 local”绿语义，掩盖文档真值断裂。

后端 `/api/knowledge/folders|docs|upload|move|delete|reindex` 与 workspace 作用域已具备；**本包无需后端改动**。卡片/图片 tab 与 `useKnowledgeCards` 完全冻结。

## 2. 产品裁定（冻结）

### 2.1 文档主状态只允许 `loading | ready | error`

| 状态 | 进入条件 | UI 与行为 |
|---|---|---|
| `loading` | 挂载首次拉取或用户触发 refresh 进行中 | 固定“正在加载知识库文档…”；两列表不展示半结果；**禁止一切写请求** |
| `ready` | **folders 与 docs 两个 GET 均成功且整批结构有效** | 以服务端数组为唯一真值；允许写操作 |
| `error` | 任一 GET 失败、网络失败、非整批合法、对账失败等 | 固定“知识库文档加载失败，请稍后重试”；**清空 folders 与 docs、文档/文件夹选择与语义状态**；**禁止一切写请求** |

- **禁止 `local` 成功态**、禁止 UI “离线本地演示”“本地演示 · 不可构建”作为可操作成功路径。
- **真实文档空态（两者等价）：**
  1. `folders=[]` 且 `docs=[]`；
  2. `folders=[合法收件箱]` 且 `docs=[]`（与后端 `ensure_default_folder` 默认收件箱一致）。
  二者均展示“知识库暂无文档”与说明“上传文档后可在这里查看解析和索引状态。”；零 mock、零旧键 seed。
- 筛选导致列表空：沿用“当前筛选下无文档”；与 loading / 真实空库 / 主 error **互斥**。
- 畸形 200 与 HTTP 失败同等整批 `error`，**零半列表**。

### 2.2 运行时 schema（folder / doc）— GET 与写响应共用

前端对 **GET 列表元素** 与 **create / upload / reindex 写响应体** 使用**同一解析与规范化规范**。任一阵列中**任一项**不合法 → 整批 `error`，**禁止半列表**。

#### 2.2.1 Folder 必需字段

| 字段 | 类型与约束 | null |
|---|---|---|
| `id` | 非空字符串 | 不可 null |
| `name` | 非空字符串 | 不可 null |
| `parentId` | 字符串或 null；null 规范化为 `null` | **可接受 null** |

- 整批 folders：`id` 必须**唯一**。
- `parentId` 非 null 时必须引用同批某个 folder 的 `id`（根级 null 除外）；禁止 self / cycle / orphan。

#### 2.2.2 Doc 必需字段

| 字段 | 类型与约束 | null |
|---|---|---|
| `id` | 非空字符串 | 不可 null |
| `name` | 非空字符串 | 不可 null |
| `tags` | **字符串数组**（元素均为 string；非数组 / 含非 string 非法） | 不可 null；缺省非法 |
| `chunks` | **非负安全整数**（`Number.isSafeInteger(n) && n >= 0`）；负值、小数、非数、布尔非法 | 不可 null |
| `updated` | 非空字符串 | 不可 null |
| `updatedAt` | 非空字符串（ISO 或服务端时间串） | 不可 null |
| `category` | 非空字符串 | 不可 null |
| `folderId` | 非空字符串；必须引用同批（或已 ready 的 folders 真值）中存在的 folder `id` | 不可 null |
| `status` | 枚举：`ready` \| `parsing` \| `indexing` \| `failed` \| `pending` | 不可 null |
| `statusMessage` | 字符串或 null；**前端不得展示原文**（见 2.4） | **可接受 null**，规范化为 `null` |
| `sizeLabel` | 字符串或 null | **可接受 null**，规范化为 `null` |

- 整批 docs：`id` 必须**唯一**。
- `folderId` 悬空（orphan）→ 整批 error。
- 深层坏字段（如 `id: 1`、`name: true`、`tags: [1]`、`chunks: -1`、非法 `status`）→ 整批 error。
- 合法 null 字段规范化后可进入 `ready`（仅指 `parentId` / `statusMessage` / `sizeLabel`）。
- **nullable 缺失与 null 均为合法正例**（`statusMessage` / `sizeLabel` / folder `parentId`）：字段键不存在与显式 `null` 等价合法，规范化为 `null`；**不得**把缺失误判为 schema 失败（含 `folder.parentId` 缺失）。
- **`doc.tags` / `doc.chunks` 缺失非法**（与 null/错型门分离）；folder 坏项与缺 `name` 的 doc 须有**独立 canary** 证明零渲染。
- **nullable 可观测结果（固定）：**
  - `parentId: null`（或缺失）的 folder **只出现在根级 DOM 关系**（树列表直接子项，不得嵌套在其它 folder item 内；禁止仅文字可见冒充根级）；
  - `statusMessage: null`（或缺失）**不**产生字面量 `null` 文本/`title`，对应 `.kb-status-msg` **精确不存在**，pill `title` 空/缺席，只显示 status 固定安全文案；
  - `sizeLabel: null`（或缺失）**不**产生字面量 `null` 文本/`title`，资料列尺寸 `div.mono` **精确不存在**（禁止仅排除字面 null）。

#### 2.2.3 坏 GET 批与写响应接线

- 每个坏 GET 批必须**同时含唯一合法 sentinel 与坏项**；任一坏项 → 整体 error，**二者均零渲染**，旧列表也零残留。
- 只维护**一份**穷举共享 GET schema 矩阵。
- create / upload / reindex 写响应按各端点**单对象 schema** 各增加 3 个独立接线用例（除指定坏点外其余字段全部合法）；畸形 2xx 均展示对应固定操作错误并进入 folders/docs 双 GET 对账，**禁止信任响应半行**。
- 单对象写响应**不适用** GET 批内 duplicate 语义；duplicate 仅在共享 GET 批矩阵覆盖。

### 2.3 旧键族：不读、不写、不删、不迁移、不上传

- 键名：`biaoshu.knowledgeBase.docs.v1` 及同族 `biaoshu.knowledgeBase.docs*`（若存在）。
- 页面运行**不得**因加载/写操作/失败路径对上述键执行 get/set/remove/clear 业务触碰；预置完整键值必须**字节级精确不变**。
- **原因（必须写入实现注释与契约，生产代码必须解释）：** 旧键混有**演示种子**与可能的**历史用户数据**，二者**不可可信自动区分**；自动迁移存在**数据完整性与隐私风险**。正确策略是忽略旧键，仅以服务端为真值；禁止上传旧键内容到服务端。

### 2.4 状态文案与隐私出口

#### 2.4.1 固定脱敏写失败 / 主加载文案

| 操作 | 固定中文（唯一） |
|---|---|
| 创建文件夹 | 创建文件夹失败，请稍后重试 |
| 上传文档 | 文档上传失败，请稍后重试 |
| 移动文档 | 移动文档失败，请稍后重试 |
| 删除文档 | 删除文档失败，请稍后重试 |
| 重新索引 | 重新索引失败，请稍后重试 |
| 主加载失败 | 知识库文档加载失败，请稍后重试 |

#### 2.4.2 statusMessage 映射（有限固定安全文案）

前端**不展示**服务端 `statusMessage` 原文。仅按 `status` 映射有限固定安全文案，例如：

| status | 展示文案（示例，实现可冻结为常量） |
|---|---|
| `ready` | （不展示附加消息或固定“已就绪”） |
| `parsing` | 解析中 |
| `indexing` | 索引中 |
| `failed` | 处理失败 |
| `pending` | 待处理 |

#### 2.4.3 隐私边界（R2 措辞）

- **异常原文**中的敏感 path / key / id **不得进入任何出口**。
- **经 schema 校验的正常服务端资源 ID** 仅允许在协议规定的 reindex / move / delete 的 path / body 位置出现且**必须使用**；其它 URL / query / body / console / DOM / storage / IDB / Cookie **仍禁止**出现未授权敏感值。
- 服务端 `detail`、`code`、异常原文、`statusMessage` 原文不得进入任何出口：DOM 文本、DOM 属性、`title`、console 任意级别、URL/query、request body、localStorage/sessionStorage/IndexedDB、Cookie。

#### 2.4.4 隐私探针与早期 arm（E2E 强制）

1. **早期 arm（同步）：** `addInitScript` 内同步捕获所有原生 Storage / IDB 方法 → 用同步 Storage 原生方法完成可选旧键预置与 baseline snapshot → 同步 `armed=true` → 之后应用脚本才可执行。**禁止** `openKnowledge` 后再 arm；**禁止**依赖异步 `addInitScript` 完成 IDB 预置。
2. **IDB 已知 baseline 固定为空**，不预创建数据库；wrappers 在应用脚本前同步 arm。最低监控 API：`indexedDB.open/deleteDatabase`，database `create/deleteObjectStore`，objectStore `create/deleteIndex`，objectStore `add/put/delete/clear`；每条 touch 记录 api、db/store/index、参数和值；writes 为会改变结构/数据的子集。所有 case 断言 IDB touches/writes **精确 0**。**IDB 终态读取全过程不 disarm**：仅用捕获的原生 `indexedDB.databases`（若存在）读取数据库名/结构快照，**禁止**临时关闭 `armed` 或替换探针引用；快照仍为空且 `armed` 保持 true。
3. **旧 poison 字节值必须原样保留。** 隐私扫描分层：
   - request / console / DOM 历史**全部**扫描；
   - arm 后 Storage / IDB touch 的参数和值**全部**扫描；
   - 最终 Storage / IDB 只扫描**新增或相对 baseline 发生变化**的值；
   - 明确排除“键和值均字节级未变”的已知 baseline，**禁止**删/改旧键来消红。
4. **全出口：**
   - page request 记录所有请求完整 URL/query/body（同源/API/静态/外网）及 **allHeaders**（含 Cookie 请求头）；外网先记录再 abort；`EXTERNAL_ROUTE_CANARY` 与 `SECRET` 继续分离。
   - console 记录所有类型及 `msg.args()` 每个参数可序列化值；无法序列化也记录安全字符串；异步 `jsonValue` promise 入队，**循环 drain 至 pending 稳定**，预算末 **pending 精确为 0**（禁止静默截断）；隐私断言前必须 await 统一 drain barrier。
   - `addInitScript` 安装 MutationObserver，在应用执行前记录 DOM 文本节点与属性新增/变化历史；**observer 与终态读取必须持有同一历史数组引用**，重置只能 `length=0` 清空，禁止替换引用；禁止 synthetic 手工 push 掩盖；隐私断言扫描历史，不能只扫最终可见 DOM。
   - 记录 `document.cookie` getter/setter touches 以及 **browser context.cookies 打开前/终态完整对账**（覆盖**任意名值**与 HttpOnly，**不得**仅筛 `v1o`/`SECRET`）；Cookie 请求头随全 request 扫描。已知 baseline 默认空，最终必须与打开前一致且 touches 精确 0。
5. **synthetic 自证：** baseline 自带 SECRET 且未触碰应通过隐私扫描+原值门；读取、同值 set、删除恢复、迁移到新键/IDB、请求/console/DOM 泄漏任一都红。

### 2.5 写操作：仅 ready + 共享单写锁 + 五类统一对账

五类 mutation：`createFolder` / `upload` / `move` / `delete` / `reindex`。

1. **门：** 仅 `ready` 可写；`loading` / `error` 下 UI 与 hook 函数门双重零写。
2. **共享单写锁：** 任一写进行中禁止并发第二写；UI 禁用/隐藏相关入口。E2E 使用声明期 **first-kind × second-kind** 交叉矩阵：
   - first action 必须先完成真实入口派发并等待 first write route arrived；
   - second 入口：不存在/隐藏 → count=0 或不可见；存在且 disabled → 精确 disabled，不得 force/移除 disabled/合成派发，DOM 事件计数 0；可派发 → 安装 DOM click/input 探针后真实 locator action，事件精确 1 次；
   - 各分支至少两轮 microtask/RAF continuation，second write route 精确 0；释放 first 后等待 fulfilled/requestfailed、双 GET 与 continuation，再次断 second write 始终 0。
   - **写分账阶段（R6）：** `first` / `second-attempt` / `first-drain`；second 尝试阶段任何新增写一律归 second（含同类 diagonal）；仅 multi-delete 已冻结剩余 path 在 drain 阶段可继续归 first；错误第二写必须使 second 断言红。
3. **彻底禁止：** 客户端 `fld_` / `kb_` 假 ID、`Math.random` 业务 ID、本地 create/move/delete/reindex/upload 成功路径、`setTimeout` 假 `parsing`/`indexing`→`ready`。
4. **结束后原子对账：** 无论成功、HTTP 明确失败、abort/网络结果不确定，结束后均执行 **folders + docs 双 GET**；合法实现允许顺序或并发：两条 GET 分别独立 handler/gate 与预先冻结、明显不同的最终态；**每条 arrived 即可独立返回**，不得“两个都 arrived 后统一 release”造成顺序实现死锁。E2E 断言 folders/docs GET **各精确 +1**（不是 `>=`），并在两者 fulfilled + 页面 continuation 后逐字段证明采用 GET 真值。
5. **响应体角色：** 写响应仅作**结构校验**（schema 合法），**不作长期列表真值**；列表真值以对账双 GET 为准。
6. **“批量部分成功”矩阵仅适用于 move / delete**；不得把单项 create / upload / reindex 臆造为部分成功场景。
7. **move `moved`：** 响应体 `moved` 必须为**严格非布尔整数**（`typeof === "number" && Number.isInteger(n) && !Object.is(n, -0)` 等），且**精确等于**请求去重后的 `ids` 数；以下**全部失败**并强制双 GET 对账：`missing` / `null` / `bool` / `string` / 负数 / 小数 / `-0` / `0`（当去重 ids>0）/ 部分 / 超量。仅精确匹配时计成功。对账最终态在动作前即冻结。
8. **批量 delete：** 串行；中途失败停止后续 DELETE；强制双 GET 对账并展示服务端最终态。
9. **create / upload / reindex 成功：** 上传/写成功桩返回与输入**明显不同**的服务端 `id`/`name`/`status`/`tags`/`chunks`/`sizeLabel`/`category`/`folderId`；刷新前后逐字段可见验证；后续 reindex/move/delete 的 path/body 必须使用**服务端 ID**。
10. **first action promise 与业务 outcome 分开：** first action promise 必须显式 await 并断言入口派发成功；HTTP fail/abort **不要求** click/check/selectOption/setInputFiles promise reject，而分别通过 response/requestfailed、固定错误 UI 和双 GET continuation 断言。**禁止** `.then(()=>undefined,()=>undefined)` 或空 catch 吞掉任何 action、route 或 continuation promise。
11. **真实制造重复 ids：** 通过可观测多入口/选择恢复构造重复输入；断请求 ids 首次出现顺序去重。

### 2.6 refresh 代次与 unmount

- refresh 与写后对账绑定 **mounted + 请求代次**。
- 旧 success、HTTP error、abort/network **各独立 case**。
- **folders + docs arrived 都必须可独立观测**（不得只等一侧）。
- **old route 自身 settle 与“新增污染”分计数：** 释放后必须等待旧 `folderGetFulfilled`/`docGetFulfilled` **各自严格大于**释放前基线；但 `folderGetArrived`/`docGetArrived`、write、semantic GET、业务 API 新副作用相对释放前基线**精确 0**。
- **确定 barrier：** 必须绑定浏览器层对应 **response**（HTTP error/success）或 **requestfailed**（abort/network），之后再经业务 catch/finally 可观测 continuation barrier（双 RAF + microtask），再断言。**禁止**仅依赖 route helper fulfilled `> base` + RAF。
- **本轮精确 +1：** 释放后 folders/docs 的 settled 相对释放前基线各自**精确 +1**（不是 `>=` / `>`）；arrived 相对释放前精确 0 增长。
- 释放前记录 **DOM + folders/docs 请求 + semantic + write** 精确基线；释放后只允许旧请求自身完成，禁止新副作用。**禁止**释放/等待后才取基线。
- unmount 同理：释放前取基线，卸载后释放，等待浏览器终态 + 旧 settled 精确 +1 + continuation，再断零污染。**unmount 导航（`page.goto`）请求不得与业务 API 污染混计**；正确实现不得因导航 document 请求必红。

### 2.7 选择清理与 UI 互斥

- `ready` 刷新后清理：不存在的 `selectedFolderId`、`selectedIds` / 批量选择、页面 `moveTarget`。
- 保留至少一个 selectedId；当前 selectedFolderId 与 moveTarget 指向即将消失 folder；刷新删除该 folder 后：
  - `moveTarget` **精确 `""`**（占位 option），禁止残留失效 folder id 或其它现存 folder id；
  - folder tree **精确默认活跃项「全部文档」**（`KB_FOLDER_ALL` / `is-active`，若有 `aria-selected` 亦须一致）；
  - `selectedIds`：失效 id 清除、合法 id 精确保留。
- loading / 真实空态 / 筛选空态 / 主失败互斥；loading/error 下旧列表不残留。
- 删除“离线本地演示”成功暗示。

### 2.8 P9C 语义索引（本包边界）

- 仍仅内存、固定模型 `BAAI/bge-small-zh-v1.5`、失败脱敏、building 轮询。
- **文档主状态非 `ready` 时不可重建**（零 rebuild POST）；不得以“local 演示”绿测掩盖文档失败。
- 卡片/图片完全冻结；`/api/cards` 在 E2E 中必须显式 stub 空数组。
- **未知 `/api/knowledge*` 与外网 fail-closed 的权威证明由新主 spec（`knowledge-doc-server-truth.spec.ts`）承担**；`semantic-index.spec.ts` 原 8 用例不扩改 route 框架，仅修正失真 local 注释与末个文档失败用例。

### 2.9 后端

- 既有 knowledge 路由/服务已具备 workspace 作用域与 CRUD/upload/move/delete/reindex；**本包无需后端改动**。
- E2E 仅用 Playwright route stub 证明前端以服务端响应为唯一真值；**不得声称**验证真实 knowledge API 鉴权/CSRF/SQLite/上传落盘。

## 3. 最小实现协议（生产授权后）

### 3.1 状态机（建议）

```text
loading --(folders OK ∧ docs OK ∧ 结构有效)--> ready
loading --(任一失败/畸形)--> error
ready   --(用户 refresh)--> loading
error   --(用户 refresh)--> loading
ready   --(写 + 对账)--> ready | error（对账失败）
```

- 内存持有 `docStatus`、`folders`、`docs`、选择态、`semantic*`、`errorMessage`（仅固定常量）、`writeLock`。
- 删除 `source: "local"` 成功路径；删除 `loadLocal`/`saveLocal` 对旧键的读写；删除 mock seed 导入路径（或生产路径永不调用）。
- 实现注释必须写明：演示种子与历史用户数据不可可信区分，自动迁移有数据/隐私风险，故旧键旁路。

### 3.2 HTTP 面（前端消费）

| 方法 | 路径 | 约束 |
|---|---|---|
| GET | `/api/knowledge/folders` | 必须数组；元素过 schema；与 docs 双成功才 ready |
| GET | `/api/knowledge/docs` | 必须数组；元素过 schema |
| POST | `/api/knowledge/folders` | 仅 ready；body `{ name }`；响应过 schema；双 GET 对账 |
| POST | `/api/knowledge/docs/upload` | 仅 ready；multipart；响应过 schema；双 GET 对账 |
| POST | `/api/knowledge/docs/move` | 仅 ready；校验 `moved` 严格整数全量；双 GET 对账 |
| DELETE | `/api/knowledge/docs/{id}` | 仅 ready；批量遇首败停并双 GET 对账 |
| POST | `/api/knowledge/docs/{id}/reindex` | 仅 ready；响应过 schema；双 GET 对账 |
| GET/POST | `/api/knowledge/semantic-index*` | 仅文档 ready 后语义面板可操作重建 |

### 3.3 生产白名单候选（后续另授权）

1. `frontend/src/features/knowledge-base/hooks/useKnowledgeBase.ts`
2. `frontend/src/features/knowledge-base/pages/KnowledgeBasePage.tsx`

禁止修改 mock/types 业务语义、package/playwright/vite、后端、卡片/图片、其它 E2E（除本任务已授权的 semantic-index 收紧）、交接路线图、依赖与配置，除非 Codex 新授权。

## 4. failure-first 与反假绿矩阵

新专项 `frontend/e2e/knowledge-doc-server-truth.spec.ts` 使用受控 route 与合成 File；不触碰真实 `biaoshu.db`、业务 uploads、密钥与用户目录。

| 编号 | 场景 | 必须证明 |
|---|---|---|
| A | poisoned 旧键（至少两 docs 同族键 + 一无关）+ 空库 | 真实空态；旧键访问计数 0；完整 key/value 快照不变；probe writes=0；URL/body 无 poison |
| A2 | `[]+[]` 与 `inbox+[]` 两真实空态 | 均空标题/说明；零 mock |
| B | 运行时 schema 畸形矩阵（folder/doc 深层坏字段、duplicate id、orphan、status/tags/chunks/null） | 坏一项整批 error、零半列表；合法 null 可观测规范化 |
| B2 | HTTP/abort/非数组；写响应 3×3 畸形接线 | 主/操作固定错误；secret 全出口扫描；双 GET 对账 |
| C | loading/error 下 create/upload/move/delete/reindex/rebuild | 每入口明确隐藏或禁用；隐藏 file input 直接 setInputFiles；fake timer 推进 >800/900ms 证明零假 ID/ready |
| D | 五类 mutation 成功 / HTTP fail / abort；仅 move/delete 部分成功 | **独立参数化 `test(...)`**（禁止 describe serial、禁止单 test 内循环矩阵）；各证双 GET 精确 +1、单写锁矩阵、固定错误保留 |
| D2 | delete ≥3 文档，第二项失败 | 第三 DELETE 精确 0；双 GET 各 +1；展示服务端最终态 |
| D3 | move `moved` 全矩阵 | missing/null/bool/string/负/小数/-0/0/部分/超量/精确成功；失败对账数据与旧 UI 不同；ids 顺序/去重与 folderId 精确 |
| E | 上传/写成功桩返回与输入不同的服务端字段 | 刷新前后逐字段；后续写 path 用服务端 ID |
| F | refresh 代次：success / HTTP error / abort 分测 + unmount | 释放前基线；arrived/fulfilled/continuation；旧代次四类计数精确 0 |
| G | 选择清理 selectedFolderId/selectedIds/moveTarget；UI 互斥；未知 knowledge + 外网 fail-closed | 主动外网用独立 `EXTERNAL_ROUTE_CANARY`；全出口隐私探针 |
| H | 单一 TypeScript AST `analyzeSpecSource` 自守卫 | 精确跳过自守卫标题回调；synthetic 内存正反表；production-read 门；删 ignoreErrors+空 catch 吞异常 |
| I | `semantic-index.spec.ts` | 原 8 用例不弱化；仅修正 local 失真注释与末用例文档失败断言 |

**禁止：** `test.skip` / `test.fixme` / `xfail`、固定 `waitForTimeout` 作完成证据、宽 route 前缀放行、吞 route 异常、用生产源码扫描冒充运行时、清理旧键后声称“未写未删”、`test.describe.configure({ mode: "serial" })`、单 test 内循环矩阵冒充覆盖、再堆叠第二套探针/AST analyzer。

## 5. 分级验收

严格串行、单 worker、零重试；**禁止并发 Playwright**（“串行”仅指命令行 `--workers=1`）。

### 5.1 R2 / Codex 单次 Playwright 策略

- **Grok R2 本轮明确禁止：** Playwright 命令、`--list`、浏览器、Vite、uvicorn、pytest，以及启动 8010/5174；**不要恢复或继续 R1 中断测试**。
- **唯一代码校验（Grok）：** 一次不启动服务的 TypeScript compiler API 静态 parse（`ts.createSourceFile` + `parseDiagnostics` 为空）；另允许只读 `git diff --check`、`git status --short`、Get-FileHash/Get-Item、进程与端口查询。
- **Codex：** 在静态 PASS 后**独立只跑一次**完整 Playwright（两 spec，`--workers=1 --retries=0`）。R1 重复/中断收集作废。

```powershell
# 仅 Codex 在静态审查 PASS 后执行；Grok R2 不得运行
cd frontend
.\node_modules\.bin\playwright.cmd test e2e/knowledge-doc-server-truth.spec.ts e2e/semantic-index.spec.ts --workers=1 --retries=0
```

- **test-only / 生产未改：** 可收集、无 webServer/fixture/encoding 错、0 skip/xfail/did-not-run；业务断言真实红。报告 passed/failed 与首红；**不得为绿改 production**。
- **生产实现后：** 上述两文件全绿；再由 Codex 授权 lint/build 与范围外回归。
- 禁止本任务跑 cards/auth-rbac/整仓 E2E、真实业务 DB、外网安装。

### 5.2 未运行项（明确）

- Grok R2：Playwright / 浏览器 / webServer / 8010 / 5174 / Vite / uvicorn / pytest。
- 整仓其它 E2E；真实 API 鉴权/CSRF；真实 SQLite/`biaoshu.db`；真实 uploads 落盘；卡片/图片业务；后端改动；production hook/page（未授权）。

## 6. 安全与非目标

- 不读真实 `biaoshu.db`、uploads、Cookie/密钥、无关用户目录；敏感 canary 仅合成。
- 不验证真实鉴权/CSRF/SQLite 落盘/上传磁盘文件。
- 不改卡片/图片、不改后端 knowledge 服务、不引入模型下载、不恢复 local 演示成功路径。
- 不 git add/commit/push/reset/stash/checkout（Grok）；提交仅 Codex 授权后执行。

## 7. 完成记录

### 7.1 本阶段（契约 + failure-first + R1 + R2）

- 原任务：`msg_6697c26f4d9d4038be658e46f1d8511d`。
- R1 返修：`msg_c089ca8bc9ee4d5d98969944fd31c15b`（Q1–Q12）；**R1 Playwright 收集作废**。
- R2 返修：`msg_3a048f3b68f14fd5b395f192dc4f8dcf`（Q1–Q9，静态校验 only）。
- R5-FINAL 返修：`msg_4ad902ba574145568ea219dd665e5ee2`（Q1–Q10 集中 test-only；Q17 NO）。
- R6-FIX 返修：`msg_0d71b9b59aa9438da2467eb9cd8e37f1`（七项：DOM detail+oldValue、refresh/unmount 浏览器终态+精确+1、写阶段分账、根级/空位、hook 身份 poll、GET 逐字段 chunks=88 排 99、moveTarget=""与默认活跃）。
- R7/R8：Hook queue/current 假红与自守卫宽 OR 均经 Codex 举证、Grok YES、最小 test-only 修复；最终主 spec 静态 parse/transpile 为 0，自守卫聚焦 `1 passed`。
- 有效 failure-first：`144 collected / 134 failed / 10 passed`；其中 1 项测试自身自守卫已关闭，完整套件按纪律不重复，剩余业务红交生产实现关闭。
- 可写四文件：本契约、`docs/plans/2026-07-23-v1o-knowledge-doc-server-truth-plan.md`、`frontend/e2e/knowledge-doc-server-truth.spec.ts`、`frontend/e2e/semantic-index.spec.ts`。
- 真实 failure-first 数字与四文件 SHA/bytes 见对应 review_request；生产未改预期业务红。

### 7.2 后续

- Codex 审查测试与契约 → 静态 PASS 后独立单次 Playwright → 合格后 test-only 提交/转移 → 另 task 授权 hook+page 生产实现 → 复跑至绿 → 文档闭环。
