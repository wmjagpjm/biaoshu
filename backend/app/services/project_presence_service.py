"""
模块：P13-F1 项目在线租约服务
用途：单事务完成项目重验、过期清理、8 client 上限、同 client 原子 upsert 与成员快照。
对接：api.project_presence；ProjectPresenceLeaseRow；LocalUserRow/WorkspaceMemberRow。
二次开发：
  - service 只 flush 不 commit；失败由路由 rollback；
  - 禁止日志/响应原始 clientId；禁止后台 timer；禁止 GET/SSE。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import (
    LocalUserRow,
    Project,
    ProjectPresenceLeaseRow,
    WorkspaceMemberRow,
)
from app.services import auth_service

# 固定契约常量
LEASE_TTL_SECONDS = 45
REFRESH_AFTER_SECONDS = 15
MAX_CLIENTS_PER_USER_PROJECT = 8
MAX_SNAPSHOT_MEMBERS = 50

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CLIENT_LIMIT = "presence_client_limit"
MSG_CLIENT_LIMIT = "当前项目活动客户端数量已达上限"
CODE_PRESENCE_FAILED = "presence_failed"
MSG_PRESENCE_FAILED = "在线状态处理失败"

# 与 P13-D2 同等级：行分隔 + 双向控制
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


class ProjectPresenceError(Exception):
    """用途：服务层固定错误，由路由映射 HTTP；禁止附带敏感细节。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class PresenceMember:
    """用途：快照成员（已通过安全用户名门）。"""

    username: str
    is_self: bool


@dataclass(frozen=True)
class PresenceHeartbeatResult:
    """用途：心跳服务结果；路由序列化为精确四键。"""

    lease_expires_at: datetime
    refresh_after_seconds: int
    members: list[PresenceMember]
    truncated: bool


def _utc_now() -> datetime:
    """用途：服务端 UTC 时钟。"""
    return datetime.now(timezone.utc)


def _new_lease_id() -> str:
    """用途：不透明租约主键，最多 64 字符。"""
    return f"ppl_{secrets.token_hex(16)}"


def digest_client_id(client_id: str) -> str:
    """
    用途：规范 clientId → SHA-256 十六进制摘要；禁止落库原文。
    """
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


def _require_project(db: Session, workspace_id: str, project_id: str) -> Project:
    """用途：项目必须属于当前活动 workspace；跨空间/缺失统一 404。"""
    project = db.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectPresenceError(404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND)
    return project


def _acquire_presence_write_serialization(db: Session) -> None:
    """
    用途：在项目重验/清理/find/count/upsert/快照之前取得可等待写事务串行化。
    规则：
      - SQLite：用无行变更的 UPDATE 升级到 RESERVED 写锁（可等待），
        避免显式 BEGIN IMMEDIATE 与已开始的 SQLAlchemy Session 事务嵌套；
      - 非 SQLite：no-op，保持上层隔离级别安全，不引入进程锁/全局库配置。
    二次开发：service 仍只 flush 不 commit；禁止吞 OperationalError 假成功。
    """
    bind = db.get_bind()
    if getattr(bind.dialect, "name", None) != "sqlite":
        return
    # WHERE 0 无副作用，但仍开启写意图并串行化后续 presence 写路径
    db.execute(text("UPDATE project_presence_leases SET id = id WHERE 0"))


