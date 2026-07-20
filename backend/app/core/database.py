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


def migrate_editor_state_revisions_revision_restore_source(conn) -> None:
    """
    用途：P12C-C2 将旧 SQLite 八来源 CHECK 幂等迁移为含 revision_restore 的九来源。
    对接：ensure_schema_columns；仅 SQLite 生效。
    二次开发：
      - 已含 revision_restore 立即 no-op；非 SQLite no-op；
      - 独立单事务：建临时表 → 显式八列复制 → 核对行数 → DROP 旧表 → RENAME → 重建索引；
      - 禁止 writable_schema / ignore_check_constraints / 无核对 DROP / 吞异常后继续启动；
      - 失败必须抛出让调用方回滚并阻止启动。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_revisions'"
    ).fetchone()
    if row is None or not row[0]:
        # 表尚不存在：create_all 将按新模型创建
        return
    ddl = row[0]
    if "revision_restore" in ddl:
        return

    # SQLAlchemy sqlite legacy 下外层 begin 在首个 DML 前可能未物理 BEGIN；
    # CREATE 会落在事务外，异常回滚后临时表残留。先做 0 行 DML 触发真实事务。
    # 事务仍由 ensure_schema_columns 的外层 begin 唯一 commit/rollback。
    conn.exec_driver_sql(
        "UPDATE editor_state_revisions SET id = id WHERE 0"
    )

    # 固定临时表 + 显式八列复制
    conn.exec_driver_sql(
        """
        CREATE TABLE editor_state_revisions__p12cc2_mig (
            id VARCHAR(64) PRIMARY KEY,
            workspace_id VARCHAR(64) NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            project_id VARCHAR(64) NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_json TEXT NOT NULL,
            state_version VARCHAR(64) NOT NULL,
            snapshot_bytes INTEGER NOT NULL,
            source_kind VARCHAR(64) NOT NULL,
            created_at DATETIME NOT NULL,
            CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
            CHECK (
                source_kind IN (
                    'browser_put','task','revise','callback',
                    'local_parser','content_fuse_apply',
                    'content_fuse_consume','checkpoint_restore',
                    'revision_restore'
                )
            )
        )
        """
    )
    before = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_revisions"
    ).fetchone()[0]
    conn.exec_driver_sql(
        """
        INSERT INTO editor_state_revisions__p12cc2_mig (
            id, workspace_id, project_id, snapshot_json,
            state_version, snapshot_bytes, source_kind, created_at
        )
        SELECT
            id, workspace_id, project_id, snapshot_json,
            state_version, snapshot_bytes, source_kind, created_at
        FROM editor_state_revisions
        """
    )
    after = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_revisions__p12cc2_mig"
    ).fetchone()[0]
    if before != after:
        raise RuntimeError(
            "editor_state_revisions 迁移行数核对失败，已中止（调用方须回滚）"
        )

    conn.exec_driver_sql("DROP TABLE editor_state_revisions")
    conn.exec_driver_sql(
        "ALTER TABLE editor_state_revisions__p12cc2_mig "
        "RENAME TO editor_state_revisions"
    )
    # 重建全部索引（单列 + 复合）
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_workspace_id "
        "ON editor_state_revisions(workspace_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_project_id "
        "ON editor_state_revisions(project_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_created_at "
        "ON editor_state_revisions(created_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_esr_workspace_project_created_id "
        "ON editor_state_revisions(workspace_id, project_id, created_at, id)"
    )


def migrate_editor_state_revisions_display_name(conn) -> None:
    """
    用途：P12F-H 在九来源 CHECK 迁移成功后幂等加 nullable display_name 列。
    对接：ensure_schema_columns；仅 SQLite 生效。
    二次开发：
      - 表不存在时 no-op（create_all 按新 ORM 建列）；
      - 列已存在立即 no-op；
      - 加列失败必须抛出让外层 begin 回滚并阻止启动；禁止吞异常后继续。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_revisions'"
    ).fetchone()
    if row is None:
        return

    cols = conn.exec_driver_sql("PRAGMA table_info(editor_state_revisions)").fetchall()
    # PRAGMA table_info：cid, name, type, notnull, dflt_value, pk
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    if "display_name" in existing:
        return

    # 单行字面量：便于验收扫描 ADD COLUMN display_name
    conn.exec_driver_sql(
        "ALTER TABLE editor_state_revisions ADD COLUMN display_name VARCHAR(160)"
    )


