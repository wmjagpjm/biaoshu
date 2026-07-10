"""
模块：合规检查路由（查重 / 废标）
用途：同步运行检查并返回结构化结果。
对接：
  - POST /api/projects/{id}/duplicate-check
  - POST /api/projects/{id}/rejection-check
  - 前端 duplicate-check / rejection-check 页面
二次开发：可改为异步 task + SSE。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.core.database import get_db
from app.services import duplicate_service, rejection_service
from app.services.project_service import ProjectNotFoundError

router = APIRouter(prefix="/projects", tags=["compliance"])


class DuplicateCheckIn(BaseModel):
    """用途：查重请求。"""

    model_config = ConfigDict(populate_by_name=True)

    scope: Literal["kb+history", "kb", "self"] = "kb+history"
    threshold: float = 0.6
    top_k: int = Field(default=50, alias="topK")


class RejectionCheckIn(BaseModel):
    """用途：废标检查请求。"""

    model_config = ConfigDict(populate_by_name=True)

    include_rules: bool = Field(default=True, alias="includeRules")


@router.post("/{project_id}/duplicate-check")
def post_duplicate_check(
    project_id: str,
    body: DuplicateCheckIn,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    """
    用途：运行标书查重。
    对接：DuplicateCheckPage
    """
    try:
        return duplicate_service.run_duplicate_check(
            db,
            workspace_id,
            project_id,
            scope=body.scope,
            threshold=body.threshold,
            top_k=body.top_k,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None


@router.post("/{project_id}/rejection-check")
def post_rejection_check(
    project_id: str,
    body: RejectionCheckIn,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> dict:
    """
    用途：运行废标项检查。
    对接：RejectionCheckPage
    """
    try:
        return rejection_service.run_rejection_check(
            db,
            workspace_id,
            project_id,
            include_rules=body.include_rules,
        )
    except ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="项目不存在") from None
