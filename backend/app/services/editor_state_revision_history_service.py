"""
模块：P12C-C1 editor-state 修订历史只读服务
用途：最近 10 条修订元数据列表与单条按需详情；列表绝不加载 snapshot_json。
对接：api.editor_state_revisions；EditorStateRevisionRow；
  editor_state_service / editor_state_revision_service 权威常量与算法。
二次开发：
  - 全程只读：禁止 commit/rollback/flush/refresh/锁/审计/写配额/读当前 editor-state/检查点；
  - 项目校验只投影 Project.id；列表五列投影；详情六列 + revision/workspace/project 三重作用域；
  - 13 键/规范 JSON/版本/来源必须委托既有权威实现，禁止第二套哈希或来源枚举；
  - 任一损坏收敛固定 corrupt，不反射正文/ID/版本/SQL/路径/异常。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateRevisionRow, Project
from app.services import editor_state_revision_service, editor_state_service

MAX_REVISIONS_LIST = editor_state_revision_service.MAX_REVISIONS_PER_PROJECT
MAX_SNAPSHOT_BYTES = editor_state_revision_service.MAX_SNAPSHOT_BYTES
MIN_SNAPSHOT_BYTES = editor_state_revision_service.MIN_SNAPSHOT_BYTES
REVISION_SOURCE_KINDS = editor_state_revision_service.REVISION_SOURCE_KINDS
SNAPSHOT_KEY_SET = editor_state_service.CANONICAL_STATE_KEY_SET

REVISION_ID_PATTERN = re.compile(r"^esr_[0-9a-f]{32}$")

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_REVISION_NOT_FOUND = "editor_state_revision_not_found"
MSG_REVISION_NOT_FOUND = "修订记录不存在或不可访问"
CODE_REVISION_CORRUPT = "editor_state_revision_corrupt"
MSG_REVISION_CORRUPT = "修订记录数据损坏，无法读取"


class EditorStateRevisionHistoryError(Exception):
    """
    用途：服务层固定错误码/消息，由路由映射 HTTP。
    对接：api.editor_state_revisions。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _corrupt() -> EditorStateRevisionHistoryError:
    """用途：统一构造脱敏损坏错误，禁止附带内部异常细节。"""
    return EditorStateRevisionHistoryError(
        500, CODE_REVISION_CORRUPT, MSG_REVISION_CORRUPT
    )


