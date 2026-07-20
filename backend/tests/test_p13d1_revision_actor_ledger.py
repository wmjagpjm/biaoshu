"""
模块：P13-D1 editor-state 修订操作者可信账本专项测试
用途：验收两列可空 actor 迁移、request-state 身份 helper、recorder before/after 语义、
  九类写链传播、任务异步持久身份、disabled/注入/泄漏/回滚门。
对接：editor_state_revision_service；ProjectTaskRow/EditorStateRevisionRow；
  deps.get_request_actor_user_id；九类写入口。
二次开发：禁止外网；禁止客户端投稿 actor；禁止回填历史；失败须原事务回滚。
"""

from __future__ import annotations

import ast
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import deps
from app.core.config import get_settings
from app.core.database import SessionLocal, ensure_schema_columns, engine
from app.main import app
from app.models.entities import (
    EditorStateRevisionRow,
    Project,
    ProjectTaskRow,
    Workspace,
    utc_now,
)

# 后端源码根（本文件位于 backend/tests/）
_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _ensure_project(project_id: str, name: str = "p13d1-svc") -> None:
    """用途：为服务层直写 revision 准备存在的 project（满足 FK）。"""
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
_ACTOR = "user_p13d1_actor"
_FAKE = "user_client_forged_actor"
_SNAPSHOT_KEYS = frozenset(editor_state_service.CANONICAL_STATE_KEYS)
_STATE_VERSION_RE = re.compile(r"^esv_[0-9a-f]{32}$")
_TEST_USERNAME = "admin_p13d1"
_TEST_PASSWORD = "P13d1-Test-Pass-9!"


# ---------- 构造工具 ----------


def _state_with_version(**overrides) -> dict:
    """用途：构造精确 13 键 + 匹配 stateVersion 的内部状态。"""
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
    assert set(snap.keys()) == _SNAPSHOT_KEYS
    payload = editor_state_service.canonical_snapshot_json(snap)
    state["stateVersion"] = (
        editor_state_service.compute_state_version_from_canonical_json(payload)
    )
    return state


def _variant(tag: str) -> dict:
    return _state_with_version(
        chapters=[{"id": f"ch_{tag}", "title": f"章节{tag}", "content": f"正文{tag}"}],
        guidance=f"指引-{tag}",
        parsedMarkdown=f"解析-{tag}",
    )


def _create_project(client: TestClient, name: str = "P13D1项目") -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "mode": "technical", "bidDeadline": None},
    )
    assert res.status_code in (200, 201), res.text
    body = res.json()
    return body["id"] if "id" in body else body["projectId"]


def _db_rev_rows(project_id: str) -> list[EditorStateRevisionRow]:
    db = SessionLocal()
    try:
        return list(
            db.query(EditorStateRevisionRow)
            .filter(
                EditorStateRevisionRow.workspace_id == _WS,
                EditorStateRevisionRow.project_id == project_id,
            )
            .order_by(
                EditorStateRevisionRow.created_at.desc(),
                EditorStateRevisionRow.id.desc(),
            )
            .all()
        )
    finally:
        db.close()


def _assert_sv(version: object) -> str:
    assert isinstance(version, str)
    assert _STATE_VERSION_RE.fullmatch(version), version
    return version


def _table_cols(conn, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }


def _fk_list(conn, table: str) -> list:
    return list(conn.execute(text(f"PRAGMA foreign_key_list({table})")).fetchall())


def _index_list(conn, table: str) -> list[str]:
    rows = conn.execute(text(f"PRAGMA index_list({table})")).fetchall()
    # (seq, name, unique, origin, partial)
    return [r[1] for r in rows if r is not None and len(r) > 1]


# ---------- failure-first：模型 / 迁移 ----------


def test_orm_actor_columns_exist_nullable_no_fk_no_index():
    """用途：两表 ORM 含可空 actor_user_id；无 FK、无 actor 索引。"""
    insp = inspect(engine)
    # 首个业务断言：修订表必须有 actor_user_id 列
    rev_cols = {c["name"]: c for c in insp.get_columns("editor_state_revisions")}
    assert "actor_user_id" in rev_cols, sorted(rev_cols)
    assert rev_cols["actor_user_id"]["nullable"] is True

    task_cols = {c["name"]: c for c in insp.get_columns("project_tasks")}
    assert "actor_user_id" in task_cols, sorted(task_cols)
    assert task_cols["actor_user_id"]["nullable"] is True

    # 无指向 users 的 actor FK
    rev_fks = insp.get_foreign_keys("editor_state_revisions")
    task_fks = insp.get_foreign_keys("project_tasks")
    for fk in [*rev_fks, *task_fks]:
        constrained = fk.get("constrained_columns") or []
        assert "actor_user_id" not in constrained, fk

    rev_idx = insp.get_indexes("editor_state_revisions")
    task_idx = insp.get_indexes("project_tasks")
    for idx in [*rev_idx, *task_idx]:
        cols = idx.get("column_names") or []
        assert "actor_user_id" not in cols, idx


