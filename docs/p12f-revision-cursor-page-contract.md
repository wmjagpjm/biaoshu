# P12F-B 修订历史后端游标页契约

模块：P12F-B editor-state 修订历史只读键集分页
用途：在 P12F-A 最多 20 条/20 MiB 有界保留上，提供默认最近 10 条之外的只读访问基础，同时保持 P12C-C1 旧列表合同不变。
对接：`editor_state_revision_history_service`、`api.editor_state_revisions`、`api.schemas`；P12F-C 前端加载更多只可消费本包新路由。
状态：2026-07-17 已完成；冻结=`4ddd896`、实现=`c84a94d`，Codex 已独立验收并推送。

## 1. 分包与兼容边界

P12F-B 只交付后端游标页基础。既有：

```text
GET /api/projects/{projectId}/editor-state-revisions
```

必须继续：

- 无分页语义，固定最近 10 条；
- 成功体顶层精确只有 `items`；
- 既有未知 `limit/offset/cursor/source/search/q` 查询参数仍不得改变结果；
- 既有前端严格 parser、详情、恢复、当前对比和正文差异全部不变。

本包新增：

```text
GET /api/projects/{projectId}/editor-state-revisions/page
GET /api/projects/{projectId}/editor-state-revisions/page?cursor={opaqueCursor}
```

第一条读取第一页；第二条读取游标后的下一页。P12F-C 前端加载更多尚未实现，不得修改任何前端或 E2E。

## 2. 新路由与响应

新路由只接受一个可选 `cursor` 查询参数，页大小服务端固定为 **10**；不得接受客户端 `limit/offset/page/source/search/q` 改变排序、上限或来源全集。成功体精确：

```json
{
  "items": [],
  "nextCursor": null
}
```

- `items` 复用既有 `EditorStateRevisionMetaOut` 五键，最多 10 条，不含 `snapshot`；
- `nextCursor` 只能是 `null` 或非空版本化不透明字符串；有第 11 条时取本页第 10 条的排序位置生成，恰好 0～10 条时必须为 `null`；
- 顶层不得增加 `total/hasMore/limit/offset/page/projectId/workspaceId`；
- 成功与所有业务错误固定 `Cache-Control: no-store`。

项目不存在沿用固定 404。游标非法固定：

```json
{
  "detail": {
    "code": "editor_state_revision_cursor_invalid",
    "message": "修订分页游标无效"
  }
}
```

HTTP 状态为 **400**；不得反射游标、ID、时间、SQL、路径或异常原文。

## 3. 游标与键集算法

固定排序仍为：

```text
created_at DESC, id DESC
```

固定页大小：

```text
REVISION_PAGE_SIZE = 10
```

游标必须：

- 前缀固定 `esrc1_`，完整长度不超过 192；
- 正文为无填充 base64url 的规范紧凑 JSON，只含 UTC 微秒时间位置和合法 `esr_` 修订 ID；
- 解码后严格校验类型、精确键集、时间范围、ID 格式，并通过重新编码全等拒绝非规范变体；
- 只是排序位置而不是授权凭据，不含正文、版本、来源、workspace、密钥或用户信息；跨项目复用仍只能查询 URL 中已鉴权项目，绝不能读取原项目数据。

带游标查询必须使用键集谓词：

```text
created_at < cursor.created_at
OR (created_at = cursor.created_at AND id < cursor.id)
```

禁止主动/非零 `OFFSET`、总数查询、全表扫描、随机排序或 Python 侧先加载全部再切片。SQLAlchemy SQLite 方言可把单纯 `.limit(11)` 编译为 `LIMIT ? OFFSET ?`，但 OFFSET 绑定必须恒为 0，源码不得调用 `.offset(`；该方言占位不视为偏移分页。

## 4. SQL、校验与只读安全

分页查询必须：

1. 先以 `Project.id + workspace_id/project_id` 验证项目存在；
2. 只投影 `id/state_version/snapshot_bytes/source_kind/created_at`，绝不投影 `snapshot_json`；
3. 同时限定 workspace/project，按固定双键降序，SQL `LIMIT 11`；
4. 完整物化并用既有 `_validate_meta_fields` 校验最多 11 行，lookahead 行损坏也必须整页固定 corrupt，禁止先返回部分结果；
5. 返回前 10 行；仅在第 11 行存在时生成 `nextCursor`；
6. 全程只读，禁止 commit/rollback/flush/refresh、锁、审计、当前 editor-state、检查点、修订写入或裁剪。