def _materialize_one_or_none(result: Any) -> Any:
    """
    用途：安全物化 one_or_none；DateTime 等列解码异常收敛为固定 corrupt。
    二次开发：不得吞掉 EditorStateRevisionHistoryError（含业务 not_found）。
    """
    try:
        return result.one_or_none()
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def _materialize_all(result: Any) -> list[Any]:
    """
    用途：安全物化列表结果；任一行列解码失败固定 corrupt，不泄漏异常原文。
    """
    try:
        return list(result.all())
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def _require_project_id(db: Session, workspace_id: str, project_id: str) -> None:
    """
    用途：项目存在性校验；SQL 只投影 Project.id，并限定 workspace_id/project_id。
    """
    try:
        result = db.execute(
            select(Project.id).where(
                Project.id == project_id,
                Project.workspace_id == workspace_id,
            )
        )
        row = _materialize_one_or_none(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None
    if row is None:
        raise EditorStateRevisionHistoryError(
            404, CODE_PROJECT_NOT_FOUND, MSG_PROJECT_NOT_FOUND
        )


def _validate_meta_fields(
    *,
    revision_id: Any,
    state_version: Any,
    snapshot_bytes: Any,
    source_kind: Any,
    created_at: Any,
) -> tuple[str, str, int, str, datetime]:
    """
    用途：严格校验列表/详情共用元数据；任一异常固定 corrupt。
    规则：esr_ ID、esv_ 版本、1..2MiB 字节、固定来源枚举、datetime 时间。
    """
    try:
        if not isinstance(revision_id, str) or not REVISION_ID_PATTERN.fullmatch(
            revision_id
        ):
            raise _corrupt() from None
        if not editor_state_service.is_valid_state_version(state_version):
            raise _corrupt() from None
        if isinstance(snapshot_bytes, bool) or not isinstance(snapshot_bytes, int):
            raise _corrupt() from None
        if (
            snapshot_bytes < MIN_SNAPSHOT_BYTES
            or snapshot_bytes > MAX_SNAPSHOT_BYTES
        ):
            raise _corrupt() from None
        if (
            not isinstance(source_kind, str)
            or source_kind not in REVISION_SOURCE_KINDS
        ):
            raise _corrupt() from None
        if not isinstance(created_at, datetime):
            raise _corrupt() from None
        return (
            revision_id,
            state_version,
            snapshot_bytes,
            source_kind,
            created_at,
        )
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def _validate_snapshot_payload(
    *,
    snapshot_json: Any,
    state_version: str,
    snapshot_bytes: int,
    source_kind: str,
) -> dict[str, Any]:
    """
    用途：详情读取后严格重验 UTF-8 字节、JSON 对象、精确 13 键、
      紧凑 sort_keys 规范 JSON、共享版本算法与固定来源。
    任一不一致固定 corrupt，不反射正文/类型细节。
    """
    try:
        if not isinstance(snapshot_json, str):
            raise _corrupt() from None
        if source_kind not in REVISION_SOURCE_KINDS:
            raise _corrupt() from None

        try:
            raw_bytes = snapshot_json.encode("utf-8")
        except Exception:
            raise _corrupt() from None
        if len(raw_bytes) != snapshot_bytes:
            raise _corrupt() from None

        try:
            data = json.loads(snapshot_json)
        except json.JSONDecodeError:
            raise _corrupt() from None
        if not isinstance(data, dict):
            raise _corrupt() from None
        if set(data.keys()) != SNAPSHOT_KEY_SET:
            raise _corrupt() from None

        try:
            recomputed_json = editor_state_service.canonical_snapshot_json(data)
        except (TypeError, ValueError, OverflowError):
            raise _corrupt() from None
        if recomputed_json != snapshot_json:
            raise _corrupt() from None

        expected_version = (
            editor_state_service.compute_state_version_from_canonical_json(
                recomputed_json
            )
        )
        if expected_version != state_version:
            raise _corrupt() from None
        return data
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def list_editor_state_revisions(
    db: Session,
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """
    用途：固定最近 10 条元数据列表；SQL 显式五列投影，绝不含 snapshot_json。
    对接：GET /api/projects/{projectId}/editor-state-revisions。
    """
    _require_project_id(db, workspace_id, project_id)
    try:
        result = db.execute(
            select(
                EditorStateRevisionRow.id,
                EditorStateRevisionRow.state_version,
                EditorStateRevisionRow.snapshot_bytes,
                EditorStateRevisionRow.source_kind,
                EditorStateRevisionRow.created_at,
            )
            .where(
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .limit(MAX_REVISIONS_LIST)
        )
        rows = _materialize_all(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None

    items: list[dict[str, Any]] = []
    for row in rows:
        rid, ver, nbytes, source, created = _validate_meta_fields(
            revision_id=row.id,
            state_version=row.state_version,
            snapshot_bytes=row.snapshot_bytes,
            source_kind=row.source_kind,
            created_at=row.created_at,
        )
        items.append(
            {
                "revision_id": rid,
                "state_version": ver,
                "snapshot_bytes": nbytes,
                "source_kind": source,
                "created_at": created,
            }
        )
    return {"items": items}


def get_editor_state_revision(
    db: Session,
    workspace_id: str,
    project_id: str,
    revision_id: str,
) -> dict[str, Any]:
    """
    用途：按 ID 读取单条修订并重验规范快照；跨项目/空间统一 not_found。
    对接：GET .../editor-state-revisions/{revisionId}。
    二次开发：SQL 必须同时带 id/workspace_id/project_id，禁止先全局 get 再 Python 过滤。
    """
    _require_project_id(db, workspace_id, project_id)
    try:
        result = db.execute(
            select(
                EditorStateRevisionRow.id,
                EditorStateRevisionRow.state_version,
                EditorStateRevisionRow.snapshot_bytes,
                EditorStateRevisionRow.source_kind,
                EditorStateRevisionRow.created_at,
                EditorStateRevisionRow.snapshot_json,
            ).where(
                EditorStateRevisionRow.id == revision_id,
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
            )
        )
        row = _materialize_one_or_none(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None
    if row is None:
        raise EditorStateRevisionHistoryError(
            404, CODE_REVISION_NOT_FOUND, MSG_REVISION_NOT_FOUND
        )

    rid, ver, nbytes, source, created = _validate_meta_fields(
        revision_id=row.id,
        state_version=row.state_version,
        snapshot_bytes=row.snapshot_bytes,
        source_kind=row.source_kind,
        created_at=row.created_at,
    )
    snapshot = _validate_snapshot_payload(
        snapshot_json=row.snapshot_json,
        state_version=ver,
        snapshot_bytes=nbytes,
        source_kind=source,
    )
    return {
        "revision_id": rid,
        "state_version": ver,
        "snapshot_bytes": nbytes,
        "source_kind": source,
        "created_at": created,
        "snapshot": snapshot,
    }