def test_sqlite_idempotent_migration_adds_actor_columns(tmp_path: Path):
    """用途：旧库两表缺列时幂等 ADD COLUMN；二次迁移不改数据。"""
    db_path = tmp_path / "p13d1_old.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE editor_state_revisions (
              id VARCHAR(64) PRIMARY KEY,
              workspace_id VARCHAR(64) NOT NULL,
              project_id VARCHAR(64) NOT NULL,
              snapshot_json TEXT NOT NULL,
              state_version VARCHAR(64) NOT NULL,
              snapshot_bytes INTEGER NOT NULL,
              source_kind VARCHAR(64) NOT NULL,
              display_name VARCHAR(160),
              is_pinned INTEGER NOT NULL DEFAULT 0,
              created_at DATETIME NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE project_tasks (
              id VARCHAR(64) PRIMARY KEY,
              project_id VARCHAR(64) NOT NULL,
              type VARCHAR(64) NOT NULL,
              status VARCHAR(32) NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              message VARCHAR(1000) NOT NULL DEFAULT '',
              payload_json TEXT,
              result_json TEXT,
              error TEXT,
              created_at DATETIME NOT NULL,
              updated_at DATETIME NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO editor_state_revisions (
              id, workspace_id, project_id, snapshot_json, state_version,
              snapshot_bytes, source_kind, is_pinned, created_at
            ) VALUES (
              'esr_old1', 'ws_local', 'proj_old', '{}', 'esv_' || hex(randomblob(16)),
              2, 'browser_put', 0, datetime('now')
            )
            """
        )
        conn.exec_driver_sql(
            """
            INSERT INTO project_tasks (
              id, project_id, type, status, progress, message, created_at, updated_at
            ) VALUES (
              'task_old1', 'proj_old', 'parse', 'success', 100, 'done',
              datetime('now'), datetime('now')
            )
            """
        )
        assert "actor_user_id" not in _table_cols(conn, "editor_state_revisions")
        assert "actor_user_id" not in _table_cols(conn, "project_tasks")

    ensure_schema_columns(old_engine)
    ensure_schema_columns(old_engine)  # 幂等

    with old_engine.connect() as conn:
        rev_cols = _table_cols(conn, "editor_state_revisions")
        task_cols = _table_cols(conn, "project_tasks")
        assert "actor_user_id" in rev_cols
        assert "actor_user_id" in task_cols
        # 旧行保持 NULL
        rev_actor = conn.execute(
            text("SELECT actor_user_id FROM editor_state_revisions WHERE id='esr_old1'")
        ).scalar()
        task_actor = conn.execute(
            text("SELECT actor_user_id FROM project_tasks WHERE id='task_old1'")
        ).scalar()
        assert rev_actor is None
        assert task_actor is None
        # 无 actor FK / 无 actor 索引名
        for table in ("editor_state_revisions", "project_tasks"):
            for fk in _fk_list(conn, table):
                # PRAGMA foreign_key_list: id, seq, table, from, to, ...
                assert fk[3] != "actor_user_id", fk
            for name in _index_list(conn, table):
                assert "actor" not in name.lower(), name

    old_engine.dispose()


# ---------- request-state helper ----------


def test_get_request_actor_user_id_required_and_disabled(monkeypatch):
    """用途：仅读 auth_db_user_id；disabled/非法/超长/空白均 None。"""
    # 首个业务断言：helper 必须存在
    assert hasattr(deps, "get_request_actor_user_id"), dir(deps)
    helper = deps.get_request_actor_user_id

    monkeypatch.setenv("AUTH_MODE", "required")
    get_settings.cache_clear()
    req = SimpleNamespace(state=SimpleNamespace(auth_db_user_id=_ACTOR))
    assert helper(req) == _ACTOR  # type: ignore[arg-type]

    # 空白 / 非字符串 / 超长 → None
    for bad in ("", "   ", None, 123, "x" * 65):
        req.state.auth_db_user_id = bad
        assert helper(req) is None  # type: ignore[arg-type]

    # disabled 固定 None，即使 state 有值
    monkeypatch.setenv("AUTH_MODE", "disabled")
    get_settings.cache_clear()
    req.state.auth_db_user_id = _ACTOR
    assert helper(req) is None  # type: ignore[arg-type]
    get_settings.cache_clear()


# ---------- recorder 语义 ----------


def test_recorder_before_null_after_actor_and_noop():
    """用途：补账 before 固定 NULL；真实 after 记 actor；no-op 不改 actor。"""
    before = _variant("b0")
    after = _variant("a1")
    project_id = f"proj_{secrets.token_hex(4)}"
    _ensure_project(project_id)

    db = SessionLocal()
    try:
        # 确保 workspace 存在（create_all 后 conftest 已 seed）
        result = record_editor_state_transition(
            db,
            _WS,
            project_id,
            before_state=before,
            after_state=after,
            source_kind="browser_put",
            actor_user_id=_ACTOR,
        )
        assert result["added_count"] == 2
        db.commit()
    finally:
        db.close()

    rows = _db_rev_rows(project_id)
    assert len(rows) == 2
    # 最新 = after，带 actor；更旧 = before，actor 固定 NULL
    assert rows[0].state_version == after["stateVersion"]
    assert rows[0].actor_user_id == _ACTOR
    assert rows[1].state_version == before["stateVersion"]
    assert rows[1].actor_user_id is None

    # no-op：before==after 最新版本，不得新增有 actor 的行
    db = SessionLocal()
    try:
        result2 = record_editor_state_transition(
            db,
            _WS,
            project_id,
            before_state=after,
            after_state=after,
            source_kind="browser_put",
            actor_user_id=_ACTOR,
        )
        assert result2["added_count"] == 0
        db.commit()
    finally:
        db.close()

    rows2 = _db_rev_rows(project_id)
    assert len(rows2) == 2
    assert rows2[0].actor_user_id == _ACTOR
    assert rows2[1].actor_user_id is None


def test_recorder_invalid_actor_raises_before_insert():
    """用途：非法 actor 在任何 revision 插入前失败。"""
    before = _variant("ib")
    after = _variant("ia")
    project_id = f"proj_{secrets.token_hex(4)}"
    _ensure_project(project_id)
    db = SessionLocal()
    try:
        with pytest.raises(EditorStateRevisionError):
            record_editor_state_transition(
                db,
                _WS,
                project_id,
                before_state=before,
                after_state=after,
                source_kind="browser_put",
                actor_user_id="  spaced  ",
            )
        db.rollback()
        assert _db_rev_rows(project_id) == []
    finally:
        db.close()


def test_recorder_disabled_null_actor_ok():
    """用途：actor=None（disabled）仍可写 after 行，值为 NULL。"""
    before = _variant("dn0")
    after = _variant("dn1")
    project_id = f"proj_{secrets.token_hex(4)}"
    _ensure_project(project_id)
    db = SessionLocal()
    try:
        record_editor_state_transition(
            db,
            _WS,
            project_id,
            before_state=before,
            after_state=after,
            source_kind="task",
            actor_user_id=None,
        )
        db.commit()
    finally:
        db.close()
    rows = _db_rev_rows(project_id)
    assert len(rows) == 2
    assert rows[0].actor_user_id is None
    assert rows[1].actor_user_id is None


# ---------- HTTP：disabled browser_put ----------


def test_disabled_browser_put_actor_null(client: TestClient):
    """用途：AUTH_MODE=disabled 时成功 browser_put 的 after 行 actor 为 NULL。"""
    pid = _create_project(client, name="disabled-put")
    # 首写
    r1 = client.put(
        f"/api/projects/{pid}/editor-state",
        json={"parsedMarkdown": "p13d1-disabled-1"},
    )
    assert r1.status_code == 200, r1.text
    sv1 = _assert_sv(r1.json()["stateVersion"])
    r2 = client.put(
        f"/api/projects/{pid}/editor-state",
        json={
            "parsedMarkdown": "p13d1-disabled-2",
            "expectedStateVersion": sv1,
        },
    )
    assert r2.status_code == 200, r2.text
    rows = _db_rev_rows(pid)
    # 首写补账 before+after，第二次真实变更再追加 after，精确共三条。
    assert len(rows) == 3
    # disabled 下三条均必须为 NULL actor。
    assert rows[0].actor_user_id is None
    assert rows[1].actor_user_id is None
    assert rows[2].actor_user_id is None


def test_disabled_client_injection_cannot_set_actor(client: TestClient):
    """用途：body/query/header 投稿 actor 不得写入账本。"""
    pid = _create_project(client, name="inject-put")
    r = client.put(
        f"/api/projects/{pid}/editor-state",
        params={"actorUserId": _FAKE, "actor_user_id": _FAKE},
        headers={
            "X-Actor-User-Id": _FAKE,
            "X-User-Id": _FAKE,
        },
        json={
            "parsedMarkdown": "inject-body",
            "actorUserId": _FAKE,
            "actor_user_id": _FAKE,
            "actor": _FAKE,
        },
    )
    assert r.status_code == 200, r.text
    blob = r.text
    assert _FAKE not in blob
    rows = _db_rev_rows(pid)
    assert rows
    for row in rows:
        assert row.actor_user_id is None
        assert row.actor_user_id != _FAKE


# ---------- HTTP：required browser_put / task ----------


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
    """用途：bootstrap 本地管理员并登录；返回 (user_id, csrf)。"""
    db = SessionLocal()
    try:
        principal = auth_service.bootstrap_local_admin(
            db,
            get_settings(),
            username=_TEST_USERNAME,
            password=_TEST_PASSWORD,
            role=auth_service.ROLE_BID_WRITER,
        )
        user_id = principal.user_id
    finally:
        db.close()
    login = client.post(
        "/api/auth/login",
        json={"username": _TEST_USERNAME, "password": _TEST_PASSWORD},
    )
    assert login.status_code == 200, login.text
    csrf = login.json()["csrfToken"]
    return user_id, csrf


def _assert_json_has_no_actor_fields(payload: object) -> None:
    """用途：递归键集合证明响应无内部 actor 泄漏。
    P13-D2 唯一放行精确键 currentRevisionActorUsername；禁止前缀/后缀/大小写近似键。
    """
    banned = {
        "actoruserid",
        "actor_user_id",
        "currentrevisionactor",
        "actor",
        "actors",
        "actorid",
        "actor_id",
    }
    allowed_exact = {"currentRevisionActorUsername"}
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert isinstance(key, str), key
            if key in allowed_exact:
                _assert_json_has_no_actor_fields(value)
                continue
            assert key.lower() not in banned, f"响应泄漏 actor 键: {key}"
            assert "actor" not in key.lower(), f"响应泄漏 actor 相关键: {key}"
            _assert_json_has_no_actor_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_json_has_no_actor_fields(item)


def test_required_browser_put_records_actor(required_client: TestClient):
    """用途：required 模式 browser_put 的 after 行精确等于登录用户。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    # 创建项目
    res = required_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "req-put", "mode": "technical"},
    )
    assert res.status_code in (200, 201), res.text
    pid = res.json().get("id") or res.json().get("projectId")

    r1 = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": "req-body-1"},
    )
    assert r1.status_code == 200, r1.text
    # 解析 JSON 键集合证明无内部 actor 泄漏（禁止恒真 or True / 宽松字符串）
    body = r1.json()
    assert isinstance(body, dict)
    _assert_json_has_no_actor_fields(body)
    top_keys = set(body.keys())
    assert "actorUserId" not in top_keys
    assert "actor_user_id" not in top_keys
    assert "currentRevisionActor" not in top_keys
    # P13-D2：唯一合法公开键；required browser PUT 后须精确等于当前活动同工作区用户名
    assert "currentRevisionActorUsername" in top_keys
    assert body["currentRevisionActorUsername"] == _TEST_USERNAME

    rows = _db_rev_rows(pid)
    assert rows, "应写入修订"
    # 最新 after 必须是本次 actor；若有补账 before 则为 NULL
    assert rows[0].actor_user_id == user_id
    if len(rows) > 1:
        assert rows[1].actor_user_id is None


