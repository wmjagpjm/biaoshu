"""
模块：P12C-C2 editor-state 修订受限恢复专项测试
用途：真实 HTTP+SQLite 验收 revision_restore 准确来源、旧库迁移、
  CAS、安全检查点、13 键写回、双配额、三域回滚、双并发与反假绿。
对接：POST .../editor-state-revisions/{id}/restore；
  editor_state_revision_restore_service；stage_locked_canonical_restore。
二次开发：禁止 mock SQLite、>= 宽松增量、空集合、固定 sleep、
  顺序冒充并发、跨项目冒充跨空间、用 checkpoint_restore 冒充。
"""

from __future__ import annotations

import ast
import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import get_settings
from app.core.database import SessionLocal, engine, ensure_schema_columns
from app.main import app
from app.models.entities import (
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
    utc_now,
)
from app.services import (
    auth_service,
    editor_state_checkpoint_service,
    editor_state_revision_service,
    editor_state_service,
)

_WS = "ws_local"
_WS_OTHER = "ws_other_p12cc2"
_OWNER_USER = "admin_p12cc2_owner"
_OWNER_PASS = "TestPass-P12CC2-Owner-0001!"
_ROLE_PASSWORDS = {
    "bid_writer": "TestPass-P12CC2-Writer-0001!",
    "finance": "TestPass-P12CC2-Finance-0001!",
    "hr": "TestPass-P12CC2-Hr-0001!",
    "bidder": "TestPass-P12CC2-Bidder-0001!",
}
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_REVISION_ID_RE = re.compile(r"^esr_[0-9a-f]{32}$")
_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_SOURCE_RESTORE = "revision_restore"
_SOURCE_CP_RESTORE = "checkpoint_restore"
_SOURCE_BROWSER = "browser_put"
_SECRET = "SECRET_P12CC2_BODY_MUST_NOT_LEAK"
_INJECT_AFTER_FLUSH = "p12cc2_injected_after_flush"
_INJECT_REV_TRIM = "p12cc2_injected_revision_trim"
_INJECT_CP_TRIM = "p12cc2_injected_checkpoint_trim"
_INJECT_COMMIT_FAIL = "p12cc2_injected_commit_failure"
_RESTORE_KEYS = frozenset({"safetyCheckpointId", "stateVersion", "restoredAt"})
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_CODE_RESTORE_FAILED = "editor_state_revision_restore_failed"
_CODE_CORRUPT = "editor_state_revision_corrupt"
_CODE_NOT_FOUND = "editor_state_revision_not_found"
_CODE_PROJECT = "project_not_found"
_CODE_CONFLICT = "editor_state_version_conflict"
_CODE_TOO_LARGE = "editor_state_checkpoint_too_large"

_SANITIZE_FORBIDDEN = (
    "editor_state_revisions",
    "editor_state_checkpoints",
    "editor_state_revision_restore_service",
    "editor_state_checkpoint_service",
    "editor_state_revision_service",
    "backend/app/services",
    "backend\\app\\services",
    str(Path(__file__).resolve().parents[1] / "app" / "services"),
)

_CP_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_service.py"
)
_RESTORE_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_restore_service.py"
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
_DB_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"
_REV_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_revision_service.py"
)

# ---------- fixtures ----------


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


# ---------- helpers ----------


