"""
模块：P12I 检查点名称与可见内容显式搜索后端专项测试
用途：真实 HTTP+SQLite 验收 POST .../editor-state-checkpoints/search
  的精确 body、八列投影、LIMIT20、完整校验后匹配、NFKC+casefold、
  并集去重、坏行/预算 corrupt、作用域/权限/CSRF、五域零写与 list 兼容。
对接：docs/p12i-checkpoint-search-contract.md；
  editor_state_checkpoint_service；api.editor_state_checkpoints。
二次开发：
  - 禁止 mock 路由返回、宽泛状态码、恒真断言、固定 sleep、吞异常、skip/xfail；
  - 红测必须证明业务语义缺失，而非收集/导入/语法/环境失败；
  - 成功/失败均断言数据库与五域零副作用，不得只断言 HTTP。
"""

from __future__ import annotations

import ast
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
    ProjectTaskRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12i"
_SECRET = "SECRET_P12I_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints/search"

_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS = frozenset(
    {
        "checkpointId",
        "stateVersion",
        "snapshotBytes",
        "outlineNodeCount",
        "chapterCount",
        "createdAt",
        "displayName",
        "isPinned",
    }
)
_SEARCH_TOP = frozenset({"items"})

_CODE_QUERY_INVALID = "editor_state_checkpoint_search_query_invalid"
_MSG_QUERY_INVALID = "检查点搜索关键词无效"
_CODE_REQUEST_INVALID = "editor_state_checkpoint_search_request_invalid"
_MSG_REQUEST_INVALID = "检查点搜索请求无效"
_CODE_CORRUPT = "editor_state_checkpoint_corrupt"
_MSG_CORRUPT = "检查点数据损坏，无法读取"
_CODE_PROJECT = "project_not_found"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_CODE_CSRF_INVALID = "csrf_invalid"

_OWNER_USER = "admin_p12i_owner"
_OWNER_PASS = "TestPass-P12I-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12I-Writer-0001!",
    "finance": "TestPass-P12I-Finance-0001!",
    "hr": "TestPass-P12I-Hr-0001!",
    "bidder": "TestPass-P12I-Bidder-0001!",
}

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_checkpoints.py"
)

_ALLOW_MARKERS = {
    "outline_title": "ALW_OUT_TITLE_α",
    "outline_desc": "ALW_OUT_DESC_β",
    "outline_child_title": "ALW_OUT_CHILD_TITLE_γ",
    "chapter_title": "ALW_CH_TITLE_δ",
    "chapter_preview": "ALW_CH_PREVIEW_ε",
    "chapter_body": "ALW_CH_BODY_ζ",
    "parsed_md": "ALW_PARSED_MD_η",
    "bq_req": "ALW_BQ_REQ_θ",
    "bq_resp": "ALW_BQ_RESP_ι",
    "bq_evid": "ALW_BQ_EVID_κ",
    "btoc_title": "ALW_BTOC_TITLE_λ",
    "btoc_cat": "ALW_BTOC_CAT_μ",
    "btoc_note": "ALW_BTOC_NOTE_ν",
    "bquote_name": "ALW_BQN_NAME_ξ",
    "bquote_unit": "ALW_BQN_UNIT_ο",
    "bquote_qty": "ALW_BQN_QTY_π",
    "bquote_price": "ALW_BQN_PRICE_ρ",
    "bquote_amount": "ALW_BQN_AMT_σ",
    "bquote_remark": "ALW_BQN_RMK_τ",
    "bquote_notes": "ALW_BQN_NOTES_υ",
    "bcommit_title": "ALW_BCM_TITLE_φ",
    "bcommit_body": "ALW_BCM_BODY_χ",
}

_FORBID_MARKERS = {
    "mode": "FBD_MODE_MARKER_05",
    "facts": "FBD_FACTS_MARKER_06",
    "analysis": "FBD_ANALYSIS_MARKER_07",
    "analysis_overview": "FBD_ANAL_OV_MARKER_08",
    "response_matrix": "FBD_MATRIX_MARKER_09",
    "guidance": "FBD_GUIDANCE_MARKER_10",
    "outline_id": "FBD_OUT_ID_MARKER_11",
    "chapter_id": "FBD_CH_ID_MARKER_12",
    "unknown_nested": "FBD_UNKNOWN_NEST_13",
    "state_version": "FBD_STATE_VERSION_02",
}


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


