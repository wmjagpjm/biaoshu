"""
模块：P12E-B 双历史修订章节正文差异专项测试
用途：真实 HTTP+SQLite 验收 pair body-diff 只读 API、完整正文判等、
  行差异、唯一配对、截断、作用域/损坏脱敏、五域零写与 AST 禁写。
对接：GET .../editor-state-revisions/{before}/body-diff/{after}
  editor_state_revision_body_diff_service
二次开发：禁止 mock SQLite、宽泛状态码、空集合假绿、跨项目冒充跨空间。
"""

from __future__ import annotations

import ast
import copy
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    AuthAuditEventRow,
    EditorStateCheckpointRow,
    EditorStateRevisionRow,
    Project,
    ProjectEditorStateRow,
    Workspace,
)
from app.services import editor_state_service
from app.services.editor_state_revision_service import record_editor_state_transition

_WS = "ws_local"
_WS_OTHER = "ws_other_p12eb"
_TOP_KEYS = frozenset(
    {
        "sameBody",
        "changedChapterCount",
        "beforeChapterCount",
        "afterChapterCount",
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
_SECRET = "SECRET_P12EB_PAIR_BODY_MUST_NOT_LEAK"
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
_MAX_CHAPTERS = 100


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


def _pair_url(project_id: str, before_id: str, after_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-revisions/"
        f"{before_id}/body-diff/{after_id}"
    )


def _create_project(
    client: TestClient,
    name: str = "P12E-B项目",
    *,
    kind: str = "technical",
) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
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


def _put_editor_state(client: TestClient, pid: str, payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "stateVersion"}
    res = client.put(f"/api/projects/{pid}/editor-state", json=body)
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
    raise AssertionError("record 后未找到 after 版本修订")


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
    assert type(body["beforeChapterCount"]) is int
    assert body["beforeChapterCount"] >= 0
    assert type(body["afterChapterCount"]) is int
    assert body["afterChapterCount"] >= 0
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
    # 禁止泄漏 ID / 版本 / 当前侧命名
    blob = str(body)
    assert "revisionId" not in blob
    assert "stateVersion" not in blob
    assert "currentChapterCount" not in blob
    assert "targetChapterCount" not in blob
    assert "esr_" not in blob
    assert "esv_" not in blob
    assert _SECRET not in blob


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


def _ensure_workspace(ws_id: str, name: str = "其他空间P12EB") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12eb",
                )
            )
            db.commit()
    finally:
        db.close()


def _seed_pair_revisions(
    client: TestClient,
    *,
    before_chapters: list[dict],
    after_chapters: list[dict],
    name: str = "双修订",
) -> tuple[str, str, str]:
    """
    用途：写入两条同项目历史修订，返回 (project_id, before_rid, after_rid)。
    二次开发：before/after 均来自修订表，不依赖请求时当前 editor-state。
    """
    pid = _create_project(client, name=name)
    before_state = _put_editor_state(client, pid, {"chapters": before_chapters})
    before_rid = _revision_id_for_version(pid, before_state["stateVersion"])
    after_state = _put_editor_state(client, pid, {"chapters": after_chapters})
    after_rid = _revision_id_for_version(pid, after_state["stateVersion"])
    # 若两次 put 正文等价未产生新修订，调用方可用 _record 再造第二条
    return pid, before_rid, after_rid


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


def test_pair_body_diff_changed_added_removed(disabled_client):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[
            _ch("keep", "保留章", "old-body"),
            _ch("gone", "删除章", "will-remove"),
        ],
        after_chapters=[
            _ch("keep", "保留章", "new-body"),
            _ch("new", "新增章", "added-body"),
        ],
        name="changed-added-removed",
    )

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["beforeChapterCount"] == 2
    assert body["afterChapterCount"] == 2
    assert body["changedChapterCount"] == 3
    kinds = {item["kind"] for item in body["items"]}
    assert kinds == {"changed", "added", "removed"}
    by_kind = {item["kind"]: item for item in body["items"]}
    assert by_kind["changed"]["beforeTitle"] == "保留章"
    assert by_kind["changed"]["afterTitle"] == "保留章"
    texts_changed = "".join(h["text"] for h in by_kind["changed"]["hunks"])
    assert "old-body" in texts_changed
    assert "new-body" in texts_changed
    assert by_kind["added"]["beforeTitle"] == ""
    assert by_kind["added"]["afterTitle"] == "新增章"
    texts_added = "".join(h["text"] for h in by_kind["added"]["hunks"])
    assert "added-body" in texts_added
    assert by_kind["removed"]["beforeTitle"] == "删除章"
    assert by_kind["removed"]["afterTitle"] == ""
    texts_removed = "".join(h["text"] for h in by_kind["removed"]["hunks"])
    assert "will-remove" in texts_removed


