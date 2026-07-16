"""
模块：P12D-A 修订与当前状态差异摘要专项测试
用途：真实 HTTP+SQLite 验收 comparison 只读 API、13 键规范比较、
  有界六项摘要、作用域/角色/损坏脱敏、五域零写与 AST 禁写。
对接：GET .../editor-state-revisions/{revisionId}/comparison；
  editor_state_revision_comparison_service。
二次开发：禁止 mock SQLite、宽泛状态码、>=1、空集合假绿、跨项目冒充跨空间。
"""

from __future__ import annotations

import ast
import copy
import json
import re
import secrets
from datetime import datetime
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
_WS_OTHER = "ws_other_p12da"
_OWNER_USER = "admin_p12da_owner"
_OWNER_PASS = "TestPass-P12DA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12DA-Writer-0001!",
    "finance": "TestPass-P12DA-Finance-0001!",
    "hr": "TestPass-P12DA-Hr-0001!",
    "bidder": "TestPass-P12DA-Bidder-0001!",
}
_TOP_KEYS = frozenset(
    {"sameState", "changedFields", "currentSummary", "targetSummary"}
)
_SUMMARY_KEYS = (
    "outlineNodeCount",
    "chapterCount",
    "factCount",
    "responseMatrixRowCount",
    "businessEntryTotal",
    "hasParsedMarkdown",
)
_SUMMARY_KEY_SET = frozenset(_SUMMARY_KEYS)
_CANONICAL_KEYS = editor_state_service.CANONICAL_STATE_KEYS
_SECRET = "SECRET_P12DA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions"
_CODE_COMPARISON_FAILED = "editor_state_revision_comparison_failed"
_MSG_COMPARISON_FAILED = "修订差异摘要生成失败"
_CODE_CORRUPT = "editor_state_revision_corrupt"
_CODE_PROJECT_NF = "project_not_found"
_CODE_REVISION_NF = "editor_state_revision_not_found"
_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_comparison_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_revisions.py"
)
_MAX_NODES = 10_000
_MAX_DEPTH = 32


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


def _cmp_url(project_id: str, revision_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{revision_id}/comparison"
    )


def _create_project(
    client: TestClient,
    name: str = "P12D-A项目",
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


def _record(
    project_id: str,
    before: dict,
    after: dict,
    source: str = "browser_put",
    *,
    workspace_id: str = _WS,
) -> str:
    """
    用途：真实写入 before→after 修订并返回 after 版本对应最新 revision id。
    二次开发：同版本可多次出现时间点，禁止 .one() 按版本全局唯一假设。
    """
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
    # 优先返回本轮新增且 state_version=after 的行
    for row in after_rows:
        if (
            row["id"] not in before_ids
            and row["state_version"] == after["stateVersion"]
        ):
            return row["id"]
    # 去重未新增时取该版本最新时间点
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
    username = f"user_{role}_p12da{'_own' if is_owner else ''}_{secrets.token_hex(2)}"
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
    assert "editor_state_revision_comparison_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "JSONDecodeError" not in blob
    assert "projectId" not in blob
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "esv_" not in blob
    assert "esr_" not in blob


def _assert_summary_shape(summary: dict) -> None:
    assert isinstance(summary, dict)
    assert set(summary.keys()) == _SUMMARY_KEY_SET
    for key in _SUMMARY_KEYS:
        if key == "hasParsedMarkdown":
            assert type(summary[key]) is bool
        else:
            assert type(summary[key]) is int
            assert summary[key] >= 0


def _assert_success_shape(body: dict) -> None:
    assert set(body.keys()) == _TOP_KEYS
    assert type(body["sameState"]) is bool
    assert isinstance(body["changedFields"], list)
    for item in body["changedFields"]:
        assert item in _CANONICAL_KEYS
    # 顺序必须为 13 键权威子序列
    positions = {_CANONICAL_KEYS.index(k): k for k in body["changedFields"]}
    assert body["changedFields"] == [
        positions[i] for i in sorted(positions)
    ]
    assert body["sameState"] is (len(body["changedFields"]) == 0)
    _assert_summary_shape(body["currentSummary"])
    _assert_summary_shape(body["targetSummary"])


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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12DA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12da",
                )
            )
            db.commit()
    finally:
        db.close()


