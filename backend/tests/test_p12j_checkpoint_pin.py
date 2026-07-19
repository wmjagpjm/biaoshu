"""
模块：P12J-A 检查点单条固定与保护裁剪后端专项测试
用途：真实 HTTP+SQLite 验收
  PATCH /api/projects/{projectId}/editor-state-checkpoints/{checkpointId}/pin
  及固定上限 5/10MiB、项目锁、保护性裁剪、required/CSRF、零写与迁移列证据。
对接：契约 docs/p12j-checkpoint-pinning-backend-contract.md；
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
from app.services import auth_service, editor_state_checkpoint_service, editor_state_service

_WS = "ws_local"
_WS_OTHER = "ws_other_p12ja"
_SECRET = "SECRET_P12JA_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints/pin"
_INJECT_EXECUTE = "p12ja_injected_execute_failure"
_INJECT_FLUSH = "p12ja_injected_flush_failure"
_INJECT_COMMIT = "p12ja_injected_commit_failure"

_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS_EIGHT = frozenset(
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
_DETAIL_KEYS_NINE = _META_KEYS_EIGHT | {"snapshot"}

_CODE_PIN_LIMIT = "editor_state_checkpoint_pin_limit"
_MSG_PIN_LIMIT = "固定检查点已达上限"
_CODE_PIN_FAILED = "editor_state_checkpoint_pin_failed"
_MSG_PIN_FAILED = "保存检查点固定状态失败"
_CODE_PIN_INVALID = "editor_state_checkpoint_pin_request_invalid"
_MSG_PIN_INVALID = "检查点固定请求无效"
_CODE_NOT_FOUND = "editor_state_checkpoint_not_found"
_MSG_NOT_FOUND = "检查点不存在"
_CODE_PROJECT = "project_not_found"
_MSG_PROJECT = "项目不存在"
_CODE_CORRUPT = "editor_state_checkpoint_corrupt"
_CODE_ROLE_FORBIDDEN = "role_forbidden"
_MSG_ROLE_FORBIDDEN = "当前角色无权访问该功能"
_CODE_CSRF_INVALID = "csrf_invalid"
_MSG_CSRF_INVALID = "CSRF 校验失败"

_OWNER_USER = "admin_p12ja_owner"
_OWNER_PASS = "TestPass-P12JA-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12JA-Writer-0001!",
    "finance": "TestPass-P12JA-Finance-0001!",
    "hr": "TestPass-P12JA-Hr-0001!",
    "bidder": "TestPass-P12JA-Bidder-0001!",
}

_PIN_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_pin_service.py"
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


def _pin_url(project_id: str, checkpoint_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-checkpoints/"
        f"{checkpoint_id}/pin"
    )


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _detail_url(project_id: str, checkpoint_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints/{checkpoint_id}"


def _delete_url(project_id: str, checkpoint_id: str) -> str:
    return _detail_url(project_id, checkpoint_id)


def _restore_url(project_id: str, checkpoint_id: str) -> str:
    return f"{_detail_url(project_id, checkpoint_id)}/restore"


def _create_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _create_project(
    client: TestClient,
    name: str = "P12J-A项目",
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
    assert "editor_state_checkpoints" not in blob
    assert "editor_state_checkpoint_pin_service" not in blob
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
    assert "checkpointId" not in blob
    assert "stateVersion" not in blob
    assert "snapshot" not in blob
    assert _SECRET not in blob


def _assert_meta_eight(item: dict, *, is_pinned: bool = False) -> None:
    assert set(item.keys()) == _META_KEYS_EIGHT, item.keys()
    assert _CHECKPOINT_ID_RE.match(item["checkpointId"])
    assert _STATE_VERSION_RE.match(item["stateVersion"])
    assert type(item["snapshotBytes"]) is int and item["snapshotBytes"] > 0
    assert type(item["outlineNodeCount"]) is int and item["outlineNodeCount"] >= 0
    assert type(item["chapterCount"]) is int and item["chapterCount"] >= 0
    assert type(item["createdAt"]) is str and item["createdAt"]
    dn = item["displayName"]
    assert dn is None or type(dn) is str
    assert type(item["isPinned"]) is bool
    assert item["isPinned"] is is_pinned


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
    username = f"user_{role}_p12ja{'_own' if is_owner else ''}_{secrets.token_hex(3)}"
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
    created_at: datetime | None = None,
    workspace_id: str = _WS,
    display_name: str | None = None,
    is_pinned: bool = False,
    outline_node_count: int = 0,
    chapter_count: int = 0,
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
            display_name=display_name,
            is_pinned=is_pinned,
            created_at=created_at or utc_now(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()


def _seed_checkpoints(
    project_id: str,
    tags: list[str],
    *,
    base_time: datetime | None = None,
    workspace_id: str = _WS,
    pad: str = "",
) -> list[dict]:
    base = base_time or datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    for i, tag in enumerate(tags):
        state = _variant(tag, pad=pad)
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        cid = "escp_" + secrets.token_hex(16)
        chapters = snap.get("chapters") or []
        chapter_n = len(chapters) if isinstance(chapters, list) else 0
        _insert_raw_checkpoint(
            project_id=project_id,
            checkpoint_id=cid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            created_at=base + timedelta(seconds=i),
            workspace_id=workspace_id,
            chapter_count=chapter_n,
        )
    return _db_cp_rows(project_id, workspace_id=workspace_id)


def _insert_revision(project_id: str, tag: str = "r0") -> str:
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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12JA") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12ja",
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
                "display_name": r.display_name,
                "is_pinned": bool(r.is_pinned),
            }
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
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
        return [
            {
                "id": r.id,
                "state_version": r.state_version,
                "snapshot_bytes": int(r.snapshot_bytes),
                "snapshot_json": r.snapshot_json,
                "source_kind": r.source_kind,
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
        "checkpoints": _db_cp_rows(project_id),
        "revisions": _db_rev_rows(project_id),
        "editor_state": _db_editor_state_row(project_id),
        "project": _db_project_row(project_id),
        "tasks": _db_task_rows(project_id),
    }


def _seed_five_domain(project_id: str, tags: list[str]) -> list[dict]:
    _ensure_editor_state(project_id, "cur")
    _insert_revision(project_id, "rev_keep")
    _insert_task(project_id, tag="keep")
    return _seed_checkpoints(project_id, tags)


def _pin_counts(project_id: str) -> tuple[int, int]:
    rows = _db_cp_rows(project_id)
    pinned = [r for r in rows if r.get("is_pinned") is True]
    return len(pinned), sum(int(r["snapshot_bytes"]) for r in pinned)


def _patch_pin(
    client: TestClient,
    project_id: str,
    checkpoint_id: str,
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
        _pin_url(project_id, checkpoint_id),
        **kwargs,
    )


def _set_pinned_sql(checkpoint_id: str, value: object) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE editor_state_checkpoints SET is_pinned = :v WHERE id = :id"
            ),
            {"v": value, "id": checkpoint_id},
        )
        db.commit()
    finally:
        db.close()


def _set_corrupt_is_pinned_sql(checkpoint_id: str, value: int = 2) -> None:
    """
    用途：独立连接临时 PRAGMA ignore_check_constraints 写入非法 is_pinned，
    绕过 0/1 CHECK 以验证服务层原始 Integer 投影与元数据校验。
    """
    with engine.connect() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        try:
            conn.execute(
                text(
                    "UPDATE editor_state_checkpoints SET is_pinned = :v WHERE id = :id"
                ),
                {"v": value, "id": checkpoint_id},
            )
            conn.commit()
        finally:
            conn.execute(text("PRAGMA ignore_check_constraints = OFF"))
            _ = conn.execute(text("PRAGMA ignore_check_constraints")).scalar()


def _set_snapshot_bytes_sql(checkpoint_id: str, value: int) -> None:
    db = SessionLocal()
    try:
        db.execute(
            text(
                "UPDATE editor_state_checkpoints SET snapshot_bytes = :v WHERE id = :id"
            ),
            {"v": value, "id": checkpoint_id},
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
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)

    res = _patch_pin(client, pid, cid, True)
    _assert_success_pin(res, True)
    after1 = _db_cp_rows(pid)
    target1 = next(r for r in after1 if r["id"] == cid)
    assert target1["is_pinned"] is True
    for r in after1:
        if r["id"] != cid:
            assert r.get("is_pinned") is False
            old = next(x for x in before["checkpoints"] if x["id"] == r["id"])
            assert r["snapshot_json"] == old["snapshot_json"]
            assert r["state_version"] == old["state_version"]
            assert r["snapshot_bytes"] == old["snapshot_bytes"]
    snap1 = _domain_snapshot(pid)
    assert snap1["revisions"] == before["revisions"]
    assert snap1["editor_state"] == before["editor_state"]
    assert snap1["project"] == before["project"]
    assert snap1["tasks"] == before["tasks"]
    assert _pin_counts(pid) == (1, target1["snapshot_bytes"])

    before_idem = _domain_snapshot(pid)
    res_idem = _patch_pin(client, pid, cid, True)
    _assert_success_pin(res_idem, True)
    assert _domain_snapshot(pid) == before_idem
    assert _pin_counts(pid) == (1, target1["snapshot_bytes"])

    res_off = _patch_pin(client, pid, cid, False)
    _assert_success_pin(res_off, False)
    assert next(r for r in _db_cp_rows(pid) if r["id"] == cid)["is_pinned"] is False
    assert _pin_counts(pid) == (0, 0)

    before_off = _domain_snapshot(pid)
    res_off2 = _patch_pin(client, pid, cid, False)
    _assert_success_pin(res_off2, False)
    assert _domain_snapshot(pid) == before_off


def test_route_missing_is_real_http_not_import_error(disabled_client):
    """用途：合法 PATCH 入口精确 200（非 import/收集错误、非宽状态集合）。"""
    client = disabled_client
    pid = _create_project(client, name="入口探测")
    rows = _seed_checkpoints(pid, ["probe"])
    res = _patch_pin(client, pid, rows[0]["id"], True)
    _assert_success_pin(res, True)


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
    assert next(r for r in _db_cp_rows(pid) if r["id"] == victim)["is_pinned"] is False


def test_pin_bytes_limit_10mib_zero_write(disabled_client, monkeypatch):
    """
    用途：固定快照合计上限（契约 10 MiB）；超限 409 零写。
    说明：单条 snapshot_bytes 库 CHECK ≤2MiB，故压低服务端字节上限做真实配额证明。
    """
    import app.services.editor_state_checkpoint_pin_service as pin_svc

    budget = 300
    monkeypatch.setattr(pin_svc, "MAX_PINNED_BYTES_PER_PROJECT", budget)
    monkeypatch.setattr(
        editor_state_checkpoint_service,
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


def test_pinned_survives_create_trim(disabled_client, monkeypatch):
    """用途：手动创建裁剪时固定旧行保留；非固定最新前缀补足。"""
    client = disabled_client
    pid = _create_project(client, name="创建裁剪保护")
    _ensure_editor_state(pid, "cur")
    rows = _seed_checkpoints(pid, [f"t{i}" for i in range(12)])
    oldest = rows[-1]["id"]
    _assert_success_pin(_patch_pin(client, pid, oldest, True), True)
    monkeypatch.setattr(
        editor_state_checkpoint_service, "MAX_CHECKPOINTS_PER_PROJECT", 5
    )
    res = client.post(_create_url(pid), json={})
    assert res.status_code == 201, res.text
    kept = _db_cp_rows(pid)
    kept_ids = {r["id"] for r in kept}
    assert oldest in kept_ids
    assert next(r for r in kept if r["id"] == oldest)["is_pinned"] is True
    assert len(kept) == 5
    assert sum(1 for r in kept if r["is_pinned"]) == 1


def test_pinned_and_protect_id_survive_restore_trim(disabled_client, monkeypatch):
    """用途：恢复裁剪同时保护固定行与本轮 safety protect_id。"""
    client = disabled_client
    pid = _create_project(client, name="恢复双保护")
    current = _ensure_editor_state(pid, "cur")
    # 目标检查点（将被恢复）
    target_state = _variant("target")
    target_snap = editor_state_service.extract_canonical_snapshot(target_state)
    target_json = editor_state_service.canonical_snapshot_json(target_snap)
    target_id = "escp_" + secrets.token_hex(16)
    base = datetime(2026, 7, 2, 10, 0, 0, tzinfo=timezone.utc)
    _insert_raw_checkpoint(
        project_id=pid,
        checkpoint_id=target_id,
        snapshot_json=target_json,
        state_version=target_state["stateVersion"],
        snapshot_bytes=len(target_json.encode("utf-8")),
        created_at=base,
        chapter_count=1,
    )
    # 最旧固定 + 若干普通
    old_rows = _seed_checkpoints(
        pid,
        [f"o{i}" for i in range(10)],
        base_time=base - timedelta(hours=2),
    )
    pinned_old = old_rows[-1]["id"]
    _assert_success_pin(_patch_pin(client, pid, pinned_old, True), True)

    monkeypatch.setattr(
        editor_state_checkpoint_service, "MAX_CHECKPOINTS_PER_PROJECT", 6
    )
    res = client.post(
        _restore_url(pid, target_id),
        json={"expectedStateVersion": current["stateVersion"]},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    safety_id = body["safetyCheckpointId"]
    kept = _db_cp_rows(pid)
    kept_ids = {r["id"] for r in kept}
    assert pinned_old in kept_ids
    assert safety_id in kept_ids
    assert next(r for r in kept if r["id"] == pinned_old)["is_pinned"] is True
    assert len(kept) == 6


def test_explicit_delete_pinned_still_allowed(disabled_client):
    """用途：P12H 显式 DELETE 固定行仍允许；删除后不补写。"""
    client = disabled_client
    pid = _create_project(client, name="删固定")
    rows = _seed_five_domain(pid, ["d0", "d1"])
    cid = rows[0]["id"]
    _assert_success_pin(_patch_pin(client, pid, cid, True), True)
    res = client.request("DELETE", _delete_url(pid, cid))
    assert res.status_code == 204, res.text
    assert all(r["id"] != cid for r in _db_cp_rows(pid))
    assert _pin_counts(pid) == (0, 0)


def test_patch_invalid_body_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="非法体")
    rows = _seed_five_domain(pid, ["ib"])
    cid = rows[0]["id"]
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
        res = _patch_pin(client, pid, cid, content=raw, raw_json=False)
        _assert_fixed_error(
            res, 422, _CODE_PIN_INVALID, message=_MSG_PIN_INVALID
        )
        assert _domain_snapshot(pid) == before


def test_patch_query_nonempty_fixed_422_zero_write(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="非法query")
    rows = _seed_five_domain(pid, ["iq"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)
    res = _patch_pin(client, pid, cid, True, params={"x": "1"})
    _assert_fixed_error(res, 422, _CODE_PIN_INVALID, message=_MSG_PIN_INVALID)
    assert _domain_snapshot(pid) == before


def test_patch_project_and_checkpoint_404_priority(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="404优先")
    rows = _seed_five_domain(pid, ["f0"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)

    missing_proj = "proj_" + secrets.token_hex(8)
    res_p = _patch_pin(client, missing_proj, cid, True)
    _assert_fixed_error(
        res_p, 404, _CODE_PROJECT, message=_MSG_PROJECT, forbid_echo=missing_proj
    )
    assert _domain_snapshot(pid) == before

    missing_cid = "escp_" + secrets.token_hex(16)
    res_c = _patch_pin(client, pid, missing_cid, True)
    _assert_fixed_error(
        res_c, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=missing_cid
    )
    assert _domain_snapshot(pid) == before

    pid_b = _create_project(client, name="404跨项目B")
    before_b = _domain_snapshot(pid_b)
    res_x = _patch_pin(client, pid_b, cid, True)
    _assert_fixed_error(
        res_x, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=cid
    )
    assert _domain_snapshot(pid) == before
    assert _domain_snapshot(pid_b) == before_b

    # 跨工作空间：检查点挂在外域空间
    foreign_pid = "proj_foreign_" + secrets.token_hex(4)
    _insert_foreign_project(project_id=foreign_pid, workspace_id=_WS_OTHER)
    foreign_state = _variant("fx")
    foreign_snap = editor_state_service.extract_canonical_snapshot(foreign_state)
    foreign_json = editor_state_service.canonical_snapshot_json(foreign_snap)
    foreign_cid = "escp_" + secrets.token_hex(16)
    _insert_raw_checkpoint(
        project_id=foreign_pid,
        checkpoint_id=foreign_cid,
        snapshot_json=foreign_json,
        state_version=foreign_state["stateVersion"],
        snapshot_bytes=len(foreign_json.encode("utf-8")),
        workspace_id=_WS_OTHER,
        chapter_count=1,
    )
    before_f = _domain_snapshot(pid)
    res_ws = _patch_pin(client, pid, foreign_cid, True)
    _assert_fixed_error(
        res_ws, 404, _CODE_NOT_FOUND, message=_MSG_NOT_FOUND, forbid_echo=foreign_cid
    )
    assert _domain_snapshot(pid) == before_f


def _assert_auth_gate(
    res,
    status: int,
    code: str,
    message: str,
    *,
    forbid_echo: str | None = None,
) -> None:
    """用途：required CSRF/角色门固定 status + 原生 dict 正文；禁止回显输入 token。"""
    assert res.status_code == status, res.text
    detail = res.json().get("detail")
    assert type(detail) is dict, res.text
    assert set(detail.keys()) == {"code", "message"}
    assert detail.get("code") == code
    assert detail.get("message") == message
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    if forbid_echo:
        assert forbid_echo not in blob


def test_patch_required_roles_csrf(required_client):
    client = required_client
    csrf_w = _login_role(client, "bid_writer")
    headers_w = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="required固定", headers=headers_w)
    rows = _seed_checkpoints(pid, ["rw"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)

    res_no = _patch_pin(client, pid, cid, True)
    _assert_auth_gate(res_no, 403, _CODE_CSRF_INVALID, _MSG_CSRF_INVALID)
    assert _domain_snapshot(pid) == before

    bad_token = "wrong-csrf-token-p12ja"
    res_bad = _patch_pin(
        client, pid, cid, True, headers={"X-CSRF-Token": bad_token}
    )
    _assert_auth_gate(
        res_bad,
        403,
        _CODE_CSRF_INVALID,
        _MSG_CSRF_INVALID,
        forbid_echo=bad_token,
    )
    assert _domain_snapshot(pid) == before

    for role in ("finance", "hr", "bidder"):
        csrf_r = _login_role(client, role)
        res = _patch_pin(
            client, pid, cid, True, headers={"X-CSRF-Token": csrf_r}
        )
        _assert_auth_gate(
            res,
            403,
            _CODE_ROLE_FORBIDDEN,
            _MSG_ROLE_FORBIDDEN,
            forbid_echo=csrf_r,
        )
        assert _domain_snapshot(pid) == before

    csrf_own = _login_role(client, "finance", is_owner=True)
    res_own = _patch_pin(
        client, pid, cid, True, headers={"X-CSRF-Token": csrf_own}
    )
    _assert_auth_gate(
        res_own,
        403,
        _CODE_ROLE_FORBIDDEN,
        _MSG_ROLE_FORBIDDEN,
        forbid_echo=csrf_own,
    )
    assert _domain_snapshot(pid) == before

    csrf_w2 = _login_role(client, "bid_writer")
    res_ok = _patch_pin(
        client, pid, cid, True, headers={"X-CSRF-Token": csrf_w2}
    )
    _assert_success_pin(res_ok, True)


def test_create_list_search_detail_eight_nine_keys_after_pin(disabled_client):
    """
    用途：create 固定 isPinned=false；pin 后 list/search/detail 精确八/九键；
      目标 true、其它 false；原生 boolean。
    """
    client = disabled_client
    pid = _create_project(client, name="八键固定读取")
    _ensure_editor_state(pid, "cur_pin_read")
    create_res = client.post(_create_url(pid), json={})
    assert create_res.status_code == 201, create_res.text
    created = create_res.json()
    _assert_meta_eight(created, is_pinned=False)
    assert created["isPinned"] is False

    rows = _seed_five_domain(pid, ["k0", "k1"])
    # _db_cp_rows 含 create 行；按 seed 标签挑目标，避免 pin 到 create
    by_seed = {r["id"]: r for r in rows}
    assert created["checkpointId"] in by_seed
    seeded = [r for r in rows if r["id"] != created["checkpointId"]]
    assert len(seeded) >= 2
    cid = seeded[0]["id"]
    other = seeded[1]["id"]
    _assert_success_pin(_patch_pin(client, pid, cid, True), True)

    list_res = client.get(_list_url(pid))
    assert list_res.status_code == 200, list_res.text
    by_id = {it["checkpointId"]: it for it in list_res.json()["items"]}
    _assert_meta_eight(by_id[cid], is_pinned=True)
    _assert_meta_eight(by_id[other], is_pinned=False)
    _assert_meta_eight(by_id[created["checkpointId"]], is_pinned=False)

    search_res = client.post(
        f"/api/projects/{pid}/editor-state-checkpoints/search",
        json={"query": "章节"},
    )
    assert search_res.status_code == 200, search_res.text
    for item in search_res.json()["items"]:
        want = item["checkpointId"] == cid
        _assert_meta_eight(item, is_pinned=want)

    detail_res = client.get(_detail_url(pid, cid))
    assert detail_res.status_code == 200, detail_res.text
    detail = detail_res.json()
    assert set(detail.keys()) == _DETAIL_KEYS_NINE
    meta_only = {k: detail[k] for k in _META_KEYS_EIGHT}
    _assert_meta_eight(meta_only, is_pinned=True)
    assert isinstance(detail["snapshot"], dict)

    # 恢复前安全检查点初始 isPinned=false
    expected = client.get(f"/api/projects/{pid}/editor-state").json()["stateVersion"]
    restore_res = client.post(
        _restore_url(pid, cid),
        json={"expectedStateVersion": expected},
    )
    assert restore_res.status_code == 200, restore_res.text
    safety_id = restore_res.json()["safetyCheckpointId"]
    list_after = client.get(_list_url(pid))
    assert list_after.status_code == 200, list_after.text
    safety = next(
        i for i in list_after.json()["items"] if i["checkpointId"] == safety_id
    )
    _assert_meta_eight(safety, is_pinned=False)


def test_corrupt_is_pinned_meta_fixed_500_zero_write(disabled_client):
    """用途：锁后原始 is_pinned 非法值固定 500 且零写；禁止 ORM Boolean 吞掉 2。"""
    client = disabled_client
    pid = _create_project(client, name="坏固定元数据")
    rows = _seed_five_domain(pid, ["bad0", "bad1", "bad2"])
    target_id = rows[0]["id"]
    sibling_id = rows[1]["id"]
    clean_id = rows[2]["id"]
    _set_corrupt_is_pinned_sql(target_id, 2)
    _set_corrupt_is_pinned_sql(sibling_id, 2)
    before = _domain_snapshot(pid)
    res_t = _patch_pin(client, pid, target_id, True)
    _assert_fixed_error(res_t, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before
    res_s = _patch_pin(client, pid, clean_id, True)
    _assert_fixed_error(res_s, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before


def test_read_paths_corrupt_is_pinned_list_detail_search_zero_write(disabled_client):
    """
    用途：list/detail/search 在原始 is_pinned=2 时固定 corrupt；
      未命中候选亦整次失败；读路径五域零写；禁止 ORM Boolean 吞 2。
    """
    client = disabled_client
    pid = _create_project(client, name="读取坏固定")
    rows = _seed_five_domain(pid, ["r0", "r1", "r2"])
    victim = rows[0]["id"]
    non_hit = rows[1]["id"]
    _set_corrupt_is_pinned_sql(victim, 2)
    domain_before = _domain_snapshot(pid)

    list_res = client.get(_list_url(pid))
    assert list_res.status_code == 500, list_res.text
    assert list_res.json()["detail"]["code"] == _CODE_CORRUPT
    assert victim not in list_res.text
    assert _SECRET not in list_res.text

    detail_res = client.get(_detail_url(pid, victim))
    assert detail_res.status_code == 500, detail_res.text
    assert detail_res.json()["detail"]["code"] == _CODE_CORRUPT
    assert victim not in detail_res.text

    # 恢复 victim 合法固定值后，未命中候选 is_pinned=2 仍使 search 整次失败
    _set_pinned_sql(victim, 0)
    _set_corrupt_is_pinned_sql(non_hit, 2)
    domain_mid = _domain_snapshot(pid)
    search_res = client.post(
        f"/api/projects/{pid}/editor-state-checkpoints/search",
        json={"query": "章节r0"},
    )
    assert search_res.status_code == 500, search_res.text
    assert search_res.json()["detail"]["code"] == _CODE_CORRUPT
    assert non_hit not in search_res.text
    assert victim not in search_res.text
    assert _domain_snapshot(pid) == domain_mid
    # list 也应对 sibling 坏值失败
    list2 = client.get(_list_url(pid))
    assert list2.status_code == 500, list2.text
    assert list2.json()["detail"]["code"] == _CODE_CORRUPT
    assert _domain_snapshot(pid) == domain_mid
    assert domain_before["revisions"] == domain_mid["revisions"]


def test_detect_over_20_rows_fixed_500(disabled_client):
    """用途：锁后读到 21 行侦测破坏 20 条不变量 → pin_failed。"""
    client = disabled_client
    pid = _create_project(client, name="21行侦测")
    rows = _seed_five_domain(pid, [f"x{i}" for i in range(21)])
    assert len(rows) == 21
    before = _domain_snapshot(pid)
    res = _patch_pin(client, pid, rows[0]["id"], True)
    _assert_fixed_error(res, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before


def test_trim_corrupt_pinned_quota_rolls_back(disabled_client, monkeypatch):
    """用途：裁剪前固定集合超 5 条 → 精确 500 corrupt 且整事务回滚。"""
    client = disabled_client
    pid = _create_project(client, name="裁剪固定配额")
    _ensure_editor_state(pid, "cur")
    rows = _seed_checkpoints(pid, [f"q{i}" for i in range(6)])
    for r in rows:
        _set_pinned_sql(r["id"], 1)
    before = _domain_snapshot(pid)
    monkeypatch.setattr(
        editor_state_checkpoint_service, "MAX_CHECKPOINTS_PER_PROJECT", 20
    )
    # 通过 create 触发 trim
    res = client.post(_create_url(pid), json={})
    detail = res.json().get("detail")
    assert res.status_code == 500, res.text
    assert isinstance(detail, dict), res.text
    assert detail.get("code") == _CODE_CORRUPT
    assert _domain_snapshot(pid) == before


def test_execute_flush_commit_failures_rollback(disabled_client, monkeypatch):
    """用途：execute/flush/commit 失败固定 500 脱敏并 rollback 零写。"""
    assert _PIN_SERVICE_PATH.is_file(), "pin service 必须存在"
    import app.api.editor_state_checkpoints as api_mod
    import app.services.editor_state_checkpoint_pin_service as pin_svc

    client = disabled_client
    pid = _create_project(client, name="回滚")
    rows = _seed_five_domain(pid, ["rb0", "rb1"])
    cid = rows[0]["id"]
    before = _domain_snapshot(pid)
    real_set = pin_svc.set_editor_state_checkpoint_pin

    def _wrap_execute_fail(db, *a, **k):
        orig_execute = db.execute
        calls = {"n": 0}

        def _e(*ea, **ek):
            calls["n"] += 1
            stmt = ea[0] if ea else None
            sql = str(getattr(stmt, "string", stmt) or "")
            low = sql.lower()
            if "editor_state_checkpoints" in low and (
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

    monkeypatch.setattr(pin_svc, "set_editor_state_checkpoint_pin", _wrap_execute_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_execute_fail)
    res_ex = _patch_pin(client, pid, cid, True)
    _assert_fixed_error(res_ex, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before

    def _wrap_flush_fail(db, *a, **k):
        orig_flush = db.flush

        def _f(*fa, **fk):
            raise RuntimeError(_INJECT_FLUSH)

        db.flush = _f  # type: ignore[method-assign]
        try:
            return real_set(db, *a, **k)
        finally:
            db.flush = orig_flush  # type: ignore[method-assign]

    monkeypatch.setattr(pin_svc, "set_editor_state_checkpoint_pin", _wrap_flush_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_flush_fail)
    res = _patch_pin(client, pid, cid, True)
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

    monkeypatch.setattr(pin_svc, "set_editor_state_checkpoint_pin", _wrap_commit_fail)
    monkeypatch.setattr(api_mod, "set_pin_svc", _wrap_commit_fail)
    res2 = _patch_pin(client, pid, cid, True)
    _assert_fixed_error(res2, 500, _CODE_PIN_FAILED, message=_MSG_PIN_FAILED)
    assert _domain_snapshot(pid) == before


def test_sqlite_idempotent_add_is_pinned_column():
    """用途：三种旧库 + 最终结构 no-op + 索引/CHECK/存量归零。"""
    from app.core import database as dbmod
    from app.models.entities import Base

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def _table_sql(conn) -> str | None:
        row = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='editor_state_checkpoints'"
        ).fetchone()
        return row[0] if row else None

    def _cols(conn) -> set[str]:
        return {r[1] for r in conn.exec_driver_sql(
            "PRAGMA table_info(editor_state_checkpoints)"
        ).fetchall()}

    # 1) create_all 新库
    Base.metadata.create_all(bind=eng)
    with eng.begin() as conn:
        cols = _cols(conn)
        assert "is_pinned" in cols
        info = {
            r[1]: (str(r[2] or "").upper(), int(r[3] or 0), r[4])
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        pin_type, pin_notnull, pin_default = info["is_pinned"]
        assert "BOOLEAN" in pin_type
        assert pin_notnull == 1
        dflt = (
            str(pin_default).strip().lower().strip("'\"")
            if pin_default is not None
            else ""
        )
        assert dflt in ("0", "false"), pin_default
        ddl = _table_sql(conn) or ""
        assert "is_pinnedin(0,1)" in re.sub(r"\s+", "", ddl).lower()
    eng.dispose()

    # 2) 无列旧表
    eng2 = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng2.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE workspaces (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id VARCHAR(64)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                name VARCHAR(200) NOT NULL,
                kind VARCHAR(32) NOT NULL DEFAULT 'technical',
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws1', 'W', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('p1', 'ws1', 'P')"
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
                outline_node_count INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL,
                display_name VARCHAR(160),
                created_at DATETIME NOT NULL,
                CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
                CHECK (outline_node_count >= 0),
                CHECK (chapter_count >= 0)
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_checkpoints_workspace_id "
            "ON editor_state_checkpoints(workspace_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_checkpoints_project_id "
            "ON editor_state_checkpoints(project_id)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_editor_state_checkpoints_created_at "
            "ON editor_state_checkpoints(created_at)"
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_escp_workspace_project_created_id "
            "ON editor_state_checkpoints(workspace_id, project_id, created_at, id)"
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, created_at"
            ") VALUES ("
            "'escp_old1', 'ws1', 'p1', '{}', 'esv_' || hex(randomblob(16)), "
            "2, 0, 0, NULL, datetime('now')"
            ")"
        )

    ensure_schema_columns(eng2)
    with eng2.begin() as conn:
        cols = _cols(conn)
        assert "is_pinned" in cols
        ddl = (_table_sql(conn) or "").replace(" ", "").lower()
        assert "is_pinnedin(0,1)" in ddl
        val = conn.exec_driver_sql(
            "SELECT is_pinned FROM editor_state_checkpoints WHERE id='escp_old1'"
        ).fetchone()[0]
        assert val in (0, False)
        # 迁移前已建索引须完整保留
        idx = {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA index_list(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "ix_escp_workspace_project_created_id" in idx
        assert "ix_editor_state_checkpoints_workspace_id" in idx
        assert "ix_editor_state_checkpoints_project_id" in idx
        assert "ix_editor_state_checkpoints_created_at" in idx
    eng2.dispose()

    # 3) 已有列无 CHECK 的中间态：非法值归零
    eng3 = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng3.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE workspaces (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id VARCHAR(64)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                name VARCHAR(200) NOT NULL,
                kind VARCHAR(32) NOT NULL DEFAULT 'technical',
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws1', 'W', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('p1', 'ws1', 'P')"
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
                outline_node_count INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL,
                display_name VARCHAR(160),
                is_pinned BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, "
            "is_pinned, created_at"
            ") VALUES ("
            "'escp_bad', 'ws1', 'p1', '{}', 'esv_bad', 2, 0, 0, NULL, 2, datetime('now')"
            ")"
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, "
            "is_pinned, created_at"
            ") VALUES ("
            "'escp_ok', 'ws1', 'p1', '{}', 'esv_ok', 2, 0, 0, NULL, 1, datetime('now')"
            ")"
        )

    ensure_schema_columns(eng3)
    with eng3.begin() as conn:
        ddl = (_table_sql(conn) or "").replace(" ", "").lower()
        assert "is_pinnedin(0,1)" in ddl
        rows = {
            r[0]: r[1]
            for r in conn.exec_driver_sql(
                "SELECT id, is_pinned FROM editor_state_checkpoints"
            ).fetchall()
        }
        assert rows["escp_bad"] in (0, False)
        assert rows["escp_ok"] in (1, True)
    eng3.dispose()

    # 4) 最终结构 no-op：捕获 SQL，证明未执行临时表 CREATE/DROP/RENAME
    eng4 = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng4)
    with eng4.begin() as conn:
        before_n = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM editor_state_checkpoints"
        ).fetchone()[0]
        ddl_before = _table_sql(conn) or ""
        info_before = {
            r[1]: (str(r[2] or "").upper(), int(r[3] or 0), r[4])
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "BOOLEAN" in info_before["is_pinned"][0]
        assert info_before["is_pinned"][1] == 1

    captured_sql: list[str] = []

    def _capture_sql(conn, cursor, statement, parameters, context, executemany):
        captured_sql.append(statement if isinstance(statement, str) else str(statement))

    event.listen(eng4, "before_cursor_execute", _capture_sql)
    try:
        ensure_schema_columns(eng4)
        ensure_schema_columns(eng4)
    finally:
        event.remove(eng4, "before_cursor_execute", _capture_sql)

    mig_markers = (
        "editor_state_checkpoints__p12ja_mig",
        "EDITOR_STATE_CHECKPOINTS__P12JA_MIG",
    )
    for sql in captured_sql:
        low = re.sub(r"\s+", " ", sql).strip().lower()
        for marker in mig_markers:
            assert marker.lower() not in low, sql
        # 不得对检查点主表执行迁移路径的 DROP/RENAME
        if "editor_state_checkpoints" in low and (
            low.startswith("drop table") or "rename to" in low
        ):
            raise AssertionError(f"最终态 no-op 不应 DROP/RENAME: {sql}")

    with eng4.begin() as conn:
        after_n = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM editor_state_checkpoints"
        ).fetchone()[0]
        assert after_n == before_n
        ddl_after = _table_sql(conn) or ""
        assert ddl_after == ddl_before
        assert "is_pinnedin(0,1)" in re.sub(r"\s+", "", ddl_after).lower()
    eng4.dispose()


def _build_legacy_checkpoint_engine_without_is_pinned():
    """用途：构造无 is_pinned 的旧检查点表 + 索引 + 一行数据。"""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE workspaces (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id VARCHAR(64)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                name VARCHAR(200) NOT NULL,
                kind VARCHAR(32) NOT NULL DEFAULT 'technical',
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws1', 'W', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('p1', 'ws1', 'P')"
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
                outline_node_count INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL,
                display_name VARCHAR(160),
                created_at DATETIME NOT NULL,
                CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
                CHECK (outline_node_count >= 0),
                CHECK (chapter_count >= 0)
            )
            """
        )
        conn.exec_driver_sql(
            "CREATE INDEX ix_escp_workspace_project_created_id "
            "ON editor_state_checkpoints(workspace_id, project_id, created_at, id)"
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, created_at"
            ") VALUES ("
            "'escp_keep', 'ws1', 'p1', '{\"k\":1}', 'esv_keep', 7, 0, 0, '名', datetime('now')"
            ")"
        )
    return eng


