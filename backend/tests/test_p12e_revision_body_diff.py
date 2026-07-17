"""
模块：P12E-A 修订与当前状态章节正文差异专项测试
用途：真实 HTTP+SQLite 验收 body-diff 只读 API、完整正文判等、行差异、
  唯一配对、截断、作用域/角色/损坏脱敏、五域零写与 AST 禁写。
对接：GET .../editor-state-revisions/{revisionId}/body-diff；
  editor_state_revision_body_diff_service。
二次开发：禁止 mock SQLite、宽泛状态码、>=1、空集合假绿、跨项目冒充跨空间。
"""

from __future__ import annotations

import ast
import copy
import json
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

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
)
from app.services import auth_service, editor_state_service
from app.services.editor_state_revision_service import record_editor_state_transition

_WS = "ws_local"
_WS_OTHER = "ws_other_p12ea"
_OWNER_USER = "admin_p12ea_owner"
_OWNER_PASS = "TestPass-P12EA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12EA-Writer-0001!",
    "finance": "TestPass-P12EA-Finance-0001!",
    "hr": "TestPass-P12EA-Hr-0001!",
    "bidder": "TestPass-P12EA-Bidder-0001!",
}
_TOP_KEYS = frozenset(
    {
        "sameBody",
        "changedChapterCount",
        "currentChapterCount",
        "targetChapterCount",
        "truncated",
        "items",
    }
)
_ITEM_KEYS = frozenset(
    {"ordinal", "kind", "beforeTitle", "afterTitle", "hunks"}
)
_HUNK_KEYS = frozenset({"op", "text"})
_KINDS = frozenset({"added", "removed", "changed"})
_OPS = frozenset({"equal", "delete", "insert"})
_SECRET = "SECRET_P12EA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions"
_CODE_BODY_DIFF_FAILED = "editor_state_revision_body_diff_failed"
_MSG_BODY_DIFF_FAILED = "修订正文差异生成失败"
_CODE_CORRUPT = "editor_state_revision_corrupt"
_CODE_PROJECT_NF = "project_not_found"
_CODE_REVISION_NF = "editor_state_revision_not_found"
_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_body_diff_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_revisions.py"
)
_MAX_TITLE = 240
_MAX_HUNK_TEXT = 2_000
_MAX_HUNKS = 80
_MAX_CHAPTERS = 100
_MAX_BODY_CODEPOINTS = 20_000


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


def _diff_url(project_id: str, revision_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{revision_id}/body-diff"
    )


def _create_project(
    client: TestClient,
    name: str = "P12E-A项目",
    *,
    kind: str = "technical",
    headers: dict | None = None,
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind},
        headers=headers or {},
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


def _empty_base() -> dict:
    analysis = editor_state_service.empty_analysis()
    business = editor_state_service.empty_business()
    return {
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


def _state_with_version(**overrides) -> dict:
    state = _empty_base()
    state.update(overrides)
    snap = editor_state_service.extract_canonical_snapshot(state)
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _put_editor_state(
    client: TestClient,
    pid: str,
    payload: dict,
    *,
    headers: dict | None = None,
) -> dict:
    body = {k: v for k, v in payload.items() if k != "stateVersion"}
    res = client.put(
        f"/api/projects/{pid}/editor-state",
        json=body,
        headers=headers or {},
    )
    assert res.status_code == 200, res.text
    return res.json()


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
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
                "snapshot_json": r.snapshot_json,
            }
            for r in rows
        ]
    finally:
        db.close()