def _revision_id_for_version(project_id: str, state_version: str) -> str:
    """用途：取指定 stateVersion 的最新 revision 行（DESC 序首条）。"""
    for row in _db_rev_rows(project_id):
        if row["state_version"] == state_version:
            return row["id"]
    raise AssertionError(f"无 stateVersion={state_version} 的修订")


def _seed_same_state(client: TestClient) -> tuple[str, str, dict]:
    """
    用途：PUT 当前状态；browser_put 已记入的 after 修订即同状态比较目标。
    返回：project_id, revision_id, 当前 GET 体。
    """
    pid = _create_project(client, name="同状态比较")
    current = _put_editor_state(
        client,
        pid,
        {
            "facts": [{"id": "f1", "text": f"事实-{_SECRET}"}],
            "chapters": [
                {
                    "id": "ch1",
                    "title": "章节A",
                    "body": f"正文-{_SECRET}",
                    "status": "pending",
                    "preview": "预览",
                    "wordCount": 2,
                }
            ],
            "parsedMarkdown": f"# md\n{_SECRET}",
            "guidance": {"note": "指引"},
        },
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])
    return pid, rid, current


# ---------------------------------------------------------------------------
# 最小红测：生产未改时路由必须真实 404
# ---------------------------------------------------------------------------


def test_same_state_comparison_exact_shape_and_no_store(disabled_client):
    client = disabled_client
    pid, rid, _ = _seed_same_state(client)
    before = _domain_snapshot(pid)

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is True
    assert body["changedFields"] == []
    assert body["currentSummary"] == body["targetSummary"]
    # 不泄漏正文/ID/版本
    blob = res.text
    assert _SECRET not in blob
    assert rid not in blob
    assert "esv_" not in blob
    assert "esr_" not in blob
    assert _domain_snapshot(pid) == before


def test_single_field_and_multi_field_changed_order(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="单多字段")
    # 当前：解析正文不同
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                {"id": "c1", "title": "T1", "body": "B1", "status": "pending",
                 "preview": "p", "wordCount": 1},
                {"id": "c2", "title": "T2", "body": "B2", "status": "pending",
                 "preview": "p", "wordCount": 1},
            ],
            "facts": [{"id": "f1", "text": "事实1"}],
            "parsedMarkdown": "CURRENT_MD",
            "guidance": {"g": 1},
        },
    )
    # 目标修订：章节/正文/指引不同，facts 相同
    target_snap = editor_state_service.extract_canonical_snapshot(current)
    target_snap = copy.deepcopy(target_snap)
    target_snap["chapters"] = [
        {"id": "c1", "title": "T1x", "body": "B1", "status": "pending",
         "preview": "p", "wordCount": 1},
        {"id": "c2", "title": "T2", "body": "B2", "status": "pending",
         "preview": "p", "wordCount": 1},
    ]
    target_snap["parsedMarkdown"] = "TARGET_MD"
    target_snap["guidance"] = {"g": 2}
    after = _state_with_version(**target_snap)
    rid = _record(pid, _state_with_version(), after)

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is False
    # 权威顺序：chapters < guidance < parsedMarkdown
    assert body["changedFields"] == ["chapters", "guidance", "parsedMarkdown"]
    assert body["currentSummary"]["chapterCount"] == 2
    assert body["targetSummary"]["chapterCount"] == 2
    assert body["currentSummary"]["hasParsedMarkdown"] is True
    assert body["targetSummary"]["hasParsedMarkdown"] is True


