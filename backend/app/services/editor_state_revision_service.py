"""
模块：P12C-A / P12F-A / P12F-J-A / P13-C / P13-D2 / P13-H1 editor-state 有限自动修订账本服务
用途：独立 revision 表上的无提交 transition 原语（相邻去重、断链补点、
  最多 20 条且总快照 20 MiB；固定行永不被自动裁剪）；
  P13-C/D2 只读解析当前已载入版本的最新修订来源与操作者用户名（不写、不回扫）。
对接：EditorStateRevisionRow；LocalUserRow/WorkspaceMemberRow；
  editor_state_service（共享 13 键/规范 JSON/版本算法）；
  GET|PUT editor-state 的 currentRevisionSourceKind / currentRevisionActorUsername。
二次开发：
  - 只 flush，绝不 commit/rollback/refresh/项目查询/锁/审计（transition 路径）
  - 13 键/JSON/哈希必须委托 editor_state_service，禁止第二套算法
  - 最新/裁剪/P13 元数据 SQL 不得加载 snapshot_json；DELETE 必须 workspace+project+id 三重限定
  - 校验完所有 snapshot_bytes/is_pinned 后才允许删除；固定行全保留；
    非固定按最新前缀补足；禁止跳洞保留更旧小非固定行
  - 返回值不含 snapshot/行 ID/项目/空间/actor_user_id/口令
  - resolve_current_revision_meta 仅 LIMIT 1 最新行 + 同 workspace 左联，禁止回扫旧同版本
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Integer, and_, delete, select, type_coerce
from sqlalchemy.orm import Session

from app.models.entities import (
    EditorStateEventRow,
    EditorStateRevisionRow,
    LocalUserRow,
    WorkspaceMemberRow,
    utc_now,
)
from app.services import editor_state_service

MAX_REVISIONS_PER_PROJECT = 20
MAX_REVISION_BYTES_PER_PROJECT = 20 * 1024 * 1024  # 20 MiB 总字节配额
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MiB 单条上限
MIN_SNAPSHOT_BYTES = 1
# P12F-J-A：固定集合上限（裁剪前校验；与 pin 服务共用语义）
MAX_PINNED_REVISIONS_PER_PROJECT = 5
MAX_PINNED_BYTES_PER_PROJECT = 10 * 1024 * 1024  # 10 MiB
# P13-H1：事件账本每项目保留上限（与修订裁剪独立）
MAX_EVENTS_PER_PROJECT = 200

REVISION_SOURCE_KINDS: frozenset[str] = frozenset(
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

CODE_REVISION_INVALID = "editor_state_revision_invalid"
MSG_REVISION_INVALID = "修订记录输入无效，未写入"


class EditorStateRevisionError(Exception):
    """
    用途：固定内部错误，禁止拼接正文/版本/项目/SQL/异常原文。
    对接：未来 B 包与业务写同事务回滚；A 包无 HTTP 映射。
    """

    def __init__(
        self,
        code: str = CODE_REVISION_INVALID,
        message: str = MSG_REVISION_INVALID,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _new_revision_id() -> str:
    """用途：生成不透明修订 ID（esr_ + 32 hex）。"""
    return f"esr_{secrets.token_hex(16)}"


def _raise_invalid() -> None:
    """用途：统一抛出固定内部错误，避免分支消息泄漏。"""
    raise EditorStateRevisionError(CODE_REVISION_INVALID, MSG_REVISION_INVALID)


def _prepare_state_payload(state: Any) -> tuple[str, str, int]:
    """
    用途：校验并规范化 before/after 状态为 (snapshot_json, state_version, bytes)。
    规则：
      - extract 前要求 CANONICAL_STATE_KEY_SET ⊆ state.keys()（允许服务端派生额外键）；
      - 委托共享 13 键/规范 JSON/版本算法；携带 stateVersion 必须合法且与重算一致。
    """
    if not isinstance(state, dict):
        _raise_invalid()

    # 禁止缺键假状态：extract 对缺键用 .get 会填 None，仅靠版本匹配会误入账
    if not editor_state_service.CANONICAL_STATE_KEY_SET.issubset(state.keys()):
        _raise_invalid()

    carried = state.get("stateVersion")
    if not editor_state_service.is_valid_state_version(carried):
        _raise_invalid()

    try:
        snapshot = editor_state_service.extract_canonical_snapshot(state)
        snapshot_json = editor_state_service.canonical_snapshot_json(snapshot)
    except (TypeError, ValueError, OverflowError):
        # NaN/Infinity/不可序列化等 → 固定错误，不泄漏异常原文
        _raise_invalid()

    try:
        snapshot_bytes = len(snapshot_json.encode("utf-8"))
    except Exception:
        _raise_invalid()

    if snapshot_bytes < MIN_SNAPSHOT_BYTES or snapshot_bytes > MAX_SNAPSHOT_BYTES:
        _raise_invalid()

    computed = editor_state_service.compute_state_version_from_canonical_json(
        snapshot_json
    )
    if carried != computed:
        _raise_invalid()

    return snapshot_json, computed, snapshot_bytes


def _latest_id_and_version(
    db: Session, workspace_id: str, project_id: str
) -> tuple[str, str] | None:
    """
    用途：只读当前项目最新一条 id/state_version，禁止加载 snapshot_json。
    """
    row = db.execute(
        select(
            EditorStateRevisionRow.id,
            EditorStateRevisionRow.state_version,
        )
        .where(
            EditorStateRevisionRow.workspace_id == workspace_id,
            EditorStateRevisionRow.project_id == project_id,
        )
        .order_by(
            EditorStateRevisionRow.created_at.desc(),
            EditorStateRevisionRow.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if row is None:
        return None
    return str(row.id), str(row.state_version)


# 用户名拒绝：C0/C1/DEL 由码点范围判定；另禁行分隔与双向控制
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


@dataclass(frozen=True)
class CurrentRevisionMeta:
    """
    用途：P13-C/D2 当前已载入版本只读元数据（不可变）。
    字段：source_kind / actor_username；均已按契约独立校验，非法侧为 None。
    对接：仅由 resolve_current_revision_meta 构造；_editor_out 与兼容入口只读消费。
    二次开发：禁止加入 actor_user_id/行 ID/snapshot 或可变字段；
      两侧独立降级，不得因一侧非法连带清空另一侧。
    """

    source_kind: str | None
    actor_username: str | None


def _safe_actor_username(value: Any) -> str | None:
    """
    用途：严格用户名安全文本门。
    规则：原生 str、1..100 Unicode 码点、无首尾空白；拒绝 C0/C1/DEL、
      U+2028/U+2029 与约定双向控制；不 trim、不 NFKC、不改写。
    """
    if not isinstance(value, str):
        return None
    # 码点长度（Python 3 str 以 Unicode 码点计）
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


def _strict_active_flag(value: Any) -> bool:
    """用途：启用位仅接受原始整数 1（或严格 True）；禁止 truthy 宽判。"""
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool) and value == 1:
        return True
    # SQLAlchemy/SQLite 可能以 int 1 返回；bool 子类 int 已在 True 分支处理
    if type(value) is int and value == 1:
        return True
    return False


def resolve_current_revision_meta(
    db: Session,
    workspace_id: str,
    project_id: str,
    state_version: str,
) -> CurrentRevisionMeta:
    """
    用途：P13-C/D2 一次 SQL 解析最新修订来源与操作者用户名。
    规则：
      - 仅最新一条（created_at DESC, id DESC, LIMIT 1）；
      - 投影 state_version/source_kind/username/两个严格启用位；
      - 左联 local_users 与同 workspace_id 的 workspace_members；
      - 禁止 snapshot_json/口令/会话/审计/actor ID 响应；
      - 禁止回扫旧同版本、禁止 add/delete/flush/commit/rollback/refresh；
      - 版本不匹配时两项均为 None；来源与用户名独立降级。
    对接：_editor_out 只调用一次；兼容入口 resolve_current_revision_source_kind 复用本函数。
    二次开发：禁止第二套查询/排序/回扫；JOIN ON 中的 actor_user_id 不得进入 SELECT 投影；
      禁止在本函数内写会话或加载 snapshot；禁止向响应公开内部 user id。
    """
    empty = CurrentRevisionMeta(source_kind=None, actor_username=None)
    if not isinstance(workspace_id, str) or not workspace_id:
        return empty
    if not isinstance(project_id, str) or not project_id:
        return empty
    if not isinstance(state_version, str) or not state_version:
        return empty
    if not editor_state_service.is_valid_state_version(state_version):
        return empty

    user_active_col = type_coerce(LocalUserRow.is_active, Integer).label(
        "user_is_active"
    )
    member_active_col = type_coerce(WorkspaceMemberRow.is_active, Integer).label(
        "member_is_active"
    )
    row = db.execute(
        select(
            EditorStateRevisionRow.state_version,
            EditorStateRevisionRow.source_kind,
            LocalUserRow.username,
            user_active_col,
            member_active_col,
        )
        .select_from(EditorStateRevisionRow)
        .outerjoin(
            LocalUserRow,
            LocalUserRow.id == EditorStateRevisionRow.actor_user_id,
        )
        .outerjoin(
            WorkspaceMemberRow,
            and_(
                WorkspaceMemberRow.user_id == EditorStateRevisionRow.actor_user_id,
                WorkspaceMemberRow.workspace_id
                == EditorStateRevisionRow.workspace_id,
            ),
        )
        .where(
            EditorStateRevisionRow.workspace_id == workspace_id,
            EditorStateRevisionRow.project_id == project_id,
        )
        .order_by(
            EditorStateRevisionRow.created_at.desc(),
            EditorStateRevisionRow.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if row is None:
        return empty

    latest_version = row.state_version
    if not isinstance(latest_version, str) or latest_version != state_version:
        return empty

    # 来源独立校验
    source_kind: str | None = None
    raw_source = row.source_kind
    if isinstance(raw_source, str) and raw_source in REVISION_SOURCE_KINDS:
        source_kind = raw_source

    # 用户名：用户存在 + 双方启用 + 安全文本
    actor_username: str | None = None
    if _strict_active_flag(row.user_is_active) and _strict_active_flag(
        row.member_is_active
    ):
        actor_username = _safe_actor_username(row.username)

    return CurrentRevisionMeta(
        source_kind=source_kind, actor_username=actor_username
    )


def resolve_current_revision_source_kind(
    db: Session,
    workspace_id: str,
    project_id: str,
    state_version: str,
) -> str | None:
    """
    用途：P13-C 兼容入口；复用 resolve_current_revision_meta，禁止第二套查询/排序。
    对接：既有调用方可继续只取来源；生产 _editor_out 应改用 meta 一次调用。
    """
    return resolve_current_revision_meta(
        db, workspace_id, project_id, state_version
    ).source_kind


def _validate_actor_user_id(actor_user_id: str | None) -> str | None:
    """
    用途：P13-D1 校验可信 actor；仅 None 或非空、无首尾空白、长度 ≤64 的字符串。
    规则：非法内部调用固定 invalid，由调用方原事务回滚；不得从客户端字段推断。
    """
    if actor_user_id is None:
        return None
    if not isinstance(actor_user_id, str):
        _raise_invalid()
    if not actor_user_id or actor_user_id.strip() != actor_user_id:
        _raise_invalid()
    if len(actor_user_id) > 64:
        _raise_invalid()
    return actor_user_id


def _insert_revision_row(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    source_kind: str,
    actor_user_id: str | None = None,
) -> None:
    """用途：插入一条修订并 flush；不 commit/refresh。"""
    row = EditorStateRevisionRow(
        id=_new_revision_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        snapshot_json=snapshot_json,
        state_version=state_version,
        snapshot_bytes=snapshot_bytes,
        source_kind=source_kind,
        is_pinned=False,
        actor_user_id=actor_user_id,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()


def _validate_trim_snapshot_bytes(value: Any) -> int:
    """
    用途：裁剪前严格校验 snapshot_bytes 元数据；非法固定 invalid，禁止部分删除。
    规则：非布尔 int，落在 [MIN_SNAPSHOT_BYTES, MAX_SNAPSHOT_BYTES]。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        _raise_invalid()
    if value < MIN_SNAPSHOT_BYTES or value > MAX_SNAPSHOT_BYTES:
        _raise_invalid()
    return value