def _record(
    project_id: str,
    before: dict,
    after: dict,
    source: str = "browser_put",
    *,
    workspace_id: str = _WS,
) -> str:
    before_ids = {r["id"] for r in _db_rev_rows(project_id, workspace_id)}
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
    after_rows = _db_rev_rows(project_id, workspace_id)
    for row in after_rows:
        if (
            row["id"] not in before_ids
            and row["state_version"] == after["stateVersion"]
        ):
            return row["id"]
    for row in after_rows:
        if row["state_version"] == after["stateVersion"]:
            return row["id"]
    raise AssertionError("record 后未找到 after 版本修订行")


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
    username = f"user_{role}_p12ea{'_own' if is_owner else ''}_{secrets.token_hex(2)}"
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
    assert "editor_state_revision_body_diff_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "JSONDecodeError" not in blob
    assert "projectId" not in blob
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "esv_" not in blob
    assert "esr_" not in blob


def _assert_success_shape(body: dict) -> None:
    assert set(body.keys()) == _TOP_KEYS
    assert type(body["sameBody"]) is bool
    assert type(body["changedChapterCount"]) is int
    assert body["changedChapterCount"] >= 0
    assert type(body["currentChapterCount"]) is int
    assert body["currentChapterCount"] >= 0
    assert type(body["targetChapterCount"]) is int
    assert body["targetChapterCount"] >= 0
    assert type(body["truncated"]) is bool
    assert isinstance(body["items"], list)
    assert body["changedChapterCount"] == len(body["items"])
    if body["sameBody"]:
        assert body["items"] == []
        assert body["changedChapterCount"] == 0
    else:
        assert len(body["items"]) >= 1
    for i, item in enumerate(body["items"]):
        assert set(item.keys()) == _ITEM_KEYS
        assert item["ordinal"] == i + 1
        assert item["kind"] in _KINDS
        assert type(item["beforeTitle"]) is str
        assert type(item["afterTitle"]) is str
        assert isinstance(item["hunks"], list)
        for hunk in item["hunks"]:
            assert set(hunk.keys()) == _HUNK_KEYS
            assert hunk["op"] in _OPS
            assert type(hunk["text"]) is str


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
        "revisions": _db_rev_rows(project_id),
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "audits": _db_audit_rows(),
    }


def _ensure_workspace(ws_id: str, name: str = "其他空间P12EA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12ea",
                )
            )
            db.commit()
    finally:
        db.close()


def _revision_id_for_version(project_id: str, state_version: str) -> str:
    for row in _db_rev_rows(project_id):
        if row["state_version"] == state_version:
            return row["id"]
    raise AssertionError(f"无 stateVersion={state_version} 的修订")


def _ch(cid: str, title: str, body: str) -> dict:
    return {
        "id": cid,
        "title": title,
        "body": body,
        "status": "pending",
        "preview": "p",
        "wordCount": max(1, len(body)),
    }


def _seed_same_body(client: TestClient) -> tuple[str, str]:
    pid = _create_project(client, name="同正文")
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                _ch("ch1", "章节A", f"正文-{_SECRET}"),
            ],
            "parsedMarkdown": f"# md\n{_SECRET}",
        },
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])
    return pid, rid


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


def test_same_body_exact_shape_and_no_store(disabled_client):
    client = disabled_client
    pid, rid = _seed_same_body(client)
    before = _domain_snapshot(pid)

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True
    assert body["changedChapterCount"] == 0
    assert body["items"] == []
    assert body["truncated"] is False
    assert body["currentChapterCount"] == 1
    assert body["targetChapterCount"] == 1
    blob = res.text
    assert _SECRET not in blob
    assert rid not in blob
    assert "esv_" not in blob
    assert "esr_" not in blob
    assert "ch1" not in blob
    assert _domain_snapshot(pid) == before


def test_body_replace_and_line_hunks(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="正文替换")
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                _ch("c1", "总体架构", "第一段\n新正文\n"),
            ]
        },
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [
        _ch("c1", "总体架构", "第一段\n旧正文\n"),
    ]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["changedChapterCount"] == 1
    item = body["items"][0]
    assert item["kind"] == "changed"
    assert item["beforeTitle"] == "总体架构"
    assert item["afterTitle"] == "总体架构"
    ops = [h["op"] for h in item["hunks"]]
    assert "delete" in ops
    assert "insert" in ops
    texts = "".join(h["text"] for h in item["hunks"])
    assert "旧正文" in texts
    assert "新正文" in texts
    assert "第一段" in texts


