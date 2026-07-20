"""
模块：P13-H1/H2 editor-state 事件游标与 SSE 路由
用途：GET /api/projects/{projectId}/editor-state-events（H1 游标页）；
  GET .../editor-state-events/stream（H2 项目级 SSE 与 Last-Event-ID 重放）。
对接：editor_state_event_service；schemas.EditorStateEvent*。
二次开发：
  - 不改公共 deps；成功/业务错误 Cache-Control: no-store；
  - 未知 query、重复参数、非法 limit/after、body 固定 422；
  - SSE 连接前短 Session 预检；生成器内 run_in_threadpool 新建/关闭 Session；
  - 禁止捕获 request-scope Session/ORM；禁止 WebSocket / 写方法。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.schemas import (
    EditorStateEventItemOut,
    EditorStateEventListOut,
)
from app.core.config import Settings, get_settings
from app.core.database import SessionLocal, get_db
from app.services import auth_service
from app.services.editor_state_event_service import (
    CODE_CURSOR_STALE,
    CODE_REQUEST_INVALID,
    MSG_CURSOR_STALE,
    MSG_REQUEST_INVALID,
    EditorStateEventError,
    list_editor_state_event_stream_page,
    list_editor_state_events,
    precheck_editor_state_event_stream,
)

router = APIRouter(prefix="/projects", tags=["editor-state-events"])

_CODE_WS_HEADER = "workspace_header_forbidden"
_MSG_WS_HEADER = "不允许通过请求头切换事件查询作用域"

_REQUEST_INVALID_DETAIL = {
    "code": CODE_REQUEST_INVALID,
    "message": MSG_REQUEST_INVALID,
}

_CODE_UNAVAILABLE = "editor_state_event_unavailable"
_MSG_UNAVAILABLE = "事件流暂时不可用"

_ALLOWED_QUERY = frozenset({"after", "limit"})
_ESE_RE = re.compile(r"^ese_[0-9a-f]{32}$")

# H2 SSE 可控时钟常量（测试可 monkeypatch）
_SSE_POLL_SECONDS = 0.25
_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_MAX_SECONDS = 11 * 60


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
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """
    用途：私有依赖——仅 required + 活动 workspace + 精确 bid_writer。
    规则：
      - 任何 X-Workspace-Id 头存在（含空）固定 403；
      - owner 不替代角色；不调用/修改公共 get_workspace_id；
      - 不注入 get_db：H2 SSE 不得持有 request-scope Session；
        H1 GET 仍由 handler 自身 Depends(get_db) 获取短请求 Session。
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


def _header_values(request: Request, name: str) -> list[str]:
    """用途：读取原始重复头；大小写不敏感。"""
    name_l = name.lower()
    raw = getattr(request.headers, "raw", None)
    if raw is not None:
        out: list[str] = []
        for key_b, val_b in raw:
            try:
                key = key_b.decode("latin-1")
                val = val_b.decode("latin-1")
            except Exception:
                continue
            if key.lower() == name_l:
                out.append(val)
        return out
    # 回落：单值
    val = request.headers.get(name)
    if val is None:
        return []
    return [val]


def _parse_last_event_id(request: Request) -> str | None:
    """
    用途：严格解析 Last-Event-ID——缺失或唯一合法 ese_；
    重复、空、空白、大小写/格式不合约固定 422。
    """
    values = _header_values(request, "last-event-id")
    if not values:
        return None
    if len(values) != 1:
        _raise_request_invalid()
    raw = values[0]
    if not isinstance(raw, str) or raw == "":
        _raise_request_invalid()
    # 禁止首尾空白与内部空白；必须精确 ese_ + 32 小写 hex
    if raw != raw.strip() or any(ch.isspace() for ch in raw):
        _raise_request_invalid()
    if not _ESE_RE.fullmatch(raw):
        _raise_request_invalid()
    return raw


