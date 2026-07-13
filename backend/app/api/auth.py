"""
模块：P10A 认证路由
用途：引导状态、登录/登出、当前身份、活动工作空间切换、成员管理。
对接：auth_service；auth_middleware；deps.require_owner；前端后续 AuthProvider。
二次开发：禁止公开注册与密码找回；响应仅脱敏字段；成员 API 仅所有者。
  登录与创建成员必须手工安全解析请求体，禁止 FastAPI/Pydantic 默认 422 回显口令等原始输入。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, NoReturn, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.api.deps import require_owner
from app.api.schemas import (
    ActiveWorkspaceUpdate,
    AuthBootstrapStatusOut,
    AuthLoginRequest,
    AuthMeOut,
    AuthMemberCreate,
    AuthMemberOut,
    AuthMemberUpdate,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import AuthSessionRow
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

TModel = TypeVar("TModel", bound=BaseModel)

# 认证请求体校验失败的固定脱敏 detail；不得拼接任何原始输入
_AUTH_REQUEST_INVALID_DETAIL = {
    "code": auth_service.CODE_BAD_REQUEST,
    "message": "请求参数无效",
}


def _raise_auth(exc: auth_service.AuthError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=exc.as_detail()) from None


def _raise_auth_request_invalid() -> NoReturn:
    """用途：认证路由专用；校验失败时返回固定中文，绝不回显原始请求值。"""
    raise HTTPException(status_code=400, detail=dict(_AUTH_REQUEST_INVALID_DETAIL)) from None


async def _read_json_object(request: Request) -> dict[str, Any]:
    """
    用途：仅认证敏感路由读取 JSON 对象；非对象/非法 JSON 一律固定错误。
    二次开发：禁止把解析异常信息或 body 片段写入 detail。
    """
    try:
        raw = await request.body()
    except Exception:
        _raise_auth_request_invalid()
    if not raw:
        _raise_auth_request_invalid()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        _raise_auth_request_invalid()
    if not isinstance(data, dict):
        _raise_auth_request_invalid()
    return data


def _parse_auth_model(model_cls: type[TModel], data: dict[str, Any]) -> TModel:
    """用途：将 JSON 对象校验为认证模型；失败时固定脱敏，不暴露 loc/input。"""
    try:
        return model_cls.model_validate(data)
    except ValidationError:
        _raise_auth_request_invalid()

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
async def login(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthMeOut:
    """
    用途：公开；成功后设置会话 Cookie，正文返回脱敏身份与 CSRF 原始值。
    二次开发：请求体必须经 _read_json_object/_parse_auth_model，禁止 body: AuthLoginRequest 直绑。
    """
    body = _parse_auth_model(AuthLoginRequest, await _read_json_object(request))
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


@router.get("/members", response_model=list[AuthMemberOut])
def list_members(
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_owner)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> list[AuthMemberOut]:
    """用途：仅当前工作空间所有者；返回脱敏成员列表。"""
    try:
        auth_service.assert_principal_is_owner(principal, workspace_id)
        rows = auth_service.list_workspace_members(db, workspace_id)
    except auth_service.AuthError as exc:
        _raise_auth(exc)
    return [AuthMemberOut.model_validate(r) for r in rows]


@router.post("/members", response_model=AuthMemberOut, status_code=201)
async def create_member(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    workspace_id: Annotated[str, Depends(require_owner)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> AuthMemberOut:
    """
    用途：仅所有者创建本机用户并加入当前空间；禁止公开注册。
    二次开发：请求体必须经安全解析，畸形 password/role 不得走默认 422 回显。
    """
    body = _parse_auth_model(AuthMemberCreate, await _read_json_object(request))
    try:
        auth_service.assert_principal_is_owner(principal, workspace_id)
        payload = auth_service.create_workspace_member(
            db,
            settings,
            workspace_id=workspace_id,
            actor_user_id=principal.user_id,
            username=body.username,
            password=body.password,
            role=body.role,
            is_owner=bool(body.is_owner),
        )
    except auth_service.AuthError as exc:
        _raise_auth(exc)
    return AuthMemberOut.model_validate(payload)


@router.patch("/members/{user_id}", response_model=AuthMemberOut)
def patch_member(
    user_id: str,
    body: AuthMemberUpdate,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_owner)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> AuthMemberOut:
    """用途：仅所有者最小更新 role/isOwner/isActive；保护最后活跃所有者。"""
    try:
        auth_service.assert_principal_is_owner(principal, workspace_id)
        payload = auth_service.update_workspace_member(
            db,
            workspace_id=workspace_id,
            target_user_id=user_id,
            actor_user_id=principal.user_id,
            role=body.role,
            is_owner=body.is_owner,
            is_active=body.is_active,
        )
    except auth_service.AuthError as exc:
        _raise_auth(exc)
    return AuthMemberOut.model_validate(payload)


@router.delete("/members/{user_id}", status_code=204)
def delete_member(
    user_id: str,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_owner)],
    principal: Annotated[auth_service.AuthPrincipal, Depends(require_principal)],
) -> None:
    """用途：仅所有者移除当前空间成员；最后活跃所有者不可移除；撤销其会话。"""
    try:
        auth_service.assert_principal_is_owner(principal, workspace_id)
        auth_service.remove_workspace_member(
            db,
            workspace_id=workspace_id,
            target_user_id=user_id,
            actor_user_id=principal.user_id,
        )
    except auth_service.AuthError as exc:
        _raise_auth(exc)
