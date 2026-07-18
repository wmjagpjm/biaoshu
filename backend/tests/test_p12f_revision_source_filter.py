"""
模块：P12F-D editor-state 修订历史来源筛选专项测试
用途：验收 /editor-state-revisions/page 的 sourceKind 服务端筛选、esrc2 游标绑定、
  非法来源 400、esrc1/esrc2 正反绑定、SQL 五列+来源谓词+LIMIT11、lookahead 与五域零写。
对接：GET .../editor-state-revisions/page[?sourceKind=&cursor=]；
  editor_state_revision_history_service；api.editor_state_revisions。
二次开发：
  - 禁止 mock SQLite、宽泛状态码、固定 sleep、恒真 or、反射输入假绿；
  - 红测必须证明旧实现忽略 sourceKind/无 esrc2，而非收集/导入/语法错误；
  - 合法九类来源复用 REVISION_SOURCE_KINDS；未知 source/search/q 仍忽略。
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
from app.services import editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fd"
_SECRET = "SECRET_P12FD_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/page"
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt", "displayName"}
)
_PAGE_TOP = frozenset({"items", "nextCursor"})
_LIST_TOP = frozenset({"items"})
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_CURSOR_V1 = "esrc1_"
_CURSOR_V2 = "esrc2_"
_CURSOR_MAX_LEN = 192
_CODE_CURSOR_INVALID = "editor_state_revision_cursor_invalid"
_MSG_CURSOR_INVALID = "修订分页游标无效"
_CODE_SOURCE_INVALID = "editor_state_revision_source_invalid"
_MSG_SOURCE_INVALID = "修订来源筛选无效"

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


def _create_project(client: TestClient, name: str = "P12F-D项目") -> str:
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
        assert len(nc) <= _CURSOR_MAX_LEN


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


def _encode_cursor_payload(payload: dict, *, prefix: str = _CURSOR_V1) -> str:
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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FD") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fd",
                )
            )
            db.commit()
    finally:
        db.close()


def _seed_mixed_revisions(
    project_id: str,
    specs: list[tuple[str, str | None]],
    *,
    base_time: datetime | None = None,
    same_created_at: bool = False,
) -> list[dict]:
    """
    用途：按 (source_kind, tag) 插入修订；返回 created_at DESC,id DESC 有序。
    specs 中 tag 为 None 时自动生成。
    """
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, (source_kind, tag) in enumerate(specs):
        use_tag = tag if tag is not None else f"m{i:02d}"
        state = _variant(use_tag)
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
            source_kind=source_kind,
            created_at=created,
        )
    return _db_rev_rows(project_id)


def _seed_n_of_source(
    project_id: str,
    n: int,
    source_kind: str,
    *,
    tag_prefix: str = "n",
    base_time: datetime | None = None,
    same_created_at: bool = False,
) -> list[dict]:
    specs = [(source_kind, f"{tag_prefix}{i:02d}") for i in range(n)]
    return _seed_mixed_revisions(
        project_id,
        specs,
        base_time=base_time,
        same_created_at=same_created_at,
    )


def _filtered_ordered(rows: list[dict], source_kind: str) -> list[dict]:
    return [r for r in rows if r["source_kind"] == source_kind]


# ---------- 权威枚举与源码门禁 ----------


def test_nine_source_kinds_match_authority():
    """用途：合法来源必须与权威 REVISION_SOURCE_KINDS 完全一致。"""
    assert set(_NINE_SOURCES) == set(editor_state_revision_service.REVISION_SOURCE_KINDS)
    from app.services import editor_state_revision_history_service as hist

    # 精确全等：history service 权威集合与 revision service 集合必须一致
    assert set(hist.REVISION_SOURCE_KINDS) == set(
        editor_state_revision_service.REVISION_SOURCE_KINDS
    )
    assert set(hist.REVISION_SOURCE_KINDS) == set(_NINE_SOURCES)


# ---------- 九来源逐值筛选 ----------


@pytest.mark.parametrize("source_kind", list(_NINE_SOURCES))
def test_filter_each_of_nine_sources(disabled_client, source_kind: str):
    """
    用途：九类合法 sourceKind 各自精确命中；响应仅含该来源；无 snapshot。
    红测：旧实现忽略 sourceKind 时会返回混排全集，导致 source 断言失败。
    """
    client = disabled_client
    pid = _create_project(client, name=f"九源-{source_kind}")
    # 每种来源各 2 条 + 目标来源额外 1 条，保证混排
    specs: list[tuple[str, str | None]] = []
    for sk in _NINE_SOURCES:
        specs.append((sk, f"{sk[:3]}_a"))
        specs.append((sk, f"{sk[:3]}_b"))
    specs.append((source_kind, f"{source_kind[:3]}_extra"))
    ordered = _seed_mixed_revisions(pid, specs)
    expect = _filtered_ordered(ordered, source_kind)
    assert len(expect) >= 2

    res = client.get(_page_url(pid), params={"sourceKind": source_kind})
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    got_ids = [it["revisionId"] for it in body["items"]]
    expect_ids = [r["id"] for r in expect[:10]]
    assert got_ids == expect_ids
    for it in body["items"]:
        assert it["sourceKind"] == source_kind
    # 不得混入其他来源
    other_ids = {r["id"] for r in ordered if r["source_kind"] != source_kind}
    assert set(got_ids).isdisjoint(other_ids)


# ---------- 0/1/10/11/20 边界 ----------


@pytest.mark.parametrize(
    "n,expect_next",
    [(0, False), (1, False), (10, False), (11, True), (20, True)],
)
def test_filter_boundaries_first_page(disabled_client, n: int, expect_next: bool):
    client = disabled_client
    pid = _create_project(client, name=f"筛选边界{n}")
    # 另插入噪声来源，证明筛选不看噪声
    if n > 0:
        _seed_n_of_source(pid, n, "task", tag_prefix=f"b{n}_")
    _seed_n_of_source(
        pid,
        5,
        "revise",
        tag_prefix=f"noise{n}_",
        base_time=datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc),
    )
    ordered = _filtered_ordered(_db_rev_rows(pid), "task")
    assert len(ordered) == n

    res = client.get(_page_url(pid), params={"sourceKind": "task"})
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_page_shape(body)
    expect_count = min(10, n)
    assert len(body["items"]) == expect_count
    assert [it["revisionId"] for it in body["items"]] == [
        r["id"] for r in ordered[:expect_count]
    ]
    if expect_next:
        assert body["nextCursor"] is not None
        assert body["nextCursor"].startswith(_CURSOR_V2)
        tenth = ordered[9]
        assert body["nextCursor"] == _encode_esrc2(
            tenth["created_at"], tenth["id"], "task"
        )
        assert len(body["nextCursor"]) <= _CURSOR_MAX_LEN
    else:
        assert body["nextCursor"] is None


def test_filter_20_two_pages_no_overlap_no_gap(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="筛选二十条")
    # 混入其他来源
    _seed_n_of_source(pid, 20, "callback", tag_prefix="cb_")
    _seed_n_of_source(
        pid,
        8,
        "task",
        tag_prefix="noise_",
        base_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    ordered = _filtered_ordered(_db_rev_rows(pid), "callback")
    assert len(ordered) == 20
    all_ids = [r["id"] for r in ordered]

    p1 = client.get(_page_url(pid), params={"sourceKind": "callback"})
    assert p1.status_code == 200, p1.text
    b1 = p1.json()
    _assert_page_shape(b1)
    assert len(b1["items"]) == 10
    assert b1["nextCursor"] is not None
    assert b1["nextCursor"].startswith(_CURSOR_V2)
    ids1 = [it["revisionId"] for it in b1["items"]]
    assert ids1 == all_ids[:10]
    for it in b1["items"]:
        assert it["sourceKind"] == "callback"

    p2 = client.get(
        _page_url(pid),
        params={"sourceKind": "callback", "cursor": b1["nextCursor"]},
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
    for it in b2["items"]:
        assert it["sourceKind"] == "callback"


def test_filter_same_created_at_stable_id_desc(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="筛选并列时间")
    base = datetime(2026, 6, 15, 8, 30, 0, tzinfo=timezone.utc)
    ordered = _seed_n_of_source(
        pid, 15, "local_parser", tag_prefix="tie_", base_time=base, same_created_at=True
    )
    # 噪声
    _seed_n_of_source(
        pid,
        3,
        "task",
        tag_prefix="tn_",
        base_time=base,
        same_created_at=True,
    )
    filtered = _filtered_ordered(_db_rev_rows(pid), "local_parser")
    assert len(filtered) == 15
    assert [r["id"] for r in filtered] == sorted(
        (r["id"] for r in filtered), reverse=True
    )

    p1 = client.get(_page_url(pid), params={"sourceKind": "local_parser"}).json()
    p2 = client.get(
        _page_url(pid),
        params={"sourceKind": "local_parser", "cursor": p1["nextCursor"]},
    ).json()
    ids = [it["revisionId"] for it in p1["items"]] + [
        it["revisionId"] for it in p2["items"]
    ]
    assert ids == [r["id"] for r in filtered]
    assert len(ids) == len(set(ids))


def test_filter_same_cursor_repeat_deterministic(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="筛选重复游标")
    _seed_n_of_source(pid, 15, "revise", tag_prefix="rep_")
    p1 = client.get(_page_url(pid), params={"sourceKind": "revise"}).json()
    cur = p1["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V2)
    a = client.get(
        _page_url(pid), params={"sourceKind": "revise", "cursor": cur}
    ).json()
    b = client.get(
        _page_url(pid), params={"sourceKind": "revise", "cursor": cur}
    ).json()
    assert a == b
    assert [it["revisionId"] for it in a["items"]] == [
        it["revisionId"] for it in b["items"]
    ]


# ---------- 混排不重不漏 ----------


def test_mixed_timeline_filter_no_overlap_no_gap(disabled_client):
    """用途：混排时间线按来源筛选后两页不重不漏，顺序仍为时间+ID 降序。"""
    client = disabled_client
    pid = _create_project(client, name="混排筛选")
    base = datetime(2026, 5, 1, 0, 0, 0, tzinfo=timezone.utc)
    # 交错插入 12 条 task 与 12 条 callback
    for i in range(12):
        for sk in ("task", "callback"):
            state = _variant(f"{sk[:2]}{i:02d}")
            snap_json = editor_state_service.canonical_snapshot_json(
                editor_state_service.extract_canonical_snapshot(state)
            )
            _insert_raw_revision(
                project_id=pid,
                revision_id="esr_" + secrets.token_hex(16),
                snapshot_json=snap_json,
                state_version=state["stateVersion"],
                snapshot_bytes=len(snap_json.encode("utf-8")),
                source_kind=sk,
                created_at=base + timedelta(seconds=i * 2 + (0 if sk == "task" else 1)),
            )
    ordered = _filtered_ordered(_db_rev_rows(pid), "task")
    assert len(ordered) == 12

    p1 = client.get(_page_url(pid), params={"sourceKind": "task"}).json()
    p2 = client.get(
        _page_url(pid),
        params={"sourceKind": "task", "cursor": p1["nextCursor"]},
    ).json()
    got = [it["revisionId"] for it in p1["items"]] + [
        it["revisionId"] for it in p2["items"]
    ]
    assert got == [r["id"] for r in ordered]
    assert all(it["sourceKind"] == "task" for it in p1["items"] + p2["items"])
    assert len(got) == len(set(got)) == 12


# ---------- esrc1 / esrc2 正反绑定 ----------


def test_unfiltered_keeps_esrc1_and_shape(disabled_client):
    """用途：无 sourceKind 时 nextCursor 仍为 esrc1，载荷精确 {i,t}。"""
    client = disabled_client
    pid = _create_project(client, name="无筛选esrc1")
    ordered = _seed_n_of_source(pid, 11, "task", tag_prefix="u1_")
    res = client.get(_page_url(pid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    assert body["nextCursor"] is not None
    assert body["nextCursor"].startswith(_CURSOR_V1)
    assert not body["nextCursor"].startswith(_CURSOR_V2)
    tenth = ordered[9]
    assert body["nextCursor"] == _encode_esrc1(tenth["created_at"], tenth["id"])


def test_esrc1_with_source_kind_rejected(disabled_client):
    """用途：esrc1 只能用于无筛选；携带任意合法 sourceKind 固定 cursor invalid。"""
    client = disabled_client
    pid = _create_project(client, name="esrc1带筛选")
    ordered = _seed_n_of_source(pid, 12, "task", tag_prefix="e1f_")
    cur = _encode_esrc1(ordered[9]["created_at"], ordered[9]["id"])
    res = client.get(
        _page_url(pid), params={"sourceKind": "task", "cursor": cur}
    )
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur)
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID


def test_esrc2_without_source_kind_rejected(disabled_client):
    """用途：esrc2 缺筛选固定 cursor invalid，禁止从游标自动采用来源。"""
    client = disabled_client
    pid = _create_project(client, name="esrc2无筛选")
    ordered = _seed_n_of_source(pid, 12, "task", tag_prefix="e2n_")
    cur = _encode_esrc2(ordered[9]["created_at"], ordered[9]["id"], "task")
    res = client.get(_page_url(pid), params={"cursor": cur})
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur)
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID
    # 响应固定 400：顶层仅 detail，不得静默按游标内 s=task 返回结果页
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert "items" not in body
    assert "nextCursor" not in body
    assert "revisionId" not in body
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text


def test_esrc2_with_mismatched_source_rejected(disabled_client):
    """用途：A 来源游标用于 B 筛选固定 cursor invalid。"""
    client = disabled_client
    pid = _create_project(client, name="esrc2错配")
    _seed_n_of_source(pid, 12, "task", tag_prefix="mis_t_")
    _seed_n_of_source(
        pid,
        12,
        "revise",
        tag_prefix="mis_r_",
        base_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    task_rows = _filtered_ordered(_db_rev_rows(pid), "task")
    cur_task = _encode_esrc2(
        task_rows[9]["created_at"], task_rows[9]["id"], "task"
    )
    res = client.get(
        _page_url(pid),
        params={"sourceKind": "revise", "cursor": cur_task},
    )
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur_task)
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID
    assert "task" not in res.text  # 不反射游标内来源


def test_esrc2_with_matching_source_ok(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="esrc2匹配")
    ordered = _seed_n_of_source(pid, 15, "callback", tag_prefix="e2ok_")
    p1 = client.get(_page_url(pid), params={"sourceKind": "callback"}).json()
    cur = p1["nextCursor"]
    assert cur and cur.startswith(_CURSOR_V2)
    p2 = client.get(
        _page_url(pid),
        params={"sourceKind": "callback", "cursor": cur},
    )
    assert p2.status_code == 200, p2.text
    b2 = p2.json()
    assert [it["revisionId"] for it in b2["items"]] == [
        r["id"] for r in ordered[10:15]
    ]


def test_esrc2_canonical_roundtrip_and_len(disabled_client):
    """用途：esrc2 规范紧凑 sort_keys 无填充 base64url，长度≤192。"""
    client = disabled_client
    pid = _create_project(client, name="esrc2规范")
    ordered = _seed_n_of_source(pid, 11, "task", tag_prefix="can_")
    p1 = client.get(_page_url(pid), params={"sourceKind": "task"}).json()
    cur = p1["nextCursor"]
    assert cur is not None
    assert len(cur) <= _CURSOR_MAX_LEN
    tenth = ordered[9]
    expected = _encode_esrc2(tenth["created_at"], tenth["id"], "task")
    assert cur == expected
    # 非规范（空格）必须 400
    messy = json.dumps(
        {
            "t": _datetime_to_us(tenth["created_at"]),
            "s": "task",
            "i": tenth["id"],
        },
        separators=(", ", ": "),
    )
    body = base64.urlsafe_b64encode(messy.encode()).decode().rstrip("=")
    bad = _CURSOR_V2 + body
    res = client.get(
        _page_url(pid), params={"sourceKind": "task", "cursor": bad}
    )
    _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=bad)


# ---------- 非法来源矩阵 ----------


@pytest.mark.parametrize(
    "source,label",
    [
        ("", "empty"),
        ("   ", "whitespace"),
        ("TASK", "upper"),
        ("Task", "mixed"),
        ("browser-put", "alias_dash"),
        ("browserPut", "alias_camel"),
        ("unknown_source", "unknown"),
        ("task ", "trailing_space"),
        (" task", "leading_space"),
        ("content_fuse", "partial"),
        ("null", "null_str"),
        ("1", "numeric"),
        (_SECRET, "secret"),
    ],
)
def test_invalid_source_kind_matrix(disabled_client, source: str, label: str):
    client = disabled_client
    pid = _create_project(client, name=f"非法来源-{label}")
    _seed_n_of_source(pid, 3, "task", tag_prefix=f"is{label[:3]}_")
    res = client.get(_page_url(pid), params={"sourceKind": source})
    _assert_fixed_error(
        res,
        400,
        _CODE_SOURCE_INVALID,
        forbid_echo=source if source.strip() else None,
    )
    assert res.json()["detail"]["message"] == _MSG_SOURCE_INVALID
    # 不反射输入
    if source and source.strip():
        assert source not in res.text


def test_invalid_source_with_non_esrc2_cursor_still_source_invalid(disabled_client):
    """
    用途：非法来源配非 esrc2 游标维持既有优先级 → source_invalid。
    二次开发：不得因存在任意 cursor 就扩大为 cursor_invalid。
    """
    client = disabled_client
    pid = _create_project(client, name="非法来源非esrc2")
    rows = _seed_n_of_source(pid, 3, "task", tag_prefix="isp_")
    # esrc1：合法规范但非 esrc2 形
    cur_esrc1 = _encode_esrc1(rows[0]["created_at"], rows[0]["id"])
    res1 = client.get(
        _page_url(pid),
        params={"sourceKind": "NOT_A_KIND", "cursor": cur_esrc1},
    )
    _assert_fixed_error(res1, 400, _CODE_SOURCE_INVALID, forbid_echo="NOT_A_KIND")
    assert res1.json()["detail"]["message"] == _MSG_SOURCE_INVALID
    assert set(res1.json().keys()) == {"detail"}
    assert "items" not in res1.text
    assert "nextCursor" not in res1.text
    assert "revisionId" not in res1.text
    assert cur_esrc1 not in res1.text

    # 任意非 esrc2_ 前缀垃圾游标
    garbage = "not_a_cursor_" + _SECRET
    res2 = client.get(
        _page_url(pid),
        params={"sourceKind": "NOT_A_KIND", "cursor": garbage},
    )
    _assert_fixed_error(res2, 400, _CODE_SOURCE_INVALID, forbid_echo="NOT_A_KIND")
    assert res2.json()["detail"]["message"] == _MSG_SOURCE_INVALID
    assert garbage not in res2.text


# ---------- esrc2 + 非法/缺筛选 → 固定 cursor_invalid ----------


def _assert_cursor_invalid_no_echo(
    res, *, forbid_parts: list[str]
) -> None:
    """用途：精确 400 cursor_invalid + 固定中文 + no-store + 零回显。"""
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


def test_canonical_esrc2_plus_illegal_source_is_cursor_invalid(disabled_client):
    """
    用途：合法规范 esrc2 + NOT_A_KIND 固定 cursor_invalid（契约 L42）。
    红测：旧实现先 normalize source → source_invalid，与冻结契约不符。
    """
    client = disabled_client
    pid = _create_project(client, name="esrc2非法来源")
    rows = _seed_n_of_source(pid, 12, "task", tag_prefix="e2il_")
    cur = _encode_esrc2(rows[9]["created_at"], rows[9]["id"], "task")
    assert cur.startswith(_CURSOR_V2)
    illegal = "NOT_A_KIND"
    res = client.get(
        _page_url(pid),
        params={"sourceKind": illegal, "cursor": cur},
    )
    _assert_cursor_invalid_no_echo(
        res, forbid_parts=[cur, illegal, "task", rows[9]["id"]]
    )


@pytest.mark.parametrize(
    "illegal,label",
    [
        ("", "empty"),
        ("   ", "whitespace"),
        ("TASK", "upper"),
        ("Task", "mixed"),
        ("task ", "trailing_space"),
        (" task", "leading_space"),
        ("unknown_source", "unknown"),
        ("browser-put", "alias_dash"),
    ],
)
def test_canonical_esrc2_plus_illegal_source_variants_cursor_invalid(
    disabled_client, illegal: str, label: str
):
    """用途：规范 esrc2 + 空串/空白/大小写/别名等非法筛选一律 cursor_invalid。"""
    client = disabled_client
    pid = _create_project(client, name=f"esrc2非法变体-{label}")
    rows = _seed_n_of_source(pid, 12, "task", tag_prefix=f"e2v{label[:3]}_")
    cur = _encode_esrc2(rows[9]["created_at"], rows[9]["id"], "task")
    res = client.get(
        _page_url(pid),
        params={"sourceKind": illegal, "cursor": cur},
    )
    forbid = [cur, rows[9]["id"]]
    if illegal.strip():
        forbid.append(illegal)
    _assert_cursor_invalid_no_echo(res, forbid_parts=forbid)


def test_same_illegal_source_without_cursor_still_source_invalid(disabled_client):
    """用途：无 cursor 的同一批非法来源仍固定 source_invalid（不扩大为 cursor）。"""
    client = disabled_client
    pid = _create_project(client, name="无游标非法来源")
    _seed_n_of_source(pid, 3, "task", tag_prefix="nsrc_")
    cases = [
        "NOT_A_KIND",
        "",
        "   ",
        "TASK",
        "Task",
        "task ",
        " task",
        "unknown_source",
        "browser-put",
    ]
    for illegal in cases:
        res = client.get(_page_url(pid), params={"sourceKind": illegal})
        _assert_fixed_error(
            res,
            400,
            _CODE_SOURCE_INVALID,
            forbid_echo=illegal if illegal.strip() else None,
        )
        assert res.json()["detail"]["message"] == _MSG_SOURCE_INVALID
        assert set(res.json().keys()) == {"detail"}
        assert "items" not in res.text
        assert "nextCursor" not in res.text
        assert "revisionId" not in res.text
        if illegal.strip():
            assert illegal not in res.text


def test_esrc2_prefix_plus_illegal_source_is_cursor_invalid(disabled_client):
    """用途：仅 esrc2_ 前缀的非规范游标 + 非法来源仍 cursor_invalid。"""
    client = disabled_client
    pid = _create_project(client, name="esrc2前缀非法来源")
    _seed_n_of_source(pid, 3, "task", tag_prefix="e2p_")
    cur = "esrc2_not_valid_payload_xyz"
    illegal = "NOT_A_KIND"
    res = client.get(
        _page_url(pid),
        params={"sourceKind": illegal, "cursor": cur},
    )
    _assert_cursor_invalid_no_echo(res, forbid_parts=[cur, illegal])


def test_missing_project_plus_esrc2_illegal_source_still_404(disabled_client):
    """用途：不存在项目 + esrc2/非法来源组合仍项目 404 最优先。"""
    client = disabled_client
    missing_pid = "proj_missing_p12fd_esrc2"
    # 构造规范形 esrc2（项目不存在时仍不得先做筛选/游标错误）
    fake_id = "esr_" + "a" * 32
    fake_t = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    cur = _encode_esrc2(fake_t, fake_id, "task")
    illegal = "NOT_A_KIND"

    combos = [
        {"sourceKind": illegal, "cursor": cur},
        {"sourceKind": "", "cursor": cur},
        {"sourceKind": "TASK", "cursor": cur},
        {"sourceKind": "   ", "cursor": cur},
        {"sourceKind": illegal},
        {"cursor": cur},
        {"sourceKind": "task", "cursor": cur},
    ]
    for params in combos:
        res = client.get(_page_url(missing_pid), params=params)
        _assert_fixed_error(res, 404, "project_not_found")
        assert res.json()["detail"]["message"] == "项目不存在或不可访问"
        assert set(res.json().keys()) == {"detail"}
        assert missing_pid not in res.text
        assert illegal not in res.text
        assert cur not in res.text
        assert "items" not in res.text
        assert "nextCursor" not in res.text
        assert "revisionId" not in res.text


def test_matched_filter_second_page_proves_no_cursor_source_adoption(
    disabled_client,
):
    """
    用途：正常匹配筛选第二页不重不漏，证明 SQL 只用显式 query 来源、
      未从游标自动采用来源。
    """
    client = disabled_client
    pid = _create_project(client, name="匹配二页不采用游标源")
    # 目标来源 15 条 + 噪声来源（若采用游标内源之外的逻辑会混入）
    ordered = _seed_n_of_source(pid, 15, "callback", tag_prefix="adopt_")
    _seed_n_of_source(
        pid,
        10,
        "task",
        tag_prefix="adoptn_",
        base_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    filtered = _filtered_ordered(_db_rev_rows(pid), "callback")
    assert len(filtered) == 15
    all_ids = [r["id"] for r in filtered]

    p1 = client.get(_page_url(pid), params={"sourceKind": "callback"})
    assert p1.status_code == 200, p1.text
    _assert_no_store(p1)
    b1 = p1.json()
    _assert_page_shape(b1)
    assert len(b1["items"]) == 10
    ids1 = [it["revisionId"] for it in b1["items"]]
    assert ids1 == all_ids[:10]
    assert all(it["sourceKind"] == "callback" for it in b1["items"])
    cur = b1["nextCursor"]
    assert cur is not None and cur.startswith(_CURSOR_V2)
    # 游标载荷 s 必须是 callback；第二页仍只带显式 sourceKind=callback
    tenth = filtered[9]
    assert cur == _encode_esrc2(tenth["created_at"], tenth["id"], "callback")

    p2 = client.get(
        _page_url(pid),
        params={"sourceKind": "callback", "cursor": cur},
    )
    assert p2.status_code == 200, p2.text
    _assert_no_store(p2)
    b2 = p2.json()
    _assert_page_shape(b2)
    ids2 = [it["revisionId"] for it in b2["items"]]
    assert ids2 == all_ids[10:15]
    assert set(ids1).isdisjoint(set(ids2))
    assert ids1 + ids2 == all_ids
    assert all(it["sourceKind"] == "callback" for it in b2["items"])
    # 不得混入噪声 task
    noise_ids = {r["id"] for r in _db_rev_rows(pid) if r["source_kind"] == "task"}
    assert set(ids1 + ids2).isdisjoint(noise_ids)
    assert b2["nextCursor"] is None


# ---------- 非法游标矩阵（筛选上下文） ----------


@pytest.mark.parametrize(
    "cursor,label",
    [
        ("", "blank"),
        ("   ", "whitespace"),
        ("x" * 193, "too_long"),
        ("esrc3_" + "a" * 20, "wrong_prefix"),
        ("esrc2_", "empty_body"),
        ("esrc2_!!!not-b64!!!", "bad_base64"),
        (
            "esrc2_"
            + base64.urlsafe_b64encode(b"not-json").decode().rstrip("="),
            "bad_json",
        ),
    ],
)
def test_invalid_esrc2_cursor_matrix(disabled_client, cursor: str, label: str):
    client = disabled_client
    pid = _create_project(client, name=f"坏esrc2-{label}")
    _seed_n_of_source(pid, 3, "task", tag_prefix=f"ic{label[:3]}_")
    res = client.get(
        _page_url(pid), params={"sourceKind": "task", "cursor": cursor}
    )
    _assert_fixed_error(
        res,
        400,
        _CODE_CURSOR_INVALID,
        forbid_echo=cursor if cursor.strip() else None,
    )
    assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID


def test_invalid_esrc2_payload_variants(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="坏esrc2载荷")
    rows = _seed_n_of_source(pid, 3, "task", tag_prefix="pl2_")
    good_id = rows[0]["id"]
    good_t = _datetime_to_us(rows[0]["created_at"])
    good_s = "task"

    variants: list[tuple[str, object]] = [
        ("extra_key", {"i": good_id, "s": good_s, "t": good_t, "x": 1}),
        ("missing_i", {"s": good_s, "t": good_t}),
        ("missing_s", {"i": good_id, "t": good_t}),
        ("missing_t", {"i": good_id, "s": good_s}),
        ("bool_t", {"i": good_id, "s": good_s, "t": True}),
        ("float_t", {"i": good_id, "s": good_s, "t": 1.5}),
        ("str_t", {"i": good_id, "s": good_s, "t": "123"}),
        ("neg_t", {"i": good_id, "s": good_s, "t": -1}),
        ("bad_id", {"i": "not_an_esr", "s": good_s, "t": good_t}),
        ("bad_s", {"i": good_id, "s": "not_a_kind", "t": good_t}),
        ("empty_s", {"i": good_id, "s": "", "t": good_t}),
        ("bool_s", {"i": good_id, "s": True, "t": good_t}),
        ("empty_keys", {}),
        # 仅 {i,t} 用 esrc2 前缀
        ("esrc1_payload", {"i": good_id, "t": good_t}),
    ]
    for label, payload in variants:
        cur = _encode_cursor_payload(payload, prefix=_CURSOR_V2)  # type: ignore[arg-type]
        res = client.get(
            _page_url(pid), params={"sourceKind": "task", "cursor": cur}
        )
        _assert_fixed_error(res, 400, _CODE_CURSOR_INVALID, forbid_echo=cur)
        assert res.json()["detail"]["message"] == _MSG_CURSOR_INVALID, label


# ---------- 项目 404 优先 / 跨域 ----------


def test_project_not_found_priority_and_cross_scope(disabled_client):
    client = disabled_client
    a = _create_project(client, name="筛选跨域A")
    b = _create_project(client, name="筛选跨域B")
    rows_a = _seed_n_of_source(a, 12, "task", tag_prefix="xa_")
    rows_b = _seed_n_of_source(b, 5, "task", tag_prefix="xb_")
    before_a = _domain_snapshot(a)
    before_b = _domain_snapshot(b)

    missing = client.get(
        _page_url("proj_missing_p12fd"),
        params={"sourceKind": "task"},
    )
    _assert_fixed_error(missing, 404, "project_not_found")
    assert "proj_missing_p12fd" not in missing.text

    # 不存在项目 + 非法来源：项目 404 优先
    missing_bad_src = client.get(
        _page_url("proj_missing_p12fd"),
        params={"sourceKind": "NOT_VALID"},
    )
    _assert_fixed_error(missing_bad_src, 404, "project_not_found")
    assert "NOT_VALID" not in missing_bad_src.text

    # 不存在项目 + 坏游标：项目 404 优先
    missing_bad_cur = client.get(
        _page_url("proj_missing_p12fd"),
        params={"sourceKind": "task", "cursor": "esrc2_bad"},
    )
    _assert_fixed_error(missing_bad_cur, 404, "project_not_found")

    # 跨项目：用 A 的 esrc2 查 B 同来源，不得读到 A
    p1a = client.get(_page_url(a), params={"sourceKind": "task"}).json()
    cur_a = p1a["nextCursor"]
    assert cur_a
    cross = client.get(
        _page_url(b),
        params={"sourceKind": "task", "cursor": cur_a},
    )
    assert cross.status_code == 200, cross.text
    cross_ids = {it["revisionId"] for it in cross.json()["items"]}
    assert cross_ids.isdisjoint({r["id"] for r in rows_a})
    for it in cross.json()["items"]:
        assert it["revisionId"] in {r["id"] for r in rows_b}

    _ensure_workspace(_WS_OTHER)
    other_pid = "proj_other_space_p12fd"
    db = SessionLocal()
    try:
        if db.get(Project, other_pid) is None:
            db.add(
                Project(
                    id=other_pid,
                    workspace_id=_WS_OTHER,
                    name="外空间项目P12FD",
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
        params={"sourceKind": "task"},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(cross_ws, 404, "project_not_found")
    assert rows_a[0]["id"] not in cross_ws.text

    other_ok = client.get(
        _page_url(other_pid),
        params={"sourceKind": "task"},
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert other_ok.status_code == 200, other_ok.text
    assert other_ok.json()["items"][0]["revisionId"] == other_rid

    assert _domain_snapshot(a) == before_a
    assert _domain_snapshot(b) == before_b


# ---------- SQL 五列 + source_kind 谓词 + LIMIT 11 ----------


def test_filter_sql_five_columns_source_predicate_limit_11(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="SQL筛选投影")
    _seed_n_of_source(pid, 15, "task", tag_prefix="sql_")
    _seed_n_of_source(
        pid,
        5,
        "revise",
        tag_prefix="sqln_",
        base_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low or "projects" in low:
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        p1 = client.get(_page_url(pid), params={"sourceKind": "task"})
        assert p1.status_code == 200, p1.text
        cur = p1.json()["nextCursor"]
        assert cur
        p2 = client.get(
            _page_url(pid),
            params={"sourceKind": "task", "cursor": cur},
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

    found_limit_11 = False
    found_source_pred = False
    found_keyset = False
    found_six_cols = False
    # P12F-H：page 元数据六键对应 SQL 六列（原五列 + display_name）
    _SIX_COLS = (
        "id",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
        "display_name",
    )

    def _param_list(parameters: object) -> list[object]:
        if isinstance(parameters, dict):
            return list(parameters.values())
        if isinstance(parameters, (list, tuple)):
            return list(parameters)
        return [parameters]

    def _assert_limit_exactly_11(sql_low: str, parameters: object) -> bool:
        """
        用途：精确证明 LIMIT 字面量或绑定值为 11；
          若存在 SQLite 自动 OFFSET ? 则只允许 0，并精确定位 LIMIT=11。
        """
        # 字面量 LIMIT 11（可选 OFFSET 0）
        if re.search(r"\blimit\s+11\b", sql_low):
            if re.search(r"\boffset\b", sql_low):
                # OFFSET 若不是字面量 0，则必须精确为占位符；占位符分支继续精确验证绑定值为 0
                if re.search(r"\boffset\s+0\b", sql_low):
                    pass  # 字面量 OFFSET 0 已精确匹配
                elif re.search(r"\boffset\s+\?", sql_low):
                    vals = _param_list(parameters)
                    # LIMIT 字面量 11 时 OFFSET ? 绑定必须精确为 0
                    assert vals, parameters
                    assert vals[-1] == 0, f"OFFSET 绑定非 0: {parameters}"
                else:
                    raise AssertionError(f"OFFSET 非字面量 0 且非占位符: {sql_low}")
            return True
        # 绑定 LIMIT ? [=11]
        if re.search(r"\blimit\s+\?", sql_low):
            vals = _param_list(parameters)
            assert vals, f"LIMIT ? 无绑定参数: {sql_low}"
            if re.search(r"\boffset\s+\?", sql_low):
                # 典型顺序 ... LIMIT ? OFFSET ? → 末两参数为 11, 0
                assert len(vals) >= 2, parameters
                assert vals[-2] == 11, f"LIMIT 绑定非 11: {parameters}"
                assert vals[-1] == 0, f"OFFSET 绑定非 0: {parameters}"
                return True
            if re.search(r"\boffset\s+0\b", sql_low):
                assert vals[-1] == 11, f"LIMIT 绑定非 11: {parameters}"
                return True
            if re.search(r"\boffset\b", sql_low):
                raise AssertionError(f"存在非零/未定位 OFFSET: {sql_low} {parameters}")
            # 仅 LIMIT ?：末参数精确 11（不得因参数列表别处偶然含 11 通过）
            assert vals[-1] == 11, f"LIMIT 绑定非 11: {parameters}"
            return True
        return False

    def _assert_keyset_predicate(sql_low: str) -> bool:
        """
        用途：精确证明键集谓词含 created_at <、created_at =、id < 及组合结构。
        """
        has_created_lt = (
            re.search(r"\bcreated_at\s*<", sql_low) is not None
            or re.search(r"\bcreated_at\s*<\s*\?", sql_low) is not None
        )
        has_created_eq = (
            re.search(r"\bcreated_at\s*=", sql_low) is not None
            or re.search(r"\bcreated_at\s*=\s*\?", sql_low) is not None
        )
        has_id_lt = (
            re.search(r"\bid\s*<", sql_low) is not None
            or re.search(r"\.\s*id\s*<", sql_low) is not None
        )
        # 组合：created_at < ... OR (created_at = ... AND id < ...)
        has_or = re.search(r"\bor\b", sql_low) is not None
        has_and = re.search(r"\band\b", sql_low) is not None
        if not (has_created_lt and has_created_eq and has_id_lt and has_or and has_and):
            return False
        # 结构：lt 分支与 eq+id 并列（拒绝任意 < 放行）
        structural = re.search(
            r"created_at\s*<[\s\S]{0,200}?\bor\b[\s\S]{0,200}?"
            r"created_at\s*=[\s\S]{0,120}?\band\b[\s\S]{0,80}?\bid\s*<",
            sql_low,
        )
        return structural is not None

    for sql, params in rev_selects:
        compact = " ".join(sql.split())
        low = compact.lower()
        assert "snapshot_json" not in low, sql
        assert "count(" not in low
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None
        select_list = match.group(1).strip()
        # 精确六列投影：id/state_version/snapshot_bytes/source_kind/created_at/display_name
        raw_parts = [p.strip() for p in select_list.split(",")]
        assert len(raw_parts) == 6, (raw_parts, sql)
        normalized_cols: list[str] = []
        for part in raw_parts:
            col = part.lower()
            if " as " in col:
                col = col.split(" as ", 1)[0].strip()
            if "." in col:
                col = col.rsplit(".", 1)[-1].strip()
            # 去掉可能的引号
            col = col.strip("`\"[]")
            normalized_cols.append(col)
        assert normalized_cols == list(_SIX_COLS), (normalized_cols, sql)
        found_six_cols = True

        # 来源谓词：必须精确 source_kind =（禁用 IS）；绑定含且仅使用显式 task
        if re.search(r"\bsource_kind\s*=", low) is not None:
            assert re.search(r"\bsource_kind\s+is\b", low) is None, sql
            param_vals = _param_list(params)
            # 来源筛选值必须出现且不得出现其他权威九类来源字面量
            assert "task" in param_vals, f"来源绑定缺 task: {params}"
            other_kinds = [k for k in _NINE_SOURCES if k != "task"]
            leaked = [k for k in other_kinds if k in param_vals]
            assert leaked == [], f"来源绑定混入其他 kind: {leaked} params={params}"
            found_source_pred = True

        if re.search(r"\blimit\b", low):
            if _assert_limit_exactly_11(low, params):
                found_limit_11 = True

        if _assert_keyset_predicate(low):
            found_keyset = True

    assert found_six_cols, f"未证明精确六列投影: {rev_selects}"
    assert found_limit_11, f"未证明精确 LIMIT 11: {rev_selects}"
    assert found_source_pred, f"未发现 source_kind = 且仅绑定 task: {rev_selects}"
    assert found_keyset, f"未发现精确键集谓词(created_at</= 与 id< 组合): {rev_selects}"


# ---------- lookahead 损坏 ----------


def test_filter_lookahead_corrupt_fails_whole_page(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="筛选lookahead损坏")
    ordered = _seed_n_of_source(pid, 12, "task", tag_prefix="cr_")
    # 插入噪声，lookahead 仍是第 11 条 task
    _seed_n_of_source(
        pid,
        3,
        "revise",
        tag_prefix="crn_",
        base_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    filtered = _filtered_ordered(_db_rev_rows(pid), "task")
    bad_id = filtered[10]["id"]
    _raw_sql_update_revision(bad_id, state_version="not_a_valid_esv")

    res = client.get(_page_url(pid), params={"sourceKind": "task"})
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    body = res.json()
    assert set(body.keys()) == {"detail"}
    assert body["detail"]["message"] == "修订记录数据损坏，无法读取"
    assert "items" not in res.text
    assert "nextCursor" not in res.text
    assert "revisionId" not in res.text
    assert bad_id not in res.text
    assert _SECRET not in res.text


# ---------- 兼容：旧页无筛选 / 旧列表 / 未知参数 ----------


def test_old_list_ignores_source_kind_and_stays_items_only(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="旧列表忽略筛选")
    ordered = _seed_mixed_revisions(
        pid,
        [(sk, f"o{i}") for i, sk in enumerate(_NINE_SOURCES)]
        + [("task", "extra")] * 6,
    )
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

    # 旧列表带 sourceKind 仍忽略，顶层仅 items
    tampered = client.get(
        _list_url(pid),
        params={
            "sourceKind": "task",
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


def test_page_unknown_params_still_ignored_with_and_without_filter(
    disabled_client,
):
    client = disabled_client
    pid = _create_project(client, name="未知参数兼容")
    _seed_n_of_source(pid, 15, "task", tag_prefix="uq_")
    _seed_n_of_source(
        pid,
        5,
        "revise",
        tag_prefix="uqn_",
        base_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )

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
            "order": "asc",
            "total": 1,
            "hasMore": "true",
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == baseline

    baseline_f = client.get(
        _page_url(pid), params={"sourceKind": "task"}
    ).json()
    tampered_f = client.get(
        _page_url(pid),
        params={
            "sourceKind": "task",
            "limit": 1,
            "offset": 3,
            "page": 2,
            "source": "revise",
            "search": _SECRET,
            "q": "正文",
            "order": "asc",
            "total": 1,
            "hasMore": "true",
        },
    )
    assert tampered_f.status_code == 200, tampered_f.text
    assert tampered_f.json() == baseline_f
    assert all(it["sourceKind"] == "task" for it in baseline_f["items"])


# ---------- 五域零写 + AST ----------


def test_filter_get_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="筛选五域零写")
    _seed_n_of_source(pid, 12, "task", tag_prefix="zw_")
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text

    before = _domain_snapshot(pid)
    p1 = client.get(_page_url(pid), params={"sourceKind": "task"})
    assert p1.status_code == 200, p1.text
    assert _domain_snapshot(pid) == before

    cur = p1.json()["nextCursor"]
    p2 = client.get(
        _page_url(pid), params={"sourceKind": "task", "cursor": cur}
    )
    assert p2.status_code == 200, p2.text
    assert _domain_snapshot(pid) == before

    bad_src = client.get(
        _page_url(pid), params={"sourceKind": "BAD_KIND"}
    )
    _assert_fixed_error(bad_src, 400, _CODE_SOURCE_INVALID)
    assert _domain_snapshot(pid) == before

    bad_cur = client.get(
        _page_url(pid),
        params={"sourceKind": "task", "cursor": "esrc2_bad"},
    )
    _assert_fixed_error(bad_cur, 400, _CODE_CURSOR_INVALID)
    assert _domain_snapshot(pid) == before


def test_service_api_no_write_ops_ast_and_source_alias():
    """用途：service/api 无写路径；list page 的 Annotated Query 别名精确为 sourceKind。"""
    api_src = _API_PATH.read_text(encoding="utf-8")
    api_tree = ast.parse(api_src)

    # AST 证明 list_editor_state_revisions_page 形参 source_kind 的元数据
    # 确为 Annotated[..., Query(alias="sourceKind")]；仅变量名 source_kind 不得通过
    page_fn: ast.FunctionDef | None = None
    for node in api_tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == (
            "list_editor_state_revisions_page"
        ):
            page_fn = node
            break
    assert page_fn is not None, "缺少 list_editor_state_revisions_page"
    source_arg: ast.arg | None = None
    for arg in page_fn.args.args:
        if arg.arg == "source_kind":
            source_arg = arg
            break
    assert source_arg is not None, "缺少形参 source_kind"
    ann = source_arg.annotation
    assert ann is not None, "source_kind 缺少注解"
    assert isinstance(ann, ast.Subscript), type(ann)
    assert isinstance(ann.value, ast.Name) and ann.value.id == "Annotated"
    slice_node = ann.slice
    assert isinstance(slice_node, ast.Tuple), type(slice_node)
    found_query_alias = False
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
            assert kw.value.value == "sourceKind", kw.value.value
            found_query_alias = True
    assert found_query_alias, (
        "list_editor_state_revisions_page 必须为 "
        'Annotated[..., Query(alias="sourceKind")]'
    )

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


def test_empty_filter_result_still_ok(disabled_client):
    """用途：项目有修订但筛选无命中 → 空 items + null nextCursor。"""
    client = disabled_client
    pid = _create_project(client, name="筛选空结果")
    _seed_n_of_source(pid, 5, "task", tag_prefix="empty_")
    res = client.get(_page_url(pid), params={"sourceKind": "revise"})
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_page_shape(body)
    assert body["items"] == []
    assert body["nextCursor"] is None
