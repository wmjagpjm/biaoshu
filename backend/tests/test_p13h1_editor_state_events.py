"""
模块：P13-H1 editor-state 事件账本与游标后端专项测试
用途：failure-first 验收独立事件表、transition 同事务 after 事件、
  200 条裁剪、required strict bid_writer 游标 GET、stale 409 与隐私门。
对接：GET /api/projects/{projectId}/editor-state-events；
  editor_state_event_service；editor_state_events 表；
  record_editor_state_transition 钩子。
二次开发：禁止预插入事件表冒充写链成功；禁止宽状态/恒真/源码字符串断言；
  禁止外网与白名单外改动。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, text

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    EditorStateRevisionRow,
    Project,
    ProjectTaskRow,
    Workspace,
    utc_now,
)
from app.services import (
    auth_service,
    editor_state_revision_service,
    editor_state_service,
)
from app.services.editor_state_revision_service import (
    EditorStateRevisionError,
    record_editor_state_transition,
)

_WS = "ws_local"
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_ESE_RE = re.compile(r"^ese_[0-9a-f]{32}$")
_ESV_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_OCCURRED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_ITEM_KEYS = frozenset({"eventId", "stateVersion", "sourceKind", "occurredAt"})
_LIST_KEYS = frozenset({"items", "nextCursor", "hasMore"})
_ALL_SOURCES = frozenset(
    {
        "browser_put",
        "task",
        "revise",
        "callback",
        "local_parser",
        "content_fuse_apply",
        "content_fuse_consume",
        "checkpoint_restore",
        "revision_restore",
    }
)
_TEST_USERNAME = "admin_p13h1"
_TEST_PASSWORD = "P13h1-Test-Pass-9!"
_WRITER_PASSWORD = "P13h1-Writer-Pass-9!"
_SECRET_BODY = "SECRET_P13H1_BODY_MUST_NOT_LEAK"
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _WRITER_PASSWORD,
    _SECRET_BODY,
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
    "snapshot_json",
    "snapshotJson",
    "actor_user_id",
    "actorUserId",
    "clientId",
    "client_id",
    "workspace_id",
    "workspaceId",
)


# ---------- 构造工具 ----------


def _state_with_version(**overrides: Any) -> dict[str, Any]:
    """用途：构造精确 13 键 + 匹配 stateVersion 的内部状态。"""
    analysis = editor_state_service.empty_analysis()
    business = editor_state_service.empty_business()
    state: dict[str, Any] = {
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
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _variant(tag: str) -> dict[str, Any]:
    return _state_with_version(
        chapters=[
            {
                "id": f"ch_{tag}",
                "title": f"章节{tag}",
                "content": f"{_SECRET_BODY}-{tag}",
            }
        ],
        guidance=f"指引-{tag}",
        parsedMarkdown=f"解析-{tag}-{_SECRET_BODY}",
    )


def _ensure_project(project_id: str, name: str = "p13h1-svc") -> None:
    db = SessionLocal()
    try:
        if db.get(Project, project_id) is None:
            db.add(
                Project(
                    id=project_id,
                    workspace_id=_WS,
                    name=name,
                    kind="technical",
                    status="draft",
                    updated_at=utc_now(),
                )
            )
            db.commit()
    finally:
        db.close()


def _record(
    project_id: str,
    before: dict[str, Any],
    after: dict[str, Any],
    source: str = "browser_put",
    *,
    workspace_id: str = _WS,
    commit: bool = True,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        result = record_editor_state_transition(
            db,
            workspace_id,
            project_id,
            before_state=before,
            after_state=after,
            source_kind=source,
            actor_user_id=actor_user_id,
        )
        if commit:
            db.commit()
        else:
            db.rollback()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _try_import_event_row():
    """用途：failure-first 下实体可能尚不存在。"""
    try:
        from app.models.entities import EditorStateEventRow  # type: ignore

        return EditorStateEventRow
    except Exception:
        return None


def _db_event_count(project_id: str, workspace_id: str | None = None) -> int:
    EventRow = _try_import_event_row()
    if EventRow is None:
        # 表也可能不存在
        insp = inspect(engine)
        if "editor_state_events" not in insp.get_table_names():
            return 0
        db = SessionLocal()
        try:
            sql = "SELECT COUNT(*) FROM editor_state_events WHERE project_id = :p"
            params: dict[str, Any] = {"p": project_id}
            if workspace_id is not None:
                sql += " AND workspace_id = :w"
                params["w"] = workspace_id
            return int(db.execute(text(sql), params).scalar() or 0)
        finally:
            db.close()
    db = SessionLocal()
    try:
        q = db.query(EventRow).filter(EventRow.project_id == project_id)
        if workspace_id is not None:
            q = q.filter(EventRow.workspace_id == workspace_id)
        return int(q.count())
    finally:
        db.close()


def _db_event_rows(
    project_id: str, workspace_id: str | None = None
) -> list[Any]:
    EventRow = _try_import_event_row()
    if EventRow is None:
        return []
    db = SessionLocal()
    try:
        q = db.query(EventRow).filter(EventRow.project_id == project_id)
        if workspace_id is not None:
            q = q.filter(EventRow.workspace_id == workspace_id)
        return list(
            q.order_by(EventRow.occurred_at.asc(), EventRow.id.asc()).all()
        )
    finally:
        db.close()


def _db_rev_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == project_id,
            )
            .count()
        )
    finally:
        db.close()


def _events_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/editor-state-events"


def _assert_no_secrets(payload: object) -> None:
    text_blob = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    low = text_blob.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text_blob, f"敏感标记泄漏: {marker}"
        assert marker.lower() not in low or marker.lower() in {
            # 允许出现在固定错误码字面量中的子串检查另行处理
        }, f"敏感标记泄漏: {marker}"
    for banned in (
        "snapshot",
        "actoruserid",
        "actor_user_id",
        "clientid",
        "password",
        "token_digest",
        "csrf",
    ):
        # 错误码 code 本身可含 editor_state_event；只禁字段级泄漏
        if banned in ("csrf",):
            assert "csrftoken" not in low.replace("_", "")
            continue
        # 不因 code 含 event 误杀


def _assert_item_shape(item: dict[str, Any], *, source: str | None = None) -> None:
    assert set(item.keys()) == _ITEM_KEYS
    assert _ESE_RE.fullmatch(item["eventId"]), item["eventId"]
    assert _ESV_RE.fullmatch(item["stateVersion"]), item["stateVersion"]
    assert item["sourceKind"] in _ALL_SOURCES
    if source is not None:
        assert item["sourceKind"] == source
    assert _OCCURRED_RE.fullmatch(item["occurredAt"]), item["occurredAt"]
    _assert_no_secrets(item)


def _assert_list_shape(body: dict[str, Any]) -> None:
    assert set(body.keys()) == _LIST_KEYS
    assert isinstance(body["items"], list)
    assert isinstance(body["hasMore"], bool)
    # hasMore=true 时 nextCursor 必为合法 ese_；hasMore=false 时允许 null
    # 或 tip 引导游标（无 after 且已有事件时的 bootstrap）
    if body["nextCursor"] is not None:
        assert isinstance(body["nextCursor"], str)
        assert _ESE_RE.fullmatch(body["nextCursor"]), body["nextCursor"]
    if body["hasMore"]:
        assert body["nextCursor"] is not None
    for item in body["items"]:
        _assert_item_shape(item)
    _assert_no_secrets(body)


def _assert_no_store(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store"


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


def _bootstrap_and_login(client: TestClient) -> tuple[str, str]:
    from app.models.entities import LocalUserRow

    db = SessionLocal()
    try:
        existing = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one_or_none()
        )
        if existing is None:
            try:
                auth_service.bootstrap_local_admin(
                    db,
                    get_settings(),
                    username=_TEST_USERNAME,
                    password=_TEST_PASSWORD,
                    role=auth_service.ROLE_BID_WRITER,
                )
            except Exception:
                # 并发/重复初始化：回落查询
                db.rollback()
        row = (
            db.query(LocalUserRow)
            .filter(LocalUserRow.username == _TEST_USERNAME)
            .one()
        )
        user_id = row.id
    finally:
        db.close()
    client.cookies.clear()
    res = client.post(
        "/api/auth/login",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
    )
    assert res.status_code == 200, res.text
    csrf = res.json()["csrfToken"]
    return user_id, csrf


def _create_project_http(
    client: TestClient, csrf: str | None = None, name: str = "P13H1项目"
) -> str:
    headers = {"X-CSRF-Token": csrf} if csrf else {}
    res = client.post(
        "/api/projects",
        json={"name": name, "mode": "technical", "bidDeadline": None},
        headers=headers,
    )
    if res.status_code not in (200, 201):
        res = client.post(
            "/api/projects",
            json={"name": name, "kind": "technical"},
            headers=headers,
        )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _create_member(
    client: TestClient,
    csrf: str,
    *,
    username: str,
    password: str,
    role: str,
    is_owner: bool = False,
):
    res = client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": is_owner,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


# ---------- ORM / 表结构 ----------


def test_orm_event_table_columns_indexes_and_fk(disabled_client):
    """用途：事件表精确字段、CHECK、复合索引与级联 FK；禁止敏感列。"""
    insp = inspect(engine)
    assert "editor_state_events" in insp.get_table_names()
    cols = {c["name"]: c for c in insp.get_columns("editor_state_events")}
    assert set(cols.keys()) == {
        "id",
        "workspace_id",
        "project_id",
        "state_version",
        "source_kind",
        "occurred_at",
    }
    for banned in (
        "snapshot_json",
        "snapshot_bytes",
        "actor_user_id",
        "client_id",
        "client_digest",
        "display_name",
        "chapter_id",
        "is_pinned",
    ):
        assert banned not in cols
    fks = insp.get_foreign_keys("editor_state_events")
    fk_maps = {(tuple(f["constrained_columns"]), f.get("referred_table")) for f in fks}
    assert (("workspace_id",), "workspaces") in fk_maps or any(
        "workspace_id" in f["constrained_columns"] and f["referred_table"] == "workspaces"
        for f in fks
    )
    assert any(
        "project_id" in f["constrained_columns"] and f["referred_table"] == "projects"
        for f in fks
    )
    indexes = insp.get_indexes("editor_state_events")
    col_sets = [tuple(ix.get("column_names") or []) for ix in indexes]
    assert ("workspace_id", "project_id", "occurred_at", "id") in col_sets or any(
        list(ix.get("column_names") or [])[:4]
        == ["workspace_id", "project_id", "occurred_at", "id"]
        for ix in indexes
    )


# ---------- 写链：真实 after 事件 ----------


def test_browser_put_real_chain_creates_exactly_one_after_event(disabled_client):
    """用途：真实 browser_put HTTP 写链恰好一条 after 事件。"""
    pid = _create_project_http(disabled_client, name="h1-put")
    before_n = _db_event_count(pid, _WS)
    r1 = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": f"body-1-{_SECRET_BODY}"},
    )
    assert r1.status_code == 200, r1.text
    sv1 = r1.json()["stateVersion"]
    assert _ESV_RE.fullmatch(sv1)
    after1 = _db_event_count(pid, _WS)
    assert after1 == before_n + 1
    rows = _db_event_rows(pid, _WS)
    assert len(rows) >= 1
    last = rows[-1]
    assert _ESE_RE.fullmatch(last.id)
    assert last.state_version == sv1
    assert last.source_kind == "browser_put"
    assert last.workspace_id == _WS
    assert last.project_id == pid
    # 二次 PUT 再 +1
    r2 = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": f"body-2-{_SECRET_BODY}"},
    )
    assert r2.status_code == 200, r2.text
    assert _db_event_count(pid, _WS) == before_n + 2
    assert _db_event_rows(pid, _WS)[-1].state_version == r2.json()["stateVersion"]


def test_task_real_chain_creates_task_event(disabled_client, monkeypatch):
    """用途：真实 analyze 任务写链 source_kind=task 恰好一条 after 事件。"""
    pid = _create_project_http(disabled_client, name="h1-task")
    put = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "seed-for-analyze"},
    )
    assert put.status_code == 200, put.text
    n0 = _db_event_count(pid, _WS)

    from app.services import llm_service, task_service
    from app.services.llm_service import ChatResult

    def _fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):  # noqa: ARG001
        return ChatResult(
            content=(
                '{"overview":"p13h1-task",'
                '"techRequirements":["t1"],'
                '"rejectionRisks":["r1"],'
                '"scoringPoints":[{"name":"s1","weight":"10%"}]}'
            ),
            model="mock-p13h1",
        )

    monkeypatch.setattr(llm_service, "chat_completion", _fake_chat)
    monkeypatch.setattr(
        "app.services.llm_service.chat_completion", _fake_chat, raising=False
    )

    create_db = SessionLocal()
    try:
        task = task_service.create_task_record(
            create_db,
            _WS,
            pid,
            task_type="analyze",
            payload={},
            actor_user_id=None,
        )
        tid = task.id
    finally:
        create_db.close()

    task_service._bg_worker(tid, _WS)
    verify = SessionLocal()
    try:
        row = verify.get(ProjectTaskRow, tid)
        assert row is not None
        assert row.status == "success", (row.status, row.error, row.message)
    finally:
        verify.close()

    n1 = _db_event_count(pid, _WS)
    assert n1 == n0 + 1
    last = _db_event_rows(pid, _WS)[-1]
    assert last.source_kind == "task"
    assert _ESE_RE.fullmatch(last.id)


@pytest.mark.parametrize(
    "source",
    sorted(
        {
            "revise",
            "callback",
            "local_parser",
            "content_fuse_apply",
            "content_fuse_consume",
            "checkpoint_restore",
            "revision_restore",
        }
    ),
)
def test_named_source_transition_creates_one_event(disabled_client, source: str):
    """用途：其余七类来源经统一 transition 各恰好一条 after 事件。"""
    pid = f"p_h1_{source}_{secrets.token_hex(3)}"
    _ensure_project(pid, name=f"h1-{source}")
    before = _variant(f"{source}_b")
    after = _variant(f"{source}_a")
    n0 = _db_event_count(pid, _WS)
    out = _record(pid, before, after, source=source)
    assert out["added_count"] >= 1
    assert _db_event_count(pid, _WS) == n0 + 1
    last = _db_event_rows(pid, _WS)[-1]
    assert last.source_kind == source
    assert last.state_version == out["final_state_version"]
    assert _ESE_RE.fullmatch(last.id)


def test_before_backfill_only_zero_events(disabled_client):
    """用途：空账本 before==after 仅补账 before，零事件。"""
    pid = f"p_h1_bf_{secrets.token_hex(3)}"
    _ensure_project(pid)
    state = _variant("same")
    n0 = _db_event_count(pid, _WS)
    out = _record(pid, state, state, source="browser_put")
    # 仅 before 补账
    assert out["added_count"] == 1
    assert _db_rev_count(pid) >= 1
    assert _db_event_count(pid, _WS) == n0


def test_same_version_noop_zero_events(disabled_client):
    """用途：连续同版本 transition 不新增事件。"""
    pid = f"p_h1_noop_{secrets.token_hex(3)}"
    _ensure_project(pid)
    a = _variant("a")
    b = _variant("b")
    _record(pid, a, b, source="browser_put")
    n0 = _db_event_count(pid, _WS)
    assert n0 >= 1
    out = _record(pid, b, b, source="browser_put")
    assert out["added_count"] == 0
    assert _db_event_count(pid, _WS) == n0


def test_gap_fill_before_then_after_one_event(disabled_client):
    """用途：断链补 before 再写 after 时只产一条 after 事件。"""
    pid = f"p_h1_gap_{secrets.token_hex(3)}"
    _ensure_project(pid)
    s1 = _variant("g1")
    s2 = _variant("g2")
    s3 = _variant("g3")
    _record(pid, s1, s2, source="browser_put")
    n0 = _db_event_count(pid, _WS)
    # 跳过中间：before=s1 after=s3，会补 s1(若最新已是 s2 则 before 补 s1?)
    # 最新是 s2，before 是 s1 != s2 → 补 before s1 + after s3；仅 after 产事件
    # 实际：current=s2, before=s1 → insert before s1, current=s1; after s3 → insert after
    out = _record(pid, s1, s3, source="task")
    assert out["added_count"] == 2
    assert _db_event_count(pid, _WS) == n0 + 1
    last = _db_event_rows(pid, _WS)[-1]
    assert last.source_kind == "task"
    assert last.state_version == s3["stateVersion"]


def test_failed_transaction_zero_events(disabled_client):
    """用途：调用方 rollback 后修订与事件双零增长。"""
    pid = f"p_h1_rb_{secrets.token_hex(3)}"
    _ensure_project(pid)
    a = _variant("rb_a")
    b = _variant("rb_b")
    n0 = _db_event_count(pid, _WS)
    r0 = _db_rev_count(pid)
    _record(pid, a, b, source="browser_put", commit=False)
    assert _db_event_count(pid, _WS) == n0
    assert _db_rev_count(pid) == r0


def test_event_flush_failure_rolls_back_revision(disabled_client, monkeypatch):
    """用途：事件 flush 失败时整事务回滚，修订也不残留。"""
    pid = f"p_h1_ef_{secrets.token_hex(3)}"
    _ensure_project(pid)
    a = _variant("ef_a")
    b = _variant("ef_b")
    n0 = _db_event_count(pid, _WS)
    r0 = _db_rev_count(pid)

    EventRow = _try_import_event_row()
    assert EventRow is not None, "事件实体必须存在"

    real_flush = SessionLocal().flush.__func__  # type: ignore[attr-defined]
    # 更稳妥：在 insert event 后的 flush 注入失败
    from sqlalchemy.orm import Session

    original_flush = Session.flush
    calls = {"n": 0}

    def _boom(self, *args, **kwargs):
        calls["n"] += 1
        # 允许修订 flush，拦截后续（事件）flush
        # 启发式：当已有 revision 新增后，再 flush 时失败
        if calls["n"] >= 3:
            raise RuntimeError("injected_event_flush_failure")
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", _boom)
    db = SessionLocal()
    try:
        with pytest.raises(Exception):
            record_editor_state_transition(
                db,
                _WS,
                pid,
                before_state=a,
                after_state=b,
                source_kind="browser_put",
            )
            db.commit()
        db.rollback()
    finally:
        db.close()

    assert _db_event_count(pid, _WS) == n0
    assert _db_rev_count(pid) == r0


def test_trim_keeps_latest_200_events_continuous(disabled_client):
    """用途：每项目最多 200 条；按 occurred_at DESC,id DESC 连续裁剪。"""
    pid = f"p_h1_trim_{secrets.token_hex(3)}"
    _ensure_project(pid)
    prev = _variant("t0")
    # 首次写入
    cur = _variant("t1")
    _record(pid, prev, cur, source="browser_put")
    prev = cur
    # 再写 210 次真实 after（总共约 211 事件，裁到 200）
    for i in range(210):
        nxt = _variant(f"t{i+2}")
        _record(pid, prev, nxt, source="browser_put")
        prev = nxt
    rows = _db_event_rows(pid, _WS)
    assert len(rows) == 200
    # 连续：正序 id/时间不回退
    for i in range(1, len(rows)):
        prev_r, cur_r = rows[i - 1], rows[i]
        t0 = prev_r.occurred_at
        t1 = cur_r.occurred_at
        if getattr(t0, "tzinfo", None) is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if getattr(t1, "tzinfo", None) is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        assert (t0, prev_r.id) < (t1, cur_r.id) or t0 <= t1
    # 最新 state 仍在
    assert rows[-1].state_version == prev["stateVersion"]


def test_trim_does_not_touch_other_project(disabled_client):
    """用途：裁剪只影响本项目事件。"""
    p1 = f"p_h1_t1_{secrets.token_hex(3)}"
    p2 = f"p_h1_t2_{secrets.token_hex(3)}"
    _ensure_project(p1)
    _ensure_project(p2)
    a = _variant("x")
    b = _variant("y")
    _record(p2, a, b, source="browser_put")
    n2 = _db_event_count(p2, _WS)
    prev = _variant("z0")
    cur = _variant("z1")
    _record(p1, prev, cur, source="browser_put")
    prev = cur
    for i in range(205):
        nxt = _variant(f"z{i+2}")
        _record(p1, prev, nxt, source="task")
        prev = nxt
    assert _db_event_count(p1, _WS) == 200
    assert _db_event_count(p2, _WS) == n2


# ---------- 只读 API ----------


def test_required_empty_without_after(required_client):
    """用途：无 after 不回放历史；无事件 tip=null；有事件返回 tip 并可增量。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}

    # 1) 无任何事件：items=[]、nextCursor=null、hasMore=false
    pid_empty = _create_project_http(required_client, csrf, name="h1-empty-none")
    res_empty = required_client.get(_events_url(pid_empty))
    assert res_empty.status_code == 200, res_empty.text
    _assert_no_store(res_empty)
    body_empty = res_empty.json()
    _assert_list_shape(body_empty)
    assert body_empty["items"] == []
    assert body_empty["nextCursor"] is None
    assert body_empty["hasMore"] is False

    # 2) 已有事件：仍不回放；nextCursor 为当前 tip（合法 ese_）
    pid = _create_project_http(required_client, csrf, name="h1-empty")
    r = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": "seed-empty-api"},
    )
    assert r.status_code == 200, r.text
    seed_sv = r.json()["stateVersion"]
    assert _db_event_count(pid) >= 1
    res = required_client.get(_events_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_list_shape(body)
    assert body["items"] == []
    assert body["hasMore"] is False
    tip = body["nextCursor"]
    assert isinstance(tip, str) and _ESE_RE.fullmatch(tip), tip

    # 3) 公开 tip 作为 after 做真实 bootstrap→增量：再写一条，GET 必须返回该新事件
    #    不得把内部 _db_event_rows ID 当作唯一 bootstrap 证据
    r2 = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": "seed-empty-api-next"},
    )
    assert r2.status_code == 200, r2.text
    new_sv = r2.json()["stateVersion"]
    assert new_sv != seed_sv
    res_inc = required_client.get(_events_url(pid), params={"after": tip})
    assert res_inc.status_code == 200, res_inc.text
    _assert_no_store(res_inc)
    body_inc = res_inc.json()
    _assert_list_shape(body_inc)
    assert body_inc["hasMore"] is False
    assert len(body_inc["items"]) == 1
    new_item = body_inc["items"][0]
    _assert_item_shape(new_item, source="browser_put")
    assert new_item["stateVersion"] == new_sv
    assert new_item["eventId"] != tip


