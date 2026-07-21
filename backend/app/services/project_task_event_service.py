"""
模块：P13-I1 项目任务事件只读查询服务
用途：在 workspace/project 作用域内按游标正序读取脱敏任务状态事件；
  after 缺失不回放历史，但已有事件时返回当前 tip 作为 next_cursor；
  游标失效固定 stale。
对接：api.project_task_events；ProjectTaskEventRow；Project。
二次开发：
  - 禁止从 project_tasks 补洞；禁止返回 message/error/result/payload/actor/client；
  - 仅 flush 调用方事务外只读；limit 固定 1..50；
  - after 必须 pte_ + 32 位小写十六进制；
  - tip 取 (occurred_at DESC, id DESC) 最新一条合法 pte_ ID。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.entities import Project, ProjectTaskEventRow

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CURSOR_STALE = "project_task_event_cursor_stale"
MSG_CURSOR_STALE = "事件游标已失效，请重新同步"
CODE_REQUEST_INVALID = "project_task_event_request_invalid"
MSG_REQUEST_INVALID = "事件查询请求无效"

DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 50

_PTE_RE = re.compile(r"^pte_[0-9a-f]{32}$")
_STATUS_OK = frozenset({"pending", "running", "success", "failed", "cancelled"})


class ProjectTaskEventError(Exception):
    """用途：服务层固定错误，由路由映射 HTTP；禁止附带敏感细节。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _raise_invalid() -> None:
    raise ProjectTaskEventError(422, CODE_REQUEST_INVALID, MSG_REQUEST_INVALID)


def _raise_stale() -> None:
    raise ProjectTaskEventError(409, CODE_CURSOR_STALE, MSG_CURSOR_STALE)


def _raise_not_found() -> None:
    raise ProjectTaskEventError(404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND)


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
    """用途：after 可空；非空必须 pte_ + 32 小写 hex。"""
    if after is None:
        return None
    if not isinstance(after, str):
        _raise_invalid()
    if after == "":
        _raise_invalid()
    if not _PTE_RE.fullmatch(after):
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


def _item_dict(row: ProjectTaskEventRow) -> dict[str, Any]:
    status = row.status
    if not isinstance(status, str) or status not in _STATUS_OK:
        raise ProjectTaskEventError(
            500, CODE_REQUEST_INVALID, MSG_REQUEST_INVALID
        )
    progress = int(row.progress)
    if progress < 0 or progress > 100:
        raise ProjectTaskEventError(
            500, CODE_REQUEST_INVALID, MSG_REQUEST_INVALID
        )
    return {
        "event_id": str(row.id),
        "task_id": str(row.task_id),
        "task_type": str(row.task_type),
        "status": status,
        "progress": progress,
        "occurred_at": _format_occurred_at(row.occurred_at),
    }


