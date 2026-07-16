"""
模块：P12C-C1/C2/P12D-A editor-state 修订历史只读、受限恢复与差异摘要路由
用途：项目最近 10 条修订元数据列表、单条按需详情、单条受限恢复、与当前状态差异摘要。
对接：/api/projects/{projectId}/editor-state-revisions*；
  editor_state_revision_history_service；
  editor_state_revision_restore_service；
  editor_state_revision_comparison_service；deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - POST 继续既有 CSRF；所有成功/业务错误 Cache-Control: no-store；
  - 错误固定 code/message（409 另含 currentStateVersion），不反射 ID/正文/路径/SQL；
  - 未知查询参数不得改变固定排序/上限/来源全集/正文不可搜索边界；
  - comparison 只读，禁止写库/锁/审计。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateRevisionComparisonOut,
    EditorStateRevisionComparisonSummaryOut,
    EditorStateRevisionDetailOut,
    EditorStateRevisionListOut,
    EditorStateRevisionMetaOut,
    EditorStateRevisionRestore,
    EditorStateRevisionRestoreOut,
)
from app.core.database import get_db
from app.services import (
    editor_state_revision_comparison_service,
    editor_state_revision_history_service,
    editor_state_revision_restore_service,
    editor_state_service,
)
from app.services.editor_state_revision_comparison_service import (
    EditorStateRevisionComparisonError,
)
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
)
from app.services.editor_state_revision_restore_service import (
    EditorStateRevisionRestoreError,
)

router = APIRouter(prefix="/projects", tags=["editor-state-revisions"])


def _no_store(response: Response) -> None:
    """用途：P12C-C1/C2 响应固定禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_history_error(exc: EditorStateRevisionHistoryError) -> None:
    """
    用途：映射只读服务层固定错误，不附加路径或异常原文。
    二次开发：业务 404/500 必须自带 Cache-Control: no-store。
    """
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_restore_error(exc: EditorStateRevisionRestoreError) -> None:
    """用途：映射恢复服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_comparison_error(exc: EditorStateRevisionComparisonError) -> None:
    """用途：映射差异摘要服务层固定错误。"""
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
        _raise_history_error(exc)
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
        _raise_history_error(exc)
    return EditorStateRevisionDetailOut(
        revision_id=data["revision_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        source_kind=data["source_kind"],
        created_at=data["created_at"],
        snapshot=data["snapshot"],
    )


@router.get(
    "/{project_id}/editor-state-revisions/{revision_id}/comparison",
    response_model=EditorStateRevisionComparisonOut,
)
def compare_editor_state_revision_with_current(
    project_id: str,
    revision_id: str,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionComparisonOut:
    """
    用途：只读比较目标修订与服务端当前 13 键，返回变更字段名与两侧有界摘要。
    对接：P12D-A；editor_state_revision_comparison_service。
    二次开发：历史 404/corrupt 原样映射；其他失败固定 comparison_failed；全程 no-store。
    """
    _no_store(response)
    try:
        data = (
            editor_state_revision_comparison_service.compare_revision_with_current(
                db, workspace_id, project_id, revision_id
            )
        )
    except EditorStateRevisionHistoryError as exc:
        _raise_history_error(exc)
    except EditorStateRevisionComparisonError as exc:
        _raise_comparison_error(exc)

    def _summary(raw: dict) -> EditorStateRevisionComparisonSummaryOut:
        return EditorStateRevisionComparisonSummaryOut(
            outline_node_count=raw["outline_node_count"],
            chapter_count=raw["chapter_count"],
            fact_count=raw["fact_count"],
            response_matrix_row_count=raw["response_matrix_row_count"],
            business_entry_total=raw["business_entry_total"],
            has_parsed_markdown=raw["has_parsed_markdown"],
        )

    return EditorStateRevisionComparisonOut(
        same_state=data["same_state"],
        changed_fields=data["changed_fields"],
        current_summary=_summary(data["current_summary"]),
        target_summary=_summary(data["target_summary"]),
    )


@router.post(
    "/{project_id}/editor-state-revisions/{revision_id}/restore",
    response_model=EditorStateRevisionRestoreOut,
    status_code=200,
)
def restore_editor_state_revision(
    project_id: str,
    revision_id: str,
    body: EditorStateRevisionRestore,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateRevisionRestoreOut:
    """
    用途：原子恢复目标修订；先写恢复前安全检查点，再覆盖当前 13 键。
    对接：P12C-C2；服务层 restore_editor_state_revision。
    二次开发：body 仅 expectedStateVersion；409 复用全状态冲突协议；成功/业务错误 no-store。
    """
    _no_store(response)
    try:
        data = editor_state_revision_restore_service.restore_editor_state_revision(
            db,
            workspace_id,
            project_id,
            revision_id,
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
    except EditorStateRevisionRestoreError as exc:
        _raise_restore_error(exc)
    return EditorStateRevisionRestoreOut(
        safety_checkpoint_id=data["safety_checkpoint_id"],
        state_version=data["state_version"],
        restored_at=data["restored_at"],
    )
