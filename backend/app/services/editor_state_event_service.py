"""
模块：P13-H1 editor-state 事件只读查询服务
用途：在 workspace/project 作用域内按游标正序读取脱敏事件；
  after 缺失不回放历史，但已有事件时返回当前 tip 作为 next_cursor；
  游标失效固定 stale。
对接：api.editor_state_events；EditorStateEventRow；Project。
二次开发：
  - 禁止从修订表补洞；禁止返回 snapshot/actor/client/内部 ID；
  - 仅 flush 调用方事务外只读；limit 固定 1..50；
  - after 必须 ese_ + 32 位小写十六进制；
  - tip 取 (occurred_at DESC, id DESC) 最新一条合法 ese_ ID。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateEventRow, Project

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CURSOR_STALE = "editor_state_event_cursor_stale"
MSG_CURSOR_STALE = "事件游标已失效，请重新同步"
CODE_REQUEST_INVALID = "editor_state_event_request_invalid"
MSG_REQUEST_INVALID = "事件查询请求无效"

DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 50

_ESE_RE = re.compile(r"^ese_[0-9a-f]{32}$")
_SOURCE_OK = frozenset(
    {
        "browser_put",
        "task",
        "revise",
        "callback",
        "local_parser",
        "content_fuse_apply",
        "content_fuse_consume",
        "checkpoint_restore",
        "revision_restore",
    }
)


class EditorStateEventError(Exception):
    """用途：服务层固定错误，由路由映射 HTTP；禁止附带敏感细节。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _raise_invalid() -> None:
    raise EditorStateEventError(422, CODE_REQUEST_INVALID, MSG_REQUEST_INVALID)


def _raise_stale() -> None:
    raise EditorStateEventError(409, CODE_CURSOR_STALE, MSG_CURSOR_STALE)


def _raise_not_found() -> None:
    raise EditorStateEventError(404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND)


def _require_project(db: Session, workspace_id: str, project_id: str) -> Project:
    """用途：项目必须属于当前活动 workspace；跨空间/缺失统一 404。"""
    if not isinstance(project_id, str) or not project_id:
        _raise_not_found()
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        _raise_not_found()
    return project


def _normalize_limit(limit: Any) -> int:
    """用途：严格 limit 1..50；缺省 50；禁止 bool/浮点/字符串数字宽松解析。"""
    if limit is None:
        return DEFAULT_LIMIT
    if isinstance(limit, bool) or not isinstance(limit, int):
        _raise_invalid()
    if limit < MIN_LIMIT or limit > MAX_LIMIT:
        _raise_invalid()
    return limit


def _normalize_after(after: Any) -> str | None:
    """用途：after 可空；非空必须 ese_ + 32 小写 hex。"""
    if after is None:
        return None
    if not isinstance(after, str):
        _raise_invalid()
    if after == "":
        _raise_invalid()
    if not _ESE_RE.fullmatch(after):
        _raise_invalid()
    return after


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_occurred_at(value: datetime) -> str:
    """用途：固定 UTC 毫秒 Z 串，匹配契约 occurredAt。"""
    dt = _as_utc(value)
    ms = dt.microsecond // 1000
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}Z"


def _item_dict(row: EditorStateEventRow) -> dict[str, Any]:
    source = row.source_kind
    if not isinstance(source, str) or source not in _SOURCE_OK:
        # 库内脏数据：固定失败，禁止泄漏
        raise EditorStateEventError(
            500, CODE_REQUEST_INVALID, MSG_REQUEST_INVALID
        )
    return {
        "event_id": str(row.id),
        "state_version": str(row.state_version),
        "source_kind": source,
        "occurred_at": _format_occurred_at(row.occurred_at),
    }


def list_editor_state_events(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    after: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    """
    用途：只读列出 after 之后的事件（正序）；无 after 不回放历史。
    无 after 且已有事件时 items=[]/has_more=False，next_cursor 为 tip。
    返回：{items, next_cursor, has_more}；items 元素为服务层 snake 键。
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_not_found()
    _require_project(db, workspace_id, project_id)
    safe_after = _normalize_after(after)
    safe_limit = _normalize_limit(limit)

    # 缺失 after：从当前最新 tip 起不回放历史；有事件则返回 tip 游标供后续增量
    if safe_after is None:
        tip = db.execute(
            select(EditorStateEventRow.id)
            .where(
                EditorStateEventRow.workspace_id == workspace_id,
                EditorStateEventRow.project_id == project_id,
            )
            .order_by(
                EditorStateEventRow.occurred_at.desc(),
                EditorStateEventRow.id.desc(),
            )
            .limit(1)
        ).first()
        if tip is None:
            return {"items": [], "next_cursor": None, "has_more": False}
        return {
            "items": [],
            "next_cursor": str(tip.id),
            "has_more": False,
        }

    cursor = db.execute(
        select(
            EditorStateEventRow.id,
            EditorStateEventRow.occurred_at,
        ).where(
            EditorStateEventRow.workspace_id == workspace_id,
            EditorStateEventRow.project_id == project_id,
            EditorStateEventRow.id == safe_after,
        )
    ).first()
    if cursor is None:
        # 未知/已裁剪/跨项目（已限定 project）统一 stale
        _raise_stale()

    cursor_at = cursor.occurred_at
    cursor_id = str(cursor.id)

    rows = list(
        db.execute(
            select(EditorStateEventRow)
            .where(
                EditorStateEventRow.workspace_id == workspace_id,
                EditorStateEventRow.project_id == project_id,
                or_(
                    EditorStateEventRow.occurred_at > cursor_at,
                    and_(
                        EditorStateEventRow.occurred_at == cursor_at,
                        EditorStateEventRow.id > cursor_id,
                    ),
                ),
            )
            .order_by(
                EditorStateEventRow.occurred_at.asc(),
                EditorStateEventRow.id.asc(),
            )
            .limit(safe_limit + 1)
        )
        .scalars()
        .all()
    )

    has_more = len(rows) > safe_limit
    page = rows[:safe_limit]
    items = [_item_dict(r) for r in page]
    next_cursor = items[-1]["event_id"] if has_more and items else None
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": has_more,
    }