def test_task_create_persists_actor_and_no_api_leak(required_client: TestClient):
    """用途：创建任务落库 actor；REST/列表不泄漏；payload 投稿无效。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    res = required_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "task-actor", "mode": "technical"},
    )
    assert res.status_code in (200, 201), res.text
    pid = res.json().get("id") or res.json().get("projectId")

    # 不执行 worker：仅创建 pending（enqueue 会启线程；用 service 直写更稳）
    from app.services import task_service

    db = SessionLocal()
    try:
        # 模拟路由从 request 取到的 actor
        task = task_service.create_task_record(
            db,
            _WS,
            pid,
            task_type="export",  # 非 writer 也可落 actor；不触发 editor 写
            payload={"actorUserId": _FAKE, "actor_user_id": _FAKE},
            actor_user_id=user_id,
        )
        tid = task.id
        # 库内精确
        row = db.get(ProjectTaskRow, tid)
        assert row is not None
        assert row.actor_user_id == user_id
        # 公开 dict 不泄漏
        d = task_service.task_to_dict(row)
        blob = json.dumps(d, ensure_ascii=False)
        assert "actor" not in blob.lower()
        assert user_id not in blob
        assert _FAKE not in blob
    finally:
        db.close()

    # REST 列表/详情不泄漏
    listed = required_client.get(f"/api/projects/{pid}/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    assert "actor" not in listed.text.lower()
    assert user_id not in listed.text
    got = required_client.get(f"/api/projects/{pid}/tasks/{tid}", headers=headers)
    assert got.status_code == 200, got.text
    assert "actor" not in got.text.lower()
    assert user_id not in got.text


def test_task_writer_uses_row_actor_not_request(required_client: TestClient, monkeypatch):
    """用途：真实任务行 + 独立 Session 的 _bg_worker 从重载 actor 写修订。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    res = required_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "task-writer", "mode": "technical"},
    )
    assert res.status_code in (200, 201), res.text
    pid = res.json().get("id") or res.json().get("projectId")

    # 先 seed 解析文本，供 analyze writer 读取（不依赖上传文件）
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": "before-task-writer-body"},
    )
    assert put.status_code == 200, put.text
    pre_sv = _assert_sv(put.json()["stateVersion"])

    from app.services import llm_service, task_service
    from app.services.llm_service import ChatResult

    # 窄 patch LLM，避免外网；真实 _run_analyze → _upsert_editor_state_for_task 保留
    def _fake_chat(db, workspace_id, *, messages, temperature=0.4, timeout_sec=120.0):  # noqa: ARG001
        return ChatResult(
            content=(
                '{"overview":"worker-row-actor",'
                '"techRequirements":["t1"],'
                '"rejectionRisks":["r1"],'
                '"scoringPoints":[{"name":"s1","weight":"10%"}]}'
            ),
            model="mock-p13d1",
        )

    monkeypatch.setattr(llm_service, "chat_completion", _fake_chat)
    monkeypatch.setattr(
        "app.services.llm_service.chat_completion", _fake_chat, raising=False
    )

    # 创建 writer 任务行并带 actor；随后关闭 Session，模拟 Request 已结束
    create_db = SessionLocal()
    try:
        task = task_service.create_task_record(
            create_db,
            _WS,
            pid,
            task_type="analyze",
            payload={"actorUserId": _FAKE, "actor_user_id": _FAKE},
            actor_user_id=user_id,
        )
        tid = task.id
        assert task.actor_user_id == user_id
        assert task.status == "pending"
    finally:
        create_db.close()

    # 独立 Session 的后台 worker：无 Request，必须从重载任务行读 actor
    task_service._bg_worker(tid, _WS)

    verify_db = SessionLocal()
    try:
        reloaded = verify_db.get(ProjectTaskRow, tid)
        assert reloaded is not None
        assert reloaded.status == "success", (reloaded.status, reloaded.error, reloaded.message)
        assert reloaded.actor_user_id == user_id
        assert reloaded.actor_user_id != _FAKE
    finally:
        verify_db.close()

    rows = _db_rev_rows(pid)
    assert rows
    assert rows[0].source_kind == "task"
    assert rows[0].actor_user_id == user_id
    assert rows[0].state_version != pre_sv


