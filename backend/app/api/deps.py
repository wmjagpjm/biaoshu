"""
模块：API 公共依赖
用途：为路由注入数据库会话上下文与「当前工作空间 id」。
对接：各业务路由 Depends(get_workspace_id) / Depends(get_db)
二次开发：
  - auth_mode=disabled：保持个人版 X-Workspace-Id 选择语义
  - auth_mode=required：从会话主体解析成员；请求头仅作成员内选择器
  - require_owner / require_bid_writer 供设置与后续细权限包使用
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import auth_service
from app.services.project_service import ensure_default_workspace


def get_workspace_id(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> str:
    """
    用途：解析当前请求的 workspace。
    disabled：
      1. ensure 默认 workspace 存在
      2. 若带 X-Workspace-Id 且非空，使用该 id
      3. 否则使用 settings.default_workspace_id
    required：
      1. 必须有中间件注入的 auth_principal
      2. X-Workspace-Id 仅在成员列表内选择；非成员 403
      3. 非 bid_writer 对既有业务依赖返回 role_forbidden
    """
    if not settings.is_auth_required():
        ensure_default_workspace(db, settings)
        if x_workspace_id and x_workspace_id.strip():
            return x_workspace_id.strip()
        return settings.default_workspace_id

    principal = getattr(request.state, "auth_principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
        )
    try:
        return auth_service.resolve_workspace_for_principal(
            principal,
            x_workspace_id,
            require_bid_writer=True,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None


def require_bid_writer(
    workspace_id: Annotated[str, Depends(get_workspace_id)],
) -> str:
    """
    用途：显式标记需要标书制作者角色的依赖（get_workspace_id 已强制 bid_writer）。
    对接：后续业务路由可选改挂本依赖以表达意图。
    """
    return workspace_id


def require_owner(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> str:
    """
    用途：要求当前用户为工作空间所有者（设置等敏感路由）。
    disabled：退化为 get_workspace_id 兼容语义（个人版单用户）。
    required：成员存在且 is_owner=True。
    """
    if not settings.is_auth_required():
        return get_workspace_id(request, db, settings, x_workspace_id)

    principal = getattr(request.state, "auth_principal", None)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
        )
    try:
        workspace_id = auth_service.resolve_workspace_for_principal(
            principal,
            x_workspace_id,
            require_bid_writer=False,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None

    member = next(
        (m for m in principal.members if m.workspace_id == workspace_id and m.is_active),
        None,
    )
    if member is None or not member.is_owner:
        raise HTTPException(
            status_code=403,
            detail={
                "code": auth_service.CODE_ROLE_FORBIDDEN,
                "message": auth_service.MSG_ROLE_FORBIDDEN,
            },
        )
    return workspace_id
