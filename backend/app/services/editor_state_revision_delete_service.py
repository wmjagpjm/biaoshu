"""
模块：P12F-G-A editor-state 修订单条物理删除服务
用途：在当前工作空间/项目三重作用域内删除恰好一条自动修订；成功唯一 commit。
对接：api.editor_state_revisions DELETE .../editor-state-revisions/{revisionId}；
  EditorStateRevisionRow；Project。
二次开发：
  - 禁止写入 history/restore/comparison 服务；禁止加载快照正文/当前态/检查点；
  - 项目确认只投影 Project.id；DELETE 必须 workspace_id+project_id+id；
  - execute/flush/commit 失败必须 rollback，固定 delete_failed，禁止拼接异常原文；
  - 成功路径 commit 后禁止 refresh/query/补写修订。
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateRevisionRow, Project

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_REVISION_NOT_FOUND = "editor_state_revision_not_found"
MSG_REVISION_NOT_FOUND = "修订记录不存在或不可访问"
CODE_DELETE_FAILED = "editor_state_revision_delete_failed"
MSG_DELETE_FAILED = "修订记录删除失败，请稍后重试"


class EditorStateRevisionDeleteError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_revisions DELETE。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _delete_failed() -> EditorStateRevisionDeleteError:
    """用途：统一构造脱敏内部失败，禁止附带异常原文。"""
    return EditorStateRevisionDeleteError(
        500, CODE_DELETE_FAILED, MSG_DELETE_FAILED
    )


def delete_editor_state_revision(
    db: Session,
    workspace_id: str,
    project_id: str,
    revision_id: str,
) -> None:
    """
    用途：确认项目后按三重作用域物理删除恰好一行修订。
    对接：DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}。
    规则：0 行 → revision 404；非 1 行或任意执行失败 → 500；成功唯一 commit。
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
            raise EditorStateRevisionDeleteError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )

        # 2) 三谓词单行 DELETE；禁止按裸 ID 加载 ORM / 读取正文
        result = db.execute(
            delete(EditorStateRevisionRow).where(
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
                EditorStateRevisionRow.id == revision_id,
            )
        )
        # 合同：仅精确整数 0 → revision 404；精确 1 → 成功；
        # None/-1/2 等非 1 一律固定 500 + rollback。禁止 int(x or 0) 把 None 当 0。
        affected = result.rowcount
        if affected == 0:
            raise EditorStateRevisionDeleteError(
                404, CODE_REVISION_NOT_FOUND, MSG_REVISION_NOT_FOUND
            )
        if affected != 1:
            raise _delete_failed()

        # 3) flush + 唯一 commit；之后禁止 refresh/query
        db.flush()
        db.commit()
        return None
    except EditorStateRevisionDeleteError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise _delete_failed() from None