# ---------- 服务层：九类命名参数传播（非 HTTP 全矩阵） ----------


def test_nine_source_kinds_accept_named_actor():
    """用途：九类 source_kind 均接受命名 actor_user_id；after 精确。"""
    kinds = sorted(editor_state_revision_service.REVISION_SOURCE_KINDS)
    assert len(kinds) == 9
    for kind in kinds:
        before = _variant(f"{kind}_b")
        after = _variant(f"{kind}_a")
        project_id = f"p_{kind}_{secrets.token_hex(3)}"
        _ensure_project(project_id, name=f"nine-{kind}")
        db = SessionLocal()
        try:
            record_editor_state_transition(
                db,
                _WS,
                project_id,
                before_state=before,
                after_state=after,
                source_kind=kind,
                actor_user_id=_ACTOR,
            )
            db.commit()
        finally:
            db.close()
        rows = _db_rev_rows(project_id)
        assert rows[0].actor_user_id == _ACTOR
        assert rows[0].source_kind == kind
        assert rows[1].actor_user_id is None


def test_upsert_editor_state_propagates_actor():
    """用途：upsert 命名 actor 进入 browser_put/task 等修订 after。"""
    pid = f"proj_up_{secrets.token_hex(4)}"
    _ensure_project(pid, name="upsert-actor")

    db = SessionLocal()
    try:
        s1 = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            parsed_markdown="u1",
            revision_source_kind="browser_put",
            actor_user_id=_ACTOR,
        )
        s2 = editor_state_service.upsert_editor_state(
            db,
            _WS,
            pid,
            parsed_markdown="u2",
            expected_state_version=s1["stateVersion"],
            revision_source_kind="browser_put",
            actor_user_id=_ACTOR,
        )
        assert s2["stateVersion"] != s1["stateVersion"]
    finally:
        db.close()
    rows = _db_rev_rows(pid)
    assert rows
    assert rows[0].actor_user_id == _ACTOR


