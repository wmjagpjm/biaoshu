"""
模块：P12C-C1 / P12F-A / P12F-B / P12F-D / P12F-E-A / P12F-F-A / P12F-I / P12F-J-B
  editor-state 修订历史只读服务
用途：默认最近 10 条修订元数据列表、键集游标页、可选 sourceKind/时间范围筛选、
  有界名称与可见内容联合搜索与单条按需详情；list/page 七列（含 display_name/原始 is_pinned）且绝不读 snapshot_json；
  detail/search 八列含 snapshot_json + 原始 is_pinned，其中 search 候选有界。
对接：api.editor_state_revisions；EditorStateRevisionRow；
  editor_state_service / editor_state_revision_service 权威常量与算法。
二次开发：
  - 全程只读：禁止 commit/rollback/flush/refresh/锁/审计/写配额/读当前 editor-state/检查点；
  - 项目校验只投影 Project.id；列表/页七列投影（含原始 is_pinned）且绝不读 snapshot_json；
    详情/搜索八列含 snapshot_json + 原始 is_pinned（search 候选有界）+ workspace/project 作用域；
  - 列表上限 MAX_REVISIONS_LIST 与页大小 REVISION_PAGE_SIZE 字面量固定 10，禁止绑定写入保留 20；
  - 搜索候选窗 LIMIT 20 固定，不补扫第 21 条；先完整校验再名称/内容联合匹配；
  - 游标页 LIMIT 11 前瞻、键集谓词；无时间无来源 esrc1；仅来源 esrc2；任一时间边界 esrc3；
  - esrc3 载荷 {b,f,i,s,t} 绑定显式时间/来源，禁止从游标采用筛选条件；
  - 13 键/规范 JSON/版本/来源必须委托既有权威实现，禁止第二套哈希或来源枚举；
  - 任一损坏收敛固定 corrupt，不反射正文/ID/版本/SQL/路径/关键词/异常。
"""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Integer, and_, or_, select, type_coerce
from sqlalchemy.orm import Session

from app.models.entities import EditorStateRevisionRow, Project
from app.services import editor_state_revision_service, editor_state_service

# P12F-A：默认只读列表与写入保留解耦，字面量固定 10（禁止引用 MAX_REVISIONS_PER_PROJECT）
MAX_REVISIONS_LIST = 10
# P12F-B：游标页固定每页 10；查询 LIMIT = 页大小 + 1
REVISION_PAGE_SIZE = 10
# P12F-F-A：搜索只扫元数据条件下最新 20 条候选（与写入保留上限对齐）
REVISION_SEARCH_CANDIDATE_LIMIT = 20
# P12F-F-A：单快照允许对象/字符串叶硬上限（规范化前计数）
REVISION_SEARCH_MAX_OBJECTS = 4096
REVISION_SEARCH_MAX_STRING_LEAVES = 8192
# P12F-F-A：关键词规范化后 Unicode 码点闭区间
REVISION_SEARCH_QUERY_MIN_LEN = 1
REVISION_SEARCH_QUERY_MAX_LEN = 64
MAX_SNAPSHOT_BYTES = editor_state_revision_service.MAX_SNAPSHOT_BYTES
MIN_SNAPSHOT_BYTES = editor_state_revision_service.MIN_SNAPSHOT_BYTES
REVISION_SOURCE_KINDS = editor_state_revision_service.REVISION_SOURCE_KINDS
SNAPSHOT_KEY_SET = editor_state_service.CANONICAL_STATE_KEY_SET

REVISION_ID_PATTERN = re.compile(r"^esr_[0-9a-f]{32}$")

# P12F-B 规范游标：前缀 + 无填充 base64url(紧凑 JSON{"i","t"})
CURSOR_PREFIX = "esrc1_"
# P12F-D 筛选游标：前缀 + 无填充 base64url(紧凑 JSON{"i","s","t"})
CURSOR_PREFIX_V2 = "esrc2_"
# P12F-E-A 时间范围游标：前缀 + 无填充 base64url(紧凑 JSON{"b","f","i","s","t"})
CURSOR_PREFIX_V3 = "esrc3_"
CURSOR_MAX_LEN = 192
CURSOR_MAX_LEN_V3 = 256
CURSOR_PAYLOAD_KEYS = frozenset({"i", "t"})
CURSOR_PAYLOAD_KEYS_V2 = frozenset({"i", "s", "t"})
CURSOR_PAYLOAD_KEYS_V3 = frozenset({"b", "f", "i", "s", "t"})
# UTC 微秒时间位置合法闭区间（含 0 与 9999-12-31 23:59:59.999999）
CURSOR_T_MIN = 0
CURSOR_T_MAX = 253402300799_999999

# P12F-E-A：严格 24 字符 UTC 毫秒 RFC3339（大写 T/Z、三位毫秒）
_TIME_BOUND_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
_TIME_BOUND_MIN = datetime(1970, 1, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
_TIME_BOUND_MAX = datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=timezone.utc)