@pytest.fixture
def required_settings(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "required")
    monkeypatch.setenv("AUTH_COOKIE_SECURE", "false")
    monkeypatch.setenv("AUTH_SESSION_TTL_HOURS", "24")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def required_client(required_settings):
    with TestClient(app) as client:
        yield client


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints/search"


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _create_project(
    client: TestClient,
    name: str = "P12I项目",
    kind: str = "technical",
    *,
    headers: dict | None = None,
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind},
        headers=headers or {},
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
                "preview": f"预览{tag}",
                "body": f"正文{tag}-{_SECRET}",
            }
        ],
        parsedMarkdown=f"md-{tag}-{_SECRET}",
        guidance=f"指引-{tag}",
    )


def _plain_state(tag: str) -> dict:
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"普通章节{tag}",
                "preview": f"普通预览{tag}",
                "body": f"普通正文{tag}",
            }
        ],
        parsedMarkdown=f"普通md-{tag}",
    )


def _full_allow_state(tag: str = "full") -> dict:
    m = {k: f"{v}_{tag}" for k, v in _ALLOW_MARKERS.items()}
    return _state_with_version(
        outline={
            "id": "out_root",
            "title": m["outline_title"],
            "description": m["outline_desc"],
            "children": [
                {
                    "id": "out_child",
                    "title": m["outline_child_title"],
                    "description": "child-desc-plain",
                    "children": [],
                }
            ],
        },
        chapters=[
            {
                "id": "ch_allow",
                "title": m["chapter_title"],
                "preview": m["chapter_preview"],
                "body": m["chapter_body"],
                "status": "pending",
            }
        ],
        parsedMarkdown=m["parsed_md"],
        businessQualify=[
            {
                "id": "bq1",
                "requirement": m["bq_req"],
                "response": m["bq_resp"],
                "evidence": m["bq_evid"],
            }
        ],
        businessToc=[
            {
                "id": "toc1",
                "title": m["btoc_title"],
                "category": m["btoc_cat"],
                "note": m["btoc_note"],
            }
        ],
        businessQuote={
            "rows": [
                {
                    "id": "qr1",
                    "name": m["bquote_name"],
                    "unit": m["bquote_unit"],
                    "quantity": m["bquote_qty"],
                    "unitPrice": m["bquote_price"],
                    "amount": m["bquote_amount"],
                    "remark": m["bquote_remark"],
                }
            ],
            "notes": m["bquote_notes"],
        },
        businessCommit=[
            {
                "id": "cm1",
                "title": m["bcommit_title"],
                "body": m["bcommit_body"],
            }
        ],
    )


def _forbid_only_state(tag: str = "fbd") -> dict:
    f = {
        k: f"{v}_{tag}" if isinstance(v, str) else v
        for k, v in _FORBID_MARKERS.items()
    }
    return _state_with_version(
        mode=f["mode"] if isinstance(f["mode"], str) else "ALIGNED",
        outline={
            "id": f["outline_id"],
            "title": "普通标题无禁止标记",
            "description": "普通描述",
            "secretPath": f["unknown_nested"],
            "children": [],
        },
        chapters=[
            {
                "id": f["chapter_id"],
                "title": "章节无禁止",
                "preview": "预览无禁止",
                "body": "正文无禁止",
                "stateVersion": f["state_version"],
            }
        ],
        facts=[{"id": "fx", "text": f["facts"]}],
        analysis={
            "overview": f["analysis"],
            "techRequirements": [f["analysis"]],
            "rejectionRisks": [],
            "scoringPoints": [],
        },
        analysisOverview=f["analysis_overview"],
        responseMatrix=[
            {
                "id": "rm1",
                "requirement": f["response_matrix"],
                "outlineNodeIds": [f["outline_id"]],
                "chapterIds": [f["chapter_id"]],
            }
        ],
        guidance=f["guidance"],
        parsedMarkdown="普通 markdown 无禁止标记",
    )


def _count_outline(outline) -> int:
    if outline is None:
        return 0
    count = 0
    stack: list = []
    if isinstance(outline, list):
        stack.extend(outline)
    elif isinstance(outline, dict):
        stack.append(outline)
    else:
        return 0
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            count += 1
            children = node.get("children")
            if isinstance(children, list):
                stack.extend(children)
    return count


