"""
模块：P12C-C1 / P12F-A / P12F-B editor-state 修订历史只读服务
用途：默认最近 10 条修订元数据列表、键集游标页与单条按需详情；绝不加载 snapshot_json。
对接：api.editor_state_revisions；EditorStateRevisionRow；
  editor_state_service / editor_state_revision_service 权威常量与算法。
二次开发：
  - 全程只读：禁止 commit/rollback/flush/refresh/锁/审计/写配额/读当前 editor-state/检查点；
  - 项目校验只投影 Project.id；列表/页五列投影；详情六列 + revision/workspace/project 三重作用域；
  - 列表上限 MAX_REVISIONS_LIST 与页大小 REVISION_PAGE_SIZE 字面量固定 10，禁止绑定写入保留 20；
  - 游标页 LIMIT 11 前瞻、键集谓词、esrc1_ 规范往返；禁止偏移分页/总数查询/正文投影；
  - 13 键/规范 JSON/版本/来源必须委托既有权威实现，禁止第二套哈希或来源枚举；
  - 任一损坏收敛固定 corrupt，不反射正文/ID/版本/SQL/路径/异常。
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.entities import EditorStateRevisionRow, Project
from app.services import editor_state_revision_service, editor_state_service

# P12F-A：默认只读列表与写入保留解耦，字面量固定 10（禁止引用 MAX_REVISIONS_PER_PROJECT）
MAX_REVISIONS_LIST = 10
# P12F-B：游标页固定每页 10；查询 LIMIT = 页大小 + 1
REVISION_PAGE_SIZE = 10
MAX_SNAPSHOT_BYTES = editor_state_revision_service.MAX_SNAPSHOT_BYTES
MIN_SNAPSHOT_BYTES = editor_state_revision_service.MIN_SNAPSHOT_BYTES
REVISION_SOURCE_KINDS = editor_state_revision_service.REVISION_SOURCE_KINDS
SNAPSHOT_KEY_SET = editor_state_service.CANONICAL_STATE_KEY_SET

REVISION_ID_PATTERN = re.compile(r"^esr_[0-9a-f]{32}$")

# P12F-B 规范游标：前缀 + 无填充 base64url(紧凑 JSON{"i","t"})
CURSOR_PREFIX = "esrc1_"
CURSOR_MAX_LEN = 192
CURSOR_PAYLOAD_KEYS = frozenset({"i", "t"})
# UTC 微秒时间位置合法闭区间（含 0 与 9999-12-31 23:59:59.999999）
CURSOR_T_MIN = 0
CURSOR_T_MAX = 253402300799_999999

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_REVISION_NOT_FOUND = "editor_state_revision_not_found"
MSG_REVISION_NOT_FOUND = "修订记录不存在或不可访问"
CODE_REVISION_CORRUPT = "editor_state_revision_corrupt"
MSG_REVISION_CORRUPT = "修订记录数据损坏，无法读取"
CODE_CURSOR_INVALID = "editor_state_revision_cursor_invalid"
MSG_CURSOR_INVALID = "修订分页游标无效"


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


def _cursor_invalid() -> EditorStateRevisionHistoryError:
    """用途：统一构造脱敏非法游标错误，禁止反射游标/ID/时间/异常原文。"""
    return EditorStateRevisionHistoryError(
        400, CODE_CURSOR_INVALID, MSG_CURSOR_INVALID
    )


def _as_utc(dt: datetime) -> datetime:
    """用途：将 datetime 规范为 UTC aware，供微秒编解码。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _datetime_to_us(dt: datetime) -> int:
    """用途：UTC datetime → 自 Unix 纪元起的微秒整数（不经 float）。"""
    aware = _as_utc(dt)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = aware - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _us_to_datetime(us: int) -> datetime:
    """
    用途：UTC 微秒整数 → aware datetime（平台无关）。
    二次开发：禁止依赖平台本地时间戳转换（Windows 在 9999 年边界会抛异常）。
      固定 UTC 纪元 + timedelta(microseconds=us)，保证 CURSOR_T_MIN/MAX 可预测。
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return epoch + timedelta(microseconds=us)


def _canonical_cursor_body(revision_id: str, created_at_us: int) -> str:
    """用途：规范紧凑 JSON + 无填充 base64url 正文（不含前缀）。"""
    payload = {"i": revision_id, "t": created_at_us}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        base64.urlsafe_b64encode(raw.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def encode_revision_page_cursor(created_at: datetime, revision_id: str) -> str:
    """
    用途：由本页末条排序位置生成 esrc1_ 规范游标。
    二次开发：仅含时间微秒与修订 ID；完整长度必须 ≤ CURSOR_MAX_LEN。
      编码端必须严格校验 revision ID 与 UTC 微秒 t 的闭区间；
      存量非法游标位置（如 pre-1970）固定 corrupt，禁止生成解码器必拒的 nextCursor。
    """
    try:
        if not isinstance(revision_id, str) or not REVISION_ID_PATTERN.fullmatch(
            revision_id
        ):
            raise _corrupt() from None
        if not isinstance(created_at, datetime):
            raise _corrupt() from None
        tus = _datetime_to_us(created_at)
        if isinstance(tus, bool) or not isinstance(tus, int):
            raise _corrupt() from None
        if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
            raise _corrupt() from None
        body = _canonical_cursor_body(revision_id, tus)
        cursor = CURSOR_PREFIX + body
        if len(cursor) > CURSOR_MAX_LEN:
            raise _corrupt() from None
        return cursor
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def decode_revision_page_cursor(cursor: str) -> tuple[datetime, str]:
    """
    用途：严格解码并规范往返校验游标；非法一律固定 cursor_invalid。
    规则：前缀 esrc1_、长度、base64url、JSON 对象、精确键 i/t、类型、
      时间闭区间、esr_ ID，且重新编码必须与输入全等。
    """
    if not isinstance(cursor, str) or cursor == "":
        raise _cursor_invalid() from None
    if len(cursor) > CURSOR_MAX_LEN:
        raise _cursor_invalid() from None
    if not cursor.startswith(CURSOR_PREFIX):
        raise _cursor_invalid() from None
    body = cursor[len(CURSOR_PREFIX) :]
    if body == "" or "=" in body:
        raise _cursor_invalid() from None
    try:
        pad = "=" * ((4 - len(body) % 4) % 4)
        # urlsafe_b64decode 不接受 validate；用 b64decode+altchars 严格拒绝非法字符
        raw = base64.b64decode(body + pad, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
        data = json.loads(text)
    except Exception:
        raise _cursor_invalid() from None
    if not isinstance(data, dict):
        raise _cursor_invalid() from None
    if set(data.keys()) != CURSOR_PAYLOAD_KEYS:
        raise _cursor_invalid() from None
    rid = data.get("i")
    tus = data.get("t")
    if not isinstance(rid, str) or not REVISION_ID_PATTERN.fullmatch(rid):
        raise _cursor_invalid() from None
    if isinstance(tus, bool) or not isinstance(tus, int):
        raise _cursor_invalid() from None
    if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
        raise _cursor_invalid() from None
    # 规范往返：拒绝空格、键序、填充、非 sort_keys 等变体
    try:
        expected = CURSOR_PREFIX + _canonical_cursor_body(rid, tus)
    except Exception:
        raise _cursor_invalid() from None
    if expected != cursor:
        raise _cursor_invalid() from None
    try:
        created_at = _us_to_datetime(tus)
    except Exception:
        raise _cursor_invalid() from None
    return created_at, rid


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


def list_editor_state_revisions_page(
    db: Session,
    workspace_id: str,
    project_id: str,
    cursor: str | None = None,
) -> dict[str, Any]:
    """
    用途：固定每页 10 条的只读键集分页；SQL 五列投影 + LIMIT 11 前瞻。
    对接：GET /api/projects/{projectId}/editor-state-revisions/page[?cursor=]。
    二次开发：
      - 先项目存在性，再严格解码游标；非法游标固定 400，不反射输入；
      - 带游标时使用 created_at/id 双键降序键集谓词，禁止偏移/总数；
      - 完整物化并校验最多 11 行（含 lookahead 损坏整页 corrupt）；
      - 仅第 11 行存在时以第 10 条位置生成 nextCursor。
    """
    _require_project_id(db, workspace_id, project_id)

    cursor_created_at: datetime | None = None
    cursor_id: str | None = None
    if cursor is not None:
        cursor_created_at, cursor_id = decode_revision_page_cursor(cursor)

    try:
        stmt = (
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
        )
        if cursor_created_at is not None and cursor_id is not None:
            stmt = stmt.where(
                or_(
                    EditorStateRevisionRow.created_at < cursor_created_at,
                    and_(
                        EditorStateRevisionRow.created_at == cursor_created_at,
                        EditorStateRevisionRow.id < cursor_id,
                    ),
                )
            )
        stmt = stmt.order_by(
            EditorStateRevisionRow.created_at.desc(),
            EditorStateRevisionRow.id.desc(),
        ).limit(REVISION_PAGE_SIZE + 1)
        result = db.execute(stmt)
        rows = _materialize_all(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None

    # 完整校验含 lookahead 的全部行；任一损坏整页固定 corrupt
    validated: list[dict[str, Any]] = []
    for row in rows:
        rid, ver, nbytes, source, created = _validate_meta_fields(
            revision_id=row.id,
            state_version=row.state_version,
            snapshot_bytes=row.snapshot_bytes,
            source_kind=row.source_kind,
            created_at=row.created_at,
        )
        validated.append(
            {
                "revision_id": rid,
                "state_version": ver,
                "snapshot_bytes": nbytes,
                "source_kind": source,
                "created_at": created,
            }
        )

    has_more = len(validated) > REVISION_PAGE_SIZE
    page_items = validated[:REVISION_PAGE_SIZE]
    next_cursor: str | None = None
    if has_more and page_items:
        last = page_items[-1]
        next_cursor = encode_revision_page_cursor(
            last["created_at"], last["revision_id"]
        )
    return {"items": page_items, "next_cursor": next_cursor}


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