CODE_PROJECT_NOT_FOUND = "project_not_found"
MSG_PROJECT_NOT_FOUND = "项目不存在或不可访问"
CODE_REVISION_NOT_FOUND = "editor_state_revision_not_found"
MSG_REVISION_NOT_FOUND = "修订记录不存在或不可访问"
CODE_REVISION_CORRUPT = "editor_state_revision_corrupt"
MSG_REVISION_CORRUPT = "修订记录数据损坏，无法读取"
CODE_CURSOR_INVALID = "editor_state_revision_cursor_invalid"
MSG_CURSOR_INVALID = "修订分页游标无效"
CODE_SOURCE_INVALID = "editor_state_revision_source_invalid"
MSG_SOURCE_INVALID = "修订来源筛选无效"
CODE_TIME_RANGE_INVALID = "editor_state_revision_time_range_invalid"
MSG_TIME_RANGE_INVALID = "修订时间范围筛选无效"
CODE_SEARCH_QUERY_INVALID = "editor_state_revision_search_query_invalid"
MSG_SEARCH_QUERY_INVALID = "修订搜索关键词无效"


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


def _source_invalid() -> EditorStateRevisionHistoryError:
    """用途：统一构造脱敏非法来源筛选错误，禁止反射输入/枚举细节。"""
    return EditorStateRevisionHistoryError(
        400, CODE_SOURCE_INVALID, MSG_SOURCE_INVALID
    )


def _time_range_invalid() -> EditorStateRevisionHistoryError:
    """用途：统一构造脱敏非法时间范围错误，禁止反射输入。"""
    return EditorStateRevisionHistoryError(
        400, CODE_TIME_RANGE_INVALID, MSG_TIME_RANGE_INVALID
    )


def _search_query_invalid() -> EditorStateRevisionHistoryError:
    """用途：统一构造脱敏非法搜索关键词错误，禁止反射原值。"""
    return EditorStateRevisionHistoryError(
        400, CODE_SEARCH_QUERY_INVALID, MSG_SEARCH_QUERY_INVALID
    )


def _normalize_source_kind_filter(source_kind: str | None) -> str | None:
    """
    用途：规范化可选 sourceKind 筛选。
    规则：None 表示全部；空串/空白/大小写变体/别名/非法值固定 source_invalid。
    """
    if source_kind is None:
        return None
    if not isinstance(source_kind, str):
        raise _source_invalid() from None
    # 缺省由调用方传 None；显式空串/空白/非权威字面量一律 400，不 strip 后接纳
    if source_kind == "" or source_kind.strip() != source_kind or source_kind.strip() == "":
        raise _source_invalid() from None
    if source_kind not in REVISION_SOURCE_KINDS:
        raise _source_invalid() from None
    return source_kind


def _parse_time_bound_literal(value: str) -> datetime:
    """
    用途：解析严格 24 字符 UTC 毫秒字面量 YYYY-MM-DDTHH:MM:SS.sssZ。
    规则：仅大写 T/Z、三位毫秒、合法日历、闭区间 1970..9999；拒绝空白/偏移/别名。
    """
    if not isinstance(value, str):
        raise _time_range_invalid() from None
    if len(value) != 24 or not value.isascii():
        raise _time_range_invalid() from None
    if _TIME_BOUND_RE.fullmatch(value) is None:
        raise _time_range_invalid() from None
    try:
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        raise _time_range_invalid() from None
    if dt < _TIME_BOUND_MIN or dt > _TIME_BOUND_MAX:
        raise _time_range_invalid() from None
    return dt


def _normalize_time_range_filter(
    created_from: str | None,
    created_before: str | None,
) -> tuple[datetime | None, datetime | None]:
    """
    用途：规范化可选 createdFrom/createdBefore。
    规则：None 表示该边界缺失；双边必须严格 from < before；非法固定 time_range_invalid。
    """
    from_dt: datetime | None = None
    before_dt: datetime | None = None
    if created_from is not None:
        from_dt = _parse_time_bound_literal(created_from)
    if created_before is not None:
        before_dt = _parse_time_bound_literal(created_before)
    if from_dt is not None and before_dt is not None:
        if not (from_dt < before_dt):
            raise _time_range_invalid() from None
    return from_dt, before_dt


def _time_bound_to_us(dt: datetime | None) -> int | None:
    """用途：时间边界 datetime → 游标载荷微秒整数；None 保持 null。"""
    if dt is None:
        return None
    return _datetime_to_us(dt)


