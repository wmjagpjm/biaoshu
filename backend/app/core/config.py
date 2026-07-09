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

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # 本地 MinerU 回传 Token；空字符串表示不校验（保密机默认）
    local_parser_token: str = ""

    def cors_origin_list(self) -> list[str]:
        """用途：将 cors_origins 字符串拆成列表，供 CORSMiddleware 使用。"""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    用途：进程内单例读取 Settings。
    注意：改 .env 后开发热重载会重启进程；单测改 env 必须 cache_clear()。
    """
    return Settings()