def test_title_only_change_is_same_body(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="仅标题")
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "新标题", "同一正文")]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", "旧标题", "同一正文")]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True
    assert body["items"] == []


def test_added_and_removed_chapters(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="增删章节")
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                _ch("keep", "保留", "same"),
                _ch("new", "新增章", "new-body"),
            ]
        },
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [
        _ch("keep", "保留", "same"),
        _ch("old", "删除章", "old-body"),
    ]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    kinds = {it["kind"] for it in body["items"]}
    assert kinds == {"added", "removed"}
    by_kind = {it["kind"]: it for it in body["items"]}
    assert by_kind["added"]["afterTitle"] == "新增章"
    assert "new-body" in "".join(h["text"] for h in by_kind["added"]["hunks"])
    assert by_kind["removed"]["beforeTitle"] == "删除章"
    assert "old-body" in "".join(h["text"] for h in by_kind["removed"]["hunks"])


def test_newline_normalization_crlf(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="换行规范化")
    # 当前用 \n
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "T", "a\nb\n")]},
    )
    # 目标用 \r\n 等价正文 → sameBody
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", "T", "a\r\nb\r\n")]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True


def test_unique_id_pairing_not_ordinal(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="唯一ID配对")
    # 当前顺序 b,a；目标 a,b — 按 id 配对后正文相同
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                _ch("b", "B", "body-b"),
                _ch("a", "A", "body-a"),
            ]
        },
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [
        _ch("a", "A", "body-a"),
        _ch("b", "B", "body-b"),
    ]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True


def test_ordinal_pairing_when_ids_missing(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="序号配对")
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                {
                    "title": "T1",
                    "body": "new1",
                    "status": "pending",
                    "preview": "p",
                    "wordCount": 1,
                },
                {
                    "title": "T2",
                    "body": "same2",
                    "status": "pending",
                    "preview": "p",
                    "wordCount": 1,
                },
            ]
        },
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [
        {
            "title": "T1",
            "body": "old1",
            "status": "pending",
            "preview": "p",
            "wordCount": 1,
        },
        {
            "title": "T2",
            "body": "same2",
            "status": "pending",
            "preview": "p",
            "wordCount": 1,
        },
    ]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["changedChapterCount"] == 1
    assert body["items"][0]["kind"] == "changed"
    texts = "".join(h["text"] for h in body["items"][0]["hunks"])
    assert "old1" in texts
    assert "new1" in texts


def test_duplicate_id_fixed_500(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="重复ID")
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("x", "A", "1"), _ch("y", "B", "2")]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("dup", "A", "1"), _ch("dup", "B", "2")]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_BODY_DIFF_FAILED)
    assert res.json()["detail"]["message"] == _MSG_BODY_DIFF_FAILED


def test_non_object_chapter_fixed_500(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="脏章节")
    current = _put_editor_state(
        client, pid, {"chapters": [_ch("c1", "T", "body")]}
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = ["not-an-object"]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_BODY_DIFF_FAILED)


def test_title_truncation_flag(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="标题截断")
    long_title = "题" * (_MAX_TITLE + 20)
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", long_title, "after-body")]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", long_title, "before-body")]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["truncated"] is True
    assert len(body["items"][0]["afterTitle"]) == _MAX_TITLE
    assert len(body["items"][0]["beforeTitle"]) == _MAX_TITLE


def test_hunk_text_truncation_flag(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="hunk截断")
    long_old = "O" * (_MAX_HUNK_TEXT + 50)
    long_new = "N" * (_MAX_HUNK_TEXT + 50)
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "T", long_new)]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", "T", long_old)]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["truncated"] is True
    for hunk in body["items"][0]["hunks"]:
        assert len(hunk["text"]) <= _MAX_HUNK_TEXT