def test_is_pinned_migration_midway_failure_rolls_back():
    """
    用途：调用真实生产 migrate；在 DROP 前、以及 DROP 后 RENAME 前分别注入异常，
    证明外层事务回滚：旧表/数据/索引完整且不残留临时表。
    """
    from app.core import database as dbmod

    assert hasattr(dbmod, "migrate_editor_state_checkpoints_is_pinned")
    _TMP = "editor_state_checkpoints__p12ja_mig"
    _INJECT_DROP = "p12ja_inject_before_drop"
    _INJECT_RENAME = "p12ja_inject_before_rename"

    def _snapshot(conn):
        ddl = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='editor_state_checkpoints'"
        ).fetchone()[0]
        cols = {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        rows = conn.exec_driver_sql(
            "SELECT id, snapshot_json, display_name FROM editor_state_checkpoints "
            "ORDER BY id"
        ).fetchall()
        idx = {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA index_list(editor_state_checkpoints)"
            ).fetchall()
        }
        tables = {
            r[0]
            for r in conn.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        return ddl, cols, rows, idx, tables

    def _run_with_inject(phase: str, inject_msg: str):
        eng = _build_legacy_checkpoint_engine_without_is_pinned()
        with eng.begin() as conn:
            pre = _snapshot(conn)
            assert "is_pinned" not in pre[1]
            assert "ix_escp_workspace_project_created_id" in pre[3]

        seen_tmp = {"created": False}
        dropped_main = {"done": False}

        def _before(conn, cursor, statement, parameters, context, executemany):
            sql = statement if isinstance(statement, str) else str(statement)
            compact = re.sub(r"\s+", " ", sql).strip()
            up = compact.upper()
            if (
                up.startswith("CREATE TABLE")
                and "EDITOR_STATE_CHECKPOINTS__P12JA_MIG" in up
            ):
                seen_tmp["created"] = True
            if phase == "before_drop":
                if (
                    seen_tmp["created"]
                    and up.startswith("DROP TABLE")
                    and "EDITOR_STATE_CHECKPOINTS" in up
                    and "__P12JA_MIG" not in up
                ):
                    raise RuntimeError(inject_msg)
            elif phase == "after_drop_before_rename":
                if (
                    seen_tmp["created"]
                    and up.startswith("DROP TABLE")
                    and "EDITOR_STATE_CHECKPOINTS" in up
                    and "__P12JA_MIG" not in up
                ):
                    dropped_main["done"] = True
                if (
                    dropped_main["done"]
                    and "RENAME TO" in up
                    and "EDITOR_STATE_CHECKPOINTS" in up
                ):
                    raise RuntimeError(inject_msg)

        event.listen(eng, "before_cursor_execute", _before)
        try:
            with pytest.raises(RuntimeError, match=re.escape(inject_msg)):
                # 直接调用真实生产迁移函数，置于事务内以验证回滚
                with eng.begin() as conn:
                    dbmod.migrate_editor_state_checkpoints_is_pinned(conn)
        finally:
            event.remove(eng, "before_cursor_execute", _before)

        with eng.begin() as conn:
            post = _snapshot(conn)
            assert post[0] == pre[0]
            assert "is_pinned" not in post[1]
            assert post[2] == pre[2]
            assert "ix_escp_workspace_project_created_id" in post[3]
            assert _TMP not in post[4]
            assert "editor_state_checkpoints" in post[4]
        eng.dispose()

    _run_with_inject("before_drop", _INJECT_DROP)
    _run_with_inject("after_drop_before_rename", _INJECT_RENAME)

    # 移除注入后真实 migrate 可完成
    eng_ok = _build_legacy_checkpoint_engine_without_is_pinned()
    with eng_ok.begin() as conn:
        dbmod.migrate_editor_state_checkpoints_is_pinned(conn)
    with eng_ok.begin() as conn:
        cols = {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "is_pinned" in cols
        info = {
            r[1]: (str(r[2] or "").upper(), int(r[3] or 0), r[4])
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "BOOLEAN" in info["is_pinned"][0]
        assert info["is_pinned"][1] == 1
        val = conn.exec_driver_sql(
            "SELECT is_pinned FROM editor_state_checkpoints WHERE id='escp_keep'"
        ).fetchone()[0]
        assert val in (0, False)
        tmp_n = conn.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master "
            f"WHERE type='table' AND name='{_TMP}'"
        ).fetchone()[0]
        assert tmp_n == 0
        idx = {
            r[1]
            for r in conn.exec_driver_sql(
                "PRAGMA index_list(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "ix_escp_workspace_project_created_id" in idx
    eng_ok.dispose()


def test_production_source_guards_and_pin_service_shape():
    """用途：白名单源码形态守卫——原始投影、三谓词、无正文投影、无日志。"""
    api_src = _API_PATH.read_text(encoding="utf-8")
    assert "/pin" in api_src
    assert "isPinned" in api_src
    assert "is_pinned" in api_src
    assert "1024" in api_src

    ent_src = _ENTITIES_PATH.read_text(encoding="utf-8")
    tree = ast.parse(ent_src)
    class_node = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EditorStateCheckpointRow":
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
        "outline_node_count",
        "chapter_count",
        "display_name",
        "is_pinned",
        "created_at",
    ]
    assert "ck_editor_state_checkpoints_is_pinned" in ent_src

    svc_src = _CHECKPOINT_SERVICE_PATH.read_text(encoding="utf-8")
    assert "is_pinned" in svc_src
    assert "type_coerce" in svc_src
    assert "snapshot_json" in svc_src
    assert "MAX_PINNED_CHECKPOINTS_PER_PROJECT" in svc_src
    assert "protect_id is not None" in svc_src
    # P12J-B：list/detail/search 三处原始 Integer 投影；禁止 .get 默认 false
    coerce_label = 'type_coerce(EditorStateCheckpointRow.is_pinned, Integer).label('
    assert svc_src.count(coerce_label) >= 4  # trim + list + detail + search
    assert '.get("is_pinned"' not in svc_src
    assert ".get('is_pinned'" not in svc_src
    api_src2 = _API_PATH.read_text(encoding="utf-8")
    assert 'data["is_pinned"]' in api_src2
    assert '.get("is_pinned"' not in api_src2

    db_src = _DATABASE_PATH.read_text(encoding="utf-8")
    assert "migrate_editor_state_checkpoints_is_pinned" in db_src
    assert "_editor_state_checkpoints_is_pinned_final" in db_src
    assert "_sqlite_normalize_ddl" in db_src

    assert _PIN_SERVICE_PATH.is_file()
    pin_src = _PIN_SERVICE_PATH.read_text(encoding="utf-8")
    body = pin_src
    if '"""' in pin_src:
        first = pin_src.find('"""')
        second = pin_src.find('"""', first + 3)
        if second != -1:
            body = pin_src[second + 3 :]
    assert "except:" not in body
    assert "logger" not in body
    assert "print(" not in body
    assert "str(exc)" not in body
    assert "type_coerce" in body
    assert "workspace_id" in body
    assert "project_id" in body
    assert "is_pinned" in body
    assert "snapshot_json" not in body
    assert "set_editor_state_checkpoint_pin" in body


def _seed_checkpoint_ids(
    project_id: str,
    tags: list[str],
    *,
    base_time: datetime,
) -> list[str]:
    """用途：插入检查点并返回按插入顺序的 id（旧→新），避免全表重读混淆。"""
    ids: list[str] = []
    for i, tag in enumerate(tags):
        state = _variant(tag)
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        cid = "escp_" + secrets.token_hex(16)
        chapters = snap.get("chapters") or []
        chapter_n = len(chapters) if isinstance(chapters, list) else 0
        _insert_raw_checkpoint(
            project_id=project_id,
            checkpoint_id=cid,
            snapshot_json=snap_json,
            state_version=state["stateVersion"],
            snapshot_bytes=len(snap_json.encode("utf-8")),
            created_at=base_time + timedelta(seconds=i),
            chapter_count=chapter_n,
        )
        ids.append(cid)
    return ids


def test_create_trim_real_20_keeps_5_pinned_new_and_14_latest(disabled_client):
    """
    用途：真实 20 行（5 固定 + 15 普通）后手动创建：
      保留 5 固定 + 新建 + 14 最新普通；淘汰最旧普通。
    """
    client = disabled_client
    pid = _create_project(client, name="真实20创建裁剪")
    _ensure_editor_state(pid, "cur20c")
    base = datetime(2026, 7, 3, 8, 0, 0, tzinfo=timezone.utc)
    # 15 普通：时间递增；再 5 固定更早
    normal_ids = _seed_checkpoint_ids(
        pid,
        [f"n{i:02d}" for i in range(15)],
        base_time=base + timedelta(hours=1),
    )
    pinned_ids_list = _seed_checkpoint_ids(
        pid,
        [f"p{i}" for i in range(5)],
        base_time=base - timedelta(hours=2),
    )
    for cid in pinned_ids_list:
        _assert_success_pin(_patch_pin(client, pid, cid, True), True)
    assert len(_db_cp_rows(pid)) == 20
    oldest_normal = normal_ids[0]
    newer_normals = set(normal_ids[1:])  # 14 条较新普通
    pinned_ids = set(pinned_ids_list)

    res = client.post(_create_url(pid), json={})
    assert res.status_code == 201, res.text
    new_id = res.json()["checkpointId"]
    kept = _db_cp_rows(pid)
    kept_ids = {r["id"] for r in kept}
    assert len(kept) == 20
    assert pinned_ids.issubset(kept_ids)
    assert new_id in kept_ids
    assert newer_normals.issubset(kept_ids)
    assert oldest_normal not in kept_ids
    assert sum(1 for r in kept if r["is_pinned"] is True) == 5
    # 固定 + 新建 + 14 普通
    assert len(kept_ids - pinned_ids - {new_id}) == 14


def test_restore_trim_real_20_keeps_5_pinned_safety_and_14(disabled_client):
    """
    用途：真实 20 行中 5 固定后恢复：
      保留 5 固定 + 本轮 safety + 14 普通。
    """
    client = disabled_client
    pid = _create_project(client, name="真实20恢复裁剪")
    current = _ensure_editor_state(pid, "cur20r")
    base = datetime(2026, 7, 4, 9, 0, 0, tzinfo=timezone.utc)

    # 恢复目标：单独插入，稍后计入 20
    target_state = _variant("tgt20")
    target_snap = editor_state_service.extract_canonical_snapshot(target_state)
    target_json = editor_state_service.canonical_snapshot_json(target_snap)
    target_id = "escp_" + secrets.token_hex(16)
    _insert_raw_checkpoint(
        project_id=pid,
        checkpoint_id=target_id,
        snapshot_json=target_json,
        state_version=target_state["stateVersion"],
        snapshot_bytes=len(target_json.encode("utf-8")),
        created_at=base + timedelta(hours=3),
        chapter_count=1,
    )
    # 14 普通 + 5 固定 = 19，加上 target 共 20
    normal_ids = _seed_checkpoint_ids(
        pid,
        [f"rn{i:02d}" for i in range(14)],
        base_time=base + timedelta(hours=1),
    )
    pinned_ids_list = _seed_checkpoint_ids(
        pid,
        [f"rp{i}" for i in range(5)],
        base_time=base - timedelta(hours=1),
    )
    for cid in pinned_ids_list:
        _assert_success_pin(_patch_pin(client, pid, cid, True), True)
    all_rows = _db_cp_rows(pid)
    assert len(all_rows) == 20
    pinned_ids = set(pinned_ids_list)

    res = client.post(
        _restore_url(pid, target_id),
        json={"expectedStateVersion": current["stateVersion"]},
    )
    assert res.status_code == 200, res.text
    safety_id = res.json()["safetyCheckpointId"]
    kept = _db_cp_rows(pid)
    kept_ids = {r["id"] for r in kept}
    assert len(kept) == 20
    assert pinned_ids.issubset(kept_ids)
    assert safety_id in kept_ids
    assert sum(1 for r in kept if r["is_pinned"] is True) == 5
    # 5 固定 + safety + 14 其它（普通/目标）= 20
    others = kept_ids - pinned_ids - {safety_id}
    assert len(others) == 14
    # 最旧普通应被淘汰
    assert normal_ids[0] not in kept_ids
    assert target_id in kept_ids


def test_migration_incomplete_nullable_is_pinned_rebuilds_and_zeros_null():
    """
    用途：已有 is_pinned + 等价 0/1 CHECK、但列可空/无 DEFAULT 的中间态
    不得错误 no-op；迁移后 BOOLEAN NOT NULL DEFAULT 0，NULL 存量归零，CHECK 保留。
    """
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE workspaces (
                id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(200) NOT NULL,
                owner_user_id VARCHAR(64)
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE projects (
                id VARCHAR(64) PRIMARY KEY,
                workspace_id VARCHAR(64) NOT NULL
                    REFERENCES workspaces(id) ON DELETE CASCADE,
                name VARCHAR(200) NOT NULL,
                kind VARCHAR(32) NOT NULL DEFAULT 'technical',
                status VARCHAR(32) NOT NULL DEFAULT 'draft',
                updated_at DATETIME
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO workspaces(id, name, owner_user_id) "
            "VALUES ('ws1', 'W', 'u1')"
        )
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('p1', 'ws1', 'P')"
        )
        # 中间态：有 is_pinned + 0/1 CHECK，但可空且无 DEFAULT
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
                outline_node_count INTEGER NOT NULL,
                chapter_count INTEGER NOT NULL,
                display_name VARCHAR(160),
                is_pinned BOOLEAN,
                created_at DATETIME NOT NULL,
                CHECK (snapshot_bytes >= 1 AND snapshot_bytes <= 2097152),
                CHECK (outline_node_count >= 0),
                CHECK (chapter_count >= 0),
                CHECK (is_pinned IN (0, 1))
            )
            """
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, "
            "is_pinned, created_at"
            ") VALUES ("
            "'escp_null', 'ws1', 'p1', '{}', 'esv_null', 2, 0, 0, NULL, NULL, "
            "datetime('now')"
            ")"
        )
        conn.exec_driver_sql(
            "INSERT INTO editor_state_checkpoints("
            "id, workspace_id, project_id, snapshot_json, state_version, "
            "snapshot_bytes, outline_node_count, chapter_count, display_name, "
            "is_pinned, created_at"
            ") VALUES ("
            "'escp_one', 'ws1', 'p1', '{}', 'esv_one', 2, 0, 0, NULL, 1, "
            "datetime('now')"
            ")"
        )
        pre = {
            r[1]: (str(r[2] or "").upper(), int(r[3] or 0), r[4])
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        assert "is_pinned" in pre
        assert pre["is_pinned"][1] == 0  # notnull=0 中间态
        assert pre["is_pinned"][2] is None  # 无 DEFAULT
        ddl_pre = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='editor_state_checkpoints'"
        ).fetchone()[0]
        assert "is_pinnedin(0,1)" in ddl_pre.replace(" ", "").lower()

    ensure_schema_columns(eng)
    with eng.begin() as conn:
        info = {
            r[1]: (str(r[2] or "").upper(), int(r[3] or 0), r[4])
            for r in conn.exec_driver_sql(
                "PRAGMA table_info(editor_state_checkpoints)"
            ).fetchall()
        }
        pin_type, pin_notnull, pin_default = info["is_pinned"]
        assert "BOOLEAN" in pin_type, pin_type
        assert pin_notnull == 1, (pin_notnull, pin_default)
        dflt = str(pin_default).strip().lower().strip("'\"") if pin_default is not None else ""
        assert dflt in ("0", "false"), pin_default
        ddl = (
            conn.exec_driver_sql(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_checkpoints'"
            ).fetchone()[0]
            or ""
        )
        normalized = re.sub(r"\s+", "", ddl).lower()
        assert "is_pinnedin(0,1)" in normalized, ddl
        rows = {
            r[0]: r[1]
            for r in conn.exec_driver_sql(
                "SELECT id, is_pinned FROM editor_state_checkpoints"
            ).fetchall()
        }
        assert rows["escp_null"] in (0, False), rows
        assert rows["escp_one"] in (1, True), rows
    eng.dispose()


def test_trim_empty_with_protect_id_is_corrupt_zero_write(disabled_client):
    """
    用途：项目无任何检查点时，带 protect_id 的裁剪必须 corrupt 且零写；
    不得静默 return。无 protect_id 时允许空集 return。
    """
    client = disabled_client
    pid = _create_project(client, name="空集保护裁剪")
    assert _db_cp_rows(pid) == []
    before = _domain_snapshot(pid)
    protect_id = "escp_" + secrets.token_hex(16)

    db = SessionLocal()
    try:
        with pytest.raises(
            editor_state_checkpoint_service.EditorStateCheckpointError
        ) as ei:
            editor_state_checkpoint_service._trim_checkpoints(
                db, _WS, pid, protect_id=protect_id
            )
        err = ei.value
        assert err.status_code == 500
        assert err.code == _CODE_CORRUPT
        db.rollback()
    finally:
        db.close()
    assert _domain_snapshot(pid) == before

    db2 = SessionLocal()
    try:
        editor_state_checkpoint_service._trim_checkpoints(db2, _WS, pid)
        db2.rollback()
    finally:
        db2.close()
    assert _domain_snapshot(pid) == before