def _assert_state_version(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _rev_url(project_id: str, revision_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-revisions"
    if revision_id is None:
        return base
    return f"{base}/{revision_id}"


def _restore_url(project_id: str, revision_id: str) -> str:
    return f"{_rev_url(project_id, revision_id)}/restore"


def _cp_url(project_id: str, checkpoint_id: str | None = None) -> str:
    base = f"/api/projects/{project_id}/editor-state-checkpoints"
    if checkpoint_id is None:
        return base
    return f"{base}/{checkpoint_id}"


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
    username = f"user_{role}_p12cc2{'_own' if is_owner else ''}"
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


def _create_project(
    client: TestClient,
    name: str = "P12C-C2",
    kind: str = "technical",
    *,
    headers: dict | None = None,
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "kind": kind},
        headers=headers or {},
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _get_state(client: TestClient, pid: str, *, headers: dict | None = None) -> dict:
    res = client.get(f"/api/projects/{pid}/editor-state", headers=headers or {})
    assert res.status_code == 200, res.text
    return res.json()


def _put_state(
    client: TestClient, pid: str, body: dict, *, headers: dict | None = None
) -> dict:
    res = client.put(
        f"/api/projects/{pid}/editor-state",
        json=body,
        headers=headers or {},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _default_chapters(n: int = 2) -> list[dict]:
    titles = ["总体架构", "安全设计", "实施计划", "质量保证", "运维保障"]
    bodies = [
        "现有架构正文。",
        "现有安全正文。",
        "现有实施正文。",
        "现有质量正文。",
        "现有运维正文。",
    ]
    out: list[dict] = []
    for i in range(n):
        out.append(
            {
                "id": f"chap_{chr(ord('a') + i)}",
                "title": titles[i],
                "body": bodies[i],
                "status": "pending",
                "preview": bodies[i],
                "wordCount": len(bodies[i].replace(" ", "")),
            }
        )
    return out


def _seed_via_browser(
    client: TestClient,
    pid: str,
    *,
    marker: str = "base",
    chapters: list[dict] | None = None,
    headers: dict | None = None,
) -> dict:
    chs = chapters or _default_chapters(2)
    return _put_state(
        client,
        pid,
        {
            "outline": [
                {"id": f"node_{c['id']}", "title": c["title"], "children": []}
                for c in chs
            ],
            "chapters": chs,
            "facts": [{"id": f"fact_{marker}", "text": f"{marker}-{_SECRET}"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": f"概述-{marker}",
                "techRequirements": [f"要求-{marker}"],
                "rejectionRisks": [],
                "scoringPoints": [f"评分-{marker}"],
            },
            "responseMatrix": [],
            "guidance": {"hints": [f"提示-{marker}"]},
            "parsedMarkdown": f"# 招标文件\n{marker}",
            "analysisOverview": f"概述-{marker}",
        },
        headers=headers,
    )


def _seed_business_via_browser(
    client: TestClient, pid: str, *, marker: str, headers: dict | None = None
) -> dict:
    return _put_state(
        client,
        pid,
        {
            "mode": "ALIGNED",
            "businessQualify": [{"name": f"资质-{marker}"}],
            "businessToc": [{"title": f"目录-{marker}"}],
            "businessQuote": {"rows": [{"item": f"报价-{marker}", "amount": 100}]},
            "businessCommit": [{"text": f"承诺-{marker}"}],
            "analysisOverview": f"商务概述-{marker}",
        },
        headers=headers,
    )


def _extract_13(state: dict) -> dict:
    return {k: state.get(k) for k in sorted(_SNAPSHOT_KEYS)}


def _db_rev_rows(
    project_id: str, workspace_id: str | None = None
) -> list[EditorStateRevisionRow]:
    db = SessionLocal()
    try:
        q = db.query(EditorStateRevisionRow).filter(
            EditorStateRevisionRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateRevisionRow.workspace_id == workspace_id)
        return list(
            q.order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            ).all()
        )
    finally:
        db.close()


def _db_rev_count(project_id: str, workspace_id: str | None = None) -> int:
    return len(_db_rev_rows(project_id, workspace_id=workspace_id))


def _source_count(rows: list[EditorStateRevisionRow], source_kind: str) -> int:
    return sum(1 for r in rows if r.source_kind == source_kind)


def _restore_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_RESTORE)


def _cp_restore_count(rows: list[EditorStateRevisionRow]) -> int:
    return _source_count(rows, _SOURCE_CP_RESTORE)


def _revision_identity_seq(
    rows: list[EditorStateRevisionRow],
) -> list[tuple[str, str, str]]:
    return [(r.id, r.state_version, r.source_kind) for r in rows]


def _db_cp_count(project_id: str, workspace_id: str | None = None) -> int:
    db = SessionLocal()
    try:
        q = db.query(EditorStateCheckpointRow).filter(
            EditorStateCheckpointRow.project_id == project_id
        )
        if workspace_id is not None:
            q = q.filter(EditorStateCheckpointRow.workspace_id == workspace_id)
        return q.count()
    finally:
        db.close()


def _db_cp_ids(project_id: str) -> list[str]:
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
        return [r.id for r in rows]
    finally:
        db.close()


def _db_get_cp(checkpoint_id: str) -> EditorStateCheckpointRow | None:
    db = SessionLocal()
    try:
        row = db.get(EditorStateCheckpointRow, checkpoint_id)
        if row is None:
            return None
        db.expunge(row)
        return row
    finally:
        db.close()


def _db_chapter_bodies(project_id: str) -> dict[str, str]:
    db = SessionLocal()
    try:
        row = db.get(ProjectEditorStateRow, project_id)
        if row is None or not row.chapters_json:
            return {}
        raw = json.loads(row.chapters_json)
        if not isinstance(raw, list):
            return {}
        out: dict[str, str] = {}
        for item in raw:
            if isinstance(item, dict) and type(item.get("id")) is str:
                body = item.get("body")
                out[item["id"]] = body if type(body) is str else ""
        return out
    finally:
        db.close()


def _find_revision_id_for_version(project_id: str, state_version: str) -> str:
    for r in _db_rev_rows(project_id):
        if r.state_version == state_version:
            return r.id
    raise AssertionError(f"未找到版本 {state_version} 的 revision")


def _restore(
    client: TestClient,
    pid: str,
    rid: str,
    expected: str,
    *,
    headers: dict | None = None,
):
    return client.post(
        _restore_url(pid, rid),
        json={"expectedStateVersion": expected},
        headers=headers or {},
    )


def _assert_success_restore(res, *, target_version: str) -> dict:
    assert res.status_code == 200, res.text
    assert res.headers.get("Cache-Control") == "no-store"
    body = res.json()
    assert set(body.keys()) == _RESTORE_KEYS, body.keys()
    after_ver = _assert_state_version(body["stateVersion"])
    assert after_ver == target_version
    assert _CHECKPOINT_ID_RE.fullmatch(body["safetyCheckpointId"])
    assert type(body["restoredAt"]) is str and body["restoredAt"]
    raw = res.text
    assert "revisionId" not in raw
    assert "restoredCheckpointId" not in raw
    assert "sourceKind" not in raw
    assert "revision_restore" not in raw
    assert "checkpoint_restore" not in raw
    assert "esr_" not in raw
    assert "source_kind" not in raw
    assert "snapshot" not in raw.lower() or "safetyCheckpointId" in raw
    return body | {"_after_ver": after_ver}


def _assert_fixed_error(res, status: int, code: str, *extra_leaks: str) -> None:
    assert res.status_code == status, res.text
    assert res.headers.get("Cache-Control") == "no-store", res.headers
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert detail.get("code") == code
    assert type(detail.get("message")) is str and detail["message"] != ""
    if status == 409:
        assert set(detail.keys()) == {"code", "message", "currentStateVersion"}
    else:
        assert set(detail.keys()) == {"code", "message"}
    blob = res.text
    assert _SECRET not in blob
    assert "Traceback" not in blob
    assert "sqlite" not in blob.lower()
    for m in extra_leaks:
        if m:
            assert m not in blob


def _assert_sanitized_500(blob: str, *extra: str) -> None:
    low = blob.lower()
    assert _SECRET not in blob
    assert "traceback" not in low
    assert "sqlite" not in low
    assert "select " not in low
    assert "insert into" not in low
    # 允许固定错误码 editor_state_revision_restore_failed 含子串；禁止独立来源字面量泄漏
    blob_wo_fixed_code = blob.replace("editor_state_revision_restore_failed", "")
    assert "revision_restore" not in blob_wo_fixed_code
    assert "source_kind" not in low
    assert "sourcekind" not in low
    for forbidden in _SANITIZE_FORBIDDEN:
        assert forbidden.lower() not in low, f"500 泄漏: {forbidden!r}"
    for m in extra:
        if m:
            assert m not in blob


def _assert_restore_after(
    rows: list[EditorStateRevisionRow], after_ver: str
) -> EditorStateRevisionRow:
    matched = [
        r
        for r in rows
        if r.state_version == after_ver and r.source_kind == _SOURCE_RESTORE
    ]
    assert len(matched) == 1, [(r.state_version, r.source_kind) for r in rows]
    row = matched[0]
    assert _REVISION_ID_RE.fullmatch(row.id)
    return row


def _prepare_diff_restore(
    client: TestClient,
    *,
    name: str = "差异恢复",
    kind: str = "technical",
    headers: dict | None = None,
) -> tuple[str, str, str, str, dict, dict]:
    """
    用途：构造目标修订 A 与当前 B（版本不同）。
    返回 (pid, rid_a, target_ver, current_ver, target_state, current_state)。
    """
    pid = _create_project(client, name=name, kind=kind, headers=headers)
    if kind == "business":
        target = _seed_business_via_browser(
            client, pid, marker="A", headers=headers
        )
    else:
        target = _seed_via_browser(client, pid, marker="A", headers=headers)
    target_ver = _assert_state_version(target["stateVersion"])
    rid_a = _find_revision_id_for_version(pid, target_ver)
    if kind == "business":
        current = _seed_business_via_browser(
            client, pid, marker="B", headers=headers
        )
    else:
        current = _seed_via_browser(client, pid, marker="B", headers=headers)
    current_ver = _assert_state_version(current["stateVersion"])
    assert current_ver != target_ver
    return pid, rid_a, target_ver, current_ver, target, current


def _ensure_workspace(ws_id: str, name: str = "其他空间P12CC2") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12cc2",
                )
            )
            db.commit()
    finally:
        db.close()


def _seed_foreign_workspace_project_with_revision(
    *,
    project_id: str = "proj_other_p12cc2",
    revision_id: str = "esr_other_p12cc2_fixedid000000000001",
) -> tuple[str, str, str, dict]:
    _ensure_workspace(_WS_OTHER)
    db = SessionLocal()
    try:
        if db.get(Project, project_id) is None:
            db.add(
                Project(
                    id=project_id,
                    workspace_id=_WS_OTHER,
                    name="外空间技术标-c2",
                    industry="通用",
                    status="draft",
                    kind="technical",
                )
            )
            db.commit()
        state = editor_state_service.upsert_editor_state(
            db,
            _WS_OTHER,
            project_id,
            outline=[{"id": "node_a", "title": "外空间章", "children": []}],
            chapters=[
                {
                    "id": "chap_a",
                    "title": "外空间章",
                    "body": f"外空间正文-{_SECRET}",
                    "status": "pending",
                    "preview": f"外空间正文-{_SECRET}",
                    "wordCount": 8,
                }
            ],
            mode="ALIGNED",
            revision_source_kind=_SOURCE_BROWSER,
        )
        snap = editor_state_service.extract_canonical_snapshot(state)
        snap_json = editor_state_service.canonical_snapshot_json(snap)
        ver = state["stateVersion"]
        existing = db.get(EditorStateRevisionRow, revision_id)
        if existing is None:
            # 若 upsert 已记 revision，优先用真实行；否则插固定 ID
            rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == project_id,
                    EditorStateRevisionRow.workspace_id == _WS_OTHER,
                )
                .all()
            )
            if rows:
                revision_id = rows[0].id
            else:
                db.add(
                    EditorStateRevisionRow(
                        id=revision_id,
                        workspace_id=_WS_OTHER,
                        project_id=project_id,
                        snapshot_json=snap_json,
                        state_version=ver,
                        snapshot_bytes=len(snap_json.encode("utf-8")),
                        source_kind=_SOURCE_BROWSER,
                    )
                )
                db.commit()
    finally:
        db.close()
    return project_id, revision_id, _assert_state_version(ver), state


