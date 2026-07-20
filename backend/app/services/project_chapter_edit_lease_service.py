"""
模块：P13-G1 项目章节编辑意图租约服务
用途：单事务完成项目写锁、技术标章节精确命中、过期清理、8 章节上限、
  单章节单持有者 heartbeat 与五维精确 leave。
对接：api.project_chapter_edit_leases；ProjectChapterEditLeaseRow；
  ProjectEditorStateRow.chapters_json。
二次开发：
  - service 只 flush 不 commit；失败由路由 rollback；
  - 禁止日志/响应原始 clientId/chapterId；禁止后台 timer；禁止强制锁语义；
  - 锁后才采样 now；同用户不同 client 冲突；失效 holder 可接管。
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import (
    LocalUserRow,
    Project,
    ProjectChapterEditLeaseRow,
    ProjectEditorStateRow,
    WorkspaceMemberRow,
)
from app.services import auth_service

# 固定契约常量
LEASE_TTL_SECONDS = 45
REFRESH_AFTER_SECONDS = 15
MAX_CHAPTERS_PER_USER_PROJECT = 8

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CHAPTER_NOT_FOUND = "chapter_not_found"
MSG_CHAPTER_NOT_FOUND = "章节不存在"
CODE_CHAPTER_STATE_INVALID = "chapter_state_invalid"
MSG_CHAPTER_STATE_INVALID = "章节状态不可用"
CODE_LEASE_CONFLICT = "chapter_edit_lease_conflict"
MSG_LEASE_CONFLICT = "此章节近期已有处理意图"
CODE_LEASE_LIMIT = "chapter_edit_lease_limit"
MSG_LEASE_LIMIT = "当前项目章节处理意图数量已达上限"
CODE_LEASE_FAILED = "chapter_edit_lease_failed"
MSG_LEASE_FAILED = "章节编辑意图处理失败"

# 与 P13-D2/P13-F1 同等级：行分隔 + 双向控制
_BIDI_AND_LINE_CONTROLS: frozenset[str] = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u2028",
        "\u2029",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)


class ProjectChapterEditLeaseError(Exception):
    """用途：服务层固定错误，由路由映射 HTTP；禁止附带敏感细节。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        holder_username: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.holder_username = holder_username

    def as_detail(self) -> dict[str, str]:
        detail: dict[str, str] = {"code": self.code, "message": self.message}
        if self.holder_username is not None:
            detail["holderUsername"] = self.holder_username
        return detail


@dataclass(frozen=True)
class ChapterEditLeaseHeartbeatResult:
    """用途：心跳服务结果；路由序列化为精确两键。"""

    lease_expires_at: datetime
    refresh_after_seconds: int


def _utc_now() -> datetime:
    """用途：服务端 UTC 时钟；必须在写锁之后采样。"""
    return datetime.now(timezone.utc)


def _new_lease_id() -> str:
    """用途：不透明租约主键，最多 64 字符。"""
    return f"pcel_{secrets.token_hex(16)}"


def digest_client_id(client_id: str) -> str:
    """用途：规范 clientId → SHA-256 小写十六进制摘要；禁止落库原文。"""
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()


def _safe_username(value: Any) -> str | None:
    """
    用途：P13-D2 同等级严格用户名安全文本门。
    规则：原生 str、1..100 Unicode 码点、无首尾空白；拒绝 C0/C1/DEL、
      U+2028/U+2029 与约定双向控制；不 trim、不 NFKC、不改写。
    """
    if not isinstance(value, str):
        return None
    n = len(value)
    if n < 1 or n > 100:
        return None
    if value.strip() != value:
        return None
    for ch in value:
        o = ord(ch)
        if o < 0x20 or o == 0x7F or (0x80 <= o <= 0x9F):
            return None
        if ch in _BIDI_AND_LINE_CONTROLS:
            return None
    return value


