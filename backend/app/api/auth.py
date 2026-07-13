"""
模块：P10A 认证路由
用途：引导状态、登录/登出、当前身份、活动工作空间切换。
对接：auth_service；auth_middleware；前端后续 AuthProvider。
二次开发：禁止公开注册与密码找回；响应仅脱敏字段。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.schemas import (
    ActiveWorkspaceUpdate,
    AuthBootstrapStatusOut,
    AuthLoginRequest,
    AuthMeOut,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import AuthSessionRow
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def _raise_auth(exc: auth_service.AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None


def _set_session_cookie(
    response: Response,
    settings: Settings,
    raw_token: str,
) -> None:
    """用途：写入 HttpOnly / SameSite=Strict / Path=/api 会话 Cookie。"""
    max_age = max(1, int(settings.auth_session_ttl_hours)) * 3600
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=raw_token,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=bool(settings.auth_cookie_secure),
        path="/api",
    )


def _clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/api",
        samesite="strict",
        secure=bool(settings.auth_cookie_secure),
        httponly=True,
    )


def get_optional_principal(request: Request) -> auth_service.AuthPrincipal | None:
    """用途：读取中间件注入的主体（disabled 或公开路由可能为空）。"""
    return getattr(request.state, "auth_principal", None)


def require_principal(request: Request) -> auth_service.AuthPrincipal:
    """用途：路由级要求已认证主体。"""
    principal = get_optional_principal(request)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
        )
    return principal


@router.get("/bootstrap-status", response_model=AuthBootstrapStatusOut)
def bootstrap_status(
    db: Annotated[Session, Depends(get_db)],
) -> AuthBootstrapStatusOut:
    """用途：公开；仅返回是否已完成管理员引导。"""
    return AuthBootstrapStatusOut(bootstrapped=auth_service.is_bootstrapped(db))


@router.post("/login", response_model=AuthMeOut)
def login(
    body: AuthLoginRequest,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthMeOut:
    """用途：公开；成功后设置会话 Cookie，正文返回脱敏身份与 CSRF 原始值。"""
    try:
        principal, raw_token, raw_csrf = auth_service.login(
            db,
            settings,
            username=body.username,
            password=body.password,
        )
    except auth_service.AuthError as exc:
        _raise_auth(exc)

    _set_session_cookie(response, settings, raw_token)
    payload = auth_service.serialize_me(
        auth_service.AuthPrincipal(
            user_id=principal.user_id,
            username=principal.username,
            session_id=principal.session_id,
            active_workspace_id=principal.active_workspace_id,
            members=principal.members,
            csrf_token=raw_csrf,
        )
    )
    return AuthMeOut.model_validate(payload)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> None:
    """用途：撤销当前会话并清除 Cookie。

    必须复用注入的 response 写入 delete_cookie（Max-Age=0），
    禁止 return Response(status_code=204) 另起响应，否则会丢弃 Set-Cookie。
    """
    session = db.get(AuthSessionRow, principal.session_id)
    if session is not None:
        auth_service.revoke_session(db, session, actor_user_id=principal.user_id)
    _clear_session_cookie(response, settings)


@router.get("/me", response_model=AuthMeOut)
def me(
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> AuthMeOut:
    """
    用途：返回脱敏身份与可访问工作空间。
    CSRF 原始值仅在登录响应中给出；me 不回传 csrfToken（客户端内存持有）。
    """
    payload = auth_service.serialize_me(principal)
    # 避免在后续 GET 中重复下发 CSRF（登录时已下发）
    payload["csrfToken"] = None
    return AuthMeOut.model_validate(payload)


@router.put("/active-workspace", response_model=AuthMeOut)
def active_workspace(
    body: ActiveWorkspaceUpdate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> AuthMeOut:
    """用途：将会话活动工作空间切换为已加入成员空间。"""
    session = db.get(AuthSessionRow, principal.session_id)
    if session is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": auth_service.CODE_AUTH_REQUIRED,
                "message": auth_service.MSG_AUTH_REQUIRED,
            },
        )
    try:
        updated = auth_service.set_active_workspace(
            db,
            session,
            user_id=principal.user_id,
            workspace_id=body.workspace_id,
        )
    except auth_service.AuthError as exc:
        _raise_auth(exc)
    payload = auth_service.serialize_me(updated)
    payload["csrfToken"] = None
    return AuthMeOut.model_validate(payload)