def _find_function_def(path: Path, name: str) -> ast.FunctionDef | None:
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _call_func_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _source_kind_literal_on_call(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "source_kind":
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
            if isinstance(kw.value, ast.Name):
                return f"${kw.value.id}"
    return None


# ---------- AST / 白名单补充 ----------


def test_ast_shared_primitive_and_restore_source_literal():
    """用途：AST 补充共享原语与 revision_restore 字面量；不能替代 HTTP。"""
    stage_fn = _find_function_def(_CP_SERVICE_PATH, "stage_locked_canonical_restore")
    assert stage_fn is not None, "缺少 stage_locked_canonical_restore 共享原语"

    # 共享原语禁止 commit/rollback/锁/目标查询
    forbidden_calls = {
        "commit",
        "rollback",
        "lock_and_assert_expected_state_version",
        "get_editor_state",
        "get_editor_state_revision",
        "get_editor_state_checkpoint",
    }
    for n in ast.walk(stage_fn):
        if isinstance(n, ast.Call):
            name = _call_func_name(n)
            assert name not in forbidden_calls, f"共享原语禁止调用 {name}"

    restore_fn = _find_function_def(
        _RESTORE_SERVICE_PATH, "restore_editor_state_revision"
    )
    assert restore_fn is not None, "缺少 restore_editor_state_revision"

    records = [
        n
        for n in ast.walk(restore_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "record_editor_state_transition"
    ]
    # 编排层应走共享原语，不直接 record；若直接 record 必须字面量
    if records:
        for call in records:
            lit = _source_kind_literal_on_call(call)
            assert lit == _SOURCE_RESTORE

    stage_calls = [
        n
        for n in ast.walk(restore_fn)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "stage_locked_canonical_restore"
    ]
    assert len(stage_calls) == 1, "restore 应唯一调用共享原语"

    # checkpoint restore 也必须复用共享原语
    cp_restore = _find_function_def(
        _CP_SERVICE_PATH, "restore_editor_state_checkpoint"
    )
    assert cp_restore is not None
    cp_stage = [
        n
        for n in ast.walk(cp_restore)
        if isinstance(n, ast.Call)
        and _call_func_name(n) == "stage_locked_canonical_restore"
    ]
    assert len(cp_stage) == 1

    # 模型与服务枚举含第九来源
    entities_src = _ENTITIES_PATH.read_text(encoding="utf-8")
    assert "revision_restore" in entities_src
    rev_src = _REV_SERVICE_PATH.read_text(encoding="utf-8")
    assert "revision_restore" in rev_src
    assert "revision_restore" in editor_state_revision_service.REVISION_SOURCE_KINDS


# ---------- 1 body / 权限 ----------


def test_body_strict_camelcase_and_extra_keys_422(client: TestClient):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="body严格"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    state0 = _get_state(client, pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))

    cases = [
        {},
        {"expected_state_version": current_ver},
        {"expectedStateVersion": current_ver, "force": True},
        {"expectedStateVersion": current_ver, "source": "browser_put"},
        {"expectedStateVersion": current_ver, "snapshot": {}},
        {"expectedStateVersion": current_ver, "checkpointId": "escp_x"},
        {"expectedStateVersion": " "},
        {"expectedStateVersion": "ESV_" + "a" * 32},
        {"expectedStateVersion": "not_a_version"},
        {"expectedStateVersion": "esv_" + "A" * 32},
    ]
    for body in cases:
        res = client.post(_restore_url(pid, rid), json=body)
        assert res.status_code == 422, (body, res.text)
        assert _db_rev_count(pid) == n0
        assert _db_cp_count(pid) == cp0
        assert _get_state(client, pid) == state0
        assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
        assert _restore_count(_db_rev_rows(pid)) == 0


def test_auth_required_bid_writer_only(required_client):
    """用途：required 模式仅 bid_writer 可写；finance/hr/bidder/仅 owner 拒绝。"""
    client = required_client
    for role in ("finance", "hr", "bidder"):
        csrf = _login_role(client, role)
        res = client.post(
            _restore_url("proj_x", "esr_" + "0" * 32),
            json={"expectedStateVersion": "esv_" + "a" * 32},
            headers={"X-CSRF-Token": csrf},
        )
        assert res.status_code in (401, 403), (role, res.status_code, res.text)

    csrf_owner_finance = _login_role(client, "finance", is_owner=True)
    res_own = client.post(
        _restore_url("proj_x", "esr_" + "0" * 32),
        json={"expectedStateVersion": "esv_" + "a" * 32},
        headers={"X-CSRF-Token": csrf_owner_finance},
    )
    assert res_own.status_code in (401, 403), res_own.text

    csrf_w = _login_role(client, "bid_writer")
    headers = {"X-CSRF-Token": csrf_w}
    pid = _create_project(client, name="required修订恢复", headers=headers)
    target = _seed_via_browser(client, pid, marker="A", headers=headers)
    target_ver = target["stateVersion"]
    rid = _find_revision_id_for_version(pid, target_ver)
    current = _seed_via_browser(client, pid, marker="B", headers=headers)
    # 无 CSRF
    no_csrf = client.post(
        _restore_url(pid, rid),
        json={"expectedStateVersion": current["stateVersion"]},
    )
    assert no_csrf.status_code in (401, 403), no_csrf.text
    # 有 CSRF 成功
    ok = _restore(client, pid, rid, current["stateVersion"], headers=headers)
    _assert_success_restore(ok, target_version=target_ver)


def test_disabled_personal_mode_restore_ok(disabled_client):
    client = disabled_client
    pid, rid, target_ver, current_ver, target, current = _prepare_diff_restore(
        client, name="disabled恢复"
    )
    res = _restore(client, pid, rid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    state = _get_state(client, pid)
    assert state["stateVersion"] == target_ver
    for key in _SNAPSHOT_KEYS:
        assert state.get(key) == target.get(key), key
    assert state["updatedAt"] == body["restoredAt"]


# ---------- 2/3/4 正常 / 同内容 / 断链 / 回旧版本 ----------


def test_normal_diff_restore_precise_fields_and_source(client: TestClient):
    pid, rid, target_ver, current_ver, target, current = _prepare_diff_restore(
        client, name="正常差异恢复"
    )
    pre_13 = _extract_13(current)
    cp_before = _db_cp_count(pid)
    n0 = _db_rev_count(pid)
    browser0 = _source_count(_db_rev_rows(pid), _SOURCE_BROWSER)
    cp_restore0 = _cp_restore_count(_db_rev_rows(pid))

    res = _restore(client, pid, rid, current_ver)
    body = _assert_success_restore(res, target_version=target_ver)

    state = _get_state(client, pid)
    assert state["stateVersion"] == target_ver
    assert state["stateVersion"] == editor_state_service.compute_full_state_version(
        state
    )
    for key in _SNAPSHOT_KEYS:
        assert state.get(key) == target.get(key), key
    assert state["updatedAt"] == body["restoredAt"]

    # 安全检查点 = 恢复前完整状态
    assert _db_cp_count(pid) == cp_before + 1
    safety = client.get(_cp_url(pid, body["safetyCheckpointId"]))
    assert safety.status_code == 200, safety.text
    assert safety.json()["stateVersion"] == current_ver
    assert safety.json()["snapshot"] == pre_13

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    after_row = _assert_restore_after(rows, target_ver)
    snap = json.loads(after_row.snapshot_json)
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    assert _restore_count(rows) == 1
    assert _source_count(rows, _SOURCE_BROWSER) == browser0
    assert _cp_restore_count(rows) == cp_restore0 == 0


def test_same_content_restore_zero_revision_identity(client: TestClient):
    pid = _create_project(client, name="同内容恢复")
    seed = _seed_via_browser(client, pid, marker="same")
    v0 = _assert_state_version(seed["stateVersion"])
    rid = _find_revision_id_for_version(pid, v0)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    n0 = len(ledger0)
    assert n0 != 0
    state0 = _get_state(client, pid)
    pre_13 = _extract_13(state0)
    pre_updated = state0["updatedAt"]
    cp0 = _db_cp_count(pid)

    res = _restore(client, pid, rid, v0)
    body = _assert_success_restore(res, target_version=v0)
    assert _db_cp_count(pid) == cp0 + 1
    state = _get_state(client, pid)
    assert state["stateVersion"] == v0
    assert _extract_13(state) == pre_13
    assert state["updatedAt"] == body["restoredAt"]
    assert state["updatedAt"] != pre_updated
    rows = _db_rev_rows(pid)
    assert len(rows) == n0
    assert _revision_identity_seq(rows) == ledger0
    assert _restore_count(rows) == 0


def test_legacy_gap_before_after_two_revision_restore(client: TestClient):
    """用途：人工制造合法遗留断链时精确补 before+after。"""
    pid = _create_project(client, name="断链补点")
    a = _seed_via_browser(client, pid, marker="gap-A")
    ver_a = _assert_state_version(a["stateVersion"])
    rid_a = _find_revision_id_for_version(pid, ver_a)
    b = _seed_via_browser(client, pid, marker="gap-B")
    ver_b = _assert_state_version(b["stateVersion"])
    # 删除最新 revision，制造 latest != before 的断链（账本停在 A，状态在 B）
    db = SessionLocal()
    try:
        latest = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == pid)
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .first()
        )
        assert latest is not None
        assert latest.state_version == ver_b
        db.delete(latest)
        db.commit()
    finally:
        db.close()
    assert _db_rev_count(pid) >= 1
    assert all(r.state_version != ver_b for r in _db_rev_rows(pid))

    n0 = _db_rev_count(pid)
    res = _restore(client, pid, rid_a, ver_b)
    body = _assert_success_restore(res, target_version=ver_a)
    rows = _db_rev_rows(pid)
    # 断链：before(B)+after(A) 两条 revision_restore
    restore_rows = [r for r in rows if r.source_kind == _SOURCE_RESTORE]
    assert len(restore_rows) == 2, [(r.state_version, r.source_kind) for r in rows]
    versions = {r.state_version for r in restore_rows}
    assert versions == {ver_a, ver_b}
    assert len(rows) == n0 + 2
    assert body["_after_ver"] == ver_a


