"""
模块：P13-H1 editor-state 事件游标只读路由
用途：GET /api/projects/{projectId}/editor-state-events；
  required 活动 workspace strict bid_writer；任意 X-Workspace-Id 拒绝。
对接：editor_state_event_service；schemas.EditorStateEvent*。
二次开发：
  - 不改公共 deps；成功/业务错误 Cache-Control: no-store；
  - 未知 query、重复参数、非法 limit/after、body 固定 422；
  - 禁止 SSE / Last-Event-ID / WebSocket / 写方法。
"""

from __future__ import annotations

from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.api.schemas import (
    EditorStateEventItemOut,
    EditorStateEventListOut,
)
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.services import auth_service
from app.services.editor_state_event_service import (
    CODE_REQUEST_INVALID,
    MSG_REQUEST_INVALID,
    EditorStateEventError,
    list_editor_state_events,
)

router = APIRouter(prefix="/projects", tags=["editor-state-events"])

_CODE_WS_HEADER = "workspace_header_forbidden"
_MSG_WS_HEADER = "不允许通过请求头切换事件查询作用域"

_REQUEST_INVALID_DETAIL = {
    "code": CODE_REQUEST_INVALID,
    "message": MSG_REQUEST_INVALID,
}

_ALLOWED_QUERY = frozenset({"after", "limit"})


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _raise_request_invalid() -> NoReturn:
    raise HTTPException(
        status_code=422,
        detail=dict(_REQUEST_INVALID_DETAIL),
        headers={"Cache-Control": "no-store"},
    ) from None


def _raise_event_error(exc: EditorStateEventError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail=exc.as_detail(),
        headers={"Cache-Control": "no-store"},
    ) from None


def require_editor_state_events_scope(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """
    用途：私有依赖——仅 required + 活动 workspace + 精确 bid_writer。
    规则：
      - 任何 X-Workspace-Id 头存在（含空）固定 403；
      - owner 不替代角色；不调用/修改公共 get_workspace_id；
    返回：workspace_id
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
    return active_ws


def _parse_query(request: Request) -> tuple[str | None, int | None]:
    """
    用途：严格解析 after/limit；未知键、重复键、空 limit 固定 422。
    二次开发：禁止把 query 原文写入 detail。
    """
    raw_pairs = list(request.query_params.multi_items())
    seen: set[str] = set()
    values: dict[str, str] = {}
    for key, value in raw_pairs:
        if key not in _ALLOWED_QUERY:
            _raise_request_invalid()
        if key in seen:
            _raise_request_invalid()
        seen.add(key)
        values[key] = value

    after: str | None = None
    if "after" in values:
        after = values["after"]

    limit: int | None = None
    if "limit" in values:
        raw_limit = values["limit"]
        if not isinstance(raw_limit, str) or raw_limit.strip() != raw_limit:
            _raise_request_invalid()
        if not raw_limit.isdigit():
            _raise_request_invalid()
        # 禁止前导零宽松（除 "0" 本身将在范围门失败）
        if len(raw_limit) > 1 and raw_limit.startswith("0"):
            _raise_request_invalid()
        try:
            limit = int(raw_limit)
        except ValueError:
            _raise_request_invalid()

    return after, limit


@router.get(
    "/{project_id}/editor-state-events",
    response_model=EditorStateEventListOut,
)
async def get_editor_state_events(
    project_id: str,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    workspace_id: Annotated[str, Depends(require_editor_state_events_scope)],
) -> EditorStateEventListOut:
    """
    用途：只读事件游标页；精确三顶层键 + items 四键。
    二次开发：全程 no-store；不回显 projectId/after/正文。
    """
    _no_store(response)
    # GET 禁止任何 body（含 content-length>0 或实际字节）
    try:
        raw_body = await request.body()
    except Exception:
        _raise_request_invalid()
    if raw_body:
        _raise_request_invalid()

    after, limit = _parse_query(request)
    try:
        data = list_editor_state_events(
            db,
            workspace_id,
            project_id,
            after=after,
            limit=limit,
        )
    except EditorStateEventError as exc:
        _raise_event_error(exc)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=dict(_REQUEST_INVALID_DETAIL),
            headers={"Cache-Control": "no-store"},
        ) from None

    items = [
        EditorStateEventItemOut(
            event_id=it["event_id"],
            state_version=it["state_version"],
            source_kind=it["source_kind"],
            occurred_at=it["occurred_at"],
        )
        for it in data["items"]
    ]
    return EditorStateEventListOut(
        items=items,
        next_cursor=data["next_cursor"],
        has_more=data["has_more"],
    )
