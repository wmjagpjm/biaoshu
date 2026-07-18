"""
模块：P12F-H 单条修订命名后端专项测试
用途：真实 HTTP+SQLite 验收
  PATCH /api/projects/{projectId}/editor-state-revisions/{revisionId}/display-name
  及 list/page/search/detail 六键 displayName、坏名 corrupt、迁移加列与零旁路。
对接：契约 docs/p12f-revision-display-name-contract.md §3–§5、§7.1；
  editor_state_revision_name_service（本阶段不得 import 实现模块顶层）；
  history/list/page/search/detail 既有只读链。
二次开发：
  - 禁止 mock 路由返回、宽泛状态码、恒真断言、固定 sleep、吞异常、skip/xfail；
  - failure-first 阶段仅通过 ASGI 打尚未存在的路由，不得顶层 import 不存在的 service；
  - 成功/失败均断言数据库与五域零副作用，不得只断言 HTTP。
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import secrets
import unicodedata
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
from app.services import auth_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12fh"
_SECRET = "SECRET_P12FH_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-revisions/display-name"
_INJECT_EXECUTE = "p12fh_injected_execute_failure"
_INJECT_FLUSH = "p12fh_injected_flush_failure"
_INJECT_COMMIT = "p12fh_injected_commit_failure"

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

# 契约 §3/§4 名称专用错误
_CODE_NAME_INVALID = "editor_state_revision_display_name_invalid"
_MSG_NAME_INVALID = "修订名称无效"
_CODE_NAME_ERROR = "editor_state_revision_display_name_error"
_MSG_NAME_ERROR = "保存修订名称失败"
# 新 PATCH 精确 404 文案（契约 §4；不得沿用 history/delete 旧长文案）
_CODE_NOT_FOUND = "editor_state_revision_not_found"
_MSG_NOT_FOUND = "修订不存在"
_CODE_PROJECT = "project_not_found"
_MSG_PROJECT = "项目不存在"
_CODE_CORRUPT = "editor_state_revision_corrupt"
_MSG_CORRUPT = "修订记录数据损坏，无法读取"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_CODE_CSRF_INVALID = "csrf_invalid"

_OWNER_USER = "admin_p12fh_owner"
_OWNER_PASS = "TestPass-P12FH-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12FH-Writer-0001!",
    "finance": "TestPass-P12FH-Finance-0001!",
    "hr": "TestPass-P12FH-Hr-0001!",
    "bidder": "TestPass-P12FH-Bidder-0001!",
}

_NAME_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_name_service.py"
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
_HISTORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_history_service.py"
)
_SCHEMAS_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "api" / "schemas.py"
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


def _name_url(project_id: str, revision_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{revision_id}/display-name"
    )


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions"


def _page_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/page"


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/search"


def _detail_url(project_id: str, revision_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-revisions/{revision_id}"


def _create_project(
    client: TestClient,
    name: str = "P12F-H项目",
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
    assert "UPDATE" not in blob
    assert "editor_state_revisions" not in blob
    assert "editor_state_revision_name_service" not in blob
    assert _PATH_MARKER not in blob
    assert "ValueError" not in blob
    assert "TypeError" not in blob
    assert "IntegrityError" not in blob
    assert _INJECT_EXECUTE not in blob
    assert _INJECT_FLUSH not in blob
    assert _INJECT_COMMIT not in blob
    if forbid_echo is not None and forbid_echo != "":
        assert forbid_echo not in blob


def _assert_success_name(res, expected: str | None) -> None:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == {"displayName"}
    if expected is None:
        assert body["displayName"] is None
    else:
        assert type(body["displayName"]) is str
        assert body["displayName"] == expected
    blob = res.text
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "snapshot" not in blob
    assert _SECRET not in blob


def _assert_meta_six(item: dict, *, forbid_echo: str | None = None) -> None:
    assert set(item.keys()) == _META_KEYS_SIX, item.keys()
    assert _REVISION_ID_RE.match(item["revisionId"])
    assert _STATE_VERSION_RE.match(item["stateVersion"])
    assert type(item["snapshotBytes"]) is int and item["snapshotBytes"] > 0
    assert type(item["sourceKind"]) is str and item["sourceKind"]
    assert type(item["createdAt"]) is str and item["createdAt"]
    dn = item["displayName"]
    if dn is None:
        assert item["displayName"] is None
    else:
        assert type(dn) is str
        assert dn == dn.strip()
        assert 1 <= len(dn) <= 40
        assert "\n" not in dn and "\t" not in dn and "\x00" not in dn
    if forbid_echo:
        assert forbid_echo not in json.dumps(item, ensure_ascii=False)


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
    username = f"user_{role}_p12fh{'_own' if is_owner else ''}_{secrets.token_hex(3)}"
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
        # 生产加列前 ORM 可能无 display_name；有列时按 kwargs/属性写入
        if hasattr(row, "display_name"):
            row.display_name = display_name
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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12FH") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12fh",
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


def _patch_name(
    client: TestClient,
    project_id: str,
    revision_id: str,
    display_name: object,
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
        kwargs["json"] = {"displayName": display_name}
    return client.request(
        "PATCH",
        _name_url(project_id, revision_id),
        **kwargs,
    )


def _normalize_sql_params(params) -> list[object]:
    if params is None:
        return []
    if isinstance(params, dict):
        return list(params.values())
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params]


def _db_has_display_name_column() -> bool:
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("editor_state_revisions")}
    return "display_name" in cols


def _set_display_name_sql(revision_id: str, value: object) -> None:
    """用途：绕过 ORM 直接写入 display_name（坏值 corrupt 夹具）。"""
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE editor_state_revisions SET display_name = :v WHERE id = :id"
            ),
            {"v": value, "id": revision_id},
        )
        db.commit()
    finally:
        db.close()


# ---------- 路由存在与成功路径（failure-first：新路由真实 404） ----------


def test_route_exists_patch_success_save_overwrite_clear(disabled_client):
    """
    用途：合法保存/覆盖/清除/重复清除；响应精确一键 displayName + no-store。
    failure-first：生产未实现时 ASGI 对未知 path 返回 404，不得 200。
    """
    client = disabled_client
    pid = _create_project(client, name="命名成功路径")
    rows = _seed_five_domain(pid, ["a", "b"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    # 保存
    res = _patch_name(client, pid, rid, "版本甲")
    _assert_success_name(res, "版本甲")
    after1 = _db_rev_rows(pid)
    target1 = next(r for r in after1 if r["id"] == rid)
    assert "display_name" in target1
    assert target1["display_name"] == "版本甲"
    # 其它修订与五域不变
    for r in after1:
        if r["id"] != rid:
            assert "display_name" in r
            assert r["display_name"] is None
            old = next(x for x in before["revisions"] if x["id"] == r["id"])
            assert r["snapshot_json"] == old["snapshot_json"]
            assert r["state_version"] == old["state_version"]
            assert r["source_kind"] == old["source_kind"]
            assert r["snapshot_bytes"] == old["snapshot_bytes"]
    assert _domain_snapshot(pid)["checkpoints"] == before["checkpoints"]
    assert _domain_snapshot(pid)["editor_state"] == before["editor_state"]
    assert _domain_snapshot(pid)["project"] == before["project"]
    assert _domain_snapshot(pid)["tasks"] == before["tasks"]

    # 覆盖
    res2 = _patch_name(client, pid, rid, "版本乙")
    _assert_success_name(res2, "版本乙")
    assert next(r for r in _db_rev_rows(pid) if r["id"] == rid)[
        "display_name"
    ] == "版本乙"

    # 清除
    res3 = _patch_name(client, pid, rid, None)
    _assert_success_name(res3, None)
    assert next(r for r in _db_rev_rows(pid) if r["id"] == rid).get(
        "display_name"
    ) is None

    # 重复清除
    res4 = _patch_name(client, pid, rid, None)
    _assert_success_name(res4, None)

    # NFKC：全角数字规范化后存储
    raw = "ｖｅｒ１"  # 全角
    nfkc = unicodedata.normalize("NFKC", raw)
    res5 = _patch_name(client, pid, rid, raw)
    _assert_success_name(res5, nfkc)
    assert next(r for r in _db_rev_rows(pid) if r["id"] == rid)[
        "display_name"
    ] == nfkc


def test_route_missing_is_real_http_not_import_error(disabled_client):
    """
    用途：failure-first 锚点——仅通过真实 ASGI 断言新路由；
    未实现时成功断言失败（精确 200 业务失败），绝非收集/导入异常。
    禁止把 404 放宽为多状态并冒充通过。
    """
    client = disabled_client
    pid = _create_project(client, name="路由锚点")
    rows = _seed_revisions(pid, ["x"])
    rid = rows[0]["id"]
    res = client.patch(
        _name_url(pid, rid),
        json={"displayName": "锚点"},
    )
    # 真实 HTTP 响应对象；业务成功条件固定 200+一键，未实现时此处红
    assert res is not None
    assert hasattr(res, "status_code")
    _assert_success_name(res, "锚点")


# ---------- 请求体校验 422 ----------


@pytest.mark.parametrize(
    "label,builder",
    [
        ("missing_key", lambda: json.dumps({}).encode("utf-8")),
        (
            "extra_key",
            lambda: json.dumps(
                {"displayName": "ok", "extra": 1}, ensure_ascii=False
            ).encode("utf-8"),
        ),
        (
            "snake_case",
            lambda: json.dumps(
                {"display_name": "ok"}, ensure_ascii=False
            ).encode("utf-8"),
        ),
        ("array", lambda: b"[\"x\"]"),
        ("scalar", lambda: b'"x"'),
        ("invalid_json", lambda: b"{not-json"),
        ("empty_string", lambda: json.dumps({"displayName": ""}).encode()),
        ("whitespace", lambda: json.dumps({"displayName": "  "}).encode()),
        (
            "leading_space",
            lambda: json.dumps({"displayName": " a"}).encode(),
        ),
        (
            "trailing_space",
            lambda: json.dumps({"displayName": "a "}).encode(),
        ),
        (
            "forty_one",
            lambda: json.dumps({"displayName": "字" * 41}, ensure_ascii=False).encode(
                "utf-8"
            ),
        ),
        (
            "control_nl",
            lambda: json.dumps({"displayName": "a\nb"}).encode(),
        ),
        (
            "control_tab",
            lambda: json.dumps({"displayName": "a\tb"}).encode(),
        ),
        (
            "control_nul",
            lambda: json.dumps({"displayName": "a\x00b"}).encode(),
        ),
        (
            "line_sep",
            lambda: json.dumps({"displayName": "a\u2028b"}).encode("utf-8"),
        ),
        (
            "bidi",
            lambda: json.dumps({"displayName": "a\u202eb"}).encode("utf-8"),
        ),
        (
            "oversized_body",
            lambda: (
                b'{"displayName":"' + (b"A" * 2000) + b'"}'
            ),
        ),
    ],
)
def test_patch_invalid_body_fixed_422_zero_write(disabled_client, label, builder):
    client = disabled_client
    pid = _create_project(client, name=f"非法体-{label}")
    rows = _seed_five_domain(pid, ["i0"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)
    raw = builder()
    marker = _SECRET if _SECRET.encode() in raw else (raw[:32].decode("utf-8", "replace") if raw else "")
    res = _patch_name(client, pid, rid, None, content=raw, raw_json=False)
    _assert_fixed_error(
        res,
        422,
        _CODE_NAME_INVALID,
        message=_MSG_NAME_INVALID,
        forbid_echo=marker if marker and marker not in ("{", "}") else None,
    )
    assert _domain_snapshot(pid) == before


def test_patch_query_nonempty_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="query非空")
    rows = _seed_five_domain(pid, ["q0"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)
    res = _patch_name(
        client, pid, rid, "名字", params={"x": "1"}
    )
    _assert_fixed_error(
        res, 422, _CODE_NAME_INVALID, message=_MSG_NAME_INVALID, forbid_echo="x"
    )
    assert _domain_snapshot(pid) == before


# ---------- 作用域与权限 ----------


def test_patch_project_and_revision_404_priority(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="本域")
    rows = _seed_five_domain(pid, ["s0", "s1"])
    rid = rows[0]["id"]
    before = _domain_snapshot(pid)

    missing_pid = "proj_missing_" + secrets.token_hex(4)
    res_p = _patch_name(client, missing_pid, rid, "x")
    _assert_fixed_error(
        res_p, 404, _CODE_PROJECT, message=_MSG_PROJECT, forbid_echo=missing_pid
    )

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
    res_fp = _patch_name(client, foreign_pid, foreign_rev, "x")
    _assert_fixed_error(res_fp, 404, _CODE_PROJECT, message=_MSG_PROJECT)

    pid_b = _create_project(client, name="兄弟")
    rows_b = _seed_revisions(pid_b, ["b0"])
    res_cross = _patch_name(client, pid, rows_b[0]["id"], "x")
    _assert_fixed_error(
        res_cross,
        404,
        _CODE_NOT_FOUND,
        message=_MSG_NOT_FOUND,
        forbid_echo=rows_b[0]["id"],
    )

    for bad_id in ("not-a-revision", "esr_short", "ESR_" + ("f" * 32)):
        res_bad = _patch_name(client, pid, bad_id, "x")
        _assert_fixed_error(
            res_bad, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=bad_id
        )

    assert _domain_snapshot(pid) == before


def test_patch_required_roles_csrf(required_client):
    client = required_client
    # bid_writer 成功
    csrf_w = _login_role(client, "bid_writer")
    headers_w = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="权限项目", headers=headers_w)
    # 种子修订需在登录会话下经 disabled 旁路——改用 DB 直写
    rows = _seed_revisions(pid, ["r0"])
    rid = rows[0]["id"]
    res_ok = _patch_name(client, pid, rid, "作者命名", headers=headers_w)
    _assert_success_name(res_ok, "作者命名")

    # 缺 CSRF：固定 403 + csrf_invalid（与删除/恢复链对齐）
    res_no = _patch_name(client, pid, rid, "无令牌")
    assert res_no.status_code == 403, res_no.text
    detail_no = res_no.json().get("detail")
    assert isinstance(detail_no, dict), res_no.text
    assert set(detail_no.keys()) == {"code", "message"}
    assert detail_no.get("code") == _CODE_CSRF_INVALID

    # 错 CSRF
    res_bad = _patch_name(
        client, pid, rid, "错令牌", headers={"X-CSRF-Token": "wrong-csrf-token"}
    )
    assert res_bad.status_code == 403, res_bad.text
    detail_bad = res_bad.json().get("detail")
    assert isinstance(detail_bad, dict), res_bad.text
    assert set(detail_bad.keys()) == {"code", "message"}
    assert detail_bad.get("code") == _CODE_CSRF_INVALID

    # 非 bid_writer 角色
    for role in ("finance", "hr", "bidder"):
        csrf_r = _login_role(client, role)
        res_r = _patch_name(
            client, pid, rid, f"{role}命名", headers={"X-CSRF-Token": csrf_r}
        )
        assert res_r.status_code == 403, (role, res_r.text)
        detail = res_r.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == _CODE_ROLE_FORBIDDEN


# ---------- SQL / AST / 事务 ----------


def test_sql_project_id_projection_and_triple_predicate_update(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="SQL三谓词命名")
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
        for t in (
            "project_editor_states",
            "editor_state_checkpoints",
            "project_tasks",
        ):
            if t in low:
                captured.append((f"FORBIDDEN::{statement}", parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _patch_name(client, pid, rid, "SQL名")
        _assert_success_name(res, "SQL名")
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    forbidden = [c for c in captured if str(c[0]).startswith("FORBIDDEN::")]
    assert forbidden == [], f"命名触及禁区表: {forbidden}"

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
    assert re.search(
        r"select\s+(?:projects\.)?id(?:\s+as\s+\w+)?\s+from\s+projects",
        low_p,
    ), sql_p
    assert "snapshot" not in low_p

    updates = [
        (s, p)
        for s, p in captured
        if "editor_state_revisions" in s.lower()
        and s.lstrip().upper().startswith("UPDATE")
    ]
    assert len(updates) == 1, updates
    sql_u, params_u = updates[0]
    low_u = sql_u.lower()
    assert " set " in f" {low_u} "
    assert " where " in f" {low_u} "
    set_part = low_u.split("set", 1)[1].split("where", 1)[0]
    # SET 子句精确只写 display_name，禁止 state_version/snapshot/source/created
    assert "display_name" in set_part
    assert "state_version" not in set_part
    assert "snapshot" not in set_part
    assert "snapshot_json" not in set_part
    assert "source_kind" not in set_part
    assert "created_at" not in set_part
    assert "workspace_id" in low_u
    assert "project_id" in low_u
    assert re.search(r"\bid\b", low_u)
    uvals = [str(v) for v in _normalize_sql_params(params_u)]
    assert rid in uvals
    assert pid in uvals
    assert _WS in uvals
    assert "SQL名" in uvals

    # 源码守卫：name service 必须存在且无 snapshot 整实体加载（只读源码，不 import 模块）
    assert _NAME_SERVICE_PATH.is_file(), "editor_state_revision_name_service.py 必须存在"
    svc = _NAME_SERVICE_PATH.read_text(encoding="utf-8")
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
    assert "db.get(EditorStateRevisionRow" not in svc
    assert "session.get(EditorStateRevisionRow" not in svc

    after = _domain_snapshot(pid)
    assert after["checkpoints"] == before_other["checkpoints"]
    assert after["editor_state"] == before_other["editor_state"]
    assert after["project"] == before_other["project"]
    assert after["tasks"] == before_other["tasks"]


def test_rowcount_none_zero_multi_fixed_500(disabled_client):
    """
    用途：可控 rowcount 直接证据，覆盖 0/None/-1/2/1。
    failure-first：顶层不 import 缺失 service；函数内断言路径后延迟 importlib。
    """
    client = disabled_client
    pid = _create_project(client, name="rowcount控制")
    rows = _seed_five_domain(pid, ["rc0", "rc1", "rc2", "rc3", "rc4"])
    targets = [r["id"] for r in rows]
    before = _domain_snapshot(pid)

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_revision_name_service.py 必须存在"
    name_svc = importlib.import_module(
        "app.services.editor_state_revision_name_service"
    )

    def _run_with_rowcount(forced, revision_id: str, display_name: str):
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
                if (
                    "editor_state_revisions" in sql
                    and "update" in sql
                    and "display_name" in sql
                ):
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
                out = name_svc.set_editor_state_revision_display_name(
                    db, _WS, pid, revision_id, display_name
                )
                assert out == display_name
                assert counters["commit"] == 1
                assert counters["rollback"] == 0
                return None

            with pytest.raises(name_svc.EditorStateRevisionNameError) as ei:
                name_svc.set_editor_state_revision_display_name(
                    db, _WS, pid, revision_id, display_name
                )
            exc = ei.value
            if forced == 0:
                assert exc.status_code == 404
                assert exc.code == _CODE_NOT_FOUND
                assert exc.message == _MSG_NOT_FOUND
            else:
                assert exc.status_code == 500
                assert exc.code == _CODE_NAME_ERROR
                assert exc.message == _MSG_NAME_ERROR
            assert counters["rollback"] == 1
            assert counters["commit"] == 0
            return exc
        finally:
            db.close()

    # 0 → 精确 revision 404 + rollback 1 + commit 0；五域全等
    _run_with_rowcount(0, targets[0], "rc-zero")
    assert _domain_snapshot(pid) == before

    # None/-1/2 → 精确名称 500 + rollback 1 + commit 0；五域全等
    for forced, tid, label in (
        (None, targets[1], "rc-none"),
        (-1, targets[2], "rc-neg"),
        (2, targets[3], "rc-multi"),
    ):
        _run_with_rowcount(forced, tid, label)
        assert _domain_snapshot(pid) == before

    # 1 → 成功、commit 1、rollback 0 且只改目标 display_name
    _run_with_rowcount(1, targets[4], "行计数探针")
    after = _domain_snapshot(pid)
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    target = next(r for r in after["revisions"] if r["id"] == targets[4])
    assert target["display_name"] == "行计数探针"
    for kept in targets[:4]:
        other = next(r for r in after["revisions"] if r["id"] == kept)
        assert other["display_name"] is None
        # 其它行原八字段逐键相等，不得被写
        before_row = next(r for r in before["revisions"] if r["id"] == kept)
        for key in (
            "id",
            "workspace_id",
            "project_id",
            "snapshot_json",
            "state_version",
            "snapshot_bytes",
            "source_kind",
            "created_at",
        ):
            assert other[key] == before_row[key]

    # 源码静态：禁止 or-折叠 rowcount
    svc = _NAME_SERVICE_PATH.read_text(encoding="utf-8")
    assert "int(result.rowcount or 0)" not in svc
    assert "int(result.rowcount or" not in svc
    assert "result.rowcount or" not in svc
    assert " == 0" in svc
    assert " != 1" in svc


def test_execute_flush_commit_failures_rollback(disabled_client):
    """
    用途：服务层真实 Session 注入 execute/flush/commit 故障；固定 500 + rollback 证据。
    failure-first：顶层不 import 缺失 service；函数内断言路径后延迟 importlib。
    """
    client = disabled_client
    pid = _create_project(client, name="故障回滚命名")
    rows = _seed_five_domain(pid, ["f0", "f1", "f2"])
    targets = [rows[0]["id"], rows[1]["id"], rows[2]["id"]]
    before = _domain_snapshot(pid)

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_revision_name_service.py 必须存在"
    name_svc = importlib.import_module(
        "app.services.editor_state_revision_name_service"
    )

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
            post_commit = {"execute": 0, "flush": 0}

            def _exec(*a, **k):
                counters["execute"] += 1
                counters["order"].append("execute")
                if counters["commit"] > 0:
                    post_commit["execute"] += 1
                sql = str(a[0]) if a else ""
                low = sql.lower()
                if (
                    kind == "execute"
                    and "editor_state_revisions" in low
                    and "update" in low
                    and "display_name" in low
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

            with pytest.raises(name_svc.EditorStateRevisionNameError) as ei:
                name_svc.set_editor_state_revision_display_name(
                    db, _WS, pid, revision_id, f"故障{kind}"
                )
            exc = ei.value
            assert exc.status_code == 500
            assert exc.code == _CODE_NAME_ERROR
            assert exc.message == _MSG_NAME_ERROR
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
            assert post_commit == {"execute": 0, "flush": 0}
            return counters
        finally:
            db.close()

    _run_fault("execute", targets[0])
    assert _domain_snapshot(pid) == before
    _run_fault("flush", targets[1])
    assert _domain_snapshot(pid) == before
    _run_fault("commit", targets[2])
    assert _domain_snapshot(pid) == before

    # 成功服务路径：项目 SELECT + UPDATE 恰好两次 execute、flush 1、commit 1、rollback 0；
    # commit 后 execute/flush/refresh/query 精确 0
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

        out = name_svc.set_editor_state_revision_display_name(
            db_ok, _WS, pid, targets[0], "故障后可写"
        )
        assert out == "故障后可写"
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

    after = _domain_snapshot(pid)
    assert after["checkpoints"] == before["checkpoints"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    target = next(r for r in after["revisions"] if r["id"] == targets[0])
    assert target["display_name"] == "故障后可写"


# ---------- 读取六键 / corrupt / 搜索集合 ----------


def test_list_page_search_detail_six_keys_and_null_default(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="六键读取")
    rows = _seed_revisions(pid, ["k0", "k1", "k2"])
    rid0 = rows[0]["id"]

    # 新修订默认 null
    res_list = client.get(_list_url(pid))
    assert res_list.status_code == 200, res_list.text
    items = res_list.json()["items"]
    assert len(items) == 3
    for it in items:
        _assert_meta_six(it)
        assert it["displayName"] is None

    res_page = client.get(_page_url(pid))
    assert res_page.status_code == 200, res_page.text
    page_body = res_page.json()
    assert set(page_body.keys()) == {"items", "nextCursor"}
    for it in page_body["items"]:
        _assert_meta_six(it)

    # 搜索命中集合按既有正文匹配，不依赖 displayName
    res_search = client.post(
        _search_url(pid),
        json={"query": "章节k0"},
    )
    assert res_search.status_code == 200, res_search.text
    sbody = res_search.json()
    assert "items" in sbody
    for it in sbody["items"]:
        _assert_meta_six(it)

    res_detail = client.get(_detail_url(pid, rid0))
    assert res_detail.status_code == 200, res_detail.text
    d = res_detail.json()
    assert "displayName" in d
    meta_only = {k: d[k] for k in _META_KEYS_SIX}
    _assert_meta_six(meta_only)
    assert d["displayName"] is None

    # 保存名称后六键回显
    res_set = _patch_name(client, pid, rid0, "展示名")
    _assert_success_name(res_set, "展示名")
    res_list2 = client.get(_list_url(pid))
    assert res_list2.status_code == 200, res_list2.text
    hit = next(i for i in res_list2.json()["items"] if i["revisionId"] == rid0)
    _assert_meta_six(hit)
    assert hit["displayName"] == "展示名"

    res_detail2 = client.get(_detail_url(pid, rid0))
    assert res_detail2.status_code == 200, res_detail2.text
    assert res_detail2.json()["displayName"] == "展示名"


def test_corrupt_display_name_fixed_corrupt_on_read(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="坏名corrupt")
    rows = _seed_revisions(pid, ["c0"])
    rid = rows[0]["id"]
    assert _db_has_display_name_column(), "display_name 列必须存在"
    # 写入非法名称（含换行）；marker 不得出现在固定 corrupt 文案中
    bad_name = "P12FH_CORRUPT_MARK\nNAME"
    _set_display_name_sql(rid, bad_name)

    for url, method, kwargs in (
        (_list_url(pid), "GET", {}),
        (_page_url(pid), "GET", {}),
        (_detail_url(pid, rid), "GET", {}),
        (_search_url(pid), "POST", {"json": {"query": "章节c0"}}),
    ):
        res = client.request(method, url, **kwargs)
        _assert_fixed_error(
            res,
            500,
            _CODE_CORRUPT,
            message=_MSG_CORRUPT,
            forbid_echo="P12FH_CORRUPT_MARK",
        )


def test_restore_does_not_copy_display_name(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="恢复不复制名")
    rows = _seed_five_domain(pid, ["r0", "r1"])
    # rows 按 created_at desc：[0]=r1 最新，[-1]=r0 较旧
    rid = rows[-1]["id"]  # 较旧
    res_set = _patch_name(client, pid, rid, "源名称")
    _assert_success_name(res_set, "源名称")
    # 对齐当前 editor-state 到最新修订账本，消除 before 缺口：
    # 不同版本恢复应恰好追加一条 after 的 revision_restore
    # （_variant 的 guidance 为 str，GET /editor-state 会因响应模型失败，故直接用账本版本）
    latest_snap = json.loads(rows[0]["snapshot_json"])
    expected = rows[0]["state_version"]
    assert type(expected) is str and expected
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, pid)
        editor_state_service.apply_canonical_snapshot_to_locked_row(
            db, pid, row, latest_snap
        )
        db.commit()
        # 复核写回后版本与最新账本一致
        row2 = db.get(ProjectEditorStateRow, pid)
        cur_state = editor_state_service._state_from_row(pid, row2)
        assert cur_state["stateVersion"] == expected
    finally:
        db.close()
    res_restore = client.post(
        f"{_detail_url(pid, rid)}/restore",
        json={"expectedStateVersion": expected},
    )
    assert res_restore.status_code == 200, res_restore.text
    # 不同版本恢复恰好产生一条 revision_restore；新行 displayName 必须 null
    after = client.get(_list_url(pid))
    assert after.status_code == 200, after.text
    items = after.json()["items"]
    restore_items = [i for i in items if i["sourceKind"] == "revision_restore"]
    assert len(restore_items) == 1
    restore_item = restore_items[0]
    _assert_meta_six(restore_item)
    assert restore_item["displayName"] is None
    # 源修订名称保留且不被复制到新行；ID 不同
    src = next(i for i in items if i["revisionId"] == rid)
    assert src["displayName"] == "源名称"
    assert restore_item["revisionId"] != rid


# ---------- 迁移 ----------


_OLD_SOURCE_CHECK = (
    "source_kind IN ("
    "'browser_put','task','revise','callback',"
    "'local_parser','content_fuse_apply',"
    "'content_fuse_consume','checkpoint_restore'"
    ")"
)


def _build_legacy_name_engine():
    """
    用途：独立旧 SQLite（八来源、无 display_name）供 ensure_schema_columns 真实迁移。
    """
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
                INSERT INTO editor_state_revisions(
                    id, workspace_id, project_id, snapshot_json,
                    state_version, snapshot_bytes, source_kind, created_at
                ) VALUES (
                    :id, 'ws_mig', 'proj_mig', :snap,
                    :ver, :nbytes, :src, :cat
                )
                """,
                {
                    "id": f"esr_{src[:12]}_{i:016d}",
                    "snap": snap,
                    "ver": f"esv_{i:032d}",
                    "nbytes": len(snap.encode("utf-8")),
                    "src": src,
                    "cat": f"2026-07-16 10:0{i}:00",
                },
            )
    return eng