def test_revisit_old_version_forms_new_timepoint(client: TestClient):
    pid = _create_project(client, name="回旧版本新时间点")
    a = _seed_via_browser(client, pid, marker="hist-A")
    ver_a = _assert_state_version(a["stateVersion"])
    rid_a = _find_revision_id_for_version(pid, ver_a)
    b = _seed_via_browser(client, pid, marker="hist-B")
    ver_b = _assert_state_version(b["stateVersion"])

    res1 = _restore(client, pid, rid_a, ver_b)
    body1 = _assert_success_restore(res1, target_version=ver_a)
    rows1 = _db_rev_rows(pid)
    first_ids = {
        r.id for r in rows1 if r.source_kind == _SOURCE_RESTORE and r.state_version == ver_a
    }
    assert len(first_ids) == 1
    first_id = next(iter(first_ids))

    c = _seed_via_browser(client, pid, marker="hist-C")
    ver_c = _assert_state_version(c["stateVersion"])
    n_before = _db_rev_count(pid)
    restore_n_before = _restore_count(_db_rev_rows(pid))

    res2 = _restore(client, pid, rid_a, ver_c)
    body2 = _assert_success_restore(res2, target_version=ver_a)
    assert body2["_after_ver"] == ver_a
    rows2 = _db_rev_rows(pid)
    assert len(rows2) == n_before + 1
    restore_rows = [
        r for r in rows2 if r.source_kind == _SOURCE_RESTORE and r.state_version == ver_a
    ]
    assert len(restore_rows) == restore_n_before + 1 == 2
    assert {r.id for r in restore_rows} != {first_id}
    assert first_id in {r.id for r in restore_rows}


# ---------- 5 配额 ----------


def test_revision_10_and_checkpoint_20_trim_independent(client: TestClient):
    pid = _create_project(client, name="双配额裁剪")
    # 先堆 10 条 revision
    versions: list[str] = []
    for i in range(10):
        st = _seed_via_browser(client, pid, marker=f"trim-r-{i}")
        versions.append(st["stateVersion"])
    assert _db_rev_count(pid) == 10

    # 堆 20 条检查点（当前状态）
    for i in range(20):
        res = client.post(_cp_url(pid), json={})
        assert res.status_code == 201, res.text
    assert _db_cp_count(pid) == 20

    # 再前进一次会裁掉最旧 revision；目标取仍会保留的次旧版本
    target_ver = versions[1]
    rid_old = _find_revision_id_for_version(pid, target_ver)
    newer = _seed_via_browser(client, pid, marker="trim-new")
    new_ver = newer["stateVersion"]
    assert new_ver != target_ver
    # 确认目标仍在 10 条账本内
    assert any(r.id == rid_old for r in _db_rev_rows(pid))

    res = _restore(client, pid, rid_old, new_ver)
    body = _assert_success_restore(res, target_version=target_ver)
    rows = _db_rev_rows(pid)
    assert len(rows) == 10  # 修订配额
    assert body["safetyCheckpointId"] in _db_cp_ids(pid)
    assert _db_cp_count(pid) == 20  # 检查点配额，保护新安全点
    # 本场景此前无 restore：恢复后 restore 账本精确 +1，禁止 >=1 假绿
    assert _restore_count(rows) == 1
    assert any(
        r.state_version == target_ver and r.source_kind == _SOURCE_RESTORE for r in rows
    )


# ---------- 6/7 409/404/跨域/损坏 ----------


def test_stale_404_cross_scope_zero_write(client: TestClient):
    pid, rid, target_ver, current_ver, _t, current = _prepare_diff_restore(
        client, name="零写矩阵"
    )
    other = _create_project(client, name="跨项目-c2")
    other_seed = _seed_via_browser(client, other, marker="other")
    other_ledger0 = _revision_identity_seq(_db_rev_rows(other))
    other_state0 = _get_state(client, other)
    other_n0 = _db_rev_count(other)
    other_cp0 = _db_cp_count(other)

    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    bodies0 = _db_chapter_bodies(pid)
    cp0 = _db_cp_count(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)
    assert restore0 == 0

    def _assert_zero(label: str) -> None:
        assert _db_rev_count(pid) == n0, label
        assert _restore_count(_db_rev_rows(pid)) == 0, label
        assert _db_chapter_bodies(pid) == bodies0, label
        assert _db_cp_count(pid) == cp0, label
        assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0, label
        assert _get_state(client, pid) == state0, label

    # 陈旧 expected（目标存在）
    stale = _restore(client, pid, rid, target_ver)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail.get("code") == _CODE_CONFLICT
    assert detail.get("currentStateVersion") == current_ver
    assert set(detail.keys()) == {"code", "message", "currentStateVersion"}
    _assert_zero("陈旧-目标存在")

    # 陈旧 + 目标不存在：仍先 409
    stale_miss = client.post(
        _restore_url(pid, "esr_missing_c2_should_not_echo000001"),
        json={"expectedStateVersion": target_ver},
    )
    assert stale_miss.status_code == 409, stale_miss.text
    assert stale_miss.json()["detail"]["currentStateVersion"] == current_ver
    _assert_zero("陈旧-目标不存在")

    # 404 缺 revision
    miss = client.post(
        _restore_url(pid, "esr_missing_c2_should_not_echo000002"),
        json={"expectedStateVersion": current_ver},
    )
    _assert_fixed_error(
        miss, 404, _CODE_NOT_FOUND, "esr_missing_c2_should_not_echo000002"
    )
    _assert_zero("缺修订")

    # 404 跨项目
    cross = client.post(
        _restore_url(other, rid),
        json={"expectedStateVersion": other_state0["stateVersion"]},
    )
    _assert_fixed_error(cross, 404, _CODE_NOT_FOUND, rid)
    _assert_zero("跨项目")
    assert _db_rev_count(other) == other_n0
    assert _revision_identity_seq(_db_rev_rows(other)) == other_ledger0
    assert _get_state(client, other) == other_state0
    assert _db_cp_count(other) == other_cp0

    # 404 缺项目
    miss_proj = client.post(
        _restore_url("proj_does_not_exist_p12cc2", rid),
        json={"expectedStateVersion": current_ver},
    )
    assert miss_proj.status_code == 404, miss_proj.text
    _assert_fixed_error(miss_proj, 404, _CODE_PROJECT, "proj_does_not_exist_p12cc2")
    _assert_zero("缺项目")