def test_same_count_different_content_must_list_field(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="同计数不同内容")
    current = _put_editor_state(
        client,
        pid,
        {
            "chapters": [
                {"id": "a", "title": "甲", "body": "1", "status": "pending",
                 "preview": "p", "wordCount": 1},
                {"id": "b", "title": "乙", "body": "2", "status": "pending",
                 "preview": "p", "wordCount": 1},
            ],
            "facts": [{"id": "f1", "text": "A"}, {"id": "f2", "text": "B"}],
        },
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["chapters"] = [
        {"id": "a", "title": "甲改", "body": "1", "status": "pending",
         "preview": "p", "wordCount": 1},
        {"id": "b", "title": "乙", "body": "2", "status": "pending",
         "preview": "p", "wordCount": 1},
    ]
    target["facts"] = [{"id": "f1", "text": "A改"}, {"id": "f2", "text": "B"}]
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is False
    assert body["changedFields"] == ["chapters", "facts"]
    # 计数相同不得掩盖内容差异
    assert body["currentSummary"]["chapterCount"] == body["targetSummary"]["chapterCount"]
    assert body["currentSummary"]["chapterCount"] == 2
    assert body["currentSummary"]["factCount"] == body["targetSummary"]["factCount"]
    assert body["currentSummary"]["factCount"] == 2


def test_true_vs_one_not_equal_via_canonical_json(disabled_client):
    """用途：规范 JSON 区分 True 与 1，禁止 Python == 假相等。"""
    client = disabled_client
    pid = _create_project(client, name="TrueVs1")
    current = _put_editor_state(
        client,
        pid,
        {"guidance": {"flag": True, "n": 1}},
    )
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["guidance"] = {"flag": 1, "n": 1}
    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is False
    assert body["changedFields"] == ["guidance"]


def test_full_thirteen_keys_order(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="十三键顺序", kind="business")
    business = editor_state_service.empty_business()
    business["qualify"] = [{"id": "q1"}]
    business["toc"] = [{"id": "t1"}]
    business["quote"] = {"rows": [{"id": "r1"}], "notes": "n"}
    business["commit"] = [{"id": "c1"}]
    current = _put_editor_state(
        client,
        pid,
        {
            "outline": [{"id": "o1", "children": []}],
            "chapters": [
                {"id": "ch", "title": "C", "body": "b", "status": "pending",
                 "preview": "p", "wordCount": 1}
            ],
            "facts": [{"id": "f", "text": "f"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": "ov",
                "techRequirements": ["a"],
                "rejectionRisks": [],
                "scoringPoints": [],
            },
            "guidance": {"x": 1},
            "parsedMarkdown": "md",
            "businessQualify": business["qualify"],
            "businessToc": business["toc"],
            "businessQuote": business["quote"],
            "businessCommit": business["commit"],
            "analysisOverview": "ov",
        },
    )
    # 以真实当前快照为基线，逐键改写，避免 PUT 规范化导致 responseMatrix 等同
    target = copy.deepcopy(editor_state_service.extract_canonical_snapshot(current))
    target["outline"] = [{"id": "o2", "children": []}]
    target["chapters"] = [
        {"id": "ch2", "title": "C2", "body": "b2", "status": "pending",
         "preview": "p", "wordCount": 1}
    ]
    target["facts"] = [{"id": "f2", "text": "f2"}]
    target["mode"] = "DRAFT"
    target["analysis"] = {
        "overview": "ov2",
        "techRequirements": ["b"],
        "rejectionRisks": ["x"],
        "scoringPoints": [],
    }
    # 当前多为 []；目标放一条非空行保证规范 JSON 不同
    target["responseMatrix"] = [
        {
            "id": "rm_t",
            "sourceType": "requirement",
            "sourceRef": "r",
            "text": "diff-row",
            "chapterIds": [],
            "outlineNodeIds": [],
            "status": "pending",
        }
    ]
    target["guidance"] = {"x": 2}
    target["parsedMarkdown"] = "md2"
    target["businessQualify"] = []
    target["businessToc"] = []
    target["businessQuote"] = {"rows": [], "notes": ""}
    target["businessCommit"] = []
    target["analysisOverview"] = "ov2-diff"
    # 断言目标相对当前 13 键全部规范不等
    cur_snap = editor_state_service.extract_canonical_snapshot(current)
    for key in _CANONICAL_KEYS:
        assert editor_state_service.canonical_snapshot_json(
            {key: cur_snap.get(key)}
        ) != editor_state_service.canonical_snapshot_json(
            {key: target.get(key)}
        ), key

    rid = _record(pid, _state_with_version(), _state_with_version(**target))

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is False
    assert body["changedFields"] == list(_CANONICAL_KEYS)
    assert body["currentSummary"]["outlineNodeCount"] == 1
    assert body["currentSummary"]["businessEntryTotal"] == 4
    assert body["targetSummary"]["businessEntryTotal"] == 0
    assert body["currentSummary"]["hasParsedMarkdown"] is True


def test_empty_default_state_same(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="空默认态")
    # 不 PUT，仅记录与默认 GET 一致的 revision
    get_res = client.get(f"/api/projects/{pid}/editor-state")
    assert get_res.status_code == 200, get_res.text
    current = get_res.json()
    snap = editor_state_service.extract_canonical_snapshot(current)
    rid = _record(pid, _state_with_version(), _state_with_version(**snap))

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameState"] is True
    assert body["changedFields"] == []
    assert body["currentSummary"] == {
        "outlineNodeCount": 0,
        "chapterCount": 0,
        "factCount": 0,
        "responseMatrixRowCount": 0,
        "businessEntryTotal": 0,
        "hasParsedMarkdown": False,
    }
    assert body["targetSummary"] == body["currentSummary"]


def _snapshot_outline_only(nodes: list, *, parsed: str = "ok") -> dict:
    """
    用途：构造仅 outline 消耗遍历预算的快照；其余可计数字段用非 list，
    避免空数组额外占用 budget 导致边界假失败。
    """
    return _state_with_version(
        outline=nodes,
        chapters=None,
        facts=None,
        mode="ALIGNED",
        analysis=editor_state_service.empty_analysis(),
        responseMatrix=None,
        guidance=None,
        parsedMarkdown=parsed,
        businessQualify=None,
        businessToc=None,
        businessQuote=None,
        businessCommit=None,
        analysisOverview="",
    )


def test_budget_boundary_ok_and_overflow_fixed_500(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="预算边界")
    # 当前侧也用非 list 可计数字段，避免 currentSummary 先耗尽预算
    current = _put_editor_state(client, pid, {"parsedMarkdown": "ok"})
    # 将当前状态对齐为“仅 outline 预算”语义：直接比较目标大纲边界
    # 边界：恰好 MAX_NODES 个大纲节点（每节点 budget+1），其余字段不增 budget
    nodes_ok = [{"id": f"n{i}", "children": []} for i in range(_MAX_NODES)]
    target_ok = _snapshot_outline_only(nodes_ok, parsed="ok-boundary")
    rid_ok = _record(
        pid,
        _state_with_version(
            **editor_state_service.extract_canonical_snapshot(current)
        ),
        target_ok,
    )
    res_ok = client.get(_cmp_url(pid, rid_ok))
    assert res_ok.status_code == 200, res_ok.text
    body_ok = res_ok.json()
    _assert_success_shape(body_ok)
    assert body_ok["targetSummary"]["outlineNodeCount"] == _MAX_NODES

    # 越界节点：MAX_NODES+1
    nodes_over = [{"id": f"n{i}", "children": []} for i in range(_MAX_NODES + 1)]
    target_over = _snapshot_outline_only(nodes_over, parsed="ok-over")
    rid_over = _record(pid, target_ok, target_over)
    res_over = client.get(_cmp_url(pid, rid_over))
    _assert_fixed_error(res_over, 500, _CODE_COMPARISON_FAILED)
    assert res_over.json()["detail"]["message"] == _MSG_COMPARISON_FAILED

    # 深度越界：depth 从 0 起，depth==33 时失败 → 嵌套 MAX_DEPTH+1 次 children
    deep = {"id": "root", "children": []}
    cursor = deep
    for i in range(_MAX_DEPTH + 1):
        child = {"id": f"d{i}", "children": []}
        cursor["children"] = [child]
        cursor = child
    target_deep = _snapshot_outline_only([deep], parsed="ok-deep")
    rid_deep = _record(pid, target_over, target_deep)
    res_deep = client.get(_cmp_url(pid, rid_deep))
    _assert_fixed_error(res_deep, 500, _CODE_COMPARISON_FAILED)


def test_corrupt_revision_fixed_500_not_comparison_failed(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="损坏修订")
    current = _put_editor_state(
        client, pid, {"parsedMarkdown": f"md-{_SECRET}"}
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])
    # 破坏 snapshot_json 字节/内容
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, rid)
        assert row is not None
        row.snapshot_json = '{"broken": true}'
        db.commit()
    finally:
        db.close()

    before = _domain_snapshot(pid)
    res = client.get(_cmp_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_CORRUPT)
    assert _domain_snapshot(pid) == before


