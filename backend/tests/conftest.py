"""
模块：pytest 夹具
用途：在导入 app 前注入独立文件型 SQLite；每测重建表并 seed 默认 workspace，支持后台任务并发。
对接：tests/test_*.py 中的 client fixture；app.core.database 的测试 DATABASE_URL。
二次开发：必须在 import app 前设置 DATABASE_URL；若改用并行 pytest，需为每个 worker 分配独立测试库。
"""

import os

# 必须在导入 app 之前设置。文件型 SQLite 允许 TestClient 与后台任务使用独立连接；
# :memory: + StaticPool 会让跨线程请求竞争同一条 sqlite3 连接，造成偶发底层异常。
os.environ["DATABASE_URL"] = "sqlite:///./data/biaoshu-pytest.db"
os.environ["DEFAULT_WORKSPACE_ID"] = "ws_local"
os.environ["DEFAULT_WORKSPACE_NAME"] = "测试工作空间"
os.environ["DEFAULT_OWNER_USER_ID"] = "user_test"
os.environ["SEED_SAMPLE_OPPORTUNITIES"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.main import app
from app.services.project_service import ensure_default_workspace


@pytest.fixture(autouse=True)
def _reset_db():
    get_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        ensure_default_workspace(db, get_settings())
    finally:
        db.close()
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    # 作为上下文管理器进入，确保 lifespan（建表/seed）执行
    with TestClient(app) as c:
        yield c