def _insert_raw_checkpoint(
    *,
    project_id: str,
    checkpoint_id: str,
    snapshot_json: str,
    state_version: str,
    snapshot_bytes: int,
    outline_node_count: int = 0,
    chapter_count: int = 0,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
    display_name: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            EditorStateCheckpointRow(
                id=checkpoint_id,
                workspace_id=workspace_id,
                project_id=project_id,
                snapshot_json=snapshot_json,
                state_version=state_version,
                snapshot_bytes=snapshot_bytes,
                outline_node_count=outline_node_count,
                chapter_count=chapter_count,
                created_at=created_at or utc_now(),
                display_name=display_name,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_checkpoint(
    project_id: str,
    state: dict,
    *,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
    checkpoint_id: str | None = None,
    display_name: str | None = None,
) -> dict:
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    cid = checkpoint_id or ("escp_" + secrets.token_hex(16))
    ver = state["stateVersion"]
    nbytes = len(snap_json.encode("utf-8"))
    outline_n = _count_outline(snap.get("outline"))
    chapters = snap.get("chapters")
    chapter_n = (
        sum(1 for c in chapters if isinstance(c, dict))
        if isinstance(chapters, list)
        else 0
    )
    _insert_raw_checkpoint(
        project_id=project_id,
        checkpoint_id=cid,
        snapshot_json=snap_json,
        state_version=ver,
        snapshot_bytes=nbytes,
        outline_node_count=outline_n,
        chapter_count=chapter_n,
        created_at=created_at,
        workspace_id=workspace_id,
        display_name=display_name,
    )
    return {
        "id": cid,
        "state_version": ver,
        "snapshot_bytes": nbytes,
        "outline_node_count": outline_n,
        "chapter_count": chapter_n,
        "display_name": display_name,
        "snapshot_json": snap_json,
    }


def _raw_sql_update_checkpoint(checkpoint_id: str, **fields: object) -> None:
    set_parts = []
    params: dict[str, object] = {"cid": checkpoint_id}
    for key, value in fields.items():
        set_parts.append(f"{key} = :{key}")
        params[key] = value
    sql = (
        "UPDATE editor_state_checkpoints SET "
        + ", ".join(set_parts)
        + " WHERE id = :cid"
    )
    with engine.begin() as conn:
        conn.execute(text(sql), params)


def _search(
    client: TestClient,
    project_id: str,
    body: object,
    *,
    headers: dict | None = None,
    params: dict | None = None,
):
    return client.post(
        _search_url(project_id),
        json=body,
        headers=headers or {},
        params=params,
    )


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(
    res,
    status: int,
    code: str,
    *,
    message: str | None = None,
    forbid_echo: str | None = None,
) -> None:
    assert res.status_code == status, res.text
    _assert_no_store(res)
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert "code" in detail and "message" in detail
    assert detail["code"] == code
    if message is not None:
        assert detail["message"] == message
    blob = res.text
    assert _SECRET not in blob
    assert _PATH_MARKER not in blob
    if forbid_echo:
        assert forbid_echo not in blob


def _assert_search_ok(res) -> dict:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _SEARCH_TOP
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 20
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert _CHECKPOINT_ID_RE.match(item["checkpointId"])
        assert _STATE_VERSION_RE.match(item["stateVersion"])
        assert type(item["snapshotBytes"]) is int
        assert item["snapshotBytes"] == abs(item["snapshotBytes"])
        assert type(item["outlineNodeCount"]) is int
        assert type(item["chapterCount"]) is int
        assert isinstance(item["createdAt"], str) and item["createdAt"]
        assert item["displayName"] is None or isinstance(item["displayName"], str)
        assert type(item["isPinned"]) is bool
        assert "snapshot" not in item
        assert _SECRET not in json.dumps(item, ensure_ascii=False)
    assert "snippet" not in body
    assert "matchedFields" not in body
    assert "query" not in body
    assert "score" not in body
    return body


def _domain_snapshot(project_id: str) -> dict:
    db = SessionLocal()
    try:
        cps = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
            .all()
        )
        revs = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == project_id)
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
        editor = db.get(ProjectEditorStateRow, project_id)
        proj = db.get(Project, project_id)
        tasks = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .order_by(ProjectTaskRow.id.desc())
            .all()
        )
        audits = (
            db.query(AuthAuditEventRow)
            .order_by(AuthAuditEventRow.id.desc())
            .limit(50)
            .all()
        )
        return {
            "cps": [
                (
                    r.id,
                    r.state_version,
                    r.snapshot_bytes,
                    r.display_name,
                    r.snapshot_json,
                    r.created_at.isoformat()
                    if hasattr(r.created_at, "isoformat")
                    else str(r.created_at),
                )
                for r in cps
            ],
            "revs": [
                (r.id, r.state_version, r.snapshot_bytes, r.source_kind) for r in revs
            ],
            "editor": None
            if editor is None
            else (
                editor.project_id,
                editor.parsed_markdown,
                editor.mode,
                editor.analysis_overview,
                editor.updated_at.isoformat()
                if editor.updated_at is not None
                and hasattr(editor.updated_at, "isoformat")
                else editor.updated_at,
            ),
            "project": None
            if proj is None
            else (
                proj.id,
                proj.name,
                proj.workspace_id,
                proj.updated_at.isoformat()
                if hasattr(proj.updated_at, "isoformat")
                else str(proj.updated_at),
            ),
            "tasks": [(t.id, t.status, t.progress, t.payload_json) for t in tasks],
            "audits": [(a.id, a.action, a.result, a.target) for a in audits],
        }
    finally:
        db.close()


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER) -> None:
    db = SessionLocal()
    try:
        if auth_service.is_bootstrapped(db):
            return None
        return auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_OWNER_USER,
            password=_OWNER_PASS,
            role=role,
        )
    finally:
        db.close()


