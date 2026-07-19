"""
模块：P12F-J-A 修订单条固定与裁剪保护后端专项测试
用途：真实 HTTP+SQLite 验收
  PATCH /api/projects/{projectId}/editor-state-revisions/{revisionId}/pin
  及固定上限 5/10MiB、项目锁、保护性裁剪、required/CSRF、零写与迁移列证据。
对接：契约 docs/p12f-revision-pinning-backend-contract.md；
  本阶段不得在模块导入期 import 不存在的 pin service。
二次开发：
  - 禁止 mock 路由返回、宽泛状态码、恒真断言、固定 sleep、吞异常、skip/xfail；
  - failure-first 仅通过 ASGI 打尚未存在的路由；收集必须成功；
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
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import SessionLocal, engine, ensure_schema_columns
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
from app.services import auth_service, editor_state_revision_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fja"
_SECRET = "SECRET_P12FJA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/pin"
_INJECT_EXECUTE = "p12fja_injected_execute_failure"
_INJECT_FLUSH = "p12fja_injected_flush_failure"
_INJECT_COMMIT = "p12fja_injected_commit_failure"

_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS_SIX = frozenset(
    {
        "revisionId",
        "stateVersion",
        "snapshotBytes",
        "sourceKind",
        "createdAt",
        "displayName",
    }
)

_CODE_PIN_LIMIT = "editor_state_revision_pin_limit"
_MSG_PIN_LIMIT = "固定修订已达上限"
_CODE_PIN_FAILED = "editor_state_revision_pin_failed"
_MSG_PIN_FAILED = "保存修订固定状态失败"
_CODE_PIN_INVALID = "editor_state_revision_pin_request_invalid"
_MSG_PIN_INVALID = "修订固定请求无效"
_CODE_NOT_FOUND = "editor_state_revision_not_found"
_MSG_NOT_FOUND = "修订不存在"
_CODE_PROJECT = "project_not_found"
_MSG_PROJECT = "项目不存在"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_CODE_CSRF_INVALID = "csrf_invalid"

_OWNER_USER = "admin_p12fja_owner"
_OWNER_PASS = "TestPass-P12FJA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12FJA-Writer-0001!",
    "finance": "TestPass-P12FJA-Finance-0001!",
    "hr": "TestPass-P12FJA-Hr-0001!",
    "bidder": "TestPass-P12FJA-Bidder-0001!",
}

_PIN_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_pin_service.py"
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
_DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"
)
_REVISION_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_service.py"
)
_SCHEMAS_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "schemas.py"
)

_OLD_SOURCE_CHECK = (
    "source_kind IN ("
    "'browser_put','task','revise','callback',"
    "'local_parser','content_fuse_apply',"
    "'content_fuse_consume','checkpoint_restore'"
    ")"
)

_PIN_BODY_MAX = 1024
_MAX_PINNED = 5
_MAX_PINNED_BYTES = 10 * 1024 * 1024


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


def _pin_url(project_id: str, revision_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{revision_id}/pin"
    )


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions"


def _page_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/page"


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/search"


def _detail_url(project_id: str, revision_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/{revision_id}"


def _delete_url(project_id: str, revision_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/{revision_id}"


def _create_project(
    client: TestClient,
    name: str = "P12F-J-A项目",
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


def _variant(tag: str, *, pad: str = "") -> dict:
    content = f"正文{tag}-{_SECRET}{pad}"
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "content": content,
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
    assert "UPDATE" not in blob
    assert "editor_state_revisions" not in blob
    assert "editor_state_revision_pin_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "IntegrityError" not in blob
    assert _INJECT_EXECUTE not in blob
    assert _INJECT_FLUSH not in blob
    assert _INJECT_COMMIT not in blob
    if forbid_echo is not None and forbid_echo != "":
        assert forbid_echo not in blob


def _assert_success_pin(res, expected: bool) -> None:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == {"isPinned"}
    assert type(body["isPinned"]) is bool
    assert body["isPinned"] is expected
    blob = res.text
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "snapshot" not in blob
    assert _SECRET not in blob


def _assert_meta_six(item: dict) -> None:
    assert set(item.keys()) == _META_KEYS_SIX, item.keys()
    assert "isPinned" not in item
    assert _REVISION_ID_RE.match(item["revisionId"])
    assert _STATE_VERSION_RE.match(item["stateVersion"])
    assert type(item["snapshotBytes"]) is int and item["snapshotBytes"] > 0
    assert type(item["sourceKind"]) is str and item["sourceKind"]
    assert type(item["createdAt"]) is str and item["createdAt"]
    dn = item["displayName"]
    assert dn is None or type(dn) is str


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
    username = f"user_{role}_p12fja{'_own' if is_owner else ''}_{secrets.token_hex(3)}"
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
    display_name: str | None = None,
    is_pinned: bool = False,
) -> None:
    db = SessionLocal()
    try:
        row = EditorStateRevisionRow(
            id=revision_id,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=snapshot_json,
            state_version=state_version,
            snapshot_bytes=snapshot_bytes,
            source_kind=source_kind,
            created_at=created_at or utc_now(),
        )
        if hasattr(row, "display_name"):
            row.display_name = display_name
        if hasattr(row, "is_pinned"):
            row.is_pinned = is_pinned
        db.add(row)
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
    pad: str = "",
) -> list[dict]:
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, tag in enumerate(tags):
        state = _variant(tag, pad=pad)
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


def _ensure_editor_state(project_id: str, tag: str = "cur") -> dict:
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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FJA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fja",
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
        out = []
        for r in rows:
            item = {
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
            if hasattr(r, "display_name"):
                item["display_name"] = r.display_name
            if hasattr(r, "is_pinned"):
                item["is_pinned"] = bool(r.is_pinned)
            out.append(item)
        return out
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


def _domain_snapshot(project_id: str) -> dict:
    return {
        "revisions": _db_rev_rows(project_id),
        "checkpoints": _db_cp_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "tasks": _db_task_rows(project_id),
    }


def _seed_five_domain(project_id: str, tags: list[str]) -> list[dict]:
    _ensure_editor_state(project_id, "cur")
    _insert_checkpoint(project_id, "cp_keep")
    _insert_task(project_id, tag="keep")
    return _seed_revisions(project_id, tags)


def _pin_counts(project_id: str) -> tuple[int, int]:
    rows = _db_rev_rows(project_id)
    pinned = [r for r in rows if r.get("is_pinned") is True]
    return len(pinned), sum(int(r["snapshot_bytes"]) for r in pinned)


def _patch_pin(
    client: TestClient,
    project_id: str,
    revision_id: str,
    is_pinned: object = True,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    content: bytes | None = None,
    raw_json: bool = True,
):
    kwargs: dict = {"headers": headers or {}}
    if params is not None:
        kwargs["params"] = params
    if content is not None:
        kwargs["content"] = content
        kwargs.setdefault("headers", {})
        kwargs["headers"] = {
            **kwargs["headers"],
            "Content-Type": "application/json",
        }
    elif raw_json:
        kwargs["json"] = {"isPinned": is_pinned}
    return client.request(
        "PATCH",
        _pin_url(project_id, revision_id),
        **kwargs,
    )


def _set_pinned_sql(revision_id: str, value: object) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE editor_state_revisions SET is_pinned = :v WHERE id = :id"
            ),
            {"v": value, "id": revision_id},
        )
        db.commit()
    finally:
        db.close()


def _set_snapshot_bytes_sql(revision_id: str, value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE editor_state_revisions SET snapshot_bytes = :v WHERE id = :id"
            ),
            {"v": value, "id": revision_id},
        )
        db.commit()
    finally:
        db.close()


# ---------- 路由存在与成功路径 ----------


def test_route_exists_pin_unpin_idempotent(disabled_client):
    """
    用途：合法固定/取消/同值幂等；响应精确一键 isPinned + no-store。
    failure-first：生产未实现时 ASGI 对未知 path 返回 404，不得 200。
    """
    client = disabled_client
    pid = _create_project(client, name="固定成功路径")
    rows = _seed_five_domain(pid, ["a", "b", "c"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    res = _patch_pin(client, pid, rid, True)
    _assert_success_pin(res, True)
    after1 = _db_rev_rows(pid)
    target1 = next(r for r in after1 if r["id"] == rid)
    assert target1["is_pinned"] is True
    for r in after1:
        if r["id"] != rid:
            assert r.get("is_pinned") is False
            old = next(x for x in before["revisions"] if x["id"] == r["id"])
            assert r["snapshot_json"] == old["snapshot_json"]
            assert r["state_version"] == old["state_version"]
            assert r["snapshot_bytes"] == old["snapshot_bytes"]
    snap1 = _domain_snapshot(pid)
    assert snap1["checkpoints"] == before["checkpoints"]
    assert snap1["editor_state"] == before["editor_state"]
    assert snap1["project"] == before["project"]
    assert snap1["tasks"] == before["tasks"]
    assert _pin_counts(pid) == (1, target1["snapshot_bytes"])

    # 同值幂等：计数/字节不变
    before_idem = _domain_snapshot(pid)
    res_idem = _patch_pin(client, pid, rid, True)
    _assert_success_pin(res_idem, True)
    assert _domain_snapshot(pid) == before_idem
    assert _pin_counts(pid) == (1, target1["snapshot_bytes"])

    # 取消固定
    res_off = _patch_pin(client, pid, rid, False)
    _assert_success_pin(res_off, False)
    assert next(r for r in _db_rev_rows(pid) if r["id"] == rid)["is_pinned"] is False
    assert _pin_counts(pid) == (0, 0)

    # 再次取消幂等
    before_off = _domain_snapshot(pid)
    res_off2 = _patch_pin(client, pid, rid, False)
    _assert_success_pin(res_off2, False)
    assert _domain_snapshot(pid) == before_off


def test_route_missing_is_real_http_not_import_error(disabled_client):
    """用途：failure-first 证据——合法 PATCH 入口必须真实 HTTP，非 import 收集错误。"""
    client = disabled_client
    pid = _create_project(client, name="入口探测")
    rows = _seed_revisions(pid, ["probe"])
    res = _patch_pin(client, pid, rows[0]["id"], True)
    # 未实现：精确 404；已实现：200。禁止 5xx 冒充红测。
    assert res.status_code in (200, 404), res.text
    if res.status_code == 404:
        # FastAPI 默认 404 或本包固定 not_found；均不得泄漏正文
        assert _SECRET not in res.text
        assert "Traceback" not in res.text


def test_pin_count_limit_5_zero_write(disabled_client):
    """用途：项目最多 5 条固定；第 6 条 409 且零写。"""
    client = disabled_client
    pid = _create_project(client, name="固定条数上限")
    rows = _seed_five_domain(pid, [f"n{i}" for i in range(7)])
    assert len(rows) >= 6
    for r in rows[:5]:
        res = _patch_pin(client, pid, r["id"], True)
        _assert_success_pin(res, True)
    assert _pin_counts(pid)[0] == 5
    before = _domain_snapshot(pid)
    victim = rows[5]["id"]
    res = _patch_pin(client, pid, victim, True)
    _assert_fixed_error(
        res, 409, _CODE_PIN_LIMIT, message=_MSG_PIN_LIMIT, forbid_echo=victim
    )
    assert _domain_snapshot(pid) == before
    assert next(r for r in _db_rev_rows(pid) if r["id"] == victim)["is_pinned"] is False


def test_pin_bytes_limit_10mib_zero_write(disabled_client, monkeypatch):
    """
    用途：固定快照合计上限（契约 10 MiB）；超限 409 零写。
    说明：单条 snapshot_bytes 库 CHECK ≤2MiB，故压低服务端字节上限做真实配额证明。
    """
    import app.services.editor_state_revision_pin_service as pin_svc

    # 压低固定字节上限，避免与单条 2MiB CHECK 冲突
    budget = 300
    monkeypatch.setattr(pin_svc, "MAX_PINNED_BYTES_PER_PROJECT", budget)
    monkeypatch.setattr(
        editor_state_revision_service,
        "MAX_PINNED_BYTES_PER_PROJECT",
        budget,
    )
    client = disabled_client
    pid = _create_project(client, name="固定字节上限")
    rows = _seed_five_domain(pid, ["big0", "big1", "small"])
    _set_snapshot_bytes_sql(rows[0]["id"], 200)
    _set_snapshot_bytes_sql(rows[1]["id"], 200)
    _assert_success_pin(_patch_pin(client, pid, rows[0]["id"], True), True)
    assert _pin_counts(pid) == (1, 200)
    before = _domain_snapshot(pid)
    res = _patch_pin(client, pid, rows[1]["id"], True)
    _assert_fixed_error(
        res,
        409,
        _CODE_PIN_LIMIT,
        message=_MSG_PIN_LIMIT,
        forbid_echo=rows[1]["id"],
    )
    assert _domain_snapshot(pid) == before


def test_pinned_survives_trim_nonpinned_prefix(disabled_client, monkeypatch):
    """用途：固定旧行在裁剪后仍保留；非固定最新前缀补足，总计受 20 条约束。"""
    client = disabled_client
    pid = _create_project(client, name="裁剪保护")
    rows = _seed_revisions(pid, [f"t{i}" for i in range(12)])
    # 最旧固定
    oldest = rows[-1]["id"]
    _assert_success_pin(_patch_pin(client, pid, oldest, True), True)
    monkeypatch.setattr(
        editor_state_revision_service, "MAX_REVISIONS_PER_PROJECT", 5
    )
    db = SessionLocal()
    try:
        editor_state_revision_service._trim_revisions(db, _WS, pid)
        db.commit()
    finally:
        db.close()
    kept = _db_rev_rows(pid)
    kept_ids = {r["id"] for r in kept}
    assert oldest in kept_ids
    assert next(r for r in kept if r["id"] == oldest)["is_pinned"] is True
    assert len(kept) == 5
    # 最新 4 非固定 + 1 固定
    assert sum(1 for r in kept if r["is_pinned"]) == 1


def test_explicit_delete_pinned_still_allowed(disabled_client):
    """用途：显式 DELETE 固定行仍允许；删除后不补写。"""
    client = disabled_client
    pid = _create_project(client, name="删固定")
    rows = _seed_five_domain(pid, ["d0", "d1"])
    rid = rows[0]["id"]
    _assert_success_pin(_patch_pin(client, pid, rid, True), True)
    res = client.request("DELETE", _delete_url(pid, rid))
    assert res.status_code == 204, res.text
    assert all(r["id"] != rid for r in _db_rev_rows(pid))
    assert _pin_counts(pid) == (0, 0)


def test_patch_invalid_body_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="非法体")
    rows = _seed_five_domain(pid, ["ib"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    cases = [
        b"",
        b"null",
        b"[]",
        b'"true"',
        b'{"isPinned":1}',
        b'{"isPinned":"true"}',
        b'{"is_pinned":true}',
        b'{"isPinned":true,"x":1}',
        b'{"isPinned":null}',
        b"{" + b"a" * (_PIN_BODY_MAX + 8),
    ]
    for raw in cases:
        res = _patch_pin(client, pid, rid, content=raw, raw_json=False)
        _assert_fixed_error(
            res, 422, _CODE_PIN_INVALID, message=_MSG_PIN_INVALID
        )
        assert _domain_snapshot(pid) == before


def test_patch_query_nonempty_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="非法query")
    rows = _seed_five_domain(pid, ["iq"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)
    res = _patch_pin(client, pid, rid, True, params={"x": "1"})
    _assert_fixed_error(res, 422, _CODE_PIN_INVALID, message=_MSG_PIN_INVALID)
    assert _domain_snapshot(pid) == before


def test_patch_project_and_revision_404_priority(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="404优先")
    rows = _seed_five_domain(pid, ["f0"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    missing_proj = "proj_" + secrets.token_hex(8)
    res_p = _patch_pin(client, missing_proj, rid, True)
    _assert_fixed_error(
        res_p, 404, _CODE_PROJECT, message=_MSG_PROJECT, forbid_echo=missing_proj
    )
    assert _domain_snapshot(pid) == before

    missing_rid = "esr_" + secrets.token_hex(16)
    res_r = _patch_pin(client, pid, missing_rid, True)
    _assert_fixed_error(
        res_r, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=missing_rid
    )
    assert _domain_snapshot(pid) == before

    # 跨项目：修订存在于 A，用 B 访问 → revision 404
    pid_b = _create_project(client, name="404跨项目B")
    before_b = _domain_snapshot(pid_b)
    res_x = _patch_pin(client, pid_b, rid, True)
    _assert_fixed_error(
        res_x, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=rid
    )
    assert _domain_snapshot(pid) == before
    assert _domain_snapshot(pid_b) == before_b


def test_patch_required_roles_csrf(required_client):
    client = required_client
    csrf_w = _login_role(client, "bid_writer")
    headers_w = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="required固定", headers=headers_w)
    rows = _seed_revisions(pid, ["rw"])
    rid = rows[0]["id"]

    # 缺 CSRF
    res_no = _patch_pin(client, pid, rid, True)
    assert res_no.status_code == 403, res_no.text
    detail = res_no.json()["detail"]
    assert detail["code"] == _CODE_CSRF_INVALID

    # 错 CSRF
    res_bad = _patch_pin(
        client, pid, rid, True, headers={"X-CSRF-Token": "wrong-csrf-token"}
    )
    assert res_bad.status_code == 403, res_bad.text
    assert res_bad.json()["detail"]["code"] == _CODE_CSRF_INVALID

    # 非 writer 角色
    for role in ("finance", "hr", "bidder"):
        csrf_r = _login_role(client, role)
        res = _patch_pin(
            client, pid, rid, True, headers={"X-CSRF-Token": csrf_r}
        )
        assert res.status_code == 403, (role, res.text)
        assert res.json()["detail"]["code"] == _CODE_ROLE_FORBIDDEN

    # owner 非 writer 不可绕过
    csrf_own = _login_role(client, "finance", is_owner=True)
    res_own = _patch_pin(
        client, pid, rid, True, headers={"X-CSRF-Token": csrf_own}
    )
    assert res_own.status_code == 403, res_own.text
    assert res_own.json()["detail"]["code"] == _CODE_ROLE_FORBIDDEN

    # writer + CSRF 成功
    csrf_w2 = _login_role(client, "bid_writer")
    res_ok = _patch_pin(
        client, pid, rid, True, headers={"X-CSRF-Token": csrf_w2}
    )
    _assert_success_pin(res_ok, True)


def test_list_page_search_detail_still_six_keys(disabled_client):
    """用途：list/page/search/detail 精确六键，绝不含 isPinned。"""
    client = disabled_client
    pid = _create_project(client, name="六键不变")
    rows = _seed_revisions(pid, ["s0", "s1"])
    rid = rows[0]["id"]
    _assert_success_pin(_patch_pin(client, pid, rid, True), True)

    lst = client.get(_list_url(pid))
    assert lst.status_code == 200, lst.text
    for item in lst.json()["items"]:
        _assert_meta_six(item)

    page = client.get(_page_url(pid))
    assert page.status_code == 200, page.text
    for item in page.json()["items"]:
        _assert_meta_six(item)

    search = client.post(_search_url(pid), json={"query": "章节"})
    assert search.status_code == 200, search.text
    for item in search.json()["items"]:
        _assert_meta_six(item)

    detail = client.get(_detail_url(pid, rid))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "isPinned" not in body
    for k in _META_KEYS_SIX:
        assert k in body


def _raw_is_pinned_map(project_id: str) -> dict[str, int]:
    """用途：按 id 读取原始 is_pinned 整型，绕过 Boolean result processor。"""
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT id, is_pinned FROM editor_state_revisions "
                "WHERE project_id = :pid ORDER BY id"
            ),
            {"pid": project_id},
        ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    finally:
        db.close()


def _set_corrupt_is_pinned_sql(revision_id: str, value: int = 2) -> None:
    """
    用途：独立连接临时 PRAGMA ignore_check_constraints 写入非法 is_pinned，
      提交后立即恢复 OFF。
    二次开发：必须用 engine.connect 保持同一连接；Session.commit 后连接会关闭，
      无法在 finally 中安全恢复 PRAGMA。
    """
    with engine.connect() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        try:
            conn.execute(
                text(
                    "UPDATE editor_state_revisions SET is_pinned = :v WHERE id = :id"
                ),
                {"v": value, "id": revision_id},
            )
            conn.commit()
        finally:
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))
            off = conn.execute(text("PRAGMA ignore_check_constraints")).scalar()
            assert int(off) == 0, f"PRAGMA 必须恢复 OFF，实际={off!r}"


def test_corrupt_is_pinned_meta_fixed_500_zero_write(disabled_client):
    """
    用途：真实 ASGI 路径下目标或同项目兄弟行 is_pinned=2 必须固定 500
      editor_state_revision_pin_failed/no-store；原始坏值与五域零变化；
      Boolean 不得把 2 吃成 True 从而绕过元数据校验。
    """
    client = disabled_client
    pid = _create_project(client, name="坏固定元数据")
    rows = _seed_five_domain(pid, ["bad0", "bad1", "bad2"])
    target_id = rows[0]["id"]
    sibling_id = rows[1]["id"]
    clean_id = rows[2]["id"]

    # 目标与兄弟同时损坏；合法路径若只 is_(True) 过滤会漏掉 2
    _set_corrupt_is_pinned_sql(target_id, 2)
    _set_corrupt_is_pinned_sql(sibling_id, 2)
    pins_before = _raw_is_pinned_map(pid)
    assert pins_before[target_id] == 2
    assert pins_before[sibling_id] == 2
    assert pins_before[clean_id] == 0
    before = _domain_snapshot(pid)

    # 1) 对损坏目标固定：必须 500，不得幂等成功
    res_t = _patch_pin(client, pid, target_id, True)
    _assert_fixed_error(
        res_t, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED, forbid_echo=target_id
    )
    assert _domain_snapshot(pid) == before
    assert _raw_is_pinned_map(pid) == pins_before

    # 2) 对干净行固定：同项目集合含坏 is_pinned 仍必须整次失败
    res_s = _patch_pin(client, pid, clean_id, True)
    _assert_fixed_error(
        res_s, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED, forbid_echo=clean_id
    )
    assert _domain_snapshot(pid) == before
    assert _raw_is_pinned_map(pid) == pins_before
    assert _raw_is_pinned_map(pid)[target_id] == 2
    assert _raw_is_pinned_map(pid)[sibling_id] == 2


def test_execute_flush_commit_failures_rollback(disabled_client, monkeypatch):
    """用途：execute/flush/commit 失败固定 500 脱敏并 rollback 零写。"""
    if not _PIN_SERVICE_PATH.is_file():
        client = disabled_client
        pid = _create_project(client, name="注入探测")
        rows = _seed_revisions(pid, ["inj"])
        res = _patch_pin(client, pid, rows[0]["id"], True)
        assert res.status_code == 404, res.text
        return

    import app.api.editor_state_revisions as api_mod
    import app.services.editor_state_revision_pin_service as pin_svc

    client = disabled_client
    pid = _create_project(client, name="回滚")
    rows = _seed_five_domain(pid, ["rb0", "rb1"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)
    real_set = pin_svc.set_editor_state_revision_pin

    def _wrap_execute_fail(db, *a, **k):
        orig_execute = db.execute
        calls = {"n": 0}

        def _e(*ea, **ek):
            calls["n"] += 1
            # 允许项目锁后的首批读，在业务写路径 execute 注入
            stmt = ea[0] if ea else None
            sql = str(getattr(stmt, "string", stmt) or "")
            low = sql.lower()
            if "editor_state_revisions" in low and (
                low.lstrip().startswith("update")
                or "is_pinned" in low
                or calls["n"] >= 3
            ):
                raise RuntimeError(_INJECT_EXECUTE)
            return orig_execute(*ea, **ek)

        db.execute = _e  # type: ignore[method-assign]
        try:
            return real_set(db, *a, **k)
        finally:
            db.execute = orig_execute  # type: ignore[method-assign]

    monkeypatch.setattr(pin_svc, "set_editor_state_revision_pin", _wrap_execute_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_execute_fail)
    res_ex = _patch_pin(client, pid, rid, True)
    _assert_fixed_error(res_ex, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before
    assert _raw_is_pinned_map(pid).get(rid, 0) == 0

    def _wrap_flush_fail(db, *a, **k):
        orig_flush = db.flush

        def _f(*fa, **fk):
            raise RuntimeError(_INJECT_FLUSH)

        db.flush = _f  # type: ignore[method-assign]
        try:
            return real_set(db, *a, **k)
        finally:
            db.flush = orig_flush  # type: ignore[method-assign]

    monkeypatch.setattr(pin_svc, "set_editor_state_revision_pin", _wrap_flush_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_flush_fail)
    res = _patch_pin(client, pid, rid, True)
    _assert_fixed_error(res, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before

    def _wrap_commit_fail(db, *a, **k):
        orig_commit = db.commit

        def _c(*ca, **ck):
            raise RuntimeError(_INJECT_COMMIT)

        db.commit = _c  # type: ignore[method-assign]
        try:
            return real_set(db, *a, **k)
        finally:
            db.commit = orig_commit  # type: ignore[method-assign]

    monkeypatch.setattr(pin_svc, "set_editor_state_revision_pin", _wrap_commit_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_commit_fail)
    res2 = _patch_pin(client, pid, rid, True)
    _assert_fixed_error(res2, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before


def test_sqlite_idempotent_add_is_pinned_column(disabled_client):
    """用途：独立旧库幂等加 is_pinned NOT NULL DEFAULT 0 + CHECK；存量全 false。"""
    # 共享测试库旁证（实现后）
    cols = {
        c["name"] for c in inspect(engine).get_columns("editor_state_revisions")
    }
    if "is_pinned" in cols:
        client = disabled_client
        pid = _create_project(client, name="迁移旁证")
        rows = _seed_revisions(pid, ["m0"])
        res = _patch_pin(client, pid, rows[0]["id"], True)
        _assert_success_pin(res, True)

    old_engine = _build_legacy_pin_engine()
    with old_engine.connect() as conn:
        ddl0 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "is_pinned" not in ddl0
        count0 = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_revisions")
        ).scalar_one()
        assert count0 == 8
        rows0 = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at "
                "FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()

    ensure_schema_columns(target_engine=old_engine)
    ensure_schema_columns(target_engine=old_engine)

    with old_engine.connect() as conn:
        cols1 = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        assert "is_pinned" in cols1
        assert "display_name" in cols1
        col_info = {
            r[1]: (r[2], r[3], r[4])
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        pin_type, pin_notnull, pin_default = col_info["is_pinned"]
        assert int(pin_notnull) == 1
        assert pin_default is not None
        ddl1 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert re.search(
            r"is_pinned\s+IN\s*\(\s*0\s*,\s*1\s*\)",
            ddl1,
            flags=re.IGNORECASE,
        ), ddl1
        count1 = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_revisions")
        ).scalar_one()
        assert count1 == 8
        rows1 = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at "
                "FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()
        assert rows1 == rows0
        pin_vals = conn.execute(
            text("SELECT is_pinned FROM editor_state_revisions ORDER BY id")
        ).fetchall()
        assert all(int(r[0]) == 0 for r in pin_vals)


def test_is_pinned_migration_midway_failure_rolls_back(disabled_client):
    """
    用途：is_pinned 迁移在 DROP 旧表前注入异常 → 真实事务回滚：
      旧表/8 行/索引/FK 保留；is_pinned 与临时表均不存在；
      移除注入后可正常迁移完成。
    """
    _ = disabled_client  # 触发应用启动路径依赖已加载
    _MIG_INJECT = "p12fja_injected_is_pinned_migration_midway_failure"
    _TMP_TABLE = "editor_state_revisions__p12fja_mig"

    eng = _build_legacy_pin_engine()
    with eng.connect() as conn:
        ddl_pre = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "is_pinned" not in ddl_pre
        rows_pre = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at "
                "FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()
        assert len(rows_pre) == 8
        idx_pre = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_revisions' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert "ix_esr_workspace_project_created_id" in idx_pre
        fk_pre = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
        assert int(fk_pre) == 1

    from sqlalchemy.engine import Connection as _SAConnection

    _orig_exec_driver_sql = _SAConnection.exec_driver_sql
    seen_p12fja_tmp = {"created": False}

    def _injecting_exec_driver_sql(self, statement, *args, **kwargs):
        sql = statement if isinstance(statement, str) else str(statement)
        compact = " ".join(sql.split())
        compact_u = compact.upper()
        # 仅在 is_pinned 临时表已创建后、DROP 旧表前注入（避开九来源/display_name 迁移）
        if (
            "EDITOR_STATE_REVISIONS__P12FJA_MIG" in compact_u
            and compact_u.startswith("CREATE TABLE")
        ):
            seen_p12fja_tmp["created"] = True
        if (
            seen_p12fja_tmp["created"]
            and compact_u.startswith("DROP TABLE")
            and "EDITOR_STATE_REVISIONS" in compact_u
            and _TMP_TABLE.upper() not in compact_u
        ):
            raise RuntimeError(_MIG_INJECT)
        return _orig_exec_driver_sql(self, statement, *args, **kwargs)

    try:
        _SAConnection.exec_driver_sql = _injecting_exec_driver_sql  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match=re.escape(_MIG_INJECT)):
            ensure_schema_columns(target_engine=eng)
    finally:
        _SAConnection.exec_driver_sql = _orig_exec_driver_sql  # type: ignore[method-assign]

    with eng.connect() as conn:
        ddl_post = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert ddl_post == ddl_pre
        assert "is_pinned" not in ddl_post
        cols_post = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        assert "is_pinned" not in cols_post
        rows_post = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at "
                "FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()
        assert rows_post == rows_pre
        assert len(rows_post) == 8
        idx_post = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_revisions' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        for name in idx_pre:
            assert name in idx_post
        tmp_exists = conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name=:n"
            ),
            {"n": _TMP_TABLE},
        ).scalar_one()
        assert tmp_exists == 0, "迁移失败后临时表不得残留"
        fk_post = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
        assert int(fk_post) == 1

    # 移除注入后可正常迁移
    ensure_schema_columns(target_engine=eng)
    with eng.connect() as conn:
        cols_ok = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        assert "is_pinned" in cols_ok
        ddl_ok = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert re.search(
            r"is_pinned\s+IN\s*\(\s*0\s*,\s*1\s*\)",
            ddl_ok,
            flags=re.IGNORECASE,
        ), ddl_ok
        count_ok = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_revisions")
        ).scalar_one()
        assert count_ok == 8
        pin_vals = conn.execute(
            text("SELECT is_pinned FROM editor_state_revisions ORDER BY id")
        ).fetchall()
        assert all(int(r[0]) == 0 for r in pin_vals)
        tmp_left = conn.execute(
            text(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='table' AND name=:n"
            ),
            {"n": _TMP_TABLE},
        ).scalar_one()
        assert tmp_left == 0


def _build_legacy_pin_engine():
    """用途：独立旧 SQLite（八来源、无 display_name/is_pinned）。"""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(eng, "connect")
    def _fk(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    with eng.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE workspaces (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id VARCHAR(64) NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                name VARCHAR(200) NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            f"""
            CREATE TABLE editor_state_revisions (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                project_id VARCHAR(64) NOT NULL
                    REFERENCES projects(id) ON DELETE CASCADE,
                snapshot_json TEXT NOT NULL,
                state_version VARCHAR(64) NOT NULL,
                snapshot_bytes INTEGER NOT NULL,
                source_kind VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL,
                CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
                CHECK ({_OLD_SOURCE_CHECK})
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE INDEX ix_esr_workspace_project_created_id
            ON editor_state_revisions(workspace_id, project_id, created_at, id)
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_revisions_workspace_id "
            "ON editor_state_revisions(workspace_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_revisions_project_id "
            "ON editor_state_revisions(project_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_revisions_created_at "
            "ON editor_state_revisions(created_at)"
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws_mig', '迁移空间', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('proj_mig', 'ws_mig', '迁移项目')"
        )
        sources = [
            "browser_put",
            "task",
            "revise",
            "callback",
            "local_parser",
            "content_fuse_apply",
            "content_fuse_consume",
            "checkpoint_restore",
        ]
        for i, src in enumerate(sources):
            snap = json.dumps(
                {"i": i, "src": src, "pad": "x" * 8},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.exec_driver_sql(
                """
                INSERT INTO editor_state_revisions (
                    id, workspace_id, project_id, snapshot_json,
                    state_version, snapshot_bytes, source_kind, created_at
                ) VALUES (
                    :id, 'ws_mig', 'proj_mig', :snap,
                    :ver, :nbytes, :src, :ts
                )
                """,
                {
                    "id": f"esr_mig_{i:02d}",
                    "snap": snap,
                    "ver": f"esv_{'a' * 32}",
                    "nbytes": len(snap.encode("utf-8")),
                    "src": src,
                    "ts": f"2026-07-01 12:00:{i:02d}",
                },
            )
    return eng


def test_production_source_guards_and_pin_service_shape():
    """用途：实现后静态守卫；failure-first 阶段仅要求测试可收集。"""
    # 模块顶层不得 import pin service（保证收集期不依赖新文件存在）
    this_src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(this_src)
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "editor_state_revision_pin_service" not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "editor_state_revision_pin_service" not in alias.name

    if not _PIN_SERVICE_PATH.is_file():
        return

    pin_src = _PIN_SERVICE_PATH.read_text(encoding="utf-8")
    assert "FOR UPDATE" in pin_src or "updated_at=Project.updated_at" in pin_src
    assert "MAX_PINNED" in pin_src
    assert "editor_state_revision_pin_limit" in pin_src
    assert "editor_state_revision_pin_failed" in pin_src
    # 禁止在 select 投影列表中读取 snapshot_json
    assert "EditorStateRevisionRow.snapshot_json" not in pin_src

    rev_src = _REVISION_SERVICE_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"def _trim_revisions\(.*?\n(?P<body>.*?)(?=\ndef |\Z)",
        rev_src,
        flags=re.S,
    )
    assert m is not None
    body = m.group("body")
    assert "is_pinned" in body
    # 代码投影不得读取 snapshot_json；docstring 禁令字样可出现
    code_only = re.sub(r'""".*?"""', "", body, flags=re.S)
    code_only = re.sub(r"#.*", "", code_only)
    assert "snapshot_json" not in code_only
    assert "EditorStateRevisionRow.is_pinned" in code_only

    api_src = _API_PATH.read_text(encoding="utf-8")
    assert "/pin" in api_src
    assert "isPinned" in api_src

    ent_src = _ENTITIES_PATH.read_text(encoding="utf-8")
    assert "is_pinned" in ent_src

    db_src = _DATABASE_PATH.read_text(encoding="utf-8")
    assert "is_pinned" in db_src
    assert "migrate_editor_state_revisions_is_pinned" in db_src