def test_real_cross_workspace_404_zero_side_effects(client: TestClient):
    pid_local, rid_local, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="本空间对照"
    )
    local_ledger0 = _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
    local_state0 = _get_state(client, pid_local)
    local_n0 = _db_rev_count(pid_local, workspace_id=_WS)
    local_cp0 = _db_cp_count(pid_local, workspace_id=_WS)
    local_bodies0 = _db_chapter_bodies(pid_local)

    pid_other, rid_other, ver_other, other_state0 = (
        _seed_foreign_workspace_project_with_revision()
    )
    other_ledger0 = _revision_identity_seq(
        _db_rev_rows(pid_other, workspace_id=_WS_OTHER)
    )
    other_n0 = _db_rev_count(pid_other, workspace_id=_WS_OTHER)
    other_cp0 = _db_cp_count(pid_other, workspace_id=_WS_OTHER)
    other_bodies0 = _db_chapter_bodies(pid_other)

    res = client.post(
        _restore_url(pid_other, rid_other),
        json={"expectedStateVersion": ver_other},
        headers={"X-Workspace-Id": _WS},
    )
    _assert_fixed_error(
        res,
        404,
        _CODE_PROJECT,
        pid_other,
        rid_other,
        _WS_OTHER,
        ver_other,
        _SECRET,
    )

    assert _db_rev_count(pid_local, workspace_id=_WS) == local_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_local, workspace_id=_WS))
        == local_ledger0
    )
    assert _get_state(client, pid_local) == local_state0
    assert _db_cp_count(pid_local, workspace_id=_WS) == local_cp0
    assert _db_chapter_bodies(pid_local) == local_bodies0

    assert _db_rev_count(pid_other, workspace_id=_WS_OTHER) == other_n0
    assert (
        _revision_identity_seq(_db_rev_rows(pid_other, workspace_id=_WS_OTHER))
        == other_ledger0
    )
    assert _db_cp_count(pid_other, workspace_id=_WS_OTHER) == other_cp0
    assert _db_chapter_bodies(pid_other) == other_bodies0
    db = SessionLocal()
    try:
        other_after = editor_state_service.get_editor_state(
            db, _WS_OTHER, pid_other
        )
    finally:
        db.close()
    assert other_after == other_state0


def test_target_corrupt_matrix_fixed_500_zero_write(client: TestClient):
    """用途：目标坏 ID/版本/字节/来源/时间/JSON/键集/非规范/漂移 → 固定 corrupt。"""
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="损坏矩阵"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)

    def _assert_zero() -> None:
        assert _db_rev_count(pid) == n0
        assert _restore_count(_db_rev_rows(pid)) == 0
        assert _db_cp_count(pid) == cp0
        assert _db_chapter_bodies(pid) == bodies0
        assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
        assert _get_state(client, pid) == state0

    # 坏 JSON 正文
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, rid)
        assert row is not None
        row.snapshot_json = '{"broken": true, "secret": "' + _SECRET + '"}'
        db.commit()
    finally:
        db.close()
    res = _restore(client, pid, rid, current_ver)
    _assert_fixed_error(res, 500, _CODE_CORRUPT, rid, current_ver, _SECRET)
    _assert_sanitized_500(res.text, rid, current_ver, _SECRET)
    _assert_zero()

    # 重建干净目标
    pid2, rid2, target_ver2, current_ver2, _t2, _c2 = _prepare_diff_restore(
        client, name="损坏矩阵2"
    )
    n2 = _db_rev_count(pid2)
    cp2 = _db_cp_count(pid2)
    bodies2 = _db_chapter_bodies(pid2)
    state2 = _get_state(client, pid2)

    # 版本漂移：先污染再冻结身份，恢复失败后必须精确等于污染后快照
    with engine.begin() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        conn.execute(
            text(
                "UPDATE editor_state_revisions SET state_version = :v WHERE id = :id"
            ),
            {"v": "esv_" + "f" * 32, "id": rid2},
        )
        conn.execute(text("PRAGMA ignore_check_constraints = OFF"))
    ledger2 = _revision_identity_seq(_db_rev_rows(pid2))
    res2 = _restore(client, pid2, rid2, current_ver2)
    _assert_fixed_error(res2, 500, _CODE_CORRUPT, rid2, "esv_" + "f" * 32)
    assert _db_rev_count(pid2) == n2
    assert _db_cp_count(pid2) == cp2
    assert _db_chapter_bodies(pid2) == bodies2
    assert _revision_identity_seq(_db_rev_rows(pid2)) == ledger2
    assert _get_state(client, pid2) == state2
    assert _restore_count(_db_rev_rows(pid2)) == 0

    # 坏来源
    pid3, rid3, _tv3, current_ver3, _t3, _c3 = _prepare_diff_restore(
        client, name="损坏来源"
    )
    n3 = _db_rev_count(pid3)
    with engine.begin() as conn:
        conn.execute(text("PRAGMA ignore_check_constraints = ON"))
        conn.execute(
            text(
                "UPDATE editor_state_revisions SET source_kind = :s WHERE id = :id"
            ),
            {"s": "forged_source", "id": rid3},
        )
        conn.execute(text("PRAGMA ignore_check_constraints = OFF"))
    ledger3 = _revision_identity_seq(_db_rev_rows(pid3))
    state3 = _get_state(client, pid3)
    res3 = _restore(client, pid3, rid3, current_ver3)
    _assert_fixed_error(res3, 500, _CODE_CORRUPT, "forged_source", rid3)
    assert _db_rev_count(pid3) == n3
    assert _revision_identity_seq(_db_rev_rows(pid3)) == ledger3
    assert _get_state(client, pid3) == state3
    assert _restore_count(_db_rev_rows(pid3)) == 0

    # 坏时间：禁止 ORM 物化损坏行；用原始 SQL 计行 + HTTP 恢复固定 corrupt
    pid4, rid4, _tv4, current_ver4, _t4, _c4 = _prepare_diff_restore(
        client, name="损坏时间"
    )
    with engine.begin() as conn:
        n4 = conn.execute(
            text(
                "SELECT COUNT(*) FROM editor_state_revisions WHERE project_id = :p"
            ),
            {"p": pid4},
        ).scalar_one()
        conn.execute(
            text(
                "UPDATE editor_state_revisions SET created_at = :c WHERE id = :id"
            ),
            {"c": "not-a-datetime", "id": rid4},
        )
    bodies4 = _db_chapter_bodies(pid4)
    state4 = _get_state(client, pid4)
    with TestClient(app, raise_server_exceptions=False) as c500:
        res4 = c500.post(
            _restore_url(pid4, rid4),
            json={"expectedStateVersion": current_ver4},
        )
    _assert_fixed_error(res4, 500, _CODE_CORRUPT, rid4, "not-a-datetime")
    with engine.connect() as conn:
        n4_after = conn.execute(
            text(
                "SELECT COUNT(*) FROM editor_state_revisions WHERE project_id = :p"
            ),
            {"p": pid4},
        ).scalar_one()
        restore_n = conn.execute(
            text(
                "SELECT COUNT(*) FROM editor_state_revisions "
                "WHERE project_id = :p AND source_kind = :s"
            ),
            {"p": pid4, "s": _SOURCE_RESTORE},
        ).scalar_one()
    assert n4_after == n4
    assert restore_n == 0
    assert _db_chapter_bodies(pid4) == bodies4
    assert _get_state(client, pid4) == state4


# ---------- 8 注入失败回滚 ----------


