"""
模块：P12A/P12B-D1 editor-state 检查点路由
用途：显式创建、有限列表、按需只读详情、锁后原子安全恢复。
对接：/api/projects/{projectId}/editor-state-checkpoints*；
  editor_state_checkpoint_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - POST 继续既有 CSRF；所有成功/业务错误 Cache-Control: no-store；
  - 错误固定 code/message（409 另含 currentStateVersion），不反射 ID/正文/路径/SQL。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateCheckpointCreate,
    EditorStateCheckpointDetailOut,
    EditorStateCheckpointListOut,
    EditorStateCheckpointMetaOut,
    EditorStateCheckpointRestore,
    EditorStateCheckpointRestoreOut,
)
from app.core.database import get_db
from app.services import editor_state_checkpoint_service, editor_state_service
from app.services.editor_state_checkpoint_service import EditorStateCheckpointError

router = APIRouter(prefix="/projects", tags=["editor-state-checkpoints"])


def _no_store(response: Response) -> None:
    """用途：P12A/P12B-D 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_app_error(exc: EditorStateCheckpointError) -> None:
    """
    用途：映射服务层固定错误，不附加路径或异常原文。
    二次开发：业务 404/413/500 必须自带 Cache-Control: no-store。
    """
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _meta_out(data: dict) -> EditorStateCheckpointMetaOut:
    """用途：service 元数据 dict → 响应模型。"""
    return EditorStateCheckpointMetaOut(
        checkpoint_id=data["checkpoint_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        outline_node_count=data["outline_node_count"],
        chapter_count=data["chapter_count"],
        created_at=data["created_at"],
    )


@router.post(
    "/{project_id}/editor-state-checkpoints",
    response_model=EditorStateCheckpointMetaOut,
    status_code=201,
)
def create_editor_state_checkpoint(
    project_id: str,
    body: EditorStateCheckpointCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateCheckpointMetaOut:
    """
    用途：显式创建一条服务端规范检查点（空对象请求体）。
    对接：P12A 手动检查点；服务层 create_editor_state_checkpoint。
    二次开发：body 仅 {}；禁止客户端投稿 snapshot/版本/名称。
    """
    _no_store(response)
    # body 已由 Schema extra=forbid 校验；不读取任何字段
    _ = body
    try:
        data = editor_state_checkpoint_service.create_editor_state_checkpoint(
            db, workspace_id, project_id
        )
    except EditorStateCheckpointError as exc:
        _raise_app_error(exc)
    return _meta_out(data)


@router.get(
    "/{project_id}/editor-state-checkpoints",
    response_model=EditorStateCheckpointListOut,
)
def list_editor_state_checkpoints(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateCheckpointListOut:
    """
    用途：读取当前项目最近 20 条检查点元数据（无 snapshot）。
    对接：检查点列表浏览。
    """
    _no_store(response)
    try:
        data = editor_state_checkpoint_service.list_editor_state_checkpoints(
            db, workspace_id, project_id
        )
    except EditorStateCheckpointError as exc:
        _raise_app_error(exc)
    return EditorStateCheckpointListOut(
        items=[_meta_out(item) for item in data["items"]]
    )


@router.get(
    "/{project_id}/editor-state-checkpoints/{checkpoint_id}",
    response_model=EditorStateCheckpointDetailOut,
)
def get_editor_state_checkpoint(
    project_id: str,
    checkpoint_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateCheckpointDetailOut:
    """
    用途：按 ID 读取单条检查点详情（元数据 + 已校验 snapshot）。
    对接：只读浏览；损坏数据固定 500 脱敏。
    """
    _no_store(response)
    try:
        data = editor_state_checkpoint_service.get_editor_state_checkpoint(
            db, workspace_id, project_id, checkpoint_id
        )
    except EditorStateCheckpointError as exc:
        _raise_app_error(exc)
    return EditorStateCheckpointDetailOut(
        checkpoint_id=data["checkpoint_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        outline_node_count=data["outline_node_count"],
        chapter_count=data["chapter_count"],
        created_at=data["created_at"],
        snapshot=data["snapshot"],
    )


@router.post(
    "/{project_id}/editor-state-checkpoints/{checkpoint_id}/restore",
    response_model=EditorStateCheckpointRestoreOut,
    status_code=200,
)
def restore_editor_state_checkpoint(
    project_id: str,
    checkpoint_id: str,
    body: EditorStateCheckpointRestore,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateCheckpointRestoreOut:
    """
    用途：原子恢复目标检查点；先写恢复前安全检查点，再覆盖当前 13 键。
    对接：P12B-D1；服务层 restore_editor_state_checkpoint。
    二次开发：body 仅 expectedStateVersion；409 复用全状态冲突协议；成功/业务错误 no-store。
    """
    _no_store(response)
    try:
        data = editor_state_checkpoint_service.restore_editor_state_checkpoint(
            db,
            workspace_id,
            project_id,
            checkpoint_id,
            body.expected_state_version,
        )
    except editor_state_service.EditorStateVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": editor_state_service.CODE_FULL_STATE_VERSION_CONFLICT,
                "message": exc.message,
                "currentStateVersion": exc.current_state_version,
            },
            headers={"Cache-Control": "no-store"},
        ) from None
    except EditorStateCheckpointError as exc:
        _raise_app_error(exc)
    return EditorStateCheckpointRestoreOut(
        restored_checkpoint_id=data["restored_checkpoint_id"],
        safety_checkpoint_id=data["safety_checkpoint_id"],
        state_version=data["state_version"],
        restored_at=data["restored_at"],
    )
