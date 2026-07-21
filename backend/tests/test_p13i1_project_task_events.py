"""
模块：P13-I1 项目任务事件游标后端专项测试
用途：failure-first 验收独立 project_task_events 表、真实任务写链同事务事件、
  200 条裁剪、required strict bid_writer 游标 GET、stale 409 与隐私门。
对接：GET /api/projects/{projectId}/task-events；
  project_task_event_service；task_service 写点钩子。
二次开发：禁止预插入事件表冒充写链成功；禁止宽状态/恒真/绕过真实任务服务；
  禁止把 project_tasks 当事件日志。
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.main import app
from app.models.entities import (
    Project,
    ProjectTaskRow,
    Workspace,
    WorkspaceMemberRow,
    utc_now,
)
from app.services import auth_service, editor_state_service, project_service, task_service

_WS = "ws_local"
_PTE_RE = re.compile(r"^pte_[0-9a-f]{32}$")
_TASK_RE = re.compile(r"^task_[0-9a-f]+$")
_OCCURRED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_ITEM_KEYS = frozenset(
    {"eventId", "taskId", "taskType", "status", "progress", "occurredAt"}
)
_LIST_KEYS = frozenset({"items", "nextCursor", "hasMore"})
_STATUSES = frozenset({"pending", "running", "success", "failed", "cancelled"})
_TEST_USERNAME = "admin_p13i1"
_TEST_PASSWORD = "P13i1-Test-Pass-9!"
_SECRET_MSG = "SECRET_P13I1_MSG_MUST_NOT_LEAK"
_SECRET_ERR = "SECRET_P13I1_ERR_PATH_C:/leak/secret.bin"
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _SECRET_MSG,
    _SECRET_ERR,
    "password_hash",
    "password_salt",
    "token_digest",
    "csrf_digest",
    "result_json",
    "payload_json",
    "actor_user_id",
    "actorUserId",
    "clientId",
    "client_id",
    "workspace_id",
    "workspaceId",
)


# ---------- 构造工具 ----------


def _try_import_event_row():
    """用途：failure-first 下实体可能尚不存在。"""
    try:
        from app.models.entities import ProjectTaskEventRow  # type: ignore

        return ProjectTaskEventRow
    except Exception:
        return None


def _db_event_count(project_id: str, workspace_id: str | None = None) -> int:
    EventRow = _try_import_event_row()
    if EventRow is None:
        insp = inspect(engine)
        if "project_task_events" not in insp.get_table_names():
            return 0
        db = SessionLocal()
        try:
            sql = "SELECT COUNT(*) FROM project_task_events WHERE project_id = :p"
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


def _db_task_count(project_id: str) -> int:
    db = SessionLocal()
    try:
        return (
            db.query(ProjectTaskRow)
            .filter(ProjectTaskRow.project_id == project_id)
            .count()
        )
    finally:
        db.close()


def _event_row_exists(event_id: str) -> bool:
    """用途：DB 精确确认事件行是否仍存在（裁剪证据）。"""
    EventRow = _try_import_event_row()
    if EventRow is None:
        return False
    db = SessionLocal()
    try:
        return db.get(EventRow, event_id) is not None
    finally:
        db.close()


def _seed_second_workspace_project(
    *,
    workspace_id: str,
    workspace_name: str,
    owner_user_id: str,
    member_user_id: str,
    project_name: str = "跨空间项目",
) -> str:
    """
    用途：创建真实第二 workspace 及其项目；成员写入 bid_writer。
    二次开发：禁止用同 workspace 第二项目冒充跨空间。
    """
    db = SessionLocal()
    try:
        if db.get(Workspace, workspace_id) is None:
            db.add(
                Workspace(
                    id=workspace_id,
                    name=workspace_name,
                    owner_user_id=owner_user_id,
                )
            )
        existing = (
            db.query(WorkspaceMemberRow)
            .filter(
                WorkspaceMemberRow.workspace_id == workspace_id,
                WorkspaceMemberRow.user_id == member_user_id,
            )
            .one_or_none()
        )
        if existing is None:
            db.add(
                WorkspaceMemberRow(
                    id=f"wm_{secrets.token_hex(8)}",
                    workspace_id=workspace_id,
                    user_id=member_user_id,
                    role=auth_service.ROLE_BID_WRITER,
                    is_owner=(member_user_id == owner_user_id),
                    is_active=True,
                )
            )
        project = project_service.create_project(
            db,
            workspace_id,
            name=project_name,
        )
        db.commit()
        return project.id
    finally:
        db.close()


def _ensure_project(project_id: str, name: str = "p13i1-svc") -> None:
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


def _create_task(
    project_id: str,
    *,
    task_type: str = "export",
    workspace_id: str = _WS,
    payload: dict | None = None,
    actor_user_id: str | None = None,
) -> ProjectTaskRow:
    """用途：经真实 create_task_record 写链创建任务（非直接插事件表）。"""
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            workspace_id,
            project_id,
            task_type=task_type,
            payload=payload or {},
            actor_user_id=actor_user_id,
        )
        # 脱离 Session 后仍可用字段
        db.expunge(task)
        return task
    finally:
        db.close()


def _create_and_finish_export(project_id: str) -> ProjectTaskRow:
    """用途：创建 export 并立即成功终态，释放同类型防重入以便连续造事件。"""
    task = _create_task(project_id, task_type="export")
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task.id)
        assert row is not None
        task_service._set_task(
            db, row, status="success", progress=100, message="done"
        )
    finally:
        db.close()
    return task


def _events_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/task-events"


def _assert_no_secrets(payload: object) -> None:
    text_blob = (
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    )
    low = text_blob.lower()
    for marker in _SENSITIVE_MARKERS:
        assert marker not in text_blob, f"敏感标记泄漏: {marker}"
        assert marker.lower() not in low, f"敏感标记泄漏: {marker}"
    for banned in (
        "result_json",
        "payload_json",
        "actoruserid",
        "actor_user_id",
        "clientid",
        "password",
        "token_digest",
        "csrf_digest",
        "traceback",
        "sqlalchemy",
        "operationalerror",
    ):
        assert banned not in low.replace("-", "_"), f"敏感/内部标记泄漏: {banned}"


def _assert_item_shape(item: dict[str, Any], *, task_id: str | None = None) -> None:
    assert set(item.keys()) == _ITEM_KEYS
    assert _PTE_RE.fullmatch(item["eventId"]), item["eventId"]
    assert isinstance(item["taskId"], str) and item["taskId"]
    if task_id is not None:
        assert item["taskId"] == task_id
    assert isinstance(item["taskType"], str) and item["taskType"]
    assert item["status"] in _STATUSES
    assert isinstance(item["progress"], int)
    assert 0 <= item["progress"] <= 100
    assert _OCCURRED_RE.fullmatch(item["occurredAt"]), item["occurredAt"]
    _assert_no_secrets(item)


def _assert_list_shape(body: dict[str, Any]) -> None:
    assert set(body.keys()) == _LIST_KEYS
    assert isinstance(body["items"], list)
    assert isinstance(body["hasMore"], bool)
    if body["nextCursor"] is not None:
        assert isinstance(body["nextCursor"], str)
        assert _PTE_RE.fullmatch(body["nextCursor"]), body["nextCursor"]
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
    client: TestClient, csrf: str | None = None, name: str = "P13I1项目"
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


def test_orm_event_table_columns_indexes_and_no_sensitive(disabled_client):
    """用途：事件表精确字段、复合索引；禁止 message/error/result/payload/actor。"""
    insp = inspect(engine)
    assert "project_task_events" in insp.get_table_names()
    cols = {c["name"]: c for c in insp.get_columns("project_task_events")}
    assert set(cols.keys()) == {
        "id",
        "workspace_id",
        "project_id",
        "task_id",
        "task_type",
        "status",
        "progress",
        "occurred_at",
    }
    for banned in (
        "message",
        "error",
        "result_json",
        "payload_json",
        "actor_user_id",
        "client_id",
        "client_digest",
        "display_name",
        "snapshot_json",
    ):
        assert banned not in cols
    fks = insp.get_foreign_keys("project_task_events")
    assert any(
        "workspace_id" in f["constrained_columns"]
        and f["referred_table"] == "workspaces"
        for f in fks
    )
    assert any(
        "project_id" in f["constrained_columns"] and f["referred_table"] == "projects"
        for f in fks
    )
    indexes = insp.get_indexes("project_task_events")
    col_sets = [tuple(ix.get("column_names") or []) for ix in indexes]
    assert ("workspace_id", "project_id", "occurred_at", "id") in col_sets or any(
        list(ix.get("column_names") or [])[:4]
        == ["workspace_id", "project_id", "occurred_at", "id"]
        for ix in indexes
    )


# ---------- 写链：真实任务状态事件 ----------


def test_create_task_record_writes_pending_event(disabled_client):
    """用途：create_task_record 真实写链产生一条 pending 事件。"""
    pid = _create_project_http(disabled_client, name="i1-create")
    n0 = _db_event_count(pid, _WS)
    task = _create_task(pid, task_type="export")
    assert task.status == "pending"
    assert task.progress == 0
    n1 = _db_event_count(pid, _WS)
    assert n1 == n0 + 1
    rows = _db_event_rows(pid, _WS)
    last = rows[-1]
    assert _PTE_RE.fullmatch(last.id)
    assert last.task_id == task.id
    assert last.task_type == "export"
    assert last.status == "pending"
    assert last.progress == 0
    assert last.workspace_id == _WS
    assert last.project_id == pid
    # 敏感字段不得出现在事件行属性
    assert not hasattr(last, "message") or getattr(last, "message", None) is None
    assert not hasattr(last, "error")


def test_progress_and_success_write_chain_events(disabled_client):
    """用途：进度变化与成功终态各记事件；同状态同进度不重复。"""
    pid = _create_project_http(disabled_client, name="i1-progress")
    task = _create_task(pid, task_type="export")
    tid = task.id
    n0 = _db_event_count(pid, _WS)

    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, tid)
        assert row is not None
        task_service._set_task(
            db, row, status="running", progress=5, message=_SECRET_MSG
        )
        task_service._set_task(db, row, progress=50, message="半程…")
        # 同 status/progress 仅 message 变化：不记事件
        task_service._set_task(db, row, message="半程心跳…")
        task_service._set_task(
            db,
            row,
            status="success",
            progress=100,
            message="完成",
            result={"ok": True, "secret": _SECRET_MSG},
        )
        # 同终态再写 message/error 不记事件
        task_service._set_task(
            db,
            row,
            status="success",
            progress=100,
            message="完成再次",
            force=True,
        )
    finally:
        db.close()

    # create(pending) 已在 n0 内；本段期望：running/5、running/50、success/100 = +3
    n1 = _db_event_count(pid, _WS)
    assert n1 == n0 + 3
    rows = [r for r in _db_event_rows(pid, _WS) if r.task_id == tid]
    statuses = [(r.status, r.progress) for r in rows]
    assert statuses == [
        ("pending", 0),
        ("running", 5),
        ("running", 50),
        ("success", 100),
    ]
    for r in rows:
        assert not hasattr(r, "result_json") or getattr(r, "result_json", None) is None
        assert _PTE_RE.fullmatch(r.id)


def test_failed_and_cancel_and_stale_and_interrupt_events(disabled_client):
    """用途：失败、取消、版本冲突失败、进程中断真实写链各产预期事件。"""
    pid = _create_project_http(disabled_client, name="i1-fail-cancel")

    # 1) 普通失败
    t_fail = _create_task(pid, task_type="export")
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, t_fail.id)
        task_service._set_task(
            db,
            row,
            status="failed",
            progress=100,
            message="任务失败",
            error=_SECRET_ERR,
        )
    finally:
        db.close()
    fail_rows = [r for r in _db_event_rows(pid, _WS) if r.task_id == t_fail.id]
    assert [(r.status, r.progress) for r in fail_rows] == [
        ("pending", 0),
        ("failed", 100),
    ]

    # 2) 取消
    t_cancel = _create_task(pid, task_type="export")
    db = SessionLocal()
    try:
        task_service.cancel_task(db, _WS, pid, t_cancel.id)
    finally:
        db.close()
    cancel_rows = [r for r in _db_event_rows(pid, _WS) if r.task_id == t_cancel.id]
    assert [(r.status, r.progress) for r in cancel_rows] == [
        ("pending", 0),
        ("cancelled", 0),
    ]

    # 3) 取消后旧 worker 迟到提交不得追加非取消事件
    n_before_late = _db_event_count(pid, _WS)
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, t_cancel.id)
        assert row is not None
        with pytest.raises(task_service.TaskCancelled):
            task_service._set_task(
                db,
                row,
                status="success",
                progress=100,
                message="迟到成功",
                result={"x": 1},
            )
    finally:
        db.close()
    assert _db_event_count(pid, _WS) == n_before_late
    cancel_rows2 = [r for r in _db_event_rows(pid, _WS) if r.task_id == t_cancel.id]
    assert all(r.status == "cancelled" or r.status == "pending" for r in cancel_rows2)
    assert cancel_rows2[-1].status == "cancelled"

    # 4) 版本冲突失败（_fail_task_stale_version）
    t_stale = _create_task(pid, task_type="export")
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, t_stale.id)
        task_service._fail_task_stale_version(db, row)
    finally:
        db.close()
    stale_rows = [r for r in _db_event_rows(pid, _WS) if r.task_id == t_stale.id]
    assert [(r.status, r.progress) for r in stale_rows] == [
        ("pending", 0),
        ("failed", 100),
    ]

    # 5) 进程中断 fail_interrupted_tasks
    t_int = _create_task(pid, task_type="export")
    db = SessionLocal()
    try:
        # 推到 running 以便中断标记
        row = db.get(ProjectTaskRow, t_int.id)
        task_service._set_task(db, row, status="running", progress=20)
    finally:
        db.close()
    n_before_int = len([r for r in _db_event_rows(pid, _WS) if r.task_id == t_int.id])
    db = SessionLocal()
    try:
        n = task_service.fail_interrupted_tasks(db)
        assert n >= 1
    finally:
        db.close()
    int_rows = [r for r in _db_event_rows(pid, _WS) if r.task_id == t_int.id]
    assert n_before_int + 1 == len(int_rows)
    assert int_rows[-1].status == "failed"
    assert int_rows[-1].progress == 100


def test_failed_transaction_zero_event_and_task_residual(disabled_client, monkeypatch):
    """用途：事件 flush 失败时任务与事件均不残留（同事务）。"""
    pid = f"p_i1_tx_{secrets.token_hex(3)}"
    _ensure_project(pid)
    n0 = _db_event_count(pid, _WS)
    t0 = _db_task_count(pid)

    original_flush = Session.flush
    calls = {"n": 0}

    def _boom(self, *args, **kwargs):
        calls["n"] += 1
        # 任务 add 后的 flush 允许；事件插入后的 flush 失败
        if calls["n"] >= 2:
            raise RuntimeError("injected_task_event_flush_failure")
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", _boom)
    db = SessionLocal()
    try:
        with pytest.raises(Exception):
            task_service.create_task_record(
                db,
                _WS,
                pid,
                task_type="export",
                payload={},
            )
        db.rollback()
    finally:
        db.close()

    assert _db_event_count(pid, _WS) == n0
    assert _db_task_count(pid) == t0


def test_trim_keeps_latest_200_events_continuous(disabled_client):
    """用途：每项目最多 200 条；按 occurred_at DESC,id DESC 连续裁剪。"""
    pid = f"p_i1_trim_{secrets.token_hex(3)}"
    _ensure_project(pid)
    # 用 export 可重复创建（同类型进行中会挡；逐个成功释放）
    for i in range(205):
        task = _create_task(pid, task_type="export")
        db = SessionLocal()
        try:
            row = db.get(ProjectTaskRow, task.id)
            task_service._set_task(
                db,
                row,
                status="success",
                progress=100,
                message=f"done-{i}",
            )
        finally:
            db.close()
    rows = _db_event_rows(pid, _WS)
    assert len(rows) == 200
    for i in range(1, len(rows)):
        prev_r, cur_r = rows[i - 1], rows[i]
        t0 = prev_r.occurred_at
        t1 = cur_r.occurred_at
        if getattr(t0, "tzinfo", None) is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if getattr(t1, "tzinfo", None) is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        assert (t0, prev_r.id) <= (t1, cur_r.id)


def test_trim_does_not_touch_other_project(disabled_client):
    """用途：裁剪只影响本项目事件。"""
    p1 = f"p_i1_t1_{secrets.token_hex(3)}"
    p2 = f"p_i1_t2_{secrets.token_hex(3)}"
    _ensure_project(p1)
    _ensure_project(p2)
    t2 = _create_task(p2, task_type="export")
    n2 = _db_event_count(p2, _WS)
    assert n2 >= 1
    for i in range(205):
        task = _create_task(p1, task_type="export")
        db = SessionLocal()
        try:
            row = db.get(ProjectTaskRow, task.id)
            task_service._set_task(
                db, row, status="success", progress=100, message=f"x-{i}"
            )
        finally:
            db.close()
    assert _db_event_count(p1, _WS) == 200
    assert _db_event_count(p2, _WS) == n2
    assert any(r.task_id == t2.id for r in _db_event_rows(p2, _WS))


# ---------- 只读 API ----------


def test_required_empty_without_after_bootstrap_tip(required_client):
    """用途：无 after 不回放；无事件 tip=null；有事件返回 tip 并可增量。"""
    _user_id, csrf = _bootstrap_and_login(required_client)

    pid_empty = _create_project_http(required_client, csrf, name="i1-empty-none")
    res_empty = required_client.get(_events_url(pid_empty))
    assert res_empty.status_code == 200, res_empty.text
    _assert_no_store(res_empty)
    body_empty = res_empty.json()
    _assert_list_shape(body_empty)
    assert body_empty["items"] == []
    assert body_empty["nextCursor"] is None
    assert body_empty["hasMore"] is False

    pid = _create_project_http(required_client, csrf, name="i1-empty")
    task = _create_task(pid, task_type="export")
    assert _db_event_count(pid) >= 1
    res = required_client.get(_events_url(pid))
    assert res.status_code == 200, res.text
    _assert_no_store(res)
    body = res.json()
    _assert_list_shape(body)
    assert body["items"] == []
    assert body["hasMore"] is False
    tip = body["nextCursor"]
    assert isinstance(tip, str) and _PTE_RE.fullmatch(tip), tip

    # tip 后真实增量
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task.id)
        task_service._set_task(
            db, row, status="running", progress=10, message="go"
        )
    finally:
        db.close()
    res_inc = required_client.get(_events_url(pid), params={"after": tip})
    assert res_inc.status_code == 200, res_inc.text
    _assert_no_store(res_inc)
    body_inc = res_inc.json()
    _assert_list_shape(body_inc)
    assert body_inc["hasMore"] is False
    assert len(body_inc["items"]) == 1
    new_item = body_inc["items"][0]
    _assert_item_shape(new_item, task_id=task.id)
    assert new_item["status"] == "running"
    assert new_item["progress"] == 10
    assert new_item["eventId"] != tip


def test_required_cursor_read_limit_and_order(required_client):
    """用途：after 游标正序读取；limit 1/50；连续 nextCursor。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-cursor")
    # 每个任务 create+success 共 2 事件；5 个任务 = 10 事件
    tasks = [_create_and_finish_export(pid) for _ in range(5)]
    rows = _db_event_rows(pid)
    assert len(rows) >= 5
    first_id = rows[0].id
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
    assert body["items"][0]["eventId"] == rows[1].id
    assert body["items"][1]["eventId"] == rows[2].id
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
    # taskId 对应真实任务
    assert {it["taskId"] for it in body["items"] + body2["items"]}.issubset(
        {t.id for t in tasks} | {rows[0].task_id}
    )