def list_project_task_events(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    after: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    """
    用途：只读列出 after 之后的任务事件（正序）；无 after 不回放历史。
    无 after 且已有事件时 items=[]/has_more=False，next_cursor 为 tip。
    返回：{items, next_cursor, has_more}；items 元素为服务层 snake 键。
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_not_found()
    _require_project(db, workspace_id, project_id)
    safe_after = _normalize_after(after)
    safe_limit = _normalize_limit(limit)

    if safe_after is None:
        tip = db.execute(
            select(ProjectTaskEventRow.id)
            .where(
                ProjectTaskEventRow.workspace_id == workspace_id,
                ProjectTaskEventRow.project_id == project_id,
            )
            .order_by(
                ProjectTaskEventRow.occurred_at.desc(),
                ProjectTaskEventRow.id.desc(),
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
            ProjectTaskEventRow.id,
            ProjectTaskEventRow.occurred_at,
        ).where(
            ProjectTaskEventRow.workspace_id == workspace_id,
            ProjectTaskEventRow.project_id == project_id,
            ProjectTaskEventRow.id == safe_after,
        )
    ).first()
    if cursor is None:
        _raise_stale()

    cursor_at = cursor.occurred_at
    cursor_id = str(cursor.id)

    rows = list(
        db.execute(
            select(ProjectTaskEventRow)
            .where(
                ProjectTaskEventRow.workspace_id == workspace_id,
                ProjectTaskEventRow.project_id == project_id,
                or_(
                    ProjectTaskEventRow.occurred_at > cursor_at,
                    and_(
                        ProjectTaskEventRow.occurred_at == cursor_at,
                        ProjectTaskEventRow.id > cursor_id,
                    ),
                ),
            )
            .order_by(
                ProjectTaskEventRow.occurred_at.asc(),
                ProjectTaskEventRow.id.asc(),
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


def _latest_tip_id(
    db: Session, workspace_id: str, project_id: str
) -> str | None:
    """用途：当前项目保留窗口内最新事件 ID；无事件返回 None。"""
    tip = db.execute(
        select(ProjectTaskEventRow.id)
        .where(
            ProjectTaskEventRow.workspace_id == workspace_id,
            ProjectTaskEventRow.project_id == project_id,
        )
        .order_by(
            ProjectTaskEventRow.occurred_at.desc(),
            ProjectTaskEventRow.id.desc(),
        )
        .limit(1)
    ).first()
    if tip is None:
        return None
    return str(tip.id)


def precheck_project_task_event_stream(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    last_event_id: str | None,
) -> tuple[str | None, bool]:
    """
    用途：连接前短 Session 预检项目与可选 Last-Event-ID。
    返回：(watermark, send_cursor_anchor)
      - 有 last_event_id：校验仍保留后作为水位，不发锚点；
      - 无 header 且有 tip：水位=tip，需先发 cursor 锚点；
      - 无 header 且空表：水位=None，从空起点等待。
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_not_found()
    _require_project(db, workspace_id, project_id)

    if last_event_id is not None:
        safe = _normalize_after(last_event_id)
        assert safe is not None
        cursor = db.execute(
            select(ProjectTaskEventRow.id).where(
                ProjectTaskEventRow.workspace_id == workspace_id,
                ProjectTaskEventRow.project_id == project_id,
                ProjectTaskEventRow.id == safe,
            )
        ).first()
        if cursor is None:
            _raise_stale()
        return safe, False

    tip = _latest_tip_id(db, workspace_id, project_id)
    if tip is None:
        return None, False
    return tip, True


def list_project_task_event_stream_page(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    after: str | None,
    limit: Any = None,
) -> dict[str, Any]:
    """
    用途：I2 流内短 Session 读取一页保留事件（正序）。
    - after 有值：返回其后最多 limit 条；未知/裁剪/跨项目统一 409；
    - after 为 None：空水位起点，从最早保留事件读取。
    返回：{items, has_more}；items 为服务层 snake 六键。
    """
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_not_found()
    _require_project(db, workspace_id, project_id)
    safe_limit = _normalize_limit(limit if limit is not None else MAX_LIMIT)

    if after is None:
        rows = list(
            db.execute(
                select(ProjectTaskEventRow)
                .where(
                    ProjectTaskEventRow.workspace_id == workspace_id,
                    ProjectTaskEventRow.project_id == project_id,
                )
                .order_by(
                    ProjectTaskEventRow.occurred_at.asc(),
                    ProjectTaskEventRow.id.asc(),
                )
                .limit(safe_limit + 1)
            )
            .scalars()
            .all()
        )
        has_more = len(rows) > safe_limit
        page = rows[:safe_limit]
        return {
            "items": [_item_dict(r) for r in page],
            "has_more": has_more,
        }

    safe_after = _normalize_after(after)
    assert safe_after is not None
    cursor = db.execute(
        select(
            ProjectTaskEventRow.id,
            ProjectTaskEventRow.occurred_at,
        ).where(
            ProjectTaskEventRow.workspace_id == workspace_id,
            ProjectTaskEventRow.project_id == project_id,
            ProjectTaskEventRow.id == safe_after,
        )
    ).first()
    if cursor is None:
        _raise_stale()

    cursor_at = cursor.occurred_at
    cursor_id = str(cursor.id)
    rows = list(
        db.execute(
            select(ProjectTaskEventRow)
            .where(
                ProjectTaskEventRow.workspace_id == workspace_id,
                ProjectTaskEventRow.project_id == project_id,
                or_(
                    ProjectTaskEventRow.occurred_at > cursor_at,
                    and_(
                        ProjectTaskEventRow.occurred_at == cursor_at,
                        ProjectTaskEventRow.id > cursor_id,
                    ),
                ),
            )
            .order_by(
                ProjectTaskEventRow.occurred_at.asc(),
                ProjectTaskEventRow.id.asc(),
            )
            .limit(safe_limit + 1)
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > safe_limit
    page = rows[:safe_limit]
    return {
        "items": [_item_dict(r) for r in page],
        "has_more": has_more,
    }
