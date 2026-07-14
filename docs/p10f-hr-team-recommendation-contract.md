# P10F 人力项目团队推荐快照契约

## 1. 目标与边界

P10F 在 P10D 最小人员资质素材卡之上，提供当前工作空间内、由严格 `hr` 人工维护的项目团队推荐快照。人力只可从当前工作空间的**有效**资质卡中按顺序挑选成员，绑定到一个技术标项目；严格 `bid_writer` 可在该项目内按需查看一份无备注、无来源卡 ID 的只读展示投影。

这是一份人工维护的静态快照，不是 AI 匹配、评分、自动补位或实时人员状态同步。资质卡在快照创建后被编辑、停用或过期，不会自动改变已保存的推荐；人力必须再次保存才会替换该项目快照。

明确不做人员业绩、身份证件校验、联系方式、附件、简历、项目角色自由文本、评分/排序算法、审批/发布流、导出、共享链接、跨工作空间搜索、跨项目汇总、Word 自动写入、项目内容读取、删除历史或自动从资质卡推演团队。

## 2. 权限与项目白名单

| 场景 | 结果 |
|---|---|
| `AUTH_MODE=required` 且当前活动成员角色严格为 `hr` | 可调用 `/api/hr/team-recommendations*`，仅操作当前空间 |
| required 未登录 | 认证中间件返回 `401 auth_required` |
| disabled、所有者隐式绕过、`bid_writer`、`finance`、`bidder` | HR 专用接口统一 `403 role_forbidden` |
| HR 指定非成员 `X-Workspace-Id` | `403 workspace_forbidden` |
| `AUTH_MODE=required` 且当前活动成员角色严格为 `bid_writer` 读取本空间单个技术标项目的展示投影 | 允许 `GET /api/projects/{projectId}/team-recommendation` |
| disabled、当前成员角色不是 `bid_writer`（包括仅有 `is_owner`）或非成员读取上述项目投影 | disabled 与角色问题为 `403 role_forbidden`；非成员 `X-Workspace-Id` 为 `403 workspace_forbidden`。`is_owner` 不能替代角色；若成员角色本身精确为 `bid_writer`，则按上一行允许 |

HR **不得**调用既有 `/api/projects*`。新增 HR 项目选择器只返回当前空间技术标项目的 `id` 与 `name`，不返回行业、状态、步骤、字数、关联项目、标讯、正文、文件、编辑态或任务。技术标项目不存在、跨空间或非技术标时，HR 写/读详情统一返回 `404 hr_team_project_not_found`。

## 3. 数据最小化与快照规则

新增 `hr_team_recommendations`（每个 `workspace_id + project_id` 至多一份）及其成员快照行。服务端生成推荐 ID（`htr_*`）、时间戳和创建/最近更新人；客户端不能传入这些字段。

成员输入只允许 `memberCardIds` 数组，保留数组顺序，长度为 0–30；本包的“有效”仅指 P10D `isActive=true`，`validUntil` 继续只是展示日期、不自动判过期。非字符串、空值、重复 ID、无效/跨空间/已停用资质卡均统一拒绝为 `422 invalid_hr_team_recommendation`。空数组表示清空该项目已推荐成员，但保留推荐记录与审计链，不产生物理删除。

每一成员行只快照下列 P10D 摘要字段：`personName`、`category`、`credentialName`、`level`、`validUntil`、显示顺序和内部 `sourceCardId`。不复制 `remark`、创建人、工作空间、联系方式、证件号、附件或任何未列字段。

HR 详情可使用内部 `sourceCardId` 预选本次编辑；标书制作者展示投影绝不返回推荐 ID、来源卡 ID、备注、创建/更新人或工作空间。所有成功读写响应固定 `Cache-Control: no-store`；浏览器不得写 `localStorage` 或 `sessionStorage`。

## 4. 接口与投影

### HR 专用（全部依赖 `require_hr`）

| 方法 | 路径 | 成功 | 响应范围 |
|---|---|---|---|
| GET | `/api/hr/team-recommendations/projects` | 200 | 仅技术标 `id`、`name` 选择器 |
| GET | `/api/hr/team-recommendations` | 200 | 当前空间推荐摘要：`projectId`、`projectName`、`memberCount`、`updatedAt` |
| GET | `/api/hr/team-recommendations/{projectId}` | 200 | 当前项目的编辑详情；成员含 `sourceCardId` 和快照摘要字段 |
| PUT | `/api/hr/team-recommendations/{projectId}` | 201 首建 / 200 替换 | 仅接受 `{ "memberCardIds": [...] }`；既有 CSRF 必须通过 |

无推荐记录的 HR 详情返回 `404 hr_team_recommendation_not_found`；跨空间/不存在/非技术标项目仍保持 `404 hr_team_project_not_found`，不得混淆为已有推荐。

### 标书制作者项目内只读投影

`GET /api/projects/{projectId}/team-recommendation` 依赖新增的严格标书制作者读取依赖，仅用于当前工作空间技术标项目。响应固定为：

```json
{
  "dataState": "empty | ready",
  "members": [
    {
      "order": 1,
      "personName": "协作显示名",
      "category": "professional | safety | performance | other",
      "credentialName": "资质名称",
      "level": "",
      "validUntil": "2027-12-31"
    }
  ],
  "updatedAt": "2026-07-14T00:00:00Z | null"
}
```

无推荐或已清空均返回 `200`、`dataState=empty`、空 `members` 与 `updatedAt=null`；不得以 404 泄露推荐是否存在。项目跨空间、不存在或非技术标统一使用既有项目不可访问 404。此接口绝不返回项目名称、项目其他字段、资质卡 ID、备注、人力操作者、财务、文件或正文。

写接口采用手工 JSON 对象读取与 `extra=forbid`；非法 JSON、非对象、额外键或任何字段不合规时固定 `422 invalid_hr_team_recommendation`，detail 为固定中文，不回显原始输入、卡 ID 或数据库异常。

## 5. 审计、前端与验收边界

HR 首建/替换（包括空数组清空）分别记录 `hr_team_recommendation_create`、`hr_team_recommendation_update`；标书制作者成功读取 ready 推荐记录 `bid_writer_team_recommendation_read`。审计 target 仅可为 `htr_*`；不得记录姓名、资质、项目名、项目 ID、卡 ID、成员数量、请求体或原始输入。

前端新增严格 HR 的「团队推荐」入口：先请求 HR 项目选择器和资质卡**摘要**，选择项目后才请求推荐详情；提交后重读摘要与详情，不乐观拼接。技术标工作区的标书制作者展示必须按用户动作按需读取单项目投影，错误只显示固定中文，不触发 `/hr/*`、完整 `/projects*`、编辑态、财务、文件或外网请求。

独立验收至少覆盖：角色/未登录/disabled/跨空间矩阵、HR 项目白名单、仅有效卡可选、重复/额外键/非对象拒绝、0–30 边界、一次保存后的快照不随资质卡变化、项目投影最小字段与 empty 语义、`no-store`、CSRF、审计脱敏、浏览器存储为零，以及 HR 和标书制作者网络白名单。

**验收记录（2026-07-14）**：计划=`12e067f`、后端=`3dc600a`、前端=`254f8c7` 已推送至协作分支。Codex 已独立通过后端全量 364 项、前端 `lint` / `build`、P10F 定向 E2E 4 项及前端全量 E2E；仅保留既有 Starlette/httpx 弃用警告与 Vite 大包体积提示。