def test_oversize_safety_and_write_drift_zero_write(client: TestClient, monkeypatch):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="超限漂移"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)

    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _huge(db, workspace_id, project_id, expected_state_version):
        row, state = real_lock(db, workspace_id, project_id, expected_state_version)
        state = dict(state)
        state["parsedMarkdown"] = "汉" * (2 * 1024 * 1024)
        return row, state

    # 注入到 restore service 与 checkpoint service 绑定的引用
    import app.services.editor_state_revision_restore_service as restore_svc

    monkeypatch.setattr(
        editor_state_service, "lock_and_assert_expected_state_version", _huge
    )
    monkeypatch.setattr(
        restore_svc.editor_state_service,
        "lock_and_assert_expected_state_version",
        _huge,
    )
    if hasattr(editor_state_checkpoint_service, "editor_state_service"):
        monkeypatch.setattr(
            editor_state_checkpoint_service.editor_state_service,
            "lock_and_assert_expected_state_version",
            _huge,
        )

    res = _restore(client, pid, rid, current_ver)
    _assert_fixed_error(res, 413, _CODE_TOO_LARGE)
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 恢复 lock
    monkeypatch.setattr(
        editor_state_service, "lock_and_assert_expected_state_version", real_lock
    )
    monkeypatch.setattr(
        restore_svc.editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )

    # 写回漂移
    real_apply = editor_state_service.apply_canonical_snapshot_to_locked_row

    def _drift(db, project_id, row, snapshot):
        out = real_apply(db, project_id, row, snapshot)
        out.facts_json = json.dumps(
            [{"id": "drift", "text": _SECRET}], ensure_ascii=False
        )
        return out

    monkeypatch.setattr(
        editor_state_service, "apply_canonical_snapshot_to_locked_row", _drift
    )
    monkeypatch.setattr(
        restore_svc.editor_state_service,
        "apply_canonical_snapshot_to_locked_row",
        _drift,
    )
    monkeypatch.setattr(
        editor_state_checkpoint_service.editor_state_service,
        "apply_canonical_snapshot_to_locked_row",
        _drift,
    )

    res2 = _restore(client, pid, rid, current_ver)
    # 写回漂移公开 500：精确 restore_failed + no-store，禁止 corrupt 假绿
    _assert_fixed_error(res2, 500, _CODE_RESTORE_FAILED, _SECRET, rid)
    _assert_sanitized_500(res2.text, _SECRET, rid)
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid) == state0


def test_recorder_flush_then_fail_full_rollback_and_retryable(
    client: TestClient, monkeypatch
):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="recorder注入"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)

    real_record = editor_state_revision_service.record_editor_state_transition
    calls = {"n": 0}

    def _boom(*args, **kwargs):
        calls["n"] += 1
        out = real_record(*args, **kwargs)
        assert kwargs.get("source_kind") == _SOURCE_RESTORE
        raise RuntimeError(_INJECT_AFTER_FLUSH)

    monkeypatch.setattr(
        editor_state_revision_service, "record_editor_state_transition", _boom
    )
    import app.services.editor_state_checkpoint_service as cps

    monkeypatch.setattr(
        cps.editor_state_revision_service, "record_editor_state_transition", _boom
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, rid),
            json={"expectedStateVersion": current_ver},
        )
    assert calls["n"] == 1
    # recorder flush 后失败：精确 restore_failed + no-store，禁止可选 detail
    _assert_fixed_error(
        res, 500, _CODE_RESTORE_FAILED, _INJECT_AFTER_FLUSH, pid, current_ver, rid, "RuntimeError"
    )
    _assert_sanitized_500(
        res.text, _INJECT_AFTER_FLUSH, pid, current_ver, rid, "RuntimeError"
    )
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    monkeypatch.setattr(
        editor_state_revision_service, "record_editor_state_transition", real_record
    )
    monkeypatch.setattr(
        cps.editor_state_revision_service,
        "record_editor_state_transition",
        real_record,
    )
    retry = _restore(client, pid, rid, current_ver)
    body = _assert_success_restore(retry, target_version=target_ver)
    assert _restore_count(_db_rev_rows(pid)) == 1
    _assert_restore_after(_db_rev_rows(pid), body["_after_ver"])
    assert _db_cp_count(pid) == cp0 + 1


def test_revision_trim_failure_full_rollback(client: TestClient, monkeypatch):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="revision裁剪失败"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)

    real_trim = editor_state_revision_service._trim_revisions

    def _trim_boom(*args, **kwargs):
        raise RuntimeError(_INJECT_REV_TRIM)

    monkeypatch.setattr(
        editor_state_revision_service, "_trim_revisions", _trim_boom
    )
    import app.services.editor_state_checkpoint_service as cps

    monkeypatch.setattr(
        cps.editor_state_revision_service, "_trim_revisions", _trim_boom
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, rid),
            json={"expectedStateVersion": current_ver},
        )
    # revision trim 失败：精确 restore_failed + no-store
    _assert_fixed_error(
        res, 500, _CODE_RESTORE_FAILED, _INJECT_REV_TRIM, pid, rid, "RuntimeError"
    )
    _assert_sanitized_500(res.text, _INJECT_REV_TRIM, pid, rid, "RuntimeError")
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid) == state0

    monkeypatch.setattr(editor_state_revision_service, "_trim_revisions", real_trim)
    monkeypatch.setattr(
        cps.editor_state_revision_service, "_trim_revisions", real_trim
    )
    retry = _restore(client, pid, rid, current_ver)
    _assert_success_restore(retry, target_version=target_ver)


def test_checkpoint_trim_failure_full_rollback(client: TestClient, monkeypatch):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="checkpoint裁剪失败"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    state0 = _get_state(client, pid)

    real_trim = editor_state_checkpoint_service._trim_checkpoints

    def _trim_boom(*args, **kwargs):
        raise RuntimeError(_INJECT_CP_TRIM)

    monkeypatch.setattr(
        editor_state_checkpoint_service, "_trim_checkpoints", _trim_boom
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, rid),
            json={"expectedStateVersion": current_ver},
        )
    # checkpoint trim 失败：精确 restore_failed + no-store
    _assert_fixed_error(
        res, 500, _CODE_RESTORE_FAILED, _INJECT_CP_TRIM, pid, rid, "RuntimeError"
    )
    _assert_sanitized_500(res.text, _INJECT_CP_TRIM, pid, rid, "RuntimeError")
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _get_state(client, pid) == state0

    monkeypatch.setattr(
        editor_state_checkpoint_service, "_trim_checkpoints", real_trim
    )
    retry = _restore(client, pid, rid, current_ver)
    _assert_success_restore(retry, target_version=target_ver)