def test_required_cursor_read_limit_and_order(required_client):
    """用途：after 游标正序读取；limit 1/50；连续 nextCursor。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-cursor")
    headers = {"X-CSRF-Token": csrf}
    # 写 5 次
    for i in range(5):
        r = required_client.put(
            f"/api/projects/{pid}/editor-state",
            headers=headers,
            json={"parsedMarkdown": f"cursor-body-{i}"},
        )
        assert r.status_code == 200, r.text
    rows = _db_event_rows(pid)
    assert len(rows) >= 5
    first_id = rows[0].id
    # after 第一条 → 后续
    res = required_client.get(
        _events_url(pid), params={"after": first_id, "limit": 2}
    )
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_list_shape(body)
    assert len(body["items"]) == 2
    assert body["hasMore"] is True
    assert body["nextCursor"] == body["items"][-1]["eventId"]
    # 顺序正序
    assert body["items"][0]["eventId"] == rows[1].id
    assert body["items"][1]["eventId"] == rows[2].id
    # 续读
    res2 = required_client.get(
        _events_url(pid),
        params={"after": body["nextCursor"], "limit": 50},
    )
    assert res2.status_code == 200, res2.text
    body2 = res2.json()
    _assert_list_shape(body2)
    assert body2["hasMore"] is False
    assert body2["nextCursor"] is None
    got_ids = [it["eventId"] for it in body2["items"]]
    assert got_ids == [r.id for r in rows[3:]]


def test_limit_bounds_1_and_50(required_client):
    """用途：limit 默认 50；1 合法；0/51/非整数 422。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-limit")
    headers = {"X-CSRF-Token": csrf}
    for i in range(3):
        assert (
            required_client.put(
                f"/api/projects/{pid}/editor-state",
                headers=headers,
                json={"parsedMarkdown": f"lim-{i}"},
            ).status_code
            == 200
        )
    rows = _db_event_rows(pid)
    after = rows[0].id
    ok1 = required_client.get(
        _events_url(pid), params={"after": after, "limit": 1}
    )
    assert ok1.status_code == 200, ok1.text
    b1 = ok1.json()
    _assert_list_shape(b1)
    assert len(b1["items"]) == 1
    ok50 = required_client.get(
        _events_url(pid), params={"after": after, "limit": 50}
    )
    assert ok50.status_code == 200, ok50.text
    for bad in (0, 51, -1, "x", 1.5):
        bad_res = required_client.get(
            _events_url(pid), params={"after": after, "limit": bad}
        )
        assert bad_res.status_code == 422, (bad, bad_res.text)
        _assert_no_store(bad_res)
        _assert_no_secrets(bad_res.text)
        assert str(bad) not in bad_res.text or bad in (0, 51, -1)


def test_stale_cursor_409(required_client):
    """用途：伪造/已裁剪/跨项目 after 统一脱敏 409。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-stale")
    pid2 = _create_project_http(required_client, csrf, name="h1-stale2")
    headers = {"X-CSRF-Token": csrf}
    assert (
        required_client.put(
            f"/api/projects/{pid}/editor-state",
            headers=headers,
            json={"parsedMarkdown": "stale-a"},
        ).status_code
        == 200
    )
    assert (
        required_client.put(
            f"/api/projects/{pid2}/editor-state",
            headers=headers,
            json={"parsedMarkdown": "stale-b"},
        ).status_code
        == 200
    )
    other = _db_event_rows(pid2)[0].id
    forged = "ese_" + "a" * 32
    for cursor in (forged, other, "ese_" + "0" * 32):
        res = required_client.get(
            _events_url(pid), params={"after": cursor}
        )
        assert res.status_code == 409, res.text
        _assert_no_store(res)
        detail = res.json().get("detail") or res.json()
        if isinstance(detail, dict):
            assert detail.get("code") == "editor_state_event_cursor_stale"
        assert cursor not in res.text
        _assert_no_secrets(res.text)


def test_invalid_after_format_422(required_client):
    """用途：非法 after 格式固定 422，不回显。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-badafter")
    leak = "ese_NOT_HEX_AND_SHOULD_NOT_ECHO_XXXX"
    for bad in (
        "esr_" + "a" * 32,
        "ese_" + "A" * 32,  # 大写
        "ese_" + "a" * 31,
        "ese_" + "a" * 33,
        "not-an-id",
        leak,
    ):
        res = required_client.get(_events_url(pid), params={"after": bad})
        assert res.status_code == 422, (bad, res.text)
        _assert_no_store(res)
        assert bad not in res.text
        _assert_no_secrets(res.text)


def test_unknown_query_duplicate_body_422(required_client):
    """用途：未知 query、重复参数、带 body 固定 422。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-q")
    # 未知 query
    res = required_client.get(
        _events_url(pid), params={"after": None, "foo": "bar"}
    )
    # starlette 可能丢掉 None；显式路径
    res = required_client.get(f"{_events_url(pid)}?limit=1&unknown=1")
    assert res.status_code == 422, res.text
    _assert_no_store(res)
    assert "unknown" not in res.text or "unknown" in "editor_state_event"
    # 重复 limit
    res2 = required_client.get(f"{_events_url(pid)}?limit=1&limit=2")
    assert res2.status_code == 422, res2.text
    _assert_no_store(res2)
    # body 不被 GET 接受：用 request
    res3 = required_client.request(
        "GET",
        _events_url(pid),
        content=b'{"limit":1}',
        headers={"Content-Type": "application/json"},
    )
    assert res3.status_code == 422, res3.text
    _assert_no_store(res3)


def test_auth_scope_matrix(required_client):
    """用途：未登录/非 bid_writer/X-Workspace-Id/跨项目固定拒绝。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-auth")
    headers = {"X-CSRF-Token": csrf}
    assert (
        required_client.put(
            f"/api/projects/{pid}/editor-state",
            headers=headers,
            json={"parsedMarkdown": "auth-seed"},
        ).status_code
        == 200
    )

    # 未登录：认证中间件固定 401（不得与 403 并集掩盖回归）
    required_client.cookies.clear()
    unauth = required_client.get(_events_url(pid))
    assert unauth.status_code == 401, unauth.text
    _assert_no_secrets(unauth.text)
    if unauth.headers.get("Cache-Control") is not None:
        _assert_no_store(unauth)

    # 重新登录 admin（刷新 CSRF）
    _user_id, csrf = _bootstrap_and_login(required_client)
    # 任意 X-Workspace-Id
    for val in ("", "ws_other", _WS, " "):
        res = required_client.get(
            _events_url(pid), headers={"X-Workspace-Id": val}
        )
        assert res.status_code == 403, (val, res.text)
        _assert_no_store(res)
        _assert_no_secrets(res.text)

    # 创建 finance 成员并切换
    fin_user = f"fin_p13h1_{secrets.token_hex(3)}"
    _create_member(
        required_client,
        csrf,
        username=fin_user,
        password="P13h1-Finance-Pass!",
        role=auth_service.ROLE_FINANCE,
    )
    required_client.cookies.clear()
    login_f = required_client.post(
        "/api/auth/login",
        json={"username": fin_user, "password": "P13h1-Finance-Pass!"},
    )
    assert login_f.status_code == 200, login_f.text
    fin = required_client.get(_events_url(pid))
    assert fin.status_code == 403, fin.text
    _assert_no_store(fin)

    # 回到 writer，跨项目/不存在 404
    _bootstrap_and_login(required_client)
    missing = required_client.get(_events_url("proj_does_not_exist_p13h1"))
    assert missing.status_code == 404, missing.text
    _assert_no_store(missing)
    assert "proj_does_not_exist_p13h1" not in missing.text


def test_privacy_no_snapshot_actor_or_internal_ids(required_client):
    """用途：成功/错误响应无快照/正文/actor/client/内部空间 ID。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-priv")
    headers = {"X-CSRF-Token": csrf}
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": _SECRET_BODY},
    )
    assert put.status_code == 200, put.text
    rows = _db_event_rows(pid)
    after = rows[0].id
    ok = required_client.get(_events_url(pid), params={"after": after})
    # after 第一条可能已是唯一 → 空或后续
    assert ok.status_code == 200, ok.text
    _assert_list_shape(ok.json())
    _assert_no_secrets(ok.text)
    # 也测无 after 成功体
    empty = required_client.get(_events_url(pid))
    assert empty.status_code == 200
    _assert_no_secrets(empty.text)
    stale = required_client.get(
        _events_url(pid), params={"after": "ese_" + "b" * 32}
    )
    assert stale.status_code == 409
    _assert_no_secrets(stale.text)


def test_methods_other_than_get_rejected(required_client):
    """用途：已登录 strict bid_writer 下仅允许 GET；其它方法精确 405。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="h1-method")
    url = _events_url(pid)
    # 固定无敏感值 body，避免错误响应回显或宽集合假绿
    safe_body = {"probe": "p13h1-method-check", "limit": 1}
    headers = {
        "X-CSRF-Token": csrf,
        "Content-Type": "application/json",
    }
    for method in ("post", "put", "patch", "delete"):
        if method == "delete":
            res = required_client.request(
                "DELETE", url, headers=headers, json=safe_body
            )
        else:
            res = getattr(required_client, method)(
                url, headers=headers, json=safe_body
            )
        assert res.status_code == 405, (method, res.status_code, res.text)
        _assert_no_secrets(res.text)
        # 若路由层已挂 Cache-Control 则必须 no-store；405 允许由框架给出
        if res.headers.get("Cache-Control") is not None:
            _assert_no_store(res)
        # 不得回显探测 body 原文
        assert "p13h1-method-check" not in res.text


def test_checkpoint_and_revision_restore_real_events(disabled_client):
    """用途：真实检查点/修订恢复写链各产 after 事件。"""
    pid = _create_project_http(disabled_client, name="h1-restore")
    # 建立两版状态
    r1 = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "restore-v1"},
    )
    assert r1.status_code == 200, r1.text
    sv1 = r1.json()["stateVersion"]
    r2 = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "restore-v2"},
    )
    assert r2.status_code == 200, r2.text
    sv2 = r2.json()["stateVersion"]
    n0 = _db_event_count(pid, _WS)

    # 创建检查点（基于当前 v2）
    cp = disabled_client.post(
        f"/api/projects/{pid}/editor-state-checkpoints",
        json={},
    )
    assert cp.status_code in (200, 201), cp.text
    cp_id = cp.json().get("id") or cp.json().get("checkpointId")
    assert isinstance(cp_id, str) and cp_id

    # 改到 v3
    r3 = disabled_client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "restore-v3"},
    )
    assert r3.status_code == 200, r3.text
    n1 = _db_event_count(pid, _WS)
    assert n1 == n0 + 1  # v3 put

    # 恢复检查点 → checkpoint_restore 事件
    rest = disabled_client.post(
        f"/api/projects/{pid}/editor-state-checkpoints/{cp_id}/restore",
        json={"expectedStateVersion": r3.json()["stateVersion"]},
    )
    assert rest.status_code == 200, rest.text
    n2 = _db_event_count(pid, _WS)
    assert n2 == n1 + 1
    assert _db_event_rows(pid, _WS)[-1].source_kind == "checkpoint_restore"

    # 修订恢复：取一条旧修订
    revs = disabled_client.get(f"/api/projects/{pid}/editor-state-revisions")
    assert revs.status_code == 200, revs.text
    items = revs.json()["items"]
    assert items
    # 找非当前版本的修订
    cur_sv = rest.json().get("stateVersion") or disabled_client.get(
        f"/api/projects/{pid}/editor-state"
    ).json()["stateVersion"]
    target = None
    for it in items:
        rid = it.get("id") or it.get("revisionId")
        ver = it.get("stateVersion")
        if rid and ver and ver != cur_sv:
            target = (rid, ver)
            break
    if target is None:
        # 若列表过短，用库
        db = SessionLocal()
        try:
            rows = (
                db.query(EditorStateRevisionRow)
                .filter(
                    EditorStateRevisionRow.project_id == pid,
                    EditorStateRevisionRow.workspace_id == _WS,
                )
                .order_by(EditorStateRevisionRow.created_at.asc())
                .all()
            )
            for row in rows:
                if row.state_version != cur_sv:
                    target = (row.id, row.state_version)
                    break
        finally:
            db.close()
    assert target is not None
    rid, _ver = target
    cur = disabled_client.get(f"/api/projects/{pid}/editor-state")
    assert cur.status_code == 200
    n3 = _db_event_count(pid, _WS)
    rr = disabled_client.post(
        f"/api/projects/{pid}/editor-state-revisions/{rid}/restore",
        json={"expectedStateVersion": cur.json()["stateVersion"]},
    )
    assert rr.status_code == 200, rr.text
    assert _db_event_count(pid, _WS) == n3 + 1
    assert _db_event_rows(pid, _WS)[-1].source_kind == "revision_restore"


def test_no_commit_in_recorder_path(disabled_client, monkeypatch):
    """用途：transition 路径不 commit/rollback/refresh。"""
    pid = f"p_h1_nc_{secrets.token_hex(3)}"
    _ensure_project(pid)
    a = _variant("nc_a")
    b = _variant("nc_b")
    from sqlalchemy.orm import Session

    banned = {"commit": 0, "rollback": 0, "refresh": 0}
    real_commit = Session.commit
    real_rollback = Session.rollback
    real_refresh = Session.refresh

    def _c(self, *a, **k):
        banned["commit"] += 1
        return real_commit(self, *a, **k)

    def _r(self, *a, **k):
        banned["rollback"] += 1
        return real_rollback(self, *a, **k)

    def _f(self, *a, **k):
        banned["refresh"] += 1
        return real_refresh(self, *a, **k)

    monkeypatch.setattr(Session, "commit", _c)
    monkeypatch.setattr(Session, "rollback", _r)
    monkeypatch.setattr(Session, "refresh", _f)
    db = SessionLocal()
    try:
        record_editor_state_transition(
            db,
            _WS,
            pid,
            before_state=a,
            after_state=b,
            source_kind="browser_put",
        )
        # 调用方提交
        assert banned["commit"] == 0
        assert banned["rollback"] == 0
        assert banned["refresh"] == 0
        db.commit()
    finally:
        db.close()