def _login(client: TestClient, username: str, password: str) -> str:
    client.cookies.clear()
    res = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert res.status_code == 200, res.text
    csrf = res.json().get("csrfToken") or client.cookies.get("csrf_token")
    assert csrf
    return csrf


def _create_member(client, csrf, *, username, password, role) -> None:
    res = client.post(
        "/api/auth/members",
        headers={"X-CSRF-Token": csrf},
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": False,
        },
    )
    assert res.status_code == 201, res.text


def _login_role(client: TestClient, role: str) -> str:
    _bootstrap()
    owner_csrf = _login(client, _OWNER_USER, _OWNER_PASS)
    uname = f"user_p12i_{role}_{secrets.token_hex(3)}"
    pwd = _ROLE_PASSWORDS[role]
    _create_member(client, owner_csrf, username=uname, password=pwd, role=role)
    return _login(client, uname, pwd)


def test_search_route_exists_exact_success(disabled_client):
    """用途：合法 POST search 精确 200 + 七键。"""
    client = disabled_client
    pid = _create_project(client, name="P12I路由存在")
    row = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c1", "title": "P12I_ROUTE_HIT", "preview": "p", "body": "b"}
            ]
        ),
        created_at=datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc),
    )
    body = _assert_search_ok(_search(client, pid, {"query": "P12I_ROUTE_HIT"}))
    assert [it["checkpointId"] for it in body["items"]] == [row["id"]]


def test_name_only_hit_returns_exact_single_meta(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I名称唯一")
    name_only = "P12I_NAME_ONLY_HIT_α"
    content_marker = "P12I_CONTENT_OTHER_β"
    row_name = _seed_checkpoint(
        pid,
        _plain_state("n1"),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        checkpoint_id="escp_" + "a" * 32,
        display_name=name_only,
    )
    row_content = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": content_marker,
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
        checkpoint_id="escp_" + "b" * 32,
    )
    _seed_checkpoint(
        pid,
        _plain_state("n3"),
        created_at=datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc),
        checkpoint_id="escp_" + "c" * 32,
    )
    before = _domain_snapshot(pid)
    body = _assert_search_ok(_search(client, pid, {"query": name_only}))
    ids = [it["checkpointId"] for it in body["items"]]
    assert ids == [row_name["id"]], f"名称唯一命中失败: {ids}"
    assert body["items"][0]["displayName"] == name_only
    assert row_content["id"] not in ids
    assert _domain_snapshot(pid) == before


def test_content_only_and_union_dedup_order(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I并集去重")
    shared = "P12I_UNION_SHARED"
    base = datetime(2026, 7, 18, 8, 0, 0, tzinfo=timezone.utc)
    r0 = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c", "title": f"t-{shared}", "preview": "p", "body": "b"}
            ]
        ),
        created_at=base + timedelta(seconds=0),
        checkpoint_id="escp_" + "d0" + "0" * 30,
    )
    r1 = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c", "title": f"t-{shared}", "preview": "p", "body": "b"}
            ]
        ),
        created_at=base + timedelta(seconds=1),
        checkpoint_id="escp_" + "d1" + "0" * 30,
        display_name=f"名-{shared}",
    )
    r2 = _seed_checkpoint(
        pid,
        _plain_state("onlyname"),
        created_at=base + timedelta(seconds=2),
        checkpoint_id="escp_" + "d2" + "0" * 30,
        display_name=f"独-{shared}",
    )
    body = _assert_search_ok(_search(client, pid, {"query": shared}))
    ids = [it["checkpointId"] for it in body["items"]]
    assert ids == [r2["id"], r1["id"], r0["id"]], ids
    assert len(ids) == 3


