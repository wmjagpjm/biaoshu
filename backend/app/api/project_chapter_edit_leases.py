"""
模块：P13-G1 项目章节编辑意图租约路由
用途：仅 POST heartbeat/leave；required 活动 workspace strict bid_writer。
对接：project_chapter_edit_lease_service；schemas.ProjectChapterEditLease*；
  AuthMiddleware CSRF。
二次开发：
  - 不改公共 deps；任何 X-Workspace-Id（含空）拒绝；
  - 成功 no-store；未知异常 rollback 脱敏 500；禁止 GET/SSE/WebSocket；
  - heartbeat/leave 必须手工安全 JSON 解析，禁止默认 422 回显 clientId/chapterId。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.schemas import (
    ProjectChapterEditLeaseBody,
    ProjectChapterEditLeaseHeartbeatOut,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import auth_service
from app.services.project_chapter_edit_lease_service import (
    CODE_LEASE_FAILED,
    MSG_LEASE_FAILED,
    ProjectChapterEditLeaseError,
    heartbeat_chapter_edit_lease,
    leave_chapter_edit_lease,
)

router = APIRouter(prefix="/projects", tags=["project-chapter-edit-lease"])

# 固定：拒绝借 X-Workspace-Id 切换租约作用域
_CODE_WS_HEADER = "workspace_header_forbidden"
_MSG_WS_HEADER = "不允许通过请求头切换章节编辑意图作用域"

# 请求体校验失败的固定脱敏 detail；不得拼接任何原始输入
_CODE_BODY_INVALID = "chapter_edit_lease_request_invalid"
_MSG_BODY_INVALID = "章节编辑意图请求无效"
_BODY_INVALID_DETAIL = {
    "code": _CODE_BODY_INVALID,
    "message": _MSG_BODY_INVALID,
}

# 手工有限 body：保守固定上限；禁止先 await request.body() 再检查
_MAX_BODY_BYTES = 4096


def _no_store(response: Response) -> None:
    """用途：成功/业务错误均禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_lease_error(exc: ProjectChapterEditLeaseError) -> None:
    """用途：映射服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_body_invalid() -> NoReturn:
    """用途：请求体专用；校验失败固定 422，绝不回显原始 clientId/chapterId。"""
    raise HTTPException(
        status_code=422,
        detail=dict(_BODY_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _read_json_object(raw: bytes) -> dict[str, Any]:
    """
    用途：读取 JSON 对象；非法 JSON/非对象一律固定脱敏 422。
    二次开发：禁止把解析异常信息或 body 片段写入 detail/日志。
    """
    if not raw:
        _raise_body_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_body_invalid()
    if not isinstance(data, dict):
        _raise_body_invalid()
    return data


def _parse_lease_body(data: dict[str, Any]) -> ProjectChapterEditLeaseBody:
    """用途：严格校验两键 body；失败固定脱敏，不暴露 loc/input。"""
    try:
        return ProjectChapterEditLeaseBody.model_validate(data)
    except ValidationError:
        _raise_body_invalid()


async def _read_limited_body_bytes(request: Request) -> bytes:
    """
    用途：有限读取 JSON body，最终门为 stream 累计上限。
    规则：
      - 合法 Content-Length 可早拒绝（>4096 → 固定 422）；
      - 缺失/伪造 Content-Length 不信任，仍以 stream 累计为最终门；
      - 超过上限立即停止读取并固定脱敏 422；禁止缓存完整超限体；
      - 禁止先 await request.body() 再检查。
    """
    cl_raw = request.headers.get("content-length")
    if cl_raw is not None:
        try:
            cl_val = int(cl_raw)
        except (TypeError, ValueError):
            cl_val = None
        else:
            if cl_val < 0 or cl_val > _MAX_BODY_BYTES:
                _raise_body_invalid()

    parts: list[bytes] = []
    total = 0
    try:
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > _MAX_BODY_BYTES:
                parts.clear()
                _raise_body_invalid()
            parts.append(chunk)
    except HTTPException:
        raise
    except Exception:
        _raise_body_invalid()
    return b"".join(parts)


async def _read_lease_body(request: Request) -> ProjectChapterEditLeaseBody:
    """
    用途：heartbeat/leave 共用安全 body 解析。
    规则：空体/非法 JSON/非对象/超限/缺失/extra/snake/type/长度/字符
      → 固定 422 脱敏 + no-store。
    """
    raw = await _read_limited_body_bytes(request)
    data = _read_json_object(raw)
    return _parse_lease_body(data)


def require_chapter_edit_lease_scope(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> tuple[str, str]:
    """
    用途：私有依赖——仅 required + 活动 workspace + 精确 bid_writer。
    规则：
      - 任何 X-Workspace-Id 头存在（含空）固定 403；
      - owner 不替代角色；actor 仅取可信 principal.user_id；
      - 不调用/修改公共 get_workspace_id。
    返回：(workspace_id, actor_user_id)
    """
    for name in request.headers.keys():
        if name.lower() == "x-workspace-id":
            raise HTTPException(
                status_code=403,
                detail={"code": _CODE_WS_HEADER, "message": _MSG_WS_HEADER},
                headers={"Cache-Control": "no-store"},
            )

    if not settings.is_auth_required():
        raise HTTPException(
            status_code=403,
            detail={
                "code": auth_service.CODE_ROLE_FORBIDDEN,
                "message": auth_service.MSG_ROLE_FORBIDDEN,
            },
            headers={"Cache-Control": "no-store"},
        )

    principal = getattr(request.state, "auth_principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
            headers={"Cache-Control": "no-store"},
        )

    actor_user_id = getattr(request.state, "auth_db_user_id", None)
    if not isinstance(actor_user_id, str) or not actor_user_id:
        actor_user_id = getattr(principal, "user_id", None)
    if not isinstance(actor_user_id, str) or not actor_user_id:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
            headers={"Cache-Control": "no-store"},
        )
    if actor_user_id.strip() != actor_user_id or len(actor_user_id) > 64:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
            headers={"Cache-Control": "no-store"},
        )

    active_ws = principal.active_workspace_id
    if not isinstance(active_ws, str) or not active_ws:
        raise HTTPException(
            status_code=403,
            detail={
                "code": auth_service.CODE_ROLE_FORBIDDEN,
                "message": auth_service.MSG_ROLE_FORBIDDEN,
            },
            headers={"Cache-Control": "no-store"},
        )

    member = next(
        (
            m
            for m in principal.members
            if m.workspace_id == active_ws and m.is_active
        ),
        None,
    )
    if member is None or member.role != auth_service.ROLE_BID_WRITER:
        raise HTTPException(
            status_code=403,
            detail={
                "code": auth_service.CODE_ROLE_FORBIDDEN,
                "message": auth_service.MSG_ROLE_FORBIDDEN,
            },
            headers={"Cache-Control": "no-store"},
        )

    _ = db
    return active_ws, actor_user_id


@router.post(
    "/{project_id}/chapter-edit-lease/heartbeat",
    response_model=ProjectChapterEditLeaseHeartbeatOut,
)
async def chapter_edit_lease_heartbeat(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    scope: Annotated[tuple[str, str], Depends(require_chapter_edit_lease_scope)],
) -> ProjectChapterEditLeaseHeartbeatOut:
    """
    用途：建立/续期章节编辑意图租约。
    成功：200 + no-store；精确两键 leaseExpiresAt/refreshAfterSeconds。
    """
    workspace_id, actor_user_id = scope
    body = await _read_lease_body(request)
    try:
        result = heartbeat_chapter_edit_lease(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            client_id=body.client_id,
            chapter_id=body.chapter_id,
        )
        db.commit()
    except ProjectChapterEditLeaseError as exc:
        db.rollback()
        _raise_lease_error(exc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": CODE_LEASE_FAILED, "message": MSG_LEASE_FAILED},
            headers={"Cache-Control": "no-store"},
        ) from None

    _no_store(response)
    return ProjectChapterEditLeaseHeartbeatOut(
        lease_expires_at=result.lease_expires_at,
        refresh_after_seconds=result.refresh_after_seconds,
    )


@router.post(
    "/{project_id}/chapter-edit-lease/leave",
    status_code=204,
    response_class=Response,
)
async def chapter_edit_lease_leave(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    scope: Annotated[tuple[str, str], Depends(require_chapter_edit_lease_scope)],
) -> Response:
    """
    用途：五维精确释放当前 actor/client/章节租约；幂等 204 空 body。
    """
    workspace_id, actor_user_id = scope
    body = await _read_lease_body(request)
    try:
        leave_chapter_edit_lease(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            client_id=body.client_id,
            chapter_id=body.chapter_id,
        )
        db.commit()
    except ProjectChapterEditLeaseError as exc:
        db.rollback()
        _raise_lease_error(exc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": CODE_LEASE_FAILED, "message": MSG_LEASE_FAILED},
            headers={"Cache-Control": "no-store"},
        ) from None

    _no_store(response)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
