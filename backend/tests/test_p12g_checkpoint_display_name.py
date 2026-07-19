"""
模块：P12G 手动检查点展示名称后端专项测试
用途：真实 HTTP+SQLite 验收
  PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/display-name
  及 create/list/detail 七/八键 displayName、迁移加列与零旁路。
对接：契约 docs/p12g-checkpoint-display-name-contract.md；
  editor_state_checkpoint_name_service（failure-first 不得顶层 import）。
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
from datetime import datetime, timezone
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
_WS_OTHER = "ws_other_p12g"
_SECRET = "SECRET_P12G_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints/display-name"
_INJECT_EXECUTE = "p12g_injected_execute_failure"
_INJECT_FLUSH = "p12g_injected_flush_failure"
_INJECT_COMMIT = "p12g_injected_commit_failure"

_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS_SEVEN = frozenset(
    {
        "checkpointId",
        "stateVersion",
        "snapshotBytes",
        "outlineNodeCount",
        "chapterCount",
        "createdAt",
        "displayName",
    }
)
_DETAIL_KEYS_EIGHT = _META_KEYS_SEVEN | frozenset({"snapshot"})
_RESTORE_KEYS = frozenset(
    {
        "restoredCheckpointId",
        "safetyCheckpointId",
        "stateVersion",
        "restoredAt",
    }
)

_CODE_NAME_INVALID = "editor_state_checkpoint_display_name_invalid"
_MSG_NAME_INVALID = "检查点名称无效"
_CODE_REQUEST_INVALID = "editor_state_checkpoint_display_name_request_invalid"
_MSG_REQUEST_INVALID = "检查点名称请求无效"
_CODE_NAME_ERROR = "editor_state_checkpoint_display_name_error"
_MSG_NAME_ERROR = "保存检查点名称失败"
_CODE_NOT_FOUND = "editor_state_checkpoint_not_found"
_MSG_NOT_FOUND = "检查点不存在"
_CODE_PROJECT = "project_not_found"
_MSG_PROJECT = "项目不存在"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_CODE_CSRF_INVALID = "csrf_invalid"

_OWNER_USER = "admin_p12g_owner"
_OWNER_PASS = "TestPass-P12G-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12G-Writer-0001!",
    "finance": "TestPass-P12G-Finance-0001!",
    "hr": "TestPass-P12G-Hr-0001!",
    "bidder": "TestPass-P12G-Bidder-0001!",
}

_NAME_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_name_service.py"
)
_API_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "api"
    / "editor_state_checkpoints.py"
)
_ENTITIES_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "models" / "entities.py"
)
_DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"
)
_CHECKPOINT_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_service.py"
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


def _name_url(project_id: str, checkpoint_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-checkpoints/"
        f"{checkpoint_id}/display-name"
    )


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _detail_url(project_id: str, checkpoint_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints/{checkpoint_id}"


def _create_project(
    client: TestClient,
    name: str = "P12G项目",
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
    assert "editor_state_checkpoints" not in blob
    assert "editor_state_checkpoint_name_service" not in blob
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
    assert "checkpointId" not in blob
    assert "stateVersion" not in blob
    assert "snapshot" not in blob
    assert _SECRET not in blob


def _assert_meta_seven(item: dict, *, forbid_echo: str | None = None) -> None:
    assert set(item.keys()) == _META_KEYS_SEVEN, item.keys()
    assert _CHECKPOINT_ID_RE.match(item["checkpointId"])
    assert _STATE_VERSION_RE.match(item["stateVersion"])
    assert type(item["snapshotBytes"]) is int and item["snapshotBytes"] > 0
    assert type(item["outlineNodeCount"]) is int and item["outlineNodeCount"] >= 0
    assert type(item["chapterCount"]) is int and item["chapterCount"] >= 0
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
    username = f"user_{role}_p12g{'_own' if is_owner else ''}_{secrets.token_hex(3)}"
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
        row = EditorStateCheckpointRow(
            id=checkpoint_id,
            workspace_id=workspace_id,
            project_id=project_id,
            snapshot_json=snapshot_json,
            state_version=state_version,
            snapshot_bytes=snapshot_bytes,
            outline_node_count=outline_node_count,
            chapter_count=chapter_count,
            created_at=created_at or utc_now(),
        )
        if hasattr(row, "display_name"):
            row.display_name = display_name
        db.add(row)
        db.commit()
    finally:
        db.close()


def _seed_checkpoints(
    project_id: str,
    tags: list[str],
    *,
    workspace_id: str = _WS,
) -> list[dict]:
    base = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, tag in enumerate(tags):
        state = _variant(tag)
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        chapters = snap.get("chapters") or []
        chapter_n = len(chapters) if isinstance(chapters, list) else 0
        cid = "escp_" + secrets.token_hex(16)
        _insert_raw_checkpoint(
            project_id=project_id,
            checkpoint_id=cid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            outline_node_count=0,
            chapter_count=chapter_n,
            created_at=base.replace(second=i),
            workspace_id=workspace_id,
        )
    return _db_cp_rows(project_id, workspace_id=workspace_id)


def _insert_revision(project_id: str, tag: str = "rev0") -> str:
    state = _variant(tag)
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    rid = "esr_" + secrets.token_hex(16)
    db = SessionLocal()
    try:
        db.add(
            EditorStateRevisionRow(
                id=rid,
                workspace_id=_WS,
                project_id=project_id,
                snapshot_json=snap_json,
                state_version=state["stateVersion"],
                snapshot_bytes=len(snap_json.encode("utf-8")),
                source_kind="browser_put",
                created_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    return rid


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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12G") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12g",
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


def _db_cp_rows(project_id: str, workspace_id: str | None = None) -> list[dict]:
    db = SessionLocal()
    try:
        q = db.query(EditorStateCheckpointRow).filter(
            EditorStateCheckpointRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateCheckpointRow.workspace_id == workspace_id)
        rows = list(
            q.order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
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
                "outline_node_count": int(r.outline_node_count),
                "chapter_count": int(r.chapter_count),
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


def _db_rev_rows(project_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == project_id)
            .all()
        )
        return [
            {
                "id": r.id,
                "state_version": r.state_version,
                "snapshot_json": r.snapshot_json,
                "source_kind": r.source_kind,
                "display_name": getattr(r, "display_name", None),
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
            "mode": row.mode,
            "parsed_markdown": row.parsed_markdown,
            "analysis_overview": row.analysis_overview,
            "outline_json": row.outline_json,
            "chapters_json": row.chapters_json,
            "facts_json": row.facts_json,
            "analysis_json": row.analysis_json,
            "response_matrix_json": row.response_matrix_json,
            "guidance_json": row.guidance_json,
            "business_json": row.business_json,
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
            "word_count": row.word_count,
        }
    finally:
        db.close()


def _db_task_rows(project_id: str) -> list[dict]:
    db = SessionLocal()
    try:
        rows = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .order_by(ProjectTaskRow.id)
            .all()
        )
        return [
            {
                "id": r.id,
                "type": r.type,
                "status": r.status,
                "message": r.message,
                "payload_json": r.payload_json,
            }
            for r in rows
        ]
    finally:
        db.close()


def _seed_five_domain(project_id: str, tags: list[str]) -> list[dict]:
    _ensure_editor_state(project_id, tag="cur")
    _insert_revision(project_id, tag="rev0")
    _insert_task(project_id, tag="t0")
    return _seed_checkpoints(project_id, tags)


def _domain_snapshot(project_id: str) -> dict:
    return {
        "checkpoints": _db_cp_rows(project_id),
        "revisions": _db_rev_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "tasks": _db_task_rows(project_id),
    }


def _patch_name(
    client: TestClient,
    project_id: str,
    checkpoint_id: str,
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
        _name_url(project_id, checkpoint_id),
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
    cols = {c["name"] for c in insp.get_columns("editor_state_checkpoints")}
    return "display_name" in cols


# ---------- 路由存在与成功路径 ----------


def test_route_exists_patch_success_save_overwrite_clear(disabled_client):
    """
    用途：合法保存/覆盖/清除/重复清除；响应精确一键 displayName + no-store。
    failure-first：生产未实现时 ASGI 对未知 path 返回 404，不得 200。
    """
    client = disabled_client
    pid = _create_project(client, name="命名成功路径")
    rows = _seed_five_domain(pid, ["a", "b"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)

    res = _patch_name(client, pid, cid, "版本甲")
    _assert_success_name(res, "版本甲")
    after1 = _db_cp_rows(pid)
    target1 = next(r for r in after1 if r["id"] == cid)
    assert "display_name" in target1
    assert target1["display_name"] == "版本甲"
    for r in after1:
        if r["id"] != cid:
            assert "display_name" in r
            assert r["display_name"] is None
            old = next(x for x in before["checkpoints"] if x["id"] == r["id"])
            assert r["snapshot_json"] == old["snapshot_json"]
            assert r["state_version"] == old["state_version"]
            assert r["snapshot_bytes"] == old["snapshot_bytes"]
    assert _domain_snapshot(pid)["revisions"] == before["revisions"]
    assert _domain_snapshot(pid)["editor_state"] == before["editor_state"]
    assert _domain_snapshot(pid)["project"] == before["project"]
    assert _domain_snapshot(pid)["tasks"] == before["tasks"]

    res2 = _patch_name(client, pid, cid, "版本乙")
    _assert_success_name(res2, "版本乙")
    assert next(r for r in _db_cp_rows(pid) if r["id"] == cid)[
        "display_name"
    ] == "版本乙"

    res3 = _patch_name(client, pid, cid, None)
    _assert_success_name(res3, None)
    assert next(r for r in _db_cp_rows(pid) if r["id"] == cid).get(
        "display_name"
    ) is None

    res4 = _patch_name(client, pid, cid, None)
    _assert_success_name(res4, None)

    raw = "ｖｅｒ１"
    nfkc = unicodedata.normalize("NFKC", raw)
    res5 = _patch_name(client, pid, cid, raw)
    _assert_success_name(res5, nfkc)
    assert next(r for r in _db_cp_rows(pid) if r["id"] == cid)[
        "display_name"
    ] == nfkc


def test_route_missing_is_real_http_not_import_error(disabled_client):
    """
    用途：failure-first 锚点——仅通过真实 ASGI 断言新路由；
    未实现时成功断言失败（精确 200 业务失败），绝非收集/导入异常。
    """
    client = disabled_client
    pid = _create_project(client, name="路由锚点")
    rows = _seed_checkpoints(pid, ["x"])
    cid = rows[0]["id"]
    res = client.patch(
        _name_url(pid, cid),
        json={"displayName": "锚点"},
    )
    assert res is not None
    assert hasattr(res, "status_code")
    _assert_success_name(res, "锚点")


# ---------- 请求体校验 422 ----------


@pytest.mark.parametrize(
    "label,builder,code,msg",
    [
        (
            "missing_key",
            lambda: json.dumps({}).encode("utf-8"),
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "extra_key",
            lambda: json.dumps(
                {"displayName": "ok", "extra": 1}, ensure_ascii=False
            ).encode("utf-8"),
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "snake_case",
            lambda: json.dumps(
                {"display_name": "ok"}, ensure_ascii=False
            ).encode("utf-8"),
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "array",
            lambda: b'["x"]',
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "scalar",
            lambda: b'"x"',
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "invalid_json",
            lambda: b"{not-json",
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
        (
            "empty_string",
            lambda: json.dumps({"displayName": ""}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "whitespace",
            lambda: json.dumps({"displayName": "  "}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "leading_space",
            lambda: json.dumps({"displayName": " a"}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "trailing_space",
            lambda: json.dumps({"displayName": "a "}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "forty_one",
            lambda: json.dumps(
                {"displayName": "字" * 41}, ensure_ascii=False
            ).encode("utf-8"),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "control_nl",
            lambda: json.dumps({"displayName": "a\nb"}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "control_tab",
            lambda: json.dumps({"displayName": "a\tb"}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "control_nul",
            lambda: json.dumps({"displayName": "a\x00b"}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "line_sep",
            lambda: json.dumps({"displayName": "a\u2028b"}).encode("utf-8"),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "bidi",
            lambda: json.dumps({"displayName": "a\u202eb"}).encode("utf-8"),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "number",
            lambda: json.dumps({"displayName": 1}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "bool",
            lambda: json.dumps({"displayName": True}).encode(),
            _CODE_NAME_INVALID,
            _MSG_NAME_INVALID,
        ),
        (
            "oversized_body",
            lambda: (b'{"displayName":"' + (b"A" * 2000) + b'"}'),
            _CODE_REQUEST_INVALID,
            _MSG_REQUEST_INVALID,
        ),
    ],
)
def test_patch_invalid_body_fixed_422_zero_write(
    disabled_client, label, builder, code, msg
):
    client = disabled_client
    pid = _create_project(client, name=f"非法体-{label}")
    rows = _seed_five_domain(pid, ["i0"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)
    raw = builder()
    marker = (
        _SECRET
        if _SECRET.encode() in raw
        else (raw[:32].decode("utf-8", "replace") if raw else "")
    )
    res = _patch_name(client, pid, cid, None, content=raw, raw_json=False)
    _assert_fixed_error(
        res,
        422,
        code,
        message=msg,
        forbid_echo=marker if marker and marker not in ("{", "}") else None,
    )
    assert _domain_snapshot(pid) == before


def test_patch_query_nonempty_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="query非空")
    rows = _seed_five_domain(pid, ["q0"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)
    res = _patch_name(client, pid, cid, "名字", params={"x": "1"})
    _assert_fixed_error(
        res,
        422,
        _CODE_REQUEST_INVALID,
        message=_MSG_REQUEST_INVALID,
        forbid_echo="x",
    )
    assert _domain_snapshot(pid) == before


def test_forty_non_bmp_codepoints_ok(disabled_client):
    """用途：40 个非 BMP 字符合法；41 非法。"""
    client = disabled_client
    pid = _create_project(client, name="码点边界")
    rows = _seed_checkpoints(pid, ["c0"])
    cid = rows[0]["id"]
    emoji = "😀"
    ok = emoji * 40
    assert len(ok) == 40  # Python str 按码点
    res = _patch_name(client, pid, cid, ok)
    _assert_success_name(res, ok)
    bad = emoji * 41
    res_bad = _patch_name(client, pid, cid, bad)
    _assert_fixed_error(
        res_bad, 422, _CODE_NAME_INVALID, message=_MSG_NAME_INVALID
    )


# ---------- 作用域与权限 ----------


def test_patch_project_and_checkpoint_404_priority(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="本域")
    rows = _seed_five_domain(pid, ["s0", "s1"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)

    missing_pid = "proj_missing_" + secrets.token_hex(4)
    res_p = _patch_name(client, missing_pid, cid, "x")
    _assert_fixed_error(
        res_p, 404, _CODE_PROJECT, message=_MSG_PROJECT, forbid_echo=missing_pid
    )

    foreign_pid = "proj_foreign_" + secrets.token_hex(4)
    _insert_foreign_project(project_id=foreign_pid, workspace_id=_WS_OTHER)
    foreign_cp = "escp_" + secrets.token_hex(16)
    state = _variant("foreign")
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    _insert_raw_checkpoint(
        project_id=foreign_pid,
        checkpoint_id=foreign_cp,
        snapshot_json=snap_json,
        state_version=state["stateVersion"],
        snapshot_bytes=len(snap_json.encode("utf-8")),
        workspace_id=_WS_OTHER,
    )
    res_fp = _patch_name(client, foreign_pid, foreign_cp, "x")
    _assert_fixed_error(res_fp, 404, _CODE_PROJECT, message=_MSG_PROJECT)

    pid_b = _create_project(client, name="兄弟")
    rows_b = _seed_checkpoints(pid_b, ["b0"])
    res_cross = _patch_name(client, pid, rows_b[0]["id"], "x")
    _assert_fixed_error(
        res_cross,
        404,
        _CODE_NOT_FOUND,
        message=_MSG_NOT_FOUND,
        forbid_echo=rows_b[0]["id"],
    )

    for bad_id in ("not-a-checkpoint", "escp_short", "ESCP_" + ("f" * 32)):
        res_bad = _patch_name(client, pid, bad_id, "x")
        _assert_fixed_error(
            res_bad, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=bad_id
        )

    assert _domain_snapshot(pid) == before


def test_patch_required_roles_csrf(required_client):
    client = required_client
    csrf_w = _login_role(client, "bid_writer")
    headers_w = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="权限项目", headers=headers_w)
    rows = _seed_checkpoints(pid, ["r0"])
    cid = rows[0]["id"]
    res_ok = _patch_name(client, pid, cid, "作者命名", headers=headers_w)
    _assert_success_name(res_ok, "作者命名")

    res_no = _patch_name(client, pid, cid, "无令牌")
    assert res_no.status_code == 403, res_no.text
    detail_no = res_no.json().get("detail")
    assert isinstance(detail_no, dict), res_no.text
    assert set(detail_no.keys()) == {"code", "message"}
    assert detail_no.get("code") == _CODE_CSRF_INVALID

    res_bad = _patch_name(
        client, pid, cid, "错令牌", headers={"X-CSRF-Token": "wrong-csrf-token"}
    )
    assert res_bad.status_code == 403, res_bad.text
    detail_bad = res_bad.json().get("detail")
    assert isinstance(detail_bad, dict), res_bad.text
    assert set(detail_bad.keys()) == {"code", "message"}
    assert detail_bad.get("code") == _CODE_CSRF_INVALID

    for role in ("finance", "hr", "bidder"):
        csrf_r = _login_role(client, role)
        res_r = _patch_name(
            client, pid, cid, f"{role}命名", headers={"X-CSRF-Token": csrf_r}
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
    cid = rows[0]["id"]
    before_other = {
        "revisions": _db_rev_rows(pid),
        "editor_state": _db_editor_state_row(pid),
        "project": _db_project_row(pid),
        "tasks": _db_task_rows(pid),
    }
    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "projects" in low or "editor_state_checkpoints" in low:
            captured.append((statement, parameters))
        for t in (
            "project_editor_states",
            "editor_state_revisions",
            "project_tasks",
        ):
            if t in low:
                captured.append((f"FORBIDDEN::{statement}", parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _patch_name(client, pid, cid, "SQL名")
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
        and "editor_state_checkpoints" not in s.lower()
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
        if "editor_state_checkpoints" in s.lower()
        and s.lstrip().upper().startswith("UPDATE")
    ]
    assert len(updates) == 1, updates
    sql_u, params_u = updates[0]
    low_u = sql_u.lower()
    assert " set " in f" {low_u} "
    assert " where " in f" {low_u} "
    set_part = low_u.split("set", 1)[1].split("where", 1)[0]
    assert "display_name" in set_part
    assert "state_version" not in set_part
    assert "snapshot" not in set_part
    assert "snapshot_json" not in set_part
    assert "created_at" not in set_part
    assert "workspace_id" in low_u
    assert "project_id" in low_u
    assert re.search(r"\bid\b", low_u)
    uvals = [str(v) for v in _normalize_sql_params(params_u)]
    assert cid in uvals
    assert pid in uvals
    assert _WS in uvals
    assert "SQL名" in uvals

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_checkpoint_name_service.py 必须存在"
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
    assert "EditorStateRevisionRow" not in names
    assert "db.get(EditorStateCheckpointRow" not in svc
    assert "session.get(EditorStateCheckpointRow" not in svc

    after = _domain_snapshot(pid)
    assert after["revisions"] == before_other["revisions"]
    assert after["editor_state"] == before_other["editor_state"]
    assert after["project"] == before_other["project"]
    assert after["tasks"] == before_other["tasks"]


def test_rowcount_none_zero_multi_fixed_500(disabled_client):
    """用途：可控 rowcount 直接证据，覆盖 0/None/-1/2/1。"""
    client = disabled_client
    pid = _create_project(client, name="rowcount控制")
    rows = _seed_five_domain(pid, ["rc0", "rc1", "rc2", "rc3", "rc4"])
    targets = [r["id"] for r in rows]
    before = _domain_snapshot(pid)

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_checkpoint_name_service.py 必须存在"
    name_svc = importlib.import_module(
        "app.services.editor_state_checkpoint_name_service"
    )

    def _run_with_rowcount(forced, checkpoint_id: str, display_name: str):
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
                    "editor_state_checkpoints" in sql
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
                out = name_svc.set_editor_state_checkpoint_display_name(
                    db, _WS, pid, checkpoint_id, display_name
                )
                assert out == display_name
                assert counters["commit"] == 1
                assert counters["rollback"] == 0
                return None

            with pytest.raises(name_svc.EditorStateCheckpointNameError) as ei:
                name_svc.set_editor_state_checkpoint_display_name(
                    db, _WS, pid, checkpoint_id, display_name
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

    _run_with_rowcount(0, targets[0], "rc-zero")
    assert _domain_snapshot(pid) == before

    for forced, tid, label in (
        (None, targets[1], "rc-none"),
        (-1, targets[2], "rc-neg"),
        (2, targets[3], "rc-multi"),
    ):
        _run_with_rowcount(forced, tid, label)
        assert _domain_snapshot(pid) == before

    _run_with_rowcount(1, targets[4], "行计数探针")
    after = _domain_snapshot(pid)
    assert after["revisions"] == before["revisions"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    target = next(r for r in after["checkpoints"] if r["id"] == targets[4])
    assert target["display_name"] == "行计数探针"
    for kept in targets[:4]:
        other = next(r for r in after["checkpoints"] if r["id"] == kept)
        assert other["display_name"] is None
        before_row = next(r for r in before["checkpoints"] if r["id"] == kept)
        for key in (
            "id",
            "workspace_id",
            "project_id",
            "snapshot_json",
            "state_version",
            "snapshot_bytes",
            "outline_node_count",
            "chapter_count",
            "created_at",
        ):
            assert other[key] == before_row[key]

    svc = _NAME_SERVICE_PATH.read_text(encoding="utf-8")
    assert "int(result.rowcount or 0)" not in svc
    assert "int(result.rowcount or" not in svc
    assert "result.rowcount or" not in svc
    assert " == 0" in svc
    assert " != 1" in svc


def test_execute_flush_commit_failures_rollback(disabled_client):
    """用途：服务层真实 Session 注入 execute/flush/commit 故障；固定 500 + rollback。"""
    client = disabled_client
    pid = _create_project(client, name="故障回滚命名")
    rows = _seed_five_domain(pid, ["f0", "f1", "f2"])
    targets = [rows[0]["id"], rows[1]["id"], rows[2]["id"]]
    before = _domain_snapshot(pid)

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_checkpoint_name_service.py 必须存在"
    name_svc = importlib.import_module(
        "app.services.editor_state_checkpoint_name_service"
    )

    def _run_fault(kind: str, checkpoint_id: str) -> dict:
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
                    and "editor_state_checkpoints" in low
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

            with pytest.raises(name_svc.EditorStateCheckpointNameError) as ei:
                name_svc.set_editor_state_checkpoint_display_name(
                    db, _WS, pid, checkpoint_id, f"故障{kind}"
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

        out = name_svc.set_editor_state_checkpoint_display_name(
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
    assert after["revisions"] == before["revisions"]
    assert after["editor_state"] == before["editor_state"]
    assert after["project"] == before["project"]
    assert after["tasks"] == before["tasks"]
    target = next(r for r in after["checkpoints"] if r["id"] == targets[0])
    assert target["display_name"] == "故障后可写"


# ---------- 读取七/八键 / 恢复不变量 ----------


def test_create_list_detail_seven_eight_keys_and_null_default(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="七键读取")
    # create 初始 null
    res_create = client.post(_list_url(pid), json={})
    assert res_create.status_code == 201, res_create.text
    created = res_create.json()
    _assert_meta_seven(created)
    assert created["displayName"] is None

    res_list = client.get(_list_url(pid))
    assert res_list.status_code == 200, res_list.text
    items = res_list.json()["items"]
    assert len(items) >= 1
    for it in items:
        _assert_meta_seven(it)
        assert it["displayName"] is None

    cid = created["checkpointId"]
    res_detail = client.get(_detail_url(pid, cid))
    assert res_detail.status_code == 200, res_detail.text
    d = res_detail.json()
    assert set(d.keys()) == _DETAIL_KEYS_EIGHT
    meta_only = {k: d[k] for k in _META_KEYS_SEVEN}
    _assert_meta_seven(meta_only)
    assert d["displayName"] is None
    assert "snapshot" in d and isinstance(d["snapshot"], dict)

    res_set = _patch_name(client, pid, cid, "展示名")
    _assert_success_name(res_set, "展示名")
    res_list2 = client.get(_list_url(pid))
    assert res_list2.status_code == 200, res_list2.text
    hit = next(i for i in res_list2.json()["items"] if i["checkpointId"] == cid)
    _assert_meta_seven(hit)
    assert hit["displayName"] == "展示名"

    res_detail2 = client.get(_detail_url(pid, cid))
    assert res_detail2.status_code == 200, res_detail2.text
    assert res_detail2.json()["displayName"] == "展示名"
    assert set(res_detail2.json().keys()) == _DETAIL_KEYS_EIGHT


def test_restore_does_not_copy_display_name(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="恢复不复制名")
    # 创建当前态检查点并命名
    _ensure_editor_state(pid, tag="v1")
    res_cp = client.post(_list_url(pid), json={})
    assert res_cp.status_code == 201, res_cp.text
    cid = res_cp.json()["checkpointId"]
    res_set = _patch_name(client, pid, cid, "源名称")
    _assert_success_name(res_set, "源名称")

    # 改写当前态后恢复命名检查点
    _ensure_editor_state(pid, tag="v2")
    expected = client.get(f"/api/projects/{pid}/editor-state").json()["stateVersion"]
    res_restore = client.post(
        f"{_detail_url(pid, cid)}/restore",
        json={"expectedStateVersion": expected},
    )
    assert res_restore.status_code == 200, res_restore.text
    body = res_restore.json()
    assert set(body.keys()) == _RESTORE_KEYS
    safety_id = body["safetyCheckpointId"]

    res_list = client.get(_list_url(pid))
    assert res_list.status_code == 200, res_list.text
    items = res_list.json()["items"]
    safety = next(i for i in items if i["checkpointId"] == safety_id)
    _assert_meta_seven(safety)
    assert safety["displayName"] is None
    src = next(i for i in items if i["checkpointId"] == cid)
    assert src["displayName"] == "源名称"


def test_list_sql_no_snapshot_projection(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="列表无正文")
    client.post(_list_url(pid), json={})
    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_checkpoints" in low and "select" in low.lstrip()[:20]:
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = client.get(_list_url(pid))
        assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    assert captured, "应捕获列表 SELECT"
    for sql in captured:
        low = sql.lower()
        assert "snapshot_json" not in low
        assert "display_name" in low


# ---------- 迁移 ----------


def _build_legacy_checkpoint_engine():
    """用途：独立旧 SQLite（无 display_name）供 ensure_schema_columns 真实迁移。"""
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
            """
            CREATE TABLE editor_state_checkpoints (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                project_id VARCHAR(64) NOT NULL
                    REFERENCES projects(id) ON DELETE CASCADE,
                snapshot_json TEXT NOT NULL,
                state_version VARCHAR(64) NOT NULL,
                snapshot_bytes INTEGER NOT NULL,
                outline_node_count INTEGER NOT NULL DEFAULT 0,
                chapter_count INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
                CHECK (outline_node_count >= 0),
                CHECK (chapter_count >= 0)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE INDEX ix_escp_workspace_project_created_id
            ON editor_state_checkpoints(workspace_id, project_id, created_at, id)
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws_mig', '迁移空间', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('proj_mig', 'ws_mig', '迁移项目')"
        )
        for i in range(3):
            snap = json.dumps(
                {"i": i, "pad": "x" * 8},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            conn.exec_driver_sql(
                """
                INSERT INTO editor_state_checkpoints(
                    id, workspace_id, project_id, snapshot_json,
                    state_version, snapshot_bytes, outline_node_count,
                    chapter_count, created_at
                ) VALUES (
                    :id, 'ws_mig', 'proj_mig', :snap,
                    :ver, :nbytes, 0, 0, :cat
                )
                """,
                {
                    "id": f"escp_mig_{i:024d}",
                    "snap": snap,
                    "ver": f"esv_{i:032d}",
                    "nbytes": len(snap.encode("utf-8")),
                    "cat": f"2026-07-16 10:0{i}:00",
                },
            )
    return eng


def test_sqlite_idempotent_add_display_name_column(disabled_client):
    """
    用途：独立旧 SQLite Engine 幂等加 nullable display_name；
    失败阻止启动且不丢数据。
    """
    assert "display_name" in {
        c["name"] for c in inspect(engine).get_columns("editor_state_checkpoints")
    }
    assert hasattr(EditorStateCheckpointRow, "display_name")
    client = disabled_client
    pid = _create_project(client, name="迁移旁证")
    rows = _seed_checkpoints(pid, ["m0"])
    res = _patch_name(client, pid, rows[0]["id"], "旁证名")
    _assert_success_name(res, "旁证名")

    old_engine = _build_legacy_checkpoint_engine()
    with old_engine.connect() as conn:
        ddl0 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_checkpoints'"
            )
        ).scalar_one()
        assert "display_name" not in ddl0
        count0 = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_checkpoints")
        ).scalar_one()
        assert count0 == 3
        rows0 = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, outline_node_count, "
                "chapter_count, created_at "
                "FROM editor_state_checkpoints ORDER BY id"
            )
        ).fetchall()
        idx0 = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_checkpoints' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert "ix_escp_workspace_project_created_id" in idx0

    ensure_schema_columns(target_engine=old_engine)
    ensure_schema_columns(target_engine=old_engine)

    with old_engine.connect() as conn:
        cols = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_checkpoints)")
            ).fetchall()
        }
        assert "display_name" in cols
        col_info = {
            r[1]: (r[2], r[3])
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_checkpoints)")
            ).fetchall()
        }
        dn_type, dn_notnull = col_info["display_name"]
        assert dn_type.upper() == "VARCHAR(160)"
        assert int(dn_notnull) == 0
        count1 = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_checkpoints")
        ).scalar_one()
        assert count1 == 3
        rows1 = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, outline_node_count, "
                "chapter_count, created_at "
                "FROM editor_state_checkpoints ORDER BY id"
            )
        ).fetchall()
        assert rows1 == rows0
        dn_vals = conn.execute(
            text(
                "SELECT display_name FROM editor_state_checkpoints ORDER BY id"
            )
        ).fetchall()
        assert all(r[0] is None for r in dn_vals)
        idx1 = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_checkpoints' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert idx1 == idx0

    eng2 = _build_legacy_checkpoint_engine()
    _MIG_INJECT = "p12g_injected_add_display_name_failure"
    with eng2.connect() as conn:
        rows_pre = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, outline_node_count, "
                "chapter_count, created_at "
                "FROM editor_state_checkpoints ORDER BY id"
            )
        ).fetchall()
        idx_pre = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_checkpoints' "
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
            "ALTER TABLE EDITOR_STATE_CHECKPOINTS" in compact
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
        cols_post = {
            r[1]
            for r in conn.execute(
                text("PRAGMA table_info(editor_state_checkpoints)")
            ).fetchall()
        }
        assert "display_name" not in cols_post
        rows_post = conn.execute(
            text(
                "SELECT id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, outline_node_count, "
                "chapter_count, created_at "
                "FROM editor_state_checkpoints ORDER BY id"
            )
        ).fetchall()
        assert rows_post == rows_pre
        idx_post = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='editor_state_checkpoints' "
                    "ORDER BY name"
                )
            ).fetchall()
        }
        assert idx_post == idx_pre

    db_src = _DATABASE_PATH.read_text(encoding="utf-8")
    flat = db_src.replace("\n", " ")
    assert "ADD COLUMN display_name" in flat
    assert "editor_state_checkpoints" in flat
    assert "target_engine" in db_src