def test_pair_body_diff_same_revision_identical(disabled_client):
    client = disabled_client
    pid = _create_project(client, name="同修订一致")
    state = _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "章", f"正文-{_SECRET}")]},
    )
    rid = _revision_id_for_version(pid, state["stateVersion"])

    res = client.get(_pair_url(pid, rid, rid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True
    assert body["items"] == []
    assert body["changedChapterCount"] == 0
    assert body["beforeChapterCount"] == 1
    assert body["afterChapterCount"] == 1
    assert body["truncated"] is False


def test_pair_body_diff_two_revisions_same_body(disabled_client):
    client = disabled_client
    chapters = [_ch("c1", "同正文", "identical-body-line")]
    pid = _create_project(client, name="两修订同正文")
    # 两条独立历史修订：chapters 相同，非章节字段不同以产生不同 stateVersion/ID
    before_rid = _record(
        pid,
        _state_with_version(),
        _state_with_version(
            chapters=chapters, parsedMarkdown="md-before-same-body"
        ),
    )
    after_rid = _record(
        pid,
        _state_with_version(
            chapters=chapters, parsedMarkdown="md-before-same-body"
        ),
        _state_with_version(
            chapters=copy.deepcopy(chapters),
            parsedMarkdown="md-after-same-body",
        ),
    )
    assert before_rid != after_rid

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is True
    assert body["items"] == []
    assert body["beforeChapterCount"] == 1
    assert body["afterChapterCount"] == 1


# ---------------------------------------------------------------------------
# 错误路径：跨项目 / 跨空间 / 不存在 / 损坏
# ---------------------------------------------------------------------------


def test_pair_missing_cross_project_cross_workspace_404(disabled_client):
    client = disabled_client
    pid_a, before_a, after_a = _seed_pair_revisions(
        client,
        before_chapters=[_ch("a", "A", "1")],
        after_chapters=[_ch("a", "A", "2")],
        name="空间A项目1",
    )
    pid_b, before_b, after_b = _seed_pair_revisions(
        client,
        before_chapters=[_ch("b", "B", "1")],
        after_chapters=[_ch("b", "B", "2")],
        name="空间A项目2",
    )

    res_proj = client.get(_pair_url("proj_dead_0000", before_a, after_a))
    _assert_fixed_error(res_proj, 404, _CODE_PROJECT_NF)

    res_before_nf = client.get(
        _pair_url(pid_a, "esr_" + "0" * 32, after_a)
    )
    _assert_fixed_error(res_before_nf, 404, _CODE_REVISION_NF)

    res_after_nf = client.get(
        _pair_url(pid_a, before_a, "esr_" + "f" * 32)
    )
    _assert_fixed_error(res_after_nf, 404, _CODE_REVISION_NF)

    before_snap_a = _domain_snapshot(pid_a)
    before_snap_b = _domain_snapshot(pid_b)
    res_cross = client.get(_pair_url(pid_a, before_b, after_b))
    _assert_fixed_error(res_cross, 404, _CODE_REVISION_NF)
    assert _domain_snapshot(pid_a) == before_snap_a
    assert _domain_snapshot(pid_b) == before_snap_b

    # 跨工作空间：第二条 ID 来自其他 workspace
    _ensure_workspace(_WS_OTHER)
    other_pid = f"proj_{secrets.token_hex(4)}_{secrets.token_hex(2)}"
    db = SessionLocal()
    try:
        db.add(
            Project(
                id=other_pid,
                workspace_id=_WS_OTHER,
                name="跨空间项目",
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
    res_ws = client.get(_pair_url(pid_a, before_a, other_rid))
    _assert_fixed_error(res_ws, 404, _CODE_REVISION_NF)
    assert _domain_snapshot(pid_a) == before_local
    assert _domain_snapshot(other_pid) == before_other

    res_ws2 = client.get(_pair_url(pid_a, other_rid, after_a))
    _assert_fixed_error(res_ws2, 404, _CODE_REVISION_NF)
    assert _domain_snapshot(pid_a) == before_local
    assert _domain_snapshot(other_pid) == before_other


def test_pair_before_corrupt_fixed_500(disabled_client):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", f"b-{_SECRET}")],
        after_chapters=[_ch("c1", "T", f"a-{_SECRET}")],
        name="前修订损坏",
    )
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, before_rid)
        assert row is not None
        row.snapshot_json = '{"broken": true}'
        db.commit()
    finally:
        db.close()

    before = _domain_snapshot(pid)
    res = client.get(_pair_url(pid, before_rid, after_rid))
    _assert_fixed_error(res, 500, _CODE_CORRUPT)
    assert _domain_snapshot(pid) == before


def test_pair_after_corrupt_fixed_500(disabled_client):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", f"b-{_SECRET}")],
        after_chapters=[_ch("c1", "T", f"a-{_SECRET}")],
        name="后修订损坏",
    )
    db = SessionLocal()
    try:
        row = db.get(EditorStateRevisionRow, after_rid)
        assert row is not None
        row.snapshot_json = '{"broken": true}'
        db.commit()
    finally:
        db.close()

    before = _domain_snapshot(pid)
    res = client.get(_pair_url(pid, before_rid, after_rid))
    _assert_fixed_error(res, 500, _CODE_CORRUPT)
    assert _domain_snapshot(pid) == before


# ---------------------------------------------------------------------------
# 路由探针：无 query/body、no-store
# ---------------------------------------------------------------------------


def test_pair_route_no_query_body_no_store(disabled_client):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", "old")],
        after_chapters=[_ch("c1", "T", "new")],
        name="路由探针",
    )
    url = _pair_url(pid, before_rid, after_rid)
    base = client.get(url)
    assert base.status_code == 200, base.text
    _assert_no_store(base)
    body_base = base.json()
    _assert_success_shape(body_base)

    # 未知 query 不得改变固定语义
    tampered = client.get(
        url,
        params={
            "fields": "all",
            "includeSnapshot": "1",
            "limit": 100,
            "source": "task",
            "q": _SECRET,
        },
    )
    assert tampered.status_code == 200, tampered.text
    assert tampered.json() == body_base
    _assert_no_store(tampered)

    # 不得依赖 body；GET 带 body 仍须返回同一只读结果或被框架拒绝，禁止写副作用
    before_domain = _domain_snapshot(pid)
    with_body = client.request(
        "GET",
        url,
        content=b'{"should":"ignore","secret":"' + _SECRET.encode() + b'"}',
        headers={"Content-Type": "application/json"},
    )
    # Starlette/TestClient 可能接受或忽略 GET body；成功则结果一致
    if with_body.status_code == 200:
        assert with_body.json() == body_base
        _assert_no_store(with_body)
    assert _domain_snapshot(pid) == before_domain


