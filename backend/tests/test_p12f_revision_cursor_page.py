"""
模块：P12F-B editor-state 修订历史后端游标页专项测试
用途：验收独立 /editor-state-revisions/page 固定 10 条键集分页、
  esrc1_ 规范游标、旧列表兼容、SQL 投影/LIMIT 11、只读零写与非法游标 400。
对接：GET .../editor-state-revisions/page[?cursor=]；
  editor_state_revision_history_service；api.editor_state_revisions；schemas。
二次开发：禁止 mock SQLite、宽泛状态码、假绿；红测必须先证明新路由真实 404。
"""

from __future__ import annotations

import ast
import base64
import hashlib
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
from app.services import editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fb"
_SECRET = "SECRET_P12FB_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/page"
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt"}
)
_PAGE_TOP = frozenset({"items", "nextCursor"})
_LIST_TOP = frozenset({"items"})
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_CURSOR_PREFIX = "esrc1_"
_CURSOR_MAX_LEN = 192
_CODE_CURSOR_INVALID = "editor_state_revision_cursor_invalid"
_MSG_CURSOR_INVALID = "修订分页游标无效"

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
_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "schemas.py"
)


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


def _create_project(
    client: TestClient,
    name: str = "P12F-B项目",
) -> str:
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


def _assert_fixed_error(res, status: int, code: str, *, forbid_echo: str | None = None) -> None:
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


def _canonical_bytes(snapshot: dict) -> bytes:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _expected_version(snapshot: dict) -> str:
    digest = hashlib.sha256(_canonical_bytes(snapshot)).hexdigest()
    return "esv_" + digest[:32]


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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FB") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fb",
                )
            )
            db.commit()
    finally:
        db.close()


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


def _encode_cursor_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    return _CURSOR_PREFIX + body


def _encode_valid_cursor(created_at: datetime, revision_id: str) -> str:
    return _encode_cursor_payload({"i": revision_id, "t": _datetime_to_us(created_at)})


def _seed_n_revisions(
    project_id: str,
    n: int,
    *,
    base_time: datetime | None = None,
    tag_prefix: str = "n",
    same_created_at: bool = False,
) -> list[dict]:
    """
    用途：插入 n 条合法修订；默认 created_at 递增 1 秒；
      same_created_at=True 时共用同一时间，靠 id DESC 分界。
    返回：created_at DESC, id DESC 有序 dict 列表。
    """
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    ids: list[str] = []
    for i in range(n):
        tag = f"{tag_prefix}{i:02d}"
        state = _variant(tag)
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        rid = "esr_" + secrets.token_hex(16)
        created = base if same_created_at else base + timedelta(seconds=i)
        _insert_raw_revision(
            project_id=project_id,
            revision_id=rid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            source_kind="task",
            created_at=created,
        )
        ids.append(rid)
    return _db_rev_rows(project_id)


def _assert_page_shape(body: dict, *, max_items: int = 10) -> None:
    assert set(body.keys()) == _PAGE_TOP, body.keys()
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= max_items
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert _REVISION_ID_RE.fullmatch(item["revisionId"])
        assert "snapshot" not in item
    nc = body["nextCursor"]
    assert nc is None or (isinstance(nc, str) and nc != "")
    if isinstance(nc, str):
        assert nc.startswith(_CURSOR_PREFIX)
        assert len(nc) <= _CURSOR_MAX_LEN


# ---------- failure-first：新路由必须真实 404 ----------


def test_page_route_registered_and_not_swallowed_by_revision_id(disabled_client):
    """
    用途：静态 /page 必须真实注册，不得被动态 {revision_id} 吞为 revision_not_found。
    说明：failure-first 阶段本路径曾返回 404 editor_state_revision_not_found。
    """
    client = disabled_client
    pid = _create_project(client, name="页路由注册")
    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    assert body["items"] == []
    assert body["nextCursor"] is None
    # 旧列表仍可用且 shape 不变
    listed = client.get(_list_url(pid))
    assert listed.status_code == 200, listed.text
    assert set(listed.json().keys()) == _LIST_TOP


# ---------- 常量 / 源码门禁 ----------


