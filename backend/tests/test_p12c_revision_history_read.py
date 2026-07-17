"""
模块：P12C-C1 editor-state 修订历史只读接口专项测试
用途：真实 HTTP+SQLite 验收列表元数据/详情、SQL 投影、三重作用域、
  损坏脱敏、完整只读零写、鉴权与未知查询参数忽略。
对接：GET .../editor-state-revisions[/{revisionId}]；
  editor_state_revision_history_service。
二次开发：禁止 mock SQLite、宽泛状态码、>=1、空集合假绿、跨项目冒充跨空间。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
    utc_now,
)
from app.services import auth_service, editor_state_revision_service, editor_state_service
from app.services.editor_state_revision_service import record_editor_state_transition

_WS = "ws_local"
_WS_OTHER = "ws_other_p12cc1"
_OWNER_USER = "admin_p12cc1_owner"
_OWNER_PASS = "TestPass-P12CC1-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12CC1-Writer-0001!",
    "finance": "TestPass-P12CC1-Finance-0001!",
    "hr": "TestPass-P12CC1-Hr-0001!",
    "bidder": "TestPass-P12CC1-Bidder-0001!",
}
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt"}
)
_DETAIL_KEYS = _META_KEYS | frozenset({"snapshot"})
_LIST_TOP = frozenset({"items"})
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_ALL_SOURCES = frozenset(editor_state_revision_service.REVISION_SOURCE_KINDS)
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_SECRET = "SECRET_P12CC1_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions"
_MAX_BYTES = 2 * 1024 * 1024
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


def _url(project_id: str, revision_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-revisions"
    if revision_id is None:
        return base
    return f"{base}/{revision_id}"


def _create_project(
    client: TestClient,
    name: str = "P12C-C1项目",
    *,
    headers: dict | None = None,
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": "technical"},
        headers=headers or {},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _put_state(
    client: TestClient,
    pid: str,
    *,
    tag: str,
    headers: dict | None = None,
) -> dict:
    res = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "facts": [{"id": f"f_{tag}", "text": f"事实-{tag}"}],
            "guidance": {"note": f"指引-{tag}"},
            "chapters": [
                {
                    "id": f"ch_{tag}",
                    "title": f"章节{tag}",
                    "body": f"正文-{tag}-{_SECRET}",
                    "status": "pending",
                    "preview": f"预览-{tag}",
                    "wordCount": 3,
                }
            ],
            "parsedMarkdown": f"# 标题-{tag}\n{_SECRET}",
        },
        headers=headers or {},
    )
    assert res.status_code == 200, res.text
    return res.json()


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
        chapters=[{"id": f"ch_{tag}", "title": f"章节{tag}", "content": f"正文{tag}"}],
        guidance=f"指引-{tag}",
        parsedMarkdown=f"md-{tag}-{_SECRET}",
    )


def _record(
    project_id: str,
    before: dict,
    after: dict,
    source: str,
    *,
    workspace_id: str = _WS,
) -> None:
    db = SessionLocal()
    try:
        record_editor_state_transition(
            db,
            workspace_id,
            project_id,
            before_state=before,
            after_state=after,
            source_kind=source,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _bootstrap(role: str = auth_service.ROLE_BID_WRITER):
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


def _login(client: TestClient, username: str, password: str):
    client.cookies.clear()
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def _owner_session(client: TestClient):
    _bootstrap()
    res = _login(client, _OWNER_USER, _OWNER_PASS)
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"], res.json()


def _create_member(client, csrf, *, username, password, role, is_owner=False):
    return client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )


def _login_role(client: TestClient, role: str, *, is_owner: bool = False) -> str:
    csrf, _ = _owner_session(client)
    username = f"user_{role}_p12cc1{'_own' if is_owner else ''}"
    created = _create_member(
        client,
        csrf,
        username=username,
        password=_ROLE_PASSWORDS[role],
        role=role,
        is_owner=is_owner,
    )
    assert created.status_code == 201, created.text
    res = _login(client, username, _ROLE_PASSWORDS[role])
    assert res.status_code == 200, res.text
    return res.json()["csrfToken"]


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(res, status: int, code: str) -> None:
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
    assert "editor_state_revisions" not in blob
    assert "editor_state_checkpoints" not in blob
    assert "editor_state_revision_history_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "JSONDecodeError" not in blob


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
    """用途：完整 revision 身份序列（created_at DESC, id DESC）。"""
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
                "created_at": r.created_at.isoformat()
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


def _domain_snapshot(project_id: str) -> dict:
    return {
        "revisions": _db_rev_rows(project_id),
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
    }


def _ensure_workspace(ws_id: str, name: str = "其他空间P12CC1") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12cc1",
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
    """
    用途：定点原始 SQL 更新 revision 行，复现 ORM 物化前的解码/CHECK 损坏。
    二次开发：ignore_check=True 时临时 PRAGMA ignore_check_constraints，
      更新后立即关闭，避免污染后续用例。
    """
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


def _raw_sql_delete_revision(revision_id: str) -> None:
    """用途：原始 SQL 删除指定 revision，避免 ORM 物化损坏行失败。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM editor_state_revisions WHERE id = :rid"),
            {"rid": revision_id},
        )