# ---------------------------------------------------------------------------
# 五域零写
# ---------------------------------------------------------------------------


def test_pair_five_domain_zero_write(disabled_client):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", "old")],
        after_chapters=[_ch("c1", "T", "new")],
        name="五域零写",
    )
    cp = client.post(f"/api/projects/{pid}/editor-state-checkpoints", json={})
    assert cp.status_code == 201, cp.text
    before = _domain_snapshot(pid)
    assert before["revisions"]
    assert before["checkpoints"]
    assert before["editor_state"] is not None
    assert before["project"] is not None

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    assert _domain_snapshot(pid) == before

    res_nf = client.get(
        _pair_url(pid, "esr_" + "a" * 32, after_rid)
    )
    assert res_nf.status_code == 404
    assert _domain_snapshot(pid) == before

    res_same = client.get(_pair_url(pid, before_rid, before_rid))
    assert res_same.status_code == 200, res_same.text
    assert _domain_snapshot(pid) == before


def test_pair_service_and_api_no_write_ops_ast(disabled_client):
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
        assert token not in low, f"body-diff service 含禁止 HTTP token: {token}"

    # 双修订入口必须存在；不得读当前 editor-state 走 pair 路径
    assert "compare_revision_bodies" in src_service
    assert "def compare_revision_bodies" in src_service
    # get_editor_state 仅允许出现在 P12E-A 对 current 路径；pair 入口禁止
    assert "get_editor_state_revision" in src_service
    # 模块说明须覆盖 P12E-B 与 C1 校验
    assert "P12E-B" in src_service
    assert "禁止读取当前 editor-state" in src_service or "禁止读取当前" in src_service

    src_api = _API_PATH.read_text(encoding="utf-8")
    assert "body-diff" in src_api
    assert "editor_state_revision_body_diff_service" in src_api
    assert "Cache-Control" in src_api
    # 双修订路由路径必须存在
    assert (
        "body-diff/{after_revision_id}" in src_api
        or "body-diff/{afterRevisionId}" in src_api
        or "/body-diff/{" in src_api
    )


# ---------------------------------------------------------------------------
# 有界 / 反假绿
# ---------------------------------------------------------------------------