def test_personal_callback_task_and_revision_share_actor(required_client: TestClient):
    """用途：个人 parse-callback 修订与新建 task 行同一 actor。"""
    user_id, csrf = _bootstrap_and_login(required_client)
    headers = {"X-CSRF-Token": csrf}
    res = required_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "cb-actor", "mode": "technical"},
    )
    assert res.status_code in (200, 201), res.text
    pid = res.json().get("id") or res.json().get("projectId")
    put = required_client.put(
        f"/api/projects/{pid}/editor-state",
        headers=headers,
        json={"parsedMarkdown": "pre-callback"},
    )
    assert put.status_code == 200, put.text
    sv = _assert_sv(put.json()["stateVersion"])

    cb = required_client.post(
        f"/api/projects/{pid}/parse-callback",
        headers=headers,
        json={
            "markdown": "callback-md-body",
            "source": "mineru",
            "filename": "a.pdf",
            "expectedStateVersion": sv,
            "actorUserId": _FAKE,
            "actor_user_id": _FAKE,
        },
    )
    assert cb.status_code == 200, cb.text
    assert "actor" not in cb.text.lower()
    assert _FAKE not in cb.text

    rows = _db_rev_rows(pid)
    assert rows
    assert rows[0].source_kind == "callback"
    assert rows[0].actor_user_id == user_id

    db = SessionLocal()
    try:
        tasks = (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == pid)
            .order_by(ProjectTaskRow.created_at.desc())
            .all()
        )
        assert tasks
        assert tasks[0].type == "parse"
        assert tasks[0].actor_user_id == user_id
        d = json.dumps(
            {
                "id": tasks[0].id,
                "status": tasks[0].status,
            },
            ensure_ascii=False,
        )
        # 公开路径不在此；库内 actor 存在即可
        assert tasks[0].actor_user_id == user_id
        assert _FAKE not in d
    finally:
        db.close()