def test_page_size_constant_and_old_list_limit_independent():
    from app.services import editor_state_revision_history_service as hist
    from app.services import editor_state_revision_service as rev

    assert hist.REVISION_PAGE_SIZE == 10
    assert hist.MAX_REVISIONS_LIST == 10
    assert rev.MAX_REVISIONS_PER_PROJECT == 20
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert re.search(r"^REVISION_PAGE_SIZE\s*=\s*10\b", src, re.MULTILINE)
    assert re.search(r"^MAX_REVISIONS_LIST\s*=\s*10\b", src, re.MULTILINE)
    # 禁止主动偏移分页与总数查询（SQLAlchemy 方言 LIMIT 旁 OFFSET 0 另由 SQL 用例约束）
    assert ".offset(" not in src
    assert "func.count" not in src
    assert "COUNT(*)" not in src.upper()


# ---------- 0/1/10/11/20 边界与两页不重不漏 ----------


def test_page_empty_zero_items_next_null(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="空页")
    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    assert body["items"] == []
    assert body["nextCursor"] is None


@pytest.mark.parametrize("n,expect_next", [(1, False), (10, False), (11, True), (20, True)])
def test_page_boundaries_first_page(disabled_client, n: int, expect_next: bool):
    client = disabled_client
    pid = _create_project(client, name=f"边界{n}")
    ordered = _seed_n_revisions(pid, n, tag_prefix=f"b{n}_")
    assert len(ordered) == n

    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    expect_count = min(10, n)
    assert len(body["items"]) == expect_count
    got_ids = [it["revisionId"] for it in body["items"]]
    expect_ids = [r["id"] for r in ordered[:expect_count]]
    assert got_ids == expect_ids
    if expect_next:
        assert body["nextCursor"] is not None
        assert body["nextCursor"].startswith(_CURSOR_PREFIX)
        # nextCursor 必须对应本页第 10 条位置
        tenth = ordered[9]
        assert body["nextCursor"] == _encode_valid_cursor(tenth["created_at"], tenth["id"])
    else:
        assert body["nextCursor"] is None


def test_page_20_two_pages_no_overlap_no_gap(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="二十条两页")
    ordered = _seed_n_revisions(pid, 20, tag_prefix="t20_")
    all_ids = [r["id"] for r in ordered]
    assert len(all_ids) == 20

    p1 = client.get(_page_url(pid))
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    _assert_page_shape(b1)
    assert len(b1["items"]) == 10
    assert b1["nextCursor"] is not None
    ids1 = [it["revisionId"] for it in b1["items"]]
    assert ids1 == all_ids[:10]

    p2 = client.get(_page_url(pid), params={"cursor": b1["nextCursor"]})
    assert p2.status_code == 200, p2.text
    b2 = p2.json()
    _assert_page_shape(b2)
    assert len(b2["items"]) == 10
    assert b2["nextCursor"] is None
    ids2 = [it["revisionId"] for it in b2["items"]]
    assert ids2 == all_ids[10:20]

    assert set(ids1).isdisjoint(set(ids2))
    assert ids1 + ids2 == all_ids


def test_page_11_second_page_single_item(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="十一条")
    ordered = _seed_n_revisions(pid, 11, tag_prefix="t11_")
    p1 = client.get(_page_url(pid)).json()
    assert len(p1["items"]) == 10
    assert p1["nextCursor"] is not None
    p2 = client.get(_page_url(pid), params={"cursor": p1["nextCursor"]}).json()
    assert [it["revisionId"] for it in p2["items"]] == [ordered[10]["id"]]
    assert p2["nextCursor"] is None