def test_pair_max_chapters_cap_applied_before_difflib(disabled_client, monkeypatch):
    """
    用途：最多 100 个实际正文差异章进入 difflib；第 101 个不得进入。
    """
    import app.services.editor_state_revision_body_diff_service as svc

    calls: list[tuple[str, str]] = []
    real_diff = svc._diff_lines

    def _counting_diff(before: str, after: str) -> list[dict[str, str]]:
        calls.append((before, after))
        return real_diff(before, after)

    monkeypatch.setattr(svc, "_diff_lines", _counting_diff)

    client = disabled_client
    before_chs = [
        _ch(f"c{i}", f"T{i}", f"before-{i}") for i in range(_MAX_CHAPTERS + 1)
    ]
    after_chs = [
        _ch(f"c{i}", f"T{i}", f"after-{i}") for i in range(_MAX_CHAPTERS + 1)
    ]
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=before_chs,
        after_chapters=after_chs,
        name="101章cap",
    )

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["truncated"] is True
    assert body["changedChapterCount"] == _MAX_CHAPTERS
    assert len(body["items"]) == _MAX_CHAPTERS
    assert body["beforeChapterCount"] == _MAX_CHAPTERS + 1
    assert body["afterChapterCount"] == _MAX_CHAPTERS + 1
    assert len(calls) <= _MAX_CHAPTERS, (
        f"第101章仍进 difflib: diff_calls={len(calls)}"
    )


def test_pair_full_value_scan_beyond_display_chapter_cap(
    disabled_client, monkeypatch
):
    """
    用途：前 100 章相同、第 101 章才不同 → sameBody=false 且 items 非空。
    """
    import app.services.editor_state_revision_body_diff_service as svc

    calls: list[tuple[str, str]] = []
    real_diff = svc._diff_lines

    def _counting_diff(before: str, after: str) -> list[dict[str, str]]:
        calls.append((before, after))
        return real_diff(before, after)

    monkeypatch.setattr(svc, "_diff_lines", _counting_diff)

    client = disabled_client
    before_chs = [
        _ch(f"s{i}", f"S{i}", "same-body") for i in range(_MAX_CHAPTERS)
    ] + [_ch("tail", "尾章", "before-tail")]
    after_chs = [
        _ch(f"s{i}", f"S{i}", "same-body") for i in range(_MAX_CHAPTERS)
    ] + [_ch("tail", "尾章", "after-tail")]
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=before_chs,
        after_chapters=after_chs,
        name="尾章反假绿",
    )

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert len(body["items"]) >= 1
    assert body["changedChapterCount"] == len(body["items"])
    assert body["items"][0]["kind"] == "changed"
    texts = "".join(h["text"] for h in body["items"][0]["hunks"])
    assert "before-tail" in texts or "after-tail" in texts
    assert len(calls) == 1, f"仅尾章差异应进 difflib，实际 calls={len(calls)}"


def test_pair_does_not_read_current_editor_state(disabled_client, monkeypatch):
    """
    用途：pair 路径禁止读取当前 editor-state；改写当前状态不得影响双修订 diff。
    """
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", "old-v")],
        after_chapters=[_ch("c1", "T", "new-v")],
        name="不读当前状态",
    )
    # 污染当前 editor-state
    _put_editor_state(
        client,
        pid,
        {"chapters": [_ch("c1", "T", "CURRENT_POLLUTED"), _ch("x", "X", "x")]},
    )

    import app.services.editor_state_service as ess

    def _boom(*_a, **_k):
        raise AssertionError("pair body-diff 禁止调用 get_editor_state")

    monkeypatch.setattr(ess, "get_editor_state", _boom)

    res = client.get(_pair_url(pid, before_rid, after_rid))
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_success_shape(body)
    assert body["sameBody"] is False
    assert body["beforeChapterCount"] == 1
    assert body["afterChapterCount"] == 1
    assert body["changedChapterCount"] == 1
    texts = "".join(h["text"] for h in body["items"][0]["hunks"])
    assert "old-v" in texts
    assert "new-v" in texts
    assert "CURRENT_POLLUTED" not in texts


def test_injected_failure_fixed_500(disabled_client, monkeypatch):
    client = disabled_client
    pid, before_rid, after_rid = _seed_pair_revisions(
        client,
        before_chapters=[_ch("c1", "T", "a")],
        after_chapters=[_ch("c1", "T", "b")],
        name="注入失败",
    )
    before = _domain_snapshot(pid)

    import app.services.editor_state_revision_body_diff_service as diff_svc

    def _boom(*_a, **_k):
        raise RuntimeError(f"LEAK_{_SECRET}_PATH_{_PATH_MARKER}")

    monkeypatch.setattr(diff_svc, "_pair_chapters", _boom)
    res = client.get(_pair_url(pid, before_rid, after_rid))
    _assert_fixed_error(res, 500, _CODE_BODY_DIFF_FAILED)
    assert res.json()["detail"]["message"] == _MSG_BODY_DIFF_FAILED
    assert _domain_snapshot(pid) == before