def test_recorder_empty_ledger_before_equals_after_only_null_backfill():
    """用途：空账本 before==after 只允许补一条 actor=NULL，绝不能写本次 actor。"""
    same = _variant("same_empty")
    project_id = f"proj_{secrets.token_hex(4)}"
    _ensure_project(project_id, name="empty-same")
    assert _db_rev_rows(project_id) == []

    db = SessionLocal()
    try:
        result = record_editor_state_transition(
            db,
            _WS,
            project_id,
            before_state=same,
            after_state=same,
            source_kind="browser_put",
            actor_user_id=_ACTOR,
        )
        # 仅补账 before；after 与 before 同版本不再追加
        assert result["added_count"] == 1
        assert result["final_state_version"] == same["stateVersion"]
        db.commit()
    finally:
        db.close()

    rows = _db_rev_rows(project_id)
    assert len(rows) == 1
    assert rows[0].state_version == same["stateVersion"]
    assert rows[0].actor_user_id is None
    assert rows[0].actor_user_id != _ACTOR


def test_actor_migration_second_alter_failure_rolls_back_first(tmp_path: Path):
    """用途：第二个 actor ALTER 失败时，ensure 抛错且第一表 actor 列不得残留。"""
    db_path = tmp_path / "p13d1_mig_fail.db"
    old_engine = create_engine(f"sqlite:///{db_path}")
    with old_engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE editor_state_revisions (
              id VARCHAR(64) PRIMARY KEY,
              workspace_id VARCHAR(64) NOT NULL,
              project_id VARCHAR(64) NOT NULL,
              snapshot_json TEXT NOT NULL,
              state_version VARCHAR(64) NOT NULL,
              snapshot_bytes INTEGER NOT NULL,
              source_kind VARCHAR(64) NOT NULL,
              display_name VARCHAR(160),
              is_pinned INTEGER NOT NULL DEFAULT 0,
              created_at DATETIME NOT NULL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE project_tasks (
              id VARCHAR(64) PRIMARY KEY,
              project_id VARCHAR(64) NOT NULL,
              type VARCHAR(64) NOT NULL,
              status VARCHAR(32) NOT NULL,
              progress INTEGER NOT NULL DEFAULT 0,
              message VARCHAR(1000) NOT NULL DEFAULT '',
              payload_json TEXT,
              result_json TEXT,
              error TEXT,
              created_at DATETIME NOT NULL,
              updated_at DATETIME NOT NULL
            )
            """
        )
        assert "actor_user_id" not in _table_cols(conn, "editor_state_revisions")
        assert "actor_user_id" not in _table_cols(conn, "project_tasks")

    from sqlalchemy.engine import Connection as SAConnection

    _INJECT = "p13d1_injected_second_actor_alter_failure"
    _orig = SAConnection.exec_driver_sql

    def _injecting_exec_driver_sql(self, statement, *args, **kwargs):
        sql = statement if isinstance(statement, str) else str(statement)
        compact = " ".join(sql.split()).upper()
        # 第二表 actor 迁移：第一表 ALTER 已成功后注入失败，验证外层事务回滚
        if (
            "ALTER TABLE PROJECT_TASKS" in compact
            and "ADD COLUMN" in compact
            and "ACTOR_USER_ID" in compact
        ):
            raise RuntimeError(_INJECT)
        return _orig(self, statement, *args, **kwargs)

    try:
        SAConnection.exec_driver_sql = _injecting_exec_driver_sql  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match=re.escape(_INJECT)):
            ensure_schema_columns(old_engine)
    finally:
        SAConnection.exec_driver_sql = _orig  # type: ignore[method-assign]

    # 新连接精确证明：两表均不得残留 actor_user_id
    with old_engine.connect() as conn:
        rev_cols = _table_cols(conn, "editor_state_revisions")
        task_cols = _table_cols(conn, "project_tasks")
        assert "actor_user_id" not in rev_cols, sorted(rev_cols)
        assert "actor_user_id" not in task_cols, sorted(task_cols)
    old_engine.dispose()


# ---------- AST：route→service→recorder / ticket 完整命名 actor 链 ----------


def _ast_parse_rel(rel: str) -> ast.Module:
    path = _BACKEND_ROOT / rel
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _ast_func(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body if isinstance(tree, ast.Module) else ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"函数未找到: {name}")


def _call_func_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _call_has_named_actor_kw(node: ast.Call) -> bool:
    """用途：精确要求关键字 actor_user_id=...，禁止位置参数冒充。"""
    for kw in node.keywords:
        if kw.arg == "actor_user_id":
            return True
    return False


def _calls_named(fn: ast.AST, name: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and _call_func_name(n) == name
    ]


def test_ast_actor_propagation_chains_exact():
    """
    用途：AST 精确证明 content_fuse apply/consume、checkpoint/revision restore、
      local_parser 票据签发→task+recorder 均携带命名 actor_user_id；禁止签名-only/恒真。
    """
    # --- content_fuse apply/consume: route → service → recorder ---
    fuse_api = _ast_parse_rel("app/api/content_fuse_applications.py")
    fuse_svc = _ast_parse_rel("app/services/content_fuse_application_service.py")

    apply_route = _ast_func(fuse_api, "create_content_fuse_application")
    # 路由函数名以文件内定义为准；若 create 不存在则用 apply 路由真实名
    apply_route_calls = _calls_named(apply_route, "apply_content_fuse_application")
    if not apply_route_calls:
        # 兼容路由函数可能命名为 apply_content_fuse_application
        apply_route = _ast_func(fuse_api, "apply_content_fuse_application")
        apply_route_calls = _calls_named(apply_route, "apply_content_fuse_application")
    assert len(apply_route_calls) == 1, "apply 路由应唯一调用 service"
    assert _call_has_named_actor_kw(apply_route_calls[0]), "apply 路由必须命名传 actor_user_id"

    consume_route = _ast_func(fuse_api, "consume_content_fuse_application")
    consume_route_calls = _calls_named(consume_route, "consume_content_fuse_application")
    assert len(consume_route_calls) == 1
    assert _call_has_named_actor_kw(consume_route_calls[0])

    apply_svc = _ast_func(fuse_svc, "apply_content_fuse_application")
    apply_records = _calls_named(apply_svc, "record_editor_state_transition")
    assert len(apply_records) == 1
    assert _call_has_named_actor_kw(apply_records[0])

    consume_svc = _ast_func(fuse_svc, "consume_content_fuse_application")
    consume_records = _calls_named(consume_svc, "record_editor_state_transition")
    assert len(consume_records) == 1
    assert _call_has_named_actor_kw(consume_records[0])

    # --- checkpoint restore: route → service → stage → recorder ---
    cp_api = _ast_parse_rel("app/api/editor_state_checkpoints.py")
    cp_svc = _ast_parse_rel("app/services/editor_state_checkpoint_service.py")
    # 路由函数名可能较长，扫描含 restore_editor_state_checkpoint 的调用方
    restore_route_fn = None
    for node in ast.walk(cp_api):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = _calls_named(node, "restore_editor_state_checkpoint")
        if calls:
            restore_route_fn = node
            assert len(calls) == 1
            assert _call_has_named_actor_kw(calls[0]), node.name
            break
    assert restore_route_fn is not None, "checkpoint restore 路由未找到 service 调用"

    restore_cp_svc = _ast_func(cp_svc, "restore_editor_state_checkpoint")
    stage_calls = _calls_named(restore_cp_svc, "stage_locked_canonical_restore")
    assert len(stage_calls) == 1
    assert _call_has_named_actor_kw(stage_calls[0])
    # 编排禁止直接 recorder
    assert _calls_named(restore_cp_svc, "record_editor_state_transition") == []

    stage_fn = _ast_func(cp_svc, "stage_locked_canonical_restore")
    stage_records = _calls_named(stage_fn, "record_editor_state_transition")
    assert len(stage_records) == 1
    assert _call_has_named_actor_kw(stage_records[0])

    # --- revision restore: route → service → stage → recorder ---
    rev_api = _ast_parse_rel("app/api/editor_state_revisions.py")
    rev_svc = _ast_parse_rel("app/services/editor_state_revision_restore_service.py")
    rev_route_fn = None
    for node in ast.walk(rev_api):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = _calls_named(node, "restore_editor_state_revision")
        if calls:
            rev_route_fn = node
            assert len(calls) == 1
            assert _call_has_named_actor_kw(calls[0]), node.name
            break
    assert rev_route_fn is not None, "revision restore 路由未找到 service 调用"

    restore_rev_svc = _ast_func(rev_svc, "restore_editor_state_revision")
    rev_stage_calls = _calls_named(restore_rev_svc, "stage_locked_canonical_restore")
    assert len(rev_stage_calls) == 1
    assert _call_has_named_actor_kw(rev_stage_calls[0])

    # --- local_parser: ticket issuer → apply 路径 task+recorder ---
    ticket_api = _ast_parse_rel("app/api/parse_callback.py")
    ticket_svc = _ast_parse_rel("app/services/local_parser_ticket_service.py")

    issue_route = _ast_func(ticket_api, "issue_parse_callback_ticket")
    issue_calls = _calls_named(issue_route, "issue_callback_ticket")
    assert len(issue_calls) == 1
    # 签发链：issued_by_user_id 命名传入（票据侧可信身份）
    issue_kws = {kw.arg for kw in issue_calls[0].keywords if kw.arg}
    assert "issued_by_user_id" in issue_kws

    issue_svc = _ast_func(ticket_svc, "issue_callback_ticket")
    # 签发服务参数含 issued_by_user_id
    issue_args = {a.arg for a in issue_svc.args.args + issue_svc.args.kwonlyargs}
    assert "issued_by_user_id" in issue_args

    # 回调应用：task 行与 recorder 均命名 actor_user_id
    apply_fn = None
    for cand in (
        "_apply_ticket_callback_locked",
        "apply_one_time_callback",
        "_write_callback_result",
    ):
        try:
            apply_fn = _ast_func(ticket_svc, cand)
            break
        except AssertionError:
            continue
    # 更稳：在含 record_editor_state_transition 的函数上断言
    recorder_hosts: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(ticket_svc):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        recs = _calls_named(node, "record_editor_state_transition")
        if recs:
            recorder_hosts.append(node)
            assert any(_call_has_named_actor_kw(c) for c in recs), node.name
    assert recorder_hosts, "local_parser 服务必须有 recorder 调用点"

    # ProjectTaskRow(...) 构造须含 keyword actor_user_id
    task_row_calls: list[ast.Call] = []
    for node in ast.walk(ticket_svc):
        if isinstance(node, ast.Call) and _call_func_name(node) == "ProjectTaskRow":
            task_row_calls.append(node)
    assert task_row_calls, "local_parser 须构造 ProjectTaskRow"
    assert any(_call_has_named_actor_kw(c) for c in task_row_calls), (
        "local_parser task 行必须命名 actor_user_id"
    )