def _validate_optional_bound_us(value: Any) -> int | None:
    """用途：校验 esrc3 载荷 f/b：JSON null 或非布尔整数且落在游标时间闭区间。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _cursor_invalid() from None
    if value < CURSOR_T_MIN or value > CURSOR_T_MAX:
        raise _cursor_invalid() from None
    return value


def _esrc3_time_semantics_ok(
    from_us: int | None,
    before_us: int | None,
    tus: int,
) -> bool:
    """
    用途：esrc3 时间语义一致性（类型/闭区间已通过后）。
    规则：V3 仅在任一边界激活时合法；双边严格 f < b；
      t 须为结果集内位置（下界包含 t>=f，上界排除 t<b）。
    """
    if from_us is None and before_us is None:
        return False
    if from_us is not None and before_us is not None and from_us >= before_us:
        return False
    if from_us is not None and tus < from_us:
        return False
    if before_us is not None and tus >= before_us:
        return False
    return True


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
    """用途：esrc1 规范紧凑 JSON + 无填充 base64url 正文（不含前缀）。"""
    payload = {"i": revision_id, "t": created_at_us}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        base64.urlsafe_b64encode(raw.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _canonical_cursor_body_v2(
    revision_id: str, source_kind: str, created_at_us: int
) -> str:
    """用途：esrc2 规范紧凑 JSON{"i","s","t"} + 无填充 base64url 正文。"""
    payload = {"i": revision_id, "s": source_kind, "t": created_at_us}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return (
        base64.urlsafe_b64encode(raw.encode("utf-8"))
        .decode("ascii")
        .rstrip("=")
    )


def _canonical_cursor_body_v3(
    *,
    revision_id: str,
    created_at_us: int,
    from_us: int | None,
    before_us: int | None,
    source_kind: str | None,
) -> str:
    """用途：esrc3 规范紧凑 JSON{"b","f","i","s","t"} + 无填充 base64url 正文。"""
    payload = {
        "b": before_us,
        "f": from_us,
        "i": revision_id,
        "s": source_kind,
        "t": created_at_us,
    }
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
    用途：由本页末条排序位置生成 esrc1_ 规范游标（无筛选页）。
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


def encode_revision_page_cursor_v2(
    created_at: datetime, revision_id: str, source_kind: str
) -> str:
    """
    用途：有筛选页末条位置生成 esrc2_ 规范游标，载荷精确 {i,s,t}。
    二次开发：s 必须为权威来源字面量；长度 ≤ CURSOR_MAX_LEN；非法存量位置固定 corrupt。
    """
    try:
        if not isinstance(revision_id, str) or not REVISION_ID_PATTERN.fullmatch(
            revision_id
        ):
            raise _corrupt() from None
        if (
            not isinstance(source_kind, str)
            or source_kind not in REVISION_SOURCE_KINDS
        ):
            raise _corrupt() from None
        if not isinstance(created_at, datetime):
            raise _corrupt() from None
        tus = _datetime_to_us(created_at)
        if isinstance(tus, bool) or not isinstance(tus, int):
            raise _corrupt() from None
        if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
            raise _corrupt() from None
        body = _canonical_cursor_body_v2(revision_id, source_kind, tus)
        cursor = CURSOR_PREFIX_V2 + body
        if len(cursor) > CURSOR_MAX_LEN:
            raise _corrupt() from None
        return cursor
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def encode_revision_page_cursor_v3(
    created_at: datetime,
    revision_id: str,
    *,
    from_us: int | None,
    before_us: int | None,
    source_kind: str | None,
) -> str:
    """
    用途：时间范围页末条位置生成 esrc3_ 规范游标，载荷精确 {b,f,i,s,t}。
    二次开发：f/b 为边界 UTC 微秒或 null；s 为权威来源或 null；长度 ≤ 256。
      类型/闭区间后拒绝：f/b 双 null、双边 f>=b、t 低于 f、t 达到/超过 b；
      非法存量位置固定 corrupt，禁止生成不可能来自结果集的 V3。
    """
    try:
        if not isinstance(revision_id, str) or not REVISION_ID_PATTERN.fullmatch(
            revision_id
        ):
            raise _corrupt() from None
        if source_kind is not None:
            if (
                not isinstance(source_kind, str)
                or source_kind not in REVISION_SOURCE_KINDS
            ):
                raise _corrupt() from None
        if from_us is not None:
            if isinstance(from_us, bool) or not isinstance(from_us, int):
                raise _corrupt() from None
            if from_us < CURSOR_T_MIN or from_us > CURSOR_T_MAX:
                raise _corrupt() from None
        if before_us is not None:
            if isinstance(before_us, bool) or not isinstance(before_us, int):
                raise _corrupt() from None
            if before_us < CURSOR_T_MIN or before_us > CURSOR_T_MAX:
                raise _corrupt() from None
        if not isinstance(created_at, datetime):
            raise _corrupt() from None
        tus = _datetime_to_us(created_at)
        if isinstance(tus, bool) or not isinstance(tus, int):
            raise _corrupt() from None
        if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
            raise _corrupt() from None
        # 语义一致性：不得编码不可能来自时间范围结果集的位置
        if not _esrc3_time_semantics_ok(from_us, before_us, tus):
            raise _corrupt() from None
        body = _canonical_cursor_body_v3(
            revision_id=revision_id,
            created_at_us=tus,
            from_us=from_us,
            before_us=before_us,
            source_kind=source_kind,
        )
        cursor = CURSOR_PREFIX_V3 + body
        if len(cursor) > CURSOR_MAX_LEN_V3:
            raise _corrupt() from None
        return cursor
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None


def decode_revision_page_cursor(cursor: str) -> tuple[datetime, str]:
    """
    用途：严格解码 esrc1 并规范往返校验；非法一律固定 cursor_invalid。
    规则：前缀 esrc1_、长度、base64url、JSON 对象、精确键 i/t、类型、
      时间闭区间、esr_ ID，且重新编码必须与输入全等。
    """
    if not isinstance(cursor, str) or cursor == "":
        raise _cursor_invalid() from None
    if len(cursor) > CURSOR_MAX_LEN:
        raise _cursor_invalid() from None
    if not cursor.startswith(CURSOR_PREFIX):
        raise _cursor_invalid() from None
    # 防止 esrc1 前缀误匹配 esrc10_ 等；esrc2/esrc3 不得走本函数
    if cursor.startswith(CURSOR_PREFIX_V2) or cursor.startswith(CURSOR_PREFIX_V3):
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


def decode_revision_page_cursor_v2(cursor: str) -> tuple[datetime, str, str]:
    """
    用途：严格解码 esrc2 并规范往返校验；返回 (created_at, revision_id, source_kind)。
    规则：前缀 esrc2_、精确键 i/s/t、s 为权威来源、规范全等往返。
    """
    if not isinstance(cursor, str) or cursor == "":
        raise _cursor_invalid() from None
    if len(cursor) > CURSOR_MAX_LEN:
        raise _cursor_invalid() from None
    if not cursor.startswith(CURSOR_PREFIX_V2):
        raise _cursor_invalid() from None
    body = cursor[len(CURSOR_PREFIX_V2) :]
    if body == "" or "=" in body:
        raise _cursor_invalid() from None
    try:
        pad = "=" * ((4 - len(body) % 4) % 4)
        raw = base64.b64decode(body + pad, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
        data = json.loads(text)
    except Exception:
        raise _cursor_invalid() from None
    if not isinstance(data, dict):
        raise _cursor_invalid() from None
    if set(data.keys()) != CURSOR_PAYLOAD_KEYS_V2:
        raise _cursor_invalid() from None
    rid = data.get("i")
    source = data.get("s")
    tus = data.get("t")
    if not isinstance(rid, str) or not REVISION_ID_PATTERN.fullmatch(rid):
        raise _cursor_invalid() from None
    if not isinstance(source, str) or source not in REVISION_SOURCE_KINDS:
        raise _cursor_invalid() from None
    if isinstance(tus, bool) or not isinstance(tus, int):
        raise _cursor_invalid() from None
    if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
        raise _cursor_invalid() from None
    try:
        expected = CURSOR_PREFIX_V2 + _canonical_cursor_body_v2(rid, source, tus)
    except Exception:
        raise _cursor_invalid() from None
    if expected != cursor:
        raise _cursor_invalid() from None
    try:
        created_at = _us_to_datetime(tus)
    except Exception:
        raise _cursor_invalid() from None
    return created_at, rid, source


def decode_revision_page_cursor_v3(
    cursor: str,
) -> tuple[datetime, str, int | None, int | None, str | None]:
    """
    用途：严格解码 esrc3 并规范往返校验；
      返回 (created_at, revision_id, from_us, before_us, source_kind)。
    规则：前缀 esrc3_、长度≤256、精确键 b/f/i/s/t、null/整数类型、规范全等往返。
    """
    if not isinstance(cursor, str) or cursor == "":
        raise _cursor_invalid() from None
    if len(cursor) > CURSOR_MAX_LEN_V3:
        raise _cursor_invalid() from None
    if not cursor.startswith(CURSOR_PREFIX_V3):
        raise _cursor_invalid() from None
    body = cursor[len(CURSOR_PREFIX_V3) :]
    if body == "" or "=" in body:
        raise _cursor_invalid() from None
    try:
        pad = "=" * ((4 - len(body) % 4) % 4)
        raw = base64.b64decode(body + pad, altchars=b"-_", validate=True)
        text = raw.decode("utf-8")
        data = json.loads(text)
    except Exception:
        raise _cursor_invalid() from None
    if not isinstance(data, dict):
        raise _cursor_invalid() from None
    if set(data.keys()) != CURSOR_PAYLOAD_KEYS_V3:
        raise _cursor_invalid() from None
    rid = data.get("i")
    tus = data.get("t")
    from_us = _validate_optional_bound_us(data.get("f"))
    before_us = _validate_optional_bound_us(data.get("b"))
    source = data.get("s")
    if not isinstance(rid, str) or not REVISION_ID_PATTERN.fullmatch(rid):
        raise _cursor_invalid() from None
    if isinstance(tus, bool) or not isinstance(tus, int):
        raise _cursor_invalid() from None
    if tus < CURSOR_T_MIN or tus > CURSOR_T_MAX:
        raise _cursor_invalid() from None
    if source is not None:
        if not isinstance(source, str) or source not in REVISION_SOURCE_KINDS:
            raise _cursor_invalid() from None
    # 与编码器同一语义：双 null / f>=b / t 不在结果集 → 固定 cursor_invalid
    if not _esrc3_time_semantics_ok(from_us, before_us, tus):
        raise _cursor_invalid() from None
    try:
        expected = CURSOR_PREFIX_V3 + _canonical_cursor_body_v3(
            revision_id=rid,
            created_at_us=tus,
            from_us=from_us,
            before_us=before_us,
            source_kind=source,
        )
    except Exception:
        raise _cursor_invalid() from None
    if expected != cursor:
        raise _cursor_invalid() from None
    try:
        created_at = _us_to_datetime(tus)
    except Exception:
        raise _cursor_invalid() from None
    return created_at, rid, from_us, before_us, source


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


def _char_forbidden_in_display_name(ch: str) -> bool:
    """用途：读取路径拒绝控制/行分隔/双向字符；与命名服务可见安全规则对齐。"""
    code = ord(ch)
    if code < 0x20 or code == 0x7F or (0x80 <= code <= 0x9F):
        return True
    if ch in ("\u2028", "\u2029"):
        return True
    if ch in (
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    ):
        return True
    return False


def _validate_stored_display_name(value: Any) -> str | None:
    """
    用途：严格校验库内 display_name；null 合法；坏类型/长度/字符固定 corrupt。
    规则：已规范字符串须等于 NFKC、首尾无空白、1..40 码点、无控制/双向字符。
    """
    if value is None:
        return None
    if type(value) is not str:
        raise _corrupt() from None
    if value == "" or value.strip() != value:
        raise _corrupt() from None
    for ch in value:
        if _char_forbidden_in_display_name(ch):
            raise _corrupt() from None
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value:
        raise _corrupt() from None
    n = len(value)
    if n < 1 or n > 40:
        raise _corrupt() from None
    return value


def _validate_is_pinned_raw(value: Any) -> bool:
    """
    用途：严格校验 type_coerce 后的原始 is_pinned。
    规则：仅原生 int 且恰为 0/1；拒绝 bool/其它类型/非法整数（含 2）。
    """
    if type(value) is int and value in (0, 1):
        return value == 1
    raise _corrupt() from None


def _validate_meta_fields(
    *,
    revision_id: Any,
    state_version: Any,
    snapshot_bytes: Any,
    source_kind: Any,
    created_at: Any,
    display_name: Any = None,
    is_pinned: Any = None,
) -> tuple[str, str, int, str, datetime, str | None, bool]:
    """
    用途：严格校验列表/详情共用元数据；任一异常固定 corrupt。
    规则：esr_ ID、esv_ 版本、1..2MiB 字节、固定来源枚举、datetime 时间、
      可选 displayName、原始 int is_pinned 0/1。
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
        name = _validate_stored_display_name(display_name)
        pinned = _validate_is_pinned_raw(is_pinned)
        return (
            revision_id,
            state_version,
            snapshot_bytes,
            source_kind,
            created_at,
            name,
            pinned,
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
    用途：固定最近 10 条元数据列表；SQL 显式七列投影（含 display_name/原始 is_pinned），
      绝不含 snapshot_json。
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
                EditorStateRevisionRow.display_name,
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
            .limit(MAX_REVISIONS_LIST)
        )
        rows = _materialize_all(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None

    items: list[dict[str, Any]] = []
    for row in rows:
        rid, ver, nbytes, source, created, dname, pinned = _validate_meta_fields(
            revision_id=row.id,
            state_version=row.state_version,
            snapshot_bytes=row.snapshot_bytes,
            source_kind=row.source_kind,
            created_at=row.created_at,
            display_name=row.display_name,
            is_pinned=row.is_pinned,
        )
        items.append(
            {
                "revision_id": rid,
                "state_version": ver,
                "snapshot_bytes": nbytes,
                "source_kind": source,
                "created_at": created,
                "display_name": dname,
                "is_pinned": pinned,
            }
        )
    return {"items": items}


def list_editor_state_revisions_page(
    db: Session,
    workspace_id: str,
    project_id: str,
    cursor: str | None = None,
    source_kind: str | None = None,
    created_from: str | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    """
    用途：固定每页 10 条的只读键集分页；可选 sourceKind 与 createdFrom/createdBefore；
      SQL 五列投影 + LIMIT 11 前瞻。
    对接：GET .../page[?sourceKind=&createdFrom=&createdBefore=&cursor=]。
    二次开发：
      - 先项目存在性（404 最优先）；
      - esrc3 形游标：任何非法/缺失/错配来源或时间条件均 cursor_invalid，禁止从游标采用；
      - esrc2 形游标：先绑定来源合同，再处理时间/版本；
      - 无 V3 形：先来源校验，再时间范围，最后游标版本；
      - 无时间无来源 esrc1；仅来源 esrc2；任一时间边界 esrc3；
      - SQL 谓词仅用显式 query 校验后的 filter_kind/from/before；
      - 完整物化并校验最多 11 行（含 lookahead 损坏整页 corrupt）。
    """
    _require_project_id(db, workspace_id, project_id)

    esrc2_shaped = (
        cursor is not None
        and isinstance(cursor, str)
        and cursor.startswith(CURSOR_PREFIX_V2)
    )
    esrc3_shaped = (
        cursor is not None
        and isinstance(cursor, str)
        and cursor.startswith(CURSOR_PREFIX_V3)
    )

    cursor_created_at: datetime | None = None
    cursor_id: str | None = None
    filter_kind: str | None
    from_dt: datetime | None
    before_dt: datetime | None

    if esrc3_shaped:
        # V3 绑定合同：任何非法/缺失/错配来源或时间 → cursor_invalid
        try:
            filter_kind = _normalize_source_kind_filter(source_kind)
        except EditorStateRevisionHistoryError as exc:
            if exc.code == CODE_SOURCE_INVALID:
                raise _cursor_invalid() from None
            raise
        try:
            from_dt, before_dt = _normalize_time_range_filter(
                created_from, created_before
            )
        except EditorStateRevisionHistoryError as exc:
            if exc.code == CODE_TIME_RANGE_INVALID:
                raise _cursor_invalid() from None
            raise
        # 无时间范围时携 esrc3 固定 cursor-invalid
        if from_dt is None and before_dt is None:
            raise _cursor_invalid() from None
        (
            cursor_created_at,
            cursor_id,
            cursor_from_us,
            cursor_before_us,
            cursor_source,
        ) = decode_revision_page_cursor_v3(cursor)
        expected_from_us = _time_bound_to_us(from_dt)
        expected_before_us = _time_bound_to_us(before_dt)
        if cursor_from_us != expected_from_us:
            raise _cursor_invalid() from None
        if cursor_before_us != expected_before_us:
            raise _cursor_invalid() from None
        if cursor_source != filter_kind:
            raise _cursor_invalid() from None
    elif esrc2_shaped:
        # esrc2 绑定合同优先：缺筛选/非法筛选/错配 → cursor_invalid
        try:
            filter_kind = _normalize_source_kind_filter(source_kind)
        except EditorStateRevisionHistoryError as exc:
            if exc.code == CODE_SOURCE_INVALID:
                raise _cursor_invalid() from None
            raise
        if filter_kind is None:
            raise _cursor_invalid() from None
        cursor_created_at, cursor_id, cursor_source = (
            decode_revision_page_cursor_v2(cursor)
        )
        if cursor_source != filter_kind:
            raise _cursor_invalid() from None
        # 绑定通过后：校验时间；时间范围激活时 esrc2 固定 cursor-invalid
        from_dt, before_dt = _normalize_time_range_filter(
            created_from, created_before
        )
        if from_dt is not None or before_dt is not None:
            raise _cursor_invalid() from None
    else:
        # 无 V3/V2 形：先来源，再时间，最后游标版本
        filter_kind = _normalize_source_kind_filter(source_kind)
        from_dt, before_dt = _normalize_time_range_filter(
            created_from, created_before
        )
        time_active = from_dt is not None or before_dt is not None
        if cursor is not None:
            if time_active:
                # 时间范围激活仅认 esrc3；非 V3 形解码固定 cursor_invalid
                cursor_created_at, cursor_id, cursor_from_us, cursor_before_us, cursor_source = (
                    decode_revision_page_cursor_v3(cursor)
                )
                expected_from_us = _time_bound_to_us(from_dt)
                expected_before_us = _time_bound_to_us(before_dt)
                if cursor_from_us != expected_from_us:
                    raise _cursor_invalid() from None
                if cursor_before_us != expected_before_us:
                    raise _cursor_invalid() from None
                if cursor_source != filter_kind:
                    raise _cursor_invalid() from None
            elif filter_kind is None:
                cursor_created_at, cursor_id = decode_revision_page_cursor(cursor)
            else:
                cursor_created_at, cursor_id, cursor_source = (
                    decode_revision_page_cursor_v2(cursor)
                )
                if cursor_source != filter_kind:
                    raise _cursor_invalid() from None

    try:
        stmt = (
            select(
                EditorStateRevisionRow.id,
                EditorStateRevisionRow.state_version,
                EditorStateRevisionRow.snapshot_bytes,
                EditorStateRevisionRow.source_kind,
                EditorStateRevisionRow.created_at,
                EditorStateRevisionRow.display_name,
                type_coerce(EditorStateRevisionRow.is_pinned, Integer).label(
                    "is_pinned"
                ),
            )
            .where(
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
            )
        )
        if filter_kind is not None:
            stmt = stmt.where(EditorStateRevisionRow.source_kind == filter_kind)
        if from_dt is not None:
            stmt = stmt.where(EditorStateRevisionRow.created_at >= from_dt)
        if before_dt is not None:
            stmt = stmt.where(EditorStateRevisionRow.created_at < before_dt)
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
        rid, ver, nbytes, source, created, dname, pinned = _validate_meta_fields(
            revision_id=row.id,
            state_version=row.state_version,
            snapshot_bytes=row.snapshot_bytes,
            source_kind=row.source_kind,
            created_at=row.created_at,
            display_name=row.display_name,
            is_pinned=row.is_pinned,
        )
        validated.append(
            {
                "revision_id": rid,
                "state_version": ver,
                "snapshot_bytes": nbytes,
                "source_kind": source,
                "created_at": created,
                "display_name": dname,
                "is_pinned": pinned,
            }
        )

    has_more = len(validated) > REVISION_PAGE_SIZE
    page_items = validated[:REVISION_PAGE_SIZE]
    next_cursor: str | None = None
    if has_more and page_items:
        last = page_items[-1]
        time_active = from_dt is not None or before_dt is not None
        if time_active:
            next_cursor = encode_revision_page_cursor_v3(
                last["created_at"],
                last["revision_id"],
                from_us=_time_bound_to_us(from_dt),
                before_us=_time_bound_to_us(before_dt),
                source_kind=filter_kind,
            )
        elif filter_kind is None:
            next_cursor = encode_revision_page_cursor(
                last["created_at"], last["revision_id"]
            )
        else:
            next_cursor = encode_revision_page_cursor_v2(
                last["created_at"], last["revision_id"], filter_kind
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
                EditorStateRevisionRow.display_name,
                type_coerce(EditorStateRevisionRow.is_pinned, Integer).label(
                    "is_pinned"
                ),
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

    rid, ver, nbytes, source, created, dname, pinned = _validate_meta_fields(
        revision_id=row.id,
        state_version=row.state_version,
        snapshot_bytes=row.snapshot_bytes,
        source_kind=row.source_kind,
        created_at=row.created_at,
        display_name=row.display_name,
        is_pinned=row.is_pinned,
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
        "display_name": dname,
        "is_pinned": pinned,
        "snapshot": snapshot,
    }


def _normalize_search_query(query: Any) -> str:
    """
    用途：严格规范化搜索关键词。
    规则：原生 str、首尾无空白、无 C0/C1/换行/制表/NUL；NFKC 后 1..64 码点；
      拒绝 null/布尔/数值/对象/数组；错误固定不反射。
    """
    if type(query) is not str:
        raise _search_query_invalid() from None
    if query == "" or query.strip() != query:
        raise _search_query_invalid() from None
    for ch in query:
        code = ord(ch)
        # C0（含 \\t\\n\\r）、DEL、C1
        if code < 0x20 or code == 0x7F or (0x80 <= code <= 0x9F):
            raise _search_query_invalid() from None
    normalized = unicodedata.normalize("NFKC", query)
    n = len(normalized)
    if n < REVISION_SEARCH_QUERY_MIN_LEN or n > REVISION_SEARCH_QUERY_MAX_LEN:
        raise _search_query_invalid() from None
    return query


def _fold_for_search(value: str) -> str:
    """用途：匹配双方统一 NFKC + casefold。"""
    return unicodedata.normalize("NFKC", value).casefold()


def _extract_allowed_search_strings(snapshot: dict[str, Any]) -> list[str]:
    """
    用途：按契约白名单提取用户可见字符串；显式栈遍历 outline.children。
    规则：
      - 仅 type is str 的允许叶子；数组只看对象项；未知键/异型忽略；
      - 对象/字符串叶预算在规范化前计数，超限固定 corrupt；
      - 禁止递归全树收集、regex、HTML/Markdown 渲染。
    """
    out: list[str] = []
    object_count = 0
    string_count = 0

    def _touch_object() -> None:
        nonlocal object_count
        object_count += 1
        if object_count > REVISION_SEARCH_MAX_OBJECTS:
            raise _corrupt() from None

    def _add_str(value: Any) -> None:
        nonlocal string_count
        if type(value) is not str:
            return
        string_count += 1
        if string_count > REVISION_SEARCH_MAX_STRING_LEAVES:
            raise _corrupt() from None
        out.append(value)

    # 1) outline：单对象或对象数组；只沿 children 对象数组
    outline = snapshot.get("outline")
    stack: list[Any] = []
    if isinstance(outline, dict):
        stack.append(outline)
    elif isinstance(outline, list):
        for item in outline:
            if isinstance(item, dict):
                stack.append(item)
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        _touch_object()
        _add_str(node.get("title"))
        _add_str(node.get("description"))
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    stack.append(child)

    # 2) chapters：title/preview/body
    chapters = snapshot.get("chapters")
    if isinstance(chapters, list):
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            _touch_object()
            _add_str(chapter.get("title"))
            _add_str(chapter.get("preview"))
            _add_str(chapter.get("body"))

    # 3) 共享 parsedMarkdown
    _add_str(snapshot.get("parsedMarkdown"))

    # 4) businessQualify
    qualify = snapshot.get("businessQualify")
    if isinstance(qualify, list):
        for item in qualify:
            if not isinstance(item, dict):
                continue
            _touch_object()
            _add_str(item.get("requirement"))
            _add_str(item.get("response"))
            _add_str(item.get("evidence"))

    # 5) businessToc
    toc = snapshot.get("businessToc")
    if isinstance(toc, list):
        for item in toc:
            if not isinstance(item, dict):
                continue
            _touch_object()
            _add_str(item.get("title"))
            _add_str(item.get("category"))
            _add_str(item.get("note"))

    # 6) businessQuote.rows + notes；quote 容器本身计入对象预算
    quote = snapshot.get("businessQuote")
    if isinstance(quote, dict):
        _touch_object()
        rows = quote.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                _touch_object()
                _add_str(row.get("name"))
                _add_str(row.get("unit"))
                _add_str(row.get("quantity"))
                _add_str(row.get("unitPrice"))
                _add_str(row.get("amount"))
                _add_str(row.get("remark"))
        _add_str(quote.get("notes"))

    # 7) businessCommit
    commit = snapshot.get("businessCommit")
    if isinstance(commit, list):
        for item in commit:
            if not isinstance(item, dict):
                continue
            _touch_object()
            _add_str(item.get("title"))
            _add_str(item.get("body"))

    return out


def _snapshot_matches_query(snapshot: dict[str, Any], needle_folded: str) -> bool:
    """用途：白名单字符串中是否存在连续字面子串（已 NFKC+casefold）。"""
    for raw in _extract_allowed_search_strings(snapshot):
        if needle_folded in _fold_for_search(raw):
            return True
    return False


def _display_name_matches_query(
    display_name: str | None, needle_folded: str
) -> bool:
    """
    用途：已通过校验的非 null display_name 与同一 needle 做连续包含。
    规则：null 不命中；折叠规则与 query/快照共用 _fold_for_search。
    """
    if display_name is None:
        return False
    return needle_folded in _fold_for_search(display_name)


def list_editor_state_revision_search(
    db: Session,
    workspace_id: str,
    project_id: str,
    query: Any,
    source_kind: Any = None,
    created_from: Any = None,
    created_before: Any = None,
) -> dict[str, Any]:
    """
    用途：在最新 20 条元数据候选中做名称与可见内容联合搜索；只返回七键元数据。
    对接：POST .../editor-state-revisions/search。
    二次开发：
      - 顺序：项目存在 → 来源 → 时间 → 关键词；
      - SQL 八列（含 display_name + 原始 is_pinned + snapshot）+ workspace/project + 可选来源/时间；
      - 先完整校验全部候选再匹配；坏行/预算超限整次 corrupt；
      - 匹配条件显式为 name_match or snapshot_match；双命中只 append 一次；
      - 禁止 OFFSET/COUNT/LIKE/JSON SQL/N+1/补扫第 21 条/写操作/名称短路校验。
    """
    _require_project_id(db, workspace_id, project_id)

    filter_kind = _normalize_source_kind_filter(source_kind)
    from_dt, before_dt = _normalize_time_range_filter(created_from, created_before)
    raw_query = _normalize_search_query(query)
    needle = _fold_for_search(raw_query)

    try:
        stmt = (
            select(
                EditorStateRevisionRow.id,
                EditorStateRevisionRow.state_version,
                EditorStateRevisionRow.snapshot_bytes,
                EditorStateRevisionRow.source_kind,
                EditorStateRevisionRow.created_at,
                EditorStateRevisionRow.display_name,
                type_coerce(EditorStateRevisionRow.is_pinned, Integer).label(
                    "is_pinned"
                ),
                EditorStateRevisionRow.snapshot_json,
            )
            .where(
                EditorStateRevisionRow.workspace_id == workspace_id,
                EditorStateRevisionRow.project_id == project_id,
            )
        )
        if filter_kind is not None:
            stmt = stmt.where(EditorStateRevisionRow.source_kind == filter_kind)
        if from_dt is not None:
            stmt = stmt.where(EditorStateRevisionRow.created_at >= from_dt)
        if before_dt is not None:
            stmt = stmt.where(EditorStateRevisionRow.created_at < before_dt)
        stmt = stmt.order_by(
            EditorStateRevisionRow.created_at.desc(),
            EditorStateRevisionRow.id.desc(),
        ).limit(REVISION_SEARCH_CANDIDATE_LIMIT)
        result = db.execute(stmt)
        rows = _materialize_all(result)
    except EditorStateRevisionHistoryError:
        raise
    except Exception:
        raise _corrupt() from None

    # 先完整校验全部候选；任一行损坏整次失败（与名称/内容是否命中无关）
    validated: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        rid, ver, nbytes, source, created, dname, pinned = _validate_meta_fields(
            revision_id=row.id,
            state_version=row.state_version,
            snapshot_bytes=row.snapshot_bytes,
            source_kind=row.source_kind,
            created_at=row.created_at,
            display_name=row.display_name,
            is_pinned=row.is_pinned,
        )
        snapshot = _validate_snapshot_payload(
            snapshot_json=row.snapshot_json,
            state_version=ver,
            snapshot_bytes=nbytes,
            source_kind=source,
        )
        validated.append(
            (
                {
                    "revision_id": rid,
                    "state_version": ver,
                    "snapshot_bytes": nbytes,
                    "source_kind": source,
                    "created_at": created,
                    "display_name": dname,
                    "is_pinned": pinned,
                },
                snapshot,
            )
        )

    items: list[dict[str, Any]] = []
    for meta, snapshot in validated:
        # 两侧均先求值：禁止 name_match 短路跳过快照提取预算校验
        name_match = _display_name_matches_query(meta["display_name"], needle)
        snapshot_match = _snapshot_matches_query(snapshot, needle)
        if name_match or snapshot_match:
            items.append(meta)
    return {"items": items}
