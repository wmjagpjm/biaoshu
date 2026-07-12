"""
模块：E2E 数据库重置脚本
用途：在独立 biaoshu-e2e.db 上 drop/create 并 seed workspace，供 Playwright 前后端隔离。
对接：frontend/playwright.config.ts globalSetup / webServer 启动前；勿指向日用库或 pytest 库。
二次开发：仅允许 e2e 库文件名；禁止读取真实 Key；改库路径时同步 playwright 环境变量。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保可 import app（从 backend 工作目录运行）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("DATABASE_URL", "sqlite:///./data/biaoshu-e2e.db")
os.environ.setdefault("DEFAULT_WORKSPACE_ID", "ws_e2e")
os.environ.setdefault("DEFAULT_WORKSPACE_NAME", "E2E 工作空间")
os.environ.setdefault("DEFAULT_OWNER_USER_ID", "user_e2e")
os.environ.setdefault("SEED_SAMPLE_OPPORTUNITIES", "false")

from app.core.config import get_settings  # noqa: E402
from app.core.database import Base, SessionLocal, engine, ensure_schema_columns  # noqa: E402
from app.models import (  # noqa: E402, F401
    BidOpportunityRow,
    KbChunkRow,
    KbDocumentRow,
    KbFolderRow,
    Project,
    ProjectEditorStateRow,
    ProjectFileRow,
    ProjectTaskRow,
    ResourceRow,
    ResourceSyncItemRow,
    ResourceSyncRunRow,
    ResourceSyncSourceRow,
    Workspace,
    WorkspaceSettingsRow,
)
from app.services.project_service import ensure_default_workspace  # noqa: E402


def main() -> int:
    """
    模块：E2E 数据库重置入口
    用途：校验 DATABASE_URL 含 biaoshu-e2e 后 drop/create 并 seed 默认 workspace。
    对接：Playwright webServer 启动链、命令行 `python scripts/e2e_reset_db.py`。
    二次开发：拒绝非 e2e 库 URL；seed 字段与 playwright 环境变量保持一致。
    """
    url = os.environ.get("DATABASE_URL", "")
    if "biaoshu-e2e" not in url:
        print("拒绝：DATABASE_URL 必须包含 biaoshu-e2e，避免误清业务库", file=sys.stderr)
        return 2
    get_settings.cache_clear()
    Path("data").mkdir(parents=True, exist_ok=True)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_schema_columns(engine)
    db = SessionLocal()
    try:
        ensure_default_workspace(db, get_settings())
    finally:
        db.close()
    print(f"E2E 库已重置: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