def test_commit_failure_pending_three_domains_then_full_rollback(
    client: TestClient, monkeypatch
):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="commit失败"
    )
    n0 = _db_rev_count(pid)
    cp0 = _db_cp_count(pid)
    bodies0 = _db_chapter_bodies(pid)
    ledger0 = _revision_identity_seq(_db_rev_rows(pid))
    state0 = _get_state(client, pid)

    import app.services.editor_state_revision_restore_service as restore_svc

    commit_probe: dict = {
        "n": 0,
        "pending": None,
        "restore_pending": None,
        "source": None,
        "after_ver": None,
        "cp_pending": None,
        "safety_version": None,
        "state_version": None,
        "facts_restored": None,
    }

    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit

        def _bad_commit(*a, **k):
            commit_probe["n"] += 1
            commit_probe["pending"] = (
                db.query(EditorStateRevisionRow)
                .filter(EditorStateRevisionRow.project_id == pid)
                .count()
            )
            restore_rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.source_kind == _SOURCE_RESTORE,
                )
                .order_by(EditorStateRevisionRow.created_at.desc())
                .all()
            )
            commit_probe["restore_pending"] = len(restore_rows)
            if restore_rows:
                commit_probe["source"] = restore_rows[0].source_kind
                commit_probe["after_ver"] = restore_rows[0].state_version
            commit_probe["cp_pending"] = (
                db.query(EditorStateCheckpointRow)
                .filter(EditorStateCheckpointRow.project_id == pid)
                .count()
            )
            latest_cp = (
                db.query(EditorStateCheckpointRow)
                .filter(EditorStateCheckpointRow.project_id == pid)
                .order_by(
                    EditorStateCheckpointRow.created_at.desc(),
                    EditorStateCheckpointRow.id.desc(),
                )
                .first()
            )
            if latest_cp is not None:
                commit_probe["safety_version"] = latest_cp.state_version
            es_row = db.get(ProjectEditorStateRow, pid)
            rebuilt = editor_state_service._state_from_row(pid, es_row)
            commit_probe["state_version"] = rebuilt["stateVersion"]
            commit_probe["facts_restored"] = any(
                isinstance(f, dict) and "A-" in str(f.get("text", ""))
                for f in (rebuilt.get("facts") or [])
            )
            raise RuntimeError(_INJECT_COMMIT_FAIL)

        db.commit = _bad_commit  # type: ignore[method-assign]
        return out

    monkeypatch.setattr(
        editor_state_service, "lock_and_assert_expected_state_version", _lock_then_arm
    )
    monkeypatch.setattr(
        restore_svc.editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm,
    )

    with TestClient(app, raise_server_exceptions=False) as c500:
        res = c500.post(
            _restore_url(pid, rid),
            json={"expectedStateVersion": current_ver},
        )
    assert commit_probe["n"] == 1
    assert commit_probe["restore_pending"] == 1, commit_probe
    assert commit_probe["pending"] == n0 + 1, commit_probe
    assert commit_probe["source"] == _SOURCE_RESTORE
    assert _assert_state_version(commit_probe["after_ver"]) == target_ver
    assert commit_probe["cp_pending"] == cp0 + 1
    assert commit_probe["safety_version"] == current_ver
    assert commit_probe["facts_restored"] is True
    assert commit_probe["state_version"] == target_ver
    # commit 失败：精确 restore_failed + no-store，禁止仅 status=500
    _assert_fixed_error(
        res, 500, _CODE_RESTORE_FAILED, _INJECT_COMMIT_FAIL, pid, rid, "RuntimeError"
    )
    _assert_sanitized_500(res.text, _INJECT_COMMIT_FAIL, pid, rid, "RuntimeError")
    assert _db_rev_count(pid) == n0
    assert _db_cp_count(pid) == cp0
    assert _db_chapter_bodies(pid) == bodies0
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger0
    assert _get_state(client, pid) == state0

    # 恢复 commit 可重试
    monkeypatch.setattr(
        editor_state_service, "lock_and_assert_expected_state_version", real_lock
    )
    monkeypatch.setattr(
        restore_svc.editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    retry = _restore(client, pid, rid, current_ver)
    _assert_success_restore(retry, target_version=target_ver)


# ---------- 9 并发 ----------


def test_concurrent_same_expected_one_win_one_409(client: TestClient):
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="双并发"
    )
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))
    cp0 = _db_cp_count(pid)
    assert restore0 == 0
    barrier = threading.Barrier(2)
    conflict_code = _CODE_CONFLICT

    # 动态导入 restore service（实现后可用）
    import importlib

    restore_mod = importlib.import_module(
        "app.services.editor_state_revision_restore_service"
    )

    def worker() -> tuple[int, str | None]:
        db = SessionLocal()
        try:
            barrier.wait(timeout=5)
            try:
                restore_mod.restore_editor_state_revision(
                    db, _WS, pid, rid, current_ver
                )
                return (200, None)
            except editor_state_service.EditorStateVersionConflict:
                db.rollback()
                return (409, conflict_code)
            except Exception as exc:
                db.rollback()
                code = getattr(exc, "code", type(exc).__name__)
                status = getattr(exc, "status_code", 500)
                return (status, code)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker), pool.submit(worker)]
        outcomes = [f.result(timeout=20) for f in futures]

    assert outcomes.count((200, None)) == 1, outcomes
    assert outcomes.count((409, conflict_code)) == 1, outcomes
    assert all(o in ((200, None), (409, conflict_code)) for o in outcomes), outcomes

    rows = _db_rev_rows(pid)
    assert len(rows) == n0 + 1
    assert _restore_count(rows) == 1
    assert _db_cp_count(pid) == cp0 + 1
    state = _get_state(client, pid)
    assert state["stateVersion"] == target_ver


# ---------- 10 来源隔离 ----------


def test_source_isolation_revision_restore_not_checkpoint(client: TestClient):
    pid, rid, target_ver, current_ver, target, current = _prepare_diff_restore(
        client, name="来源隔离"
    )
    browser0 = _source_count(_db_rev_rows(pid), _SOURCE_BROWSER)
    cp_restore0 = _cp_restore_count(_db_rev_rows(pid))

    # 伪造 source 已在 422 覆盖；此处验证成功路径计数
    res = _restore(client, pid, rid, current_ver)
    _assert_success_restore(res, target_version=target_ver)
    rows = _db_rev_rows(pid)
    assert _restore_count(rows) == 1
    assert _source_count(rows, _SOURCE_BROWSER) == browser0
    assert _cp_restore_count(rows) == cp_restore0 == 0
    assert all(
        r.source_kind != _SOURCE_CP_RESTORE
        or r.state_version != target_ver
        for r in rows
    )


