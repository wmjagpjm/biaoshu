"""
模块：P10A 本机身份与会话服务
用途：用户名口令（scrypt）、不透明会话、CSRF 摘要校验、成员解析与脱敏序列化。
对接：api.auth、auth_middleware、deps；scripts/bootstrap_local_admin.py。
二次开发：
  - 禁止 JWT/OAuth/邮件/短信；禁止口令命令行落盘；禁止回显 Cookie/摘要/口令
  - 角色仅 bid_writer|finance|hr|bidder；所有者是成员标记
  - 成员管理细接口在后续任务扩展，本模块提供底座函数
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.entities import (
    AuthAuditEventRow,
    AuthSessionRow,
    LocalUserRow,
    Workspace,
    WorkspaceMemberRow,
)
from app.services.project_service import ensure_default_workspace

logger = logging.getLogger(__name__)

# 固定业务角色（不含独立「所有者」角色）
ROLE_BID_WRITER = "bid_writer"
ROLE_FINANCE = "finance"
ROLE_HR = "hr"
ROLE_BIDDER = "bidder"
ALLOWED_ROLES = frozenset({ROLE_BID_WRITER, ROLE_FINANCE, ROLE_HR, ROLE_BIDDER})

# 固定错误码（中文消息与路由层共用）
CODE_NOT_BOOTSTRAPPED = "auth_not_bootstrapped"
CODE_AUTH_REQUIRED = "auth_required"
CODE_INVALID_CREDENTIALS = "invalid_credentials"
CODE_CSRF_INVALID = "csrf_invalid"
CODE_WORKSPACE_FORBIDDEN = "workspace_forbidden"
CODE_ROLE_FORBIDDEN = "role_forbidden"
CODE_USER_INACTIVE = "user_inactive"
CODE_BAD_REQUEST = "auth_bad_request"

MSG_NOT_BOOTSTRAPPED = "尚未完成管理员初始化"
MSG_AUTH_REQUIRED = "未登录或会话已失效"
MSG_INVALID_CREDENTIALS = "用户名或口令错误"
MSG_CSRF_INVALID = "CSRF 校验失败"
MSG_WORKSPACE_FORBIDDEN = "无权访问该工作空间"
MSG_ROLE_FORBIDDEN = "当前角色无权访问该功能"
MSG_USER_INACTIVE = "用户已停用"

# scrypt 参数：本机交互登录可接受的强度
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


class AuthError(Exception):
    """用途：可映射为 HTTP 的认证/授权受控失败。"""

    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class MemberInfo:
    """用途：脱敏成员视图。"""

    workspace_id: str
    workspace_name: str
    role: str
    is_owner: bool
    is_active: bool


@dataclass(frozen=True)
class AuthPrincipal:
    """用途：请求级已验证主体（脱敏）。"""

    user_id: str
    username: str
    session_id: str
    active_workspace_id: str | None
    members: tuple[MemberInfo, ...]
    csrf_token: str | None = None


def utc_now() -> datetime:
    """用途：统一 UTC 时间源。"""
    return datetime.now(timezone.utc)


def normalize_username(username: str) -> str:
    """用途：用户名规范化唯一键（去空白、小写）。"""
    return (username or "").strip().lower()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """
    用途：scrypt 派生口令；返回 (salt_hex, hash_hex)。
    注意：不得记录 password 明文。
    """
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return salt.hex(), derived.hex()


def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """用途：常量时间比较口令派生值。"""
    try:
        _, candidate = hash_password(password, salt_hex=salt_hex)
        return secrets.compare_digest(candidate, hash_hex)
    except (ValueError, TypeError):
        return False


def digest_token(raw: str) -> str:
    """用途：会话/CSRF 原始值的 SHA-256 摘要（仅摘要入库）。"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_bootstrapped(db: Session) -> bool:
    """用途：是否已存在至少一名本机用户（管理员引导完成）。"""
    row = db.execute(select(LocalUserRow.id).limit(1)).first()
    return row is not None


def record_audit(
    db: Session,
    *,
    action: str,
    result: str,
    actor_user_id: str | None = None,
    workspace_id: str | None = None,
    target: str | None = None,
    commit: bool = True,
) -> None:
    """
    用途：写入最小审计事件；target 仅允许短标签，禁止口令/Token/摘要。
    """
    event = AuthAuditEventRow(
        id=_new_id("aud"),
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        action=action,
        result=result,
        target=(target or "")[:200] or None,
        created_at=utc_now(),
    )
    db.add(event)
    if commit:
        db.commit()


