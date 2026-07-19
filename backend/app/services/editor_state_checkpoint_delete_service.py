"""
模块：P12H editor-state 检查点单条物理删除服务
用途：在当前工作空间/项目三重作用域内删除恰好一条检查点；成功唯一 commit。
对接：api.editor_state_checkpoints DELETE .../editor-state-checkpoints/{checkpointId}；
  EditorStateCheckpointRow；Project。
二次开发：
  - 禁止写入当前态/修订/快照读取；禁止加载 ORM 整实体；
  - 项目确认只投影 Project.id；DELETE 必须 workspace_id+project_id+id；
  - execute/flush/commit 失败必须 rollback，固定 delete_error，禁止拼接异常原文；
  - 成功路径 commit 后禁止 refresh/query/补写检查点。
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateCheckpointRow, Project

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CHECKPOINT_NOT_FOUND = "editor_state_checkpoint_not_found"
MSG_CHECKPOINT_NOT_FOUND = "检查点不存在"
CODE_DELETE_ERROR = "editor_state_checkpoint_delete_error"
MSG_DELETE_ERROR = "删除检查点失败"


class EditorStateCheckpointDeleteError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_checkpoints DELETE。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _delete_failed() -> EditorStateCheckpointDeleteError:
    """用途：统一构造脱敏内部失败，禁止附带异常原文。"""
    return EditorStateCheckpointDeleteError(
        500, CODE_DELETE_ERROR, MSG_DELETE_ERROR
    )


def delete_editor_state_checkpoint(
    db: Session,
    workspace_id: str,
    project_id: str,
    checkpoint_id: str,
) -> None:
    """
    用途：确认项目后按三重作用域物理删除恰好一行检查点。
    对接：DELETE /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}。
    规则：0 行 → checkpoint 404；非 1 行或任意执行失败 → 500；成功唯一 commit。
    """
    try:
        # 1) 项目存在性：只投影 id，限定 workspace/project
        project_row = db.execute(
            select(Project.id).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
        ).first()
        if project_row is None:
            raise EditorStateCheckpointDeleteError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )

        # 2) 三谓词单行 DELETE；禁止按裸 ID 加载 ORM / 读取正文
        result = db.execute(
            delete(EditorStateCheckpointRow).where(
                EditorStateCheckpointRow.workspace_id == workspace_id,
                EditorStateCheckpointRow.project_id == project_id,
                EditorStateCheckpointRow.id == checkpoint_id,
            )
        )
        # 合同：仅精确整数 0 → checkpoint 404；精确 1 → 成功；
        # None/-1/2 等非 1 一律固定 500 + rollback。禁止 int(x or 0) 把 None 当 0。
        affected = result.rowcount
        if affected == 0:
            raise EditorStateCheckpointDeleteError(
                404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
            )
        if affected != 1:
            raise _delete_failed()

        # 3) flush + 唯一 commit；之后禁止 refresh/query
        db.flush()
        db.commit()
        return None
    except EditorStateCheckpointDeleteError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise _delete_failed() from None