# ---------- 源码 / 白名单守卫 ----------


def test_production_source_guards_and_name_service_shape(disabled_client):
    """用途：真实 ASGI + 静态源码证据；不 import 缺失 service。"""
    client = disabled_client
    pid = _create_project(client, name="源码守卫HTTP")
    rows = _seed_checkpoints(pid, ["g0"])
    cid = rows[0]["id"]
    res = _patch_name(client, pid, cid, "守卫名")
    _assert_success_name(res, "守卫名")

    assert _NAME_SERVICE_PATH.is_file(), "editor_state_checkpoint_name_service.py 必须存在"
    ent = _ENTITIES_PATH.read_text(encoding="utf-8")
    assert "display_name" in ent
    assert "EditorStateCheckpointRow" in ent
    schemas = _SCHEMAS_PATH.read_text(encoding="utf-8")
    assert "displayName" in schemas
    assert "EditorStateCheckpoint" in schemas
    api_src = _API_PATH.read_text(encoding="utf-8")
    assert "display-name" in api_src
    assert "@router.patch" in api_src
    # A. 精确元数据：_meta_out 与详情映射禁止 data.get("display_name") 静默伪装 null
    assert 'data.get("display_name")' not in api_src
    assert "data.get('display_name')" not in api_src
    assert api_src.count('data["display_name"]') >= 2
    # AST：_meta_out 与 get 详情两处 data 映射必须 Subscript，禁止 Call(.get)
    api_tree = ast.parse(api_src)
    meta_fn = next(
        n
        for n in api_tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_meta_out"
    )
    get_fn = next(
        n
        for n in api_tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "get_editor_state_checkpoint"
    )

    def _data_display_name_exprs(fn: ast.FunctionDef) -> list[ast.AST]:
        found: list[ast.AST] = []
        for node in ast.walk(fn):
            if not isinstance(node, ast.keyword) or node.arg != "display_name":
                continue
            val = node.value
            # 合法：data["display_name"]
            if isinstance(val, ast.Subscript) and isinstance(val.value, ast.Name):
                if val.value.id == "data":
                    found.append(val)
                    continue
            # 非法：data.get("display_name")
            if isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute):
                if (
                    isinstance(val.func.value, ast.Name)
                    and val.func.value.id == "data"
                    and val.func.attr == "get"
                ):
                    found.append(val)
                    continue
        return found

    meta_exprs = _data_display_name_exprs(meta_fn)
    get_exprs = _data_display_name_exprs(get_fn)
    assert len(meta_exprs) == 1, meta_exprs
    assert len(get_exprs) == 1, get_exprs
    for expr in (meta_exprs[0], get_exprs[0]):
        assert isinstance(expr, ast.Subscript), (
            f"必须 data['display_name']，实际 {ast.dump(expr)}"
        )
        assert isinstance(expr.value, ast.Name) and expr.value.id == "data"
        key = expr.slice
        if isinstance(key, ast.Constant):
            assert key.value == "display_name"
        else:
            assert getattr(key, "value", None) == "display_name" or (
                isinstance(key, ast.Constant) is False
                and str(getattr(key, "s", "")) == "display_name"
            )
    svc_src = _CHECKPOINT_SERVICE_PATH.read_text(encoding="utf-8")
    assert "display_name" in svc_src
    name_src = _NAME_SERVICE_PATH.read_text(encoding="utf-8")
    assert "set_editor_state_checkpoint_display_name" in name_src
    assert "EditorStateCheckpointNameError" in name_src
    # 禁止读取快照/当前态/修订
    assert "snapshot_json" not in name_src
    assert "ProjectEditorStateRow" not in name_src
    assert "EditorStateRevisionRow" not in name_src