def test_limit_bounds_1_and_50(required_client):
    """用途：limit 默认 50；1 合法；0/51/非整数 422。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-limit")
    for _ in range(3):
        _create_and_finish_export(pid)
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
    _assert_list_shape(ok50.json())
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
    pid = _create_project_http(required_client, csrf, name="i1-stale")
    pid2 = _create_project_http(required_client, csrf, name="i1-stale2")
    _create_and_finish_export(pid)
    _create_and_finish_export(pid2)
    other = _db_event_rows(pid2)[0].id
    forged = "pte_" + "a" * 32
    for cursor in (forged, other, "pte_" + "0" * 32):
        res = required_client.get(
            _events_url(pid), params={"after": cursor}
        )
        assert res.status_code == 409, res.text
        _assert_no_store(res)
        detail = res.json().get("detail") or res.json()
        if isinstance(detail, dict):
            assert detail.get("code") == "project_task_event_cursor_stale"
        assert cursor not in res.text
        _assert_no_secrets(res.text)


def test_invalid_after_format_422(required_client):
    """用途：非法 after 格式固定 422，不回显。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-badafter")
    leak = "pte_NOT_HEX_AND_SHOULD_NOT_ECHO_XXXX"
    for bad in (
        "ese_" + "a" * 32,
        "pte_" + "A" * 32,
        "pte_" + "a" * 31,
        "pte_" + "a" * 33,
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
    pid = _create_project_http(required_client, csrf, name="i1-q")
    res = required_client.get(f"{_events_url(pid)}?limit=1&unknown=1")
    assert res.status_code == 422, res.text
    _assert_no_store(res)
    assert "unknown" not in res.text
    res2 = required_client.get(f"{_events_url(pid)}?limit=1&limit=2")
    assert res2.status_code == 422, res2.text
    _assert_no_store(res2)
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
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-auth")
    _create_task(pid, task_type="export")

    required_client.cookies.clear()
    unauth = required_client.get(_events_url(pid))
    assert unauth.status_code == 401, unauth.text
    _assert_no_secrets(unauth.text)
    # 契约：AuthMiddleware 统一认证错误出口必须无条件 no-store，禁止条件豁免
    _assert_no_store(unauth)
    detail_u = unauth.json().get("detail") or unauth.json()
    assert isinstance(detail_u, dict)
    assert detail_u.get("code") == auth_service.CODE_AUTH_REQUIRED
    assert detail_u.get("message") == auth_service.MSG_AUTH_REQUIRED
    assert pid not in unauth.text

    _user_id, csrf = _bootstrap_and_login(required_client)
    for val in ("", "ws_other", _WS, " "):
        res = required_client.get(
            _events_url(pid), headers={"X-Workspace-Id": val}
        )
        assert res.status_code == 403, (val, res.text)
        _assert_no_store(res)
        _assert_no_secrets(res.text)

    fin_user = f"fin_p13i1_{secrets.token_hex(3)}"
    _create_member(
        required_client,
        csrf,
        username=fin_user,
        password="P13i1-Finance-Pass!",
        role=auth_service.ROLE_FINANCE,
    )
    required_client.cookies.clear()
    login_f = required_client.post(
        "/api/auth/login",
        json={"username": fin_user, "password": "P13i1-Finance-Pass!"},
    )
    assert login_f.status_code == 200, login_f.text
    fin = required_client.get(_events_url(pid))
    assert fin.status_code == 403, fin.text
    _assert_no_store(fin)

    _bootstrap_and_login(required_client)
    missing = required_client.get(_events_url("proj_does_not_exist_p13i1"))
    assert missing.status_code == 404, missing.text
    _assert_no_store(missing)
    assert "proj_does_not_exist_p13i1" not in missing.text


def test_privacy_no_message_error_result_payload(required_client):
    """用途：成功/错误响应无 message/error/result/payload/actor/client/异常原文。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-priv")
    task = _create_task(pid, task_type="export")
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, task.id)
        task_service._set_task(
            db,
            row,
            status="failed",
            progress=100,
            message=_SECRET_MSG,
            error=_SECRET_ERR,
            result={"payload": _SECRET_MSG, "path": _SECRET_ERR},
        )
    finally:
        db.close()
    rows = _db_event_rows(pid)
    after = rows[0].id
    ok = required_client.get(_events_url(pid), params={"after": after})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    _assert_list_shape(body)
    _assert_no_secrets(ok.text)
    for it in body["items"]:
        assert "message" not in it
        assert "error" not in it
        assert "result" not in it
        assert "payload" not in it
    empty = required_client.get(_events_url(pid))
    assert empty.status_code == 200
    _assert_no_secrets(empty.text)
    stale = required_client.get(
        _events_url(pid), params={"after": "pte_" + "b" * 32}
    )
    assert stale.status_code == 409
    _assert_no_secrets(stale.text)


def test_methods_other_than_get_rejected(required_client):
    """用途：已登录 strict bid_writer 下仅允许 GET；其它方法精确 405。"""
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-method")
    url = _events_url(pid)
    safe_body = {"probe": "p13i1-method-check", "limit": 1}
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


def test_disabled_auth_mode_rejects_task_events(disabled_client):
    """用途：AUTH_MODE=disabled 时固定 403，不开放事件游标。"""
    pid = _create_project_http(disabled_client, name="i1-disabled")
    res = disabled_client.get(_events_url(pid))
    assert res.status_code == 403, res.text
    _assert_no_store(res)
    _assert_no_secrets(res.text)


# ---------- 直接终态写链：个人 callback / 一次性票据 ----------


def _events_for_task(project_id: str, task_id: str, workspace_id: str = _WS) -> list[Any]:
    """用途：按 taskId 过滤本项目事件，禁止测试自行插入事件冒充。"""
    return [r for r in _db_event_rows(project_id, workspace_id) if r.task_id == task_id]


def test_personal_parse_callback_writes_single_success_event(required_client):
    """
    用途：个人 parse_callback 真实 HTTP 写链仅产一条 success/100/parse 事件。
    二次开发：必须走 HTTP；响应 taskId 与事件 taskId 精确一致；禁止直接 insert 事件。
    """
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-cb-personal")
    st = required_client.get(f"/api/projects/{pid}/editor-state")
    assert st.status_code == 200, st.text
    v0 = st.json()["stateVersion"]
    n0 = _db_event_count(pid, _WS)
    secret_name = "SECRET_P13I1_CB_FILE.pdf"

    res = required_client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# MinerU\n\nP13I1 personal callback body.",
            "source": "mineru",
            "filename": secret_name,
            "expectedStateVersion": v0,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    task_id = body["taskId"]
    assert isinstance(task_id, str) and task_id.startswith("task_")

    # 任务确已创建
    db = SessionLocal()
    try:
        task_row = db.get(ProjectTaskRow, task_id)
        assert task_row is not None
        assert task_row.project_id == pid
        assert task_row.type == "parse"
        assert task_row.status == "success"
        assert int(task_row.progress) == 100
    finally:
        db.close()

    rows = _events_for_task(pid, task_id)
    # 期望：恰好一条终态事件；当前漏钩时精确为 0（failure-first）
    assert len(rows) == 1, (
        f"个人 callback 响应 taskId={task_id} 在 project_task_events 中应恰有 1 条，"
        f"实际={len(rows)}"
    )
    ev = rows[0]
    assert _PTE_RE.fullmatch(ev.id)
    assert ev.task_id == task_id
    assert ev.task_type == "parse"
    assert ev.status == "success"
    assert int(ev.progress) == 100
    assert ev.workspace_id == _WS
    assert ev.project_id == pid
    assert not hasattr(ev, "message") or getattr(ev, "message", None) is None
    assert not hasattr(ev, "result_json")
    assert _db_event_count(pid, _WS) == n0 + 1
    # 不得伪造 pending/running
    all_statuses = [(r.status, r.progress) for r in rows]
    assert all_statuses == [("success", 100)]
    _assert_no_secrets(res.text)
    assert secret_name not in json.dumps(
        [
            {
                "id": r.id,
                "task_id": r.task_id,
                "status": r.status,
                "progress": r.progress,
            }
            for r in rows
        ],
        ensure_ascii=False,
    )


def test_local_parser_ticket_callback_writes_single_success_event(required_client):
    """
    用途：一次性票据公开回传真实 HTTP 写链仅产一条 success/100/parse 事件。
    二次开发：走 issue + /api/local-parser/callback；禁止 mock 写链或直接插事件。
    """
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-cb-ticket")
    n0 = _db_event_count(pid, _WS)

    issue = required_client.post(
        f"/api/projects/{pid}/parse-callback-ticket",
        headers={"X-CSRF-Token": csrf},
    )
    assert issue.status_code == 201, issue.text
    ticket = issue.json()["ticket"]
    assert isinstance(ticket, str) and len(ticket) >= 32

    # 公开回传无会话；仅认 X-Local-Parse-Ticket
    required_client.cookies.clear()
    res = required_client.post(
        "/api/local-parser/callback",
        json={
            "markdown": "# Docling\n\nP13I1 ticket callback body.",
            "source": "docling",
            "filename": "SECRET_P13I1_TICKET.pdf",
        },
        headers={"X-Local-Parse-Ticket": ticket},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body.keys()) == {"ok", "chars", "taskId"}
    task_id = body["taskId"]
    assert isinstance(task_id, str) and task_id.startswith("task_")

    db = SessionLocal()
    try:
        task_row = db.get(ProjectTaskRow, task_id)
        assert task_row is not None
        assert task_row.project_id == pid
        assert task_row.type == "parse"
        assert task_row.status == "success"
        assert int(task_row.progress) == 100
    finally:
        db.close()

    rows = _events_for_task(pid, task_id)
    assert len(rows) == 1, (
        f"票据回传响应 taskId={task_id} 在 project_task_events 中应恰有 1 条，"
        f"实际={len(rows)}"
    )
    ev = rows[0]
    assert _PTE_RE.fullmatch(ev.id)
    assert ev.task_id == task_id
    assert ev.task_type == "parse"
    assert ev.status == "success"
    assert int(ev.progress) == 100
    assert ev.workspace_id == _WS
    assert ev.project_id == pid
    assert _db_event_count(pid, _WS) == n0 + 1
    assert [(r.status, r.progress) for r in rows] == [("success", 100)]
    _assert_no_secrets(res.text)
    assert ticket not in res.text


def test_personal_parse_callback_event_flush_or_commit_zero_residual(
    required_client, monkeypatch
):
    """
    用途：个人 callback 在事件已进入同一 Session 后 flush/commit 故障时，
      任务/事件/同事务业务写入（parsed_markdown/项目步骤）精确零残留。
    """
    from app.models.entities import ProjectEditorStateRow

    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-cb-tx")
    st = required_client.get(f"/api/projects/{pid}/editor-state")
    assert st.status_code == 200, st.text
    v0 = st.json()["stateVersion"]
    n0_events = _db_event_count(pid, _WS)
    n0_tasks = _db_task_count(pid)

    seen = {"event_in_session": False}
    original_flush = Session.flush

    def _boom_on_event_flush(self, *args, **kwargs):
        EventRow = _try_import_event_row()
        if EventRow is not None:
            pending = [obj for obj in self.new if isinstance(obj, EventRow)]
            if pending:
                # 证明 commit/flush 前事件确已进入同一 Session（非假回滚）
                seen["event_in_session"] = True
                raise RuntimeError("injected_parse_callback_event_flush_failure")
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", _boom_on_event_flush)

    res = required_client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# fail-tx\n\nshould not persist",
            "source": "mineru",
            "filename": "tx_fail.pdf",
            "expectedStateVersion": v0,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 500, res.text
    assert "injected_parse_callback_event_flush_failure" not in res.text
    assert seen["event_in_session"] is True

    assert _db_event_count(pid, _WS) == n0_events
    assert _db_task_count(pid) == n0_tasks
    db = SessionLocal()
    try:
        state = db.get(ProjectEditorStateRow, pid)
        md = (state.parsed_markdown if state is not None else None) or ""
        assert "should not persist" not in md
        assert "fail-tx" not in md
        proj = db.get(Project, pid)
        assert proj is not None
        # 成功路径才会把步骤推到 analyzing/1；失败不得残留
        if proj.status == "analyzing" and (proj.technical_plan_step or 0) == 1:
            raise AssertionError("项目步骤不应在事件 flush 失败后残留 analyzing/step=1")
    finally:
        db.close()


def test_ticket_callback_event_flush_or_commit_zero_residual_and_retry(
    required_client, monkeypatch
):
    """
    用途：一次性票据回传事件入 Session 后 flush 故障时，任务/事件/正文零残留，
      票据保持可重试；恢复后同票同正文成功且仅一条 success 事件。
    """
    import hashlib

    from app.models.entities import (
        LocalParserCallbackTicketRow,
        ProjectEditorStateRow,
    )
    from sqlalchemy import select

    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-tk-tx")
    n0_events = _db_event_count(pid, _WS)
    n0_tasks = _db_task_count(pid)

    issue = required_client.post(
        f"/api/projects/{pid}/parse-callback-ticket",
        headers={"X-CSRF-Token": csrf},
    )
    assert issue.status_code == 201, issue.text
    ticket = issue.json()["ticket"]
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    seen = {"event_in_session": False}
    fail_once = {"armed": True}
    original_flush = Session.flush

    def _boom_on_event_flush(self, *args, **kwargs):
        EventRow = _try_import_event_row()
        if fail_once["armed"] and EventRow is not None:
            pending = [obj for obj in self.new if isinstance(obj, EventRow)]
            if pending:
                seen["event_in_session"] = True
                raise RuntimeError("injected_ticket_callback_event_flush_failure")
        return original_flush(self, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", _boom_on_event_flush)

    fail_body = {
        "markdown": "# ticket-tx\n\nshould not persist ticket body",
        "source": "mineru",
    }
    required_client.cookies.clear()
    res = required_client.post(
        "/api/local-parser/callback",
        json=fail_body,
        headers={"X-Local-Parse-Ticket": ticket},
    )
    assert res.status_code == 500, res.text
    assert "injected_ticket_callback_event_flush_failure" not in res.text
    assert seen["event_in_session"] is True

    assert _db_event_count(pid, _WS) == n0_events
    assert _db_task_count(pid) == n0_tasks
    db = SessionLocal()
    try:
        trow = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        # 中途失败不得消费票据
        assert trow.consumed_at is None
        state = db.get(ProjectEditorStateRow, pid)
        md = (state.parsed_markdown if state is not None else None) or ""
        assert "should not persist ticket body" not in md
    finally:
        db.close()

    # 恢复 flush：同一票据可重试并成功
    fail_once["armed"] = False
    retry = required_client.post(
        "/api/local-parser/callback",
        json=fail_body,
        headers={"X-Local-Parse-Ticket": ticket},
    )
    assert retry.status_code == 200, retry.text
    task_id = retry.json()["taskId"]
    rows = _events_for_task(pid, task_id)
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert int(rows[0].progress) == 100
    assert rows[0].task_type == "parse"
    assert _db_event_count(pid, _WS) == n0_events + 1

    db = SessionLocal()
    try:
        trow = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        assert trow.consumed_at is not None
    finally:
        db.close()


# ---------- 第二轮返修：真实裁剪游标 / 跨 workspace / 最终 commit 故障 ----------


_INJECT_COMMIT_FAIL_PERSONAL = "p13i1_injected_personal_commit_failure"
_INJECT_COMMIT_FAIL_TICKET = "p13i1_injected_ticket_commit_failure"


def test_trimmed_event_cursor_returns_stale_409(required_client):
    """
    用途：经真实 task_service 写链保存早期 eventId，触发 200 条裁剪并确认行已删除后，
      公开 GET after=旧 ID 精确 409 project_task_event_cursor_stale + no-store + 不回显。
    二次开发：禁止直接插入事件表或直接调用事件 helper 造数据。
    """
    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-trim-stale")

    # 真实写链：首条 pending 事件作为早期游标
    first_task = _create_task(pid, task_type="export")
    early_rows = _db_event_rows(pid, _WS)
    assert len(early_rows) >= 1
    early_id = early_rows[0].id
    assert _PTE_RE.fullmatch(early_id)
    assert early_rows[0].task_id == first_task.id
    assert _event_row_exists(early_id) is True

    # 完成首任务并继续写入，直到触发每项目 200 条裁剪并删除 early_id
    db = SessionLocal()
    try:
        row = db.get(ProjectTaskRow, first_task.id)
        assert row is not None
        task_service._set_task(
            db, row, status="success", progress=100, message="early-done"
        )
    finally:
        db.close()

    for i in range(205):
        task = _create_task(pid, task_type="export")
        db = SessionLocal()
        try:
            row = db.get(ProjectTaskRow, task.id)
            assert row is not None
            task_service._set_task(
                db,
                row,
                status="success",
                progress=100,
                message=f"trim-{i}",
            )
        finally:
            db.close()
        if not _event_row_exists(early_id):
            break

    assert _event_row_exists(early_id) is False, "早期事件行应已被真实裁剪删除"
    assert _db_event_count(pid, _WS) == 200

    res = required_client.get(_events_url(pid), params={"after": early_id})
    assert res.status_code == 409, res.text
    _assert_no_store(res)
    detail = res.json().get("detail") or res.json()
    assert isinstance(detail, dict)
    assert detail.get("code") == "project_task_event_cursor_stale"
    assert early_id not in res.text
    assert pid not in res.text
    _assert_no_secrets(res.text)


def test_cross_workspace_project_404_and_cursor_409(required_client):
    """
    用途：创建真实第二 workspace 与项目，由 task_service 产生 B 事件；
      会话保持活动空间 A 时，查 B 项目固定 404，A 项目用 B 游标固定 409。
    二次开发：禁止用同 workspace 第二项目冒充跨空间。
    """
    user_id, csrf = _bootstrap_and_login(required_client)
    pid_a = _create_project_http(required_client, csrf, name="i1-ws-a")
    # 活动空间 A 内先有一条真实事件，便于对照
    _create_task(pid_a, task_type="export", workspace_id=_WS)

    ws_b = f"ws_p13i1_b_{secrets.token_hex(4)}"
    pid_b = _seed_second_workspace_project(
        workspace_id=ws_b,
        workspace_name="P13I1第二空间",
        owner_user_id=user_id,
        member_user_id=user_id,
        project_name="i1-ws-b",
    )
    assert pid_b != pid_a
    assert ws_b != _WS

    # 必须由真实 task_service 写链产生 B 事件
    task_b = _create_task(pid_b, task_type="export", workspace_id=ws_b)
    rows_b = _db_event_rows(pid_b, ws_b)
    assert len(rows_b) >= 1
    cursor_b = rows_b[0].id
    assert _PTE_RE.fullmatch(cursor_b)
    assert rows_b[0].task_id == task_b.id
    assert rows_b[0].workspace_id == ws_b

    # 会话仍在 A：查询 B 项目固定 404，不回显 A/B 项目、workspace、游标
    res_b = required_client.get(_events_url(pid_b))
    assert res_b.status_code == 404, res_b.text
    _assert_no_store(res_b)
    assert pid_b not in res_b.text
    assert pid_a not in res_b.text
    assert ws_b not in res_b.text
    assert cursor_b not in res_b.text
    _assert_no_secrets(res_b.text)

    # A 项目使用 B 游标固定 409
    res_stale = required_client.get(
        _events_url(pid_a), params={"after": cursor_b}
    )
    assert res_stale.status_code == 409, res_stale.text
    _assert_no_store(res_stale)
    detail = res_stale.json().get("detail") or res_stale.json()
    assert isinstance(detail, dict)
    assert detail.get("code") == "project_task_event_cursor_stale"
    assert cursor_b not in res_stale.text
    assert pid_a not in res_stale.text
    assert pid_b not in res_stale.text
    assert ws_b not in res_stale.text
    _assert_no_secrets(res_stale.text)


def test_personal_parse_callback_final_commit_failure_zero_residual(
    required_client, monkeypatch
):
    """
    用途：个人 callback 只在最终 commit 注入失败；钩子内证明同一 Session 已有且仅有
      对应 taskId 的 success/100/parse 事件；响应脱敏 500；任务/事件/正文/步骤零残留。
    二次开发：禁止仅测 flush 或靠函数名冒充 commit。
    """
    from app.models.entities import ProjectEditorStateRow

    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-cb-commit")
    st = required_client.get(f"/api/projects/{pid}/editor-state")
    assert st.status_code == 200, st.text
    v0 = st.json()["stateVersion"]
    n0_events = _db_event_count(pid, _WS)
    n0_tasks = _db_task_count(pid)

    commit_probe: dict[str, Any] = {
        "n": 0,
        "task_id": None,
        "event_count": None,
        "event_shape": None,
        "project_event_count": None,
    }
    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm_final_commit(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit

        def _bad_commit(*a, **k):
            # 禁止钩子内 assert（未接实现时 AssertionError 可能被吞成假绿）
            commit_probe["n"] += 1
            EventRow = _try_import_event_row()
            # 优先同 Session 查询（flush 后可见未提交行）；禁止只靠函数名冒充
            parse_tasks = (
                db.query(ProjectTaskRow)
                .filter(
                    ProjectTaskRow.project_id == pid,
                    ProjectTaskRow.type == "parse",
                    ProjectTaskRow.status == "success",
                )
                .all()
            )
            parse_tasks = [
                t for t in parse_tasks if int(t.progress) == 100
            ]
            task_id = parse_tasks[-1].id if parse_tasks else None
            commit_probe["task_id"] = task_id
            commit_probe["identity_types"] = [type(o).__name__ for o in db]
            if EventRow is not None and task_id is not None:
                events = (
                    db.query(EventRow)
                    .filter(EventRow.task_id == task_id)
                    .all()
                )
                commit_probe["event_count"] = len(events)
                commit_probe["event_shape"] = [
                    (e.status, int(e.progress), e.task_type) for e in events
                ]
                commit_probe["project_event_count"] = (
                    db.query(EventRow)
                    .filter(EventRow.project_id == pid)
                    .count()
                )
            raise RuntimeError(_INJECT_COMMIT_FAIL_PERSONAL)

        db.commit = _bad_commit  # type: ignore[method-assign]
        return out

    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm_final_commit,
    )

    res = required_client.post(
        f"/api/projects/{pid}/parse-callback",
        json={
            "markdown": "# commit-fail-personal\n\nshould not persist commit body",
            "source": "mineru",
            "filename": "commit_fail_personal.pdf",
            "expectedStateVersion": v0,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 500, res.text
    assert _INJECT_COMMIT_FAIL_PERSONAL not in res.text
    assert "should not persist commit body" not in res.text
    detail = res.json().get("detail") or res.json()
    assert isinstance(detail, dict)
    assert detail.get("code") == "parse_callback_failed"
    assert detail.get("message") == "回传处理失败"

    # 最终 commit 必须被真实调用，且同一 Session 中事件已就绪
    assert commit_probe["n"] == 1
    assert isinstance(commit_probe["task_id"], str) and commit_probe[
        "task_id"
    ].startswith("task_")
    assert commit_probe["event_count"] == 1
    assert commit_probe["event_shape"] == [("success", 100, "parse")]
    # 本项目本事务仅该一条事件（相对 n0 增量）
    assert commit_probe["project_event_count"] == n0_events + 1

    assert _db_event_count(pid, _WS) == n0_events
    assert _db_task_count(pid) == n0_tasks
    db = SessionLocal()
    try:
        state = db.get(ProjectEditorStateRow, pid)
        md = (state.parsed_markdown if state is not None else None) or ""
        assert "should not persist commit body" not in md
        assert "commit-fail-personal" not in md
        proj = db.get(Project, pid)
        assert proj is not None
        if proj.status == "analyzing" and (proj.technical_plan_step or 0) == 1:
            raise AssertionError(
                "最终 commit 失败后项目步骤不得残留 analyzing/step=1"
            )
        # 对应 taskId 不得残留
        assert db.get(ProjectTaskRow, commit_probe["task_id"]) is None
    finally:
        db.close()


def test_ticket_callback_final_commit_failure_zero_residual_and_retry(
    required_client, monkeypatch
):
    """
    用途：一次性票据只在最终 commit 注入失败；钩子内证明 Session 内 success/100/parse 事件；
      脱敏 500、任务/事件/正文/审计零残留、票据未消费；恢复后同票成功且仅一条 success 事件。
    """
    import hashlib

    from app.models.entities import (
        LocalParserCallbackTicketRow,
        ProjectEditorStateRow,
    )
    from sqlalchemy import select

    _user_id, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i1-tk-commit")
    n0_events = _db_event_count(pid, _WS)
    n0_tasks = _db_task_count(pid)

    issue = required_client.post(
        f"/api/projects/{pid}/parse-callback-ticket",
        headers={"X-CSRF-Token": csrf},
    )
    assert issue.status_code == 201, issue.text
    ticket = issue.json()["ticket"]
    digest = hashlib.sha256(ticket.encode("utf-8")).hexdigest()

    commit_probe: dict[str, Any] = {
        "n": 0,
        "task_id": None,
        "event_count": None,
        "event_shape": None,
        "project_event_count": None,
    }
    real_lock = editor_state_service.lock_and_assert_expected_state_version

    def _lock_then_arm_final_commit(db, *args, **kwargs):
        out = real_lock(db, *args, **kwargs)
        real_commit = db.commit

        def _bad_commit(*a, **k):
            commit_probe["n"] += 1
            EventRow = _try_import_event_row()
            # 最终 commit 钩子：同 Session 查询证明事件/任务已 flush 入账
            parse_tasks = (
                db.query(ProjectTaskRow)
                .filter(
                    ProjectTaskRow.project_id == pid,
                    ProjectTaskRow.type == "parse",
                    ProjectTaskRow.status == "success",
                )
                .all()
            )
            parse_tasks = [
                t for t in parse_tasks if int(t.progress) == 100
            ]
            task_id = parse_tasks[-1].id if parse_tasks else None
            commit_probe["task_id"] = task_id
            commit_probe["identity_types"] = [type(o).__name__ for o in db]
            commit_probe["task_rows"] = [
                (t.id, t.type, t.status, int(t.progress), t.project_id)
                for t in db.query(ProjectTaskRow)
                .filter(ProjectTaskRow.project_id == pid)
                .all()
            ]
            if EventRow is not None and task_id is not None:
                events = (
                    db.query(EventRow)
                    .filter(EventRow.task_id == task_id)
                    .all()
                )
                commit_probe["event_count"] = len(events)
                commit_probe["event_shape"] = [
                    (e.status, int(e.progress), e.task_type) for e in events
                ]
                commit_probe["project_event_count"] = (
                    db.query(EventRow)
                    .filter(EventRow.project_id == pid)
                    .count()
                )
            elif EventRow is not None:
                commit_probe["event_count"] = (
                    db.query(EventRow)
                    .filter(EventRow.project_id == pid)
                    .count()
                )
                commit_probe["event_shape"] = [
                    (e.status, int(e.progress), e.task_type, e.task_id)
                    for e in db.query(EventRow)
                    .filter(EventRow.project_id == pid)
                    .all()
                ]
            raise RuntimeError(_INJECT_COMMIT_FAIL_TICKET)

        db.commit = _bad_commit  # type: ignore[method-assign]
        return out

    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        _lock_then_arm_final_commit,
    )

    fail_body = {
        "markdown": "# ticket-commit-fail\n\nshould not persist ticket commit body",
        "source": "docling",
        "filename": "ticket_commit_fail.pdf",
    }
    required_client.cookies.clear()
    res = required_client.post(
        "/api/local-parser/callback",
        json=fail_body,
        headers={"X-Local-Parse-Ticket": ticket},
    )
    assert res.status_code == 500, res.text
    assert _INJECT_COMMIT_FAIL_TICKET not in res.text
    assert "should not persist ticket commit body" not in res.text
    assert ticket not in res.text
    detail = res.json().get("detail") or res.json()
    assert isinstance(detail, dict)
    assert detail.get("code") == "local_parser_callback_failed"
    assert detail.get("message") == "回传处理失败"

    assert commit_probe["n"] == 1, commit_probe
    assert isinstance(commit_probe["task_id"], str) and commit_probe[
        "task_id"
    ].startswith("task_"), commit_probe
    assert commit_probe["event_count"] == 1, commit_probe
    assert commit_probe["event_shape"] == [("success", 100, "parse")], commit_probe
    assert commit_probe["project_event_count"] == n0_events + 1, commit_probe

    assert _db_event_count(pid, _WS) == n0_events
    assert _db_task_count(pid) == n0_tasks
    db = SessionLocal()
    try:
        trow = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        assert trow.consumed_at is None
        state = db.get(ProjectEditorStateRow, pid)
        md = (state.parsed_markdown if state is not None else None) or ""
        assert "should not persist ticket commit body" not in md
        assert "ticket-commit-fail" not in md
        proj = db.get(Project, pid)
        assert proj is not None
        if proj.status == "analyzing" and (proj.technical_plan_step or 0) == 1:
            raise AssertionError(
                "最终 commit 失败后项目步骤不得残留 analyzing/step=1"
            )
        assert db.get(ProjectTaskRow, commit_probe["task_id"]) is None
    finally:
        db.close()

    # 恢复真实 commit：同票可重试成功，只留一条 success 事件
    monkeypatch.setattr(
        editor_state_service,
        "lock_and_assert_expected_state_version",
        real_lock,
    )
    retry = required_client.post(
        "/api/local-parser/callback",
        json=fail_body,
        headers={"X-Local-Parse-Ticket": ticket},
    )
    assert retry.status_code == 200, retry.text
    task_id = retry.json()["taskId"]
    rows = _events_for_task(pid, task_id)
    assert len(rows) == 1
    assert rows[0].status == "success"
    assert int(rows[0].progress) == 100
    assert rows[0].task_type == "parse"
    assert _db_event_count(pid, _WS) == n0_events + 1

    db = SessionLocal()
    try:
        trow = db.scalars(
            select(LocalParserCallbackTicketRow).where(
                LocalParserCallbackTicketRow.ticket_digest == digest
            )
        ).one()
        assert trow.consumed_at is not None
    finally:
        db.close()