def list_recent_audit_events(db: Session, limit: int = 50) -> list[AuthAuditEventRow]:
    """用途：测试与本机排查读取最近审计（不含敏感字段）。"""
    stmt = (
        select(AuthAuditEventRow)
        .order_by(AuthAuditEventRow.created_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    return list(db.scalars(stmt).all())


def list_active_members_for_user(db: Session, user_id: str) -> list[MemberInfo]:
    """用途：列出用户全部启用中的工作空间成员关系。"""
    stmt = (
        select(WorkspaceMemberRow, Workspace)
        .join(Workspace, Workspace.id == WorkspaceMemberRow.workspace_id)
        .where(
            WorkspaceMemberRow.user_id == user_id,
            WorkspaceMemberRow.is_active.is_(True),
        )
        .order_by(WorkspaceMemberRow.created_at.asc())
    )
    out: list[MemberInfo] = []
    for member, ws in db.execute(stmt).all():
        out.append(
            MemberInfo(
                workspace_id=ws.id,
                workspace_name=ws.name,
                role=member.role,
                is_owner=bool(member.is_owner),
                is_active=bool(member.is_active),
            )
        )
    return out


def get_member(
    db: Session, *, workspace_id: str, user_id: str
) -> WorkspaceMemberRow | None:
    """用途：查询单条成员（含停用，由调用方判断）。"""
    stmt = select(WorkspaceMemberRow).where(
        WorkspaceMemberRow.workspace_id == workspace_id,
        WorkspaceMemberRow.user_id == user_id,
    )
    return db.scalars(stmt).first()


def add_member(
    db: Session,
    *,
    workspace_id: str,
    user_id: str,
    role: str = ROLE_BID_WRITER,
    is_owner: bool = False,
    is_active: bool = True,
    commit: bool = False,
) -> WorkspaceMemberRow:
    """用途：创建成员行；角色必须在白名单内。"""
    if role not in ALLOWED_ROLES:
        raise AuthError(CODE_BAD_REQUEST, "无效角色", status_code=400)
    existing = get_member(db, workspace_id=workspace_id, user_id=user_id)
    if existing is not None:
        return existing
    row = WorkspaceMemberRow(
        id=_new_id("wsm"),
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        is_owner=is_owner,
        is_active=is_active,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    return row


def bootstrap_local_admin(
    db: Session,
    settings: Settings,
    *,
    username: str,
    password: str,
    role: str = ROLE_BID_WRITER,
) -> AuthPrincipal:
    """
    用途：交互式/测试引导创建首个管理员并接管默认工作空间。
    规则：已存在用户则拒绝；口令不得为空；默认空间 owner_user_id 安全更新为该用户。
    """
    if is_bootstrapped(db):
        raise AuthError(CODE_BAD_REQUEST, "管理员已初始化", status_code=400)
    normalized = normalize_username(username)
    if not normalized or len(normalized) > 100:
        raise AuthError(CODE_BAD_REQUEST, "用户名无效", status_code=400)
    if not password or len(password) < 8:
        raise AuthError(CODE_BAD_REQUEST, "口令过短", status_code=400)
    if role not in ALLOWED_ROLES:
        raise AuthError(CODE_BAD_REQUEST, "无效角色", status_code=400)

    salt_hex, hash_hex = hash_password(password)
    user = LocalUserRow(
        id=_new_id("usr"),
        username=username.strip(),
        username_normalized=normalized,
        password_salt=salt_hex,
        password_hash=hash_hex,
        is_active=True,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(user)
    db.flush()

    ws = ensure_default_workspace(db, settings)
    # 安全接管默认空间所有者字符串，不批量改写历史业务行
    if ws.owner_user_id != user.id:
        ws.owner_user_id = user.id
    add_member(
        db,
        workspace_id=ws.id,
        user_id=user.id,
        role=role,
        is_owner=True,
        is_active=True,
    )
    record_audit(
        db,
        action="bootstrap_admin",
        result="ok",
        actor_user_id=user.id,
        workspace_id=ws.id,
        target="local_admin",
        commit=False,
    )
    db.commit()

    members = list_active_members_for_user(db, user.id)
    return AuthPrincipal(
        user_id=user.id,
        username=user.username,
        session_id="",
        active_workspace_id=ws.id,
        members=tuple(members),
    )


def _create_session_tokens(
    db: Session,
    settings: Settings,
    *,
    user_id: str,
    active_workspace_id: str | None,
) -> tuple[AuthSessionRow, str, str]:
    """用途：签发会话与 CSRF 原始值；库内仅存摘要。"""
    raw_token = secrets.token_urlsafe(32)
    raw_csrf = secrets.token_urlsafe(32)
    ttl = max(1, int(settings.auth_session_ttl_hours))
    now = utc_now()
    row = AuthSessionRow(
        id=_new_id("ses"),
        user_id=user_id,
        token_digest=digest_token(raw_token),
        csrf_digest=digest_token(raw_csrf),
        active_workspace_id=active_workspace_id,
        expires_at=now + timedelta(hours=ttl),
        revoked_at=None,
        created_at=now,
        last_seen_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw_token, raw_csrf


def login(
    db: Session,
    settings: Settings,
    *,
    username: str,
    password: str,
) -> tuple[AuthPrincipal, str, str]:
    """
    用途：校验用户名口令并签发会话。
    返回：(主体含 csrf_token, 原始 session token, 原始 csrf)。
    失败：统一 invalid_credentials，避免用户枚举。
    """
    normalized = normalize_username(username)
    user = db.scalars(
        select(LocalUserRow).where(LocalUserRow.username_normalized == normalized)
    ).first()
    if user is None or not user.is_active:
        record_audit(
            db,
            action="login",
            result="invalid_credentials",
            target="unknown_or_inactive",
        )
        raise AuthError(CODE_INVALID_CREDENTIALS, MSG_INVALID_CREDENTIALS, status_code=401)
    if not verify_password(password, user.password_salt, user.password_hash):
        record_audit(
            db,
            action="login",
            result="invalid_credentials",
            actor_user_id=user.id,
            target="bad_password",
        )
        raise AuthError(CODE_INVALID_CREDENTIALS, MSG_INVALID_CREDENTIALS, status_code=401)

    members = list_active_members_for_user(db, user.id)
    active_ws = None
    if members:
        # 优先默认工作空间，否则第一个成员空间
        default_id = settings.default_workspace_id
        active_ws = next(
            (m.workspace_id for m in members if m.workspace_id == default_id),
            members[0].workspace_id,
        )

    session, raw_token, raw_csrf = _create_session_tokens(
        db,
        settings,
        user_id=user.id,
        active_workspace_id=active_ws,
    )
    record_audit(
        db,
        action="login",
        result="ok",
        actor_user_id=user.id,
        workspace_id=active_ws,
        target="session_created",
    )
    principal = AuthPrincipal(
        user_id=user.id,
        username=user.username,
        session_id=session.id,
        active_workspace_id=active_ws,
        members=tuple(members),
        csrf_token=raw_csrf,
    )
    return principal, raw_token, raw_csrf


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def load_session_by_raw_token(
    db: Session,
    settings: Settings,
    raw_token: str,
    *,
    touch: bool = True,
) -> tuple[AuthSessionRow, LocalUserRow, list[MemberInfo]]:
    """用途：用 Cookie 原始值加载有效会话；过期/撤销则失败。"""
    if not raw_token:
        raise AuthError(CODE_AUTH_REQUIRED, MSG_AUTH_REQUIRED, status_code=401)
    digest = digest_token(raw_token)
    session = db.scalars(
        select(AuthSessionRow).where(AuthSessionRow.token_digest == digest)
    ).first()
    if session is None:
        raise AuthError(CODE_AUTH_REQUIRED, MSG_AUTH_REQUIRED, status_code=401)
    if session.revoked_at is not None:
        raise AuthError(CODE_AUTH_REQUIRED, MSG_AUTH_REQUIRED, status_code=401)
    if _as_aware(session.expires_at) <= utc_now():
        raise AuthError(CODE_AUTH_REQUIRED, MSG_AUTH_REQUIRED, status_code=401)

    user = db.get(LocalUserRow, session.user_id)
    if user is None or not user.is_active:
        raise AuthError(CODE_AUTH_REQUIRED, MSG_AUTH_REQUIRED, status_code=401)

    if touch:
        session.last_seen_at = utc_now()
        db.commit()

    members = list_active_members_for_user(db, user.id)
    return session, user, members


def principal_from_session(
    session: AuthSessionRow,
    user: LocalUserRow,
    members: list[MemberInfo],
    *,
    csrf_token: str | None = None,
) -> AuthPrincipal:
    """用途：组装请求级主体。"""
    return AuthPrincipal(
        user_id=user.id,
        username=user.username,
        session_id=session.id,
        active_workspace_id=session.active_workspace_id,
        members=tuple(members),
        csrf_token=csrf_token,
    )


def verify_csrf(session: AuthSessionRow, raw_csrf: str | None) -> None:
    """用途：比对 CSRF 原始头与库内摘要。"""
    if not raw_csrf:
        raise AuthError(CODE_CSRF_INVALID, MSG_CSRF_INVALID, status_code=403)
    if not secrets.compare_digest(digest_token(raw_csrf), session.csrf_digest):
        raise AuthError(CODE_CSRF_INVALID, MSG_CSRF_INVALID, status_code=403)


def revoke_session(db: Session, session: AuthSessionRow, *, actor_user_id: str) -> None:
    """用途：撤销会话（登出）。"""
    session.revoked_at = utc_now()
    db.commit()
    record_audit(
        db,
        action="logout",
        result="ok",
        actor_user_id=actor_user_id,
        workspace_id=session.active_workspace_id,
        target="session_revoked",
    )


def set_active_workspace(
    db: Session,
    session: AuthSessionRow,
    *,
    user_id: str,
    workspace_id: str,
) -> AuthPrincipal:
    """用途：将会话活动工作空间切换为已加入空间。"""
    members = list_active_members_for_user(db, user_id)
    if not any(m.workspace_id == workspace_id for m in members):
        record_audit(
            db,
            action="active_workspace",
            result="forbidden",
            actor_user_id=user_id,
            target=workspace_id[:64],
        )
        raise AuthError(CODE_WORKSPACE_FORBIDDEN, MSG_WORKSPACE_FORBIDDEN, status_code=403)
    session.active_workspace_id = workspace_id
    session.last_seen_at = utc_now()
    db.commit()
    user = db.get(LocalUserRow, user_id)
    assert user is not None
    record_audit(
        db,
        action="active_workspace",
        result="ok",
        actor_user_id=user_id,
        workspace_id=workspace_id,
        target="switched",
    )
    return principal_from_session(session, user, members)


def resolve_workspace_for_principal(
    principal: AuthPrincipal,
    x_workspace_id: str | None,
    *,
    require_bid_writer: bool = True,
) -> str:
    """
    用途：在已验证成员列表内解析工作空间；请求头仅作选择器。
    require_bid_writer：P10A 既有业务默认仅 bid_writer。
    """
    header = (x_workspace_id or "").strip() or None
    member_map = {m.workspace_id: m for m in principal.members if m.is_active}
    if not member_map:
        raise AuthError(CODE_WORKSPACE_FORBIDDEN, MSG_WORKSPACE_FORBIDDEN, status_code=403)

    if header:
        member = member_map.get(header)
        if member is None:
            raise AuthError(
                CODE_WORKSPACE_FORBIDDEN, MSG_WORKSPACE_FORBIDDEN, status_code=403
            )
        target = header
    else:
        preferred = principal.active_workspace_id
        if preferred and preferred in member_map:
            target = preferred
        else:
            target = next(iter(member_map.keys()))
        member = member_map[target]

    if require_bid_writer and member.role != ROLE_BID_WRITER:
        raise AuthError(CODE_ROLE_FORBIDDEN, MSG_ROLE_FORBIDDEN, status_code=403)
    return target


def serialize_me(principal: AuthPrincipal) -> dict[str, Any]:
    """用途：/auth/me 与 login 共用脱敏结构。"""
    return {
        "user": {
            "id": principal.user_id,
            "username": principal.username,
        },
        "workspaces": [
            {
                "id": m.workspace_id,
                "name": m.workspace_name,
                "role": m.role,
                "isOwner": m.is_owner,
            }
            for m in principal.members
        ],
        "activeWorkspaceId": principal.active_workspace_id,
        "csrfToken": principal.csrf_token,
    }