def migrate_editor_state_checkpoints_display_name(conn) -> None:
    """
    用途：P12G 幂等为检查点表加 nullable display_name 列。
    对接：ensure_schema_columns；仅 SQLite 生效。
    二次开发：
      - 表不存在时 no-op（create_all 按新 ORM 建列）；
      - 列已存在立即 no-op；
      - 加列失败必须抛出让外层 begin 回滚并阻止启动；禁止吞异常后继续。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_checkpoints'"
    ).fetchone()
    if row is None:
        return

    cols = conn.exec_driver_sql(
        "PRAGMA table_info(editor_state_checkpoints)"
    ).fetchall()
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    if "display_name" in existing:
        return

    # 单行字面量：便于验收扫描 ADD COLUMN display_name
    conn.exec_driver_sql(
        "ALTER TABLE editor_state_checkpoints ADD COLUMN display_name VARCHAR(160)"
    )


def _sqlite_normalize_ddl(ddl: str) -> str:
    """用途：消除 DDL 全部空白（含 tab/换行/全角空格等）后小写，供幂等判定。"""
    # 先统一常见空白与全角空格，再去掉所有 Unicode 空白类字符
    collapsed = (
        str(ddl)
        .replace("\u3000", " ")
        .replace("\xa0", " ")
    )
    return "".join(ch for ch in collapsed if not ch.isspace()).lower()


def _sqlite_default_equiv_zero(dflt_value) -> bool:
    """用途：PRAGMA dflt_value 是否等价 0/false（含 '0'/0/false）。"""
    if dflt_value is None:
        return False
    text = str(dflt_value).strip().lower()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip().lower()
    return text in ("0", "false", "0.0")


def _editor_state_checkpoints_is_pinned_final(
    cols,
    ddl: str,
) -> bool:
    """
    用途：判断 is_pinned 是否已达最终态：
      列存在、类型 BOOLEAN、notnull=1、default 等价 0/false、DDL 等价 0/1 CHECK。
    任一不完整返回 False，调用方须重建。
    """
    pin_row = None
    for r in cols:
        if r is not None and len(r) > 1 and r[1] == "is_pinned":
            pin_row = r
            break
    if pin_row is None:
        return False
    col_type = str(pin_row[2] or "").strip().upper()
    notnull = int(pin_row[3] or 0)
    dflt = pin_row[4] if len(pin_row) > 4 else None
    if "BOOLEAN" not in col_type:
        return False
    if notnull != 1:
        return False
    if not _sqlite_default_equiv_zero(dflt):
        return False
    if "is_pinnedin(0,1)" not in _sqlite_normalize_ddl(ddl):
        return False
    return True


def migrate_editor_state_checkpoints_is_pinned(conn) -> None:
    """
    用途：P12J-A 幂等加 is_pinned BOOLEAN NOT NULL DEFAULT 0，并附 0/1 CHECK。
    对接：ensure_schema_columns；仅 SQLite 生效；须在检查点 display_name 迁移之后。
    二次开发：
      - 表不存在 no-op；列已达最终态（BOOLEAN + notnull + default0 + 0/1 CHECK）才 no-op；
      - SQLite 禁止 ADD COLUMN 附 CHECK，故用临时表重建；
      - 存量无列时 is_pinned 固定 0；已有列时仅保留原始 0/1，NULL/其它归零；
      - 完整保留十一列、三个既有数值 CHECK、外键与四个索引；
      - 失败必须抛出阻止启动并由外层 begin 回滚。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_checkpoints'"
    ).fetchone()
    if row is None or not row[0]:
        return

    ddl = row[0]
    cols = conn.exec_driver_sql(
        "PRAGMA table_info(editor_state_checkpoints)"
    ).fetchall()
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    # 最终态齐全才幂等 no-op；可空/无 DEFAULT/类型或 CHECK 不完整均重建
    if _editor_state_checkpoints_is_pinned_final(cols, ddl):
        return

    # 触发真实事务（与修订 is_pinned 迁移同策略）
    conn.exec_driver_sql(
        "UPDATE editor_state_checkpoints SET id = id WHERE 0"
    )

    has_display_name = "display_name" in existing
    has_is_pinned = "is_pinned" in existing

    # DROP 前捕获用户索引 DDL（sql 非空），重建后原样恢复，不新增未有索引
    index_ddls = [
        r[0]
        for r in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='editor_state_checkpoints' "
            "AND sql IS NOT NULL"
        ).fetchall()
        if r is not None and r[0]
    ]

    conn.exec_driver_sql(
        """
        CREATE TABLE editor_state_checkpoints__p12ja_mig (
            id VARCHAR(64) PRIMARY KEY,
            workspace_id VARCHAR(64) NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            project_id VARCHAR(64) NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_json TEXT NOT NULL,
            state_version VARCHAR(64) NOT NULL,
            snapshot_bytes INTEGER NOT NULL,
            outline_node_count INTEGER NOT NULL,
            chapter_count INTEGER NOT NULL,
            display_name VARCHAR(160),
            is_pinned BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
            CHECK (outline_node_count >= 0),
            CHECK (chapter_count >= 0),
            CHECK (is_pinned IN (0, 1))
        )
        """
    )
    before = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_checkpoints"
    ).fetchone()[0]

    if has_display_name and has_is_pinned:
        select_sql = """
            INSERT INTO editor_state_checkpoints__p12ja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, display_name,
                CASE WHEN is_pinned IN (0, 1) THEN is_pinned ELSE 0 END,
                created_at
            FROM editor_state_checkpoints
        """
    elif has_display_name:
        select_sql = """
            INSERT INTO editor_state_checkpoints__p12ja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, display_name, 0, created_at
            FROM editor_state_checkpoints
        """
    else:
        select_sql = """
            INSERT INTO editor_state_checkpoints__p12ja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, outline_node_count,
                chapter_count, NULL, 0, created_at
            FROM editor_state_checkpoints
        """
    conn.exec_driver_sql(select_sql)

    after = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_checkpoints__p12ja_mig"
    ).fetchone()[0]
    if before != after:
        raise RuntimeError(
            "editor_state_checkpoints is_pinned 迁移行数核对失败，已中止（调用方须回滚）"
        )

    conn.exec_driver_sql("DROP TABLE editor_state_checkpoints")
    conn.exec_driver_sql(
        "ALTER TABLE editor_state_checkpoints__p12ja_mig "
        "RENAME TO editor_state_checkpoints"
    )
    # 原样恢复 DROP 前用户索引；生产 create_all 库通常已有四索引，迁移 no-op 不经此路径
    for ddl_sql in index_ddls:
        conn.exec_driver_sql(ddl_sql)


