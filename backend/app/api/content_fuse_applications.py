"""
模块：M3-D 融合写入持久恢复批次路由
用途：原子确认、有限列表、一次性漂移安全恢复。
对接：/api/projects/{projectId}/content-fuse-applications*；
  content_fuse_application_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - CSRF 由既有中间件处理；
  - 所有响应 Cache-Control: no-store；错误固定 code/message，不反射 ID/正文；
  - P12B-C3：apply/consume 强制 expectedStateVersion；全状态冲突映射固定 409。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_request_actor_user_id, get_workspace_id
from app.api.schemas import (
    ContentFuseApplicationConsume,
    ContentFuseApplicationConsumeOut,
    ContentFuseApplicationCreate,
    ContentFuseApplicationCreateOut,
    ContentFuseApplicationListItemOut,
    ContentFuseApplicationListOut,
)
from app.core.database import get_db
from app.services import content_fuse_application_service, editor_state_service
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


def _raise_version_conflict(
    db: Session, exc: editor_state_service.EditorStateVersionConflict
) -> None:
    """
    用途：P12B-C3 全状态冲突固定 409 最小 detail。
    二次开发：仅 code/message/currentStateVersion；rollback 后映射；
      禁止正文/任务/批次/路径/异常原文。
    """
    db.rollback()
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT,
            "message": exc.message,
            "currentStateVersion": exc.current_state_version,
        },
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
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ContentFuseApplicationCreateOut:
    """
    用途：原子确认所选 content_fuse 建议并写有限恢复批次。
    对接：ContentFuseDialog 确认写入；服务层 apply_content_fuse_application。
    二次开发：请求强制 taskId/suggestionIds/expectedStateVersion；
      成功后前端须经版本化外部写队列单次重读 editor-state。
      P13-D1：actor 仅 request-state helper。
    """
    _no_store(response)
    try:
        data = content_fuse_application_service.apply_content_fuse_application(
            db,
            workspace_id,
            project_id,
            task_id=body.task_id,
            suggestion_ids=list(body.suggestion_ids),
            expected_state_version=body.expected_state_version,
            actor_user_id=get_request_actor_user_id(request),
        )
    except editor_state_service.EditorStateVersionConflict as exc:
        _raise_version_conflict(db, exc)
    except ContentFuseApplicationError as exc:
        _raise_app_error(exc)
    return ContentFuseApplicationCreateOut(
        batch_id=data["batch_id"],
        applied_chapter_count=data["applied_chapter_count"],
        created_at=data["created_at"],
        state_version=data["state_version"],
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
    body: ContentFuseApplicationConsume,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> ContentFuseApplicationConsumeOut:
    """
    用途：对 active 批次执行一次恢复尝试；完整/部分/零恢复均消费。
    对接：ContentFuseDialog 二次确认恢复。
    二次开发：请求体仅 expectedStateVersion；全状态冲突不消费批次。
      P13-D1：actor 仅 request-state helper；零恢复不记修订。
    """
    _no_store(response)
    try:
        data = content_fuse_application_service.consume_content_fuse_application(
            db,
            workspace_id,
            project_id,
            batch_id,
            expected_state_version=body.expected_state_version,
            actor_user_id=get_request_actor_user_id(request),
        )
    except editor_state_service.EditorStateVersionConflict as exc:
        _raise_version_conflict(db, exc)
    except ContentFuseApplicationError as exc:
        _raise_app_error(exc)
    return ContentFuseApplicationConsumeOut(
        restored_chapter_count=data["restored_chapter_count"],
        skipped_chapter_count=data["skipped_chapter_count"],
        consumed_at=data["consumed_at"],
        state_version=data["state_version"],
    )
