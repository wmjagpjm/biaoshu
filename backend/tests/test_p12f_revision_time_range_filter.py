"""
模块：P12F-E-A editor-state 修订历史时间范围筛选专项测试
用途：验收 /editor-state-revisions/page 的 createdFrom/createdBefore 严格 UTC 筛选、
  包含下界/排除上界、esrc3 条件绑定、非法时间矩阵、V1/V2/V3 交叉、SQL 五列+谓词+LIMIT11、
  错误优先级与五域零写。
对接：GET .../page[?createdFrom=&createdBefore=&sourceKind=&cursor=]；
  editor_state_revision_history_service；api.editor_state_revisions。
二次开发：
  - 禁止 mock SQLite、宽泛状态码、固定 sleep、恒真 or、反射输入假绿；
  - 红测必须证明旧实现忽略时间范围/无 esrc3，而非收集/导入/语法错误；
  - 合法时间精确 24 字符 YYYY-MM-DDTHH:MM:SS.sssZ；非法范围固定 time_range_invalid。
"""

from __future__ import annotations

import ast
import base64
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
    utc_now,
)
from app.services import editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fea"
_SECRET = "SECRET_P12FEA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/page"
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt"}
)
_PAGE_TOP = frozenset({"items", "nextCursor"})
_LIST_TOP = frozenset({"items"})
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_CURSOR_V1 = "esrc1_"
_CURSOR_V2 = "esrc2_"
_CURSOR_V3 = "esrc3_"
_CURSOR_MAX_LEN = 192
_CURSOR_MAX_LEN_V3 = 256
_CODE_CURSOR_INVALID = "editor_state_revision_cursor_invalid"
_MSG_CURSOR_INVALID = "修订分页游标无效"
_CODE_SOURCE_INVALID = "editor_state_revision_source_invalid"
_MSG_SOURCE_INVALID = "修订来源筛选无效"
_CODE_TIME_RANGE_INVALID = "editor_state_revision_time_range_invalid"
_MSG_TIME_RANGE_INVALID = "修订时间范围筛选无效"

_NINE_SOURCES = (
    "browser_put",
    "task",
    "revise",
    "callback",
    "local_parser",
    "content_fuse_apply",
    "content_fuse_consume",
    "checkpoint_restore",
    "revision_restore",
)

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_history_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_revisions.py"
)

# 规范 24 字符 UTC 毫秒时间字面量
_T_MIN = "1970-01-01T00:00:00.000Z"
_T_MAX = "9999-12-31T23:59:59.999Z"
_T_2026_A = "2026-07-01T00:00:00.000Z"
_T_2026_B = "2026-07-10T00:00:00.000Z"
_T_2026_C = "2026-07-20T00:00:00.000Z"
_T_2026_D = "2026-08-01T00:00:00.000Z"


@pytest.fixture
def disabled_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "disabled")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(disabled_settings):
    with TestClient(app) as client:
        yield client


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions"


def _page_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/page"


def _create_project(client: TestClient, name: str = "P12F-E-A项目") -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _state_with_version(**overrides) -> dict:
    analysis = editor_state_service.empty_analysis()
    business = editor_state_service.empty_business()
    state = {
        "outline": None,
        "chapters": None,
        "facts": None,
        "mode": "ALIGNED",
        "analysis": analysis,
        "responseMatrix": [],
        "guidance": None,
        "parsedMarkdown": None,
        "businessQualify": business["qualify"],
        "businessToc": business["toc"],
        "businessQuote": business["quote"],
        "businessCommit": business["commit"],
        "analysisOverview": analysis.get("overview", ""),
    }
    state.update(overrides)
    snap = editor_state_service.extract_canonical_snapshot(state)
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _variant(tag: str) -> dict:
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "content": f"正文{tag}-{_SECRET}",
            }
        ],
        guidance=f"指引-{tag}",
        parsedMarkdown=f"md-{tag}-{_SECRET}",
    )


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(
    res, status: int, code: str, *, forbid_echo: str | None = None
) -> None:
    assert res.status_code == status, res.text
    _assert_no_store(res)
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    assert "SELECT" not in blob
    assert "INSERT" not in blob
    assert "OFFSET" not in blob
    assert "editor_state_revisions" not in blob
    assert "editor_state_revision_history_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "JSONDecodeError" not in blob
    if forbid_echo is not None and forbid_echo != "":
        assert forbid_echo not in blob


def _assert_page_shape(body: dict, *, max_items: int = 10) -> None:
    assert set(body.keys()) == _PAGE_TOP, body.keys()
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= max_items
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert _REVISION_ID_RE.fullmatch(item["revisionId"])
        assert "snapshot" not in item
    nc = body["nextCursor"]
    if nc is None:
        pass
    else:
        assert isinstance(nc, str)
        assert nc != ""


def _assert_time_range_invalid(res, *, forbid_parts: list[str] | None = None) -> None:
    """用途：精确 400 time_range_invalid + 固定中文 + no-store + 零回显。"""
    _assert_fixed_error(res, 400, _CODE_TIME_RANGE_INVALID)
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert set(body["detail"].keys()) == {"code", "message"}
    assert body["detail"]["code"] == _CODE_TIME_RANGE_INVALID
    assert body["detail"]["message"] == _MSG_TIME_RANGE_INVALID
    assert "items" not in body
    assert "nextCursor" not in body
    assert "revisionId" not in body
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    if forbid_parts:
        for part in forbid_parts:
            if part is not None and part != "":
                assert part not in res.text, f"不应回显: {part!r}"


def _assert_cursor_invalid_no_echo(res, *, forbid_parts: list[str]) -> None:
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID)
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert set(body["detail"].keys()) == {"code", "message"}
    assert body["detail"]["code"] == _CODE_CURSOR_INVALID
    assert body["detail"]["message"] == _MSG_CURSOR_INVALID
    assert "items" not in body
    assert "nextCursor" not in body
    assert "revisionId" not in body
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    for part in forbid_parts:
        if part is not None and part != "":
            assert part not in res.text, f"不应回显: {part!r}"


def _datetime_to_us(dt: datetime) -> int:
    if dt.tzinfo is None:
        aware = dt.replace(tzinfo=timezone.utc)
    else:
        aware = dt.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = aware - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _parse_utc_ms_literal(s: str) -> datetime:
    """用途：测试侧解析规范 24 字符字面量为 UTC aware datetime（毫秒）。"""
    assert len(s) == 24
    assert s[10] == "T" and s[-1] == "Z"
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _utc_ms_literal_to_us(s: str) -> int:
    """用途：规范毫秒字面量 → UTC 微秒（毫秒*1000）。"""
    dt = _parse_utc_ms_literal(s)
    return _datetime_to_us(dt)


def _encode_cursor_payload(payload: dict, *, prefix: str) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return prefix + body


def _encode_esrc1(created_at: datetime, revision_id: str) -> str:
    return _encode_cursor_payload(
        {"i": revision_id, "t": _datetime_to_us(created_at)}, prefix=_CURSOR_V1
    )


def _encode_esrc2(created_at: datetime, revision_id: str, source_kind: str) -> str:
    return _encode_cursor_payload(
        {
            "i": revision_id,
            "s": source_kind,
            "t": _datetime_to_us(created_at),
        },
        prefix=_CURSOR_V2,
    )


def _encode_esrc3(
    created_at: datetime,
    revision_id: str,
    *,
    f_us: int | None,
    b_us: int | None,
    s: str | None,
) -> str:
    """用途：esrc3 规范载荷 {b,f,i,s,t}。"""
    return _encode_cursor_payload(
        {
            "b": b_us,
            "f": f_us,
            "i": revision_id,
            "s": s,
            "t": _datetime_to_us(created_at),
        },
        prefix=_CURSOR_V3,
    )


def _insert_raw_revision(
    *,
    project_id: str,
    revision_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    source_kind: str,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            EditorStateRevisionRow(
                id=revision_id,
                workspace_id=workspace_id,
                project_id=project_id,
                snapshot_json=snapshot_json,
                state_version=state_version,
                snapshot_bytes=snapshot_bytes,
                source_kind=source_kind,
                created_at=created_at or utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()


def _raw_sql_update_revision(
    revision_id: str,
    *,
    ignore_check: bool = False,
    **fields: object,
) -> None:
    if not fields:
        raise ValueError("fields required")
    set_parts: list[str] = []
    params: dict[str, object] = {"rid": revision_id}
    for key, value in fields.items():
        set_parts.append(f"{key} = :{key}")
        params[key] = value
    sql = (
        "UPDATE editor_state_revisions SET "
        + ", ".join(set_parts)
        + " WHERE id = :rid"
    )
    with engine.begin() as conn:
        if ignore_check:
            conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        conn.execute(text(sql), params)
        if ignore_check:
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))


def _db_rev_rows(project_id: str, workspace_id: str | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(EditorStateRevisionRow).filter(
            EditorStateRevisionRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateRevisionRow.workspace_id == workspace_id)
        rows = list(
            q.order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            ).all()
        )
        return [
            {
                "id": r.id,
                "workspace_id": r.workspace_id,
                "project_id": r.project_id,
                "state_version": r.state_version,
                "snapshot_bytes": int(r.snapshot_bytes),
                "source_kind": r.source_kind,
                "created_at": r.created_at,
                "created_at_iso": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
                "snapshot_json": r.snapshot_json,
            }
            for r in rows
        ]
    finally:
        db.close()