def test_checkpoint_restore_still_uses_checkpoint_restore_source(client: TestClient):
    """用途：既有 checkpoint restore 来源/同内容零修订不变。"""
    pid = _create_project(client, name="cp恢复回归")
    a = _seed_via_browser(client, pid, marker="cp-A")
    ver_a = a["stateVersion"]
    cp = client.post(_cp_url(pid), json={})
    assert cp.status_code == 201
    cid = cp.json()["checkpointId"]
    b = _seed_via_browser(client, pid, marker="cp-B")
    ver_b = b["stateVersion"]
    n0 = _db_rev_count(pid)
    restore0 = _restore_count(_db_rev_rows(pid))

    res = client.post(
        f"{_cp_url(pid, cid)}/restore",
        json={"expectedStateVersion": ver_b},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {
        "restoredCheckpointId",
        "safetyCheckpointId",
        "stateVersion",
        "restoredAt",
    }
    assert body["stateVersion"] == ver_a
    rows = _db_rev_rows(pid)
    assert _cp_restore_count(rows) == 1
    assert _restore_count(rows) == restore0 == 0
    assert len(rows) == n0 + 1

    # 同内容 checkpoint restore 零修订
    state = _get_state(client, pid)
    ledger = _revision_identity_seq(rows)
    res2 = client.post(
        f"{_cp_url(pid, cid)}/restore",
        json={"expectedStateVersion": state["stateVersion"]},
    )
    assert res2.status_code == 200, res2.text
    assert _revision_identity_seq(_db_rev_rows(pid)) == ledger
    assert _restore_count(_db_rev_rows(pid)) == 0


# ---------- 11 共享原语边界 ----------


def test_shared_primitive_rejects_arbitrary_source_and_no_txn_owner():
    """用途：共享原语仅允许两来源；伪造来源在触碰 db 前拒绝；无事务所有权。"""
    from app.services import editor_state_checkpoint_service as cps
    from app.services.editor_state_checkpoint_service import EditorStateCheckpointError

    assert hasattr(cps, "stage_locked_canonical_restore")
    stage = cps.stage_locked_canonical_restore

    # 精确允许集合：仅 checkpoint_restore / revision_restore
    allowed = getattr(cps, "_RESTORE_SOURCE_KINDS", None)
    assert allowed == frozenset({"checkpoint_restore", "revision_restore"}), allowed

    class _ExplodingDb:
        """用途：任何属性/方法访问即失败，证明非法 source 未触碰 db。"""

        def __getattr__(self, name: str):
            raise AssertionError(f"非法 source_kind 不应触碰 db.{name}")

        def __bool__(self) -> bool:
            raise AssertionError("非法 source_kind 不应求值 db")

    with pytest.raises(EditorStateCheckpointError) as ei:
        stage(
            _ExplodingDb(),  # type: ignore[arg-type]
            "ws_forged",
            "proj_forged",
            row=None,
            current_state={"stateVersion": "esv_" + ("0" * 32)},
            target_snapshot={"outline": []},
            target_version="esv_" + ("1" * 32),
            source_kind="forged_source",
        )
    exc = ei.value
    assert type(exc) is EditorStateCheckpointError
    assert exc.status_code == 500
    # 非法来源必须精确等于 checkpoint service 固定 corrupt 常量，禁止任意非空串
    assert exc.code == cps.CODE_CHECKPOINT_CORRUPT
    assert exc.message == cps.MSG_CHECKPOINT_CORRUPT

    # 无事务所有权 AST 守卫
    src = ast.get_source_segment(
        _CP_SERVICE_PATH.read_text(encoding="utf-8"),
        _find_function_def(_CP_SERVICE_PATH, "stage_locked_canonical_restore"),
    )
    assert src is not None
    assert "db.commit" not in src
    assert "db.rollback" not in src
    assert "lock_and_assert" not in src


# ---------- 12 旧库迁移 ----------


_OLD_SOURCE_CHECK = (
    "source_kind IN ("
    "'browser_put','task','revise','callback',"
    "'local_parser','content_fuse_apply',"
    "'content_fuse_consume','checkpoint_restore'"
    ")"
)

_NEW_SOURCE_CHECK = (
    "source_kind IN ("
    "'browser_put','task','revise','callback',"
    "'local_parser','content_fuse_apply',"
    "'content_fuse_consume','checkpoint_restore',"
    "'revision_restore'"
    ")"
)


def _build_legacy_engine():
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
        # 八来源各一行
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


def test_legacy_sqlite_eight_source_migration_idempotent_and_failure_safe():
    """用途：旧八来源表真实迁移两次；DDL/行/FK/索引保留；失败不丢数据。"""
    eng = _build_legacy_engine()
    with eng.connect() as conn:
        ddl0 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" not in ddl0
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
        # 旧 CHECK 拒绝 revision_restore
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO editor_state_revisions("
                    "id, workspace_id, project_id, snapshot_json, "
                    "state_version, snapshot_bytes, source_kind, created_at"
                    ") VALUES ("
                    ":id, 'ws_mig', 'proj_mig', :snap, "
                    ":ver, 2, 'revision_restore', '2026-07-16 12:00:00')"
                ),
                {
                    "id": "esr_new_fail",
                    "snap": "{}",
                    "ver": "esv_" + ("1" * 32),
                },
            )
            conn.commit()
        conn.rollback()

    # 第一次迁移
    ensure_schema_columns(target_engine=eng)
    with eng.connect() as conn:
        ddl1 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" in ddl1
        assert "browser_put" in ddl1
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
        idx = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='editor_state_revisions' "
                "ORDER BY name"
            )
        ).fetchall()
        idx_names = {r[0] for r in idx}
        assert "ix_esr_workspace_project_created_id" in idx_names
        # 新来源可写（避免 text() 把 :数字 当 bind）
        new_ver = "esv_" + ("2" * 32)
        conn.execute(
            text(
                "INSERT INTO editor_state_revisions("
                "id, workspace_id, project_id, snapshot_json, "
                "state_version, snapshot_bytes, source_kind, created_at"
                ") VALUES ("
                ":id, 'ws_mig', 'proj_mig', :snap, "
                ":ver, :nbytes, 'revision_restore', '2026-07-16 12:00:00')"
            ),
            {
                "id": "esr_new_ok01",
                "snap": '{"k":true}',
                "ver": new_ver,
                "nbytes": len(b'{"k":true}'),
            },
        )
        conn.commit()
        # 伪造来源仍拒绝
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO editor_state_revisions("
                    "id, workspace_id, project_id, snapshot_json, "
                    "state_version, snapshot_bytes, source_kind, created_at"
                    ") VALUES ("
                    ":id, 'ws_mig', 'proj_mig', :snap, "
                    ":ver, 2, 'forged', '2026-07-16 12:00:00')"
                ),
                {
                    "id": "esr_bad",
                    "snap": "{}",
                    "ver": "esv_" + ("3" * 32),
                },
            )
            conn.commit()
        conn.rollback()
        # FK 级联仍在
        conn.execute(text("DELETE FROM projects WHERE id='proj_mig'"))
        conn.commit()
        left = conn.execute(
            text("SELECT COUNT(*) FROM editor_state_revisions WHERE project_id='proj_mig'")
        ).scalar_one()
        assert left == 0

    # 重建行后第二次迁移幂等
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO projects(id, workspace_id, name) "
            "VALUES ('proj_mig2', 'ws_mig', '迁移项目2')"
        )
        snap = '{"idem":true}'
        conn.exec_driver_sql(
            """
            INSERT INTO editor_state_revisions(
                id, workspace_id, project_id, snapshot_json,
                state_version, snapshot_bytes, source_kind, created_at
            ) VALUES (
                'esr_idem_01', 'ws_mig', 'proj_mig2', :snap,
                :ver, :nbytes, 'browser_put', '2026-07-16 13:00:00'
            )
            """,
            {
                "snap": snap,
                "ver": "esv_" + "4" * 32,
                "nbytes": len(snap.encode("utf-8")),
            },
        )
    ensure_schema_columns(target_engine=eng)
    with eng.connect() as conn:
        ddl2 = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" in ddl2
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM editor_state_revisions")
            ).scalar_one()
            == 1
        )

    # 故障注入：迁移已建临时表并复制后、DROP/RENAME 前抛固定异常
    eng2 = _build_legacy_engine()
    _MIG_INJECT = "p12cc2_injected_migration_midway_failure"
    _TMP_TABLE = "editor_state_revisions__p12cc2_mig"
    with eng2.connect() as conn:
        ddl_pre = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert "revision_restore" not in ddl_pre
        assert "checkpoint_restore" in ddl_pre
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

    def _injecting_exec_driver_sql(self, statement, *args, **kwargs):
        sql = statement if isinstance(statement, str) else str(statement)
        compact = " ".join(sql.split())
        # 紧邻破坏性步骤：临时表已创建并复制后、DROP 旧表前注入
        if compact.upper().startswith("DROP TABLE") and (
            "EDITOR_STATE_REVISIONS" in compact.upper()
            and _TMP_TABLE.upper() not in compact.upper()
        ):
            raise RuntimeError(_MIG_INJECT)
        return _orig_exec_driver_sql(self, statement, *args, **kwargs)

    try:
        _SAConnection.exec_driver_sql = _injecting_exec_driver_sql  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match=re.escape(_MIG_INJECT)):
            ensure_schema_columns(target_engine=eng2)
    finally:
        # 不得永久修改生产/全局 Connection 状态
        _SAConnection.exec_driver_sql = _orig_exec_driver_sql  # type: ignore[method-assign]

    # 新连接精确证明旧表完整保留（临时表不存在；不弱化该断言）
    with eng2.connect() as conn:
        ddl_post = conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='editor_state_revisions'"
            )
        ).scalar_one()
        assert ddl_post == ddl_pre
        assert "revision_restore" not in ddl_post
        assert _OLD_SOURCE_CHECK in ddl_post or all(
            s in ddl_post
            for s in (
                "browser_put",
                "task",
                "revise",
                "callback",
                "local_parser",
                "content_fuse_apply",
                "content_fuse_consume",
                "checkpoint_restore",
            )
        )
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
        assert "ix_esr_workspace_project_created_id" in idx_post
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
        # 旧 CHECK 仍拒绝 revision_restore
        with pytest.raises(Exception):
            conn.execute(
                text(
                    "INSERT INTO editor_state_revisions("
                    "id, workspace_id, project_id, snapshot_json, "
                    "state_version, snapshot_bytes, source_kind, created_at"
                    ") VALUES ("
                    ":id, 'ws_mig', 'proj_mig', :snap, "
                    ":ver, 2, 'revision_restore', '2026-07-16 14:00:00')"
                ),
                {
                    "id": "esr_x_fail",
                    "snap": "{}",
                    "ver": "esv_" + ("5" * 32),
                },
            )
            conn.commit()
        conn.rollback()
        # FK 仍开启且可用
        fk_post = conn.execute(text("PRAGMA foreign_keys")).scalar_one()
        assert int(fk_post) == 1
        conn.execute(text("DELETE FROM projects WHERE id='proj_mig'"))
        conn.commit()
        left = conn.execute(
            text(
                "SELECT COUNT(*) FROM editor_state_revisions "
                "WHERE project_id='proj_mig'"
            )
        ).scalar_one()
        assert left == 0


def test_api_route_exists_and_method_post_only(client: TestClient):
    """用途：路由存在且仅 POST restore；GET 不得当作写。"""
    pid, rid, target_ver, current_ver, _t, _c = _prepare_diff_restore(
        client, name="路由存在"
    )
    get_res = client.get(_restore_url(pid, rid))
    assert get_res.status_code in (404, 405), get_res.text
    post = _restore(client, pid, rid, current_ver)
    # 实现后验收：POST 必须 200，并严格复用成功响应校验
    assert post.status_code == 200, post.text
    _assert_success_restore(post, target_version=target_ver)