def _validate_trim_is_pinned(value: Any) -> bool:
    """
    用途：裁剪前严格校验 is_pinned；仅接受 bool 或 SQLite 0/1 整型。
    """
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    _raise_invalid()
    return False  # 不可达；满足类型检查


def _trim_revisions(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：同事务内保护性裁剪本项目修订：
      固定行永远保留；非固定按 created_at DESC,id DESC 最新前缀补足；
      总条数 ≤ MAX_REVISIONS_PER_PROJECT，总字节 ≤ MAX_REVISION_BYTES_PER_PROJECT。
    二次开发：
      - SELECT 仅 id/state_version/snapshot_bytes/is_pinned，禁止加载 snapshot_json；
      - 必须先完整物化并校验全部元数据，再校验固定集合 ≤5/10MiB；
      - 首次不适配的非固定及其后所有更旧非固定删除；固定旧行可形成空洞；
      - 禁止跳过大非固定保留更旧小非固定；DELETE 三重限定；只 flush。
    """
    # type_coerce(Integer)：绕过 Boolean result processor，返回原始 0/1/非法整型
    rows = list(
        db.execute(
            select(
                EditorStateRevisionRow.id,
                EditorStateRevisionRow.state_version,
                EditorStateRevisionRow.snapshot_bytes,
                type_coerce(EditorStateRevisionRow.is_pinned, Integer).label(
                    "is_pinned"
                ),
            )
            .where(
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
        ).all()
    )
    if not rows:
        return

    # 先完整校验全部元数据，再决定删除集合；任一行非法则整事务回滚
    validated: list[tuple[str, int, bool]] = []
    pinned_count = 0
    pinned_bytes = 0
    for row in rows:
        nbytes = _validate_trim_snapshot_bytes(row.snapshot_bytes)
        pinned = _validate_trim_is_pinned(row.is_pinned)
        validated.append((str(row.id), nbytes, pinned))
        if pinned:
            pinned_count += 1
            pinned_bytes += nbytes

    if (
        pinned_count > MAX_PINNED_REVISIONS_PER_PROJECT
        or pinned_bytes > MAX_PINNED_BYTES_PER_PROJECT
    ):
        _raise_invalid()

    # 固定行永远进入保留集合
    keep_ids: set[str] = set()
    kept_count = 0
    kept_bytes = 0
    for rid, nbytes, pinned in validated:
        if pinned:
            keep_ids.add(rid)
            kept_count += 1
            kept_bytes += nbytes

    # 非固定按最新前缀补足；首次不适配后丢弃所有更旧非固定
    dropping_non_pinned = False
    for rid, nbytes, pinned in validated:
        if pinned:
            continue
        if dropping_non_pinned:
            continue
        if (
            kept_count >= MAX_REVISIONS_PER_PROJECT
            or kept_bytes + nbytes > MAX_REVISION_BYTES_PER_PROJECT
        ):
            dropping_non_pinned = True
            continue
        keep_ids.add(rid)
        kept_count += 1
        kept_bytes += nbytes

    drop_ids = [rid for rid, _n, _p in validated if rid not in keep_ids]
    if not drop_ids:
        return
    db.execute(
        delete(EditorStateRevisionRow).where(
            EditorStateRevisionRow.workspace_id == workspace_id,
            EditorStateRevisionRow.project_id == project_id,
            EditorStateRevisionRow.id.in_(drop_ids),
        )
    )
    db.flush()



def _new_event_id() -> str:
    """用途：生成不透明事件 ID（ese_ + 32 hex）。"""
    return f"ese_{secrets.token_hex(16)}"


def _insert_event_row(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    state_version: str,
    source_kind: str,
) -> None:
    """用途：插入一条脱敏 after 事件并 flush；不 commit/refresh。"""
    row = EditorStateEventRow(
        id=_new_event_id(),
        workspace_id=workspace_id,
        project_id=project_id,
        state_version=state_version,
        source_kind=source_kind,
        occurred_at=utc_now(),
    )
    db.add(row)
    db.flush()


def _trim_events(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：同事务内按 occurred_at DESC,id DESC 连续裁剪本项目事件至最多 200 条。
    二次开发：SELECT 仅 id；DELETE 必须 workspace+project+id 三重限定；只 flush。
    """
    rows = list(
        db.execute(
            select(EditorStateEventRow.id)
            .where(
                EditorStateEventRow.workspace_id == workspace_id,
                EditorStateEventRow.project_id == project_id,
            )
            .order_by(
                EditorStateEventRow.occurred_at.desc(),
                EditorStateEventRow.id.desc(),
            )
        ).all()
    )
    if len(rows) <= MAX_EVENTS_PER_PROJECT:
        return
    drop_ids = [str(r.id) for r in rows[MAX_EVENTS_PER_PROJECT:]]
    if not drop_ids:
        return
    db.execute(
        delete(EditorStateEventRow).where(
            EditorStateEventRow.workspace_id == workspace_id,
            EditorStateEventRow.project_id == project_id,
            EditorStateEventRow.id.in_(drop_ids),
        )
    )
    db.flush()


def record_editor_state_transition(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    source_kind: str,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """
    用途：在调用方已有的同一事务内记录 before→after 自动修订（无提交原语）。
    对接：九类写链；P13-D1 命名参数 actor_user_id。
    规则：
      1. 固定 source 枚举；非法输入固定内部错误；
      2. 写入前先校验 actor（None 或非空无空白 ≤64）；
      3. 账本空或最新 != before → 先追加 before，actor 固定 NULL；
      4. 最新 != after → 再追加 after，记录本次可信 actor；
      5. before==after 空操作不新增有 actor 行；相邻同版本去重；
      6. 只 flush；返回仅 added_count 与 final_state_version。
    """
    if not isinstance(source_kind, str) or source_kind not in REVISION_SOURCE_KINDS:
        _raise_invalid()
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_invalid()
    if not isinstance(project_id, str) or not project_id:
        _raise_invalid()

    # actor 必须在任何 revision 插入前校验
    safe_actor = _validate_actor_user_id(actor_user_id)

    # 两态均先完整校验，避免半写入
    before_json, before_ver, before_bytes = _prepare_state_payload(before_state)
    after_json, after_ver, after_bytes = _prepare_state_payload(after_state)

    latest = _latest_id_and_version(db, workspace_id, project_id)
    current_version = latest[1] if latest is not None else None
    added = 0

    if current_version is None or current_version != before_ver:
        # 补账 before：发现/补齐既有状态，不代表本次请求创造 → actor 固定 NULL
        _insert_revision_row(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=before_json,
            state_version=before_ver,
            snapshot_bytes=before_bytes,
            source_kind=source_kind,
            actor_user_id=None,
        )
        added += 1
        current_version = before_ver

    if current_version != after_ver:
        _insert_revision_row(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=after_json,
            state_version=after_ver,
            snapshot_bytes=after_bytes,
            source_kind=source_kind,
            actor_user_id=safe_actor,
        )
        # P13-H1：仅真实 after 修订插入时写一条脱敏事件（before 补账不产事件）
        _insert_event_row(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            state_version=after_ver,
            source_kind=source_kind,
        )
        added += 1
        current_version = after_ver

    _trim_revisions(db, workspace_id, project_id)
    _trim_events(db, workspace_id, project_id)

    return {
        "added_count": added,
        "final_state_version": current_version,
    }
