"""
模块：M3-D 融合写入持久恢复批次路由
用途：原子确认、有限列表、一次性漂移安全恢复。
对接：/api/projects/{projectId}/content-fuse-applications*；
  content_fuse_application_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - CSRF 由既有中间件处理；
  - 所有响应 Cache-Control: no-store；错误固定 code/message，不反射 ID/正文。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    ContentFuseApplicationConsumeOut,
    ContentFuseApplicationCreate,
    ContentFuseApplicationCreateOut,
    ContentFuseApplicationListItemOut,
    ContentFuseApplicationListOut,
)
from app.core.database import get_db
from app.services import content_fuse_application_service
from app.services.content_fuse_application_service import ContentFuseApplicationError

router = APIRouter(prefix="/projects", tags=["content-fuse-applications"])


def _no_store(response: Response) -> None:
    """用途：M3-D 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_app_error(exc: ContentFuseApplicationError) -> None:
    """
    用途：映射服务层固定错误，不附加路径或异常原文。
    二次开发：业务 404/409 必须自带 Cache-Control: no-store；
      不得依赖成功路径的 response 头，也不得声称全局鉴权/422 由此设置。
    """
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


@router.post(
    "/{project_id}/content-fuse-applications",
    response_model=ContentFuseApplicationCreateOut,
    status_code=201,
)
def create_content_fuse_application(
    project_id: str,
    body: ContentFuseApplicationCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ContentFuseApplicationCreateOut:
    """
    用途：原子确认所选 content_fuse 建议并写有限恢复批次。
    对接：ContentFuseDialog 确认写入；服务层 apply_content_fuse_application。
    二次开发：请求仅 taskId/suggestionIds；成功后前端须强制重读 editor-state。
    """
    _no_store(response)
    try:
        data = content_fuse_application_service.apply_content_fuse_application(
            db,
            workspace_id,
            project_id,
            task_id=body.task_id,
            suggestion_ids=list(body.suggestion_ids),
        )
    except ContentFuseApplicationError as exc:
        _raise_app_error(exc)
    return ContentFuseApplicationCreateOut(
        batch_id=data["batch_id"],
        applied_chapter_count=data["applied_chapter_count"],
        created_at=data["created_at"],
    )


@router.get(
    "/{project_id}/content-fuse-applications",
    response_model=ContentFuseApplicationListOut,
)
def list_content_fuse_applications(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ContentFuseApplicationListOut:
    """
    用途：读取当前技术标最近 20 批最小投影。
    对接：ContentFuseDialog 打开时刷新批次列表。
    """
    _no_store(response)
    try:
        data = content_fuse_application_service.list_content_fuse_applications(
            db, workspace_id, project_id
        )
    except ContentFuseApplicationError as exc:
        _raise_app_error(exc)
    return ContentFuseApplicationListOut(
        items=[
            ContentFuseApplicationListItemOut(
                batch_id=item["batch_id"],
                chapter_count=item["chapter_count"],
                state=item["state"],
                created_at=item["created_at"],
                consumed_at=item.get("consumed_at"),
            )
            for item in data["items"]
        ]
    )


@router.post(
    "/{project_id}/content-fuse-applications/{batch_id}/consume",
    response_model=ContentFuseApplicationConsumeOut,
)
def consume_content_fuse_application(
    project_id: str,
    batch_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ContentFuseApplicationConsumeOut:
    """
    用途：对 active 批次执行一次恢复尝试；完整/部分/零恢复均消费。
    对接：ContentFuseDialog 二次确认恢复。
    """
    _no_store(response)
    try:
        data = content_fuse_application_service.consume_content_fuse_application(
            db, workspace_id, project_id, batch_id
        )
    except ContentFuseApplicationError as exc:
        _raise_app_error(exc)
    return ContentFuseApplicationConsumeOut(
        restored_chapter_count=data["restored_chapter_count"],
        skipped_chapter_count=data["skipped_chapter_count"],
        consumed_at=data["consumed_at"],
    )
