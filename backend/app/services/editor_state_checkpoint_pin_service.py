"""
模块：P12J-A editor-state 检查点单条固定状态服务
用途：在当前工作空间/项目三重作用域内设置/取消恰好一条检查点的 is_pinned；
  固定上限 5 条/10 MiB；项目级写锁后重读；成功唯一 commit。
对接：api.editor_state_checkpoints PATCH .../pin；
  EditorStateCheckpointRow；Project；与 checkpoint create/restore 同项目锁域。
二次开发：
  - 禁止加载快照正文 / ORM 整实体列表含正文 / 当前态 / 修订；
  - 锁后投影 id/snapshot_bytes/原始 is_pinned，最多 21 行侦测 20 条不变量；
  - UPDATE 必须 workspace+project+id 且只写 is_pinned；
  - 超限 409 零写；execute/flush/commit 失败必须 rollback，固定 pin_failed；
  - 同值幂等；成功路径 commit 后禁止 refresh/补写检查点或修订。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, select, type_coerce, update
from sqlalchemy.orm import Session

from app.models.entities import EditorStateCheckpointRow, Project
from app.services import editor_state_checkpoint_service

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CHECKPOINT_NOT_FOUND = "editor_state_checkpoint_not_found"
MSG_CHECKPOINT_NOT_FOUND = "检查点不存在"
CODE_PIN_LIMIT = "editor_state_checkpoint_pin_limit"
MSG_PIN_LIMIT = "固定检查点已达上限"
CODE_PIN_FAILED = "editor_state_checkpoint_pin_failed"
MSG_PIN_FAILED = "保存检查点固定状态失败"
CODE_PIN_INVALID = "editor_state_checkpoint_pin_request_invalid"
MSG_PIN_INVALID = "检查点固定请求无效"

MAX_PINNED_CHECKPOINTS_PER_PROJECT = (
    editor_state_checkpoint_service.MAX_PINNED_CHECKPOINTS_PER_PROJECT
)
MAX_PINNED_BYTES_PER_PROJECT = (
    editor_state_checkpoint_service.MAX_PINNED_BYTES_PER_PROJECT
)
MAX_CHECKPOINTS_PER_PROJECT = (
    editor_state_checkpoint_service.MAX_CHECKPOINTS_PER_PROJECT
)
MAX_SNAPSHOT_BYTES = editor_state_checkpoint_service.MAX_SNAPSHOT_BYTES
MIN_SNAPSHOT_BYTES = editor_state_checkpoint_service.MIN_SNAPSHOT_BYTES
# 锁后最多读 21 行，用于侦测破坏既有 20 条不变量
_PIN_CANDIDATE_PROBE_LIMIT = MAX_CHECKPOINTS_PER_PROJECT + 1


class EditorStateCheckpointPinError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_checkpoints PATCH pin。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _pin_failed() -> EditorStateCheckpointPinError:
    """用途：统一构造脱敏内部失败，禁止附带异常原文。"""
    return EditorStateCheckpointPinError(500, CODE_PIN_FAILED, MSG_PIN_FAILED)


def _pin_invalid() -> EditorStateCheckpointPinError:
    """用途：统一构造脱敏请求无效（服务内兜底）。"""
    return EditorStateCheckpointPinError(422, CODE_PIN_INVALID, MSG_PIN_INVALID)


def normalize_is_pinned(value: Any) -> bool:
    """
    用途：仅接受原生 bool；拒绝 0/1/字符串/null/其它类型。
    """
    if type(value) is not bool:
        raise _pin_invalid() from None
    return value


def _validate_snapshot_bytes(value: Any) -> int:
    """用途：固定配额计算前校验 snapshot_bytes。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise _pin_failed() from None
    if value < MIN_SNAPSHOT_BYTES or value > MAX_SNAPSHOT_BYTES:
        raise _pin_failed() from None
    return value


def _validate_is_pinned_meta(value: Any) -> bool:
    """用途：锁后目标/集合 is_pinned 元数据严格校验。"""
    if isinstance(value, bool):
        return value
    if type(value) is int and value in (0, 1):
        return value == 1
    raise _pin_failed() from None