def test_full_value_equality_not_truncated_false_same(disabled_client):
    """用途：差异在 hunk 截断点之后时仍 sameBody=false，不能因截断报相同。"""
    client = disabled_client
    pid = _create_project(client, name="截断后仍判不等")
    prefix = "P" * _MAX_HUNK_TEXT
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "T", prefix + "TAIL_A")]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", "T", prefix + "TAIL_B")]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["changedChapterCount"] >= 1


def test_max_body_codepoints_applied_before_difflib(disabled_client, monkeypatch):
    """
    用途：反假绿——MAX_BODY_CODEPOINTS 在进 difflib 前必须生效。
    完整正文先判等；差异仅在 20000 码点之后时仍 sameBody=false、truncated=true；
    实际捕获 SequenceMatcher 输入码点不超过 20000，不得只断言常量存在。
    """
    import difflib

    import app.services.editor_state_revision_body_diff_service as svc

    captured: list[tuple[list[str], list[str]]] = []
    real_sm = difflib.SequenceMatcher

    def _capturing_sm(*args, **kwargs):  # type: ignore[no-untyped-def]
        # SequenceMatcher(isjunk=None, a="", b="", autojunk=True)
        a = kwargs.get("a", args[1] if len(args) > 1 else "")
        b = kwargs.get("b", args[2] if len(args) > 2 else "")
        a_list = list(a) if not isinstance(a, list) else a
        b_list = list(b) if not isinstance(b, list) else b
        captured.append((a_list, b_list))
        # 任一展示输入不得超过 MAX_BODY_CODEPOINTS 码点
        a_text = "".join(a_list)
        b_text = "".join(b_list)
        assert len(list(a_text)) <= _MAX_BODY_CODEPOINTS, (
            f"difflib a 输入超限: {len(list(a_text))}"
        )
        assert len(list(b_text)) <= _MAX_BODY_CODEPOINTS, (
            f"difflib b 输入超限: {len(list(b_text))}"
        )
        # 截断点后的 tail 不得进入 difflib
        assert "AFTER_TAIL" not in a_text
        assert "AFTER_TAIL" not in b_text
        return real_sm(*args, **kwargs)

    monkeypatch.setattr(svc.difflib, "SequenceMatcher", _capturing_sm)

    client = disabled_client
    pid = _create_project(client, name="正文码点截断前判等")
    # 前 20000 码点完全相同，仅 tail 不同 → 完整值不同
    prefix = "前" * _MAX_BODY_CODEPOINTS
    current_body = prefix + "AFTER_TAIL_X"
    target_body = prefix + "AFTER_TAIL_Y"
    assert current_body != target_body
    assert current_body[:_MAX_BODY_CODEPOINTS] == target_body[:_MAX_BODY_CODEPOINTS]
    assert len(list(current_body)) > _MAX_BODY_CODEPOINTS
    assert len(list(target_body)) > _MAX_BODY_CODEPOINTS

    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "长正文", current_body)]},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [_ch("c1", "长正文", target_body)]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False, "截断后差异不得误报 sameBody=true"
    assert body["truncated"] is True, "展示正文截断必须传播 truncated=true"
    assert body["changedChapterCount"] == 1
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["kind"] == "changed"
    assert item["beforeTitle"] == "长正文"
    assert item["afterTitle"] == "长正文"
    # 必须真实进入 difflib，且输入已截断
    assert len(captured) >= 1, "必须实际调用 SequenceMatcher"
    for a_list, b_list in captured:
        a_text = "".join(a_list)
        b_text = "".join(b_list)
        assert len(list(a_text)) <= _MAX_BODY_CODEPOINTS
        assert len(list(b_text)) <= _MAX_BODY_CODEPOINTS
        assert "AFTER_TAIL_X" not in a_text and "AFTER_TAIL_X" not in b_text
        assert "AFTER_TAIL_Y" not in a_text and "AFTER_TAIL_Y" not in b_text
    # 截断后展示输入相同 → hunks 可为 equal 或空；不得出现截断点之后的 tail 泄漏
    all_text = "".join(h["text"] for h in item["hunks"])
    assert "AFTER_TAIL_X" not in all_text
    assert "AFTER_TAIL_Y" not in all_text
    for hunk in item["hunks"]:
        assert len(hunk["text"]) <= _MAX_HUNK_TEXT
        assert "AFTER_TAIL" not in hunk["text"]

    # 对称：完整正文相同（含超长）时仍 sameBody=true，且不触发截断、不进 difflib
    captured.clear()
    pid2 = _create_project(client, name="超长相同正文")
    same_long = "同" * (_MAX_BODY_CODEPOINTS + 80)
    cur2 = _put_editor_state(
        client, pid2, {"chapters": [_ch("c2", "同", same_long)]}
    )
    tgt2 = copy.deepcopy(editor_state_service.extract_canonical_snapshot(cur2))
    tgt2["chapters"] = [_ch("c2", "同", same_long)]
    rid2 = _record(pid2, _state_with_version(), _state_with_version(**tgt2))
    res2 = client.get(_diff_url(pid2, rid2))
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    _assert_success_shape(body2)
    assert body2["sameBody"] is True
    assert body2["items"] == []
    assert body2["truncated"] is False
    assert captured == [], "完整正文相同不得调用 difflib"