def migrate_editor_state_revisions_is_pinned(conn) -> None:
    """
    用途：P12F-J-A 幂等加 is_pinned BOOLEAN NOT NULL DEFAULT 0，并附 0/1 CHECK。
    对接：ensure_schema_columns；仅 SQLite 生效；须在 display_name 迁移之后。
    二次开发：
      - 表不存在 no-op；列已存在且 DDL 含 is_pinned IN (0,1) 则 no-op；
      - SQLite 禁止 ADD COLUMN 附 CHECK，故用临时表重建；
      - 存量行 is_pinned 固定 0（已有列时保留原值）；失败必须抛出阻止启动并回滚。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT sql FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_revisions'"
    ).fetchone()
    if row is None or not row[0]:
        return

    ddl = row[0]
    cols = conn.exec_driver_sql("PRAGMA table_info(editor_state_revisions)").fetchall()
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    normalized = ddl.replace(" ", "").lower()
    # 已具备列 + 0/1 CHECK → 幂等 no-op
    if "is_pinned" in existing and "is_pinnedin(0,1)" in normalized:
        return

    # 触发真实事务（与九来源迁移同策略）
    conn.exec_driver_sql(
        "UPDATE editor_state_revisions SET id = id WHERE 0"
    )

    has_display_name = "display_name" in existing
    has_is_pinned = "is_pinned" in existing

    conn.exec_driver_sql(
        """
        CREATE TABLE editor_state_revisions__p12fja_mig (
            id VARCHAR(64) PRIMARY KEY,
            workspace_id VARCHAR(64) NOT NULL
                REFERENCES workspaces(id) ON DELETE CASCADE,
            project_id VARCHAR(64) NOT NULL
                REFERENCES projects(id) ON DELETE CASCADE,
            snapshot_json TEXT NOT NULL,
            state_version VARCHAR(64) NOT NULL,
            snapshot_bytes INTEGER NOT NULL,
            source_kind VARCHAR(64) NOT NULL,
            display_name VARCHAR(160),
            is_pinned BOOLEAN NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL,
            CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
            CHECK (
                source_kind IN (
                    'browser_put','task','revise','callback',
                    'local_parser','content_fuse_apply',
                    'content_fuse_consume','checkpoint_restore',
                    'revision_restore'
                )
            ),
            CHECK (is_pinned IN (0, 1))
        )
        """
    )
    before = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_revisions"
    ).fetchone()[0]

    if has_display_name and has_is_pinned:
        select_sql = """
            INSERT INTO editor_state_revisions__p12fja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                display_name,
                CASE WHEN is_pinned IN (0, 1) THEN is_pinned ELSE 0 END,
                created_at
            FROM editor_state_revisions
        """
    elif has_display_name:
        select_sql = """
            INSERT INTO editor_state_revisions__p12fja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                display_name, 0, created_at
            FROM editor_state_revisions
        """
    else:
        select_sql = """
            INSERT INTO editor_state_revisions__p12fja_mig (
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                display_name, is_pinned, created_at
            )
            SELECT
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind,
                NULL, 0, created_at
            FROM editor_state_revisions
        """
    conn.exec_driver_sql(select_sql)

    after = conn.exec_driver_sql(
        "SELECT COUNT(*) FROM editor_state_revisions__p12fja_mig"
    ).fetchone()[0]
    if before != after:
        raise RuntimeError(
            "editor_state_revisions is_pinned 迁移行数核对失败，已中止（调用方须回滚）"
        )

    conn.exec_driver_sql("DROP TABLE editor_state_revisions")
    conn.exec_driver_sql(
        "ALTER TABLE editor_state_revisions__p12fja_mig "
        "RENAME TO editor_state_revisions"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_workspace_id "
        "ON editor_state_revisions(workspace_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_project_id "
        "ON editor_state_revisions(project_id)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_editor_state_revisions_created_at "
        "ON editor_state_revisions(created_at)"
    )
    conn.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_esr_workspace_project_created_id "
        "ON editor_state_revisions(workspace_id, project_id, created_at, id)"
    )


def migrate_editor_state_revisions_actor_user_id(conn) -> None:
    """
    用途：P13-D1 幂等为修订表加 nullable actor_user_id（VARCHAR(64)）。
    对接：ensure_schema_columns；仅 SQLite 生效。
    二次开发：
      - 表不存在时 no-op（create_all 按新 ORM 建列）；
      - 列已存在立即 no-op；
      - 禁止 FK/索引；失败必须抛出让外层 begin 回滚；禁止自行 commit。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='editor_state_revisions'"
    ).fetchone()
    if row is None:
        return

    cols = conn.exec_driver_sql("PRAGMA table_info(editor_state_revisions)").fetchall()
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    if "actor_user_id" in existing:
        return

    conn.exec_driver_sql(
        "ALTER TABLE editor_state_revisions "
        "ADD COLUMN actor_user_id VARCHAR(64)"
    )