def _json_data(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _sse_named_with_id(event_id: str, event: str, data: dict[str, Any]) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {_json_data(data)}\n\n"


def _sse_control(event: str, data: dict[str, Any]) -> str:
    """用途：无 id 的控制帧。"""
    return f"event: {event}\ndata: {_json_data(data)}\n\n"


def _sse_heartbeat() -> str:
    return ": heartbeat\n\n"


def _item_to_editor_data(item: dict[str, Any]) -> dict[str, Any]:
    """用途：服务层 snake → SSE 公开 camel 四键。"""
    return {
        "eventId": item["event_id"],
        "stateVersion": item["state_version"],
        "sourceKind": item["source_kind"],
        "occurredAt": item["occurred_at"],
    }


def _read_stream_page_sync(
    workspace_id: str,
    project_id: str,
    after: str | None,
) -> dict[str, Any]:
    """
    用途：线程池内短 Session 读一页；finally 关闭。
    禁止跨调用复用 Session。
    """
    db = SessionLocal()
    try:
        return list_editor_state_event_stream_page(
            db,
            workspace_id,
            project_id,
            after=after,
        )
    finally:
        db.close()


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


@router.get("/{project_id}/editor-state-events/stream")
async def stream_editor_state_events(
    project_id: str,
    request: Request,
    workspace_id: Annotated[str, Depends(require_editor_state_events_scope)],
) -> StreamingResponse:
    """
    用途：项目级 editor-state SSE；Last-Event-ID 重放；无 header 时 tip 锚点。
    二次开发：
      - 连接前 SessionLocal 短预检后关闭；生成器不捕获 request db；
      - 每轮 run_in_threadpool 新建/关闭 Session；积压连续排空 50 条页；
      - 心跳仅注释；stale/unavailable 无 id 控制帧；最大时限安静关闭。
    """
    # 拒绝任意 query
    if list(request.query_params.multi_items()):
        _raise_request_invalid()

    try:
        raw_body = await request.body()
    except Exception:
        _raise_request_invalid()
    if raw_body:
        _raise_request_invalid()

    last_event_id = _parse_last_event_id(request)

    # 连接前短 Session：项目 + 游标预检
    db = SessionLocal()
    try:
        watermark, send_cursor_anchor = precheck_editor_state_event_stream(
            db,
            workspace_id,
            project_id,
            last_event_id=last_event_id,
        )
    except EditorStateEventError as exc:
        _raise_event_error(exc)
    except Exception:
        raise HTTPException(
            status_code=500,
            detail=dict(_REQUEST_INVALID_DETAIL),
            headers={"Cache-Control": "no-store"},
        ) from None
    finally:
        db.close()

    # 固定到闭包的授权作用域字符串（禁止 Session/ORM）
    auth_workspace_id = workspace_id
    auth_project_id = project_id
    initial_watermark = watermark
    need_cursor = send_cursor_anchor

    async def event_stream():
        current: str | None = initial_watermark
        started_at = time.monotonic()
        last_activity_at = started_at
        try:
            if need_cursor and current is not None:
                yield _sse_named_with_id(
                    current,
                    "cursor",
                    {"eventId": current},
                )
                last_activity_at = time.monotonic()

            while True:
                if await request.is_disconnected():
                    return
                now = time.monotonic()
                if now - started_at >= _SSE_MAX_SECONDS:
                    # 安静关闭，无伪造事件
                    return

                # 连续排空页面
                drained_any = False
                try:
                    while True:
                        page = await run_in_threadpool(
                            _read_stream_page_sync,
                            auth_workspace_id,
                            auth_project_id,
                            current,
                        )
                        items = page["items"]
                        has_more = bool(page["has_more"])
                        if not items:
                            break
                        drained_any = True
                        for item in items:
                            data = _item_to_editor_data(item)
                            eid = data["eventId"]
                            yield _sse_named_with_id(
                                eid, "editor-state", data
                            )
                            # 每成功发送一条才推进水位
                            current = eid
                            last_activity_at = time.monotonic()
                        if not has_more:
                            break
                except EditorStateEventError as exc:
                    if exc.code == CODE_CURSOR_STALE:
                        yield _sse_control(
                            "cursor-stale",
                            {
                                "code": CODE_CURSOR_STALE,
                                "message": MSG_CURSOR_STALE,
                            },
                        )
                        return
                    yield _sse_control(
                        "unavailable",
                        {
                            "code": _CODE_UNAVAILABLE,
                            "message": _MSG_UNAVAILABLE,
                        },
                    )
                    return
                except Exception:
                    yield _sse_control(
                        "unavailable",
                        {
                            "code": _CODE_UNAVAILABLE,
                            "message": _MSG_UNAVAILABLE,
                        },
                    )
                    return

                now = time.monotonic()
                if now - last_activity_at >= _SSE_HEARTBEAT_SECONDS:
                    yield _sse_heartbeat()
                    last_activity_at = now

                if not drained_any:
                    await asyncio.sleep(_SSE_POLL_SECONDS)
                # 有积压刚排空时立即下一轮探测，避免额外固定 sleep
        except asyncio.CancelledError:
            return
        except Exception:
            # 流已开始则尽量发 unavailable；失败则安静结束
            try:
                yield _sse_control(
                    "unavailable",
                    {
                        "code": _CODE_UNAVAILABLE,
                        "message": _MSG_UNAVAILABLE,
                    },
                )
            except Exception:
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )
