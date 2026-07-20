"""
模块：P12K 检查点固定优先默认列表后端专项测试
用途：真实 HTTP+SQLite 验收 GET 默认列表 ORDER BY
  is_pinned DESC, created_at DESC, id DESC；
  同时冻结 search 候选/顺序、pin/unpin 下次 GET、零写与隔离。
对接：docs/p12k-checkpoint-pinned-first-list-contract.md；
  editor_state_checkpoint_service.list_editor_state_checkpoints /
  search_editor_state_checkpoints；pin PATCH；create。
二次开发：
  - 禁止 mock 路由、宽泛状态码、恒真断言、skip/xfail、文件级假绿；
  - failure-first 必须因旧列表仍纯时间倒序而失败；
  - AST/SQL 分别锁定 list 三项与 search 两项，不得集合/只看首项。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

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

_WS = "ws_local"
_WS_OTHER = "ws_other_p12k"
_SECRET = "SECRET_P12K_BODY_MUST_NOT_LEAK"
_PATH_MARKER = "/api/projects/leaked/editor-state-checkpoints/list"
_SEARCH_MARKER = "P12K_SEARCH_HIT_MARKER_ξ"

_CHECKPOINT_ID_RE = re.compile(r"^escp_[0-9a-f]{32}$")
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_META_KEYS = frozenset(
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
_LIST_TOP = frozenset({"items"})
_SEARCH_TOP = frozenset({"items"})

_CODE_CORRUPT = "editor_state_checkpoint_corrupt"
_MSG_CORRUPT = "检查点数据损坏，无法读取"
_CODE_PROJECT = "project_not_found"
_CODE_PIN_FAILED = "editor_state_checkpoint_pin_failed"

_SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "editor_state_checkpoint_service.py"
)
_FROZEN_PROD_SHA256 = (
    "20A0FBACFE20DF4D6FE0157B2DF6F41436EDAC5B298F6D2174803E7A66CF4DC3"
)

_LIST_SQL_COLS = (
    "id",
    "state_version",
    "snapshot_bytes",
    "outline_node_count",
    "chapter_count",
    "created_at",
    "display_name",
    "is_pinned",
)
_SEARCH_SQL_COLS = (
    "id",
    "state_version",
    "snapshot_bytes",
    "outline_node_count",
    "chapter_count",
    "created_at",
    "display_name",
    "snapshot_json",
    "is_pinned",
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


# ---------- helpers ----------


def _list_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _pin_url(project_id: str, checkpoint_id: str) -> str:
    return (
        f"/api/projects/{project_id}/editor-state-checkpoints/"
        f"{checkpoint_id}/pin"
    )


def _search_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints/search"


def _create_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-checkpoints"


def _create_project(
    client: TestClient,
    name: str = "P12K项目",
    kind: str = "technical",
) -> str:
    res = client.post("/api/projects", json={"name": name, "kind": kind})
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


def _variant(tag: str, *, body_extra: str = "") -> dict:
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "preview": f"预览{tag}",
                "body": f"正文{tag}-{_SECRET}{body_extra}",
            }
        ],
        parsedMarkdown=f"md-{tag}-{_SECRET}",
        guidance=f"指引-{tag}",
    )


def _count_outline(outline) -> int:
    if not isinstance(outline, dict):
        return 0
    n = 1
    children = outline.get("children")
    if isinstance(children, list):
        for child in children:
            n += _count_outline(child)
    return n


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
    is_pinned: bool = False,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            EditorStateCheckpointRow(
                id=checkpoint_id,
                workspace_id=workspace_id,
                project_id=project_id,
                snapshot_json=snapshot_json,
                state_version=state_version,
                snapshot_bytes=snapshot_bytes,
                outline_node_count=outline_node_count,
                chapter_count=chapter_count,
                created_at=created_at or utc_now(),
                display_name=display_name,
                is_pinned=is_pinned,
            )
        )
        db.commit()
    finally:
        db.close()


def _seed_checkpoint(
    project_id: str,
    state: dict,
    *,
    created_at: datetime | None = None,
    workspace_id: str = _WS,
    checkpoint_id: str | None = None,
    display_name: str | None = None,
    is_pinned: bool = False,
) -> dict:
    snap = editor_state_service.extract_canonical_snapshot(state)
    snap_json = editor_state_service.canonical_snapshot_json(snap)
    cid = checkpoint_id or ("escp_" + secrets.token_hex(16))
    ver = state["stateVersion"]
    nbytes = len(snap_json.encode("utf-8"))
    outline_n = _count_outline(snap.get("outline"))
    chapters = snap.get("chapters")
    chapter_n = (
        sum(1 for c in chapters if isinstance(c, dict))
        if isinstance(chapters, list)
        else 0
    )
    _insert_raw_checkpoint(
        project_id=project_id,
        checkpoint_id=cid,
        snapshot_json=snap_json,
        state_version=ver,
        snapshot_bytes=nbytes,
        outline_node_count=outline_n,
        chapter_count=chapter_n,
        created_at=created_at,
        workspace_id=workspace_id,
        display_name=display_name,
        is_pinned=is_pinned,
    )
    return {
        "id": cid,
        "state_version": ver,
        "snapshot_bytes": nbytes,
        "outline_node_count": outline_n,
        "chapter_count": chapter_n,
        "display_name": display_name,
        "is_pinned": is_pinned,
        "created_at": created_at,
        "snapshot_json": snap_json,
    }


def _ensure_editor_state(client: TestClient, project_id: str, tag: str = "cur") -> dict:
    """用途：PUT 权威 editor-state，供 create 检查点读取。"""
    put = client.put(
        f"/api/projects/{project_id}/editor-state",
        json={
            "outline": {
                "id": f"out_{tag}",
                "title": f"大纲{tag}",
                "children": [],
            },
            "chapters": [
                {
                    "id": f"chap_{tag}",
                    "title": f"章节{tag}",
                    "body": f"正文{tag}-{_SECRET}",
                    "status": "pending",
                    "preview": f"预览{tag}",
                    "wordCount": 3,
                }
            ],
            "facts": [{"id": f"fact_{tag}", "text": f"事实{tag}"}],
            "mode": "ALIGNED",
            "analysis": {
                "overview": f"概述{tag}",
                "techRequirements": [],
                "rejectionRisks": [],
                "scoringPoints": [],
            },
            "responseMatrix": [],
            "guidance": {"hints": [f"提示{tag}"]},
            "parsedMarkdown": f"# md-{tag}",
            "analysisOverview": f"概述{tag}",
        },
    )
    assert put.status_code == 200, put.text
    return put.json()


def _assert_no_store(res) -> None:
    assert res.headers.get("Cache-Control") == "no-store", res.headers


def _assert_fixed_error(
    res,
    status: int,
    code: str,
    *,
    message: str | None = None,
) -> None:
    assert res.status_code == status, res.text
    _assert_no_store(res)
    detail = res.json().get("detail")
    assert isinstance(detail, dict), res.text
    assert "code" in detail and "message" in detail
    assert detail["code"] == code
    if message is not None:
        assert detail["message"] == message
    blob = res.text
    assert _SECRET not in blob
    assert _PATH_MARKER not in blob
    assert "Traceback" not in blob


def _assert_list_ok(res) -> dict:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _LIST_TOP
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 20
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert _CHECKPOINT_ID_RE.match(item["checkpointId"])
        assert _STATE_VERSION_RE.match(item["stateVersion"])
        assert type(item["snapshotBytes"]) is int
        assert type(item["outlineNodeCount"]) is int
        assert type(item["chapterCount"]) is int
        assert isinstance(item["createdAt"], str) and item["createdAt"]
        assert item["displayName"] is None or isinstance(item["displayName"], str)
        assert type(item["isPinned"]) is bool
        assert "snapshot" not in item
        assert _SECRET not in json.dumps(item, ensure_ascii=False)
    return body


def _assert_search_ok(res) -> dict:
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    assert set(body.keys()) == _SEARCH_TOP
    assert isinstance(body["items"], list)
    assert len(body["items"]) <= 20
    for item in body["items"]:
        assert set(item.keys()) == _META_KEYS
        assert type(item["isPinned"]) is bool
        assert "snapshot" not in item
    return body


def _list(client: TestClient, project_id: str):
    return client.get(_list_url(project_id))


def _search(client: TestClient, project_id: str, query: str):
    return client.post(_search_url(project_id), json={"query": query})


def _patch_pin(
    client: TestClient,
    project_id: str,
    checkpoint_id: str,
    is_pinned: bool,
):
    return client.request(
        "PATCH",
        _pin_url(project_id, checkpoint_id),
        json={"isPinned": is_pinned},
    )


def _set_corrupt_is_pinned_sql(checkpoint_id: str, value: int = 2) -> None:
    """用途：绕过 CHECK 写入原始非法 is_pinned。"""
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
    tid = "task_p12k_" + secrets.token_hex(4)
    db = SessionLocal()
    try:
        db.add(
            ProjectTaskRow(
                id=tid,
                project_id=project_id,
                type="parse",
                status="done",
                progress=100,
                message=f"ok-{tag}",
                payload_json="{}",
                result_json="{}",
                error=None,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        db.commit()
    finally:
        db.close()
    return tid


def _ensure_workspace(ws_id: str, name: str = "其他空间P12K") -> None:
    db = SessionLocal()
    try:
        if db.get(Workspace, ws_id) is None:
            db.add(
                Workspace(
                    id=ws_id,
                    name=name,
                    owner_user_id="user_other_p12k",
                )
            )
            db.commit()
    finally:
        db.close()


def _insert_foreign_project(
    *,
    workspace_id: str,
    project_id: str | None = None,
    name: str = "外项目",
) -> str:
    _ensure_workspace(workspace_id)
    pid = project_id or ("proj_" + secrets.token_hex(8))
    db = SessionLocal()
    try:
        if db.get(Project, pid) is None:
            db.add(
                Project(
                    id=pid,
                    workspace_id=workspace_id,
                    name=name,
                    kind="technical",
                    status="draft",
                )
            )
            db.commit()
    finally:
        db.close()
    return pid


def _domain_snapshot(project_id: str) -> dict:
    """用途：五域快照（检查点/修订/编辑态/项目/任务 + 审计样本）。"""
    db = SessionLocal()
    try:
        cps = (
            db.query(EditorStateCheckpointRow)
            .filter(EditorStateCheckpointRow.project_id == project_id)
            .order_by(
                EditorStateCheckpointRow.created_at.desc(),
                EditorStateCheckpointRow.id.desc(),
            )
            .all()
        )
        revs = (
            db.query(EditorStateRevisionRow)
            .filter(EditorStateRevisionRow.project_id == project_id)
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
        editor = db.get(ProjectEditorStateRow, project_id)
        proj = db.get(Project, project_id)
        tasks = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .order_by(ProjectTaskRow.id.desc())
            .all()
        )
        audits = (
            db.query(AuthAuditEventRow)
            .order_by(AuthAuditEventRow.id.desc())
            .limit(50)
            .all()
        )
        return {
            "cps": [
                (
                    r.id,
                    r.state_version,
                    r.snapshot_bytes,
                    r.display_name,
                    bool(r.is_pinned),
                    r.snapshot_json,
                    r.created_at.isoformat()
                    if hasattr(r.created_at, "isoformat")
                    else str(r.created_at),
                )
                for r in cps
            ],
            "revs": [
                (r.id, r.state_version, r.snapshot_bytes, r.source_kind) for r in revs
            ],
            "editor": None
            if editor is None
            else (
                editor.project_id,
                editor.parsed_markdown,
                editor.mode,
                editor.analysis_overview,
                editor.updated_at.isoformat()
                if editor.updated_at is not None
                and hasattr(editor.updated_at, "isoformat")
                else editor.updated_at,
            ),
            "project": None
            if proj is None
            else (
                proj.id,
                proj.name,
                proj.workspace_id,
                proj.updated_at.isoformat()
                if hasattr(proj.updated_at, "isoformat")
                else str(proj.updated_at),
            ),
            "tasks": [(t.id, t.status, t.progress, t.payload_json) for t in tasks],
            "audits": [(a.id, a.action, a.result, a.target) for a in audits],
        }
    finally:
        db.close()


def _prod_sha256() -> str:
    data = _SERVICE_PATH.read_bytes()
    return hashlib.sha256(data).hexdigest().upper()


def _normalize_select_projection(select_list: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in select_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    normalized: list[str] = []
    for col in parts:
        col = re.sub(r"(?is)\s+as\s+[A-Za-z_][\w]*$", "", col).strip()
        if "." in col:
            col = col.rsplit(".", 1)[-1].strip()
        col = col.strip('`"[]')
        normalized.append(col)
    return normalized


def _param_list(params: object) -> list:
    if isinstance(params, dict):
        return list(params.values())
    if isinstance(params, (list, tuple)):
        return list(params)
    return [params]


def _extract_order_by_clause(sql: str) -> str:
    """用途：提取 ORDER BY 子句到 LIMIT/OFFSET/结尾，供精确顺序断言。"""
    m = re.search(
        r"(?is)\border\s+by\s+(.*?)(?:\blimit\b|\boffset\b|$)",
        sql,
    )
    assert m is not None, f"缺少 ORDER BY: {sql}"
    return " ".join(m.group(1).split()).strip().rstrip(",")


def _order_by_terms(order_clause: str) -> list[str]:
    """用途：按逗号深度切分 ORDER BY 项，归一表前缀。"""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in order_clause:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    if current:
        parts.append("".join(current).strip())
    out: list[str] = []
    for p in parts:
        low = " ".join(p.split()).lower()
        # 去表名前缀 editor_state_checkpoints.
        low = re.sub(r"\b[a-z_][\w]*\.", "", low)
        out.append(low)
    return out


def _find_function_node(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in tree.body:  # type: ignore[attr-defined]
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"未找到函数 {name}")


def _collect_order_by_calls(fn: ast.FunctionDef) -> list[ast.Call]:
    """用途：在函数 AST 内收集 .order_by(...) 调用，禁止文件级宽扫。"""
    found: list[ast.Call] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "order_by":
            found.append(node)
    return found


def _order_by_arg_repr(arg: ast.AST, src: str) -> str:
    """用途：把 order_by 参数还原为可比较的归一化片段。"""
    seg = ast.get_source_segment(src, arg) or ""
    compact = re.sub(r"\s+", "", seg)
    return compact


# ---------- 行为：固定优先与组内倒序 ----------


def test_older_pinned_before_newer_unpinned(disabled_client):
    """
    用途：较旧固定项必须排在较新普通项之前。
    failure-first：旧实现纯时间倒序会把新普通项放在第一位。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K旧固定优先")
    base = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
    old_pin = _seed_checkpoint(
        pid,
        _variant("old_pin"),
        created_at=base,
        display_name="旧固定",
        is_pinned=True,
    )
    new_plain = _seed_checkpoint(
        pid,
        _variant("new_plain"),
        created_at=base + timedelta(hours=2),
        display_name="新普通",
        is_pinned=False,
    )
    body = _assert_list_ok(_list(client, pid))
    ids = [it["checkpointId"] for it in body["items"]]
    assert ids == [old_pin["id"], new_plain["id"]], ids
    assert body["items"][0]["isPinned"] is True
    assert body["items"][1]["isPinned"] is False


def test_group_internal_time_and_id_desc(disabled_client):
    """
    用途：固定组与普通组内分别按 created_at DESC, id DESC。
    含同秒不同 ID 的稳定顺序证据。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K组内倒序")
    t0 = datetime(2026, 7, 11, 8, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 11, 9, 0, 0, tzinfo=timezone.utc)
    t_same = datetime(2026, 7, 11, 10, 0, 0, tzinfo=timezone.utc)

    # 固定：同秒两 ID + 更早一条
    pin_early = _seed_checkpoint(
        pid,
        _variant("pin_early"),
        created_at=t0,
        checkpoint_id="escp_" + ("a" * 32),
        is_pinned=True,
        display_name="pin_early",
    )
    pin_same_low = _seed_checkpoint(
        pid,
        _variant("pin_same_low"),
        created_at=t_same,
        checkpoint_id="escp_" + ("b" * 32),
        is_pinned=True,
        display_name="pin_same_low",
    )
    pin_same_high = _seed_checkpoint(
        pid,
        _variant("pin_same_high"),
        created_at=t_same,
        checkpoint_id="escp_" + ("c" * 32),
        is_pinned=True,
        display_name="pin_same_high",
    )
    # 普通
    plain_mid = _seed_checkpoint(
        pid,
        _variant("plain_mid"),
        created_at=t1,
        checkpoint_id="escp_" + ("d" * 32),
        is_pinned=False,
        display_name="plain_mid",
    )
    plain_same_low = _seed_checkpoint(
        pid,
        _variant("plain_same_low"),
        created_at=t_same,
        checkpoint_id="escp_" + ("e" * 32),
        is_pinned=False,
        display_name="plain_same_low",
    )
    plain_same_high = _seed_checkpoint(
        pid,
        _variant("plain_same_high"),
        created_at=t_same,
        checkpoint_id="escp_" + ("f" * 32),
        is_pinned=False,
        display_name="plain_same_high",
    )

    body = _assert_list_ok(_list(client, pid))
    ids = [it["checkpointId"] for it in body["items"]]
    # 固定组：t_same 内 id DESC → high, low；再 t0 early
    # 普通组：t_same 内 id DESC → high, low；再 t1 mid
    expected = [
        pin_same_high["id"],
        pin_same_low["id"],
        pin_early["id"],
        plain_same_high["id"],
        plain_same_low["id"],
        plain_mid["id"],
    ]
    assert ids == expected, ids
    pins = [it["isPinned"] for it in body["items"]]
    assert pins == [True, True, True, False, False, False]


def test_pin_then_next_get_moves_up_unpin_restores(disabled_client):
    """
    用途：PATCH 固定后下一次 GET 上移；取消后回归时间位置；
    PATCH 自身不额外触发 GET/list。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K pin下次GET")
    base = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
    oldest = _seed_checkpoint(
        pid, _variant("oldest"), created_at=base, display_name="最旧"
    )
    mid = _seed_checkpoint(
        pid,
        _variant("mid"),
        created_at=base + timedelta(minutes=1),
        display_name="中间",
    )
    newest = _seed_checkpoint(
        pid,
        _variant("newest"),
        created_at=base + timedelta(minutes=2),
        display_name="最新",
    )

    before = _assert_list_ok(_list(client, pid))
    # 未固定时仍应时间倒序（与固定优先无关的稳定基线）
    assert [it["checkpointId"] for it in before["items"]] == [
        newest["id"],
        mid["id"],
        oldest["id"],
    ]

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_checkpoints" in low and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            # 粗分 list 八列 vs pin 服务读取
            captured.append(statement)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        res = _patch_pin(client, pid, oldest["id"], True)
        assert res.status_code == 200, res.text
        assert res.json() == {"isPinned": True}
        _assert_no_store(res)
        pin_selects = list(captured)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    # PATCH 响应精确一键，不得夹带 list items
    assert "items" not in res.text
    assert "checkpointId" not in res.text
    # pin 路径可有 SELECT，但 PATCH 期间不得出现与默认列表八列投影相等的 SELECT
    assert len(pin_selects) >= 1, pin_selects
    list_proj_hits = 0
    for sql in pin_selects:
        compact = " ".join(sql.split())
        match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
        assert match is not None, sql
        normalized = _normalize_select_projection(match.group(1).strip())
        if normalized == list(_LIST_SQL_COLS):
            list_proj_hits += 1
    assert list_proj_hits == 0, (
        f"PATCH 期间出现默认列表八列投影 {list_proj_hits} 次: {pin_selects}"
    )
    # 顺序变化只能由 PATCH 后显式 GET 观察
    after_pin = _assert_list_ok(_list(client, pid))
    ids_pin = [it["checkpointId"] for it in after_pin["items"]]
    assert ids_pin[0] == oldest["id"], ids_pin
    assert after_pin["items"][0]["isPinned"] is True
    assert ids_pin[1:] == [newest["id"], mid["id"]]
    # 证明顺序变化来自“下一次 GET”，不是 PATCH 返回体
    assert "items" not in res.json()

    res_un = _patch_pin(client, pid, oldest["id"], False)
    assert res_un.status_code == 200, res_un.text
    assert res_un.json() == {"isPinned": False}
    after_un = _assert_list_ok(_list(client, pid))
    ids_un = [it["checkpointId"] for it in after_un["items"]]
    assert ids_un == [newest["id"], mid["id"], oldest["id"]], ids_un
    assert all(it["isPinned"] is False for it in after_un["items"])


def test_create_new_plain_after_pinned_group(disabled_client):
    """用途：create 普通新项 isPinned=false，位于固定组之后。"""
    client = disabled_client
    pid = _create_project(client, name="P12K create后置")
    base = datetime(2026, 7, 13, 10, 0, 0, tzinfo=timezone.utc)
    pin_a = _seed_checkpoint(
        pid,
        _variant("pin_a"),
        created_at=base,
        is_pinned=True,
        display_name="固定A",
    )
    plain_old = _seed_checkpoint(
        pid,
        _variant("plain_old"),
        created_at=base + timedelta(minutes=5),
        is_pinned=False,
        display_name="旧普通",
    )
    _ensure_editor_state(client, pid, "create_cur")
    created = client.post(_create_url(pid), json={})
    assert created.status_code == 201, created.text
    _assert_no_store(created)
    new_body = created.json()
    assert set(new_body.keys()) == _META_KEYS
    assert new_body["isPinned"] is False
    new_id = new_body["checkpointId"]

    body = _assert_list_ok(_list(client, pid))
    ids = [it["checkpointId"] for it in body["items"]]
    # 固定组先，再新 create，再旧普通
    assert ids[0] == pin_a["id"]
    assert body["items"][0]["isPinned"] is True
    assert ids[1] == new_id
    assert body["items"][1]["isPinned"] is False
    assert ids[2] == plain_old["id"]


def test_corrupt_is_pinned_2_list_fixed_500_zero_write(disabled_client):
    """用途：原始 is_pinned=2 使 list 固定 corrupt；no-store；五域零写。"""
    client = disabled_client
    pid = _create_project(client, name="P12K坏固定零写")
    _ensure_editor_state(client, pid, "zw")
    good = _seed_checkpoint(
        pid,
        _variant("good"),
        created_at=datetime(2026, 7, 14, 1, 0, 0, tzinfo=timezone.utc),
        is_pinned=False,
    )
    bad = _seed_checkpoint(
        pid,
        _variant("bad"),
        created_at=datetime(2026, 7, 14, 2, 0, 0, tzinfo=timezone.utc),
        is_pinned=False,
    )
    _insert_revision(pid, "rev_zw")
    _insert_task(pid, tag="zw")
    _set_corrupt_is_pinned_sql(bad["id"], 2)
    before = _domain_snapshot(pid)

    res = _list(client, pid)
    _assert_fixed_error(res, 500, _CODE_CORRUPT, message=_MSG_CORRUPT)
    assert bad["id"] not in res.text
    assert good["id"] not in res.text
    assert _domain_snapshot(pid) == before


def test_cross_project_and_workspace_isolation(disabled_client):
    """用途：其它项目/空间检查点不参与排序或响应。"""
    client = disabled_client
    pid = _create_project(client, name="P12K本项目")
    other_pid = _create_project(client, name="P12K他项目")
    base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)

    mine_pin = _seed_checkpoint(
        pid,
        _variant("mine_pin"),
        created_at=base,
        is_pinned=True,
        display_name="本固定",
    )
    mine_plain = _seed_checkpoint(
        pid,
        _variant("mine_plain"),
        created_at=base + timedelta(hours=1),
        is_pinned=False,
        display_name="本普通",
    )
    other_new = _seed_checkpoint(
        other_pid,
        _variant("other_new"),
        created_at=base + timedelta(hours=3),
        is_pinned=True,
        display_name="他项目最新固定",
    )

    _ensure_workspace(_WS_OTHER)
    foreign_pid = _insert_foreign_project(
        workspace_id=_WS_OTHER, name="跨空间项目"
    )
    foreign = _seed_checkpoint(
        foreign_pid,
        _variant("foreign"),
        created_at=base + timedelta(hours=4),
        workspace_id=_WS_OTHER,
        is_pinned=True,
        display_name="跨空间固定",
    )

    body = _assert_list_ok(_list(client, pid))
    ids = [it["checkpointId"] for it in body["items"]]
    assert ids == [mine_pin["id"], mine_plain["id"]], ids
    assert other_new["id"] not in ids
    assert foreign["id"] not in ids
    blob = json.dumps(body, ensure_ascii=False)
    assert other_new["id"] not in blob
    assert foreign["id"] not in blob
    assert foreign_pid not in blob

    # 外空间项目对本空间应 404
    res_foreign = _list(client, foreign_pid)
    _assert_fixed_error(res_foreign, 404, _CODE_PROJECT)


# ---------- 搜索冻结合同：21 条种子 ----------


def test_search_candidate_window_ignores_oldest_pinned_21st(disabled_client):
    """
    用途：21 条直接种子；最旧第 21 条即使固定且命中也不进候选；
    最新 20 条多项命中仍纯 created_at/id 倒序（不因固定重排）。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K搜索21窗")
    base = datetime(2026, 7, 16, 0, 0, 0, tzinfo=timezone.utc)
    rows: list[dict] = []
    # i=0 最旧 … i=20 最新
    for i in range(21):
        # 最旧固定且带命中标记；若干新项也带标记；部分固定
        hit = i in {0, 5, 10, 15, 20}
        pinned = i in {0, 10, 18}
        tag = f"s{i:02d}"
        body_extra = f"-{_SEARCH_MARKER}" if hit else ""
        state = _variant(tag, body_extra=body_extra)
        # 固定 ID 使同秒可预测；这里秒级递增，ID 用十六进制序号
        cid = "escp_" + f"{i:032x}"
        row = _seed_checkpoint(
            pid,
            state,
            created_at=base + timedelta(seconds=i),
            checkpoint_id=cid,
            display_name=f"名称{tag}" + (_SEARCH_MARKER if hit else ""),
            is_pinned=pinned,
        )
        rows.append(row)

    oldest = rows[0]
    assert oldest["is_pinned"] is True

    body = _assert_search_ok(_search(client, pid, _SEARCH_MARKER))
    ids = [it["checkpointId"] for it in body["items"]]
    # 最旧第 21 条（i=0）不得出现
    assert oldest["id"] not in ids, ids
    # 命中且在窗口内：i=5,10,15,20（按时间/ID 倒序）
    expected_hits = [rows[i]["id"] for i in (20, 15, 10, 5)]
    assert ids == expected_hits, ids
    # 顺序不得因 isPinned 重排：i=10 固定但应在 15 之后、5 之前
    assert body["items"][2]["isPinned"] is True  # i=10
    assert body["items"][0]["isPinned"] is False  # i=20
    assert body["items"][1]["isPinned"] is False  # i=15


# ---------- SQL 运行时证据 ----------


def test_list_sql_order_by_three_terms_limit_20(disabled_client):
    """
    用途：捕获 list SELECT，精确断言 ORDER BY 三项顺序与 LIMIT 20。
    禁止只断言首项或集合包含。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K list SQL")
    base = datetime(2026, 7, 17, 12, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        _seed_checkpoint(
            pid,
            _variant(f"sql{i}"),
            created_at=base + timedelta(seconds=i),
            is_pinned=(i == 0),
        )

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_checkpoints" in low and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        body = _assert_list_ok(_list(client, pid))
        assert len(body["items"]) == 3
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    cp_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_checkpoints" in s.lower()
        and "snapshot_json" not in s.lower()  # list 不含 snapshot
    ]
    assert len(cp_selects) == 1, f"list SELECT 次数异常: {len(cp_selects)}"
    sql, params = cp_selects[0]
    compact = " ".join(sql.split())
    low = compact.lower()
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
    assert match is not None, sql
    normalized = _normalize_select_projection(match.group(1).strip())
    assert normalized == list(_LIST_SQL_COLS), normalized

    order_clause = _extract_order_by_clause(compact)
    terms = _order_by_terms(order_clause)
    # 精确三项有序序列：is_pinned DESC, created_at DESC, id DESC（禁止集合/宽 OR）
    assert terms == ["is_pinned desc", "created_at desc", "id desc"], terms
    # 禁止 CASE/UNION 重排
    assert " case " not in f" {low} "
    assert "union" not in low

    assert re.search(r"\bworkspace_id\s*=", low)
    assert re.search(r"\bproject_id\s*=", low)
    vals = _param_list(params)
    if re.search(r"\blimit\s+20\b", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-1] == 0, params
    elif re.search(r"\blimit\s+\?", low):
        if re.search(r"\boffset\s+\?", low):
            assert vals[-2:] == [20, 0], params
        else:
            assert vals[-1] == 20, params
    else:
        raise AssertionError(f"未发现 LIMIT 20: {sql} {params}")


def test_search_sql_order_by_two_terms_no_pin(disabled_client):
    """
    用途：search SELECT 仍精确 created_at DESC, id DESC 两项；
    不得加入 is_pinned 排序。
    """
    client = disabled_client
    pid = _create_project(client, name="P12K search SQL")
    base = datetime(2026, 7, 17, 15, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        _seed_checkpoint(
            pid,
            _variant(f"ss{i}", body_extra=f"-{_SEARCH_MARKER}"),
            created_at=base + timedelta(seconds=i),
            is_pinned=(i == 1),
            display_name=f"搜{i}",
        )

    captured: list[tuple[str, object]] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        low = statement.lower()
        if "editor_state_checkpoints" in low and statement.lstrip().upper().startswith(
            "SELECT"
        ):
            captured.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        body = _assert_search_ok(_search(client, pid, _SEARCH_MARKER))
        assert len(body["items"]) == 3
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    cp_selects = [
        (s, p)
        for s, p in captured
        if "editor_state_checkpoints" in s.lower() and "snapshot_json" in s.lower()
    ]
    assert len(cp_selects) == 1, f"search SELECT 次数异常: {len(cp_selects)}"
    sql, params = cp_selects[0]
    compact = " ".join(sql.split())
    low = compact.lower()
    match = re.search(r"(?is)\bSELECT\b(.*?)\bFROM\b", compact)
    assert match is not None, sql
    normalized = _normalize_select_projection(match.group(1).strip())
    assert normalized == list(_SEARCH_SQL_COLS), normalized

    order_clause = _extract_order_by_clause(compact)
    terms = _order_by_terms(order_clause)
    # 精确两项有序序列：created_at DESC, id DESC（禁止集合/宽 OR）
    assert terms == ["created_at desc", "id desc"], terms
    # 排序项不得含 is_pinned（投影可含）
    assert all("is_pinned" not in t for t in terms), terms


# ---------- AST 源码门 ----------


def test_list_and_search_ast_order_by_locked():
    """
    用途：分别定位 list/search 函数 AST，锁定 order_by 参数精确序列。
    不得文件级字符串、宽 OR、集合忽略顺序或只看首项。
    """
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    list_fn = _find_function_node(tree, "list_editor_state_checkpoints")
    search_fn = _find_function_node(tree, "search_editor_state_checkpoints")

    list_orders = _collect_order_by_calls(list_fn)
    search_orders = _collect_order_by_calls(search_fn)
    assert len(list_orders) == 1, f"list order_by 次数: {len(list_orders)}"
    assert len(search_orders) == 1, f"search order_by 次数: {len(search_orders)}"

    list_args = [
        _order_by_arg_repr(a, src) for a in list_orders[0].args
    ]
    search_args = [
        _order_by_arg_repr(a, src) for a in search_orders[0].args
    ]

    # list 精确三项
    assert len(list_args) == 3, list_args
    assert list_args[0] == (
        "type_coerce(EditorStateCheckpointRow.is_pinned,Integer).desc()"
    ), list_args[0]
    assert list_args[1] == "EditorStateCheckpointRow.created_at.desc()", list_args[1]
    assert list_args[2] == "EditorStateCheckpointRow.id.desc()", list_args[2]

    # search 精确两项，禁止 is_pinned
    assert len(search_args) == 2, search_args
    assert search_args[0] == "EditorStateCheckpointRow.created_at.desc()", search_args[0]
    assert search_args[1] == "EditorStateCheckpointRow.id.desc()", search_args[1]
    assert all("is_pinned" not in a for a in search_args), search_args

    # list 函数内禁止 Python 排序/二次查询重排迹象
    list_seg = ast.get_source_segment(src, list_fn) or ""
    list_low = list_seg.lower()
    assert "sorted(" not in list_low
    assert ".sort(" not in list_low
    assert "union" not in list_low
    assert "commit(" not in list_low
    assert "flush(" not in list_low


def test_list_docstring_mentions_pinned_first_order():
    """用途：list 函数中文说明同步固定优先语义。"""
    src = _SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    list_fn = _find_function_node(tree, "list_editor_state_checkpoints")
    doc = ast.get_docstring(list_fn) or ""
    # 精确要求“固定优先”与 is_pinned，禁止“固定”单字或 OR 宽匹配假绿
    assert "固定优先" in doc, doc
    assert "is_pinned" in doc, doc
    assert "20" in doc


# ---------- 读路径零写（正常 list） ----------


def test_list_success_five_domain_zero_write(disabled_client):
    """用途：成功 list 不写五域。"""
    client = disabled_client
    pid = _create_project(client, name="P12K成功零写")
    _ensure_editor_state(client, pid, "ok")
    _seed_checkpoint(
        pid,
        _variant("z0"),
        created_at=datetime(2026, 7, 18, 1, 0, 0, tzinfo=timezone.utc),
        is_pinned=True,
    )
    _seed_checkpoint(
        pid,
        _variant("z1"),
        created_at=datetime(2026, 7, 18, 2, 0, 0, tzinfo=timezone.utc),
        is_pinned=False,
    )
    _insert_revision(pid, "rz")
    _insert_task(pid, tag="rz")
    before = _domain_snapshot(pid)
    body = _assert_list_ok(_list(client, pid))
    assert len(body["items"]) == 2
    assert body["items"][0]["isPinned"] is True
    assert _domain_snapshot(pid) == before