def _as_utc(value: datetime) -> datetime:
    """用途：将库内时间统一为 aware UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _acquire_project_write_lock(db: Session, project_id: str) -> None:
    """
    用途：在任何项目/章节判断、过期清理、计数、冲突与写入前取得项目级写锁。
    规则：
      - SQLite：对当前项目行无值变化 UPDATE，升级可等待写锁；
      - 非 SQLite：SELECT projects ... FOR UPDATE；
      - 禁止进程锁/GIL 冒充数据库并发；锁后调用方才采样 now。
    """
    dialect = getattr(db.get_bind().dialect, "name", None)
    if dialect == "sqlite":
        db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(updated_at=Project.updated_at)
        )
        return
    db.execute(
        select(Project).where(Project.id == project_id).with_for_update()
    ).scalar_one_or_none()


def _require_technical_project(
    db: Session, workspace_id: str, project_id: str
) -> Project:
    """用途：项目必须属于当前活动 workspace 且 kind=technical；否则统一 404。"""
    project = db.get(Project, project_id)
    if (
        project is None
        or project.workspace_id != workspace_id
        or project.kind != "technical"
    ):
        raise ProjectChapterEditLeaseError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )
    return project


def _require_project_scope(
    db: Session, workspace_id: str, project_id: str
) -> Project:
    """
    用途：leave 路径项目作用域重验（含 technical 边界）。
    说明：跨空间/缺失/非 technical 统一 404，避免泄漏存在性。
    """
    return _require_technical_project(db, workspace_id, project_id)


def _load_chapters_raw(db: Session, project_id: str) -> Any:
    """用途：读取权威 chapters_json；无状态返回 None。"""
    row = db.get(ProjectEditorStateRow, project_id)
    if row is None or row.chapters_json is None:
        return None
    try:
        return json.loads(row.chapters_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _require_chapter_unique_hit(db: Session, project_id: str, chapter_id: str) -> None:
    """
    用途：目标 chapterId 必须在当前章节数组的字典项中以原生字符串精确出现一次。
    规则：无状态/非数组/缺失 → chapter_not_found；重复 → chapter_state_invalid；
      禁止 trim/NFKC/大小写折叠/标题回退。
    """
    raw = _load_chapters_raw(db, project_id)
    if raw is None or not isinstance(raw, list):
        raise ProjectChapterEditLeaseError(
            404, CODE_CHAPTER_NOT_FOUND, MSG_CHAPTER_NOT_FOUND
        )
    hits = 0
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if type(cid) is str and cid == chapter_id:
            hits += 1
    if hits == 0:
        raise ProjectChapterEditLeaseError(
            404, CODE_CHAPTER_NOT_FOUND, MSG_CHAPTER_NOT_FOUND
        )
    if hits > 1:
        raise ProjectChapterEditLeaseError(
            409, CODE_CHAPTER_STATE_INVALID, MSG_CHAPTER_STATE_INVALID
        )


def _require_actor_safe(
    db: Session, *, workspace_id: str, actor_user_id: str
) -> str:
    """
    用途：重验当前 actor 启用用户/成员/bid_writer 与安全用户名。
    返回：安全用户名；失败固定 role_forbidden，零租约。
    """
    user = db.get(LocalUserRow, actor_user_id)
    if user is None or not user.is_active:
        raise ProjectChapterEditLeaseError(
            403,
            auth_service.CODE_ROLE_FORBIDDEN,
            auth_service.MSG_ROLE_FORBIDDEN,
        )
    safe = _safe_username(user.username)
    if safe is None:
        raise ProjectChapterEditLeaseError(
            403,
            auth_service.CODE_ROLE_FORBIDDEN,
            auth_service.MSG_ROLE_FORBIDDEN,
        )
    member = db.execute(
        select(WorkspaceMemberRow).where(
            WorkspaceMemberRow.workspace_id == workspace_id,
            WorkspaceMemberRow.user_id == actor_user_id,
            WorkspaceMemberRow.is_active.is_(True),
            WorkspaceMemberRow.role == auth_service.ROLE_BID_WRITER,
        )
    ).scalar_one_or_none()
    if member is None:
        raise ProjectChapterEditLeaseError(
            403,
            auth_service.CODE_ROLE_FORBIDDEN,
            auth_service.MSG_ROLE_FORBIDDEN,
        )
    return safe


def _resolve_valid_holder_username(
    db: Session, *, workspace_id: str, user_id: str
) -> str | None:
    """
    用途：冲突前重验 holder 启用用户/成员/角色与安全用户名。
    返回：安全用户名；失效则 None（租约视为陈旧可接管）。
    """
    user = db.get(LocalUserRow, user_id)
    if user is None or not user.is_active:
        return None
    safe = _safe_username(user.username)
    if safe is None:
        return None
    member = db.execute(
        select(WorkspaceMemberRow).where(
            WorkspaceMemberRow.workspace_id == workspace_id,
            WorkspaceMemberRow.user_id == user_id,
            WorkspaceMemberRow.is_active.is_(True),
            WorkspaceMemberRow.role == auth_service.ROLE_BID_WRITER,
        )
    ).scalar_one_or_none()
    if member is None:
        return None
    return safe


def _purge_expired_for_project(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    now: datetime,
) -> None:
    """用途：机会性清理当前项目已过期租约；不启动后台线程。"""
    rows = db.execute(
        select(ProjectChapterEditLeaseRow).where(
            ProjectChapterEditLeaseRow.workspace_id == workspace_id,
            ProjectChapterEditLeaseRow.project_id == project_id,
        )
    ).scalars().all()
    for row in rows:
        if _as_utc(row.expires_at) <= now:
            db.delete(row)
    db.flush()


def _count_active_chapters_for_user(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    user_id: str,
    now: datetime,
) -> int:
    """用途：统计用户在本项目未过期的活动章节租约数。"""
    rows = db.execute(
        select(ProjectChapterEditLeaseRow).where(
            ProjectChapterEditLeaseRow.workspace_id == workspace_id,
            ProjectChapterEditLeaseRow.project_id == project_id,
            ProjectChapterEditLeaseRow.user_id == user_id,
        )
    ).scalars().all()
    return sum(1 for r in rows if _as_utc(r.expires_at) > now)


def _find_chapter_lease(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    chapter_id: str,
) -> ProjectChapterEditLeaseRow | None:
    """用途：按 (workspace, project, chapter) 查找单持有者租约。"""
    return db.execute(
        select(ProjectChapterEditLeaseRow).where(
            ProjectChapterEditLeaseRow.workspace_id == workspace_id,
            ProjectChapterEditLeaseRow.project_id == project_id,
            ProjectChapterEditLeaseRow.chapter_id == chapter_id,
        )
    ).scalar_one_or_none()


def _create_lease_row(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    chapter_id: str,
    user_id: str,
    client_digest: str,
    now: datetime,
    expires_at: datetime,
) -> ProjectChapterEditLeaseRow:
    """用途：新建章节租约行；并发唯一键冲突转为冲突语义。"""
    row = ProjectChapterEditLeaseRow(
        id=_new_lease_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=user_id,
        client_digest=client_digest,
        last_seen_at=now,
        expires_at=expires_at,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError as exc:
        # 并发抢占：重查并按现有持有者处理
        raced = _find_chapter_lease(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            chapter_id=chapter_id,
        )
        if raced is None:
            raise ProjectChapterEditLeaseError(
                500, CODE_LEASE_FAILED, MSG_LEASE_FAILED
            ) from exc
        if (
            raced.user_id == user_id
            and raced.client_digest == client_digest
        ):
            raced.last_seen_at = now
            raced.expires_at = expires_at
            db.flush()
            return raced
        holder = _resolve_valid_holder_username(
            db, workspace_id=workspace_id, user_id=raced.user_id
        )
        if holder is None:
            db.delete(raced)
            db.flush()
            # 陈旧 holder：再试一次插入
            retry = ProjectChapterEditLeaseRow(
                id=_new_lease_id(),
                workspace_id=workspace_id,
                project_id=project_id,
                chapter_id=chapter_id,
                user_id=user_id,
                client_digest=client_digest,
                last_seen_at=now,
                expires_at=expires_at,
            )
            try:
                with db.begin_nested():
                    db.add(retry)
                    db.flush()
                return retry
            except IntegrityError as exc2:
                raise ProjectChapterEditLeaseError(
                    500, CODE_LEASE_FAILED, MSG_LEASE_FAILED
                ) from exc2
        raise ProjectChapterEditLeaseError(
            409,
            CODE_LEASE_CONFLICT,
            MSG_LEASE_CONFLICT,
            holder_username=holder,
        ) from None


def heartbeat_chapter_edit_lease(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    client_id: str,
    chapter_id: str,
) -> ChapterEditLeaseHeartbeatResult:
    """
    用途：心跳主入口；锁后重验/清理/限额/续期/冲突/接管。
    对接：POST .../chapter-edit-lease/heartbeat；不 commit。
    时钟：必须先取得项目写锁，再取唯一 UTC now/expires_at。
    """
    _acquire_project_write_lock(db, project_id)
    now = _utc_now()
    expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)

    _require_technical_project(db, workspace_id, project_id)
    _require_chapter_unique_hit(db, project_id, chapter_id)
    _require_actor_safe(db, workspace_id=workspace_id, actor_user_id=actor_user_id)
    _purge_expired_for_project(
        db, workspace_id=workspace_id, project_id=project_id, now=now
    )

    digest = digest_client_id(client_id)
    existing = _find_chapter_lease(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        chapter_id=chapter_id,
    )

    if existing is not None and _as_utc(existing.expires_at) > now:
        # 同 user + 同 digest → 原行续期
        if (
            existing.user_id == actor_user_id
            and existing.client_digest == digest
        ):
            existing.last_seen_at = now
            existing.expires_at = expires_at
            db.flush()
            return ChapterEditLeaseHeartbeatResult(
                lease_expires_at=expires_at,
                refresh_after_seconds=REFRESH_AFTER_SECONDS,
            )

        holder = _resolve_valid_holder_username(
            db, workspace_id=workspace_id, user_id=existing.user_id
        )
        if holder is not None:
            # 活动冲突：同用户不同 client 或其它用户
            raise ProjectChapterEditLeaseError(
                409,
                CODE_LEASE_CONFLICT,
                MSG_LEASE_CONFLICT,
                holder_username=holder,
            )
        # 失效 holder：删除后接管
        db.delete(existing)
        db.flush()
        existing = None

    # 无活动租约：新建（含过期行复用/删除后接管）
    if existing is not None:
        # 已过期行（purge 漏网）：覆写为当前 actor
        existing.user_id = actor_user_id
        existing.client_digest = digest
        existing.last_seen_at = now
        existing.expires_at = expires_at
        db.flush()
        return ChapterEditLeaseHeartbeatResult(
            lease_expires_at=expires_at,
            refresh_after_seconds=REFRESH_AFTER_SECONDS,
        )

    active_n = _count_active_chapters_for_user(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor_user_id,
        now=now,
    )
    if active_n >= MAX_CHAPTERS_PER_USER_PROJECT:
        raise ProjectChapterEditLeaseError(429, CODE_LEASE_LIMIT, MSG_LEASE_LIMIT)

    _create_lease_row(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        chapter_id=chapter_id,
        user_id=actor_user_id,
        client_digest=digest,
        now=now,
        expires_at=expires_at,
    )
    db.flush()
    return ChapterEditLeaseHeartbeatResult(
        lease_expires_at=expires_at,
        refresh_after_seconds=REFRESH_AFTER_SECONDS,
    )


def leave_chapter_edit_lease(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    client_id: str,
    chapter_id: str,
) -> None:
    """
    用途：五维精确删除 (workspace, project, chapter, actor, client digest)；
      幂等；章节已删除仍可清理；不误删其它 client/章节/用户/项目。
    对接：POST .../chapter-edit-lease/leave；不 commit。
    """
    _acquire_project_write_lock(db, project_id)
    _require_project_scope(db, workspace_id, project_id)
    digest = digest_client_id(client_id)
    db.execute(
        delete(ProjectChapterEditLeaseRow).where(
            ProjectChapterEditLeaseRow.workspace_id == workspace_id,
            ProjectChapterEditLeaseRow.project_id == project_id,
            ProjectChapterEditLeaseRow.chapter_id == chapter_id,
            ProjectChapterEditLeaseRow.user_id == actor_user_id,
            ProjectChapterEditLeaseRow.client_digest == digest,
        )
    )
    db.flush()