def test_corrupt_revision_fixed_500_not_body_diff_failed(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="损坏修订")
    current = _put_editor_state(
        client, pid, {"chapters": [_ch("c1", "T", f"b-{_SECRET}")]}
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, rid)
        assert row is not None
        row.snapshot_json = '{"broken": true}'
        db.commit()
    finally:
        db.close()

    before = _domain_snapshot(pid)
    res = client.get(_diff_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_CORRUPT)
    assert _domain_snapshot(pid) == before


def test_missing_cross_project_cross_workspace_404(disabled_client):
    client = disabled_client
    pid_a = _create_project(client, name="空间A主项目")
    pid_b = _create_project(client, name="空间A旁项目")
    current_a = _put_editor_state(
        client, pid_a, {"chapters": [_ch("a", "A", "1")]}
    )
    current_b = _put_editor_state(
        client, pid_b, {"chapters": [_ch("b", "B", "2")]}
    )
    rid_a = _revision_id_for_version(pid_a, current_a["stateVersion"])
    rid_b = _revision_id_for_version(pid_b, current_b["stateVersion"])

    res_nf = client.get(_diff_url("proj_dead_0000", rid_a))
    _assert_fixed_error(res_nf, 404, _CODE_PROJECT_NF)

    res_rn = client.get(_diff_url(pid_a, "esr_" + "0" * 32))
    _assert_fixed_error(res_rn, 404, _CODE_REVISION_NF)

    before_a = _domain_snapshot(pid_a)
    before_b = _domain_snapshot(pid_b)
    res_cross_p = client.get(_diff_url(pid_a, rid_b))
    _assert_fixed_error(res_cross_p, 404, _CODE_REVISION_NF)
    assert _domain_snapshot(pid_a) == before_a
    assert _domain_snapshot(pid_b) == before_b

    _ensure_workspace(_WS_OTHER)
    db = SessionLocal()
    try:
        other_pid = f"proj_{secrets.token_hex(4)}_{secrets.token_hex(2)}"
        db.add(
            Project(
                id=other_pid,
                workspace_id=_WS_OTHER,
                name="外空间项目",
                kind="technical",
                status="draft",
            )
        )
        db.commit()
    finally:
        db.close()
    other_after = _state_with_version(chapters=[_ch("z", "Z", "zbody")])
    other_rid = _record(
        other_pid,
        _state_with_version(),
        other_after,
        workspace_id=_WS_OTHER,
    )
    before_local = _domain_snapshot(pid_a)
    before_other = _domain_snapshot(other_pid)
    res_ws = client.get(
        _diff_url(other_pid, other_rid),
        headers={"X-Workspace-Id": _WS},
    )
    _assert_fixed_error(res_ws, 404, _CODE_PROJECT_NF)
    assert other_pid not in res_ws.text
    assert other_rid not in res_ws.text
    assert _domain_snapshot(pid_a) == before_local
    assert _domain_snapshot(other_pid) == before_other


