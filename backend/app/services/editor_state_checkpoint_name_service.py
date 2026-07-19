"""
模块：P12G editor-state 检查点单条展示名称服务
用途：在当前工作空间/项目三重作用域内更新恰好一条检查点的 display_name；成功唯一 commit。
对接：api.editor_state_checkpoints PATCH .../display-name；
  EditorStateCheckpointRow；Project。
二次开发：
  - 禁止加载快照正文 / ORM 整实体 / 当前态 / 修订；
  - 项目确认只投影 Project.id；UPDATE 必须 workspace_id+project_id+id 且只写 display_name；
  - execute/flush/commit 失败必须 rollback，固定 display_name_error，禁止拼接异常原文；
  - 成功路径 commit 后禁止 refresh/query/补写检查点或其它域。
"""

from __future__ import annotations

import unicodedata
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.entities import EditorStateCheckpointRow, Project

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在"
CODE_CHECKPOINT_NOT_FOUND = "editor_state_checkpoint_not_found"
MSG_CHECKPOINT_NOT_FOUND = "检查点不存在"
CODE_NAME_INVALID = "editor_state_checkpoint_display_name_invalid"
MSG_NAME_INVALID = "检查点名称无效"
CODE_NAME_ERROR = "editor_state_checkpoint_display_name_error"
MSG_NAME_ERROR = "保存检查点名称失败"

DISPLAY_NAME_MIN_LEN = 1
DISPLAY_NAME_MAX_LEN = 40
# Unicode 双向控制字符（含 LRE/RLE/PDF/LRO/RLO 与 isolate 系列）
_BIDI_CONTROLS = frozenset(
    {
        "\u061c",  # ALM
        "\u200e",  # LRM
        "\u200f",  # RLM
        "\u202a",  # LRE
        "\u202b",  # RLE
        "\u202c",  # PDF
        "\u202d",  # LRO
        "\u202e",  # RLO
        "\u2066",  # LRI
        "\u2067",  # RLI
        "\u2068",  # FSI
        "\u2069",  # PDI
    }
)


class EditorStateCheckpointNameError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_checkpoints PATCH display-name。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _name_failed() -> EditorStateCheckpointNameError:
    """用途：统一构造脱敏内部失败，禁止附带异常原文。"""
    return EditorStateCheckpointNameError(500, CODE_NAME_ERROR, MSG_NAME_ERROR)


def _name_invalid() -> EditorStateCheckpointNameError:
    """用途：统一构造脱敏名称无效，禁止反射输入。"""
    return EditorStateCheckpointNameError(422, CODE_NAME_INVALID, MSG_NAME_INVALID)


def _char_forbidden(ch: str) -> bool:
    """
    用途：拒绝 C0/C1、换行/制表/NUL、U+2028/U+2029 与双向控制字符。
    """
    code = ord(ch)
    # C0（含 \\t\\n\\r）、DEL、C1
    if code < 0x20 or code == 0x7F or (0x80 <= code <= 0x9F):
        return True
    if ch in ("\u2028", "\u2029"):
        return True
    if ch in _BIDI_CONTROLS:
        return True
    return False


def normalize_display_name(value: Any) -> str | None:
    """
    用途：规范化请求体 displayName；null 清除；字符串 NFKC 后 1..40 码点。
    规则：原生 str/null；首尾无空白；无控制/双向字符；错误固定不反射。
    """
    if value is None:
        return None
    if type(value) is not str:
        raise _name_invalid() from None
    if value == "" or value.strip() != value:
        raise _name_invalid() from None
    for ch in value:
        if _char_forbidden(ch):
            raise _name_invalid() from None
    normalized = unicodedata.normalize("NFKC", value)
    if normalized == "" or normalized.strip() != normalized:
        raise _name_invalid() from None
    for ch in normalized:
        if _char_forbidden(ch):
            raise _name_invalid() from None
    n = len(normalized)
    if n < DISPLAY_NAME_MIN_LEN or n > DISPLAY_NAME_MAX_LEN:
        raise _name_invalid() from None
    return normalized


def set_editor_state_checkpoint_display_name(
    db: Session,
    workspace_id: str,
    project_id: str,
    checkpoint_id: str,
    display_name: Any,
) -> str | None:
    """
    用途：确认项目后按三重作用域更新恰好一行检查点的 display_name。
    对接：PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name。
    规则：0 行 → checkpoint 404；精确 1 → 成功；None/负数/非 1 → 500；成功唯一 commit。
    """
    normalized = normalize_display_name(display_name)
    try:
        # 1) 项目存在性：只投影 id，限定 workspace/project
        project_row = db.execute(
            select(Project.id).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
        ).first()
        if project_row is None:
            raise EditorStateCheckpointNameError(
                404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
            )

        # 2) 三谓词单列 UPDATE；禁止按裸 ID 加载 ORM / 读取快照正文
        result = db.execute(
            update(EditorStateCheckpointRow)
            .where(
                EditorStateCheckpointRow.workspace_id == workspace_id,
                EditorStateCheckpointRow.project_id == project_id,
                EditorStateCheckpointRow.id == checkpoint_id,
            )
            .values(display_name=normalized)
        )
        # 合同：仅精确整数 0 → checkpoint 404；精确 1 → 成功；
        # None/-1/2 等非 1 一律固定 500 + rollback。禁止 int(x or 0) 把 None 当 0。
        affected = result.rowcount
        if affected == 0:
            raise EditorStateCheckpointNameError(
                404, CODE_CHECKPOINT_NOT_FOUND, MSG_CHECKPOINT_NOT_FOUND
            )
        if affected != 1:
            raise _name_failed()

        # 3) flush + 唯一 commit；之后禁止 refresh/query
        db.flush()
        db.commit()
        return normalized
    except EditorStateCheckpointNameError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise _name_failed() from None
