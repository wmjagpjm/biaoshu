"""
模块：P12A/P12B-D1/P12G editor-state 检查点路由
用途：显式创建、有限列表、按需只读详情、锁后原子安全恢复、单条展示名称 PATCH。
对接：/api/projects/{projectId}/editor-state-checkpoints*；
  editor_state_checkpoint_service；editor_state_checkpoint_name_service；
  deps.get_workspace_id。
二次开发：
  - 复用 get_workspace_id（disabled 兼容，required 仅 bid_writer）；
  - POST 继续既有 CSRF；所有成功/业务错误 Cache-Control: no-store；
  - PATCH display-name：query 空、body≤1024、精确一键；成功仅回 displayName；
  - 错误固定 code/message（409 另含 currentStateVersion），不反射 ID/正文/路径/SQL。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_workspace_id
from app.api.schemas import (
    EditorStateCheckpointCreate,
    EditorStateCheckpointDetailOut,
    EditorStateCheckpointDisplayNameOut,
    EditorStateCheckpointDisplayNameUpdate,
    EditorStateCheckpointListOut,
    EditorStateCheckpointMetaOut,
    EditorStateCheckpointRestore,
    EditorStateCheckpointRestoreOut,
)
from app.core.database import get_db
from app.services import editor_state_checkpoint_service, editor_state_service
from app.services.editor_state_checkpoint_name_service import (
    EditorStateCheckpointNameError,
    set_editor_state_checkpoint_display_name as set_display_name_svc,
)
from app.services.editor_state_checkpoint_service import EditorStateCheckpointError

# P12G：命名请求体原始字节硬上限
_DISPLAY_NAME_BODY_MAX_BYTES = 1024

router = APIRouter(prefix="/projects", tags=["editor-state-checkpoints"])

# P12G 命名请求 query/body 外壳失败的固定脱敏 detail；禁止反射输入
_NAME_REQUEST_INVALID_DETAIL = {
    "code": "editor_state_checkpoint_display_name_request_invalid",
    "message": "检查点名称请求无效",
}


def _no_store(response: Response) -> None:
    """用途：P12A/P12B-D/P12G 响应固定禁止缓存。"""
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


def _raise_name_request_invalid() -> NoReturn:
    """
    用途：PATCH display-name 路由专用；query/体外壳非法固定脱敏 422。
    二次开发：禁止回显 query/body/路径/header/异常原文/名称。
    """
    raise HTTPException(
        status_code=422,
        detail=dict(_NAME_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_name_error(exc: EditorStateCheckpointNameError) -> NoReturn:
    """用途：映射命名服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


async def _read_display_name_json_object(request: Request) -> dict[str, Any]:
    """
    用途：仅 display-name 路由读取 ≤1024 字节 JSON 对象；失败固定 422。
    二次开发：禁止把解析异常或 body 片段写入 detail/日志。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_name_request_invalid()
    if not raw or len(raw) > _DISPLAY_NAME_BODY_MAX_BYTES:
        _raise_name_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_name_request_invalid()
    if not isinstance(data, dict):
        _raise_name_request_invalid()
    return data


def _parse_display_name_body(
    data: dict[str, Any],
) -> EditorStateCheckpointDisplayNameUpdate:
    """用途：JSON 对象 → 命名 Schema；失败固定脱敏，不暴露 loc/input。"""
    try:
        return EditorStateCheckpointDisplayNameUpdate.model_validate(data)
    except ValidationError:
        _raise_name_request_invalid()


def _meta_out(data: dict) -> EditorStateCheckpointMetaOut:
    """用途：service 元数据 dict → 响应模型。"""
    return EditorStateCheckpointMetaOut(
        checkpoint_id=data["checkpoint_id"],
        state_version=data["state_version"],
        snapshot_bytes=data["snapshot_bytes"],
        outline_node_count=data["outline_node_count"],
        chapter_count=data["chapter_count"],
        created_at=data["created_at"],
        # 精确键：服务漏键必须 KeyError 暴露，禁止 .get 伪装合法 null
        display_name=data["display_name"],
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
    二次开发：body 仅 {}；禁止客户端投稿 snapshot/版本/名称；初始 displayName=null。
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
        # 精确键：服务漏键必须 KeyError 暴露，禁止 .get 伪装合法 null
        display_name=data["display_name"],
        snapshot=data["snapshot"],
    )


@router.patch(
    "/{project_id}/editor-state-checkpoints/{checkpoint_id}/display-name",
    response_model=EditorStateCheckpointDisplayNameOut,
)
async def patch_editor_state_checkpoint_display_name(
    project_id: str,
    checkpoint_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> EditorStateCheckpointDisplayNameOut:
    """
    用途：单条更新当前工作空间项目内检查点的展示名称；成功仅回 displayName。
    对接：P12G；editor_state_checkpoint_name_service。
    二次开发：
      - 任意 query 或 body 外壳失败固定 422 脱敏（request_invalid）；
      - body 原始长度 ≤1024；精确一键 displayName；
      - 值非法固定 422 display_name_invalid；
      - 成功/业务错误 no-store；不回显 ID/版本/正文/输入。
    """
    _no_store(response)
    if request.query_params:
        _raise_name_request_invalid()
    body = _parse_display_name_body(await _read_display_name_json_object(request))
    try:
        stored = set_display_name_svc(
            db, workspace_id, project_id, checkpoint_id, body.display_name
        )
    except EditorStateCheckpointNameError as exc:
        _raise_name_error(exc)
    return EditorStateCheckpointDisplayNameOut(display_name=stored)


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