def test_required_role_matrix(required_client):
    client = required_client
    csrf = _login_role(client, "bid_writer")
    pid = _create_project(
        client, name="角色矩阵", headers={"X-CSRF-Token": csrf}
    )
    current = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c", "T", "b")]},
        headers={"X-CSRF-Token": csrf},
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])

    res_ok = client.get(_diff_url(pid, rid))
    assert res_ok.status_code == 200, res_ok.text
    _assert_no_store(res_ok)

    for role in ("finance", "hr", "bidder"):
        _login_role(client, role)
        res = client.get(_diff_url(pid, rid))
        assert res.status_code == 403, (role, res.status_code, res.text)


def test_unknown_query_params_do_not_change_result(disabled_client):
    client = disabled_client
    pid, rid = _seed_same_body(client)
    base = client.get(_diff_url(pid, rid))
    assert base.status_code == 200, base.text
    tampered = client.get(
        _diff_url(pid, rid),
        params={
            "fields": "all",
            "includeSnapshot": "1",
            "limit": 100,
            "source": "task",
            "q": _SECRET,
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == base.json()
    _assert_no_store(tampered)


def test_body_diff_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid, rid = _seed_same_body(client)
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    before = _domain_snapshot(pid)
    assert before["revisions"]
    assert before["checkpoints"]
    assert before["editor_state"] is not None
    assert before["project"] is not None

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    assert _domain_snapshot(pid) == before

    res_nf = client.get(_diff_url(pid, "esr_" + "a" * 32))
    assert res_nf.status_code == 404
    assert _domain_snapshot(pid) == before


def test_service_and_api_no_write_ops_ast(disabled_client):
    if not _SERVICE_PATH.is_file():
        pytest.fail(f"生产服务尚未创建: {_SERVICE_PATH}")
    if not _API_PATH.is_file():
        pytest.fail(f"生产路由文件缺失: {_API_PATH}")

    src_service = _SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src_service)
    banned_names = {
        "commit",
        "rollback",
        "flush",
        "refresh",
        "with_for_update",
        "record_editor_state_transition",
        "create_editor_state_checkpoint",
        "restore_editor_state_checkpoint",
        "restore_editor_state_revision",
        "upsert_editor_state",
        "add",
        "delete",
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
                pytest.fail(f"body-diff service 禁止调用 {name}")
        if isinstance(node, ast.Attribute) and node.attr in {
            "commit",
            "rollback",
            "flush",
            "refresh",
        }:
            pytest.fail(f"body-diff service 禁止属性 {node.attr}")

    low = src_service.lower()
    for token in (
        "db.commit",
        "db.rollback",
        "db.flush",
        "db.refresh",
        "db.add",
        "db.delete",
        "with_for_update",
        "httpx",
        "requests.",
        "urllib",
    ):
        assert token not in low, f"body-diff service 含禁写/HTTP token: {token}"

    src_api = _API_PATH.read_text(encoding="utf-8")
    assert "/body-diff" in src_api or "body-diff" in src_api
    assert "editor_state_revision_body_diff_service" in src_api
    assert "Cache-Control" in src_api


def test_injected_failure_fixed_500(disabled_client, monkeypatch):
    """用途：内部异常固定 body_diff_failed，不反射内部细节。"""
    client = disabled_client
    pid, rid = _seed_same_body(client)
    before = _domain_snapshot(pid)

    import app.services.editor_state_revision_body_diff_service as diff_svc

    def _boom(*_a, **_k):
        raise RuntimeError(f"LEAK_{_SECRET}_PATH_{_PATH_MARKER}")

    monkeypatch.setattr(diff_svc, "_pair_chapters", _boom)
    res = client.get(_diff_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_BODY_DIFF_FAILED)
    assert res.json()["detail"]["message"] == _MSG_BODY_DIFF_FAILED
    assert _domain_snapshot(pid) == before


def test_max_chapters_cap_applied_before_difflib(disabled_client, monkeypatch):
    """
    用途：反假绿——最多 100 个实际正文差异章节进入 difflib。
    不得只断言常量；必须真实捕获 _diff_lines 调用次数。
    101 个正文不同的 changed 配对：第 101 个不得进入差异算法；
    返回 items/changedChapterCount 一致且 truncated=true。
    """
    import app.services.editor_state_revision_body_diff_service as svc

    calls: list[tuple[str, str]] = []
    real_diff = svc._diff_lines

    def _counting_diff(before: str, after: str) -> list[dict[str, str]]:
        calls.append((before, after))
        return real_diff(before, after)

    monkeypatch.setattr(svc, "_diff_lines", _counting_diff)

    # —— 单元层：101 个正文均不同的 changed pairs ——
    pairs: list[tuple[str, dict, dict]] = []
    for i in range(_MAX_CHAPTERS + 1):
        cur = {"id": f"c{i}", "title": "T", "body": f"new-{i}"}
        tgt = {"id": f"c{i}", "title": "T", "body": f"old-{i}"}
        pairs.append((svc.KIND_CHANGED, cur, tgt))

    raw_items, any_diff, pre_trunc = svc._build_raw_items(pairs)
    assert any_diff is True
    assert len(calls) <= _MAX_CHAPTERS, (
        f"第101章仍进入差异算法: diff_calls={len(calls)} "
        f"MAX_CHAPTERS={_MAX_CHAPTERS}"
    )
    assert len(raw_items) <= _MAX_CHAPTERS
    assert len(raw_items) == _MAX_CHAPTERS
    assert pre_trunc is True, "超过展示章上限时必须标记截断"
    # 不得为第 101 个差异再构造 raw hunks
    assert all(isinstance(it.get("hunks"), list) for it in raw_items)

    # —— HTTP 集成：响应 items/changedChapterCount/truncated 契约 ——
    calls.clear()
    client = disabled_client
    pid = _create_project(client, name="章上限进difflib前")
    current_chs = [
        _ch(f"c{i}", f"T{i}", f"after-{i}") for i in range(_MAX_CHAPTERS + 1)
    ]
    target_chs = [
        _ch(f"c{i}", f"T{i}", f"before-{i}") for i in range(_MAX_CHAPTERS + 1)
    ]
    current = _put_editor_state(client, pid, {"chapters": current_chs})
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = target_chs
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["truncated"] is True
    assert body["changedChapterCount"] == len(body["items"])
    assert len(body["items"]) == _MAX_CHAPTERS
    assert body["changedChapterCount"] == _MAX_CHAPTERS
    assert body["currentChapterCount"] == _MAX_CHAPTERS + 1
    assert body["targetChapterCount"] == _MAX_CHAPTERS + 1
    # 进 difflib 的章节数仍不得超过 100（含 HTTP 路径）
    assert len(calls) <= _MAX_CHAPTERS, (
        f"HTTP 路径第101章仍进 difflib: diff_calls={len(calls)}"
    )


def test_full_value_scan_beyond_display_chapter_cap(disabled_client, monkeypatch):
    """
    用途：完整值判断必须覆盖全部配对，不能因展示 cap 假绿。
    前 100 个配对正文相同、后续章节才不同 → sameBody=false，
    且必须得到有界可见 item（不能 sameBody=false + items=[]）。
    """
    import app.services.editor_state_revision_body_diff_service as svc

    calls: list[tuple[str, str]] = []
    real_diff = svc._diff_lines

    def _counting_diff(before: str, after: str) -> list[dict[str, str]]:
        calls.append((before, after))
        return real_diff(before, after)

    monkeypatch.setattr(svc, "_diff_lines", _counting_diff)

    # 前 100 配对正文完全相同
    pairs: list[tuple[str, dict | None, dict | None]] = []
    for i in range(_MAX_CHAPTERS):
        ch = {"id": f"s{i}", "title": f"S{i}", "body": "same-body"}
        pairs.append((svc.KIND_CHANGED, dict(ch), dict(ch)))
    # 后续 2 个正文不同
    for i in range(2):
        cur = {"id": f"d{i}", "title": f"D{i}", "body": f"new-tail-{i}"}
        tgt = {"id": f"d{i}", "title": f"D{i}", "body": f"old-tail-{i}"}
        pairs.append((svc.KIND_CHANGED, cur, tgt))

    raw_items, any_diff, pre_trunc = svc._build_raw_items(pairs)
    assert any_diff is True, "后续章节正文不同不得假绿 sameBody"
    assert len(raw_items) == 2, (
        f"后续差异须生成有界可见 item，实际 items={len(raw_items)}"
    )
    assert len(calls) == 2, f"仅后续差异章进 difflib，实际 calls={len(calls)}"
    assert pre_trunc is False

    # HTTP：前 100 相同 + 第 101 不同
    calls.clear()
    client = disabled_client
    pid = _create_project(client, name="完整值扫后续章")
    current_chs = [
        _ch(f"s{i}", f"S{i}", "same-body") for i in range(_MAX_CHAPTERS)
    ] + [_ch("tail", "尾章", "after-tail")]
    target_chs = [
        _ch(f"s{i}", f"S{i}", "same-body") for i in range(_MAX_CHAPTERS)
    ] + [_ch("tail", "尾章", "before-tail")]
    current = _put_editor_state(client, pid, {"chapters": current_chs})
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = target_chs
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_diff_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert len(body["items"]) >= 1, "禁止 sameBody=false 且 items=[]"
    assert body["changedChapterCount"] == len(body["items"])
    assert body["items"][0]["kind"] == "changed"
    texts = "".join(h["text"] for h in body["items"][0]["hunks"])
    assert "before-tail" in texts or "after-tail" in texts
    # 仅 1 个正文差异章应进 difflib
    assert len(calls) == 1, f"仅尾章差异应进 difflib，实际 calls={len(calls)}"


def test_constants_exact(disabled_client):
    """用途：固定上限常量集中在新服务并由测试精确断言。"""
    import app.services.editor_state_revision_body_diff_service as svc

    assert svc.MAX_CHAPTERS == 100
    assert svc.MAX_BODY_CODEPOINTS == _MAX_BODY_CODEPOINTS
    assert svc.MAX_TITLE_CODEPOINTS == 240
    assert svc.MAX_HUNKS_PER_CHAPTER == 80
    assert svc.MAX_HUNK_TEXT_CODEPOINTS == 2_000
    assert svc.MAX_TOTAL_DIFF_TEXT == 120_000
    assert svc.CODE_BODY_DIFF_FAILED == _CODE_BODY_DIFF_FAILED
    assert svc.MSG_BODY_DIFF_FAILED == _MSG_BODY_DIFF_FAILED
    # 服务源码必须在 difflib 路径前使用 MAX_BODY_CODEPOINTS（反假绿）
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    assert "MAX_BODY_CODEPOINTS" in src
    assert "_display_body" in src or "MAX_BODY_CODEPOINTS" in src
    # 不得仅声明常量后从未引用到截断逻辑
    assert src.count("MAX_BODY_CODEPOINTS") >= 2
    # MAX_CHAPTERS 必须参与 difflib 前的章节上限（反假绿：不得只声明）
    assert src.count("MAX_CHAPTERS") >= 2