def _db_cp_rows(project_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "state_version": r.state_version,
                "snapshot_bytes": int(r.snapshot_bytes),
                "snapshot_json": r.snapshot_json,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def _db_editor_state_row(project_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None:
            return None
        return {
            "project_id": row.project_id,
            "outline_json": row.outline_json,
            "chapters_json": row.chapters_json,
            "facts_json": row.facts_json,
            "mode": row.mode,
            "analysis_json": row.analysis_json,
            "response_matrix_json": row.response_matrix_json,
            "guidance_json": row.guidance_json,
            "parsed_markdown": row.parsed_markdown,
            "business_json": row.business_json,
            "analysis_overview": row.analysis_overview,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at is not None and hasattr(row.updated_at, "isoformat")
            else row.updated_at,
        }
    finally:
        db.close()


def _db_project_row(project_id: str) -> dict | None:
    db = SessionLocal()
    try:
        row = db.get(Project, project_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "workspace_id": row.workspace_id,
            "name": row.name,
            "kind": row.kind,
            "status": row.status,
            "industry": row.industry,
            "word_count": row.word_count,
            "technical_plan_step": row.technical_plan_step,
            "linked_project_id": row.linked_project_id,
            "source_opportunity_id": row.source_opportunity_id,
            "updated_at": row.updated_at.isoformat()
            if row.updated_at is not None and hasattr(row.updated_at, "isoformat")
            else str(row.updated_at),
        }
    finally:
        db.close()


def _db_audit_rows() -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(AuthAuditEventRow)
            .order_by(
                AuthAuditEventRow.created_at.desc(),
                AuthAuditEventRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "actor_user_id": r.actor_user_id,
                "workspace_id": r.workspace_id,
                "action": r.action,
                "result": r.result,
                "target": r.target,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
            }
            for r in rows
        ]
    finally:
        db.close()


def _domain_snapshot(project_id: str) -> dict:
    return {
        "revisions": [
            {
                "id": r["id"],
                "workspace_id": r["workspace_id"],
                "project_id": r["project_id"],
                "state_version": r["state_version"],
                "snapshot_bytes": r["snapshot_bytes"],
                "source_kind": r["source_kind"],
                "created_at": r["created_at_iso"],
                "snapshot_json": r["snapshot_json"],
            }
            for r in _db_rev_rows(project_id)
        ],
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "audits": _db_audit_rows(),
    }


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FEA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fea",
                )
            )
            db.commit()
    finally:
        db.close()


def _seed_timed_revisions(
    project_id: str,
    specs: list[tuple[datetime, str, str | None]],
) -> list[dict]:
    """
    用途：按 (created_at, source_kind, tag) 插入修订；返回 created_at DESC,id DESC。
    """
    for i, (created, source_kind, tag) in enumerate(specs):
        use_tag = tag if tag is not None else f"t{i:02d}"
        state = _variant(use_tag)
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        rid = "esr_" + secrets.token_hex(16)
        _insert_raw_revision(
            project_id=project_id,
            revision_id=rid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            source_kind=source_kind,
            created_at=created,
        )
    return _db_rev_rows(project_id)


def _seed_n_timed(
    project_id: str,
    n: int,
    *,
    source_kind: str = "task",
    base_time: datetime | None = None,
    step: timedelta | None = None,
    same_created_at: bool = False,
    tag_prefix: str = "n",
) -> list[dict]:
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    delta = step or timedelta(hours=1)
    specs: list[tuple[datetime, str, str | None]] = []
    for i in range(n):
        created = base if same_created_at else base + delta * i
        specs.append((created, source_kind, f"{tag_prefix}{i:02d}"))
    return _seed_timed_revisions(project_id, specs)


