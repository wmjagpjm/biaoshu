"""
模块：应用配置
用途：集中读取运行时配置（环境变量 / .env），供数据库、CORS、默认 workspace 使用。
对接：pydantic-settings；变量名见 backend/.env.example
二次开发：
  - 新增配置项写在 Settings 类字段，并同步更新 .env.example 中文说明
  - 禁止在本文件或默认值中写死 LLM API Key / sk-
  - get_settings 带缓存；测试改环境变量后需 get_settings.cache_clear()
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# P10A：AUTH_MODE 白名单；未知值必须在配置加载时拒绝，禁止静默降级为 disabled
_AUTH_MODE_ALLOWED = frozenset({"disabled", "required"})


class Settings(BaseSettings):
    """
    用途：强类型配置模型。
    字段均可通过同名环境变量覆盖（大小写不敏感，如 DATABASE_URL）。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 服务标识，出现在 /api/health
    app_name: str = "biaoshu-backend"
    # SQLite 默认路径相对「启动时的工作目录」；测试可用 sqlite:///:memory:
    database_url: str = "sqlite:///./data/biaoshu.db"
    # 个人版单用户：固定默认工作空间（后续可改为登录用户所属 space）
    default_workspace_id: str = "ws_local"
    default_workspace_name: str = "我的工作空间"
    default_owner_user_id: str = "user_local"
    # 逗号分隔的前端 Origin 白名单
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    # 上传文件根目录（相对启动工作目录）
    upload_dir: str = "./uploads"
    # 单文件上限（字节），默认 50MB
    max_upload_bytes: int = 50 * 1024 * 1024
    # 正文图片独立上限：避免大图占满导出内存或污染招标源文件上传契约
    max_image_upload_bytes: int = 5 * 1024 * 1024
    max_image_pixels: int = 8192 * 8192
    max_project_images: int = 50
    # 标讯离线导入仅在请求内存解析；限制文件与行数，避免本地误选大文件拖垮服务
    max_opportunity_import_bytes: int = 2 * 1024 * 1024
    max_opportunity_import_rows: int = 2000
    # 国能 e 招计划追踪：均为服务端固定上限，前端不得传入或覆盖。
    max_opportunity_watch_import_bytes: int = 2 * 1024 * 1024
    max_opportunity_watch_plan_rows: int = 120
    max_opportunity_watch_plans_per_sync: int = 120
    max_opportunity_watch_candidates_per_plan: int = 5
    max_opportunity_watch_detail_pages_per_sync: int = 50
    opportunity_watch_min_interval_seconds: float = 1.0
    opportunity_watch_connect_timeout_seconds: float = 5.0
    opportunity_watch_read_timeout_seconds: float = 15.0
    opportunity_watch_search_retry_count: int = 1
    # 受控资源同步：来源仅由服务端 JSON 配置；默认空数组，浏览器不接收 URL 或同步密钥
    resource_sync_sources: str = "[]"
    resource_sync_allowed_hosts: str = ""
    resource_sync_max_bytes: int = 1024 * 1024
    resource_sync_timeout_seconds: int = 10
    # 仅本地演示时写入示例标讯；默认关闭，避免污染真实空工作空间
    seed_sample_opportunities: bool = False
    # 本地 MinerU 回传 Token；空字符串表示不校验（保密机默认）
    local_parser_token: str = ""
    # P9C 离线语义索引：模型/维度/缓存/磁盘下限均由服务端固定，禁止 API/前端传入
    semantic_model_id: str = "BAAI/bge-small-zh-v1.5"
    semantic_embedding_dim: int = 512
    # data 下固定子目录名；真实路径必须由 resolve_semantic_model_cache_dir 从 upload_dir 推导
    # 禁止依赖进程 cwd，禁止 HTTP/前端/工作空间设置覆盖
    semantic_model_cache_dir: str = "semantic-models"
    # 重建前最低可用磁盘（字节），默认 5 GiB
    semantic_min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024
    # P10A 本机身份：仅允许 disabled（默认，个人版兼容）或 required（强制会话与成员校验）
    # 大小写不敏感；任何未知值在配置加载时拒绝，绝不可静默按 disabled 运行
    auth_mode: str = "disabled"
    # 会话有效期（小时）；仅服务端配置，禁止由请求覆盖
    auth_session_ttl_hours: int = 72
    # Cookie 名与 Secure 标记；Path 固定 /api，SameSite 固定 Strict
    auth_cookie_name: str = "biaoshu_session"
    auth_cookie_secure: bool = False
    # 变更请求 CSRF 头名；原始值仅存浏览器内存，库内仅 SHA-256 摘要
    auth_csrf_header_name: str = "X-CSRF-Token"

    @field_validator("auth_mode", mode="before")
    @classmethod
    def validate_auth_mode(cls, value: object) -> str:
        """
        用途：在应用启动/配置加载时校验 AUTH_MODE。
        规则：仅 disabled|required（去空白、大小写不敏感）；非法值抛 ValidationError。
        """
        if value is None:
            raise ValueError("AUTH_MODE 仅允许 disabled 或 required，不能为空")
        normalized = str(value).strip().lower()
        if normalized not in _AUTH_MODE_ALLOWED:
            raise ValueError(
                f"AUTH_MODE 仅允许 disabled 或 required，当前值非法: {value!r}"
            )
        return normalized

    def cors_origin_list(self) -> list[str]:
        """用途：将 cors_origins 字符串拆成列表，供 CORSMiddleware 使用。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def is_auth_required(self) -> bool:
        """
        用途：是否启用强制本机会话。
        规则：auth_mode 经校验后仅为 disabled|required；仅 required 为真。
        """
        return self.auth_mode == "required"

    def resource_sync_allowed_host_set(self) -> set[str]:
        """
        用途：归一化受控同步的精确主机白名单，空集合表示所有同步源均不可连接。
        对接：resource_sync_service.parse_configured_sources；RESOURCE_SYNC_ALLOWED_HOSTS。
        二次开发：仅接受纯主机名；不得支持通配符、CIDR、端口或由客户端传入的主机。
        """
        return {
            host.strip().lower().rstrip(".")
            for host in self.resource_sync_allowed_hosts.split(",")
            if host.strip()
        }


@lru_cache
def get_settings() -> Settings:
    """
    用途：进程内单例读取 Settings。
    注意：改 .env 后开发热重载会重启进程；单测改 env 必须 cache_clear()。
    """
    return Settings()


def resolve_semantic_model_cache_dir(settings: Settings | None = None) -> Path:
    """
    用途：解析 P9C 离线模型固定缓存目录。
    规则：与 knowledge_service._kb_root 同根——upload_dir 父目录 / data / <子目录名>；
    不依赖进程启动工作目录；不可由 HTTP/前端/工作空间设置传入。
    对接：embedding_service.OfflineBgeEmbedder。
    """
    s = settings or get_settings()
    # 仅取末段目录名，防止配置写成绝对路径或 ../ 逃逸
    raw = (s.semantic_model_cache_dir or "semantic-models").strip().replace("\\", "/")
    name = Path(raw.rstrip("/")).name
    if not name or name in {".", ".."}:
        name = "semantic-models"
    return Path(s.upload_dir).resolve().parent / "data" / name