def test_missing_cross_project_cross_workspace_404(disabled_client):
    client = disabled_client
    pid_a = _create_project(client, name="空间A主项目")
    pid_b = _create_project(client, name="空间A旁项目")
    current_a = _put_editor_state(client, pid_a, {"parsedMarkdown": "A"})
    current_b = _put_editor_state(client, pid_b, {"parsedMarkdown": "B"})
    rid_a = _revision_id_for_version(pid_a, current_a["stateVersion"])
    rid_b = _revision_id_for_version(pid_b, current_b["stateVersion"])

    # 不存在项目
    res_nf = client.get(_cmp_url("proj_dead_0000", rid_a))
    _assert_fixed_error(res_nf, 404, _CODE_PROJECT_NF)

    # 不存在修订
    res_rn = client.get(_cmp_url(pid_a, "esr_" + "0" * 32))
    _assert_fixed_error(res_rn, 404, _CODE_REVISION_NF)

    # 跨项目：A 的路径 + B 的 revision
    before_a = _domain_snapshot(pid_a)
    before_b = _domain_snapshot(pid_b)
    res_cross_p = client.get(_cmp_url(pid_a, rid_b))
    _assert_fixed_error(res_cross_p, 404, _CODE_REVISION_NF)
    assert _domain_snapshot(pid_a) == before_a
    assert _domain_snapshot(pid_b) == before_b

    # 真实跨工作空间
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
    # 在外空间写入 editor-state 与 revision
    other_after = _state_with_version(parsedMarkdown="OTHER")
    other_rid = _record(
        other_pid,
        _state_with_version(),
        other_after,
        workspace_id=_WS_OTHER,
    )
    before_local = _domain_snapshot(pid_a)
    before_other = _domain_snapshot(other_pid)
    res_ws = client.get(
        _cmp_url(other_pid, other_rid),
        headers={"X-Workspace-Id": _WS},
    )
    # 本空间看不到外空间项目 → project_not_found
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
        {"parsedMarkdown": "role"},
        headers={"X-CSRF-Token": csrf},
    )
    rid = _revision_id_for_version(pid, current["stateVersion"])

    # bid_writer 成功
    res_ok = client.get(_cmp_url(pid, rid))
    assert res_ok.status_code == 200, res_ok.text
    _assert_no_store(res_ok)

    for role in ("finance", "hr", "bidder"):
        _login_role(client, role)
        res = client.get(_cmp_url(pid, rid))
        assert res.status_code == 403, (role, res.status_code, res.text)


