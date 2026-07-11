# 受控资源同步清单协议

本协议用于将可信发布方维护的少量 Markdown 资源同步到 biaoshu 的“系统精选”。它不是网页抓取、RSS、附件下载或标讯来源协议。

## 管理员配置

在 `backend/.env` 配置来源。默认不配置任何来源；空数组时运行同步命令不会产生网络请求。

```env
RESOURCE_SYNC_SOURCES=[{"id":"official-guides","label":"官方写作指南","manifestUrl":"https://publisher.example/resources.json","publicKey":"Base64Ed25519公钥"}]
RESOURCE_SYNC_ALLOWED_HOSTS=publisher.example
RESOURCE_SYNC_MAX_BYTES=1048576
RESOURCE_SYNC_TIMEOUT_SECONDS=10
```

- `id`：小写字母、数字、`-`、`_` 组成的稳定来源标识。
- `label`：资源中心显示的来源名称。
- `manifestUrl`：仅 HTTPS、仅 443 端口、无用户名密码的固定清单地址。
- `publicKey`：发布方 Ed25519 **公钥**的 Base64 编码。私钥、Token、Cookie 和自定义请求头不属于应用配置，也不得写入仓库。
- `RESOURCE_SYNC_ALLOWED_HOSTS`：逗号分隔的精确主机白名单，必须包含每个清单主机名；不支持通配符、CIDR 或端口。

## 清单格式

发布方返回 `application/json`，根对象必须恰好包含 `manifest` 与 `signature`：

```json
{
  "manifest": {
    "version": 1,
    "resources": [
      {
        "key": "technical-scoring-v1",
        "title": "技术标评分点响应写法",
        "description": "将评分表映射到技术方案正文。",
        "category": "写作指南",
        "tags": ["技术标", "评分"],
        "bodyMarkdown": "# 评分点\n\n逐条响应。",
        "tone": "violet"
      }
    ]
  },
  "signature": "Base64 编码的 Ed25519 签名"
}
```

`manifest` 使用 UTF-8、`ensure_ascii=false`、键排序、无多余空白的 JSON 字节进行 Ed25519 签名。发布方签名时应等价于：

```python
canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
signature = private_key.sign(canonical)
```

资源条目只允许 `key`、`title`、`description`、`category`、`tags`、`bodyMarkdown`、`tone`；不允许 URL、HTML、附件、图片、`source`、`workspaceId` 或未知字段。已签名字段必须就是最终入库内容：除 `bodyMarkdown` 保留 Markdown 原文外，其余字符串不允许依赖本地 trim；`tags` 最多 20 个，且不得为空、重复或需要修剪。`version` 必须为正整数，不能回退；同一版本不得更换内容。

## 运行

在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe scripts\sync_resources.py
```

命令逐个同步已配置来源，单个失败不阻塞其它来源。来源地址、公共密钥、远端正文和底层错误不会输出到命令行、API 或审计记录。

同步请求仅使用固定 DNS 解析出的公共 IP 建立 TLS 连接，SNI 与证书校验仍使用白名单主机；不跟随重定向，不接受压缩编码，且受大小和超时限制。清单缺失条目不会自动删除已有资源。
