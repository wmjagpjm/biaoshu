# 受控外部资源同步实施计划

> **执行方式**：按本文逐项实现、测试和复审；当前工作区存在未提交改动，本轮不创建提交。

**目标：** 让自托管管理员可从预先配置、签名验证的 HTTPS 资源清单同步全局只读 Markdown 资源，同时不向浏览器开放任意 URL 请求或同步触发入口。

**架构：** 同步源只可由服务端环境变量配置，默认空数组。同步命令固定解析并校验配置源，通过固定 IP 的 HTTPS/TLS 请求读取有上限的 JSON 信封，验签后才在独立的来源、运行审计和条目映射表中更新 `ResourceRow(source=system)`；`ResourceRow` 本身不保存 URL、密钥或同步状态。前端只读取本地资源 API，不参与外网通信。

**技术栈：** FastAPI、SQLAlchemy、SQLite、Python 标准库 `ssl/socket/http.client`、`cryptography` 的 Ed25519、pytest、React。

---

## 冻结边界

- 只接受 `RESOURCE_SYNC_SOURCES` 中的源；配置为空时命令成功退出且不发网络请求。
- 源配置仅含 `id`、`label`、`manifestUrl`、`publicKey`；不含 Token、Cookie、请求头或客户端可写参数。
- URL 必须为 HTTPS、无用户名密码、仅 443 端口、主机精确命中 `RESOURCE_SYNC_ALLOWED_HOSTS`，且所有 DNS 解析结果均为公共 IP。
- 请求禁重定向、禁代理、禁压缩编码、限制超时和响应体大小；连接使用已校验的 IP，但 TLS SNI/证书仍绑定原始主机名，避免 DNS 二次解析导致重绑定。
- 清单是 `{manifest, signature}` JSON 信封；签名校验对象是 `manifest` 的确定性 JSON 字节。只同步 Markdown 字段，不处理附件、图片、外链、HTML、RSS 或网页抓取。
- 同步由 `backend/scripts/sync_resources.py` 在本机管理员环境手动执行；不增加 `POST /api/resources/*sync*`，因为当前个人版无登录鉴权，不能把外网请求能力交给浏览器调用。
- 只新增或更新签名清单仍包含的条目，缺失条目不自动删除，避免发布端短暂故障造成内容丢失。
- 同一来源清单版本不变且内容摘要一致时幂等跳过；版本回退或同版本摘要变化记为失败审计。

## 清单契约

```json
{
  "manifest": {
    "version": 1,
    "resources": [
      {
        "key": "technical-scoring-v1",
        "title": "技术标评分点响应写法",
        "description": "把评分表映射到正文。",
        "category": "写作指南",
        "tags": ["技术标", "评分"],
        "bodyMarkdown": "# 正文",
        "tone": "violet"
      }
    ]
  },
  "signature": "Base64 编码的 Ed25519 签名"
}
```

`version` 是正整数；`key` 仅允许小写字母、数字、`-` 和 `_`，不包含 URL 或路径。签名由发布方私钥生成，应用仅保存公共密钥配置。

## 任务 1：先写同步失败用例

**文件：**

- 修改：`backend/tests/test_resources.py`
- 新建：`backend/tests/test_resource_sync.py`

1. 生成临时 Ed25519 密钥对和已签名清单，使用注入的假获取器验证首次同步创建系统资源、来源、运行审计和条目映射。
2. 再次同步相同版本与摘要，断言不重复创建资源；提高版本并修改正文，断言原资源更新、映射不变。
3. 用错误签名、非法字段、版本回退、同版本不同摘要分别断言资源零写入或保持旧值，且产生失败审计。
4. 覆盖 `http`、IP 字面量、私网/环回 DNS、非白名单主机、非 443 端口、用户名密码 URL、重定向状态、超长体积和非 JSON 内容的拒绝路径。
5. 断言跨 workspace 只能读取同步后的系统资源，且 `PATCH`、`DELETE` 仍为 403；`GET /api/resources/sync-sources` 不泄露 URL、公共密钥或错误细节。

## 任务 2：加入来源、审计与映射持久化模型

**文件：**

- 修改：`backend/app/models/entities.py`
- 修改：`backend/app/models/__init__.py`

1. 新增全局 `ResourceSyncSourceRow`：来源 ID、展示名称、清单 URL、公共密钥指纹、最后成功版本/摘要、最近运行时间和状态。
2. 新增 `ResourceSyncRunRow`：来源外键、开始/结束时间、状态、创建/更新/跳过计数、受限错误码和短错误摘要。
3. 新增 `ResourceSyncItemRow`：来源外键、外部条目键、资源外键、内容摘要和最近出现时间；为 `(source_id, external_key)` 建唯一约束。
4. 不向 `ResourceRow` 新增 URL、密钥、抓取游标或状态字段；旧系统精选不创建映射。
5. 新增表和唯一约束由 SQLAlchemy metadata 创建；本轮不对旧 SQLite 做 ALTER 兼容迁移，所有新增实体/公开入口写齐中文四字段注释。