def test_nfkc_casefold_literal_and_empty(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12INFKC")
    fw = "１２３"
    _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {"id": "c", "title": f"Cafe{fw}", "preview": "p", "body": "b"}
            ]
        ),
        created_at=datetime(2026, 7, 18, 9, 0, 0, tzinfo=timezone.utc),
    )
    assert len(_assert_search_ok(_search(client, pid, {"query": "cafe"}))["items"]) == 1
    assert len(_assert_search_ok(_search(client, pid, {"query": "123"}))["items"]) == 1
    empty = _assert_search_ok(_search(client, pid, {"query": "NOMATCH_XYZ_99"}))
    assert empty["items"] == []


def test_allow_and_forbid_fields(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I白名单")
    allow = _seed_checkpoint(
        pid,
        _full_allow_state("a1"),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
    )
    _seed_checkpoint(
        pid,
        _forbid_only_state("f1"),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
    )
    for key, base in _ALLOW_MARKERS.items():
        marker = f"{base}_a1"
        body = _assert_search_ok(_search(client, pid, {"query": marker}))
        ids = [it["checkpointId"] for it in body["items"]]
        assert ids == [allow["id"]], f"允许字段 {key} 未命中: {ids}"
    for key, base in _FORBID_MARKERS.items():
        if not isinstance(base, str):
            continue
        marker = f"{base}_f1"
        body = _assert_search_ok(_search(client, pid, {"query": marker}))
        assert body["items"] == [], f"禁止字段 {key} 不应命中"


def test_candidate_window_20_skips_21st(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I候选20")
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(21):
        mark = f"P12I_WIN_{i:02d}"
        rows.append(
            _seed_checkpoint(
                pid,
                _state_with_version(
                    chapters=[
                        {
                            "id": f"c{i}",
                            "title": mark,
                            "preview": "p",
                            "body": "b",
                        }
                    ]
                ),
                created_at=base + timedelta(seconds=i),
                checkpoint_id="escp_" + f"{i:02d}" + "0" * 30,
            )
        )
    hit20 = _assert_search_ok(_search(client, pid, {"query": "P12I_WIN_20"}))
    assert [it["checkpointId"] for it in hit20["items"]] == [rows[20]["id"]]
    miss0 = _assert_search_ok(_search(client, pid, {"query": "P12I_WIN_00"}))
    assert miss0["items"] == []


def test_corrupt_candidate_fails_whole_search(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I坏行")
    good = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": "P12I_GOOD_MARK",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        display_name="好名称命中",
    )
    bad = _seed_checkpoint(
        pid,
        _plain_state("bad"),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
        display_name="坏行也会先校验",
    )
    _raw_sql_update_checkpoint(bad["id"], snapshot_bytes=1)
    before = _domain_snapshot(pid)
    res = _search(client, pid, {"query": "坏行也会先校验"})
    _assert_fixed_error(res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    res2 = _search(client, pid, {"query": "P12I_GOOD_MARK"})
    _assert_fixed_error(res2, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert good["id"]
    assert _domain_snapshot(pid) == before


def test_search_corrupt_is_pinned_non_hit_candidate_fixed_500_zero_write(
    disabled_client,
):
    """
    用途：未命中候选 is_pinned=2 仍整次 corrupt；禁止跳过未命中行校验；五域零写。
    """
    client = disabled_client
    pid = _create_project(client, name="P12I坏固定未命中")
    good = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": "P12I_PIN_GOOD",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        display_name="好名称命中固定",
    )
    bad = _seed_checkpoint(
        pid,
        _plain_state("bad_pin"),
        created_at=datetime(2026, 7, 18, 11, 0, 0, tzinfo=timezone.utc),
        display_name="未命中也要先校验固定",
    )
    # 绕过 CHECK 写原始非法 is_pinned=2
    with engine.connect() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        try:
            conn.execute(
                text(
                    "UPDATE editor_state_checkpoints SET is_pinned = 2 WHERE id = :id"
                ),
                {"id": bad["id"]},
            )
            conn.commit()
        finally:
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))
    before = _domain_snapshot(pid)
    # 关键词只命中 good 名称，但 bad 未命中候选也必须先校验
    res = _search(client, pid, {"query": "好名称命中固定"})
    _assert_fixed_error(res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert bad["id"] not in res.text
    assert good["id"] not in res.text
    assert _SECRET not in res.text
    assert _domain_snapshot(pid) == before


def test_object_budget_exceed_corrupt(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I对象预算")
    chapters = [
        {"id": f"oid{i}", "title": "t", "preview": "p", "body": "b"}
        for i in range(4097)
    ]
    _seed_checkpoint(
        pid,
        _state_with_version(chapters=chapters),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
    )
    before = _domain_snapshot(pid)
    res = _search(client, pid, {"query": "t"})
    _assert_fixed_error(res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert _domain_snapshot(pid) == before


def test_string_leaf_budget_8193_exceed_corrupt(disabled_client):
    """用途：字符串叶 8193 且对象数≤4096 时真实预算超限 corrupt，不得只测对象预算。"""
    client = disabled_client
    pid = _create_project(client, name="P12I字符串叶预算")
    # 每章 title/preview/body 各 1 叶 → 2731*3=8193 叶；对象数 2731≤4096
    chapters = [
        {
            "id": f"sleaf{i}",
            "title": "SLEAF_MARK",
            "preview": "p",
            "body": "b",
        }
        for i in range(2731)
    ]
    _seed_checkpoint(
        pid,
        _state_with_version(chapters=chapters),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
    )
    before = _domain_snapshot(pid)
    res = _search(client, pid, {"query": "SLEAF_MARK"})
    _assert_fixed_error(res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert _domain_snapshot(pid) == before


def test_illegal_display_name_whole_search_corrupt(disabled_client):
    """用途：库内非法 display_name（控制/非NFKC/超长）即使名称或内容命中也整次 corrupt。"""
    client = disabled_client
    pid = _create_project(client, name="P12I非法名称")
    hit_name = "P12I_ILLEGAL_NAME_HIT"
    content_mark = "P12I_ILLEGAL_CONTENT_HIT"
    good = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "cgood",
                    "title": content_mark,
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 9, 0, 0, tzinfo=timezone.utc),
        display_name="合法名称",
    )
    bad = _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "cbad",
                    "title": "其它正文",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc),
        display_name="临时名",
    )
    # 真实 raw SQL 写入非法 display_name：含控制字符 + 会名称命中的子串
    _raw_sql_update_checkpoint(bad["id"], display_name=f"{hit_name}\x07x")
    before = _domain_snapshot(pid)
    # 名称侧命中路径
    res_name = _search(client, pid, {"query": hit_name})
    _assert_fixed_error(res_name, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    # 内容侧命中路径（坏行仍先完整校验）
    res_content = _search(client, pid, {"query": content_mark})
    _assert_fixed_error(res_content, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert good["id"]
    assert _domain_snapshot(pid) == before


def test_query_and_request_invalid_matrix(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I请求校验")
    _seed_checkpoint(
        pid,
        _plain_state("q"),
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    for bad in ["", "  a", "a  ", "a\nb", "a\tb", None, 1, True, [], {}]:
        res = _search(client, pid, {"query": bad})
        _assert_fixed_error(
            res, 400, _CODE_QUERY_INVALID, message=_MSG_QUERY_INVALID
        )

    res = _search(client, pid, {"query": "x" * 65})
    _assert_fixed_error(res, 400, _CODE_QUERY_INVALID, message=_MSG_QUERY_INVALID)

    for body in [
        {},
        {"query": "ok", "extra": 1},
        {"Query": "ok"},
        {"search": "ok"},
        [],
        "x",
    ]:
        if isinstance(body, (dict, list)):
            res = _search(client, pid, body)
        else:
            res = client.post(
                _search_url(pid),
                content=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        _assert_fixed_error(
            res, 422, _CODE_REQUEST_INVALID, message=_MSG_REQUEST_INVALID
        )

    res = client.post(
        _search_url(pid),
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    _assert_fixed_error(res, 422, _CODE_REQUEST_INVALID, message=_MSG_REQUEST_INVALID)

    res = _search(client, pid, {"query": "ok"}, params={"q": "x"})
    _assert_fixed_error(res, 422, _CODE_REQUEST_INVALID, message=_MSG_REQUEST_INVALID)

    big = json.dumps({"query": "a" * 1100}, ensure_ascii=False).encode("utf-8")
    assert len(big) > 1024
    res = client.post(
        _search_url(pid),
        content=big,
        headers={"Content-Type": "application/json"},
    )
    _assert_fixed_error(res, 422, _CODE_REQUEST_INVALID, message=_MSG_REQUEST_INVALID)


def test_error_priority_project_before_query(disabled_client):
    client = disabled_client
    res2 = _search(client, "proj_not_exist_p12i", {"query": "valid"})
    _assert_fixed_error(res2, 404, _CODE_PROJECT)
    pid = _create_project(client, name="优先级")
    res3 = _search(client, pid, {"query": ""})
    _assert_fixed_error(res3, 400, _CODE_QUERY_INVALID, message=_MSG_QUERY_INVALID)


def test_cross_workspace_project_no_leak(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I本空间")
    marker = "P12I_SCOPE_MARK"
    _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[{"id": "c", "title": marker, "preview": "p", "body": "b"}]
        ),
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    other_pid = "proj_other_p12i_" + secrets.token_hex(4)
    db = SessionLocal()
    try:
        if db.get(Workspace, _WS_OTHER) is None:
            db.add(
                Workspace(
                    id=_WS_OTHER,
                    name="其它",
                    owner_user_id="user_other_p12i",
                )
            )
        db.add(
            Project(
                id=other_pid,
                workspace_id=_WS_OTHER,
                name="其它项目",
                kind="technical",
                status="draft",
            )
        )
        db.commit()
    finally:
        db.close()
    _seed_checkpoint(
        other_pid,
        _state_with_version(
            chapters=[{"id": "c", "title": marker, "preview": "p", "body": "b"}]
        ),
        workspace_id=_WS_OTHER,
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    body = _assert_search_ok(_search(client, pid, {"query": marker}))
    assert len(body["items"]) == 1
    res = _search(client, other_pid, {"query": marker})
    _assert_fixed_error(res, 404, _CODE_PROJECT)


# 九列投影顺序与生产 select(...) 完全一致（含原始 is_pinned）；禁止“列名出现即可”
_SEARCH_SQL_COLS = (
    "id",
    "state_version",
    "snapshot_bytes",
    "outline_node_count",
    "chapter_count",
    "created_at",
    "display_name",
    "snapshot_json",
    "is_pinned",
)


def _normalize_select_projection(select_list: str) -> list[str]:
    """用途：解析 SELECT...FROM 投影列，按逗号深度切分并去别名/表前缀。"""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in select_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    normalized: list[str] = []
    for col in parts:
        col = re.sub(r"(?is)\s+as\s+[A-Za-z_][\w]*$", "", col).strip()
        if "." in col:
            col = col.rsplit(".", 1)[-1].strip()
        col = col.strip('`"[]')
        normalized.append(col)
    return normalized


def _param_list(params: object) -> list:
    if isinstance(params, dict):
        return list(params.values())
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params]


def test_search_sql_nine_columns_limit_20(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12ISQL")
    for i in range(3):
        _seed_checkpoint(
            pid,
            _variant(f"sql{i}"),
            created_at=datetime(2026, 7, 1, 12, 0, i, tzinfo=timezone.utc),
        )
    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_checkpoints" in low or (
            "projects" in low and "select" in low
        ):
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _search(client, pid, {"query": "章节"})
        assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    cp_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_checkpoints" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert len(cp_selects) == 1, f"checkpoint SELECT 次数异常: {len(cp_selects)}"
    sql, params = cp_selects[0]
    compact = " ".join(sql.split())
    low = compact.lower()
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
    assert match is not None, sql
    normalized = _normalize_select_projection(match.group(1).strip())
    assert normalized == list(_SEARCH_SQL_COLS), normalized
    assert "limit" in low
    assert "count(" not in low
    assert re.search(r"\blike\b", low) is None
    # 禁非零 OFFSET；SQLite 方言可能把 limit(20) 编译为 LIMIT ? OFFSET ? 且末参为 0
    assert re.search(r"\boffset\s+[1-9]", low) is None, sql
    assert "json_extract" not in low
    assert "json_each" not in low
    vals = _param_list(params)
    if re.search(r"\blimit\s+20\b", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-1] == 0, params
    elif re.search(r"\blimit\s+\?", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-2:] == [20, 0], params
        else:
            assert vals[-1] == 20, params
    else:
        raise AssertionError(f"未发现 LIMIT 20: {sql} {params}")
    assert re.search(r"\bworkspace_id\s*=", low)
    assert re.search(r"\bproject_id\s*=", low)
    assert re.search(
        r"order\s+by\s+.*created_at\s+desc.*,\s*.*id\s+desc", low
    ), sql


def test_required_bid_writer_csrf_and_role_gates(required_client):
    client = required_client
    writer_csrf = _login_role(client, "bid_writer")
    pid = _create_project(
        client,
        name="P12I权限",
        headers={"X-CSRF-Token": writer_csrf},
    )
    _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": "P12I_AUTH_HIT",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )

    def _assert_auth_deny(res, status: int, code: str) -> None:
        """用途：中间件 CSRF/角色固定 status + 原生 dict detail.code。"""
        assert res.status_code == status, res.text
        detail = res.json().get("detail")
        assert type(detail) is dict, res.text
        assert detail.get("code") == code
        assert type(detail.get("message")) is str and detail["message"] != ""

    # 缺 CSRF：精确 403 + csrf_invalid（原生 dict）
    res_no_csrf = _search(client, pid, {"query": "P12I_AUTH_HIT"})
    _assert_auth_deny(res_no_csrf, 403, _CODE_CSRF_INVALID)

    # 错误 CSRF：精确 403 + csrf_invalid
    res_bad_csrf = _search(
        client,
        pid,
        {"query": "P12I_AUTH_HIT"},
        headers={"X-CSRF-Token": "definitely-wrong-csrf-p12i"},
    )
    _assert_auth_deny(res_bad_csrf, 403, _CODE_CSRF_INVALID)

    # 合法 Cookie + CSRF 成功
    res_ok = _search(
        client,
        pid,
        {"query": "P12I_AUTH_HIT"},
        headers={"X-CSRF-Token": writer_csrf},
    )
    body = _assert_search_ok(res_ok)
    assert len(body["items"]) == 1

    # finance：精确 403 + role_forbidden 原生 dict
    client.cookies.clear()
    fin_csrf = _login_role(client, "finance")
    res_f = _search(
        client,
        pid,
        {"query": "P12I_AUTH_HIT"},
        headers={"X-CSRF-Token": fin_csrf},
    )
    _assert_auth_deny(res_f, 403, _CODE_ROLE_FORBIDDEN)


def test_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I零写")
    _seed_checkpoint(
        pid,
        _state_with_version(
            chapters=[
                {
                    "id": "c",
                    "title": "P12I_ZERO_WRITE",
                    "preview": "p",
                    "body": "b",
                }
            ]
        ),
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    db = SessionLocal()
    try:
        rid = "esr_" + secrets.token_hex(16)
        state = _plain_state("rev")
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        db.add(
            EditorStateRevisionRow(
                id=rid,
                workspace_id=_WS,
                project_id=pid,
                snapshot_json=snap_json,
                state_version=state["stateVersion"],
                snapshot_bytes=len(snap_json.encode("utf-8")),
                source_kind="task",
                created_at=utc_now(),
            )
        )
        db.add(
            ProjectTaskRow(
                id="task_p12i_" + secrets.token_hex(4),
                project_id=pid,
                type="parse",
                status="done",
                progress=100,
                message="ok",
                payload_json="{}",
                result_json="{}",
                error=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    before = _domain_snapshot(pid)
    body = _assert_search_ok(_search(client, pid, {"query": "P12I_ZERO_WRITE"}))
    assert len(body["items"]) == 1
    assert _domain_snapshot(pid) == before
    res = _search(client, pid, {"query": ""})
    _assert_fixed_error(res, 400, _CODE_QUERY_INVALID, message=_MSG_QUERY_INVALID)
    assert _domain_snapshot(pid) == before


def test_list_compat_unknown_search_q_ignored(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="P12I列表兼容")
    _seed_checkpoint(
        pid,
        _plain_state("lc"),
        created_at=datetime(2026, 7, 18, tzinfo=timezone.utc),
    )
    res = client.get(_list_url(pid), params={"search": "x", "q": "y"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"items"}
    assert len(body["items"]) == 1


def test_service_api_ast_bans():
    svc = _SERVICE_PATH.read_text(encoding="utf-8")
    api = _API_PATH.read_text(encoding="utf-8")
    assert "search_editor_state_checkpoints" in svc
    assert "editor-state-checkpoints/search" in api
    tree = ast.parse(svc)
    search_fn = None
    for node in tree.body:
        if (
            isinstance(node, ast.FunctionDef)
            and node.name == "search_editor_state_checkpoints"
        ):
            search_fn = node
            break
    assert search_fn is not None
    src_seg = ast.get_source_segment(svc, search_fn) or ""
    low = src_seg.lower()
    assert "commit(" not in low
    assert "flush(" not in low
    assert "rollback(" not in low
    assert ".like(" not in low
    assert "offset(" not in low