def test_sqlite_idempotent_add_display_name_column(disabled_client):
    """
    用途：独立旧 SQLite Engine 真实完成八→九来源后再加 nullable display_name；
    幂等、行/八字段/索引保留；ADD COLUMN 失败阻止启动且不丢数据。
    """
    # 共享测试库已有列：仅作旁证，不替代独立旧库
    assert "display_name" in {
        c["name"] for c in inspect(engine).get_columns("editor_state_revisions")
    }
    assert hasattr(EditorStateRevisionRow, "display_name")
    # 当前库可正常命名（disabled ASGI）
    client = disabled_client
    pid = _create_project(client, name="迁移旁证")
    rows = _seed_revisions(pid, ["m0"])
    res = _patch_name(client, pid, rows[0]["id"], "旁证名")
    _assert_success_name(res, "旁证名")

    old_engine = _build_legacy_name_engine()
    with old_engine.connect() as conn:
        ddl0 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" not in ddl0
        assert "display_name" not in ddl0
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
        idx0 = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_revisions' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert "ix_esr_workspace_project_created_id" in idx0

    # 第一次：八→九来源 + 加 display_name
    ensure_schema_columns(target_engine=old_engine)
    # 第二次：幂等
    ensure_schema_columns(target_engine=old_engine)

    with old_engine.connect() as conn:
        ddl1 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" in ddl1
        cols = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        assert "display_name" in cols
        # nullable VARCHAR(160)：PRAGMA type 与 notnull
        col_info = {
            r[1]: (r[2], r[3])
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        dn_type, dn_notnull = col_info["display_name"]
        assert dn_type.upper() == "VARCHAR(160)"
        assert int(dn_notnull) == 0
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
        # 存量 display_name 全 null
        dn_vals = conn.execute(
            text(
                "SELECT display_name FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()
        assert all(r[0] is None for r in dn_vals)
        idx1 = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_revisions' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert idx1 == idx0
        # 可写 revision_restore
        new_ver = "esv_" + ("2" * 32)
        conn.execute(
            text(
                "INSERT INTO editor_state_revisions("
                "id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at, "
                "display_name"
                ") VALUES ("
                ":id, 'ws_mig', 'proj_mig', :snap, "
                ":ver, :nbytes, 'revision_restore', '2026-07-16 12:00:00', NULL)"
            ),
            {
                "id": "esr_new_ok01",
                "snap": '{"k":true}',
                "ver": new_ver,
                "nbytes": len(b'{"k":true}'),
            },
        )
        conn.commit()

    # ADD COLUMN 故障注入：异常外抛、列仍缺失、原行/索引完整
    eng2 = _build_legacy_name_engine()
    _MIG_INJECT = "p12fh_injected_add_display_name_failure"
    with eng2.connect() as conn:
        ddl_pre = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "display_name" not in ddl_pre
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

    from sqlalchemy.engine import Connection as _SAConnection

    _orig_exec_driver_sql = _SAConnection.exec_driver_sql

    def _injecting_exec_driver_sql(self, statement, *args, **kwargs):
        sql = statement if isinstance(statement, str) else str(statement)
        compact = " ".join(sql.split()).upper()
        if (
            "ALTER TABLE EDITOR_STATE_REVISIONS" in compact
            and "ADD COLUMN DISPLAY_NAME" in compact
        ):
            raise RuntimeError(_MIG_INJECT)
        return _orig_exec_driver_sql(self, statement, *args, **kwargs)

    try:
        _SAConnection.exec_driver_sql = _injecting_exec_driver_sql  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match=re.escape(_MIG_INJECT)):
            ensure_schema_columns(target_engine=eng2)
    finally:
        _SAConnection.exec_driver_sql = _orig_exec_driver_sql  # type: ignore[method-assign]

    with eng2.connect() as conn:
        ddl_post = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        # 整事务回滚：仍无 display_name，且八来源 CHECK 保留
        assert "display_name" not in ddl_post
        assert "revision_restore" not in ddl_post
        cols_post = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_revisions)")
            ).fetchall()
        }
        assert "display_name" not in cols_post
        rows_post = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at "
                "FROM editor_state_revisions ORDER BY id"
            )
        ).fetchall()
        assert rows_post == rows_pre
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
        assert idx_post == idx_pre

    # 源码：database.py 含精确 ADD COLUMN display_name
    db_src = _DATABASE_PATH.read_text(encoding="utf-8")
    flat = db_src.replace("\n", " ")
    assert "ADD COLUMN display_name" in flat
    assert "target_engine" in db_src