def test_unknown_query_params_do_not_change_result(disabled_client):
    client = disabled_client
    pid, rid, _ = _seed_same_state(client)
    base = client.get(_cmp_url(pid, rid))
    assert base.status_code == 200, base.text
    tampered = client.get(
        _cmp_url(pid, rid),
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


def test_comparison_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid, rid, _ = _seed_same_state(client)
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    before = _domain_snapshot(pid)
    assert before["revisions"]
    assert before["checkpoints"]
    assert before["editor_state"] is not None
    assert before["project"] is not None

    res = client.get(_cmp_url(pid, rid))
    assert res.status_code == 200, res.text
    assert _domain_snapshot(pid) == before

    # 失败路径也零写
    res_nf = client.get(_cmp_url(pid, "esr_" + "a" * 32))
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
                pytest.fail(f"comparison service 禁止调用 {name}")
        if isinstance(node, ast.Attribute) and node.attr in {
            "commit",
            "rollback",
            "flush",
            "refresh",
        }:
            pytest.fail(f"comparison service 禁止属性 {node.attr}")

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
        assert token not in low, f"comparison service 含禁写/HTTP token: {token}"

    # 路由文件必须挂载 comparison 路径；list/detail/restore 行为不在此扩改断言
    src_api = _API_PATH.read_text(encoding="utf-8")
    assert "/comparison" in src_api or 'comparison"' in src_api
    assert "editor_state_revision_comparison_service" in src_api
    assert "Cache-Control" in src_api


def test_injected_summary_failure_fixed_500(disabled_client, monkeypatch):
    """用途：摘要遍历异常固定 comparison_failed，不反射内部异常。"""
    client = disabled_client
    pid, rid, _ = _seed_same_state(client)
    before = _domain_snapshot(pid)

    import app.services.editor_state_revision_comparison_service as cmp_svc

    def _boom(*_a, **_k):
        raise RuntimeError(f"LEAK_{_SECRET}_PATH_{_PATH_MARKER}")

    monkeypatch.setattr(cmp_svc, "summarize_canonical_snapshot", _boom)
    res = client.get(_cmp_url(pid, rid))
    _assert_fixed_error(res, 500, _CODE_COMPARISON_FAILED)
    assert res.json()["detail"]["message"] == _MSG_COMPARISON_FAILED
    assert _domain_snapshot(pid) == before
