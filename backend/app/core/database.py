"""
模块：数据库引擎与会话
用途：初始化 SQLAlchemy 引擎、声明基类 Base、提供请求级 Session（get_db）。
对接：
  - main.lifespan → create_all
  - api.deps / 路由 Depends(get_db)
二次开发：
  - 升 PostgreSQL：改 DATABASE_URL，去掉 SQLite 专用 connect_args / StaticPool
  - 新表：在 models 继承 Base，启动时自动 create_all（生产建议 Alembic 迁移）
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    """用途：所有 ORM 实体的公共基类，元数据集中在此。"""


def _ensure_sqlite_dir(url: str) -> None:
    """
    用途：文件型 SQLite 启动前创建父目录（如 ./data/）。
    内存库（:memory:）跳过。
    """
    if not url.startswith("sqlite:///"):
        return
    raw = url.removeprefix("sqlite:///")
    if raw == ":memory:" or raw.startswith(":memory:"):
        return
    # 相对路径或 Windows 非盘符绝对路径
    if raw.startswith("./") or (not raw.startswith("/") and ":" not in raw[:2]):
        path = Path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine():
    """
    用途：按配置创建 Engine。
    SQLite：关闭 check_same_thread；内存库用 StaticPool 保证同库可见。
    """
    settings = get_settings()
    _ensure_sqlite_dir(settings.database_url)
    connect_args = {}
    engine_kwargs: dict = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # 内存库若不用 StaticPool，每个连接是空库，测试/seed 会失败
        if ":memory:" in settings.database_url:
            engine_kwargs["poolclass"] = StaticPool
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        **engine_kwargs,
    )

    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
            # 用途：启用外键（SQLite 默认关闭）
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


# 模块加载时创建，全进程共用
engine = create_db_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """
    用途：FastAPI 依赖注入，每个请求一个 Session，结束时关闭。
    对接：路由参数 db: Annotated[Session, Depends(get_db)]
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_columns() -> None:
    """
    用途：SQLite 个人版轻量加列（create_all 不会改已有表）。
    对接：main.lifespan 启动时调用；列已存在则忽略。
    """
    statements = [
        "ALTER TABLE project_editor_states ADD COLUMN analysis_json TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN parsed_markdown TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN payload_json TEXT",
        "ALTER TABLE project_tasks ADD COLUMN payload_json TEXT",
        "ALTER TABLE workspace_settings ADD COLUMN export_format_json TEXT",
    ]
    # payload_json 在 tasks 表；editor_states 无 payload — 去掉错误那行
    statements = [
        "ALTER TABLE project_editor_states ADD COLUMN analysis_json TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN parsed_markdown TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN business_json TEXT",
        "ALTER TABLE project_tasks ADD COLUMN payload_json TEXT",
        "ALTER TABLE workspace_settings ADD COLUMN export_format_json TEXT",
        "ALTER TABLE projects ADD COLUMN kind VARCHAR(32) DEFAULT 'technical'",
        "ALTER TABLE projects ADD COLUMN linked_project_id VARCHAR(64)",
    ]
    with engine.begin() as conn:
        for sql in statements:
            try:
                conn.exec_driver_sql(sql)
            except Exception:
                # 列已存在或其它可忽略错误
                pass
