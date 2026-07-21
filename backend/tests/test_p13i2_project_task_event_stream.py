"""
模块：P13-I2 项目任务事件 SSE 与断线重放专项测试
用途：failure-first 验收项目级 task-events SSE、Last-Event-ID 重放、
  空水位、跨页、stale 控制帧、鉴权与隐私；事件必须经 task_service 写链产生。
对接：GET /api/projects/{projectId}/task-events/stream；
  project_task_event_service 流页原语；I1 真实任务写链。
二次开发：禁止直接插事件表作主证据；禁止宽状态/skip/xfail；时钟仅 monkeypatch 路由常量。
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.main import app
from app.models.entities import (
    Project,
    ProjectTaskRow,
    Workspace,
    WorkspaceMemberRow,
    utc_now,
)
from app.services import auth_service, project_service, task_service

_WS = "ws_local"
_PTE_RE = re.compile(r"^pte_[0-9a-f]{32}$")
_OCCURRED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_CURSOR_KEYS = frozenset({"eventId"})
_TASK_EVENT_KEYS = frozenset(
    {"eventId", "taskId", "taskType", "status", "progress", "occurredAt"}
)
_STATUSES = frozenset({"pending", "running", "success", "failed", "cancelled"})
_TEST_USERNAME = "admin_p13i2"
_TEST_PASSWORD = "P13i2-Test-Pass-9!"
_WRITER_PASSWORD = "P13i2-Writer-Pass-9!"
_SECRET_MSG = "SECRET_P13I2_MSG_MUST_NOT_LEAK"
_SENSITIVE_MARKERS = (
    _TEST_PASSWORD,
    _WRITER_PASSWORD,
    _SECRET_MSG,
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
    "Set-Cookie",
    "csrfToken",
)


# ---------- SSE 解析 ----------


def _parse_sse_frames(raw: str) -> list[dict[str, Any]]:
    """用途：精确解析 SSE 帧，保留 id/event/data/comment。"""
    frames: list[dict[str, Any]] = []
    blocks = re.split(r"\n\n", raw)
    for block in blocks:
        if block == "":
            continue
        lines = block.split("\n")
        frame: dict[str, Any] = {
            "id": None,
            "event": None,
            "data": None,
            "comments": [],
            "raw": block,
        }
        data_lines: list[str] = []
        for line in lines:
            if line.startswith(":"):
                frame["comments"].append(
                    line[1:].lstrip() if line.startswith(": ") else line[1:]
                )
                continue
            if line.startswith("id:"):
                frame["id"] = line[3:].lstrip() if line.startswith("id: ") else line[3:]
                continue
            if line.startswith("event:"):
                frame["event"] = (
                    line[6:].lstrip() if line.startswith("event: ") else line[6:]
                )
                continue
            if line.startswith("data:"):
                data_lines.append(
                    line[5:].lstrip() if line.startswith("data: ") else line[5:]
                )
                continue
        if data_lines:
            raw_data = "\n".join(data_lines)
            frame["data_raw"] = raw_data
            frame["data"] = json.loads(raw_data)
        if (
            frame["comments"]
            or frame["id"] is not None
            or frame["event"] is not None
            or data_lines
        ):
            frames.append(frame)
    return frames


def _stream_url(project_id: str) -> str:
    return f"/api/projects/{project_id}/task-events/stream"


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
        "message",
        "error",
    ):
        # message/error 仅作为控制帧 code/message 允许；检查原始敏感形态
        if banned in ("message", "error"):
            continue
        assert banned not in low.replace("-", "_"), f"敏感/内部标记泄漏: {banned}"


def _assert_task_event_frame(frame: dict[str, Any]) -> dict:
    assert frame["event"] == "task-event"
    assert frame["id"] is not None and _PTE_RE.fullmatch(frame["id"])
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == _TASK_EVENT_KEYS
    assert data["eventId"] == frame["id"]
    assert _PTE_RE.fullmatch(data["eventId"])
    assert isinstance(data["taskId"], str) and data["taskId"]
    assert isinstance(data["taskType"], str) and data["taskType"]
    assert data["status"] in _STATUSES
    assert isinstance(data["progress"], int)
    assert 0 <= data["progress"] <= 100
    assert _OCCURRED_RE.fullmatch(data["occurredAt"])
    assert not frame["comments"]
    _assert_no_secrets(data)
    return data


def _assert_cursor_frame(frame: dict[str, Any]) -> str:
    assert frame["event"] == "cursor"
    assert frame["id"] is not None and _PTE_RE.fullmatch(frame["id"])
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == _CURSOR_KEYS
    assert data["eventId"] == frame["id"]
    assert not frame["comments"]
    _assert_no_secrets(data)
    return data["eventId"]


def _assert_control_frame(frame: dict[str, Any], event_name: str, code: str) -> None:
    assert frame["event"] == event_name
    assert frame["id"] is None
    data = frame["data"]
    assert isinstance(data, dict)
    assert set(data.keys()) == {"code", "message"}
    assert data["code"] == code
    assert isinstance(data["message"], str) and data["message"]
    assert not frame["comments"]
    _assert_no_secrets(data)


def _assert_sse_headers(response) -> None:
    ct = response.headers.get("content-type") or response.headers.get("Content-Type") or ""
    assert "text/event-stream" in ct
    assert "charset=utf-8" in ct.lower()
    cc = response.headers.get("Cache-Control") or ""
    assert "no-cache" in cc
    assert "no-store" in cc
    assert response.headers.get("X-Accel-Buffering") == "no"


def _assert_no_store_http(response) -> None:
    assert response.headers.get("Cache-Control") == "no-store"


# ---------- 构造 / fixtures ----------


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
    return user_id, res.json()["csrfToken"]


def _create_project_http(
    client: TestClient, csrf: str, name: str = "P13I2项目"
) -> str:
    res = client.post(
        "/api/projects",
        json={"name": name, "mode": "technical", "bidDeadline": None},
        headers={"X-CSRF-Token": csrf},
    )
    if res.status_code not in (200, 201):
        res = client.post(
            "/api/projects",
            json={"name": name, "kind": "technical"},
            headers={"X-CSRF-Token": csrf},
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
):
    res = client.post(
        "/api/auth/members",
        json={
            "username": username,
            "password": password,
            "role": role,
            "isOwner": False,
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_and_finish_export(
    project_id: str,
    *,
    workspace_id: str = _WS,
    message: str = "done",
) -> ProjectTaskRow:
    """用途：经真实 task_service 写链创建 export 并终态，释放防重入以便连续造事件。"""
    db = SessionLocal()
    try:
        task = task_service.create_task_record(
            db,
            workspace_id,
            project_id,
            task_type="export",
            payload={},
            actor_user_id=None,
        )
        row = db.get(ProjectTaskRow, task.id)
        assert row is not None
        task_service._set_task(
            db, row, status="success", progress=100, message=message
        )
        db.expunge(row)
        return row
    finally:
        db.close()


def _try_import_event_row():
    try:
        from app.models.entities import ProjectTaskEventRow  # type: ignore

        return ProjectTaskEventRow
    except Exception:
        return None


def _db_events(project_id: str, workspace_id: str = _WS) -> list[Any]:
    EventRow = _try_import_event_row()
    if EventRow is None:
        return []
    db = SessionLocal()
    try:
        return list(
            db.query(EventRow)
            .filter(
                EventRow.workspace_id == workspace_id,
                EventRow.project_id == project_id,
            )
            .order_by(EventRow.occurred_at.asc(), EventRow.id.asc())
            .all()
        )
    finally:
        db.close()


def _short_stream_constants(
    monkeypatch, *, max_s: float = 1.2, hb: float = 0.35, poll: float = 0.05
):
    """用途：可控时钟；仅 patch 路由模块常量。"""
    import app.api.project_task_events as route_mod

    monkeypatch.setattr(route_mod, "_SSE_MAX_SECONDS", max_s, raising=False)
    monkeypatch.setattr(route_mod, "_SSE_HEARTBEAT_SECONDS", hb, raising=False)
    monkeypatch.setattr(route_mod, "_SSE_POLL_SECONDS", poll, raising=False)


def _get_stream(
    client: TestClient,
    project_id: str,
    *,
    last_event_id: str | None = None,
    extra_headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
    content: bytes | None = None,
    method: str = "GET",
):
    headers: dict[str, str] = {}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    if extra_headers:
        headers.update(extra_headers)
    url = _stream_url(project_id)
    # httpx/Starlette TestClient：仅非 GET 用 content；GET body 用 request 原语
    if method == "GET":
        if content is not None:
            return client.request(
                "GET", url, headers=headers, params=params, content=content
            )
        return client.get(url, headers=headers, params=params)
    if method == "POST":
        return client.post(
            url, headers=headers, params=params, content=content or b"{}"
        )
    if method == "PUT":
        return client.put(url, headers=headers, content=content or b"{}")
    if method == "DELETE":
        return client.delete(url, headers=headers)
    if method == "HEAD":
        return client.head(url, headers=headers)
    raise AssertionError(f"unsupported method {method}")


def _seed_second_workspace_project(
    *,
    workspace_id: str,
    workspace_name: str,
    owner_user_id: str,
    member_user_id: str,
    project_name: str = "跨空间项目I2",
) -> str:
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


# ---------- 核心业务 ----------


def test_history_without_last_event_id_cursor_anchor_only(
    required_client, monkeypatch
):
    """已有历史无 Last-Event-ID：只发 cursor 锚点，不回放旧 task-event；连接后写链新事件按序发送。"""
    _short_stream_constants(monkeypatch, max_s=2.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-anchor")
    t1 = _create_and_finish_export(pid, message="hist-1")
    t2 = _create_and_finish_export(pid, message="hist-2")
    rows = _db_events(pid)
    assert len(rows) >= 2
    tip_id = rows[-1].id
    hist_task_ids = {t1.id, t2.id}

    barrier = threading.Event()
    holder: dict[str, str] = {}

    def _late_write():
        barrier.wait(timeout=5)
        time.sleep(0.15)
        live = _create_and_finish_export(pid, message="live-after-anchor")
        holder["task_id"] = live.id

    th = threading.Thread(target=_late_write, daemon=True)
    th.start()
    barrier.set()
    res = _get_stream(required_client, pid)
    th.join(timeout=6)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    assert frames, res.text

    tip = _assert_cursor_frame(frames[0])
    assert tip == tip_id
    task_frames = [f for f in frames if f["event"] == "task-event"]
    for ef in task_frames:
        data = _assert_task_event_frame(ef)
        # 历史任务不得作为新变化回放（仅允许连接后新 task）
        if data["taskId"] in hist_task_ids:
            assert data["taskId"] == holder.get("task_id")
    assert "task_id" in holder
    matched = [
        _assert_task_event_frame(f)
        for f in task_frames
        if f["data"]["taskId"] == holder["task_id"]
    ]
    assert len(matched) >= 1
    _assert_no_secrets(res.text)


def test_last_event_id_replays_only_following_in_order(
    required_client, monkeypatch
):
    """有 Last-Event-ID：精确正序重放其后仍保留事件；id 与 data.eventId 一致。"""
    _short_stream_constants(monkeypatch, max_s=1.8, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-replay")
    for i in range(4):
        _create_and_finish_export(pid, message=f"rp-{i}")
    rows = _db_events(pid)
    assert len(rows) >= 4
    after = rows[1].id
    expected_ids = [r.id for r in rows[2:]]

    res = _get_stream(required_client, pid, last_event_id=after)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    task_frames = [f for f in frames if f["event"] == "task-event"]
    assert len(task_frames) == len(expected_ids)
    for i, ef in enumerate(task_frames):
        data = _assert_task_event_frame(ef)
        assert data["eventId"] == expected_ids[i]
    assert all(f["event"] != "cursor" for f in frames)
    _assert_no_secrets(res.text)


def test_reconnect_with_last_received_id_only_following(
    required_client, monkeypatch
):
    """断线后以最后收到 ID 重连只收到后续事件。"""
    _short_stream_constants(monkeypatch, max_s=1.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-reconnect")
    for i in range(3):
        _create_and_finish_export(pid, message=f"rc-a-{i}")
    rows = _db_events(pid)
    first_after = rows[0].id
    res1 = _get_stream(required_client, pid, last_event_id=first_after)
    assert res1.status_code == 200, res1.text
    frames1 = _parse_sse_frames(res1.text)
    editors1 = [f for f in frames1 if f["event"] == "task-event"]
    assert len(editors1) >= 2
    last_id = editors1[-1]["id"]
    assert last_id == rows[-1].id

    live1 = _create_and_finish_export(pid, message="rc-b-1")
    live2 = _create_and_finish_export(pid, message="rc-b-2")
    res2 = _get_stream(required_client, pid, last_event_id=last_id)
    assert res2.status_code == 200, res2.text
    frames2 = _parse_sse_frames(res2.text)
    editors2 = [f for f in frames2 if f["event"] == "task-event"]
    # 每个 export 写链产生 pending+success 两条事件
    assert len(editors2) >= 2
    task_ids = [_assert_task_event_frame(ef)["taskId"] for ef in editors2]
    assert live1.id in task_ids
    assert live2.id in task_ids
    # 顺序：live1 的事件全部先于 live2
    first_live2 = task_ids.index(live2.id)
    assert all(tid == live1.id for tid in task_ids[:first_live2])
    old_ids = {r.id for r in rows}
    for ef in editors2:
        assert ef["id"] not in old_ids


def test_empty_table_first_and_following_events_sent(
    required_client, monkeypatch
):
    """空表连接后的第一条及连续事件必须作为 task-event 发送，不被 bootstrap 吸收。"""
    _short_stream_constants(monkeypatch, max_s=2.8, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-empty")
    assert _db_events(pid) == []

    barrier = threading.Event()
    holder: dict[str, list[str]] = {"task_ids": []}

    def _writes():
        barrier.wait(timeout=5)
        time.sleep(0.12)
        holder["task_ids"].append(
            _create_and_finish_export(pid, message="empty-1").id
        )
        time.sleep(0.08)
        holder["task_ids"].append(
            _create_and_finish_export(pid, message="empty-2").id
        )

    th = threading.Thread(target=_writes, daemon=True)
    th.start()
    barrier.set()
    res = _get_stream(required_client, pid)
    th.join(timeout=6)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    assert [f for f in frames if f["event"] == "cursor"] == []
    editors = [f for f in frames if f["event"] == "task-event"]
    assert len(holder["task_ids"]) == 2
    assert len(editors) >= 2
    ordered_unique: list[str] = []
    for f in editors:
        tid = _assert_task_event_frame(f)["taskId"]
        if tid not in ordered_unique:
            ordered_unique.append(tid)
    assert ordered_unique[:2] == holder["task_ids"]


def test_cross_page_51_events_no_loss_dup_or_reorder(
    required_client, monkeypatch
):
    """51 条以上积压跨页：无丢失、重复、乱序；每页最多 50。"""
    _short_stream_constants(monkeypatch, max_s=4.0, hb=30.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-page51")
    _create_and_finish_export(pid, message="base")
    base_rows = _db_events(pid)
    watermark = base_rows[0].id
    # create_task + success 各记一条事件，循环直到水位后 >=51
    i = 0
    while True:
        _create_and_finish_export(pid, message=f"p51-{i}")
        i += 1
        rows = _db_events(pid)
        after_ids = []
        seen = False
        for r in rows:
            if r.id == watermark:
                seen = True
                continue
            if seen:
                after_ids.append(r.id)
        if len(after_ids) >= 51:
            break
        if i > 80:
            raise AssertionError(f"无法堆出 51 条后续事件: {len(after_ids)}")
    assert len(after_ids) >= 51, len(after_ids)

    res = _get_stream(required_client, pid, last_event_id=watermark)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    editors = [f for f in frames if f["event"] == "task-event"]
    assert len(editors) == len(after_ids)
    got_ids = [_assert_task_event_frame(ef)["eventId"] for ef in editors]
    assert got_ids == after_ids
    assert len(set(got_ids)) == len(after_ids)
    assert len(got_ids) >= 51


def test_heartbeat_is_comment_only_without_id(required_client, monkeypatch):
    """空闲发送注释心跳，无 id/event/data，不改变水位。"""
    _short_stream_constants(monkeypatch, max_s=1.0, hb=0.2, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-hb")
    _create_and_finish_export(pid, message="hb-seed")
    tip = _db_events(pid)[-1].id

    res = _get_stream(required_client, pid)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    assert frames
    assert _assert_cursor_frame(frames[0]) == tip
    comment_frames = [
        f
        for f in frames
        if f["comments"] and f["event"] is None and f["data"] is None
    ]
    assert comment_frames, f"缺少心跳帧: {res.text!r}"
    for cf in comment_frames:
        assert cf["id"] is None
        assert any(c.strip() == "heartbeat" for c in cf["comments"])
    assert [f for f in frames if f["event"] == "task-event"] == []


def test_max_duration_quiet_close(required_client, monkeypatch):
    """最大时限到达安静关闭，无伪造事件 id。"""
    _short_stream_constants(monkeypatch, max_s=0.4, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-max")
    _create_and_finish_export(pid, message="max-seed")
    res = _get_stream(required_client, pid)
    assert res.status_code == 200, res.text
    frames = _parse_sse_frames(res.text)
    assert frames and frames[0]["event"] == "cursor"
    assert all(f["event"] not in ("error", "unavailable") for f in frames)
    for f in frames:
        if f["id"] is not None:
            assert _PTE_RE.fullmatch(f["id"])


# ---------- stale / 鉴权 / 非法请求 ----------


def test_unknown_and_forged_last_event_id_409(required_client, monkeypatch):
    """未知/伪造合法形态游标连接前固定 409，no-store，不回显。"""
    _short_stream_constants(monkeypatch, max_s=0.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-stale")
    _create_and_finish_export(pid, message="seed")
    forged = "pte_" + ("a" * 32)
    res = _get_stream(required_client, pid, last_event_id=forged)
    assert res.status_code == 409, res.text
    _assert_no_store_http(res)
    body = res.json()
    detail = body.get("detail", body)
    assert detail["code"] == "project_task_event_cursor_stale"
    assert forged not in res.text
    _assert_no_secrets(res.text)


def test_trimmed_cursor_409(required_client, monkeypatch):
    """真实裁剪后 early eventId 连接前 409。"""
    _short_stream_constants(monkeypatch, max_s=0.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-trim")
    early = _create_and_finish_export(pid, message="early")
    rows0 = _db_events(pid)
    assert rows0
    early_id = rows0[0].id
    # 触发 200 条窗口裁剪：再写足够多成功 export
    for i in range(210):
        _create_and_finish_export(pid, message=f"trim-{i}")
    assert not any(r.id == early_id for r in _db_events(pid))
    res = _get_stream(required_client, pid, last_event_id=early_id)
    assert res.status_code == 409, res.text
    _assert_no_store_http(res)
    detail = res.json().get("detail", res.json())
    assert detail["code"] == "project_task_event_cursor_stale"
    assert early_id not in res.text
    del early  # 静默未使用


def test_cross_project_cursor_409(required_client, monkeypatch):
    """跨项目游标固定 409。"""
    _short_stream_constants(monkeypatch, max_s=0.5, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid_a = _create_project_http(required_client, csrf, name="i2-xa")
    pid_b = _create_project_http(required_client, csrf, name="i2-xb")
    _create_and_finish_export(pid_a, message="a1")
    _create_and_finish_export(pid_b, message="b1")
    id_b = _db_events(pid_b)[-1].id
    res = _get_stream(required_client, pid_a, last_event_id=id_b)
    assert res.status_code == 409, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == "project_task_event_cursor_stale"
    assert id_b not in res.text


def test_auth_scope_matrix(required_client, monkeypatch):
    """required 未登录 401；非 writer 403；workspace 头 403；跨项目 404；disabled 403。"""
    _short_stream_constants(monkeypatch, max_s=0.4, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-auth")
    _create_and_finish_export(pid, message="auth-seed")

    # 未登录：先 bootstrap，再新 client 无 cookie
    unauth = TestClient(app)
    res = unauth.get(_stream_url(pid))
    assert res.status_code == 401, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == auth_service.CODE_AUTH_REQUIRED

    # 任意 X-Workspace-Id
    res = _get_stream(
        required_client, pid, extra_headers={"X-Workspace-Id": _WS}
    )
    assert res.status_code == 403, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == "workspace_header_forbidden"

    res = _get_stream(
        required_client, pid, extra_headers={"X-Workspace-Id": ""}
    )
    assert res.status_code == 403, res.text

    # 非 bid_writer
    _create_member(
        required_client,
        csrf,
        username="viewer_p13i2",
        password=_WRITER_PASSWORD,
        role=auth_service.ROLE_FINANCE,  # 非 writer
    )
    viewer = TestClient(app)
    login = viewer.post(
        "/api/auth/login",
        json={"username": "viewer_p13i2", "password": _WRITER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    res = viewer.get(_stream_url(pid))
    assert res.status_code == 403, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == auth_service.CODE_ROLE_FORBIDDEN

    # 跨项目 404
    res = _get_stream(required_client, "proj_not_exist_" + secrets.token_hex(4))
    assert res.status_code == 404, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == "project_not_found"

    # disabled 模式
    get_settings.cache_clear()
    import os

    os.environ["AUTH_MODE"] = "disabled"
    get_settings.cache_clear()
    try:
        with TestClient(app) as dclient:
            res = dclient.get(_stream_url(pid))
            assert res.status_code == 403, res.text
            _assert_no_store_http(res)
    finally:
        os.environ["AUTH_MODE"] = "required"
        get_settings.cache_clear()


def test_illegal_query_body_header_methods(required_client, monkeypatch):
    """非法 query/body/重复 Last-Event-ID 422；非 GET 405。"""
    _short_stream_constants(monkeypatch, max_s=0.4, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-ill")
    _create_and_finish_export(pid, message="ill")

    res = _get_stream(required_client, pid, params={"after": "x"})
    assert res.status_code == 422, res.text
    _assert_no_store_http(res)

    res = _get_stream(required_client, pid, content=b'{"x":1}')
    assert res.status_code == 422, res.text
    _assert_no_store_http(res)

    # 非法 Last-Event-ID 形态
    res = _get_stream(required_client, pid, last_event_id="PTE_" + ("a" * 32))
    assert res.status_code == 422, res.text
    res = _get_stream(required_client, pid, last_event_id="pte_" + ("A" * 32))
    assert res.status_code == 422, res.text
    res = _get_stream(required_client, pid, last_event_id="")
    assert res.status_code == 422, res.text

    # 重复头：Starlette 合并为逗号串时仍应 422（非法格式）或严格重复检测
    res = required_client.get(
        _stream_url(pid),
        headers=[
            (b"last-event-id", b"pte_" + b"a" * 32),
            (b"last-event-id", b"pte_" + b"b" * 32),
        ],
    )
    assert res.status_code == 422, res.text
    _assert_no_store_http(res)

    # 非 GET：带 CSRF 后框架应精确 405（与 H2 一致）
    url = _stream_url(pid)
    headers = {"X-CSRF-Token": csrf, "Content-Type": "application/json"}
    for method in ("post", "put", "patch", "delete"):
        if method == "delete":
            res = required_client.request(
                "DELETE", url, headers=headers, json={"probe": "i2"}
            )
        else:
            res = getattr(required_client, method)(
                url, headers=headers, json={"probe": "i2"}
            )
        assert res.status_code == 405, (method, res.status_code, res.text)
        _assert_no_secrets(res.text)


def test_cross_workspace_project_404(required_client, monkeypatch):
    """真实第二 workspace：B 项目 404；A 项目携带 B eventId → 409。"""
    _short_stream_constants(monkeypatch, max_s=0.5, hb=10.0, poll=0.05)
    uid, csrf = _bootstrap_and_login(required_client)
    pid_a = _create_project_http(required_client, csrf, name="i2-ws-a")
    _create_and_finish_export(pid_a, message="a")
    pid_b = _seed_second_workspace_project(
        workspace_id="ws_p13i2_other",
        workspace_name="I2其它空间",
        owner_user_id=uid,
        member_user_id=uid,
    )
    _create_and_finish_export(pid_b, workspace_id="ws_p13i2_other", message="b")
    rows_b = _db_events(pid_b, workspace_id="ws_p13i2_other")
    assert rows_b, "B 空间须经 task_service 写链产生事件"
    event_id_b = rows_b[-1].id
    assert _PTE_RE.fullmatch(event_id_b)

    # 活动 A 查 B 项目 → 404
    res = _get_stream(required_client, pid_b)
    assert res.status_code == 404, res.text
    _assert_no_store_http(res)
    assert res.json()["detail"]["code"] == "project_not_found"

    # 活动 A 对 A 项目 stream 携带 B 游标 → 409（跨 workspace 游标 stale）
    res2 = _get_stream(required_client, pid_a, last_event_id=event_id_b)
    assert res2.status_code == 409, res2.text
    _assert_no_store_http(res2)
    detail = res2.json().get("detail", res2.json())
    assert detail["code"] == "project_task_event_cursor_stale"
    assert event_id_b not in res2.text
    _assert_no_secrets(res2.text)


def test_session_lifecycle_no_request_scope_capture(
    required_client, monkeypatch
):
    """流内短 Session 全关闭 + stream 路径 get_db 精确零调用。"""
    import app.api.project_task_events as route_mod
    from app.core import database as dbmod
    from app.core.database import get_db

    _short_stream_constants(monkeypatch, max_s=1.2, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-sess")
    _create_and_finish_export(pid, message="s0")
    watermark = _db_events(pid)[0].id
    _create_and_finish_export(pid, message="s1")

    opened: list[Any] = []
    closed: list[Any] = []
    real_session_local = dbmod.SessionLocal
    get_db_calls = {"n": 0}

    class TrackingSession:
        def __init__(self, real):
            self._real = real
            opened.append(self)

        def __getattr__(self, name):
            return getattr(self._real, name)

        def close(self):
            closed.append(self)
            return self._real.close()

    def factory():
        return TrackingSession(real_session_local())

    def _forbidden_get_db():
        get_db_calls["n"] += 1
        raise AssertionError(
            "stream 路径不得调用 request-scope get_db"
        )

    monkeypatch.setattr(route_mod, "SessionLocal", factory, raising=False)
    app.dependency_overrides[get_db] = _forbidden_get_db
    try:
        res = _get_stream(required_client, pid, last_event_id=watermark)
        assert res.status_code == 200, res.text
        assert get_db_calls["n"] == 0, f"get_db 被调用 {get_db_calls['n']} 次"
        assert opened, "应创建短 Session"
        assert len(closed) == len(opened), (
            f"opened={len(opened)} closed={len(closed)}"
        )
        frames = _parse_sse_frames(res.text)
        assert [f for f in frames if f["event"] == "task-event"]
        _assert_no_secrets(res.text)
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_mid_stream_cursor_stale_control_frame(required_client, monkeypatch):
    """连接建立后水位失效：已发 task-event 后 cursor-stale 无 id 并关闭。"""
    import app.api.project_task_events as route_mod
    from app.services.project_task_event_service import (
        CODE_CURSOR_STALE,
        MSG_CURSOR_STALE,
        ProjectTaskEventError,
    )

    _short_stream_constants(monkeypatch, max_s=2.0, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-mid-stale")
    _create_and_finish_export(pid, message="ms0")
    watermark = _db_events(pid)[0].id
    _create_and_finish_export(pid, message="ms1")

    real_read = route_mod._read_stream_page_sync
    calls = {"n": 0}

    def _wrapped(workspace_id, project_id, after):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_read(workspace_id, project_id, after)
        raise ProjectTaskEventError(409, CODE_CURSOR_STALE, MSG_CURSOR_STALE)

    monkeypatch.setattr(route_mod, "_read_stream_page_sync", _wrapped)
    res = _get_stream(required_client, pid, last_event_id=watermark)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    assert frames
    task_frames = [f for f in frames if f["event"] == "task-event"]
    assert len(task_frames) >= 1
    last_task = _assert_task_event_frame(task_frames[-1])
    # 精确唯一 cursor-stale，且为最后一帧
    stale_frames = [f for f in frames if f["event"] == "cursor-stale"]
    assert len(stale_frames) == 1
    last = frames[-1]
    assert last is stale_frames[0]
    _assert_control_frame(last, "cursor-stale", CODE_CURSOR_STALE)
    assert last["id"] is None
    # 不伪造新 id：stale 帧无 id；业务 id 均合法 pte_
    for f in task_frames:
        assert _PTE_RE.fullmatch(f["id"])
    assert last_task["eventId"] == task_frames[-1]["id"]
    assert CODE_CURSOR_STALE in res.text
    _assert_no_secrets(res.text)


def test_mid_stream_unavailable_control_frame_privacy(
    required_client, monkeypatch
):
    """流内读页异常：unavailable 精确两键、无 id、不泄漏 SECRET/栈。"""
    import app.api.project_task_events as route_mod

    secret = "SECRET_P13I2_UNAVAIL_LEAK_9f3c2a"
    _short_stream_constants(monkeypatch, max_s=2.0, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-unavail")
    _create_and_finish_export(pid, message="u0")
    watermark = _db_events(pid)[0].id
    _create_and_finish_export(pid, message="u1")

    def _boom(workspace_id, project_id, after):
        raise RuntimeError(f"inject boom path C:/leak/{secret}.bin")

    monkeypatch.setattr(route_mod, "_read_stream_page_sync", _boom)
    res = _get_stream(required_client, pid, last_event_id=watermark)
    assert res.status_code == 200, res.text
    _assert_sse_headers(res)
    frames = _parse_sse_frames(res.text)
    assert frames
    # 精确唯一 unavailable，且为最后一帧
    unavail = [f for f in frames if f["event"] == "unavailable"]
    assert len(unavail) == 1
    last = frames[-1]
    assert last is unavail[0]
    _assert_control_frame(
        last, "unavailable", "project_task_event_unavailable"
    )
    assert last["id"] is None
    assert secret not in res.text
    assert "RuntimeError" not in res.text
    assert "C:/leak" not in res.text
    assert "traceback" not in res.text.lower()
    # 无伪造业务帧 id
    for f in frames:
        if f["event"] == "task-event":
            _assert_task_event_frame(f)
        elif f["id"] is not None:
            assert _PTE_RE.fullmatch(f["id"])
    _assert_no_secrets(res.text)
    assert secret not in json.dumps(
        [f.get("data") for f in frames], ensure_ascii=False
    )


def test_privacy_six_keys_only_on_success_frames(required_client, monkeypatch):
    """成功帧精确六键，无 message/error/result/workspace 等。"""
    _short_stream_constants(monkeypatch, max_s=1.2, hb=10.0, poll=0.05)
    _uid, csrf = _bootstrap_and_login(required_client)
    pid = _create_project_http(required_client, csrf, name="i2-priv")
    task = _create_and_finish_export(pid, message=_SECRET_MSG)
    rows = _db_events(pid)
    after = rows[0].id if len(rows) > 1 else None
    # 再写一条以便 Last-Event-ID 重放
    if after is None:
        # 仅一条：用无 header 拿 cursor 后不应含 secret
        res = _get_stream(required_client, pid)
    else:
        live = _create_and_finish_export(pid, message=_SECRET_MSG)
        res = _get_stream(required_client, pid, last_event_id=rows[0].id)
        del live
    assert res.status_code == 200, res.text
    assert _SECRET_MSG not in res.text
    frames = _parse_sse_frames(res.text)
    for f in frames:
        if f["event"] == "task-event":
            data = _assert_task_event_frame(f)
            assert "message" not in data
            assert "error" not in data
            assert "result" not in data
            assert data["taskId"] == task.id or data["taskId"].startswith("task_")
    _assert_no_secrets(res.text)
