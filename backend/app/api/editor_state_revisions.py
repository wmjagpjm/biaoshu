"""
模块：P12C-C1 editor-state 修订历史只读路由
用途：项目最近 10 条修订元数据列表与单条按需详情。
对接：/api/projects/{projectId}/editor-state-revisions*；
  editor_state_revision_history_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - 仅 GET；所有成功/业务错误 Cache-Control: no-store；
  - 错误固定 code/message，不反射 ID/正文/路径/SQL；
  - 未知查询参数不得改变固定排序/上限/来源全集/正文不可搜索边界。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateRevisionDetailOut,
    EditorStateRevisionListOut,
    EditorStateRevisionMetaOut,
)
from app.core.database import get_db
from app.services import editor_state_revision_history_service
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
)

router = APIRouter(prefix="/projects", tags=["editor-state-revisions"])


def _no_store(response: Response) -> None:
    """用途：P12C-C1 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_app_error(exc: EditorStateRevisionHistoryError) -> None:
    """
    用途：映射服务层固定错误，不附加路径或异常原文。
    二次开发：业务 404/500 必须自带 Cache-Control: no-store。
    """
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _meta_out(data: dict) -> EditorStateRevisionMetaOut:
    """用途：service 元数据 dict → 响应模型。"""
    return EditorStateRevisionMetaOut(
        revision_id=data["revision_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        source_kind=data["source_kind"],
        created_at=data["created_at"],
    )


@router.get(
    "/{project_id}/editor-state-revisions",
    response_model=EditorStateRevisionListOut,
)
def list_editor_state_revisions(
    project_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionListOut:
    """
    用途：读取当前项目最近 10 条修订元数据（无 snapshot 正文）。
    对接：修订历史列表浏览。
    """
    _no_store(response)
    try:
        data = editor_state_revision_history_service.list_editor_state_revisions(
            db, workspace_id, project_id
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_app_error(exc)
    return EditorStateRevisionListOut(
        items=[_meta_out(item) for item in data["items"]]
    )


@router.get(
    "/{project_id}/editor-state-revisions/{revision_id}",
    response_model=EditorStateRevisionDetailOut,
)
def get_editor_state_revision(
    project_id: str,
    revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionDetailOut:
    """
    用途：按 ID 读取单条修订详情（元数据 + 已校验 snapshot）。
    对接：只读浏览；损坏数据固定 500 脱敏。
    """
    _no_store(response)
    try:
        data = editor_state_revision_history_service.get_editor_state_revision(
            db, workspace_id, project_id, revision_id
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_app_error(exc)
    return EditorStateRevisionDetailOut(
        revision_id=data["revision_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        source_kind=data["source_kind"],
        created_at=data["created_at"],
        snapshot=data["snapshot"],
    )
