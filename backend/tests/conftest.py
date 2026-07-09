"""
模块：pytest 夹具
用途：在导入 app 前注入内存库环境变量；每测重建表并 seed 默认 workspace。
对接：tests/test_*.py 中的 client fixture
注意：必须在 import app 之前设置 DATABASE_URL，否则 engine 仍指向文件库。
"""

import os

# 必须在导入 app 之前设置
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DEFAULT_WORKSPACE_ID"] = "ws_local"
os.environ["DEFAULT_WORKSPACE_NAME"] = "测试工作空间"
os.environ["DEFAULT_OWNER_USER_ID"] = "user_test"

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