def _as_utc_aware(dt: datetime) -> datetime:
    """用途：测试侧将 DB 可能返回的 naive datetime 规范为 UTC aware。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _filter_time_range(
    rows: list[dict],
    *,
    created_from: datetime | None = None,
    created_before: datetime | None = None,
    source_kind: str | None = None,
) -> list[dict]:
    """用途：测试侧镜像服务端包含下界/排除上界 + 可选来源。"""
    out: list[dict] = []
    for r in rows:
        ca_cmp = _as_utc_aware(r["created_at"])
        if created_from is not None and ca_cmp < created_from:
            continue
        if created_before is not None and not (ca_cmp < created_before):
            continue
        if source_kind is not None and r["source_kind"] != source_kind:
            continue
        out.append(r)
    return out


# ---------- 下界包含 / 上界排除 / 单边双边 ----------


def test_created_from_inclusive_lower_bound(disabled_client):
    """
    用途：createdFrom 为包含下界 created_at >=；精确边界同值必须命中。
    红测：旧实现忽略 createdFrom 会返回范围外行。
    """
    client = disabled_client
    pid = _create_project(client, name="下界包含")
    t0 = datetime(2026, 7, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    t_bound = datetime(2026, 7, 10, 0, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 15, 0, 0, 0, 0, tzinfo=timezone.utc)
    ordered = _seed_timed_revisions(
        pid,
        [
            (t0, "task", "before"),
            (t1, "task", "mid"),
            (t_bound, "task", "bound"),
            (t2, "task", "after"),
        ],
    )
    from_lit = "2026-07-10T00:00:00.000Z"
    expect = _filter_time_range(
        ordered, created_from=_parse_utc_ms_literal(from_lit)
    )
    assert len(expect) == 2
    assert {r["id"] for r in expect} == {
        r["id"]
        for r in ordered
        if _as_utc_aware(r["created_at"]) >= t_bound
    }

    res = client.get(_page_url(pid), params={"createdFrom": from_lit})
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    got_ids = [it["revisionId"] for it in body["items"]]
    assert got_ids == [r["id"] for r in expect]
    # 不得包含下界之前
    before_ids = {
        r["id"]
        for r in ordered
        if _as_utc_aware(r["created_at"]) < t_bound
    }
    assert set(got_ids).isdisjoint(before_ids)


def test_created_before_exclusive_upper_bound(disabled_client):
    """用途：createdBefore 为排除上界 created_at <；边界同值不得命中。"""
    client = disabled_client
    pid = _create_project(client, name="上界排除")
    t0 = datetime(2026, 7, 1, 0, 0, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 5, 12, 0, 0, 0, tzinfo=timezone.utc)
    t_bound = datetime(2026, 7, 10, 0, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 7, 15, 0, 0, 0, 0, tzinfo=timezone.utc)
    ordered = _seed_timed_revisions(
        pid,
        [
            (t0, "task", "a"),
            (t1, "task", "b"),
            (t_bound, "task", "bound"),
            (t2, "task", "c"),
        ],
    )
    before_lit = "2026-07-10T00:00:00.000Z"
    expect = _filter_time_range(
        ordered, created_before=_parse_utc_ms_literal(before_lit)
    )
    assert len(expect) == 2
    assert all(_as_utc_aware(r["created_at"]) < t_bound for r in expect)

    res = client.get(_page_url(pid), params={"createdBefore": before_lit})
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    got_ids = [it["revisionId"] for it in body["items"]]
    assert got_ids == [r["id"] for r in expect]
    # 上界同值不得进入
    bound_ids = {
        r["id"]
        for r in ordered
        if _as_utc_aware(r["created_at"]) == t_bound
    }
    assert set(got_ids).isdisjoint(bound_ids)


def test_bilateral_range_and_empty_result(disabled_client):
    """用途：双边范围 from < before；无命中返回空 items + null nextCursor。"""
    client = disabled_client
    pid = _create_project(client, name="双边与空")
    ordered = _seed_timed_revisions(
        pid,
        [
            (datetime(2026, 6, 1, tzinfo=timezone.utc), "task", "early"),
            (datetime(2026, 7, 5, tzinfo=timezone.utc), "task", "in"),
            (datetime(2026, 8, 1, tzinfo=timezone.utc), "task", "late"),
        ],
    )
    params = {
        "createdFrom": "2026-07-01T00:00:00.000Z",
        "createdBefore": "2026-07-10T00:00:00.000Z",
    }
    expect = _filter_time_range(
        ordered,
        created_from=_parse_utc_ms_literal(params["createdFrom"]),
        created_before=_parse_utc_ms_literal(params["createdBefore"]),
    )
    assert len(expect) == 1

    res = client.get(_page_url(pid), params=params)
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    assert [it["revisionId"] for it in body["items"]] == [expect[0]["id"]]
    assert body["nextCursor"] is None

    # 空范围结果
    empty_params = {
        "createdFrom": "2026-09-01T00:00:00.000Z",
        "createdBefore": "2026-09-02T00:00:00.000Z",
    }
    empty = client.get(_page_url(pid), params=empty_params)
    assert empty.status_code == 200, empty.text
    eb = empty.json()
    _assert_page_shape(eb)
    assert eb["items"] == []
    assert eb["nextCursor"] is None


def test_no_time_range_means_all(disabled_client):
    """用途：两个时间边界都缺失表示无时间筛选，行为与既有兼容。"""
    client = disabled_client
    pid = _create_project(client, name="无时间范围")
    ordered = _seed_n_timed(pid, 5, source_kind="task", tag_prefix="all_")
    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    body = res.json()
    assert [it["revisionId"] for it in body["items"]] == [
        r["id"] for r in ordered
    ]
    assert body["nextCursor"] is None


# ---------- 0/1/10/11/20 边界 + esrc3 ----------


@pytest.mark.parametrize(
    "n,expect_next",
    [(0, False), (1, False), (10, False), (11, True), (20, True)],
)
def test_time_range_boundaries_first_page(
    disabled_client, n: int, expect_next: bool
):
    client = disabled_client
    pid = _create_project(client, name=f"时间边界{n}")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    if n > 0:
        _seed_n_timed(
            pid,
            n,
            source_kind="task",
            base_time=base,
            step=timedelta(hours=1),
            tag_prefix=f"b{n}_",
        )
    # 范围外噪声
    _seed_n_timed(
        pid,
        5,
        source_kind="task",
        base_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        tag_prefix=f"noise{n}_",
    )
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    ordered = _filter_time_range(
        _db_rev_rows(pid),
        created_from=_parse_utc_ms_literal(from_lit),
        created_before=_parse_utc_ms_literal(before_lit),
    )
    assert len(ordered) == n

    res = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    expect_count = min(10, n)
    assert len(body["items"]) == expect_count
    assert [it["revisionId"] for it in body["items"]] == [
        r["id"] for r in ordered[:expect_count]
    ]
    if expect_next:
        assert body["nextCursor"] is not None
        assert body["nextCursor"].startswith(_CURSOR_V3)
        assert len(body["nextCursor"]) <= _CURSOR_MAX_LEN_V3
        tenth = ordered[9]
        expected_cur = _encode_esrc3(
            tenth["created_at"],
            tenth["id"],
            f_us=_utc_ms_literal_to_us(from_lit),
            b_us=_utc_ms_literal_to_us(before_lit),
            s=None,
        )
        assert body["nextCursor"] == expected_cur
    else:
        assert body["nextCursor"] is None


def test_time_range_20_two_pages_esrc3_binding(disabled_client):
    """用途：20 条双边时间筛选两页不重不漏；第二页必须重复相同 from/before。"""
    client = disabled_client
    pid = _create_project(client, name="时间二十条")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    _seed_n_timed(
        pid, 20, source_kind="callback", base_time=base, tag_prefix="cb_"
    )
    # 范围外
    _seed_n_timed(
        pid,
        8,
        source_kind="callback",
        base_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
        tag_prefix="out_",
    )
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    ordered = _filter_time_range(
        _db_rev_rows(pid),
        created_from=_parse_utc_ms_literal(from_lit),
        created_before=_parse_utc_ms_literal(before_lit),
    )
    assert len(ordered) == 20
    all_ids = [r["id"] for r in ordered]

    p1 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    _assert_page_shape(b1)
    assert len(b1["items"]) == 10
    assert b1["nextCursor"] is not None
    assert b1["nextCursor"].startswith(_CURSOR_V3)
    ids1 = [it["revisionId"] for it in b1["items"]]
    assert ids1 == all_ids[:10]

    p2 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": b1["nextCursor"],
        },
    )
    assert p2.status_code == 200, p2.text
    b2 = p2.json()
    _assert_page_shape(b2)
    assert len(b2["items"]) == 10
    assert b2["nextCursor"] is None
    ids2 = [it["revisionId"] for it in b2["items"]]
    assert ids2 == all_ids[10:20]
    assert set(ids1).isdisjoint(set(ids2))
    assert ids1 + ids2 == all_ids


def test_same_ms_microsecond_and_same_time_id_stable(disabled_client):
    """用途：同毫秒内微秒差与同时间不同 ID 的降序稳定、不重不漏。"""
    client = disabled_client
    pid = _create_project(client, name="同毫秒微秒")
    base = datetime(2026, 7, 10, 12, 0, 0, 0, tzinfo=timezone.utc)
    # 同毫秒不同微秒
    specs: list[tuple[datetime, str, str | None]] = []
    for i, us in enumerate((0, 1, 500, 999)):
        specs.append(
            (base.replace(microsecond=us), "task", f"us{i}")
        )
    # 完全同时间多 ID
    same = datetime(2026, 7, 10, 13, 0, 0, 123000, tzinfo=timezone.utc)
    for i in range(5):
        specs.append((same, "task", f"tie{i}"))
    ordered = _seed_timed_revisions(pid, specs)
    from_lit = "2026-07-10T00:00:00.000Z"
    before_lit = "2026-07-11T00:00:00.000Z"
    expect = _filter_time_range(
        ordered,
        created_from=_parse_utc_ms_literal(from_lit),
        created_before=_parse_utc_ms_literal(before_lit),
    )
    assert len(expect) == len(specs)

    res = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    got = [it["revisionId"] for it in body["items"]]
    assert got == [r["id"] for r in expect]
    assert len(got) == len(set(got))


def test_time_range_same_cursor_repeat_deterministic(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="时间重复游标")
    _seed_n_timed(pid, 15, tag_prefix="rep_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    p1 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    ).json()
    cur = p1["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V3)
    a = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    ).json()
    b = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    ).json()
    assert a == b
    assert [it["revisionId"] for it in a["items"]] == [
        it["revisionId"] for it in b["items"]
    ]


# ---------- 与九来源组合 ----------


@pytest.mark.parametrize("source_kind", list(_NINE_SOURCES))
def test_time_range_with_each_of_nine_sources(disabled_client, source_kind: str):
    """用途：时间范围 + 九类来源组合服务端过滤，非返回后过滤。"""
    client = disabled_client
    pid = _create_project(client, name=f"时间九源-{source_kind}")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    specs: list[tuple[datetime, str, str | None]] = []
    for i, sk in enumerate(_NINE_SOURCES):
        specs.append((base + timedelta(hours=i), sk, f"{sk[:3]}_in"))
        # 范围外
        specs.append(
            (
                datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i),
                sk,
                f"{sk[:3]}_out",
            )
        )
    # 目标来源范围内额外 2 条
    specs.append((base + timedelta(hours=20), source_kind, "extra1"))
    specs.append((base + timedelta(hours=21), source_kind, "extra2"))
    ordered = _seed_timed_revisions(pid, specs)
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    expect = _filter_time_range(
        ordered,
        created_from=_parse_utc_ms_literal(from_lit),
        created_before=_parse_utc_ms_literal(before_lit),
        source_kind=source_kind,
    )
    # 夹具精确：九源各 1 条范围内 + 目标源额外 2 条 = 3
    assert len(expect) == 3

    res = client.get(
        _page_url(pid),
        params={
            "sourceKind": source_kind,
            "createdFrom": from_lit,
            "createdBefore": before_lit,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    got_ids = [it["revisionId"] for it in body["items"]]
    assert got_ids == [r["id"] for r in expect[:10]]
    for it in body["items"]:
        assert it["sourceKind"] == source_kind
    from_dt = _parse_utc_ms_literal(from_lit)
    before_dt = _parse_utc_ms_literal(before_lit)
    other_ids = {
        r["id"]
        for r in ordered
        if r["source_kind"] != source_kind
        or _as_utc_aware(r["created_at"]) < from_dt
        or not (_as_utc_aware(r["created_at"]) < before_dt)
    }
    assert set(got_ids).isdisjoint(other_ids)


# ---------- 无时间范围时 V1/V2 字节级兼容 ----------


def test_no_time_range_v1_byte_compatible(disabled_client):
    """用途：无时间范围时 esrc1 编码与既有完全一致。"""
    client = disabled_client
    pid = _create_project(client, name="V1兼容")
    ordered = _seed_n_timed(pid, 11, tag_prefix="v1_")
    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["nextCursor"] is not None
    assert body["nextCursor"].startswith(_CURSOR_V1)
    assert not body["nextCursor"].startswith(_CURSOR_V2)
    assert not body["nextCursor"].startswith(_CURSOR_V3)
    tenth = ordered[9]
    assert body["nextCursor"] == _encode_esrc1(tenth["created_at"], tenth["id"])
    assert len(body["nextCursor"]) <= _CURSOR_MAX_LEN


def test_no_time_range_source_only_uses_esrc2(disabled_client):
    """用途：仅来源筛选无时间范围时仍只认 esrc2。"""
    client = disabled_client
    pid = _create_project(client, name="V2兼容")
    ordered = _seed_n_timed(pid, 11, source_kind="revise", tag_prefix="v2_")
    res = client.get(_page_url(pid), params={"sourceKind": "revise"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["nextCursor"] is not None
    assert body["nextCursor"].startswith(_CURSOR_V2)
    assert not body["nextCursor"].startswith(_CURSOR_V3)
    tenth = ordered[9]
    assert body["nextCursor"] == _encode_esrc2(
        tenth["created_at"], tenth["id"], "revise"
    )
    assert len(body["nextCursor"]) <= _CURSOR_MAX_LEN


# ---------- V3 规范编解码 / 载荷 / 往返 ----------


def test_esrc3_canonical_roundtrip_keys_types_and_len(disabled_client):
    """用途：esrc3 精确 {b,f,i,s,t}、sort_keys 紧凑、无填充、长度≤256、规范往返。"""
    client = disabled_client
    pid = _create_project(client, name="esrc3规范")
    ordered = _seed_n_timed(pid, 11, tag_prefix="can3_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    p1 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    ).json()
    cur = p1["nextCursor"]
    assert cur is not None
    assert cur.startswith(_CURSOR_V3)
    assert len(cur) <= _CURSOR_MAX_LEN_V3
    tenth = ordered[9]
    expected = _encode_esrc3(
        tenth["created_at"],
        tenth["id"],
        f_us=_utc_ms_literal_to_us(from_lit),
        b_us=_utc_ms_literal_to_us(before_lit),
        s=None,
    )
    assert cur == expected

    # 解码载荷精确键集
    body = cur[len(_CURSOR_V3) :]
    pad = "=" * ((4 - len(body) % 4) % 4)
    raw = base64.urlsafe_b64decode(body + pad)
    data = json.loads(raw.decode("utf-8"))
    assert set(data.keys()) == {"b", "f", "i", "s", "t"}
    assert data["i"] == tenth["id"]
    assert data["t"] == _datetime_to_us(tenth["created_at"])
    assert data["f"] == _utc_ms_literal_to_us(from_lit)
    assert data["b"] == _utc_ms_literal_to_us(before_lit)
    assert data["s"] is None

    # 非规范 JSON（空格）必须 400
    messy = json.dumps(
        {
            "t": data["t"],
            "s": None,
            "i": data["i"],
            "f": data["f"],
            "b": data["b"],
        },
        separators=(", ", ": "),
    )
    bad = _CURSOR_V3 + base64.urlsafe_b64encode(messy.encode()).decode().rstrip(
        "="
    )
    res = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": bad,
        },
    )
    _assert_cursor_invalid_no_echo(res, forbid_parts=[bad])


def test_esrc3_with_source_and_one_sided_bounds(disabled_client):
    """用途：单边时间 + 来源 → esrc3 中 f/b 可为 null，s 为权威来源。"""
    client = disabled_client
    pid = _create_project(client, name="esrc3单边来源")
    base = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
    _seed_n_timed(
        pid, 12, source_kind="task", base_time=base, tag_prefix="side_"
    )
    _seed_n_timed(
        pid,
        5,
        source_kind="revise",
        base_time=base,
        tag_prefix="noise_",
    )
    from_lit = "2026-07-01T00:00:00.000Z"
    ordered = _filter_time_range(
        _db_rev_rows(pid),
        created_from=_parse_utc_ms_literal(from_lit),
        source_kind="task",
    )
    assert len(ordered) == 12

    p1 = client.get(
        _page_url(pid),
        params={"sourceKind": "task", "createdFrom": from_lit},
    )
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    cur = b1["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V3)
    tenth = ordered[9]
    expected = _encode_esrc3(
        tenth["created_at"],
        tenth["id"],
        f_us=_utc_ms_literal_to_us(from_lit),
        b_us=None,
        s="task",
    )
    assert cur == expected

    p2 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "createdFrom": from_lit,
            "cursor": cur,
        },
    )
    assert p2.status_code == 200, p2.text
    assert [it["revisionId"] for it in p2.json()["items"]] == [
        r["id"] for r in ordered[10:12]
    ]


def test_esrc3_encoder_decoder_semantic_matrix_direct():
    """
    用途：直接 encode/decode 证明 esrc3 时间语义；禁止仅经 HTTP query mismatch 间接通过。
    覆盖：双 null / f==b / 倒序 / t<f / t>=b 编码 corrupt、解码 cursor-invalid；
      合法 t==f、t==b-1、单边 from/before 往返；f/b/t 边界类型数值拒绝。
    """
    from app.services import editor_state_revision_history_service as hist

    rid = "esr_" + ("ab" * 16)
    f_us = 1000
    b_us = 2000
    t_mid = 1500

    def _assert_encode_corrupt(
        *,
        tus: int,
        from_us: int | None,
        before_us: int | None,
        source_kind: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        ca = created_at if created_at is not None else hist._us_to_datetime(tus)
        with pytest.raises(hist.EditorStateRevisionHistoryError) as exc:
            hist.encode_revision_page_cursor_v3(
                ca,
                rid,
                from_us=from_us,
                before_us=before_us,
                source_kind=source_kind,
            )
        assert exc.value.status_code == 500
        assert exc.value.code == hist.CODE_REVISION_CORRUPT
        assert exc.value.message == hist.MSG_REVISION_CORRUPT

    def _assert_decode_cursor_invalid(payload: dict) -> None:
        cur = _encode_cursor_payload(payload, prefix=_CURSOR_V3)
        with pytest.raises(hist.EditorStateRevisionHistoryError) as exc:
            hist.decode_revision_page_cursor_v3(cur)
        assert exc.value.status_code == 400
        assert exc.value.code == hist.CODE_CURSOR_INVALID
        assert exc.value.message == hist.MSG_CURSOR_INVALID

    # 1) encoder：语义非法矩阵 → corrupt
    _assert_encode_corrupt(tus=t_mid, from_us=None, before_us=None)
    _assert_encode_corrupt(tus=1000, from_us=1000, before_us=1000)
    _assert_encode_corrupt(tus=1500, from_us=2000, before_us=1000)
    _assert_encode_corrupt(tus=999, from_us=1000, before_us=2000)
    _assert_encode_corrupt(tus=2000, from_us=1000, before_us=2000)
    _assert_encode_corrupt(tus=2001, from_us=1000, before_us=2000)
    # 单边 t 越界
    _assert_encode_corrupt(tus=500, from_us=1000, before_us=None)
    _assert_encode_corrupt(tus=1000, from_us=None, before_us=1000)

    # 2) 手工规范紧凑 esrc3 直调 decoder：同一语义矩阵 → cursor_invalid
    semantic_payloads = [
        {"b": None, "f": None, "i": rid, "s": None, "t": t_mid},
        {"b": 1000, "f": 1000, "i": rid, "s": None, "t": 1000},
        {"b": 1000, "f": 2000, "i": rid, "s": None, "t": 1500},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": 999},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": 2000},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": 2001},
        {"b": None, "f": 1000, "i": rid, "s": "task", "t": 500},
        {"b": 1000, "f": None, "i": rid, "s": None, "t": 1000},
    ]
    for pl in semantic_payloads:
        _assert_decode_cursor_invalid(pl)

    # 3) 合法往返：t==f、t==b-1、单边 from、单边 before
    legal_cases = [
        (1000, 1000, 2000, None),  # t == f 下界包含
        (1999, 1000, 2000, None),  # t == b-1 上界排除
        (5000, 1000, None, "task"),  # 单边 from
        (500, None, 1000, None),  # 单边 before
    ]
    for tus, fu, bu, sk in legal_cases:
        ca = hist._us_to_datetime(tus)
        cur = hist.encode_revision_page_cursor_v3(
            ca, rid, from_us=fu, before_us=bu, source_kind=sk
        )
        assert cur.startswith(_CURSOR_V3)
        assert len(cur) <= _CURSOR_MAX_LEN_V3
        back = hist.decode_revision_page_cursor_v3(cur)
        assert back[1] == rid
        assert hist._datetime_to_us(back[0]) == tus
        assert back[2] == fu
        assert back[3] == bu
        assert back[4] == sk
        # 手工紧凑载荷同样可解码
        hand = _encode_cursor_payload(
            {"b": bu, "f": fu, "i": rid, "s": sk, "t": tus},
            prefix=_CURSOR_V3,
        )
        assert hand == cur
        hand_back = hist.decode_revision_page_cursor_v3(hand)
        assert hand_back[1] == rid
        assert hist._datetime_to_us(hand_back[0]) == tus

    # 4) f/b/t 边界数值与类型：encoder corrupt / decoder cursor_invalid
    bound_type_encode = [
        # (from_us, before_us, tus_or_none, created_at_override)
        (-1, 2000, 1000, None),
        (0, hist.CURSOR_T_MAX + 1, 1000, None),
        (True, 2000, 1000, None),  # bool 冒充 int
        (False, 2000, 1000, None),
        (1.5, 2000, 1000, None),  # type: ignore[arg-type]
        ("1000", 2000, 1000, None),  # type: ignore[arg-type]
        (1000, -1, 1000, None),
        (1000, True, 1000, None),
        (1000, 1.5, 1000, None),
        (1000, "2000", 1000, None),
    ]
    for fu, bu, tus, _ in bound_type_encode:
        _assert_encode_corrupt(tus=int(tus) if isinstance(tus, int) else 1000, from_us=fu, before_us=bu)  # type: ignore[arg-type]

    # t 越闭区间：pre-1970 / MAX+1
    _assert_encode_corrupt(
        tus=0,
        from_us=0,
        before_us=2000,
        created_at=datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
    )
    # 合法 tus 但 t 字段类型/范围在 decode 侧拒绝
    type_bound_decode = [
        {"b": 2000, "f": -1, "i": rid, "s": None, "t": 1000},
        {"b": hist.CURSOR_T_MAX + 1, "f": 0, "i": rid, "s": None, "t": 1000},
        {"b": 2000, "f": True, "i": rid, "s": None, "t": 1000},
        {"b": False, "f": 1000, "i": rid, "s": None, "t": 1500},
        {"b": 2000, "f": 1.5, "i": rid, "s": None, "t": 1500},
        {"b": "2000", "f": 1000, "i": rid, "s": None, "t": 1500},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": -1},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": hist.CURSOR_T_MAX + 1},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": True},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": 1.5},
        {"b": 2000, "f": 1000, "i": rid, "s": None, "t": "1500"},
    ]
    for pl in type_bound_decode:
        _assert_decode_cursor_invalid(pl)


def test_esrc3_payload_invalid_variants(disabled_client):
    """用途：esrc3 额外/缺失键、布尔冒充整数、坏 ID、非法 s、填充等固定 cursor-invalid。"""
    client = disabled_client
    pid = _create_project(client, name="esrc3坏载荷")
    rows = _seed_n_timed(pid, 3, tag_prefix="pl3_")
    good_id = rows[0]["id"]
    good_t = _datetime_to_us(rows[0]["created_at"])
    f_us = _utc_ms_literal_to_us(_T_2026_A)
    b_us = _utc_ms_literal_to_us(_T_2026_D)
    from_lit = _T_2026_A
    before_lit = _T_2026_D

    variants: list[tuple[str, object]] = [
        (
            "extra_key",
            {"b": b_us, "f": f_us, "i": good_id, "s": None, "t": good_t, "x": 1},
        ),
        ("missing_b", {"f": f_us, "i": good_id, "s": None, "t": good_t}),
        ("missing_f", {"b": b_us, "i": good_id, "s": None, "t": good_t}),
        ("missing_i", {"b": b_us, "f": f_us, "s": None, "t": good_t}),
        ("missing_s", {"b": b_us, "f": f_us, "i": good_id, "t": good_t}),
        ("missing_t", {"b": b_us, "f": f_us, "i": good_id, "s": None}),
        (
            "bool_t",
            {"b": b_us, "f": f_us, "i": good_id, "s": None, "t": True},
        ),
        (
            "bool_f",
            {"b": b_us, "f": True, "i": good_id, "s": None, "t": good_t},
        ),
        (
            "bool_b",
            {"b": False, "f": f_us, "i": good_id, "s": None, "t": good_t},
        ),
        (
            "float_t",
            {"b": b_us, "f": f_us, "i": good_id, "s": None, "t": 1.5},
        ),
        (
            "str_f",
            {"b": b_us, "f": "123", "i": good_id, "s": None, "t": good_t},
        ),
        (
            "empty_s",
            {"b": b_us, "f": f_us, "i": good_id, "s": "", "t": good_t},
        ),
        (
            "bad_s",
            {"b": b_us, "f": f_us, "i": good_id, "s": "not_a_kind", "t": good_t},
        ),
        (
            "bad_id",
            {"b": b_us, "f": f_us, "i": "not_an_esr", "s": None, "t": good_t},
        ),
        ("empty_keys", {}),
        ("v1_payload", {"i": good_id, "t": good_t}),
        ("v2_payload", {"i": good_id, "s": "task", "t": good_t}),
    ]
    for label, payload in variants:
        cur = _encode_cursor_payload(payload, prefix=_CURSOR_V3)  # type: ignore[arg-type]
        res = client.get(
            _page_url(pid),
            params={
                "createdFrom": from_lit,
                "createdBefore": before_lit,
                "cursor": cur,
            },
        )
        _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur)
        assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID, label

    # 填充 base64 拒绝
    good_payload = {
        "b": b_us,
        "f": f_us,
        "i": good_id,
        "s": None,
        "t": good_t,
    }
    raw = json.dumps(
        good_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    padded = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")
    if "=" not in padded:
        # 强制附加填充使解码路径可见
        padded = padded + "=="
    cur_pad = _CURSOR_V3 + padded
    res_pad = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur_pad,
        },
    )
    _assert_fixed_error(res_pad, 400, _CODE_CURSOR_INVALID, forbid_echo=cur_pad)

    # 超长
    too_long = _CURSOR_V3 + ("a" * 300)
    res_long = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": too_long,
        },
    )
    _assert_fixed_error(
        res_long, 400, _CODE_CURSOR_INVALID, forbid_echo=too_long
    )

    # 非法字符
    bad_b64 = _CURSOR_V3 + "!!!not-b64!!!"
    res_b64 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": bad_b64,
        },
    )
    _assert_fixed_error(res_b64, 400, _CODE_CURSOR_INVALID, forbid_echo=bad_b64)


# ---------- 第二页条件绑定：缺失/增加/改变/非法/倒序 ----------


def test_esrc3_second_page_must_repeat_exact_bounds(disabled_client):
    """用途：第二页缺失/增加/改变/倒序时间边界均 cursor-invalid，禁止从游标采用。"""
    client = disabled_client
    pid = _create_project(client, name="二页绑定")
    _seed_n_timed(pid, 15, tag_prefix="bind_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    p1 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    assert p1.status_code == 200, p1.text
    cur = p1.json()["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V3)

    # 缺失 from
    r1 = client.get(
        _page_url(pid),
        params={"createdBefore": before_lit, "cursor": cur},
    )
    _assert_cursor_invalid_no_echo(r1, forbid_parts=[cur, from_lit])

    # 缺失 before
    r2 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "cursor": cur},
    )
    _assert_cursor_invalid_no_echo(r2, forbid_parts=[cur, before_lit])

    # 两边都缺失
    r3 = client.get(_page_url(pid), params={"cursor": cur})
    _assert_cursor_invalid_no_echo(r3, forbid_parts=[cur])

    # 改变 from
    r4 = client.get(
        _page_url(pid),
        params={
            "createdFrom": "2026-07-02T00:00:00.000Z",
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(
        r4, forbid_parts=[cur, "2026-07-02T00:00:00.000Z"]
    )

    # 改变 before
    r5 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": "2026-09-01T00:00:00.000Z",
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(
        r5, forbid_parts=[cur, "2026-09-01T00:00:00.000Z"]
    )

    # 增加 sourceKind（原载荷 s=null）
    r6 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r6, forbid_parts=[cur, "task"])

    # 非法时间 + esrc3 → cursor-invalid（绑定优先）
    r7 = client.get(
        _page_url(pid),
        params={
            "createdFrom": "not-a-time",
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r7, forbid_parts=[cur, "not-a-time"])

    # 倒序时间 + esrc3 → cursor-invalid
    r8 = client.get(
        _page_url(pid),
        params={
            "createdFrom": before_lit,
            "createdBefore": from_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r8, forbid_parts=[cur])


def test_esrc3_source_mismatch_and_missing_source(disabled_client):
    """用途：esrc3 第二页缺失/改变/非法来源固定 cursor-invalid。"""
    client = disabled_client
    pid = _create_project(client, name="esrc3来源绑定")
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    _seed_n_timed(
        pid, 15, source_kind="task", base_time=base, tag_prefix="sbind_"
    )
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    p1 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "createdFrom": from_lit,
            "createdBefore": before_lit,
        },
    )
    assert p1.status_code == 200, p1.text
    cur = p1.json()["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V3)

    # 缺失来源
    r1 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r1, forbid_parts=[cur])

    # 改变来源
    r2 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "revise",
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r2, forbid_parts=[cur, "revise"])

    # 非法来源
    r3 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "NOT_A_KIND",
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    _assert_cursor_invalid_no_echo(r3, forbid_parts=[cur, "NOT_A_KIND"])


# ---------- 版本交叉矩阵 ----------


def test_version_cross_matrix_time_range_requires_esrc3(disabled_client):
    """用途：时间范围 + esrc1/esrc2 固定 cursor-invalid；无范围 + esrc3 固定 cursor-invalid。"""
    client = disabled_client
    pid = _create_project(client, name="版本交叉")
    ordered = _seed_n_timed(pid, 12, source_kind="task", tag_prefix="vx_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    tenth = ordered[9]

    esrc1 = _encode_esrc1(tenth["created_at"], tenth["id"])
    esrc2 = _encode_esrc2(tenth["created_at"], tenth["id"], "task")
    esrc3 = _encode_esrc3(
        tenth["created_at"],
        tenth["id"],
        f_us=_utc_ms_literal_to_us(from_lit),
        b_us=_utc_ms_literal_to_us(before_lit),
        s=None,
    )

    # 时间范围 + esrc1
    r1 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": esrc1,
        },
    )
    _assert_cursor_invalid_no_echo(r1, forbid_parts=[esrc1])

    # 时间范围 + esrc2
    r2 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": esrc2,
        },
    )
    _assert_cursor_invalid_no_echo(r2, forbid_parts=[esrc2])

    # 时间范围 + 来源 + esrc2（有范围仍只认 esrc3）
    r2b = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": esrc2,
        },
    )
    _assert_cursor_invalid_no_echo(r2b, forbid_parts=[esrc2])

    # 无时间范围 + esrc3
    r3 = client.get(_page_url(pid), params={"cursor": esrc3})
    _assert_cursor_invalid_no_echo(r3, forbid_parts=[esrc3])

    # 仅来源 + esrc3（无时间范围）
    r4 = client.get(
        _page_url(pid),
        params={"sourceKind": "task", "cursor": esrc3},
    )
    _assert_cursor_invalid_no_echo(r4, forbid_parts=[esrc3])

    # 无范围 + 合法 esrc1 仍 OK
    r5 = client.get(_page_url(pid), params={"cursor": esrc1})
    assert r5.status_code == 200, r5.text

    # 仅来源 + 合法 esrc2 仍 OK
    r6 = client.get(
        _page_url(pid),
        params={"sourceKind": "task", "cursor": esrc2},
    )
    assert r6.status_code == 200, r6.text


# ---------- 严格时间非法矩阵 ----------


@pytest.mark.parametrize(
    "value,label",
    [
        ("", "empty"),
        ("   ", "whitespace"),
        ("2026-07-18", "date_only"),
        ("2026-07-18 00:00:00.000Z", "space_sep"),
        ("2026-07-18t00:00:00.000Z", "lower_t"),
        ("2026-07-18T00:00:00.000z", "lower_z"),
        ("2026-07-18T00:00:00.000", "no_z"),
        ("2026-07-18T00:00:00Z", "no_ms"),
        ("2026-07-18T00:00:00.00Z", "two_ms"),
        ("2026-07-18T00:00:00.0000Z", "four_ms"),
        ("2026-07-18T00:00:00.000+00:00", "offset"),
        ("2026-07-18T00:00:00.000+08:00", "offset_plus"),
        ("2026-07-18T00:00:00.000-05:00", "offset_minus"),
        (" 2026-07-18T00:00:00.000Z", "lead_space"),
        ("2026-07-18T00:00:00.000Z ", "trail_space"),
        ("2026-13-01T00:00:00.000Z", "bad_month"),
        ("2026-00-01T00:00:00.000Z", "zero_month"),
        ("2026-07-32T00:00:00.000Z", "bad_day"),
        ("2026-02-30T00:00:00.000Z", "feb30"),
        ("2025-02-29T00:00:00.000Z", "non_leap"),
        ("2026-07-18T24:00:00.000Z", "hour24"),
        ("2026-07-18T00:60:00.000Z", "min60"),
        ("2026-07-18T00:00:60.000Z", "sec60"),
        ("1969-12-31T23:59:59.999Z", "pre_epoch"),
        ("0001-01-01T00:00:00.000Z", "year1"),
        ("10000-01-01T00:00:00.000Z", "year10000"),
        ("2026/07/18T00:00:00.000Z", "slash_date"),
        ("2026-7-18T00:00:00.000Z", "no_pad_month"),
        ("2026-07-18T0:00:00.000Z", "no_pad_hour"),
        ("not-a-timestamp", "garbage"),
        ("null", "null_str"),
        ("0", "numeric"),
        (_SECRET, "secret"),
        ("2026-07-18T00:00:00.000Z\n", "newline"),
        ("2026-07-18T00:00:00.000ZX", "extra_char"),
        ("x2026-07-18T00:00:00.000Z", "prefix_char"),
    ],
)
def test_invalid_time_string_matrix_created_from(
    disabled_client, value: str, label: str
):
    client = disabled_client
    pid = _create_project(client, name=f"非法from-{label}")
    _seed_n_timed(pid, 2, tag_prefix=f"if{label[:3]}_")
    res = client.get(_page_url(pid), params={"createdFrom": value})
    _assert_time_range_invalid(
        res, forbid_parts=[value] if value.strip() else None
    )


@pytest.mark.parametrize(
    "value,label",
    [
        ("", "empty"),
        ("2026-07-18t00:00:00.000Z", "lower_t"),
        ("2026-07-18T00:00:00.000+00:00", "offset"),
        ("2026-02-30T00:00:00.000Z", "feb30"),
        ("1969-12-31T23:59:59.999Z", "pre_epoch"),
        (" 2026-07-18T00:00:00.000Z", "lead_space"),
        ("2026-07-18T00:00:00.00Z", "two_ms"),
        ("not-before", "garbage"),
    ],
)
def test_invalid_time_string_matrix_created_before(
    disabled_client, value: str, label: str
):
    client = disabled_client
    pid = _create_project(client, name=f"非法before-{label}")
    _seed_n_timed(pid, 2, tag_prefix=f"ib{label[:3]}_")
    res = client.get(_page_url(pid), params={"createdBefore": value})
    _assert_time_range_invalid(
        res, forbid_parts=[value] if value.strip() else None
    )


def test_equal_and_inverted_time_range_invalid(disabled_client):
    """用途：from == before 或 from > before 固定 time_range_invalid。"""
    client = disabled_client
    pid = _create_project(client, name="范围倒序")
    _seed_n_timed(pid, 2, tag_prefix="inv_")
    same = "2026-07-10T00:00:00.000Z"
    res_eq = client.get(
        _page_url(pid),
        params={"createdFrom": same, "createdBefore": same},
    )
    _assert_time_range_invalid(res_eq, forbid_parts=[same])

    res_inv = client.get(
        _page_url(pid),
        params={
            "createdFrom": "2026-07-20T00:00:00.000Z",
            "createdBefore": "2026-07-10T00:00:00.000Z",
        },
    )
    _assert_time_range_invalid(
        res_inv,
        forbid_parts=[
            "2026-07-20T00:00:00.000Z",
            "2026-07-10T00:00:00.000Z",
        ],
    )


def test_valid_time_bounds_min_max_accepted(disabled_client):
    """用途：闭区间边界 1970 与 9999 合法字面量可被接受（结果可为空）。"""
    client = disabled_client
    pid = _create_project(client, name="边界合法")
    _seed_n_timed(pid, 2, tag_prefix="mm_")
    res = client.get(
        _page_url(pid),
        params={"createdFrom": _T_MIN, "createdBefore": _T_MAX},
    )
    assert res.status_code == 200, res.text
    _assert_page_shape(res.json())


# ---------- 错误优先级 ----------


def test_project_not_found_priority_over_time_and_cursor(disabled_client):
    """用途：项目/跨空间 404 最优先于时间与游标错误。"""
    client = disabled_client
    missing = "proj_missing_p12fea"
    fake_id = "esr_" + "b" * 32
    fake_t = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    cur3 = _encode_esrc3(
        fake_t,
        fake_id,
        f_us=_utc_ms_literal_to_us(_T_2026_A),
        b_us=_utc_ms_literal_to_us(_T_2026_D),
        s=None,
    )
    combos = [
        {"createdFrom": "not-a-time"},
        {"createdFrom": _T_2026_D, "createdBefore": _T_2026_A},
        {"createdFrom": _T_2026_A, "cursor": cur3},
        {"createdFrom": "bad", "cursor": cur3},
        {"sourceKind": "NOT_A_KIND", "createdFrom": _T_2026_A},
        {"sourceKind": "task", "createdFrom": _T_2026_A, "cursor": cur3},
    ]
    for params in combos:
        res = client.get(_page_url(missing), params=params)
        _assert_fixed_error(res, 404, "project_not_found")
        assert res.json()["detail"]["message"] == "项目不存在或不可访问"
        assert missing not in res.text
        assert "items" not in res.text


def test_esrc3_shaped_with_illegal_time_is_cursor_invalid(disabled_client):
    """用途：V3 形游标 + 任意非法/缺失/错配时间条件 → cursor-invalid（非 time_range）。"""
    client = disabled_client
    pid = _create_project(client, name="V3优先")
    rows = _seed_n_timed(pid, 12, tag_prefix="v3p_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    cur = _encode_esrc3(
        rows[9]["created_at"],
        rows[9]["id"],
        f_us=_utc_ms_literal_to_us(from_lit),
        b_us=_utc_ms_literal_to_us(before_lit),
        s=None,
    )
    # 仅 esrc3 前缀也算 V3 形
    prefix_only = "esrc3_not_valid_payload"

    cases = [
        {"createdFrom": "BAD_TIME", "cursor": cur},
        {"createdFrom": from_lit, "createdBefore": "BAD", "cursor": cur},
        {
            "createdFrom": before_lit,
            "createdBefore": from_lit,
            "cursor": cur,
        },
        {"cursor": cur},
        {"createdFrom": "BAD_TIME", "cursor": prefix_only},
    ]
    for params in cases:
        res = client.get(_page_url(pid), params=params)
        _assert_cursor_invalid_no_echo(
            res, forbid_parts=[params.get("cursor", ""), "BAD_TIME", "BAD"]
        )


def test_without_v3_source_then_time_then_cursor_priority(disabled_client):
    """
    用途：无 V3 形游标时：先来源校验，再时间范围，最后游标版本。
    """
    client = disabled_client
    pid = _create_project(client, name="无V3优先级")
    rows = _seed_n_timed(pid, 3, tag_prefix="prio_")
    esrc1 = _encode_esrc1(rows[0]["created_at"], rows[0]["id"])

    # 非法来源优先于非法时间
    r1 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "NOT_A_KIND",
            "createdFrom": "not-a-time",
        },
    )
    _assert_fixed_error(r1, 400, _CODE_SOURCE_INVALID, forbid_echo="NOT_A_KIND")
    assert r1.json()["detail"]["message"] == _MSG_SOURCE_INVALID

    # 合法来源 + 非法时间 → time_range_invalid
    r2 = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "createdFrom": "not-a-time",
        },
    )
    _assert_time_range_invalid(r2, forbid_parts=["not-a-time"])

    # 合法时间 + 非法 esrc1 版本（有时间范围）→ cursor_invalid
    r3 = client.get(
        _page_url(pid),
        params={
            "createdFrom": _T_2026_A,
            "createdBefore": _T_2026_D,
            "cursor": esrc1,
        },
    )
    _assert_cursor_invalid_no_echo(r3, forbid_parts=[esrc1])

    # 非法时间 + 非 V3 游标 → 时间优先（非 cursor）
    r4 = client.get(
        _page_url(pid),
        params={"createdFrom": "bad-time", "cursor": esrc1},
    )
    _assert_time_range_invalid(r4, forbid_parts=["bad-time"])


# ---------- 跨域 ----------


def test_cross_project_workspace_zero_leak_with_time_range(disabled_client):
    client = disabled_client
    a = _create_project(client, name="时间跨域A")
    b = _create_project(client, name="时间跨域B")
    rows_a = _seed_n_timed(a, 12, tag_prefix="xa_")
    rows_b = _seed_n_timed(b, 5, tag_prefix="xb_")
    before_a = _domain_snapshot(a)
    before_b = _domain_snapshot(b)
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"

    p1a = client.get(
        _page_url(a),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    ).json()
    cur_a = p1a["nextCursor"]
    assert cur_a and cur_a.startswith(_CURSOR_V3)

    cross = client.get(
        _page_url(b),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur_a,
        },
    )
    assert cross.status_code == 200, cross.text
    cross_ids = {it["revisionId"] for it in cross.json()["items"]}
    assert cross_ids.isdisjoint({r["id"] for r in rows_a})
    for it in cross.json()["items"]:
        assert it["revisionId"] in {r["id"] for r in rows_b}

    _ensure_workspace(_WS_OTHER)
    other_pid = "proj_other_space_p12fea"
    db = SessionLocal()
    try:
        if db.get(Project, other_pid) is None:
            db.add(
                Project(
                    id=other_pid,
                    workspace_id=_WS_OTHER,
                    name="外空间项目P12FEA",
                    kind="technical",
                )
            )
            db.commit()
    finally:
        db.close()
    state = _variant("ows")
    snap_json = editor_state_service.canonical_snapshot_json(
        editor_state_service.extract_canonical_snapshot(state)
    )
    other_rid = "esr_" + secrets.token_hex(16)
    _insert_raw_revision(
        project_id=other_pid,
        revision_id=other_rid,
        snapshot_json=snap_json,
        state_version=state["stateVersion"],
        snapshot_bytes=len(snap_json.encode("utf-8")),
        source_kind="task",
        workspace_id=_WS_OTHER,
        created_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
    )

    cross_ws = client.get(
        _page_url(a),
        params={"createdFrom": from_lit},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(cross_ws, 404, "project_not_found")
    assert rows_a[0]["id"] not in cross_ws.text

    other_ok = client.get(
        _page_url(other_pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert other_ok.status_code == 200, other_ok.text
    assert other_ok.json()["items"][0]["revisionId"] == other_rid

    assert _domain_snapshot(a) == before_a
    assert _domain_snapshot(b) == before_b


# ---------- SQL 五列 + 全部谓词 + LIMIT 11 ----------


def test_time_range_sql_five_columns_predicates_limit_11(disabled_client):
    """
    用途：首屏 revision SELECT 独立证明五列+source/from/before 上界与 LIMIT11；
      第二页独立证明三筛选仍保留 + 精确键集；禁止用任意 created_at < 冒充上界。
    """
    client = disabled_client
    pid = _create_project(client, name="SQL时间投影")
    _seed_n_timed(pid, 15, source_kind="task", tag_prefix="sql_")
    _seed_n_timed(
        pid,
        5,
        source_kind="revise",
        base_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
        tag_prefix="sqln_",
    )
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    from_dt = _parse_utc_ms_literal(from_lit)
    before_dt = _parse_utc_ms_literal(before_lit)

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low or "projects" in low:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        p1 = client.get(
            _page_url(pid),
            params={
                "sourceKind": "task",
                "createdFrom": from_lit,
                "createdBefore": before_lit,
            },
        )
        assert p1.status_code == 200, p1.text
        cur = p1.json()["nextCursor"]
        assert cur
        p2 = client.get(
            _page_url(pid),
            params={
                "sourceKind": "task",
                "createdFrom": from_lit,
                "createdBefore": before_lit,
                "cursor": cur,
            },
        )
        assert p2.status_code == 200, p2.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    rev_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert rev_selects, f"未捕获 revision SELECT: {captured}"

    svc_src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert ".offset(" not in svc_src
    assert "func.count" not in svc_src
    assert "COUNT(*)" not in svc_src.upper()

    _FIVE_COLS = (
        "id",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
    )

    def _param_list(parameters: object) -> list[object]:
        if isinstance(parameters, dict):
            return list(parameters.values())
        if isinstance(parameters, (list, tuple)):
            return list(parameters)
        return [parameters]

    def _as_utc_dt(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return _as_utc_aware(value)
        if isinstance(value, str):
            try:
                # SQLite 可能绑定 ISO 文本
                raw = value.replace("Z", "+00:00")
                return _as_utc_aware(datetime.fromisoformat(raw))
            except ValueError:
                return None
        return None

    def _params_contain_dt(params: object, expected: datetime) -> bool:
        exp = _as_utc_aware(expected)
        for v in _param_list(params):
            got = _as_utc_dt(v)
            if got is not None and got == exp:
                return True
        return False

    def _assert_limit_exactly_11(sql_low: str, parameters: object) -> None:
        if re.search(r"\blimit\s+11\b", sql_low):
            if re.search(r"\boffset\b", sql_low):
                if re.search(r"\boffset\s+0\b", sql_low):
                    return
                if re.search(r"\boffset\s+\?", sql_low):
                    vals = _param_list(parameters)
                    assert vals[-1:] == [0], f"OFFSET 绑定非 0: {parameters}"
                    return
                raise AssertionError(f"OFFSET 非字面量 0 且非占位符: {sql_low}")
            return
        if re.search(r"\blimit\s+\?", sql_low):
            vals = _param_list(parameters)
            assert vals, f"LIMIT ? 无绑定参数: {sql_low}"
            if re.search(r"\boffset\s+\?", sql_low):
                # 精确尾部切片：(... , LIMIT=11, OFFSET=0)
                assert vals[-2:] == [11, 0], f"LIMIT/OFFSET 尾部非 [11,0]: {parameters}"
                return
            if re.search(r"\boffset\s+0\b", sql_low):
                assert vals[-1:] == [11], f"LIMIT 绑定非 11: {parameters}"
                return
            if re.search(r"\boffset\b", sql_low):
                raise AssertionError(f"存在非零/未定位 OFFSET: {sql_low} {parameters}")
            assert vals[-1:] == [11], f"LIMIT 绑定非 11: {parameters}"
            return
        raise AssertionError(f"未发现精确 LIMIT 11: {sql_low} {parameters}")

    def _has_keyset_predicate(sql_low: str) -> bool:
        # 等式须排除 >=/>；结构：created_at < … OR (created_at = … AND id < …)
        structural = re.search(
            r"created_at\s*<[\s\S]{0,200}?\bor\b[\s\S]{0,200}?"
            r"created_at\s*=(?!>)[\s\S]{0,120}?\band\b[\s\S]{0,80}?\bid\s*<",
            sql_low,
        )
        return structural is not None

    def _count_created_eq(sql_low: str) -> int:
        """用途：精确计数 created_at =，排除 >= / => 误匹配。"""
        return len(re.findall(r"\bcreated_at\s*=(?!>)", sql_low))

    def _assert_five_cols(compact: str) -> None:
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None
        select_list = match.group(1).strip()
        raw_parts = [p.strip() for p in select_list.split(",")]
        assert len(raw_parts) == 5, (raw_parts, compact)
        normalized_cols: list[str] = []
        for part in raw_parts:
            col = part.lower()
            if " as " in col:
                col = col.split(" as ", 1)[0].strip()
            if "." in col:
                col = col.rsplit(".", 1)[-1].strip()
            col = col.strip("`\"[]")
            normalized_cols.append(col)
        assert normalized_cols == list(_FIVE_COLS), (normalized_cols, compact)

    def _assert_common_filters(sql_low: str, params: object, *, label: str) -> None:
        assert re.search(r"\bworkspace_id\s*=", sql_low), f"{label} 缺 workspace_id: {sql_low}"
        assert re.search(r"\bproject_id\s*=", sql_low), f"{label} 缺 project_id: {sql_low}"
        assert re.search(r"\bsource_kind\s*=", sql_low), f"{label} 缺 source_kind =: {sql_low}"
        assert re.search(r"\bsource_kind\s+is\b", sql_low) is None, sql_low
        param_vals = _param_list(params)
        assert "task" in param_vals, f"{label} 来源绑定缺 task: {params}"
        assert pid in param_vals, f"{label} 缺 project 绑定: {params}"
        assert _WS in param_vals, f"{label} 缺 workspace 绑定: {params}"
        assert _params_contain_dt(params, from_dt), f"{label} 缺规范 from 绑定: {params}"
        assert _params_contain_dt(params, before_dt), f"{label} 缺规范 before 绑定: {params}"

    # 分离首屏（无键集）与第二页（有键集）revision SELECT
    first_page_sqls: list[tuple[str, object, str]] = []
    second_page_sqls: list[tuple[str, object, str]] = []
    for sql, params in rev_selects:
        compact = " ".join(sql.split())
        low = compact.lower()
        assert "snapshot_json" not in low, sql
        assert "count(" not in low
        _assert_five_cols(compact)
        if _has_keyset_predicate(low):
            second_page_sqls.append((compact, params, low))
        else:
            first_page_sqls.append((compact, params, low))

    assert first_page_sqls, f"未捕获首屏无键集 revision SELECT: {rev_selects}"
    assert second_page_sqls, f"未捕获第二页键集 revision SELECT: {rev_selects}"

    # —— 首屏：精确五列 + workspace/project + source + 唯一 from + 唯一 before + LIMIT11 ——
    first_ok = False
    for compact, params, low in first_page_sqls:
        # 首屏不得含键集结构（created_at = 与 id <）
        assert _count_created_eq(low) == 0, f"首屏不应有 created_at=: {compact}"
        assert re.search(r"\bid\s*<", low) is None, f"首屏不应有 id<: {compact}"
        assert not _has_keyset_predicate(low)
        ge_count = len(re.findall(r"\bcreated_at\s*>=", low))
        lt_count = len(re.findall(r"\bcreated_at\s*<", low))
        # 唯一下界与唯一上界；上界不得靠键集 < 冒充
        assert ge_count == 1, f"首屏 created_at >= 须恰 1 次: {ge_count} {compact}"
        assert lt_count == 1, f"首屏 created_at < 须恰 1 次（上界）: {lt_count} {compact}"
        _assert_common_filters(low, params, label="首屏")
        _assert_limit_exactly_11(low, params)
        first_ok = True
    assert first_ok, f"首屏 SQL 未完整证明: {first_page_sqls}"

    # —— 第二页：三筛选仍在 + 精确键集结构 ——
    second_ok = False
    for compact, params, low in second_page_sqls:
        assert _has_keyset_predicate(low), f"第二页缺键集结构: {compact}"
        # 精确：from 1 次；上界 < + 键集 < 恰 2 次；键集等式恰 1 次
        assert len(re.findall(r"\bcreated_at\s*>=", low)) == 1, compact
        assert len(re.findall(r"\bcreated_at\s*<", low)) == 2, compact
        assert _count_created_eq(low) == 1, compact
        # 证明 source/from/before 绑定仍在（不能只靠 cursor 时间或源码字面量）
        _assert_common_filters(low, params, label="第二页")
        _assert_limit_exactly_11(low, params)
        second_ok = True
    assert second_ok, f"第二页 SQL 未完整证明: {second_page_sqls}"


# ---------- lookahead 损坏 ----------


def test_time_range_lookahead_corrupt_fails_whole_page(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="时间lookahead损坏")
    ordered = _seed_n_timed(pid, 12, tag_prefix="cr_")
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    filtered = _filter_time_range(
        ordered,
        created_from=_parse_utc_ms_literal(from_lit),
        created_before=_parse_utc_ms_literal(before_lit),
    )
    # 夹具精确 12 条范围内（含 lookahead 第 11 条）
    assert len(filtered) == 12
    bad_id = filtered[10]["id"]
    _raw_sql_update_revision(bad_id, state_version="not_a_valid_esv")

    res = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert body["detail"]["message"] == "修订记录数据损坏，无法读取"
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    assert bad_id not in res.text
    assert _SECRET not in res.text


# ---------- 兼容：旧列表 / 未知参数 ----------


def test_old_list_ignores_time_params_and_stays_items_only(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="旧列表忽略时间")
    ordered = _seed_n_timed(pid, 12, tag_prefix="ol_")
    listed = client.get(_list_url(pid))
    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    body = listed.json()
    assert set(body.keys()) == _LIST_TOP
    assert "nextCursor" not in body
    assert len(body["items"]) == 10
    assert [it["revisionId"] for it in body["items"]] == [
        r["id"] for r in ordered[:10]
    ]

    tampered = client.get(
        _list_url(pid),
        params={
            "createdFrom": _T_2026_A,
            "createdBefore": _T_2026_D,
            "sourceKind": "task",
            "limit": 100,
            "cursor": "x",
            "dateFrom": _T_2026_A,
            "search": _SECRET,
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == body


def test_page_unknown_date_aliases_ignored(disabled_client):
    """用途：未知 dateFrom/dateTo/start/end 等仍忽略，不改变无筛选页。"""
    client = disabled_client
    pid = _create_project(client, name="未知日期别名")
    _seed_n_timed(pid, 12, tag_prefix="uq_")
    baseline = client.get(_page_url(pid)).json()
    tampered = client.get(
        _page_url(pid),
        params={
            "dateFrom": _T_2026_A,
            "dateTo": _T_2026_D,
            "start": _T_2026_A,
            "end": _T_2026_D,
            "from": _T_2026_A,
            "to": _T_2026_D,
            "search": _SECRET,
            "q": "正文",
            "limit": 1,
            "offset": 3,
            "page": 2,
            "order": "asc",
            "total": 1,
            "hasMore": "true",
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == baseline


# ---------- 五域零写 + AST ----------


def test_time_range_get_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="时间五域零写")
    _seed_n_timed(pid, 12, tag_prefix="zw_")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text

    before = _domain_snapshot(pid)
    from_lit = "2026-07-01T00:00:00.000Z"
    before_lit = "2026-08-01T00:00:00.000Z"
    p1 = client.get(
        _page_url(pid),
        params={"createdFrom": from_lit, "createdBefore": before_lit},
    )
    assert p1.status_code == 200, p1.text
    assert _domain_snapshot(pid) == before

    cur = p1.json()["nextCursor"]
    p2 = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": cur,
        },
    )
    assert p2.status_code == 200, p2.text
    assert _domain_snapshot(pid) == before

    bad_time = client.get(
        _page_url(pid), params={"createdFrom": "not-a-time"}
    )
    _assert_time_range_invalid(bad_time, forbid_parts=["not-a-time"])
    assert _domain_snapshot(pid) == before

    bad_cur = client.get(
        _page_url(pid),
        params={
            "createdFrom": from_lit,
            "createdBefore": before_lit,
            "cursor": "esrc3_bad",
        },
    )
    _assert_fixed_error(bad_cur, 400, _CODE_CURSOR_INVALID)
    assert _domain_snapshot(pid) == before


def test_service_api_no_write_ops_ast_and_time_aliases():
    """用途：service/api 无写路径；page 路由 Query 别名含 createdFrom/createdBefore。"""
    api_src = _API_PATH.read_text(encoding="utf-8")
    api_tree = ast.parse(api_src)

    page_fn: ast.FunctionDef | None = None
    for node in api_tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == (
            "list_editor_state_revisions_page"
        ):
            page_fn = node
            break
    assert page_fn is not None, "缺少 list_editor_state_revisions_page"

    def _find_query_alias(arg_name: str, expected_alias: str) -> None:
        target: ast.arg | None = None
        for arg in page_fn.args.args:
            if arg.arg == arg_name:
                target = arg
                break
        assert target is not None, f"缺少形参 {arg_name}"
        ann = target.annotation
        assert ann is not None, f"{arg_name} 缺少注解"
        assert isinstance(ann, ast.Subscript), type(ann)
        assert isinstance(ann.value, ast.Name) and ann.value.id == "Annotated"
        slice_node = ann.slice
        assert isinstance(slice_node, ast.Tuple), type(slice_node)
        found = False
        for elt in slice_node.elts:
            if not isinstance(elt, ast.Call):
                continue
            func = elt.func
            is_query = (isinstance(func, ast.Name) and func.id == "Query") or (
                isinstance(func, ast.Attribute) and func.attr == "Query"
            )
            if not is_query:
                continue
            for kw in elt.keywords:
                if kw.arg != "alias":
                    continue
                assert isinstance(kw.value, ast.Constant), type(kw.value)
                assert kw.value.value == expected_alias, kw.value.value
                found = True
        assert found, (
            f"{arg_name} 必须为 Annotated[..., Query(alias={expected_alias!r})]"
        )

    _find_query_alias("source_kind", "sourceKind")
    _find_query_alias("created_from", "createdFrom")
    _find_query_alias("created_before", "createdBefore")

    for path in (_SERVICE_PATH, _API_PATH):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned = {
            "commit",
            "rollback",
            "flush",
            "refresh",
            "with_for_update",
            "record_editor_state_transition",
            "create_editor_state_checkpoint",
            "restore_editor_state_checkpoint",
            "upsert_editor_state",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in banned:
                    pytest.fail(f"{path.name} 禁止调用 {name}")
            if isinstance(node, ast.Attribute) and node.attr in {
                "commit",
                "rollback",
                "flush",
                "refresh",
            }:
                pytest.fail(f"{path.name} 禁止属性 {node.attr}")
        low = src.lower()
        for token in (
            "db.commit",
            "db.rollback",
            "db.flush",
            "db.refresh",
            "with_for_update",
        ):
            assert token not in low, f"{path.name} 含写路径 token: {token}"


def test_nine_source_kinds_still_match_authority():
    assert set(_NINE_SOURCES) == set(
        editor_state_revision_service.REVISION_SOURCE_KINDS
    )
