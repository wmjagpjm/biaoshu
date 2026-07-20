"""
模块：P13-F1 项目在线租约路由
用途：仅 POST heartbeat/leave；required 活动 workspace strict bid_writer。
对接：project_presence_service；schemas.ProjectPresence*；AuthMiddleware CSRF。
二次开发：
  - 不改公共 deps；任何 X-Workspace-Id（含空）拒绝；
  - 成功 no-store；未知异常 rollback 脱敏 500；禁止 GET/SSE/WebSocket；
  - heartbeat/leave 必须手工安全 JSON 解析，禁止默认 422 回显 clientId。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.schemas import (
    ProjectPresenceClientBody,
    ProjectPresenceHeartbeatOut,
    ProjectPresenceMemberOut,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import auth_service
from app.services.project_presence_service import (
    CODE_PRESENCE_FAILED,
    MSG_PRESENCE_FAILED,
    ProjectPresenceError,
    heartbeat_presence,
    leave_presence,
)

router = APIRouter(prefix="/projects", tags=["project-presence"])

# 固定：拒绝借 X-Workspace-Id 切换 presence 作用域
_CODE_WS_HEADER = "workspace_header_forbidden"
_MSG_WS_HEADER = "不允许通过请求头切换 presence 作用域"

# 请求体校验失败的固定脱敏 detail；不得拼接任何原始输入
_CODE_BODY_INVALID = "presence_client_invalid"
_MSG_BODY_INVALID = "在线状态客户端标识不合法"
_BODY_INVALID_DETAIL = {
    "code": _CODE_BODY_INVALID,
    "message": _MSG_BODY_INVALID,
}

def _no_store(response: Response) -> None:
    """用途：成功/业务错误均禁止缓存。"""
    response.headers["Cache-Control"] = "no-store"


def _raise_presence_error(exc: ProjectPresenceError) -> None:
    """用途：映射服务层固定错误。"""
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_body_invalid() -> NoReturn:
    """用途：presence 请求体专用；校验失败固定 422，绝不回显原始 clientId/额外值。"""
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


def _parse_presence_body(data: dict[str, Any]) -> ProjectPresenceClientBody:
    """用途：严格校验 ProjectPresenceClientBody；失败固定脱敏，不暴露 loc/input。"""
    try:
        return ProjectPresenceClientBody.model_validate(data)
    except ValidationError:
        _raise_body_invalid()


async def _read_presence_client_body(request: Request) -> ProjectPresenceClientBody:
    """
    用途：heartbeat/leave 共用安全 body 解析。
    规则：非法 JSON、非对象、缺失/extra/snake/type/长度/字符 → 固定 422 脱敏。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_body_invalid()
    data = _read_json_object(raw)
    return _parse_presence_body(data)


def require_presence_scope(
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
    # 头名大小写不敏感；存在即拒（含空字符串）
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
        # 回退 principal.user_id（同为中间件注入可信主体）
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

    # 避免未使用参数告警：db 保留与其它依赖一致生命周期
    _ = db
    return active_ws, actor_user_id


@router.post(
    "/{project_id}/presence/heartbeat",
    response_model=ProjectPresenceHeartbeatOut,
)
async def presence_heartbeat(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    scope: Annotated[tuple[str, str], Depends(require_presence_scope)],
) -> ProjectPresenceHeartbeatOut:
    """
    用途：续租/建立 presence 租约并返回成员快照。
    成功：200 + no-store；精确四键。
    """
    workspace_id, actor_user_id = scope
    body = await _read_presence_client_body(request)
    try:
        result = heartbeat_presence(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            client_id=body.client_id,
        )
        db.commit()
    except ProjectPresenceError as exc:
        db.rollback()
        _raise_presence_error(exc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": CODE_PRESENCE_FAILED, "message": MSG_PRESENCE_FAILED},
            headers={"Cache-Control": "no-store"},
        ) from None

    _no_store(response)
    return ProjectPresenceHeartbeatOut(
        lease_expires_at=result.lease_expires_at,
        refresh_after_seconds=result.refresh_after_seconds,
        members=[
            ProjectPresenceMemberOut(username=m.username, is_self=m.is_self)
            for m in result.members
        ],
        truncated=result.truncated,
    )


@router.post(
    "/{project_id}/presence/leave",
    status_code=204,
    response_class=Response,
)
async def presence_leave(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    scope: Annotated[tuple[str, str], Depends(require_presence_scope)],
) -> Response:
    """
    用途：删除当前 actor 当前项目当前 client 摘要租约；幂等 204 空 body。
    """
    workspace_id, actor_user_id = scope
    body = await _read_presence_client_body(request)
    try:
        leave_presence(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=actor_user_id,
            client_id=body.client_id,
        )
        db.commit()
    except ProjectPresenceError as exc:
        db.rollback()
        _raise_presence_error(exc)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": CODE_PRESENCE_FAILED, "message": MSG_PRESENCE_FAILED},
            headers={"Cache-Control": "no-store"},
        ) from None

    _no_store(response)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})