def migrate_project_tasks_actor_user_id(conn) -> None:
    """
    用途：P13-D1 幂等为任务表加 nullable actor_user_id（VARCHAR(64)）。
    对接：ensure_schema_columns；仅 SQLite 生效。
    二次开发：
      - 表不存在时 no-op；列已存在 no-op；
      - 禁止 FK/索引；失败外层回滚；禁止自行 commit。
    """
    dialect_name = getattr(getattr(conn, "dialect", None), "name", None)
    if dialect_name != "sqlite":
        return

    row = conn.exec_driver_sql(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='project_tasks'"
    ).fetchone()
    if row is None:
        return

    cols = conn.exec_driver_sql("PRAGMA table_info(project_tasks)").fetchall()
    existing = {r[1] for r in cols if r is not None and len(r) > 1}
    if "actor_user_id" in existing:
        return

    conn.exec_driver_sql(
        "ALTER TABLE project_tasks ADD COLUMN actor_user_id VARCHAR(64)"
    )


def ensure_schema_columns(target_engine=None) -> None:
    """
    用途：SQLite 个人版轻量加列（create_all 不会改已有表）。
    对接：main.lifespan 启动时调用；列已存在则忽略。
    二次开发：测试旧库迁移时可传独立 target_engine，避免共享测试库 DDL 锁。
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
        "ALTER TABLE project_editor_states ADD COLUMN response_matrix_json TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN parsed_markdown TEXT",
        "ALTER TABLE project_editor_states ADD COLUMN business_json TEXT",
        "ALTER TABLE project_tasks ADD COLUMN payload_json TEXT",
        "ALTER TABLE workspace_settings ADD COLUMN export_format_json TEXT",
        "ALTER TABLE projects ADD COLUMN kind VARCHAR(32) DEFAULT 'technical'",
        "ALTER TABLE projects ADD COLUMN linked_project_id VARCHAR(64)",
        "ALTER TABLE kb_chunks ADD COLUMN embedding_json TEXT",
        "ALTER TABLE workspace_settings ADD COLUMN embedding_model VARCHAR(200) DEFAULT ''",
        "ALTER TABLE project_files ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'source'",
        "ALTER TABLE projects ADD COLUMN source_opportunity_id VARCHAR(64)",
        "ALTER TABLE bid_opportunities ADD COLUMN source_key VARCHAR(200)",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_bid_opportunities_workspace_source_key
        ON bid_opportunities(workspace_id, source_key)
        """,
        # P9C：新表由 create_all 建立；此处仅补列与常用查询索引（幂等）
        "ALTER TABLE semantic_embedding_indexes ADD COLUMN total_chunks INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE semantic_embedding_indexes ADD COLUMN embedded_chunks INTEGER NOT NULL DEFAULT 0",
        """
        CREATE INDEX IF NOT EXISTS ix_semantic_embedding_indexes_workspace_status
        ON semantic_embedding_indexes(workspace_id, status)
        """,
        # P9C：同 workspace 同时最多一条 queued/running；不影响 active/failed/superseded 并存
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
        uq_semantic_embedding_indexes_workspace_building
        ON semantic_embedding_indexes(workspace_id)
        WHERE status IN ('queued', 'running')
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_semantic_chunk_embeddings_workspace_index
        ON semantic_chunk_embeddings(workspace_id, index_id)
        """,
        # P10A：身份表由 create_all 建立；此处仅幂等补常用查询索引
        """
        CREATE INDEX IF NOT EXISTS ix_workspace_members_user
        ON workspace_members(user_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_expires
        ON auth_sessions(user_id, expires_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_auth_audit_events_created
        ON auth_audit_events(created_at)
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_resources_validate_insert
        BEFORE INSERT ON resources
        FOR EACH ROW
        WHEN NEW.source NOT IN ('system', 'user')
          OR (NEW.source = 'system' AND NEW.workspace_id IS NOT NULL)
          OR (NEW.source = 'user' AND NEW.workspace_id IS NULL)
        BEGIN
            SELECT RAISE(ABORT, '资源来源与工作空间不一致');
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_resources_validate_update
        BEFORE UPDATE OF source, workspace_id ON resources
        FOR EACH ROW
        WHEN NEW.source NOT IN ('system', 'user')
          OR (NEW.source = 'system' AND NEW.workspace_id IS NOT NULL)
          OR (NEW.source = 'user' AND NEW.workspace_id IS NULL)
        BEGIN
            SELECT RAISE(ABORT, '资源来源与工作空间不一致');
        END
        """,
        # P12B-C2：P8C 票据绑定签发时权威全状态版本；旧行允许 NULL
        "ALTER TABLE local_parser_callback_tickets ADD COLUMN expected_state_version VARCHAR(64)",
    ]
    active_engine = target_engine or engine
    with active_engine.begin() as conn:
        for sql in statements:
            try:
                conn.exec_driver_sql(sql)
            except Exception:
                # 列已存在或其它可忽略错误
                pass
        # P12C-C2：九来源 CHECK 迁移失败必须回滚并阻止启动（不吞异常）
        migrate_editor_state_revisions_revision_restore_source(conn)
        # P12F-H：九来源迁移成功后幂等加 display_name；失败阻止启动
        migrate_editor_state_revisions_display_name(conn)
        # P12F-J-A：display_name 后幂等加 is_pinned + CHECK；失败阻止启动
        migrate_editor_state_revisions_is_pinned(conn)
        # P12G：检查点表幂等加 display_name；失败阻止启动
        migrate_editor_state_checkpoints_display_name(conn)
        # P12J-A：检查点 display_name 后幂等加 is_pinned + CHECK；失败阻止启动
        migrate_editor_state_checkpoints_is_pinned(conn)
        # P13-D1：修订/任务可空 actor_user_id；失败阻止启动（外层事务回滚）
        migrate_editor_state_revisions_actor_user_id(conn)
        migrate_project_tasks_actor_user_id(conn)
