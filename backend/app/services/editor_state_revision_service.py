"""
模块：P12C-A editor-state 有限自动修订账本服务
用途：独立 revision 表上的无提交 transition 原语（相邻去重、断链补点、10 条裁剪）。
对接：EditorStateRevisionRow；editor_state_service（共享 13 键/规范 JSON/版本算法）。
二次开发：
  - A 包禁止任何生产写入者调用；不得声称自动历史已可用
  - 只 flush，绝不 commit/rollback/refresh/项目查询/锁/审计
  - 13 键/JSON/哈希必须委托 editor_state_service，禁止第二套算法
  - 最新/裁剪 SQL 不得加载 snapshot_json；DELETE 必须 workspace+project+id 三重限定
  - 返回值不含 snapshot/行 ID/项目/空间
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateRevisionRow, utc_now
from app.services import editor_state_service

MAX_REVISIONS_PER_PROJECT = 10
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024  # 2 MiB
MIN_SNAPSHOT_BYTES = 1

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


def _insert_revision_row(
    db: Session,
    *,
    workspace_id: str,
    project_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    source_kind: str,
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
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()


def _trim_revisions(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：同事务内仅保留本项目最近 10 条（created_at DESC, id DESC）。
    二次开发：
      - SELECT 仅 id/state_version，禁止加载 snapshot_json；
      - DELETE 必须同时限定 workspace_id/project_id/id；
      - 禁止跨项目/跨空间删除。
    """
    rows = list(
        db.execute(
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
        ).all()
    )
    if len(rows) <= MAX_REVISIONS_PER_PROJECT:
        return
    drop_ids = [str(r.id) for r in rows[MAX_REVISIONS_PER_PROJECT:]]
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


def record_editor_state_transition(
    db: Session,
    workspace_id: str,
    project_id: str,
    *,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    source_kind: str,
) -> dict[str, Any]:
    """
    用途：在调用方已有的同一事务内记录 before→after 自动修订（无提交原语）。
    对接：未来 P12C-B 各写入者；A 包仅测试调用。
    规则：
      1. 固定 source 枚举；非法输入固定内部错误；
      2. 账本空或最新 != before → 先追加 before；最新 != after → 再追加 after；
      3. 相邻同版本去重；回到旧版本因与最新不同仍形成新时间点；
      4. 只 flush；返回仅 added_count 与 final_state_version。
    """
    if not isinstance(source_kind, str) or source_kind not in REVISION_SOURCE_KINDS:
        _raise_invalid()
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_invalid()
    if not isinstance(project_id, str) or not project_id:
        _raise_invalid()

    # 两态均先完整校验，避免半写入
    before_json, before_ver, before_bytes = _prepare_state_payload(before_state)
    after_json, after_ver, after_bytes = _prepare_state_payload(after_state)

    latest = _latest_id_and_version(db, workspace_id, project_id)
    current_version = latest[1] if latest is not None else None
    added = 0

    if current_version is None or current_version != before_ver:
        _insert_revision_row(
            db,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=before_json,
            state_version=before_ver,
            snapshot_bytes=before_bytes,
            source_kind=source_kind,
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
        )
        added += 1
        current_version = after_ver

    _trim_revisions(db, workspace_id, project_id)

    return {
        "added_count": added,
        "final_state_version": current_version,
    }