# ---------- 空列表 / 多来源列表 / 默认列表 10 / 写入保留 20 ----------


def test_p12f_a_list_limit_independent_of_write_retention():
    """
    用途：默认历史列表上限必须独立固定为 10，不得再绑定写入保留上限 20。
    """
    from app.services import editor_state_revision_history_service as hist

    assert hist.MAX_REVISIONS_LIST == 10
    assert editor_state_revision_service.MAX_REVISIONS_PER_PROJECT == 20
    assert hist.MAX_REVISIONS_LIST != editor_state_revision_service.MAX_REVISIONS_PER_PROJECT
    # 源码字面量 10，禁止继续引用写入服务常量
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert re.search(
        r"^MAX_REVISIONS_LIST\s*=\s*10\b",
        src,
        re.MULTILINE,
    ), "MAX_REVISIONS_LIST 必须字面量固定为 10"
    assert "MAX_REVISIONS_LIST = editor_state_revision_service.MAX_REVISIONS_PER_PROJECT" not in src


def test_empty_project_list_is_exact_empty_items(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="空修订列表")
    res = client.get(_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _LIST_TOP
    assert body["items"] == []


def test_multi_source_list_shape_order_and_max_ten(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="多来源列表")
    # 真实 browser_put 写入
    _put_state(client, pid, tag="bp1")
    # 其余 7 类内部来源各形成一条 after（连续链）
    sources = [
        "task",
        "revise",
        "callback",
        "local_parser",
        "content_fuse_apply",
        "content_fuse_consume",
        "checkpoint_restore",
    ]
    cursor = _variant("bp1")
    # 对齐 browser_put 后最新版本链
    rows_after_put = _db_rev_rows(pid)
    assert len(rows_after_put) >= 1
    latest_ver = rows_after_put[0]["state_version"]
    # 用真实 GET 状态构造带匹配版本的 before
    current = client.get(f"/api/projects/{pid}/editor-state").json()
    before = editor_state_service.extract_canonical_snapshot(current)
    before["stateVersion"] = current["stateVersion"]
    assert before["stateVersion"] == latest_ver

    for i, src in enumerate(sources):
        after = _variant(f"s{i}")
        _record(pid, before, after, src)
        before = after

    # 再追加若干 browser_put 使 DB 保留数 > 默认列表 10（写入上限 20）
    for i in range(6):
        _put_state(client, pid, tag=f"more{i}")

    db_rows = _db_rev_rows(pid)
    assert len(db_rows) > 10, len(db_rows)
    assert len(db_rows) <= 20, len(db_rows)

    res = client.get(_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _LIST_TOP
    items = body["items"]
    assert len(items) == 10
    listed_ids = []
    for item in items:
        assert set(item.keys()) == _META_KEYS
        assert _REVISION_ID_RE.fullmatch(item["revisionId"])
        assert _STATE_VERSION_RE.fullmatch(item["stateVersion"])
        assert type(item["snapshotBytes"]) is int
        assert 1 <= item["snapshotBytes"] <= _MAX_BYTES
        assert item["sourceKind"] in _ALL_SOURCES
        assert type(item["createdAt"]) is str and item["createdAt"]
        listed_ids.append(item["revisionId"])
        assert "snapshot" not in item

    # 列表仍只返回最近 10 条，顺序对齐 DB 契约序前缀；不得因写入扩容而返回 11+
    top10 = db_rows[:10]
    assert listed_ids == [r["id"] for r in top10]
    assert [i["stateVersion"] for i in items] == [r["state_version"] for r in top10]
    assert [i["sourceKind"] for i in items] == [r["source_kind"] for r in top10]
    assert [i["snapshotBytes"] for i in items] == [r["snapshot_bytes"] for r in top10]

    # 响应不含正文；禁止出现 snapshot 键（snapshotBytes 合法）
    blob = json.dumps(body, ensure_ascii=False)
    assert _SECRET not in blob
    assert "正文-" not in blob
    assert '"snapshot"' not in blob
    assert "snapshot_json" not in blob


def test_list_sql_excludes_snapshot_json_and_project_min_projection(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _put_state(client, pid, tag="sql")

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_revisions" in low or "projects" in low:
            if statement.lstrip().upper().startswith("SELECT"):
                captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        listed = client.get(_url(pid))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert listed.status_code == 200, listed.text
    assert "snapshot" not in listed.text.lower() or "snapshotbytes" in listed.text.lower()
    body = listed.json()
    assert "snapshot" not in body["items"][0]

    # 项目校验最小投影：SELECT 投影段含 id，不含 name/kind 等无关列
    project_selects = [
        s
        for s in captured
        if re.search(r"\bfrom\s+projects\b", " ".join(s.split()), re.I)
    ]
    assert project_selects, f"未捕获 projects SELECT: {captured}"
    found_min_proj = False
    for sql in project_selects:
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", " ".join(sql.split()))
        if match is None:
            continue
        select_list = match.group(1).lower()
        # 最小投影允许 table.id 或 id
        if "id" in select_list and "name" not in select_list and "kind" not in select_list:
            found_min_proj = True
            break
    assert found_min_proj, project_selects

    # revision 列表 SELECT 投影绝不含 snapshot_json
    rev_selects = [
        s
        for s in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
    ]
    assert rev_selects, f"未捕获 revision SELECT: {captured}"
    found_meta = False
    for sql in rev_selects:
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", " ".join(sql.split()))
        if match is None:
            continue
        select_list = match.group(1).lower()
        if "state_version" in select_list or "snapshot_bytes" in select_list:
            found_meta = True
            assert "snapshot_json" not in select_list, sql
            assert "state_version" in select_list
            assert "snapshot_bytes" in select_list
            assert "source_kind" in select_list
            assert "created_at" in select_list
            assert re.search(r"\bid\b", select_list) or ".id" in select_list
    assert found_meta, rev_selects


# ---------- 详情 ----------


def test_detail_six_fields_canonical_snapshot_and_version(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="详情规范")
    put = _put_state(client, pid, tag="detail")
    after_ver = put["stateVersion"]
    rows = _db_rev_rows(pid)
    after_row = next(r for r in rows if r["state_version"] == after_ver)
    rid = after_row["id"]

    res = client.get(_url(pid, rid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _DETAIL_KEYS
    assert body["revisionId"] == rid
    assert body["stateVersion"] == after_ver
    assert body["sourceKind"] == "browser_put"
    assert type(body["snapshotBytes"]) is int
    snap = body["snapshot"]
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    for bad in (
        "projectId",
        "updatedAt",
        "responseMatrixVersion",
        "workspaceId",
        "taskId",
        "batchId",
        "userId",
    ):
        assert bad not in snap
    assert body["stateVersion"] == _expected_version(snap)
    assert body["snapshotBytes"] == len(_canonical_bytes(snap))
    assert body["snapshotBytes"] == after_row["snapshot_bytes"]
    # 响应不得附带项目/空间/内部关联
    raw = res.text
    assert "workspaceId" not in raw
    assert "projectId" not in raw
    assert "workspace_id" not in raw
    assert "project_id" not in raw


def test_detail_sql_triple_scope_not_global_get(disabled_client):
    client = disabled_client
    a = _create_project(client, name="详情域A")
    b = _create_project(client, name="详情域B")
    _put_state(client, a, tag="scopeA")
    rid = _db_rev_rows(a)[0]["id"]

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "editor_state_revisions" not in statement.lower():
            return
        if not statement.lstrip().upper().startswith("SELECT"):
            return
        captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        cross = client.get(_url(b, rid))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    _assert_fixed_error(cross, 404, "editor_state_revision_not_found")
    assert rid not in cross.text
    assert captured, "未捕获详情 SELECT"
    scoped = False
    for sql, _params in captured:
        compact = " ".join(sql.split()).lower()
        where_match = re.search(r"(?is)\bwhere\b(.*)$", compact)
        if where_match is None:
            continue
        where_clause = where_match.group(1)
        has_id = bool(
            re.search(r"\bid\s*=", where_clause) or re.search(r"\.id\s*=", where_clause)
        )
        has_ws = "workspace_id" in where_clause
        has_proj = "project_id" in where_clause
        if has_id and has_ws and has_proj:
            scoped = True
            break
    assert scoped, f"跨项目详情未在 SQL WHERE 作用域过滤: {captured}"


# ---------- 404 / 跨空间 ----------


def test_project_and_revision_not_found_cross_project_real_cross_space(disabled_client):
    client = disabled_client
    a = _create_project(client, name="项目A-404")
    b = _create_project(client, name="项目B-404")
    _put_state(client, a, tag="a404")
    rid = _db_rev_rows(a)[0]["id"]
    before_a = _domain_snapshot(a)
    before_b = _domain_snapshot(b)

    missing_proj = client.get(_url("proj_missing_p12cc1"))
    _assert_fixed_error(missing_proj, 404, "project_not_found")
    assert "proj_missing_p12cc1" not in missing_proj.text

    missing_rev = client.get(_url(a, "esr_" + "0" * 32))
    _assert_fixed_error(missing_rev, 404, "editor_state_revision_not_found")

    cross = client.get(_url(b, rid))
    _assert_fixed_error(cross, 404, "editor_state_revision_not_found")
    assert rid not in cross.text

    # 真实跨空间：另一 workspace 存在，同 project id 头为 project_not_found
    _ensure_workspace(_WS_OTHER)
    # 在另一空间插入同名 revision id 的旁路行（不同 project）以证明不泄漏
    other_pid = "proj_other_space_p12cc1"
    db = SessionLocal()
    try:
        if db.get(Project, other_pid) is None:
            db.add(
                Project(
                    id=other_pid,
                    workspace_id=_WS_OTHER,
                    name="外空间项目",
                    kind="technical",
                )
            )
            db.commit()
    finally:
        db.close()
    snap = _variant("other")
    snap_json = editor_state_service.canonical_snapshot_json(
        editor_state_service.extract_canonical_snapshot(snap)
    )
    # 主键全局唯一：外空间使用独立 revision id，证明跨空间项目不可见
    other_rid = "esr_" + secrets.token_hex(16)
    _insert_raw_revision(
        project_id=other_pid,
        revision_id=other_rid,
        snapshot_json=snap_json,
        state_version=snap["stateVersion"],
        snapshot_bytes=len(snap_json.encode("utf-8")),
        source_kind="task",
        workspace_id=_WS_OTHER,
    )

    cross_ws_list = client.get(
        _url(a),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(cross_ws_list, 404, "project_not_found")

    cross_ws_detail = client.get(
        _url(a, rid),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    _assert_fixed_error(cross_ws_detail, 404, "project_not_found")
    assert rid not in cross_ws_detail.text

    # 用外空间头访问外空间项目列表/详情应成功（证明空间隔离而非整库空）
    other_list = client.get(
        _url(other_pid),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert other_list.status_code == 200, other_list.text
    assert other_list.json()["items"][0]["revisionId"] == other_rid
    assert other_list.json()["items"][0]["sourceKind"] == "task"
    other_detail = client.get(
        _url(other_pid, other_rid),
        headers={"X-Workspace-Id": _WS_OTHER},
    )
    assert other_detail.status_code == 200, other_detail.text

    # 本空间完整身份不受影响；外空间行不泄漏到本空间
    assert _domain_snapshot(a) == before_a
    assert _domain_snapshot(b) == before_b
    other_rows = _db_rev_rows(other_pid, workspace_id=_WS_OTHER)
    assert len(other_rows) == 1
    assert other_rows[0]["id"] == other_rid
    assert other_rows[0]["source_kind"] == "task"
    assert other_rid not in {r["id"] for r in _db_rev_rows(a)}


# ---------- 权限 / no-store ----------


def test_auth_required_bid_writer_only(required_client):
    """用途：required 模式仅 bid_writer 可读；finance/hr/bidder/仅 owner 拒绝。"""
    client = required_client
    for role in ("finance", "hr", "bidder"):
        csrf_role = _login_role(client, role)
        r = client.get(_url("any"), headers={"X-CSRF-Token": csrf_role})
        assert r.status_code == 403, (role, r.text)
        detail = r.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "role_forbidden"
        _assert_no_store(r) if r.headers.get("Cache-Control") else None

    csrf_owner_finance = _login_role(client, "finance", is_owner=True)
    r_owner = client.get(
        _url("any"),
        headers={"X-CSRF-Token": csrf_owner_finance},
    )
    assert r_owner.status_code == 403, r_owner.text
    assert r_owner.json()["detail"]["code"] == "role_forbidden"

    csrf_w = _login_role(client, "bid_writer")
    create = client.post(
        "/api/projects",
        json={"name": "required修订历史", "kind": "technical"},
        headers={"X-CSRF-Token": csrf_w},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]
    _put_state(client, pid, tag="auth", headers={"X-CSRF-Token": csrf_w})
    listed = client.get(_url(pid), headers={"X-CSRF-Token": csrf_w})
    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    rid = listed.json()["items"][0]["revisionId"]
    detail = client.get(_url(pid, rid), headers={"X-CSRF-Token": csrf_w})
    assert detail.status_code == 200, detail.text
    _assert_no_store(detail)

    client.cookies.clear()
    no_sess = client.get(_url(pid))
    assert no_sess.status_code in (401, 403), no_sess.text


def test_disabled_personal_mode_list_detail_and_errors(disabled_client):
    """用途：disabled 个人版兼容列表/详情；业务错误同样 no-store。"""
    client = disabled_client
    pid = _create_project(client)
    _put_state(client, pid, tag="dis")
    listed = client.get(_url(pid))
    assert listed.status_code == 200, listed.text
    _assert_no_store(listed)
    rid = listed.json()["items"][0]["revisionId"]
    detail = client.get(_url(pid, rid))
    assert detail.status_code == 200
    _assert_no_store(detail)
    miss = client.get(_url("proj_missing_disabled"))
    _assert_fixed_error(miss, 404, "project_not_found")


# ---------- 损坏矩阵 ----------


def test_corrupt_metadata_and_body_fixed_500_no_leak(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="损坏矩阵")
    put = _put_state(client, pid, tag="corrupt")
    rows = _db_rev_rows(pid)
    target = next(r for r in rows if r["state_version"] == put["stateVersion"])
    rid = target["id"]
    good_json = target["snapshot_json"]
    good_ver = target["state_version"]
    good_bytes = target["snapshot_bytes"]
    good_created = target["created_at"]
    good_source = target["source_kind"]

    def _restore_good() -> None:
        """用途：原始 SQL 恢复合法行；兼容 created_at 解码损坏后 ORM 无法物化。"""
        # 清理注入的非法 id 旁路行
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM editor_state_revisions "
                    "WHERE project_id = :pid AND id != :rid"
                ),
                {"pid": pid, "rid": rid},
            )
            conn.execute(text("PRAGMA ignore_check_constraints = ON"))
            conn.execute(
                text(
                    "UPDATE editor_state_revisions SET "
                    "snapshot_json = :j, state_version = :v, snapshot_bytes = :b, "
                    "source_kind = :src, created_at = :c "
                    "WHERE id = :rid"
                ),
                {
                    "j": good_json,
                    "v": good_ver,
                    "b": good_bytes,
                    "src": good_source or "browser_put",
                    "c": good_created,
                    "rid": rid,
                },
            )
            # 若目标行被删，重新插入合法行
            exists = conn.execute(
                text("SELECT 1 FROM editor_state_revisions WHERE id = :rid"),
                {"rid": rid},
            ).first()
            if exists is None:
                conn.execute(
                    text(
                        "INSERT INTO editor_state_revisions "
                        "(id, workspace_id, project_id, snapshot_json, "
                        "state_version, snapshot_bytes, source_kind, created_at) "
                        "VALUES (:rid, :ws, :pid, :j, :v, :b, :src, :c)"
                    ),
                    {
                        "rid": rid,
                        "ws": _WS,
                        "pid": pid,
                        "j": good_json,
                        "v": good_ver,
                        "b": good_bytes,
                        "src": good_source or "browser_put",
                        "c": good_created,
                    },
                )
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))

    def _patch(**fields) -> None:
        """用途：合法范围内的 ORM 字段覆盖（版本/正文等无 CHECK 冲突）。"""
        db = SessionLocal()
        try:
            row = db.get(EditorStateRevisionRow, rid)
            assert row is not None
            for k, v in fields.items():
                setattr(row, k, v)
            db.commit()
        finally:
            db.close()

    # 非法 ID：DB 允许任意字符串主键；列表读取时固定 500
    bad_id = "esr_BAD_CASE_NOT_HEX_32chars!!"
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, rid)
        assert row is not None
        db.add(
            EditorStateRevisionRow(
                id=bad_id,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                snapshot_json=row.snapshot_json,
                state_version=row.state_version,
                snapshot_bytes=row.snapshot_bytes,
                source_kind=row.source_kind,
                created_at=row.created_at,
            )
        )
        db.delete(row)
        db.commit()
    finally:
        db.close()
    listed = client.get(_url(pid))
    _assert_fixed_error(listed, 500, "editor_state_revision_corrupt")
    assert bad_id not in listed.text
    assert _SECRET not in listed.text
    detail_bad = client.get(_url(pid, bad_id))
    _assert_fixed_error(detail_bad, 500, "editor_state_revision_corrupt")
    assert bad_id not in detail_bad.text
    _raw_sql_delete_revision(bad_id)
    _restore_good()

    # 非法/大写版本（DB 无格式 CHECK，可落库）
    for bad_ver in ("esv_NOT_HEX", "ESV_" + "a" * 32, "esv_" + "g" * 32):
        _patch(state_version=bad_ver)
        listed = client.get(_url(pid))
        _assert_fixed_error(listed, 500, "editor_state_revision_corrupt")
        assert bad_ver not in listed.text
        detail = client.get(_url(pid, rid))
        _assert_fixed_error(detail, 500, "editor_state_revision_corrupt")
        assert bad_ver not in detail.text
        assert rid not in detail.text
        _restore_good()

    # 真实 SQLite 行 + HTTP：越界字节（CHECK 需临时忽略）
    for nbytes in (0, _MAX_BYTES + 1, -1):
        _raw_sql_update_revision(
            rid,
            ignore_check=True,
            snapshot_bytes=nbytes,
            state_version=good_ver,
            source_kind="browser_put",
            snapshot_json=good_json,
        )
        listed = client.get(_url(pid))
        _assert_fixed_error(listed, 500, "editor_state_revision_corrupt")
        detail = client.get(_url(pid, rid))
        _assert_fixed_error(detail, 500, "editor_state_revision_corrupt")
        assert str(nbytes) not in listed.text or nbytes in (0, -1)
        # 禁止泄漏异常类型/表名/SQL
        for res in (listed, detail):
            assert "ValueError" not in res.text
            assert "editor_state_revisions" not in res.text
            assert "CHECK" not in res.text
            assert rid not in res.text
            assert _SECRET not in res.text
        _restore_good()

    # 真实 SQLite 行 + HTTP：非法来源（CHECK 需临时忽略）
    for src in ("forged_source", "BROWSER_PUT", "browser-put", ""):
        _raw_sql_update_revision(
            rid,
            ignore_check=True,
            source_kind=src,
            state_version=good_ver,
            snapshot_bytes=good_bytes,
            snapshot_json=good_json,
        )
        listed = client.get(_url(pid))
        _assert_fixed_error(listed, 500, "editor_state_revision_corrupt")
        detail = client.get(_url(pid, rid))
        _assert_fixed_error(detail, 500, "editor_state_revision_corrupt")
        for res in (listed, detail):
            if src:
                assert src not in res.text
            assert rid not in res.text
            assert _SECRET not in res.text
            assert "ValueError" not in res.text
        _restore_good()

    # 真实 SQLite 行 + HTTP：created_at 解码损坏（物化边界，非仅私有校验）
    # Codex 复现：UPDATE created_at='not-a-datetime' 后须固定 500 corrupt + no-store
    for bad_ts in ("not-a-datetime", "2026-01-01T00:00:00Z-not-iso", "???"):
        _raw_sql_update_revision(rid, created_at=bad_ts)
        listed = client.get(_url(pid))
        _assert_fixed_error(listed, 500, "editor_state_revision_corrupt")
        detail = client.get(_url(pid, rid))
        _assert_fixed_error(detail, 500, "editor_state_revision_corrupt")
        for res in (listed, detail):
            assert bad_ts not in res.text
            assert "not-a-datetime" not in res.text
            assert "Invalid isoformat" not in res.text
            assert "ValueError" not in res.text
            assert "Traceback" not in res.text
            assert "editor_state_revisions" not in res.text
            assert rid not in res.text
            assert _SECRET not in res.text
            assert res.headers.get("Cache-Control") == "no-store"
        _restore_good()

    # 正文类损坏（HTTP 详情）
    data = json.loads(good_json)
    body_cases = [
        json.dumps(
            {"mode": "ALIGNED", "evil": _SECRET, "path": _PATH_MARKER},
            ensure_ascii=False,
        ),
        "[1,2,3]",
        "{not-json",
        json.dumps(data, ensure_ascii=False, sort_keys=False, indent=2),
    ]
    for poisoned in body_cases:
        _patch(
            snapshot_json=poisoned,
            snapshot_bytes=len(poisoned.encode("utf-8")),
            state_version=good_ver,
            source_kind="browser_put",
        )
        res = client.get(_url(pid, rid))
        _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
        assert rid not in res.text
        assert _SECRET not in res.text
        assert _PATH_MARKER not in res.text
        _restore_good()

    # 规范 JSON + 同步字节，但版本被篡改
    _patch(
        snapshot_json=good_json,
        snapshot_bytes=good_bytes,
        state_version="esv_" + "0" * 32,
        source_kind="browser_put",
    )
    res = client.get(_url(pid, rid))
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    assert ("esv_" + "0" * 32) not in res.text
    _restore_good()

    # 字节与 UTF-8 不一致（仍落在 1..2MiB CHECK 内）
    drift_bytes = good_bytes + 7
    assert 1 <= drift_bytes <= _MAX_BYTES
    _patch(
        snapshot_json=good_json,
        snapshot_bytes=drift_bytes,
        state_version=good_ver,
        source_kind="browser_put",
    )
    res = client.get(_url(pid, rid))
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    _restore_good()

    # NaN 非标准 JSON
    data_nan = json.loads(good_json)
    data_nan["analysisOverview"] = float("nan")
    poisoned_nan = json.dumps(
        data_nan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    )
    _patch(
        snapshot_json=poisoned_nan,
        snapshot_bytes=len(poisoned_nan.encode("utf-8")),
        state_version="esv_"
        + hashlib.sha256(poisoned_nan.encode("utf-8")).hexdigest()[:32],
        source_kind="browser_put",
    )
    res = client.get(_url(pid, rid))
    _assert_fixed_error(res, 500, "editor_state_revision_corrupt")
    assert "NaN" not in res.text
    assert "Infinity" not in res.text
    _restore_good()


# ---------- GET 前后完整零写 ----------


def test_get_list_and_detail_are_full_readonly_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="只读零写")
    _put_state(client, pid, tag="ro")
    # 手动检查点，证明 checkpoint 域也不变
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    rid = _db_rev_rows(pid)[0]["id"]

    before = _domain_snapshot(pid)
    assert before["revisions"]
    assert before["checkpoints"]
    assert before["editor_state"] is not None
    assert before["project"] is not None

    listed = client.get(_url(pid))
    assert listed.status_code == 200, listed.text
    after_list = _domain_snapshot(pid)
    assert after_list == before

    detail = client.get(_url(pid, rid))
    assert detail.status_code == 200, detail.text
    after_detail = _domain_snapshot(pid)
    assert after_detail == before

    # 未知查询参数不改变结果
    listed2 = client.get(
        _url(pid),
        params={
            "limit": 100,
            "offset": 0,
            "cursor": "x",
            "source": "task",
            "search": _SECRET,
            "q": "正文",
        },
    )
    assert listed2.status_code == 200, listed2.text
    assert listed2.json() == listed.json()
    assert _domain_snapshot(pid) == before


def test_service_and_api_source_no_write_ops_ast_supplement(disabled_client):
    """用途：源码/AST 补充证明无 commit/rollback/flush/refresh/锁/写；不得替代 DB 证据。"""
    # 先确保实现文件存在（failure-first 时允许跳过 AST 若文件尚未创建）
    if not _SERVICE_PATH.is_file():
        pytest.fail(f"生产服务尚未创建: {_SERVICE_PATH}")
    if not _API_PATH.is_file():
        pytest.fail(f"生产路由尚未创建: {_API_PATH}")

    for path in (_SERVICE_PATH, _API_PATH):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned_names = {
            "commit",
            "rollback",
            "flush",
            "refresh",
            "with_for_update",
            "record_editor_state_transition",
            "create_editor_state_checkpoint",
            "restore_editor_state_checkpoint",
            "upsert_editor_state",
            "get_editor_state",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Attribute):
                    name = func.attr
                elif isinstance(func, ast.Name):
                    name = func.id
                if name in banned_names:
                    pytest.fail(f"{path.name} 禁止调用 {name}")
            if isinstance(node, ast.Attribute) and node.attr in {
                "commit",
                "rollback",
                "flush",
                "refresh",
            }:
                # 允许出现在注释字符串外的属性访问也拦截
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


def test_no_write_routes_on_revision_history(disabled_client):
    """
    用途：除精确 C2 POST restore 外，修订历史仍无其他写路由。
    二次开发：C2 注册 POST .../restore 后，空 body 固定 422 且零写；
      不得弱化列表/详情 PUT/PATCH/DELETE 与 POST 列表/详情的 404/405 守卫。
    """
    client = disabled_client
    pid = _create_project(client)
    _put_state(client, pid, tag="nowrite")
    rid = _db_rev_rows(pid)[0]["id"]
    before = _domain_snapshot(pid)
    restore_path = f"{_url(pid, rid)}/restore"

    for method in ("post", "put", "patch", "delete"):
        for path in (_url(pid), _url(pid, rid), restore_path):
            fn = getattr(client, method)
            if method in ("post", "put", "patch"):
                res = fn(path, json={})
            else:
                res = fn(path)
            # 精确 C2 POST restore：路由已注册，空 body 固定 422 且不得成功写
            if method == "post" and path == restore_path:
                assert res.status_code == 422, (
                    method,
                    path,
                    res.status_code,
                    res.text,
                )
                assert res.status_code not in (200, 201)
                continue
            # 除精确 C2 POST 外仍无其他写路由
            assert res.status_code in (404, 405), (
                method,
                path,
                res.status_code,
                res.text,
            )
            assert res.status_code != 200
            assert res.status_code != 201

    assert _domain_snapshot(pid) == before


def test_unknown_query_params_do_not_filter_or_search_body(disabled_client):
    client = disabled_client
    pid = _create_project(client)
    _put_state(client, pid, tag="q1")
    _put_state(client, pid, tag="q2")
    baseline = client.get(_url(pid)).json()
    assert len(baseline["items"]) >= 2

    # 试图按来源筛选 / 扩大条数 / 搜索正文
    tampered = client.get(
        _url(pid),
        params={
            "limit": 1,
            "sourceKind": "task",
            "source": "task",
            "search": _SECRET,
            "q": "正文-q1",
            "order": "asc",
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == baseline
    # 不得因 search 命中正文而改变集合
    ids = {i["revisionId"] for i in tampered.json()["items"]}
    assert ids == {i["revisionId"] for i in baseline["items"]}