# ---------- 源码 / 白名单守卫 ----------


def test_production_source_guards_and_name_service_shape(disabled_client):
    """
    用途：真实 ASGI + 静态源码证据；不 import 缺失 service。
    failure-first：路由缺失时 200 断言业务失败；实现后源码形状精确。
    """
    client = disabled_client
    pid = _create_project(client, name="源码守卫HTTP")
    rows = _seed_revisions(pid, ["g0"])
    rid = rows[0]["id"]
    res = _patch_name(client, pid, rid, "守卫名")
    _assert_success_name(res, "守卫名")

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_revision_name_service.py 必须存在"
    ent = _ENTITIES_PATH.read_text(encoding="utf-8")
    assert "display_name" in ent
    schemas = _SCHEMAS_PATH.read_text(encoding="utf-8")
    assert "displayName" in schemas
    api_src = _API_PATH.read_text(encoding="utf-8")
    assert "display-name" in api_src
    assert "@router.patch" in api_src
    hist = _HISTORY_PATH.read_text(encoding="utf-8")
    assert "display_name" in hist
    svc = _NAME_SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(svc)
    fn_names = [
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "set_editor_state_revision_display_name" in fn_names
    assert "display_name" in svc
    assert _CODE_NAME_ERROR in svc
    assert "editor_state_revision_display_name_invalid" in svc
