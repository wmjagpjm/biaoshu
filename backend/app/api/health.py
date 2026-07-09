"""
模块：健康检查路由
用途：进程探活 + 可选 DB 探测，供前端状态条与编排使用。
对接：GET /api/health
二次开发：可增加 version、git_sha，保持 status==ok 语义兼容。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.schemas import HealthOut
from app.core.config import get_settings
from app.core.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(db: Session = Depends(get_db)) -> HealthOut:
    """
    用途：返回服务存活与默认 workspace、数据库是否可查询。
    成功：HTTP 200，body.status == "ok"。
    """
    settings = get_settings()
    db_ok = False
    try:
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return HealthOut(
        status="ok",
        service=settings.app_name,
        default_workspace_id=settings.default_workspace_id,
        db_ok=db_ok,
    )
