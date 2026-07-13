"""
模块：P10A 认证中间件
用途：auth_mode=required 时统一拦截 /api；校验会话 Cookie，变更请求校验 CSRF。
对接：main.create_app；auth_service；request.state.auth_principal / auth_session。
二次开发：
  - 仅放行健康检查与明确列出的公开 auth 端点
  - disabled 模式直接放行，保持个人版兼容
  - 禁止在日志中输出 Cookie、CSRF 或口令
"""

from __future__ import annotations

import json
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services import auth_service

# 公开路径：无需会话（精确匹配）
_PUBLIC_EXACT = frozenset(
    {
        "/api/health",
        "/api/auth/bootstrap-status",
        "/api/auth/login",
    }
)

# 无需 CSRF 的方法
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
    )


class AuthMiddleware(BaseHTTPMiddleware):
    """
    用途：required 模式下的会话与 CSRF 闸门。
    将脱敏主体写入 request.state.auth_principal，会话行写入 request.state.auth_session。
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        # 非 API 或不强制认证：直接放行
        path = request.url.path
        if not settings.is_auth_required():
            return await call_next(request)
        if not path.startswith("/api"):
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        if path in _PUBLIC_EXACT:
            return await call_next(request)

        db = SessionLocal()
        try:
            if not auth_service.is_bootstrapped(db):
                return _error_response(
                    503,
                    auth_service.CODE_NOT_BOOTSTRAPPED,
                    auth_service.MSG_NOT_BOOTSTRAPPED,
                )

            raw_token = request.cookies.get(settings.auth_cookie_name) or ""
            try:
                session, user, members = auth_service.load_session_by_raw_token(
                    db, settings, raw_token, touch=True
                )
            except auth_service.AuthError as exc:
                return _error_response(exc.status_code, exc.code, exc.message)

            # 变更请求必须带 CSRF（login 已在公开白名单）
            if request.method not in _SAFE_METHODS:
                raw_csrf = request.headers.get(settings.auth_csrf_header_name)
                try:
                    auth_service.verify_csrf(session, raw_csrf)
                except auth_service.AuthError as exc:
                    auth_service.record_audit(
                        db,
                        action="csrf_check",
                        result="invalid",
                        actor_user_id=user.id,
                        workspace_id=session.active_workspace_id,
                        target=request.method,
                    )
                    return _error_response(exc.status_code, exc.code, exc.message)

            principal = auth_service.principal_from_session(
                session, user, members
            )
            request.state.auth_principal = principal
            request.state.auth_session = session
            request.state.auth_db_user_id = user.id
        finally:
            db.close()

        return await call_next(request)


def auth_error_detail(exc: auth_service.AuthError) -> dict:
    """用途：路由层统一 detail 形状。"""
    return exc.as_detail()


def dumps_safe(data: object) -> str:
    """用途：调试辅助，默认 ensure_ascii。"""
    return json.dumps(data, ensure_ascii=False)