def _as_utc(value: datetime) -> datetime:
    """用途：将库内时间统一为 aware UTC，避免 SQLite 朴素时间比较偏差。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _purge_expired(db: Session, *, now: datetime) -> None:
    """
    用途：机会性删除全表已过期租约。
    说明：SQLite DateTime 可能以朴素串存储；在 Python 侧做 UTC 比较更稳妥。
    """
    rows = db.execute(select(ProjectPresenceLeaseRow)).scalars().all()
    for row in rows:
        if _as_utc(row.expires_at) <= now:
            db.delete(row)
    db.flush()


def _count_active_clients(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    user_id: str,
    now: datetime,
) -> int:
    """用途：统计当前用户在本项目的未过期 client 数。"""
    rows = db.execute(
        select(ProjectPresenceLeaseRow).where(
            ProjectPresenceLeaseRow.workspace_id == workspace_id,
            ProjectPresenceLeaseRow.project_id == project_id,
            ProjectPresenceLeaseRow.user_id == user_id,
        )
    ).scalars().all()
    return sum(1 for r in rows if _as_utc(r.expires_at) > now)


def _find_lease(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    user_id: str,
    client_digest: str,
) -> ProjectPresenceLeaseRow | None:
    """用途：按四元组查找租约行（含已过期，供 upsert 复用主键）。"""
    return db.execute(
        select(ProjectPresenceLeaseRow).where(
            ProjectPresenceLeaseRow.workspace_id == workspace_id,
            ProjectPresenceLeaseRow.project_id == project_id,
            ProjectPresenceLeaseRow.user_id == user_id,
            ProjectPresenceLeaseRow.client_digest == client_digest,
        )
    ).scalar_one_or_none()


def _upsert_lease(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    user_id: str,
    client_digest: str,
    now: datetime,
    expires_at: datetime,
) -> ProjectPresenceLeaseRow:
    """
    用途：同 client 原子 upsert；并发下不重复行/不 500。
    规则：已有行可续租；新 client 达 8 上限固定 429 零新增。
    """
    existing = _find_lease(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        client_digest=client_digest,
    )
    if existing is not None:
        existing.last_seen_at = now
        existing.expires_at = expires_at
        db.flush()
        return existing

    active_n = _count_active_clients(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=user_id,
        now=now,
    )
    if active_n >= MAX_CLIENTS_PER_USER_PROJECT:
        raise ProjectPresenceError(429, CODE_CLIENT_LIMIT, MSG_CLIENT_LIMIT)

    row = ProjectPresenceLeaseRow(
        id=_new_lease_id(),
        workspace_id=workspace_id,
        project_id=project_id,
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
    except IntegrityError:
        # 同 client 并发插入：转为更新
        raced = _find_lease(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            client_digest=client_digest,
        )
        if raced is None:
            raise ProjectPresenceError(
                500, CODE_PRESENCE_FAILED, MSG_PRESENCE_FAILED
            ) from None
        raced.last_seen_at = now
        raced.expires_at = expires_at
        db.flush()
        return raced


def _build_snapshot(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    now: datetime,
) -> tuple[list[PresenceMember], bool]:
    """
    用途：联表校验启用用户/成员/bid_writer，同用户多 client 聚合，安全用户名门。
    规则：自身优先，其余按用户名大小写折叠稳定排序；最多 50，超限 truncated。
    """
    stmt = (
        select(
            ProjectPresenceLeaseRow.user_id,
            ProjectPresenceLeaseRow.expires_at,
            LocalUserRow.username,
        )
        .join(LocalUserRow, LocalUserRow.id == ProjectPresenceLeaseRow.user_id)
        .join(
            WorkspaceMemberRow,
            (WorkspaceMemberRow.user_id == ProjectPresenceLeaseRow.user_id)
            & (WorkspaceMemberRow.workspace_id == ProjectPresenceLeaseRow.workspace_id),
        )
        .where(
            ProjectPresenceLeaseRow.workspace_id == workspace_id,
            ProjectPresenceLeaseRow.project_id == project_id,
            LocalUserRow.is_active.is_(True),
            WorkspaceMemberRow.is_active.is_(True),
            WorkspaceMemberRow.role == auth_service.ROLE_BID_WRITER,
            WorkspaceMemberRow.workspace_id == workspace_id,
        )
    )
    rows = db.execute(stmt).all()

    # user_id -> 安全用户名（坏名整用户隐藏）；过期在 Python 侧过滤
    by_user: dict[str, str] = {}
    for user_id, expires_at, username in rows:
        if _as_utc(expires_at) <= now:
            continue
        if user_id in by_user:
            continue
        safe = _safe_username(username)
        if safe is None:
            continue
        by_user[str(user_id)] = safe

    self_name = by_user.get(actor_user_id)
    others = [
        (uid, name)
        for uid, name in by_user.items()
        if uid != actor_user_id
    ]
    others.sort(key=lambda item: (item[1].casefold(), item[0]))

    ordered: list[tuple[str, str]] = []
    if self_name is not None:
        ordered.append((actor_user_id, self_name))
    ordered.extend(others)

    truncated = len(ordered) > MAX_SNAPSHOT_MEMBERS
    sliced = ordered[:MAX_SNAPSHOT_MEMBERS]
    members = [
        PresenceMember(username=name, is_self=(uid == actor_user_id))
        for uid, name in sliced
    ]
    return members, truncated


def heartbeat_presence(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    client_id: str,
) -> PresenceHeartbeatResult:
    """
    用途：心跳主入口；单事务边界内重验/清理/限额/upsert/快照。
    对接：POST .../presence/heartbeat；不 commit。
    时钟：必须先取得写串行化，再取唯一 UTC now/expires_at，
      避免等待写锁期间陈旧 now 导致 TTL 缩短、过期租约误计活动。
    """
    # 写串行化必须在取样时钟与重验/清理/find/count/upsert/快照之前
    _acquire_presence_write_serialization(db)
    now = _utc_now()
    expires_at = now + timedelta(seconds=LEASE_TTL_SECONDS)
    _require_project(db, workspace_id, project_id)
    _purge_expired(db, now=now)
    digest = digest_client_id(client_id)
    _upsert_lease(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        user_id=actor_user_id,
        client_digest=digest,
        now=now,
        expires_at=expires_at,
    )
    members, truncated = _build_snapshot(
        db,
        workspace_id=workspace_id,
        project_id=project_id,
        actor_user_id=actor_user_id,
        now=now,
    )
    db.flush()
    return PresenceHeartbeatResult(
        lease_expires_at=expires_at,
        refresh_after_seconds=REFRESH_AFTER_SECONDS,
        members=members,
        truncated=truncated,
    )


def leave_presence(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    actor_user_id: str,
    client_id: str,
) -> None:
    """
    用途：精确删除当前 actor+空间+项目+摘要租约；幂等；不误删其它。
    对接：POST .../presence/leave；不 commit。
    """
    _acquire_presence_write_serialization(db)
    _require_project(db, workspace_id, project_id)
    digest = digest_client_id(client_id)
    db.execute(
        delete(ProjectPresenceLeaseRow).where(
            ProjectPresenceLeaseRow.workspace_id == workspace_id,
            ProjectPresenceLeaseRow.project_id == project_id,
            ProjectPresenceLeaseRow.user_id == actor_user_id,
            ProjectPresenceLeaseRow.client_digest == digest,
        )
    )
    db.flush()