同一游标重复请求必须得到同一有序 ID 序列（数据库未变化前提）。`created_at` 并列必须由 `id DESC` 稳定分界，不重不漏。

## 5. 四文件白名单

Grok 只允许修改：

1. `backend/app/services/editor_state_revision_history_service.py`
2. `backend/app/api/editor_state_revisions.py`
3. `backend/app/api/schemas.py`
4. `backend/tests/test_p12f_revision_cursor_page.py`（新建）

禁止修改模型、数据库、迁移、主应用注册、写入/恢复/对比服务、既有测试、前端、E2E、依赖、配置或其他文档；不得 `git add/commit/push`。

## 6. Failure-first 与验收门

Grok 必须先只新建测试得到真实业务红测，再修改三个生产文件。红测至少证明新路由当前为 404，而非收集、导入、fixture、依赖或语法错误。

最终测试必须覆盖：

- 0/1/10/11/20 条边界；第一页/第二页精确不重不漏，20 条时两页各 10；
- `created_at` 并列时 `id DESC` 稳定分页，同一游标重复确定；
- 旧列表仍顶层仅 `items`、最多 10 条，未知查询参数仍不改变结果；
- 新页顶层精确 `items/nextCursor`，未知 `limit/offset/page/source/search/q` 不改变固定页；
- 游标空白、超长、错前缀、坏 base64、坏 JSON、额外/缺失键、布尔/越界时间、坏 ID、非规范编码均固定 400 且不反射输入；
- 跨项目/跨 workspace 零泄漏；项目 404 优先级固定；
- SELECT 五列无正文、LIMIT 11、键集谓词、无主动/非零 OFFSET 且无 COUNT，lookahead 损坏整页 corrupt；
- GET 前后 editor-state、project、revision、checkpoint、audit 五域完全不变；
- `py_compile`、`git diff --check`、精确四文件和空暂存区。

Codex 独立运行新专项、既有 P12C-C1/P12F-A 受影响回归和后端全量后才可提交。P12F-C 前端加载更多必须在本包完成后另行冻结。

## 7. 实际实现与验收闭环

Grok 原任务=`msg_b044740a30cc4e82ac4c98c4c42731c4`。生产三文件修改前，新专项真实得到 **27 failed / 3 passed**：静态 `/page` 尚未注册并被动态 `/{revision_id}` 吞为旧 `editor_state_revision_not_found`，页大小常量也不存在；不是收集、导入、fixture、依赖或语法假红。首版实现后专项 **30 passed**，review_request=`msg_5df53113b2894ea984694c8d21d15601`。

Codex 审查发现三类必须关闭的问题：Windows `datetime.fromtimestamp` 对合法最大年份的平台范围风险；编码端未拒绝 pre-1970 存量位置，可能生成解码器必拒的 `nextCursor`；lookahead 损坏测试末尾含已由状态码保证的恒真 `or`。返修任务=`msg_628cbdef5bf24ac09f4f08d676f79d25`，返修 review_request=`msg_6a45abaf4cc141d7bcf066c809b7a11f`。最终转换使用固定 UTC epoch + `timedelta(microseconds=us)`；编码端严格校验 revision ID 与时间闭区间，非法存量位置固定 corrupt；测试覆盖 MIN/MAX 往返、MAX+1 固定 400、pre-1970 第十条有 lookahead 时整页 corrupt，以及固定错误体无部分 items/游标/ID/正文。

Codex 独立结果：新专项 **34 passed**，P12C-C1/P12F-A/P12D/P12E 受影响 7 文件 **171 passed**，后端串行全量 **905 passed**；每组仅 1 条既有 Starlette/httpx 弃用告警。`py_compile`、`git diff --check`、精确四文件白名单和空暂存区通过，验收回执=`msg_6163277b22da433a8ae672560eeec3b5`。P12F-C 前端加载更多、搜索、筛选、删除、total/hasMore、跨项目历史和多人协作仍未实现。