def test_page_same_created_at_stable_id_desc(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="并列时间")
    base = datetime(2026, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
    ordered = _seed_n_revisions(
        pid, 15, base_time=base, tag_prefix="tie_", same_created_at=True
    )
    # 全部同一 created_at，排序仅靠 id DESC
    assert len({r["created_at_iso"] for r in ordered}) == 1
    assert [r["id"] for r in ordered] == sorted(
        (r["id"] for r in ordered), reverse=True
    )

    p1 = client.get(_page_url(pid)).json()
    p2 = client.get(_page_url(pid), params={"cursor": p1["nextCursor"]}).json()
    ids = [it["revisionId"] for it in p1["items"]] + [
        it["revisionId"] for it in p2["items"]
    ]
    assert ids == [r["id"] for r in ordered]
    assert len(ids) == len(set(ids))


def test_same_cursor_repeat_deterministic(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="重复游标")
    _seed_n_revisions(pid, 15, tag_prefix="rep_")
    p1 = client.get(_page_url(pid)).json()
    cur = p1["nextCursor"]
    assert cur
    a = client.get(_page_url(pid), params={"cursor": cur}).json()
    b = client.get(_page_url(pid), params={"cursor": cur}).json()
    assert a == b
    assert [it["revisionId"] for it in a["items"]] == [
        it["revisionId"] for it in b["items"]
    ]


# ---------- 旧列表兼容 / 未知查询参数 ----------


def test_old_list_still_items_only_max_ten(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="旧列表兼容")
    ordered = _seed_n_revisions(pid, 15, tag_prefix="old_")
    listed = client.get(_list_url(pid))
    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    body = listed.json()
    assert set(body.keys()) == _LIST_TOP
    assert "nextCursor" not in body
    assert len(body["items"]) == 10
    assert [it["revisionId"] for it in body["items"]] == [r["id"] for r in ordered[:10]]

    # 未知分页参数不得改变旧列表
    tampered = client.get(
        _list_url(pid),
        params={
            "limit": 100,
            "offset": 5,
            "cursor": "x",
            "source": "task",
            "search": _SECRET,
            "q": "正文",
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == body


def test_page_unknown_query_params_do_not_change_fixed_page(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="新页未知参数")
    _seed_n_revisions(pid, 15, tag_prefix="uq_")
    baseline = client.get(_page_url(pid)).json()
    tampered = client.get(
        _page_url(pid),
        params={
            "limit": 1,
            "offset": 3,
            "page": 2,
            "source": "task",
            "search": _SECRET,
            "q": "正文",
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == baseline
    assert len(tampered.json()["items"]) == 10


# ---------- 非法游标矩阵 ----------


@pytest.mark.parametrize(
    "cursor,label",
    [
        ("", "blank"),
        ("   ", "whitespace"),
        ("x" * 193, "too_long"),
        ("esrc2_" + "a" * 20, "wrong_prefix"),
        ("esr_" + "a" * 20, "missing_esrc_prefix"),
        ("esrc1_", "empty_body"),
        ("esrc1_!!!not-b64!!!", "bad_base64"),
        ("esrc1_" + base64.urlsafe_b64encode(b"not-json").decode().rstrip("="), "bad_json"),
    ],
)
def test_invalid_cursor_matrix_basic(disabled_client, cursor: str, label: str):
    client = disabled_client
    pid = _create_project(client, name=f"坏游标-{label}")
    _seed_n_revisions(pid, 3, tag_prefix=f"ic{label[:4]}_")
    res = client.get(_page_url(pid), params={"cursor": cursor})
    _assert_fixed_error(
        res, 400, _CODE_CURSOR_INVALID, forbid_echo=cursor if cursor.strip() else None
    )
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID


def test_invalid_cursor_payload_variants(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="坏载荷")
    rows = _seed_n_revisions(pid, 3, tag_prefix="pl_")
    good_id = rows[0]["id"]
    good_t = _datetime_to_us(rows[0]["created_at"])

    variants: list[tuple[str, object]] = [
        ("extra_key", {"i": good_id, "t": good_t, "x": 1}),
        ("missing_i", {"t": good_t}),
        ("missing_t", {"i": good_id}),
        ("bool_t", {"i": good_id, "t": True}),
        ("float_t", {"i": good_id, "t": 1.5}),
        ("str_t", {"i": good_id, "t": "123"}),
        ("neg_t", {"i": good_id, "t": -1}),
        ("huge_t", {"i": good_id, "t": 10**20}),
        ("bad_id", {"i": "not_an_esr", "t": good_t}),
        ("bad_id_hex", {"i": "esr_" + "g" * 32, "t": good_t}),
        ("bool_i", {"i": True, "t": good_t}),
        ("empty_keys", {}),
    ]
    for label, payload in variants:
        cur = _encode_cursor_payload(payload)  # type: ignore[arg-type]
        res = client.get(_page_url(pid), params={"cursor": cur})
        _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur)
        assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID, label


def test_non_canonical_cursor_rejected(disabled_client):
    """用途：空格/键序/填充 base64 等非规范变体必须 400。"""
    client = disabled_client
    pid = _create_project(client, name="非规范游标")
    rows = _seed_n_revisions(pid, 3, tag_prefix="nc_")
    rid = rows[0]["id"]
    t = _datetime_to_us(rows[0]["created_at"])

    # 非 sort_keys / 有空格
    messy = json.dumps({"t": t, "i": rid}, separators=(", ", ": "))
    body = base64.urlsafe_b64encode(messy.encode()).decode().rstrip("=")
    cur_space = _CURSOR_PREFIX + body
    res = client.get(_page_url(pid), params={"cursor": cur_space})
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur_space)

    # 带 padding 的 base64url
    raw = json.dumps(
        {"i": rid, "t": t}, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    padded = base64.urlsafe_b64encode(raw.encode()).decode()  # 可能含 =
    if "=" in padded:
        cur_pad = _CURSOR_PREFIX + padded
        res2 = client.get(_page_url(pid), params={"cursor": cur_pad})
        _assert_fixed_error(res2, 400, _CODE_CURSOR_INVALID, forbid_echo=cur_pad)

    # 标准规范游标应成功
    good = _encode_valid_cursor(rows[0]["created_at"], rid)
    ok = client.get(_page_url(pid), params={"cursor": good})
    assert ok.status_code == 200, ok.text


# ---------- 跨域 / 项目 404 ----------


def test_project_not_found_and_cross_scope_zero_leak(disabled_client):
    client = disabled_client
    a = _create_project(client, name="跨域A")
    b = _create_project(client, name="跨域B")
    rows_a = _seed_n_revisions(a, 12, tag_prefix="xa_")
    rows_b = _seed_n_revisions(b, 5, tag_prefix="xb_")
    before_a = _domain_snapshot(a)
    before_b = _domain_snapshot(b)

    missing = client.get(_page_url("proj_missing_p12fb"))
    _assert_fixed_error(missing, 404, "project_not_found")
    assert "proj_missing_p12fb" not in missing.text

    # 非法游标 + 不存在项目：项目 404 优先
    missing_bad_cur = client.get(
        _page_url("proj_missing_p12fb"),
        params={"cursor": "esrc1_bad"},
    )
    _assert_fixed_error(missing_bad_cur, 404, "project_not_found")

    # 跨项目：用 A 的游标查 B，不得读到 A 的数据
    cur_a = client.get(_page_url(a)).json()["nextCursor"]
    assert cur_a
    cross = client.get(_page_url(b), params={"cursor": cur_a})
    assert cross.status_code == 200, cross.text
    cross_ids = {it["revisionId"] for it in cross.json()["items"]}
    assert cross_ids.isdisjoint({r["id"] for r in rows_a})
    # B 只有 5 条，游标位置在 A 的第 10 条，键集谓词可能返回 B 中“更旧”子集；不得出现 A
    for it in cross.json()["items"]:
        assert it["revisionId"] in {r["id"] for r in rows_b}

    _ensure_workspace(_WS_OTHER)
    other_pid = "proj_other_space_p12fb"
    db = SessionLocal()
    try:
        if db.get(Project, other_pid) is None:
            db.add(
                Project(
                    id=other_pid,
                    workspace_id=_WS_OTHER,
                    name="外空间项目P12FB",
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
    )

    cross_ws = client.get(
        _page_url(a),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(cross_ws, 404, "project_not_found")
    assert rows_a[0]["id"] not in cross_ws.text

    other_ok = client.get(
        _page_url(other_pid),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert other_ok.status_code == 200, other_ok.text
    assert other_ok.json()["items"][0]["revisionId"] == other_rid

    assert _domain_snapshot(a) == before_a
    assert _domain_snapshot(b) == before_b


# ---------- SQL 五列 / LIMIT 11 / 零 OFFSET/COUNT ----------


def test_page_sql_five_columns_limit_11_keyset_no_offset_count(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="SQL投影")
    ordered = _seed_n_revisions(pid, 15, tag_prefix="sql_")
    del ordered  # 仅用于播种顺序

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low or "projects" in low:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        p1 = client.get(_page_url(pid))
        assert p1.status_code == 200, p1.text
        cur = p1.json()["nextCursor"]
        p2 = client.get(_page_url(pid), params={"cursor": cur})
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

    # 服务源码禁止主动 .offset( 分页；SQLAlchemy 方言可能带 OFFSET 0
    svc_src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert ".offset(" not in svc_src
    assert "func.count" not in svc_src
    assert "COUNT(*)" not in svc_src.upper()

    found_limit_11 = False
    found_keyset = False
    for sql, params in rev_selects:
        compact = " ".join(sql.split())
        low = compact.lower()
        assert "snapshot_json" not in low, sql
        assert "count(" not in low
        # 若方言输出 OFFSET，参数必须为 0（非分页偏移）
        if re.search(r"\boffset\b", low):
            vals: list[object]
            if isinstance(params, dict):
                vals = list(params.values())
            elif isinstance(params, (list, tuple)):
                vals = list(params)
            else:
                vals = [params]
            assert 0 in vals, f"非零 OFFSET 分页禁止: {sql} params={params}"
            # LIMIT 11 与 OFFSET 0 同现时，末两绑定通常为 11, 0
            assert vals[-1] == 0, params
            assert 11 in vals, params
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None
        select_list = match.group(1).lower()
        for col in ("state_version", "snapshot_bytes", "source_kind", "created_at"):
            assert col in select_list, (col, sql)
        assert re.search(r"\bid\b", select_list) or ".id" in select_list
        if "limit" in low:
            found_limit_11 = True
        if "<" in low and ("or" in low or "and" in low):
            found_keyset = True

    assert found_limit_11, rev_selects
    # 第二页必须出现键集谓词
    assert found_keyset, f"未发现键集谓词: {rev_selects}"

    # 项目校验最小投影
    project_selects = [
        s
        for s, _p in captured
        if re.search(r"\bfrom\s+projects\b", " ".join(s.split()), re.I)
    ]
    assert project_selects
    assert any(
        "name"
        not in re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", " ".join(s.split())).group(1).lower()  # type: ignore[union-attr]
        for s in project_selects
        if re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", " ".join(s.split()))
    )


def test_page_sql_limit_bound_is_eleven(disabled_client):
    """用途：硬核断言 LIMIT 参数值为 11（lookahead）。"""
    client = disabled_client
    pid = _create_project(client, name="LIMIT11")
    _seed_n_revisions(pid, 12, tag_prefix="lim_")

    bounds: list[object] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            bounds.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = client.get(_page_url(pid))
        assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert bounds, "未捕获 SELECT"
    saw_11 = False
    for sql, params in bounds:
        blob = (sql + " " + repr(params)).lower()
        if "limit" in sql.lower() and (
            " 11" in f" {sql.lower()} "
            or (params is not None and 11 in (params if isinstance(params, (list, tuple)) else list(getattr(params, "values", lambda: params)()) if not isinstance(params, (str, bytes)) else []))
            or (isinstance(params, dict) and 11 in params.values())
            or (isinstance(params, (list, tuple)) and 11 in params)
            or re.search(r"limit\s+11\b", sql, re.I)
        ):
            saw_11 = True
            break
        # SQLAlchemy 可能把 limit 放在 compiled 参数
        if params is not None:
            try:
                vals = list(params) if isinstance(params, (list, tuple)) else list(params.values())  # type: ignore[union-attr]
            except Exception:
                vals = [params]
            if 11 in vals:
                saw_11 = True
                break
    assert saw_11, bounds


# ---------- lookahead 损坏整页 corrupt ----------


def test_lookahead_corrupt_fails_whole_page(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="lookahead损坏")
    ordered = _seed_n_revisions(pid, 12, tag_prefix="cr_")
    # 第 11 条（0-based index 10）损坏：列表会 LIMIT 11，校验含 lookahead
    bad_id = ordered[10]["id"]
    _raw_sql_update_revision(bad_id, state_version="not_a_valid_esv")

    res = client.get(_page_url(pid))
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    # 精确固定错误体：仅 detail，消息固定，无部分结果/ID/正文泄漏
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert body["detail"]["message"] == "修订记录数据损坏，无法读取"
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    assert bad_id not in res.text
    assert _SECRET not in res.text


# ---------- 游标时间边界 / 平台无关转换 / 编码端校验 ----------


def test_cursor_time_bounds_platform_independent_roundtrip():
    """
    用途：证明 CURSOR_T_MIN/MAX 经平台无关 _us_to_datetime 可往返编码/解码；
      禁止依赖 fromtimestamp；越界 t 固定 cursor_invalid。
    """
    from app.services import editor_state_revision_history_service as hist

    # 源码 AST：禁止 datetime.fromtimestamp 调用；必须存在 timedelta 用法
    svc_src = _SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(svc_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "fromtimestamp":
                pytest.fail("禁止调用 fromtimestamp（平台相关时间转换）")
            if isinstance(func, ast.Name) and func.id == "fromtimestamp":
                pytest.fail("禁止调用 fromtimestamp（平台相关时间转换）")
    assert "timedelta(microseconds=" in svc_src or "timedelta(" in svc_src

    rid = "esr_" + ("ab" * 16)
    # 最小值 0：合法往返
    dt_min = hist._us_to_datetime(hist.CURSOR_T_MIN)
    assert dt_min == datetime(1970, 1, 1, tzinfo=timezone.utc)
    assert hist._datetime_to_us(dt_min) == hist.CURSOR_T_MIN
    cur_min = hist.encode_revision_page_cursor(dt_min, rid)
    back_min_dt, back_min_id = hist.decode_revision_page_cursor(cur_min)
    assert back_min_id == rid
    assert hist._datetime_to_us(back_min_dt) == hist.CURSOR_T_MIN

    # 最大值 9999-12-31 23:59:59.999999：合法往返（Windows 亦不可抛）
    dt_max = hist._us_to_datetime(hist.CURSOR_T_MAX)
    assert dt_max == datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc)
    assert hist._datetime_to_us(dt_max) == hist.CURSOR_T_MAX
    cur_max = hist.encode_revision_page_cursor(dt_max, rid)
    back_max_dt, back_max_id = hist.decode_revision_page_cursor(cur_max)
    assert back_max_id == rid
    assert hist._datetime_to_us(back_max_dt) == hist.CURSOR_T_MAX

    # 越界 t=CURSOR_T_MAX+1：解码固定 cursor_invalid
    bad_payload = _encode_cursor_payload({"i": rid, "t": hist.CURSOR_T_MAX + 1})
    with pytest.raises(hist.EditorStateRevisionHistoryError) as exc_max:
        hist.decode_revision_page_cursor(bad_payload)
    assert exc_max.value.status_code == 400
    assert exc_max.value.code == hist.CODE_CURSOR_INVALID
    assert exc_max.value.message == hist.MSG_CURSOR_INVALID

    # 越界 t=-1：解码固定 cursor_invalid
    bad_neg = _encode_cursor_payload({"i": rid, "t": hist.CURSOR_T_MIN - 1})
    with pytest.raises(hist.EditorStateRevisionHistoryError) as exc_neg:
        hist.decode_revision_page_cursor(bad_neg)
    assert exc_neg.value.status_code == 400
    assert exc_neg.value.code == hist.CODE_CURSOR_INVALID


def test_cursor_t_max_plus_one_http_invalid(disabled_client):
    """用途：HTTP 层 CURSOR_T_MAX+1 固定 400 editor_state_revision_cursor_invalid。"""
    from app.services import editor_state_revision_history_service as hist

    client = disabled_client
    pid = _create_project(client, name="游标上界+1")
    _seed_n_revisions(pid, 1, tag_prefix="tmax_")
    rid = "esr_" + ("cd" * 16)
    bad = _encode_cursor_payload({"i": rid, "t": hist.CURSOR_T_MAX + 1})
    res = client.get(_page_url(pid), params={"cursor": bad})
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=bad)
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID
    assert "items" not in res.text
    assert "nextCursor" not in res.text


def test_encode_rejects_pre1970_and_bad_id_as_corrupt():
    """用途：编码端拒绝 pre-1970 时间与非法 revision ID，固定 corrupt。"""
    from app.services import editor_state_revision_history_service as hist

    rid_ok = "esr_" + ("ef" * 16)
    pre = datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    with pytest.raises(hist.EditorStateRevisionHistoryError) as exc_pre:
        hist.encode_revision_page_cursor(pre, rid_ok)
    assert exc_pre.value.status_code == 500
    assert exc_pre.value.code == hist.CODE_REVISION_CORRUPT
    assert exc_pre.value.message == hist.MSG_REVISION_CORRUPT

    dt_ok = hist._us_to_datetime(hist.CURSOR_T_MIN)
    with pytest.raises(hist.EditorStateRevisionHistoryError) as exc_id:
        hist.encode_revision_page_cursor(dt_ok, "not_a_revision_id")
    assert exc_id.value.status_code == 500
    assert exc_id.value.code == hist.CODE_REVISION_CORRUPT


def test_pre1970_tenth_with_lookahead_page_corrupt(disabled_client):
    """
    用途：第 10 条为 pre-1970 且存在第 11 条 lookahead 时，
      公开页整页固定 corrupt，绝不返回不可用 nextCursor / items / revisionId / 正文。
    """
    client = disabled_client
    pid = _create_project(client, name="pre1970第十条")
    # 11 条：前 9 条 2026 年较新，第 10 条 pre-1970，第 11 条更旧作 lookahead
    modern_base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    pre = datetime(1969, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
    older = datetime(1968, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    modern_ids: list[str] = []
    for i in range(9):
        state = _variant(f"m{i:02d}")
        snap_json = editor_state_service.canonical_snapshot_json(
            editor_state_service.extract_canonical_snapshot(state)
        )
        rid = "esr_" + secrets.token_hex(16)
        _insert_raw_revision(
            project_id=pid,
            revision_id=rid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            source_kind="task",
            created_at=modern_base + timedelta(seconds=i),
        )
        modern_ids.append(rid)

    state10 = _variant("pre10")
    snap10 = editor_state_service.canonical_snapshot_json(
        editor_state_service.extract_canonical_snapshot(state10)
    )
    rid10 = "esr_" + secrets.token_hex(16)
    _insert_raw_revision(
        project_id=pid,
        revision_id=rid10,
        snapshot_json=snap10,
        state_version=state10["stateVersion"],
        snapshot_bytes=len(snap10.encode("utf-8")),
        source_kind="task",
        created_at=pre,
    )

    state11 = _variant("pre11")
    snap11 = editor_state_service.canonical_snapshot_json(
        editor_state_service.extract_canonical_snapshot(state11)
    )
    rid11 = "esr_" + secrets.token_hex(16)
    _insert_raw_revision(
        project_id=pid,
        revision_id=rid11,
        snapshot_json=snap11,
        state_version=state11["stateVersion"],
        snapshot_bytes=len(snap11.encode("utf-8")),
        source_kind="task",
        created_at=older,
    )

    # 确认排序：DESC 后 index 9 为 pre-1970 的 rid10，index 10 为 lookahead
    ordered = _db_rev_rows(pid)
    assert len(ordered) == 11
    assert ordered[9]["id"] == rid10
    assert ordered[10]["id"] == rid11
    assert ordered[9]["created_at"].year < 1970

    res = client.get(_page_url(pid))
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert body["detail"]["code"] == "editor_state_revision_corrupt"
    assert body["detail"]["message"] == "修订记录数据损坏，无法读取"
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    assert rid10 not in res.text
    assert rid11 not in res.text
    assert _SECRET not in res.text
    for mid in modern_ids:
        assert mid not in res.text


# ---------- 五域零写 + AST ----------


def test_page_get_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="五域零写")
    _seed_n_revisions(pid, 12, tag_prefix="zw_")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text

    before = _domain_snapshot(pid)
    p1 = client.get(_page_url(pid))
    assert p1.status_code == 200, p1.text
    assert _domain_snapshot(pid) == before

    cur = p1.json()["nextCursor"]
    p2 = client.get(_page_url(pid), params={"cursor": cur})
    assert p2.status_code == 200, p2.text
    assert _domain_snapshot(pid) == before

    bad = client.get(_page_url(pid), params={"cursor": "esrc1_bad"})
    _assert_fixed_error(bad, 400, _CODE_CURSOR_INVALID)
    assert _domain_snapshot(pid) == before


def test_service_api_no_write_ops_ast():
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
        for token in ("db.commit", "db.rollback", "db.flush", "db.refresh", "with_for_update"):
            assert token not in low, f"{path.name} 含写路径 token: {token}"


def test_static_page_route_registered_before_dynamic_revision_id():
    """用途：静态 /page 必须排在动态 /{revision_id} 之前，避免被吞。"""
    src = _API_PATH.read_text(encoding="utf-8")
    page_pos = src.find("/editor-state-revisions/page")
    dyn_pos = src.find("/editor-state-revisions/{revision_id}")
    assert page_pos != -1, "缺少静态 /page 路由"
    assert dyn_pos != -1, "缺少动态 revision 路由"
    assert page_pos < dyn_pos, "静态 /page 必须注册在动态 {revision_id} 之前"
