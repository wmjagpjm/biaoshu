"""
模块：P12C-C2 editor-state 修订受限恢复服务
用途：锁后 CAS + C1 目标重验 + 安全检查点 + 13 键写回 + revision_restore 记账。
对接：api.editor_state_revisions POST restore；
  editor_state_checkpoint_service.stage_locked_canonical_restore；
  editor_state_revision_history_service（锁后只读目标）。
二次开发：
  - 禁止复用 checkpoint_restore / 目标原来源 / browser_put 冒充；
  - 禁止 commit 后 refresh/GET；失败三域完整回滚；
  - 不查询目标之外的全局 get；不复制第二套 13 键算法。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services import (
    editor_state_checkpoint_service,
    editor_state_revision_history_service,
    editor_state_service,
)
from app.services.editor_state_checkpoint_service import EditorStateCheckpointError
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
)
from app.services.project_service import ProjectNotFoundError

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_REVISION_NOT_FOUND = "editor_state_revision_not_found"
MSG_REVISION_NOT_FOUND = "修订记录不存在或不可访问"
CODE_REVISION_CORRUPT = "editor_state_revision_corrupt"
MSG_REVISION_CORRUPT = "修订记录数据损坏，无法读取"
CODE_RESTORE_FAILED = "editor_state_revision_restore_failed"
MSG_RESTORE_FAILED = "修订恢复失败，未修改编辑内容"
CODE_CHECKPOINT_TOO_LARGE = "editor_state_checkpoint_too_large"
MSG_CHECKPOINT_TOO_LARGE = "检查点快照超过大小限制，未写入"


class EditorStateRevisionRestoreError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_revisions restore。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _restore_failed() -> EditorStateRevisionRestoreError:
    """用途：统一构造脱敏内部失败，禁止附带异常原文。"""
    return EditorStateRevisionRestoreError(
        500, CODE_RESTORE_FAILED, MSG_RESTORE_FAILED
    )


def restore_editor_state_revision(
    db: Session,
    workspace_id: str,
    project_id: str,
    revision_id: str,
    expected_state_version: str,
) -> dict[str, Any]:
    """
    用途：锁后 CAS + C1 目标重验 + 安全检查点 + 13 键写回 + 条件 revision_restore。
    对接：POST .../editor-state-revisions/{revisionId}/restore。
    固定顺序见契约 §4；唯一 commit；失败三域回滚。
    """
    try:
        # 1) 项目写锁 + 全状态 CAS（陈旧 expected 在任何写入前）
        row, current_state = (
            editor_state_service.lock_and_assert_expected_state_version(
                db, workspace_id, project_id, expected_state_version
            )
        )

        # 2) 锁后调用 C1 权威读取：revision/workspace/project 三重作用域 + 重验
        try:
            target = editor_state_revision_history_service.get_editor_state_revision(
                db, workspace_id, project_id, revision_id
            )
        except EditorStateRevisionHistoryError as exc:
            if exc.status_code == 404:
                raise EditorStateRevisionRestoreError(
                    404, exc.code, exc.message
                ) from None
            if exc.code == CODE_REVISION_CORRUPT or exc.status_code == 500:
                raise EditorStateRevisionRestoreError(
                    500, CODE_REVISION_CORRUPT, MSG_REVISION_CORRUPT
                ) from None
            raise _restore_failed() from None

        target_snapshot = target.get("snapshot")
        target_version = target.get("state_version")
        if not isinstance(target_snapshot, dict) or not isinstance(
            target_version, str
        ):
            raise EditorStateRevisionRestoreError(
                500, CODE_REVISION_CORRUPT, MSG_REVISION_CORRUPT
            )

        # 3–7) 共享无提交原语：安全检查点 + 写回 + 条件 revision_restore + 双配额裁剪
        staged = editor_state_checkpoint_service.stage_locked_canonical_restore(
            db,
            workspace_id,
            project_id,
            row=row,
            current_state=current_state,
            target_snapshot=target_snapshot,
            target_version=target_version,
            source_kind="revision_restore",
        )

        response = {
            "safety_checkpoint_id": staged["safety_checkpoint_id"],
            "state_version": staged["state_version"],
            "restored_at": staged["restored_at"],
        }
        db.commit()
        return response
    except editor_state_service.EditorStateVersionConflict:
        db.rollback()
        raise
    except EditorStateRevisionRestoreError:
        db.rollback()
        raise
    except EditorStateCheckpointError as exc:
        db.rollback()
        if exc.status_code == 413:
            raise EditorStateRevisionRestoreError(
                413, CODE_CHECKPOINT_TOO_LARGE, MSG_CHECKPOINT_TOO_LARGE
            ) from None
        # 写回漂移/安全版本不一致等内部失败 → 固定 restore_failed
        raise _restore_failed() from None
    except ProjectNotFoundError:
        db.rollback()
        raise EditorStateRevisionRestoreError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        ) from None
    except Exception:
        db.rollback()
        raise _restore_failed() from None