def _lock_project(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：项目级写锁，与 checkpoint create/restore 同域。
    SQLite：无副作用 UPDATE；其它方言：SELECT FOR UPDATE。
    """
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        result = db.execute(
            update(Project)
            .where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
            .values(updated_at=Project.updated_at)
        )
        if result.rowcount == 0:
            raise EditorStateCheckpointPinError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )
        db.expire_all()
        return

    project = db.execute(
        select(Project)
        .where(
            Project.id == project_id,
            Project.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if project is None:
        raise EditorStateCheckpointPinError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )


def set_editor_state_checkpoint_pin(
    db: Session,
    workspace_id: str,
    project_id: str,
    checkpoint_id: str,
    is_pinned: Any,
) -> bool:
    """
    用途：确认项目后按三重作用域更新恰好一行检查点的 is_pinned。
    对接：PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/pin。
    规则：锁后重读目标与固定集合；同值幂等；超限 409 零写；成功唯一 commit。
    """
    desired = normalize_is_pinned(is_pinned)
    try:
        # 1) 项目级写锁（同时证明项目存在）
        _lock_project(db, workspace_id, project_id)

        # 2) 锁后完整投影同项目最多 21 行：id/snapshot_bytes/原始 is_pinned
        # type_coerce(Integer) 绕过 Boolean result processor，非法 2 不得被吃成 True；
        # 禁止 is_(True) 过滤，否则坏值会被排除在校验集合外。
        rows = list(
            db.execute(
                select(
                    EditorStateCheckpointRow.id,
                    EditorStateCheckpointRow.snapshot_bytes,
                    type_coerce(EditorStateCheckpointRow.is_pinned, Integer).label(
                        "is_pinned"
                    ),
                )
                .where(
                    EditorStateCheckpointRow.workspace_id == workspace_id,
                    EditorStateCheckpointRow.project_id == project_id,
                )
                .order_by(
                    EditorStateCheckpointRow.created_at.desc(),
                    EditorStateCheckpointRow.id.desc(),
                )
                .limit(_PIN_CANDIDATE_PROBE_LIMIT)
            ).all()
        )

        # 候选超过 20 → 破坏既有不变量
        if len(rows) > MAX_CHECKPOINTS_PER_PROJECT:
            raise _pin_failed()

        # 3) 先验证全部元数据，再定位目标与固定集合
        current_pinned: bool | None = None
        target_bytes = 0
        pin_count = 0
        pin_bytes = 0
        for row in rows:
            pinned = _validate_is_pinned_meta(row.is_pinned)
            nbytes = _validate_snapshot_bytes(row.snapshot_bytes)
            if str(row.id) == checkpoint_id:
                current_pinned = pinned
                target_bytes = nbytes
            if pinned:
                pin_count += 1
                pin_bytes += nbytes

        if current_pinned is None:
            raise EditorStateCheckpointPinError(
                404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
            )

        # 同值幂等：不改配额、不写 UPDATE
        if current_pinned is desired:
            db.commit()
            return desired

        # 4) 若将固定：在已验证集合上计入目标后检查上限
        if desired is True:
            if not current_pinned:
                pin_count += 1
                pin_bytes += target_bytes
            if (
                pin_count > MAX_PINNED_CHECKPOINTS_PER_PROJECT
                or pin_bytes > MAX_PINNED_BYTES_PER_PROJECT
            ):
                raise EditorStateCheckpointPinError(
                    409, CODE_PIN_LIMIT, MSG_PIN_LIMIT
                )

        # 5) 三谓词单行 UPDATE
        result = db.execute(
            update(EditorStateCheckpointRow)
            .where(
                EditorStateCheckpointRow.workspace_id == workspace_id,
                EditorStateCheckpointRow.project_id == project_id,
                EditorStateCheckpointRow.id == checkpoint_id,
            )
            .values(is_pinned=desired)
        )
        affected = result.rowcount
        if affected == 0:
            raise EditorStateCheckpointPinError(
                404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
            )
        if affected != 1:
            raise _pin_failed()

        db.flush()
        db.commit()
        return desired
    except EditorStateCheckpointPinError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise _pin_failed() from None