## 任务 3：实现受控清单校验与同步服务

**文件：**

- 新建：`backend/app/services/resource_sync_service.py`
- 修改：`backend/app/core/config.py`
- 修改：`backend/.env.example`
- 修改：`backend/requirements.txt`

1. 新增来源配置、`RESOURCE_SYNC_ALLOWED_HOSTS`、响应上限与超时配置；默认来源为空，解析异常在命令和读取接口中安全报告而非尝试网络连接。
2. 使用 URL 解析、精确主机白名单、DNS 解析和 `ipaddress.is_global` 进行前置校验；拒绝协议混淆、用户信息、端口绕过、私网、链路本地、保留地址和无解析结果。
3. 使用固定已校验 IP 建立 TLS 连接，携带原始 hostname 的 SNI，禁重定向和压缩，限制长度，读取单一 JSON 响应。
4. 用 Ed25519 和 Base64 严格验证签名，解析白名单字段，复用资源文本、标签和色调校验语义；不渲染 HTML、不接受清单携带 `source`、`workspaceId`、URL 或附件字段。
5. 先验证完整清单和版本摘要，再在同一数据库事务内写入来源、运行、资源和映射；错误时保留失败运行审计，不写半成品资源。

## 任务 4：提供管理员命令和只读状态 API

**文件：**

- 新建：`backend/scripts/sync_resources.py`
- 修改：`backend/app/api/schemas.py`
- 修改：`backend/app/api/resources.py`

1. 命令初始化表结构和系统精选，再按来源逐个同步；输出每个来源的匿名计数和状态，不输出 URL、公共密钥、远端正文或环境变量内容。
2. 仅新增 `GET /api/resources/sync-sources`，返回来源名称、最近运行状态、最近成功时间和统计；不返回配置 URL、公共密钥、错误原文，也不提供 POST 触发端点。
3. 命令成功、某来源失败、无来源配置分别使用可区分退出码，方便本机计划任务调用；不实现应用内定时器或任务队列。

## 任务 5：本地 API 读模型、文档与复审

**文件：**

- 修改：`docs/HANDOFF-next.md`
- 修改：`docs/integration-checklist.md`
- 更新：本文件执行记录

1. 手工验证默认空配置不会产生出站连接；显式配置测试来源时命令能写入系统资源，普通资源页面无需改为远程请求。
2. 运行专项、后端全量测试、前端构建和 `git diff --check`。
3. 将实体、源校验、固定 IP TLS 获取器、事务、命令和 API 差异交给 Grok 做反方审查，优先检查 SSRF/DNS 重绑定、签名对象、事务边界、错误泄露和 SQLite 兼容性；P0/P1 必须修复后再交接。
4. 更新交接基线、注释齐备表、环境变量说明和未完成边界；不提交 `.env`、真实私钥、测试数据库、缓存或任何远端正文样本。

## 明确不做

- 任意 URL 输入、浏览器 fetch、RSS/Atom、网页抓取、附件、图片、HTML 渲染、Token/Cookie/自定义请求头、代理、重定向和应用内定时任务。
- API 触发同步、多人权限、登录、PostgreSQL、Alembic、内容删除、发布端托管、外部标讯抓取。
- 恢复 `mock.ts`、前端本地浏览量计数或对 `ResourceRow` 放宽系统只读约束。

## 执行记录（2026-07-11）

- 已完成独立来源、运行审计与条目映射实体；`ResourceRow` 未增加 URL、密钥、游标或同步状态字段。
- 已完成空默认配置、精确主机白名单、HTTPS/443、公共 IP DNS、固定 IP TLS/SNI、无重定向/压缩、大小/超时限制、Ed25519 验签、严格字段白名单、版本回退/同版本变更拒绝及全批事务写入。
- 已完成管理员命令 `backend/scripts/sync_resources.py` 和脱敏只读状态接口 `GET /api/resources/sync-sources`；默认空配置实测无网络请求。
- 已覆盖首次同步、幂等、更新、签名失败、版本回退、超长签名字段、签名字段静默修剪/截断/去重拒绝、陈旧版本并发写保护、URL/私网 DNS/端口/用户信息绕过、传输响应边界、空配置、系统资源写保护与 API 脱敏，共 25 项专项测试。
- Grok CLI 通过 `127.0.0.1:7890` 代理恢复后完成只读审查：首轮指出 `tags` 静默截断/去重及并发旧版本覆盖新版本两个 P1；Codex 已收紧字段校验、增加条件版本写入并补测试；Grok 二次确认“未发现 P0/P1，上一轮两个 P1 已修复”。剩余 P2：陈旧同步失败会把来源 `last_status` 记为 `failed`，当前语义为最近一次尝试状态。
- 最终验证：`backend\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q` 为 **114 passed**；`frontend npm run build` 通过（仅既有单包体积警告）；`git diff --check` 通过；资源中心前端目录未检出 `fetch(`、`http(s)`、`XMLHttpRequest` 或 `axios`。
