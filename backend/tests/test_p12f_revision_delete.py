"""
模块：P12F-G-A 单条修订删除后端专项测试
用途：真实 HTTP+SQLite 验收 DELETE .../editor-state-revisions/{revisionId}
  的空 query/body、204/no-store、作用域 404、权限/CSRF、SQL 三谓词、
  execute/flush/commit 回滚、五域零副作用与读取链兼容。
对接：DELETE /api/projects/{projectId}/editor-state-revisions/{revisionId}；
  editor_state_revision_delete_service；api.editor_state_revisions。
二次开发：
  - 禁止 mock 路由返回、宽泛状态码、恒真断言、固定 sleep、吞异常、skip/xfail；
  - 最终绿测不得保留 failure-first 405/404 能力缺失分支；
  - 成功/失败均断言数据库，不得只断言 HTTP。
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
from sqlalchemy.orm import Session

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
from app.services import editor_state_revision_delete_service as delete_svc

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fga"
_SECRET = "SECRET_P12FGA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/delete"
_INJECT_EXECUTE = "p12fga_injected_execute_failure"
_INJECT_FLUSH = "p12fga_injected_flush_failure"
_INJECT_COMMIT = "p12fga_injected_commit_failure"

_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS = frozenset(
    {"revisionId", "stateVersion", "snapshotBytes", "sourceKind", "createdAt"}
)

_CODE_REQUEST_INVALID = "editor_state_revision_delete_request_invalid"
_MSG_REQUEST_INVALID = "修订删除请求无效"
_CODE_NOT_FOUND = "editor_state_revision_not_found"
_MSG_NOT_FOUND = "修订记录不存在或不可访问"
_CODE_PROJECT = "project_not_found"
_MSG_PROJECT = "项目不存在或不可访问"
_CODE_DELETE_FAILED = "editor_state_revision_delete_failed"
_MSG_DELETE_FAILED = "修订记录删除失败，请稍后重试"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_CODE_CSRF_INVALID = "csrf_invalid"

_OWNER_USER = "admin_p12fga_owner"
_OWNER_PASS = "TestPass-P12FGA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12FGA-Writer-0001!",
    "finance": "TestPass-P12FGA-Finance-0001!",
    "hr": "TestPass-P12FGA-Hr-0001!",
    "bidder": "TestPass-P12FGA-Bidder-0001!",
}

_DELETE_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_delete_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_revisions.py"
)
_ENTITIES_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "models" / "entities.py"
)


# ---------- fixtures ----------


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


# ---------- helpers ----------


def _delete_url(project_id: str, revision_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/{revision_id}"


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions"


def _page_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/page"


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/search"


def _detail_url(project_id: str, revision_id: str) -> str:
    return _delete_url(project_id, revision_id)


def _comparison_url(project_id: str, revision_id: str) -> str:
    return f"{_delete_url(project_id, revision_id)}/comparison"


def _body_diff_url(project_id: str, revision_id: str) -> str:
    return f"{_delete_url(project_id, revision_id)}/body-diff"


def _pair_body_diff_url(
    project_id: str, before_id: str, after_id: str
) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{before_id}/body-diff/{after_id}"
    )


def _restore_url(project_id: str, revision_id: str) -> str:
    return f"{_delete_url(project_id, revision_id)}/restore"


def _create_project(
    client: TestClient,
    name: str = "P12F-G-A项目",
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
                "content": f"正文{tag}-{_SECRET}",
            }
        ],
        guidance=f"指引-{tag}",
        parsedMarkdown=f"md-{tag}-{_SECRET}",
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
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    if message is not None:
        assert detail["message"] == message
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    assert "SELECT" not in blob
    assert "INSERT" not in blob
    assert "editor_state_revisions" not in blob
    assert "editor_state_revision_delete_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "IntegrityError" not in blob
    assert _INJECT_EXECUTE not in blob
    assert _INJECT_FLUSH not in blob
    assert _INJECT_COMMIT not in blob
    if forbid_echo is not None and forbid_echo != "":
        assert forbid_echo not in blob


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
    username = f"user_{role}_p12fga{'_own' if is_owner else ''}_{secrets.token_hex(3)}"
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


def _seed_revisions(
    project_id: str,
    tags: list[str],
    *,
    source_kind: str = "browser_put",
    base_time: datetime | None = None,
    workspace_id: str = _WS,
) -> list[dict]:
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, tag in enumerate(tags):
        state = _variant(tag)
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
            created_at=base + timedelta(seconds=i),
            workspace_id=workspace_id,
        )
    return _db_rev_rows(project_id, workspace_id=workspace_id)


def _insert_corrupt_revision(
    project_id: str,
    *,
    revision_id: str | None = None,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
) -> str:
    rid = revision_id or ("esr_" + secrets.token_hex(16))
    # 损坏 JSON：仍满足非空与字节上限，但无法解析为合法快照
    bad_json = '{"broken": true, "secret": "' + _SECRET + '"'
    _insert_raw_revision(
        project_id=project_id,
        revision_id=rid,
        snapshot_json=bad_json,
        state_version="esv_" + ("a" * 32),
        snapshot_bytes=len(bad_json.encode("utf-8")),
        source_kind="browser_put",
        created_at=created_at or datetime(2026, 7, 2, tzinfo=timezone.utc),
        workspace_id=workspace_id,
    )
    return rid


def _insert_checkpoint(project_id: str, tag: str = "cp0") -> str:
    state = _variant(tag)
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    cid = "escp_" + secrets.token_hex(16)
    chapters = snap.get("chapters") or []
    chapter_n = len(chapters) if isinstance(chapters, list) else 0
    db = SessionLocal()
    try:
        db.add(
            EditorStateCheckpointRow(
                id=cid,
                workspace_id=_WS,
                project_id=project_id,
                snapshot_json=snap_json,
                state_version=state["stateVersion"],
                snapshot_bytes=len(snap_json.encode("utf-8")),
                outline_node_count=0,
                chapter_count=chapter_n,
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    return cid


def _insert_task(project_id: str, *, tag: str = "t0") -> str:
    """用途：写入真实合成 ProjectTaskRow，纳入五域快照。"""
    tid = "task_" + secrets.token_hex(12)
    db = SessionLocal()
    try:
        db.add(
            ProjectTaskRow(
                id=tid,
                project_id=project_id,
                type="parse",
                status="pending",
                progress=0,
                message=f"合成任务-{tag}",
                payload_json=json.dumps({"tag": tag}, ensure_ascii=False),
                result_json=None,
                error=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    return tid


def _ensure_editor_state(project_id: str, tag: str = "cur") -> dict:
    """用途：写入合法 13 键当前态；guidance 使用对象以兼容 EditorStateOut。"""
    state = _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "content": f"正文{tag}-{_SECRET}",
            }
        ],
        guidance={"note": f"指引-{tag}"},
        parsedMarkdown=f"md-{tag}-{_SECRET}",
    )
    snap = editor_state_service.extract_canonical_snapshot(state)
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        payload = {
            "outline_json": json.dumps(snap["outline"], ensure_ascii=False),
            "chapters_json": json.dumps(snap["chapters"], ensure_ascii=False),
            "facts_json": json.dumps(snap["facts"], ensure_ascii=False),
            "mode": snap["mode"],
            "analysis_json": json.dumps(snap["analysis"], ensure_ascii=False),
            "response_matrix_json": json.dumps(
                snap["responseMatrix"], ensure_ascii=False
            ),
            "guidance_json": json.dumps(snap["guidance"], ensure_ascii=False),
            "parsed_markdown": snap["parsedMarkdown"],
            "business_json": json.dumps(
                {
                    "qualify": snap["businessQualify"],
                    "toc": snap["businessToc"],
                    "quote": snap["businessQuote"],
                    "commit": snap["businessCommit"],
                },
                ensure_ascii=False,
            ),
            "analysis_overview": snap["analysisOverview"],
        }
        if row is None:
            db.add(ProjectEditorStateRow(project_id=project_id, **payload))
        else:
            for k, v in payload.items():
                setattr(row, k, v)
        db.commit()
    finally:
        db.close()
    return state


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FGA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fga",
                )
            )
            db.commit()
    finally:
        db.close()


def _insert_foreign_project(
    *,
    project_id: str,
    workspace_id: str,
    name: str = "外域项目",
) -> None:
    _ensure_workspace(workspace_id)
    db = SessionLocal()
    try:
        if db.get(Project, project_id) is None:
            db.add(
                Project(
                    id=project_id,
                    workspace_id=workspace_id,
                    name=name,
                    kind="technical",
                    status="draft",
                )
            )
            db.commit()
    finally:
        db.close()


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


def _db_task_rows(project_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .order_by(
                ProjectTaskRow.created_at.desc(),
                ProjectTaskRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "project_id": r.project_id,
                "type": r.type,
                "status": r.status,
                "progress": int(r.progress),
                "message": r.message,
                "payload_json": r.payload_json,
                "result_json": r.result_json,
                "error": r.error,
                "created_at": r.created_at.isoformat()
                if hasattr(r.created_at, "isoformat")
                else str(r.created_at),
                "updated_at": r.updated_at.isoformat()
                if hasattr(r.updated_at, "isoformat")
                else str(r.updated_at),
            }
            for r in rows
        ]
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


def _domain_snapshot(project_id: str, *, include_audits: bool = False) -> dict:
    """
    用途：五域快照——修订/检查点/当前态/项目/任务。
    二次开发：权限登录会写审计；断言删除零副作用时默认排除 audits。
    """
    snap = {
        "revisions": _db_rev_rows(project_id),
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "tasks": _db_task_rows(project_id),
    }
    if include_audits:
        snap["audits"] = _db_audit_rows()
    return snap


def _seed_five_domain(project_id: str, tags: list[str]) -> list[dict]:
    """用途：写入当前态/检查点/任务/多修订，供五域不变性断言。"""
    _ensure_editor_state(project_id, "cur")
    _insert_checkpoint(project_id, "cp_keep")
    _insert_task(project_id, tag="keep")
    return _seed_revisions(project_id, tags)


def _delete(
    client: TestClient,
    project_id: str,
    revision_id: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    content: bytes | None = None,
    json_body: object | None = None,
):
    kwargs: dict = {"headers": headers or {}}
    if params is not None:
        kwargs["params"] = params
    if content is not None:
        kwargs["content"] = content
    if json_body is not None:
        kwargs["json"] = json_body
    return client.request(
        "DELETE",
        _delete_url(project_id, revision_id),
        **kwargs,
    )


def _assert_success_204(res) -> None:
    assert res.status_code == 204, res.text
    _assert_no_store(res)
    assert res.content == b""
    assert res.text == ""
    # 禁止回显 ID/版本/计数/正文
    blob = res.text
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "snapshot" not in blob
    assert _SECRET not in blob


def _normalize_sql_params(params) -> list[object]:
    """用途：将 SQLAlchemy 参数统一为可精确比较的值列表。"""
    if params is None:
        return []
    if isinstance(params, dict):
        return list(params.values())
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params]


def _put_editor_state(client: TestClient, project_id: str, tag: str) -> dict:
    """用途：真实 transition 写入当前态并记账（browser_put）。"""
    state = _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "content": f"正文{tag}-{_SECRET}",
            }
        ],
        guidance={"note": f"指引-{tag}"},
        parsedMarkdown=f"md-{tag}-{_SECRET}",
    )
    got = client.get(f"/api/projects/{project_id}/editor-state")
    body = {
        "outline": state["outline"],
        "chapters": state["chapters"],
        "facts": state["facts"],
        "mode": state["mode"],
        "analysis": state["analysis"],
        "responseMatrix": state["responseMatrix"],
        "guidance": state["guidance"],
        "parsedMarkdown": state["parsedMarkdown"],
        "businessQualify": state["businessQualify"],
        "businessToc": state["businessToc"],
        "businessQuote": state["businessQuote"],
        "businessCommit": state["businessCommit"],
        "analysisOverview": state["analysisOverview"],
    }
    if got.status_code == 200:
        cur = got.json()
        if cur.get("stateVersion"):
            body["expectedStateVersion"] = cur["stateVersion"]
    res = client.put(f"/api/projects/{project_id}/editor-state", json=body)
    assert res.status_code == 200, res.text
    return res.json()


# ---------- 成功路径 ----------


def test_delete_success_204_only_target_row(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="成功删除目标行")
    rows = _seed_five_domain(pid, ["old", "mid", "new"])
    assert len(rows) == 3
    target = rows[1]  # 中间
    keep_ids = {rows[0]["id"], rows[2]["id"]}
    before = _domain_snapshot(pid)
    expected_revs = [r for r in before["revisions"] if r["id"] != target["id"]]

    res = _delete(client, pid, target["id"])
    _assert_success_204(res)

    after = _domain_snapshot(pid)
    after_ids = {r["id"] for r in after["revisions"]}
    assert target["id"] not in after_ids
    assert after_ids == keep_ids
    assert after["revisions"] == expected_revs
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    assert before["tasks"], "五域必须含真实任务行"


@pytest.mark.parametrize("position", ["newest", "middle", "oldest", "corrupt"])
def test_delete_positions_and_corrupt_then_second_404(disabled_client, position):
    client = disabled_client
    pid = _create_project(client, name=f"删除位置{position}")
    rows = _seed_revisions(pid, ["a", "b", "c"])
    corrupt_id = _insert_corrupt_revision(
        pid,
        created_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
    )
    all_rows = _db_rev_rows(pid)
    by_id = {r["id"]: r for r in all_rows}
    if position == "newest":
        target_id = all_rows[0]["id"]
    elif position == "middle":
        target_id = all_rows[1]["id"]
    elif position == "oldest":
        target_id = all_rows[-1]["id"]
    else:
        target_id = corrupt_id
        assert target_id in by_id

    res = _delete(client, pid, target_id)
    _assert_success_204(res)
    assert target_id not in {r["id"] for r in _db_rev_rows(pid)}

    res2 = _delete(client, pid, target_id)
    _assert_fixed_error(
        res2, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=target_id
    )


# ---------- 404 隔离 ----------


def test_delete_project_missing_and_cross_scope_404(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="本域项目")
    rows = _seed_five_domain(pid, ["x", "y"])
    target = rows[0]["id"]
    before = _domain_snapshot(pid)

    # 项目缺失
    missing_pid = "proj_missing_" + secrets.token_hex(4)
    res = _delete(client, missing_pid, target)
    _assert_fixed_error(
        res, 404, _CODE_PROJECT, message=_MSG_PROJECT, forbid_echo=missing_pid
    )

    # 跨 workspace：外域项目 + 同 ID 修订
    foreign_pid = "proj_foreign_" + secrets.token_hex(4)
    _insert_foreign_project(project_id=foreign_pid, workspace_id=_WS_OTHER)
    foreign_rev = "esr_" + secrets.token_hex(16)
    state = _variant("foreign")
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    _insert_raw_revision(
        project_id=foreign_pid,
        revision_id=foreign_rev,
        snapshot_json=snap_json,
        state_version=state["stateVersion"],
        snapshot_bytes=len(snap_json.encode("utf-8")),
        source_kind="browser_put",
        workspace_id=_WS_OTHER,
    )
    # 用当前 workspace 访问外域 project → project 404
    res_fp = _delete(client, foreign_pid, foreign_rev)
    _assert_fixed_error(res_fp, 404, _CODE_PROJECT, message=_MSG_PROJECT)

    # 跨 project：B 项目的 revision 用 A 项目路径
    pid_b = _create_project(client, name="兄弟项目")
    rows_b = _seed_revisions(pid_b, ["b0"])
    res_cross = _delete(client, pid, rows_b[0]["id"])
    _assert_fixed_error(
        res_cross, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=rows_b[0]["id"]
    )

    # 任意格式 ID
    for bad_id in ("not-a-revision", "esr_short", "ESR_" + ("f" * 32), target.upper()):
        res_bad = _delete(client, pid, bad_id)
        _assert_fixed_error(
            res_bad, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=bad_id
        )

    # 外域 revision 行必须仍在；本域五域精确不变
    assert any(
        r["id"] == foreign_rev
        for r in _db_rev_rows(foreign_pid, workspace_id=_WS_OTHER)
    )
    assert _domain_snapshot(pid) == before
    assert _db_rev_rows(pid_b)[0]["id"] == rows_b[0]["id"]


# ---------- query/body 422 ----------


def test_delete_query_and_body_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="非法请求体")
    rows = _seed_five_domain(pid, ["q0", "q1"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    cases = [
        {"params": {"force": "1"}, "content": None, "json_body": None, "echo": "force"},
        {"params": {"revisionId": rid}, "content": None, "json_body": None, "echo": rid},
        {
            "params": None,
            "content": b'{"BODYMARKER_P12FGA":true}',
            "json_body": None,
            "echo": "BODYMARKER_P12FGA",
        },
        {"params": None, "content": b"null", "json_body": None, "echo": "null"},
        {"params": None, "content": b"{}", "json_body": None, "echo": None},
        {
            "params": None,
            "content": f'{{"secret":"{_SECRET}"}}'.encode("utf-8"),
            "json_body": None,
            "echo": _SECRET,
        },
        {
            "params": None,
            "content": b"plain-text-body",
            "json_body": None,
            "echo": "plain-text-body",
        },
        {
            "params": None,
            "content": None,
            "json_body": {"x": 1, "marker": "JSONBODY_P12FGA"},
            "echo": "JSONBODY_P12FGA",
        },
        {
            "params": None,
            "content": None,
            "json_body": {},
            "echo": None,
        },
    ]
    for case in cases:
        captured: list[tuple[str, object]] = []
        commits_before = {"n": 0}

        def _capture(conn, cursor, statement, parameters, context, executemany):
            low = statement.lower()
            if "editor_state_revisions" in low and statement.lstrip().upper().startswith(
                "DELETE"
            ):
                captured.append((statement, parameters))

        def _count_commit(session):
            commits_before["n"] += 1

        event.listen(engine, "before_cursor_execute", _capture)
        event.listen(Session, "after_commit", _count_commit)
        try:
            res = _delete(
                client,
                pid,
                rid,
                params=case["params"],
                content=case["content"],
                json_body=case["json_body"],
            )
            _assert_fixed_error(
                res,
                422,
                _CODE_REQUEST_INVALID,
                message=_MSG_REQUEST_INVALID,
                forbid_echo=case["echo"],
            )
            assert captured == [], f"422 不得执行 revision DELETE: {captured}"
            assert commits_before["n"] == 0, "422 不得 commit"
            assert _domain_snapshot(pid) == before
        finally:
            event.remove(engine, "before_cursor_execute", _capture)
            event.remove(Session, "after_commit", _count_commit)


# ---------- required 权限 ----------


def test_delete_required_roles_csrf_and_owner_no_bypass(required_client):
    client = required_client
    csrf_w = _login_role(client, "bid_writer")
    headers_w = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="权限项目", headers=headers_w)
    rows = _seed_five_domain(pid, ["auth0", "auth1"])
    rid_ok = rows[0]["id"]
    rid_deny = rows[1]["id"]
    before = _domain_snapshot(pid)

    # 未登录：固定 401；中间件未必附 no-store，仍需五域不变
    client.cookies.clear()
    res_anon = _delete(client, pid, rid_deny)
    assert res_anon.status_code == 401, res_anon.text
    assert res_anon.status_code != 204
    assert _domain_snapshot(pid) == before

    def _assert_auth_deny(res, status: int, code: str) -> None:
        """用途：中间件权限/CSRF 固定 status+code；不强制 no-store（中间件未必附带）。"""
        assert res.status_code == status, res.text
        assert res.status_code != 204
        detail = res.json().get("detail")
        assert isinstance(detail, dict), res.text
        assert detail.get("code") == code
        assert type(detail.get("message")) is str and detail["message"] != ""

    # finance/hr/bidder 403
    for role in ("finance", "hr", "bidder"):
        csrf = _login_role(client, role)
        res = _delete(client, pid, rid_deny, headers={"X-CSRF-Token": csrf})
        _assert_auth_deny(res, 403, _CODE_ROLE_FORBIDDEN)
        assert _domain_snapshot(pid) == before

    # owner 但非 bid_writer 不旁路
    csrf_own_fin = _login_role(client, "finance", is_owner=True)
    res_own = _delete(
        client, pid, rid_deny, headers={"X-CSRF-Token": csrf_own_fin}
    )
    _assert_auth_deny(res_own, 403, _CODE_ROLE_FORBIDDEN)
    assert _domain_snapshot(pid) == before

    # bid_writer 缺 CSRF
    csrf_w2 = _login_role(client, "bid_writer")
    res_no_csrf = _delete(client, pid, rid_deny)
    _assert_auth_deny(res_no_csrf, 403, _CODE_CSRF_INVALID)
    assert _domain_snapshot(pid) == before

    # bid_writer 错 CSRF
    res_bad_csrf = _delete(
        client, pid, rid_deny, headers={"X-CSRF-Token": "definitely-wrong-csrf"}
    )
    _assert_auth_deny(res_bad_csrf, 403, _CODE_CSRF_INVALID)
    assert _domain_snapshot(pid) == before

    # 合法 Cookie + CSRF 唯一成功；仅删指定行
    res_ok = _delete(client, pid, rid_ok, headers={"X-CSRF-Token": csrf_w2})
    _assert_success_204(res_ok)
    after = _domain_snapshot(pid)
    assert rid_ok not in {r["id"] for r in after["revisions"]}
    assert rid_deny in {r["id"] for r in after["revisions"]}
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]


# ---------- SQL 证据 ----------


def test_delete_sql_project_id_projection_and_triple_predicate(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="SQL三谓词")
    rows = _seed_five_domain(pid, ["s0", "s1"])
    rid = rows[0]["id"]
    before_other = {
        "checkpoints": _db_cp_rows(pid),
        "editor_state": _db_editor_state_row(pid),
        "project": _db_project_row(pid),
        "tasks": _db_task_rows(pid),
    }

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "projects" in low or "editor_state_revisions" in low:
            captured.append((statement, parameters))
        # 删除路径不得触碰当前态/检查点/任务表
        forbidden_tables = (
            "project_editor_states",
            "editor_state_checkpoints",
            "project_tasks",
        )
        if any(t in low for t in forbidden_tables):
            captured.append((f"FORBIDDEN::{statement}", parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _delete(client, pid, rid)
        _assert_success_204(res)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    forbidden = [c for c in captured if str(c[0]).startswith("FORBIDDEN::")]
    assert forbidden == [], f"删除触及禁区表: {forbidden}"

    project_selects = [
        (s, p)
        for s, p in captured
        if "projects" in s.lower()
        and s.lstrip().upper().startswith("SELECT")
        and "editor_state_revisions" not in s.lower()
    ]
    assert len(project_selects) == 1, project_selects
    sql_p, params_p = project_selects[0]
    low_p = " ".join(sql_p.lower().split())
    # 投影严格只有 id（允许 projects.id / AS 别名）
    assert re.search(
        r"select\s+(?:projects\.)?id(?:\s+as\s+\w+)?\s+from\s+projects",
        low_p,
    ), sql_p
    assert "snapshot" not in low_p
    # 不得投影 name 等无关列（WHERE 中的 workspace_id 允许）
    assert not re.search(r"select\s+.*\bname\b.*\bfrom\s+projects", low_p), sql_p
    assert "workspace_id" in low_p
    # WHERE 仅 workspace_id + id 两谓词；参数规范化后精确两值，禁止额外参数
    assert re.search(
        r"where\b.+\b(?:projects\.)?id\b.+\b(?:projects\.)?workspace_id\b"
        r"|"
        r"where\b.+\b(?:projects\.)?workspace_id\b.+\b(?:projects\.)?id\b",
        low_p,
    ), sql_p
    # 不得出现第三业务谓词（如 name / mode 过滤）
    assert not re.search(
        r"where\b.+\b(?:name|mode|snapshot)\b",
        low_p,
    ), sql_p
    pvals = [str(v) for v in _normalize_sql_params(params_p)]
    assert sorted(pvals) == sorted([_WS, pid]), pvals
    assert len(pvals) == 2

    deletes = [
        (s, p)
        for s, p in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("DELETE")
    ]
    assert len(deletes) == 1, deletes
    sql_d, params_d = deletes[0]
    low_d = sql_d.lower()
    assert "snapshot_json" not in low_d
    assert "workspace_id" in low_d
    assert "project_id" in low_d
    assert re.search(r"\bid\b", low_d)
    dvals = [str(v) for v in _normalize_sql_params(params_d)]
    assert sorted(dvals) == sorted([_WS, pid, rid]), dvals

    # 源码禁区：可执行路径不得读取 snapshot/相关模型
    svc = _DELETE_SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(svc)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert "snapshot_json" not in names
    assert "ProjectEditorStateRow" not in names
    assert "EditorStateCheckpointRow" not in names
    assert "ProjectTaskRow" not in names
    assert "db.get(EditorStateRevisionRow" not in svc
    assert "session.get(EditorStateRevisionRow" not in svc

    after = _domain_snapshot(pid)
    assert after["checkpoints"] == before_other["checkpoints"]
    assert after["editor_state"] == before_other["editor_state"]
    assert after["project"] == before_other["project"]
    assert after["tasks"] == before_other["tasks"]


# ---------- 故障回滚与 rowcount ----------


def test_delete_execute_flush_commit_failures_rollback(disabled_client):
    """
    用途：execute/flush/commit 三类故障均 rollback，目标行与五域保留，固定 500。
    二次开发：直接调用服务层注入 Session 方法；并用独立 Session 复核持久化。
    """
    client = disabled_client
    pid = _create_project(client, name="故障回滚")
    rows = _seed_five_domain(pid, ["f0", "f1", "f2"])
    targets = [rows[0]["id"], rows[1]["id"], rows[2]["id"]]
    before = _domain_snapshot(pid)

    def _run_fault(kind: str, revision_id: str) -> dict:
        db = SessionLocal()
        try:
            real_execute = db.execute
            real_flush = db.flush
            real_commit = db.commit
            real_rollback = db.rollback
            counters = {
                "execute": 0,
                "flush": 0,
                "commit": 0,
                "rollback": 0,
                "order": [],
            }
            # 故障路径只真实包裹 execute/flush；不得声明未包裹的 query/refresh 恒零
            post_commit = {"execute": 0, "flush": 0}

            def _exec(*a, **k):
                counters["execute"] += 1
                counters["order"].append("execute")
                if counters["commit"] > 0:
                    post_commit["execute"] += 1
                sql = ""
                if a:
                    sql = str(a[0])
                low = sql.lower()
                if (
                    kind == "execute"
                    and "editor_state_revisions" in low
                    and "delete" in low
                ):
                    raise RuntimeError(_INJECT_EXECUTE)
                return real_execute(*a, **k)

            def _flush(*a, **k):
                counters["flush"] += 1
                counters["order"].append("flush")
                if counters["commit"] > 0:
                    post_commit["flush"] += 1
                if kind == "flush":
                    raise RuntimeError(_INJECT_FLUSH)
                return real_flush(*a, **k)

            def _commit(*a, **k):
                counters["commit"] += 1
                counters["order"].append("commit")
                if kind == "commit":
                    raise RuntimeError(_INJECT_COMMIT)
                return real_commit(*a, **k)

            def _rollback(*a, **k):
                counters["rollback"] += 1
                counters["order"].append("rollback")
                return real_rollback(*a, **k)

            db.execute = _exec  # type: ignore[method-assign]
            db.flush = _flush  # type: ignore[method-assign]
            db.commit = _commit  # type: ignore[method-assign]
            db.rollback = _rollback  # type: ignore[method-assign]

            with pytest.raises(delete_svc.EditorStateRevisionDeleteError) as ei:
                delete_svc.delete_editor_state_revision(db, _WS, pid, revision_id)
            exc = ei.value
            assert exc.status_code == 500
            assert exc.code == _CODE_DELETE_FAILED
            assert exc.message == _MSG_DELETE_FAILED
            assert _INJECT_EXECUTE not in str(exc)
            assert _INJECT_FLUSH not in str(exc)
            assert _INJECT_COMMIT not in str(exc)

            if kind == "execute":
                assert counters["execute"] == 2
                assert counters["flush"] == 0
                assert counters["commit"] == 0
                assert counters["rollback"] == 1
                assert counters["order"] == ["execute", "execute", "rollback"]
            elif kind == "flush":
                assert counters["execute"] == 2
                assert counters["flush"] == 1
                assert counters["commit"] == 0
                assert counters["rollback"] == 1
                assert counters["order"] == [
                    "execute",
                    "execute",
                    "flush",
                    "rollback",
                ]
            else:
                assert counters["execute"] == 2
                assert counters["flush"] == 1
                assert counters["commit"] == 1
                assert counters["rollback"] == 1
                assert counters["order"] == [
                    "execute",
                    "execute",
                    "flush",
                    "commit",
                    "rollback",
                ]
            assert post_commit == {
                "execute": 0,
                "flush": 0,
            }
            return counters
        finally:
            db.close()

    _run_fault("execute", targets[0])
    assert _domain_snapshot(pid) == before
    _run_fault("flush", targets[1])
    assert _domain_snapshot(pid) == before
    _run_fault("commit", targets[2])
    assert _domain_snapshot(pid) == before

    # 成功服务路径：恰好 execute 两次、flush 一次、commit 一次、rollback 零次；
    # commit 后无 execute/flush/refresh/query（query 必须真实包裹，禁止恒零假证据）
    db_ok = SessionLocal()
    try:
        real_execute = db_ok.execute
        real_flush = db_ok.flush
        real_commit = db_ok.commit
        real_rollback = db_ok.rollback
        real_refresh = getattr(db_ok, "refresh", None)
        real_query = db_ok.query
        counters = {"execute": 0, "flush": 0, "commit": 0, "rollback": 0}
        post_commit = {"execute": 0, "flush": 0, "refresh": 0, "query": 0}
        committed = {"done": False}

        def _exec(*a, **k):
            counters["execute"] += 1
            if committed["done"]:
                post_commit["execute"] += 1
            return real_execute(*a, **k)

        def _flush(*a, **k):
            counters["flush"] += 1
            if committed["done"]:
                post_commit["flush"] += 1
            return real_flush(*a, **k)

        def _commit(*a, **k):
            counters["commit"] += 1
            out = real_commit(*a, **k)
            committed["done"] = True
            return out

        def _rollback(*a, **k):
            counters["rollback"] += 1
            return real_rollback(*a, **k)

        def _refresh(*a, **k):
            if committed["done"]:
                post_commit["refresh"] += 1
            if real_refresh is None:
                return None
            return real_refresh(*a, **k)

        def _query(*a, **k):
            # 真实包裹 Session.query：commit 后若调用则递增，杜绝恒零假证据
            if committed["done"]:
                post_commit["query"] += 1
            return real_query(*a, **k)

        db_ok.execute = _exec  # type: ignore[method-assign]
        db_ok.flush = _flush  # type: ignore[method-assign]
        db_ok.commit = _commit  # type: ignore[method-assign]
        db_ok.rollback = _rollback  # type: ignore[method-assign]
        db_ok.query = _query  # type: ignore[method-assign]
        if real_refresh is not None:
            db_ok.refresh = _refresh  # type: ignore[method-assign]

        delete_svc.delete_editor_state_revision(db_ok, _WS, pid, targets[0])
        assert counters == {
            "execute": 2,
            "flush": 1,
            "commit": 1,
            "rollback": 0,
        }
        assert post_commit == {
            "execute": 0,
            "flush": 0,
            "refresh": 0,
            "query": 0,
        }
    finally:
        db_ok.close()

    assert targets[0] not in {r["id"] for r in _db_rev_rows(pid)}
    assert targets[1] in {r["id"] for r in _db_rev_rows(pid)}
    assert targets[2] in {r["id"] for r in _db_rev_rows(pid)}


def test_delete_rowcount_none_negative_multi_fixed_500(disabled_client):
    """用途：可控 rowcount 直接证据，覆盖 0/None/-1/2/1，不依赖 SQLite 只给 0/1。"""
    client = disabled_client
    pid = _create_project(client, name="rowcount控制")
    rows = _seed_five_domain(pid, ["rc0", "rc1", "rc2", "rc3", "rc4"])
    targets = [r["id"] for r in rows]
    before = _domain_snapshot(pid)

    def _run_with_rowcount(forced, revision_id: str):
        db = SessionLocal()
        try:
            real_execute = db.execute
            real_rollback = db.rollback
            counters = {"rollback": 0, "commit": 0}

            class _ForcedResult:
                def __init__(self, rowcount):
                    self.rowcount = rowcount

            def _exec(*a, **k):
                result = real_execute(*a, **k)
                sql = str(a[0]).lower() if a else ""
                if "editor_state_revisions" in sql and "delete" in sql:
                    return _ForcedResult(forced)
                return result

            def _commit(*a, **k):
                counters["commit"] += 1
                return db.__class__.commit(db, *a, **k)

            def _rollback(*a, **k):
                counters["rollback"] += 1
                return real_rollback(*a, **k)

            db.execute = _exec  # type: ignore[method-assign]
            db.commit = _commit  # type: ignore[method-assign]
            db.rollback = _rollback  # type: ignore[method-assign]

            if forced == 1:
                delete_svc.delete_editor_state_revision(db, _WS, pid, revision_id)
                assert counters["commit"] == 1
                assert counters["rollback"] == 0
                return None

            with pytest.raises(delete_svc.EditorStateRevisionDeleteError) as ei:
                delete_svc.delete_editor_state_revision(db, _WS, pid, revision_id)
            exc = ei.value
            if forced == 0:
                assert exc.status_code == 404
                assert exc.code == _CODE_NOT_FOUND
                assert exc.message == _MSG_NOT_FOUND
            else:
                assert exc.status_code == 500
                assert exc.code == _CODE_DELETE_FAILED
                assert exc.message == _MSG_DELETE_FAILED
            assert counters["rollback"] == 1
            assert counters["commit"] == 0
            return exc
        finally:
            db.close()

    # 0 → revision 404 + rollback，五域恢复
    _run_with_rowcount(0, targets[0])
    assert _domain_snapshot(pid) == before

    # None/-1/2 → delete_failed 500 + rollback
    for forced, tid in ((None, targets[1]), (-1, targets[2]), (2, targets[3])):
        _run_with_rowcount(forced, tid)
        assert _domain_snapshot(pid) == before

    # 1 → 成功；仅目标消失
    _run_with_rowcount(1, targets[4])
    after = _domain_snapshot(pid)
    assert targets[4] not in {r["id"] for r in after["revisions"]}
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    for kept in targets[:4]:
        assert kept in {r["id"] for r in after["revisions"]}

    # HTTP 路径对真实缺失仍 404
    res = _delete(client, pid, targets[4])
    _assert_fixed_error(
        res, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=targets[4]
    )


# ---------- 读取链与后续 transition ----------


def test_delete_read_chain_and_next_transition_slot(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="读取链与空位")
    # 真实 PUT 建立连贯账本，使最新修订 == 当前态；再删中间行后下一次 transition 恰 +1
    _put_editor_state(client, pid, "r0")
    _put_editor_state(client, pid, "r1")
    _put_editor_state(client, pid, "r2")
    _put_editor_state(client, pid, "r3")
    _insert_checkpoint(pid, "cp_read")
    _insert_task(pid, tag="read")
    rows = _db_rev_rows(pid)
    assert len(rows) >= 4
    # 删中间行，保留最新（与当前态一致）
    target = rows[1]
    baseline_ids = [r["id"] for r in rows]
    expected_ids = [i for i in baseline_ids if i != target["id"]]
    # 搜索合同：删除前锁定「章节」命中的固定顺序；四条 seed 均应命中
    pre_search = client.post(_search_url(pid), json={"query": "章节"})
    assert pre_search.status_code == 200, pre_search.text
    pre_search_ids = [i["revisionId"] for i in pre_search.json()["items"]]
    assert len(pre_search_ids) == 4, pre_search_ids
    assert target["id"] in pre_search_ids
    # 精确 expected：集合与顺序均来自合同基线减目标，禁止子集/包含
    expected_search_ids = [i for i in pre_search_ids if i != target["id"]]
    assert len(expected_search_ids) == 3
    marker = f"SEARCH_MARK_{secrets.token_hex(3)}"
    before_other = {
        "checkpoints": _db_cp_rows(pid),
        "editor_state": _db_editor_state_row(pid),
        "project": _db_project_row(pid),
        "tasks": _db_task_rows(pid),
    }

    res = _delete(client, pid, target["id"])
    _assert_success_204(res)

    remaining = [r["id"] for r in _db_rev_rows(pid)]
    assert remaining == expected_ids
    assert _db_cp_rows(pid) == before_other["checkpoints"]
    assert _db_editor_state_row(pid) == before_other["editor_state"]
    assert _db_project_row(pid) == before_other["project"]
    assert _db_task_rows(pid) == before_other["tasks"]

    lst = client.get(_list_url(pid))
    assert lst.status_code == 200, lst.text
    list_ids = [i["revisionId"] for i in lst.json()["items"]]
    assert list_ids == expected_ids

    page = client.get(_page_url(pid))
    assert page.status_code == 200, page.text
    page_ids = [i["revisionId"] for i in page.json()["items"]]
    assert page_ids == expected_ids

    sres = client.post(_search_url(pid), json={"query": "章节"})
    assert sres.status_code == 200, sres.text
    search_ids = [i["revisionId"] for i in sres.json()["items"]]
    # 删除后搜索完整序列必须精确等于合同 expected（集合+顺序）
    assert search_ids == expected_search_ids, (search_ids, expected_search_ids)

    d = client.get(_detail_url(pid, target["id"]))
    _assert_fixed_error(d, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND)

    c = client.get(_comparison_url(pid, target["id"]))
    _assert_fixed_error(c, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND)

    b = client.get(_body_diff_url(pid, target["id"]))
    _assert_fixed_error(b, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND)

    keep = remaining[0]
    pair = client.get(_pair_body_diff_url(pid, target["id"], keep))
    _assert_fixed_error(pair, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND)

    cur = client.get(f"/api/projects/{pid}/editor-state")
    expected = cur.json().get("stateVersion") if cur.status_code == 200 else None
    body = {"expectedStateVersion": expected or ("esv_" + "0" * 32)}
    rst = client.post(_restore_url(pid, target["id"]), json=body)
    _assert_fixed_error(rst, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND)

    ok_detail = client.get(_detail_url(pid, keep))
    assert ok_detail.status_code == 200, ok_detail.text
    assert ok_detail.json()["revisionId"] == keep

    n_before = len(_db_rev_rows(pid))
    ids_before = {r["id"] for r in _db_rev_rows(pid)}
    put = _put_editor_state(client, pid, f"after_del_{marker}")
    assert put.get("stateVersion")
    after_rows = _db_rev_rows(pid)
    ids_after = {r["id"] for r in after_rows}
    assert len(after_rows) == n_before + 1
    new_ids = ids_after - ids_before
    assert len(new_ids) == 1
    new_id = next(iter(new_ids))
    assert new_id != target["id"]
    assert target["id"] not in ids_after
    assert after_rows[0]["id"] == new_id


# ---------- AST / 反假绿 ----------


def test_delete_ast_and_source_guards():
    """用途：源码与测试反假绿；实现与路由必须同时存在。"""
    api_src = _API_PATH.read_text(encoding="utf-8")
    idx_page = api_src.find("/{project_id}/editor-state-revisions/page")
    idx_search = api_src.find("/{project_id}/editor-state-revisions/search")
    idx_dynamic = api_src.find("/{project_id}/editor-state-revisions/{revision_id}")
    assert idx_page != -1 and idx_search != -1 and idx_dynamic != -1
    assert idx_page < idx_dynamic
    assert idx_search < idx_dynamic

    ent_src = _ENTITIES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(ent_src)
    class_node = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EditorStateRevisionRow":
            class_node = node
            break
    assert class_node is not None
    ann_fields = []
    for stmt in class_node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            ann_fields.append(stmt.target.id)
    assert ann_fields == [
        "id",
        "workspace_id",
        "project_id",
        "snapshot_json",
        "state_version",
        "snapshot_bytes",
        "source_kind",
        "created_at",
    ]

    assert _DELETE_SERVICE_PATH.exists()
    svc_src = _DELETE_SERVICE_PATH.read_text(encoding="utf-8")
    assert "except:" not in svc_src
    assert "except Exception:\n        pass" not in svc_src
    assert "str(exc)" not in svc_src
    assert "logger" not in svc_src
    assert "print(" not in svc_src
    assert "EditorStateCheckpointRow" not in svc_src
    assert "ProjectEditorStateRow" not in svc_src
    assert "snapshot_json" not in svc_src
    assert "int(result.rowcount or 0)" not in svc_src
    assert "int(result.rowcount or" not in svc_src
    assert "workspace_id" in svc_src
    assert "project_id" in svc_src
    assert "delete_editor_state_revision" in svc_src
    assert "editor_state_revision_delete_failed" in svc_src

    # 路由 AST：精确 DELETE decorator 路径 + 函数名同时存在
    api_tree = ast.parse(api_src)
    delete_funcs: list[ast.FunctionDef] = []
    for node in api_tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "delete_editor_state_revision":
            continue
        has_delete_decorator = False
        path_ok = False
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            attr = fn.attr if isinstance(fn, ast.Attribute) else ""
            if attr != "delete":
                continue
            has_delete_decorator = True
            for arg in dec.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value
                    == "/{project_id}/editor-state-revisions/{revision_id}"
                ):
                    path_ok = True
        assert has_delete_decorator
        assert path_ok
        delete_funcs.append(node)
    assert len(delete_funcs) == 1
    assert "editor_state_revision_delete_request_invalid" in api_src

    # 测试自身反假绿扫描
    test_src = Path(__file__).read_text(encoding="utf-8")
    test_tree = ast.parse(test_src)
    forbidden_calls = {"skip", "xfail", "failme"}
    for node in ast.walk(test_tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = ""
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            assert name not in forbidden_calls, f"禁止弱调用: {name}"
        if isinstance(node, ast.Assert) and isinstance(node.test, ast.BoolOp):
            if isinstance(node.test.op, ast.Or):
                raise AssertionError("禁止断言中的 OR 弱分支")
        if isinstance(node, ast.Compare) and any(
            isinstance(op, ast.In) for op in node.ops
        ):
            # 拒绝 status_code in (混有成功与失败)
            for comp in node.comparators:
                if not isinstance(comp, (ast.Tuple, ast.List)):
                    continue
                vals = []
                for elt in comp.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, int):
                        vals.append(elt.value)
                if not vals:
                    continue
                has_2xx = any(200 <= v < 300 for v in vals)
                has_err = any(v >= 400 for v in vals)
                if has_2xx and has_err:
                    raise AssertionError(f"禁止宽状态码集合: {vals}")
    # 拒绝 failure-first 实现缺失分支残留（拼接针，避免本断言自命中）
    bad_if = "if not " + "_DELETE_SERVICE_PATH.exists()"
    assert bad_if not in test_src
    bad_wide = "status_code in (" + "204, 405, 404, 500)"
    assert bad_wide not in test_src
    bad_ff = "status_code in (" + "405, 404)"
    assert bad_ff not in test_src


def test_delete_success_empty_body_strict_204(disabled_client):
    """用途：最终严格 204 冒烟；空 query/body，仅删目标。"""
    client = disabled_client
    pid = _create_project(client, name="严格204冒烟")
    rows = _seed_five_domain(pid, ["smoke"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)
    res = _delete(client, pid, rid)
    _assert_success_204(res)
    after = _domain_snapshot(pid)
    assert after["revisions"] == []
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
